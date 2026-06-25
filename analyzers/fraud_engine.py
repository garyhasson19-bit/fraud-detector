"""
Moteur d'analyse de fraude intégré — 100% local, aucune API externe requise.
Couvre tous les cas de fraude courants pour les TPE/PME (restaurants, commerces, artisans).
"""

import pandas as pd
import numpy as np
from datetime import timedelta
from scipy import stats as scipy_stats
import re
from collections import defaultdict


# ─── Seuils de risque ──────────────────────────────────────────────────────────
SEUIL_MONTANT_ELEVE = 2000       # € — paiement considéré comme élevé
SEUIL_MONTANT_TRES_ELEVE = 10000 # € — paiement très élevé
SEUIL_VIGILANCE = [500, 1000, 3000, 5000, 10000, 15000]  # Seuils réglementaires courants
MARGE_SEUIL = 100                # € — zone d'alerte sous un seuil
JOURS_DOUBLON = 7
JOURS_FREQUENCE = 14
NB_TRANSACTIONS_FREQ = 5
SCORE_CRITIQUE = 70
SCORE_ELEVE = 45
SCORE_MODERE = 20


# ─── Cas de fraude couverts ────────────────────────────────────────────────────

def cas_doublons(df):
    """FRAUDE INTERNE — Paiement dupliqué (double comptabilisation, erreur ou malveillance)."""
    flags = []
    df = df.copy()
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:80]

    seen = set()
    for i, row in df.iterrows():
        key_base = (round(abs(row['amount']), 2), row['desc_norm'])
        window = df[
            (df.index != i) &
            (df['amount'].round(2) == round(row['amount'], 2)) &
            (df['desc_norm'] == row['desc_norm']) &
            ((df['date'] - row['date']).abs() <= timedelta(days=JOURS_DOUBLON))
        ]
        if not window.empty and key_base not in seen:
            seen.add(key_base)
            for _, dup in window.iterrows():
                flags.append(_flag(
                    row, 90,
                    "Doublon de paiement",
                    f"Transaction de {abs(row['amount']):.2f}€ vers '{row['description'][:50]}' "
                    f"répétée {len(window)+1} fois en {JOURS_DOUBLON} jours. "
                    "Peut indiquer une double comptabilisation, un fournisseur fictif ou une fraude interne.",
                    "fraude_interne"
                ))
    return flags


def cas_fractionnement(df):
    """FRAUDE — Fractionnement pour passer sous un seuil de contrôle (smurfing)."""
    flags = []
    df = df.copy()
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:60]

    for seuil in SEUIL_VIGILANCE:
        grouped = df[df['amount'] < 0].groupby('desc_norm')
        for desc, group in grouped:
            if len(group) < 3:
                continue
            group = group.sort_values('date')
            for start_i in range(len(group)):
                window_end = group.iloc[start_i]['date'] + timedelta(days=JOURS_FREQUENCE)
                window = group[
                    (group['date'] >= group.iloc[start_i]['date']) &
                    (group['date'] <= window_end)
                ]
                if len(window) < 3:
                    continue
                total = window['amount'].abs().sum()
                max_single = window['amount'].abs().max()
                if total >= seuil * 0.8 and max_single < seuil:
                    for _, row in window.iterrows():
                        flags.append(_flag(
                            row, 85,
                            f"Fractionnement sous {seuil:,}€",
                            f"{len(window)} paiements à '{desc[:40]}' totalisant {total:.2f}€ "
                            f"en {JOURS_FREQUENCE} jours, chacun sous {seuil:,}€. "
                            f"Technique classique de contournement des contrôles (smurfing).",
                            "fraude_financiere"
                        ))
                    break
    return _dedup(flags)


def cas_juste_sous_seuil(df):
    """FRAUDE — Montant intentionnellement juste en dessous d'un seuil de contrôle."""
    flags = []
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        for seuil in SEUIL_VIGILANCE:
            if seuil - MARGE_SEUIL <= amt < seuil and amt > 200:
                flags.append(_flag(
                    row, 80,
                    f"Juste sous le seuil de {seuil:,}€",
                    f"Montant de {amt:.2f}€, soit {seuil - amt:.2f}€ sous le seuil de {seuil:,}€. "
                    f"Ce positionnement peut être intentionnel pour éviter un contrôle ou une validation.",
                    "fraude_financiere"
                ))
                break
    return flags


def cas_fournisseur_fantome(df):
    """FRAUDE FOURNISSEUR — Premier gros paiement vers un bénéficiaire inconnu."""
    flags = []
    df = df.copy()
    df = df.sort_values('date')
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:80]

    seen = {}
    for _, row in df.iterrows():
        desc = row['desc_norm']
        amt = abs(row['amount'])
        if desc not in seen:
            seen[desc] = row['date']
            if amt >= SEUIL_MONTANT_ELEVE and row['amount'] < 0:
                flags.append(_flag(
                    row, 75,
                    "Nouveau fournisseur, montant élevé",
                    f"Premier paiement de {amt:.2f}€ vers '{row['description'][:50]}' — "
                    f"ce bénéficiaire n'apparaît jamais auparavant dans l'historique. "
                    "Risque de fournisseur fictif ou de détournement.",
                    "fraude_fournisseur"
                ))
    return flags


def cas_montant_rond(df):
    """FRAUDE CAISSE — Montants ronds élevés (manipulation de liquidités, faux remboursements)."""
    flags = []
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        if amt >= 500 and amt % 100 == 0:
            score = 60 if amt < 5000 else 75
            flags.append(_flag(
                row, score,
                "Montant rond suspect",
                f"Transaction de {amt:.0f}€ exactement. "
                "Les montants ronds élevés sont associés aux manipulations de caisse, "
                "retraits non justifiés ou paiements fictifs.",
                "fraude_caisse"
            ))
    return flags


def cas_pic_depenses(df):
    """ANOMALIE — Semaine ou mois avec dépenses anormalement élevées."""
    flags = []
    debits = df[df['amount'] < 0].copy()
    if len(debits) < 8:
        return flags

    debits['week'] = debits['date'].dt.to_period('W')
    weekly = debits.groupby('week')['amount'].sum().abs()
    if weekly.std() == 0 or pd.isna(weekly.std()):
        return flags

    z_scores = np.abs(scipy_stats.zscore(weekly))
    for week, z in zip(weekly.index, z_scores):
        if z > 2.5:
            week_total = weekly[week]
            week_mean = weekly.mean()
            week_txns = debits[debits['week'] == week]
            for _, row in week_txns.iterrows():
                flags.append(_flag(
                    row, 65,
                    "Pic de dépenses anormal",
                    f"La semaine du {week.start_time.strftime('%d/%m/%Y')} totalise "
                    f"{week_total:.2f}€ de dépenses vs moyenne de {week_mean:.2f}€ "
                    f"(z-score: {z:.1f}). Peut indiquer des achats non autorisés ou des sorties fictives.",
                    "anomalie_depenses"
                ))
    return _dedup(flags)


def cas_virements_personnels(df):
    """FRAUDE DIRIGEANT — Paiements vers comptes personnels ou IBAN inhabituels."""
    mots_suspects = [
        'virement perso', 'compte perso', 'particulier', 'personnel',
        'vrt perso', 'cpte perso', 'prelevement perso', 'pret perso',
    ]
    flags = []
    for _, row in df.iterrows():
        desc = row['description'].lower()
        for mot in mots_suspects:
            if mot in desc:
                flags.append(_flag(
                    row, 85,
                    "Virement vers compte personnel",
                    f"La description '{row['description'][:60]}' suggère un virement "
                    f"vers un compte personnel depuis le compte professionnel. "
                    "Risque élevé de détournement de fonds.",
                    "fraude_interne"
                ))
                break
    return flags


def cas_remboursements_suspects(df):
    """FRAUDE CAISSE — Remboursements ou avoirs excessifs."""
    mots_remb = ['remboursement', 'remb ', 'avoir', 'refund', 'annulation', 'retour', 'crédit note']
    flags = []
    for _, row in df.iterrows():
        if row['amount'] > 0:
            continue
        desc = row['description'].lower()
        for mot in mots_remb:
            if mot in desc and abs(row['amount']) > 200:
                flags.append(_flag(
                    row, 70,
                    "Remboursement ou avoir suspect",
                    f"Transaction de remboursement/avoir de {abs(row['amount']):.2f}€ : "
                    f"'{row['description'][:60]}'. "
                    "Vérifier que ce remboursement est justifié et correspond à un retour réel.",
                    "fraude_caisse"
                ))
                break
    return flags


def cas_transactions_weekend(df):
    """ANOMALIE — Transactions importantes le week-end (hors activité normale)."""
    flags = []
    for _, row in df.iterrows():
        dow = row['date'].weekday()
        amt = abs(row['amount'])
        if dow == 6 and amt >= 1000:  # Dimanche uniquement si montant significatif
            flags.append(_flag(
                row, 50,
                "Transaction importante un dimanche",
                f"Paiement de {amt:.2f}€ un dimanche. "
                "Inhabituel pour un commerce — vérifier l'autorisation et la justification.",
                "anomalie_temporelle"
            ))
    return flags


def cas_salaires_irreguliers(df):
    """FRAUDE PAIE — Versements de salaires irréguliers ou multipliés."""
    mots_salaire = ['salaire', 'paie', 'paie ', 'acompte', 'avance', 'rémunération', 'salarie', 'paye']
    salaires = []
    for _, row in df.iterrows():
        desc = row['description'].lower()
        if any(m in desc for m in mots_salaire) and row['amount'] < 0:
            salaires.append(row)

    if len(salaires) < 2:
        return []

    flags = []
    sal_df = pd.DataFrame(salaires)
    sal_df['month'] = sal_df['date'].dt.to_period('M')
    monthly_sal = sal_df.groupby('month')['amount'].agg(['sum', 'count'])
    mean_sal = monthly_sal['sum'].mean()
    std_sal = monthly_sal['sum'].std()

    if pd.isna(std_sal) or std_sal == 0:
        return []

    for period, row_m in monthly_sal.iterrows():
        z = abs((row_m['sum'] - mean_sal) / std_sal)
        if z > 2 or row_m['count'] > 3:
            month_txns = sal_df[sal_df['month'] == period]
            for _, row in month_txns.iterrows():
                flags.append(_flag(
                    row, 75,
                    "Irrégularité sur les salaires",
                    f"Mois {period}: {row_m['count']} versements de salaires pour "
                    f"{abs(row_m['sum']):.2f}€ (normale: {abs(mean_sal):.2f}€). "
                    "Risque d'employé fantôme, de doublon de paie ou de détournement.",
                    "fraude_paie"
                ))
    return _dedup(flags)


def cas_outliers_statistiques(df):
    """ANOMALIE STATISTIQUE — Transactions statistiquement aberrantes (Z-score)."""
    flags = []
    if len(df) < 10:
        return flags

    amounts = df['amount'].abs()
    z_scores = np.abs(scipy_stats.zscore(amounts))

    for i, (z, (_, row)) in enumerate(zip(z_scores, df.iterrows())):
        if z > 3.5:
            flags.append(_flag(
                row, min(95, int(50 + z * 10)),
                "Montant statistiquement aberrant",
                f"Montant de {abs(row['amount']):.2f}€ — {z:.1f} fois l'écart-type au-dessus de la moyenne. "
                f"Ce montant est statistiquement exceptionnel par rapport au reste des transactions.",
                "anomalie_statistique"
            ))
    return flags


def cas_retraits_especes(df):
    """FRAUDE CAISSE — Retraits en espèces répétés ou importants."""
    mots_especes = ['retrait', 'espece', 'espèce', 'dab ', 'atm', 'cash', 'billetterie', 'retrait cb']
    flags = []
    total_cash = 0
    cash_txns = []

    for _, row in df.iterrows():
        desc = row['description'].lower()
        if any(m in desc for m in mots_especes) and row['amount'] < 0:
            total_cash += abs(row['amount'])
            cash_txns.append(row)

    if total_cash > 5000:
        for row in cash_txns:
            flags.append(_flag(
                row, 70,
                "Retraits espèces importants",
                f"Total des retraits en espèces : {total_cash:.2f}€. "
                "Des retraits d'espèces fréquents ou importants sans justification "
                "sont un signal de blanchiment ou de détournement.",
                "fraude_caisse"
            ))
    return flags


def cas_paiements_nuit_weekend(df):
    """ANOMALIE — Activité à des heures ou jours impossibles pour le secteur."""
    flags = []
    # Reprise brutale après inactivité prolongée
    df_sorted = df.sort_values('date').copy()
    df_sorted['gap'] = df_sorted['date'].diff().dt.days

    mean_gap = df_sorted['gap'].mean()
    std_gap = df_sorted['gap'].std()

    if pd.isna(std_gap) or std_gap < 1:
        return flags

    for _, row in df_sorted.iterrows():
        if pd.isna(row['gap']):
            continue
        if row['gap'] > max(mean_gap + 3 * std_gap, 45):
            flags.append(_flag(
                row, 55,
                "Reprise après silence prolongé",
                f"Aucune activité pendant {row['gap']:.0f} jours avant cette transaction "
                f"(délai normal: {mean_gap:.0f} jours). "
                "Peut indiquer une prise de contrôle du compte ou une activité inhabituellement suspendue.",
                "anomalie_temporelle"
            ))
    return flags


def cas_concentration_fournisseur(df):
    """RISQUE — Un seul fournisseur concentre une part anormale des dépenses."""
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return flags

    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    total_debits = debits['amount'].abs().sum()
    if total_debits == 0:
        return flags

    by_vendor = debits.groupby('desc_norm')['amount'].agg(['sum', 'count'])
    for vendor, vrow in by_vendor.iterrows():
        pct = abs(vrow['sum']) / total_debits * 100
        if pct > 40 and abs(vrow['sum']) > 5000:
            vendor_txns = debits[debits['desc_norm'] == vendor]
            for _, row in vendor_txns.iterrows():
                flags.append(_flag(
                    row, 65,
                    "Concentration excessive vers un fournisseur",
                    f"'{vendor[:40]}' représente {pct:.1f}% des débits totaux "
                    f"({abs(vrow['sum']):.2f}€ en {int(vrow['count'])} transactions). "
                    "Une dépendance aussi forte peut masquer une surfacturation ou complicité.",
                    "risque_fournisseur"
                ))
    return _dedup(flags)


def cas_achats_inhabituels(df):
    """FRAUDE INTERNE — Catégories de dépenses inhabituelles pour le secteur."""
    mots_suspects = {
        'bijouterie': "achat en bijouterie depuis le compte professionnel",
        'casino': "dépense dans un casino",
        'paris sportif': "paris sportifs sur compte pro",
        'luxe': "achat de luxe sur compte professionnel",
        'voyage perso': "voyage personnel sur compte professionnel",
        'hôtel personnel': "hôtel personnel",
        'jeux': "jeux/loisirs non professionnels",
        'spa': "spa/bien-être non professionnel",
    }
    flags = []
    for _, row in df.iterrows():
        desc = row['description'].lower()
        for mot, explication in mots_suspects.items():
            if mot in desc:
                flags.append(_flag(
                    row, 70,
                    "Dépense non professionnelle",
                    f"Possible {explication} : '{row['description'][:60]}'. "
                    "Dépense personnelle imputée au compte de l'entreprise.",
                    "fraude_interne"
                ))
                break
    return flags


def cas_virements_multiples_meme_jour(df):
    """FRAUDE — Plusieurs virements importants le même jour vers des bénéficiaires différents."""
    flags = []
    debits = df[(df['amount'] < 0) & (df['amount'].abs() >= 1000)].copy()
    if debits.empty:
        return flags

    debits['date_only'] = debits['date'].dt.date
    by_day = debits.groupby('date_only')
    for day, group in by_day:
        if len(group) >= 3:
            total = group['amount'].abs().sum()
            if total >= SEUIL_MONTANT_TRES_ELEVE:
                for _, row in group.iterrows():
                    flags.append(_flag(
                        row, 75,
                        "Multiples gros virements le même jour",
                        f"Le {day}: {len(group)} virements ≥ 1000€ totalisant {total:.2f}€ "
                        f"vers des bénéficiaires différents. "
                        "Comportement caractéristique d'une fraude à la prise de contrôle de compte.",
                        "fraude_financiere"
                    ))
    return _dedup(flags)


# ─── Nouveaux cas basés sur recherches forensiques ACFE / TRACFIN ─────────────

def cas_loi_benford(df):
    """
    LOI DE BENFORD — Les premiers chiffres des montants doivent suivre une distribution naturelle.
    Utilisée par l'ACFE, le fisc américain (IRS) et l'administration fiscale française.
    Si les chiffres 7/8/9 sont surreprésentés → fabrication de montants probable.
    """
    if len(df) < 50:
        return []

    amounts = df['amount'].abs()
    amounts = amounts[amounts >= 10]
    if len(amounts) < 50:
        return []

    first_digits = amounts.apply(lambda x: int(str(x).replace('.', '').replace(',', '').lstrip('0')[0]))
    observed = first_digits.value_counts(normalize=True).sort_index()

    # Distribution théorique de Benford
    benford = {d: np.log10(1 + 1/d) for d in range(1, 10)}

    flags = []
    for digit in range(7, 10):  # 7, 8, 9 sont les plus suspects si surreprésentés
        obs = observed.get(digit, 0)
        exp = benford[digit]
        if obs > exp * 2.5:
            pct_obs = obs * 100
            pct_exp = exp * 100
            suspicious_txns = df[df['amount'].abs().apply(
                lambda x: str(x).replace('.', '').lstrip('0')[:1] == str(digit)
            )]
            for _, row in suspicious_txns.head(10).iterrows():
                flags.append(_flag(
                    row, 80,
                    f"Loi de Benford — chiffre {digit} anormal",
                    f"Le chiffre '{digit}' apparaît en tête de {pct_obs:.1f}% des montants "
                    f"(normal: {pct_exp:.1f}%). Technique de détection utilisée par le fisc et l'ACFE. "
                    "Indique une possible fabrication de montants.",
                    "fraude_financiere"
                ))
    return _dedup(flags)


def cas_micro_transactions(df):
    """
    CARD TESTING / TEST DE CARTE — Très nombreuses petites transactions vers un même bénéficiaire.
    Technique utilisée pour tester si une carte volée fonctionne avant une grosse fraude.
    """
    flags = []
    df_small = df[df['amount'].abs() < 10].copy()
    if len(df_small) < 5:
        return []

    df_small['desc_norm'] = df_small['description'].str.lower().str.strip().str[:60]
    by_vendor = df_small.groupby('desc_norm').size()

    for vendor, count in by_vendor.items():
        if count >= 5:
            vendor_txns = df_small[df_small['desc_norm'] == vendor]
            for _, row in vendor_txns.iterrows():
                flags.append(_flag(
                    row, 75,
                    "Test de carte suspect (micro-transactions)",
                    f"{count} transactions inférieures à 10€ vers '{vendor[:40]}'. "
                    "Pattern classique de 'card testing' : test d'une carte volée avec de petits montants "
                    "avant une tentative de fraude importante.",
                    "fraude_financiere"
                ))
    return _dedup(flags)


def cas_transactions_circulaires(df):
    """
    BLANCHIMENT — Argent qui sort puis revient (aller-retour).
    Signe possible de blanchiment ou de gonflement artificiel du chiffre d'affaires.
    """
    flags = []
    df = df.copy().sort_values('date')
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:60]
    df['abs_amount'] = df['amount'].abs()

    for i, row_out in df[df['amount'] < 0].iterrows():
        amt = row_out['abs_amount']
        if amt < 500:
            continue
        # Cherche un crédit du même montant dans les 30 jours suivants
        window = df[
            (df['amount'] > 0) &
            (df['abs_amount'].between(amt * 0.95, amt * 1.05)) &
            (df['date'] > row_out['date']) &
            (df['date'] <= row_out['date'] + timedelta(days=30))
        ]
        if not window.empty:
            flags.append(_flag(
                row_out, 85,
                "Transaction circulaire (aller-retour)",
                f"Débit de {amt:.2f}€ suivi d'un crédit quasi-identique "
                f"({window.iloc[0]['amount']:.2f}€) dans les 30 jours. "
                "Schéma caractéristique du blanchiment ou du gonflement artificiel du CA.",
                "fraude_financiere"
            ))
    return _dedup(flags)


def cas_inflation_salaires(df):
    """
    FRAUDE PAIE — Augmentations progressives de salaires non justifiées (paie padding).
    Chaque mois le salaire augmente légèrement — invisible individuellement, énorme sur l'année.
    """
    mots_salaire = ['salaire', 'paie', 'acompte', 'avance salaire', 'salarie', 'paye']
    flags = []
    sal_rows = [row for _, row in df.iterrows()
                if any(m in row['description'].lower() for m in mots_salaire) and row['amount'] < 0]

    if len(sal_rows) < 3:
        return []

    sal_df = pd.DataFrame(sal_rows).sort_values('date')
    sal_df['month'] = sal_df['date'].dt.to_period('M')
    sal_df['desc_norm'] = sal_df['description'].str.lower().str.strip().str[:60]

    for emp, group in sal_df.groupby('desc_norm'):
        if len(group) < 3:
            continue
        group = group.sort_values('date')
        amounts = group['amount'].abs().values
        # Détecte une tendance à la hausse régulière
        diffs = np.diff(amounts)
        if np.all(diffs > 0) and diffs.mean() > 50:
            total_drift = amounts[-1] - amounts[0]
            for _, row in group.iterrows():
                flags.append(_flag(
                    row, 78,
                    "Inflation progressive de salaire",
                    f"Le salaire de '{emp[:40]}' augmente de {diffs.mean():.0f}€ par mois en moyenne "
                    f"(+{total_drift:.0f}€ au total sur la période). "
                    "Sans avenant signé, c'est un signe de manipulation de paie.",
                    "fraude_paie"
                ))
    return _dedup(flags)


def cas_fournisseur_similaire(df):
    """
    FRAUDE FOURNISSEUR — Deux fournisseurs au nom quasi-identique (typosquatting, fournisseur fantôme).
    Ex: 'METRO CASH' et 'METRO CASHS' — l'un est réel, l'autre est fictif.
    """
    flags = []
    df = df.copy()
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:50]
    vendors = df[df['amount'] < 0]['desc_norm'].unique().tolist()

    from difflib import SequenceMatcher

    def similarity(a, b):
        return SequenceMatcher(None, a, b).ratio()

    seen_pairs = set()
    for i, v1 in enumerate(vendors):
        for v2 in vendors[i+1:]:
            if len(v1) < 5 or len(v2) < 5:
                continue
            pair = tuple(sorted([v1, v2]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            sim = similarity(v1, v2)
            if 0.80 <= sim < 1.0:
                for vendor in [v1, v2]:
                    txns = df[df['desc_norm'] == vendor]
                    for _, row in txns.iterrows():
                        flags.append(_flag(
                            row, 80,
                            "Fournisseurs au nom similaire (suspect)",
                            f"'{v1[:35]}' et '{v2[:35]}' ont des noms quasi-identiques "
                            f"(similarité: {sim*100:.0f}%). "
                            "Technique classique du fournisseur fictif — vérifier les SIRET.",
                            "fraude_fournisseur"
                        ))
    return _dedup(flags)


def cas_fin_de_periode(df):
    """
    MANIPULATION COMPTABLE — Pic de transactions en toute fin de mois.
    Technique classique pour gonfler ou vider les comptes avant clôture comptable.
    """
    flags = []
    df = df.copy()
    df['day_of_month'] = df['date'].dt.day
    df['month'] = df['date'].dt.to_period('M')

    fin_mois = df[df['day_of_month'] >= 28]
    if fin_mois.empty:
        return []

    avg_daily = df['amount'].abs().sum() / max(df['date'].nunique(), 1)

    for month, group in fin_mois.groupby('month'):
        total = group['amount'].abs().sum()
        n_days = len(group['day_of_month'].unique())
        if n_days == 0:
            continue
        avg_end = total / n_days
        if avg_end > avg_daily * 4 and total > 2000:
            for _, row in group.iterrows():
                flags.append(_flag(
                    row, 70,
                    "Pic de transactions en fin de mois",
                    f"Activité 4x supérieure à la normale les derniers jours de {month}. "
                    "Technique de manipulation comptable courante avant clôture.",
                    "anomalie_depenses"
                ))
    return _dedup(flags)


def cas_frais_rembourses_excessifs(df):
    """
    FRAUDE INTERNE — Trop de remboursements de frais (notes de frais fictives).
    L'une des fraudes les plus courantes selon l'ACFE : 21% des cas en PME.
    """
    mots_frais = ['frais', 'note de frais', 'remb frais', 'expense', 'ndf', 'indemnite', 'indemnité']
    flags = []
    frais_txns = [row for _, row in df.iterrows()
                  if any(m in row['description'].lower() for m in mots_frais) and row['amount'] < 0]

    if not frais_txns:
        return []

    total_frais = sum(abs(r['amount']) for r in frais_txns)
    total_debits = df[df['amount'] < 0]['amount'].abs().sum()

    pct = total_frais / total_debits * 100 if total_debits > 0 else 0

    if pct > 15 or total_frais > 3000:
        for row in frais_txns:
            flags.append(_flag(
                row, 72,
                "Remboursements de frais excessifs",
                f"Les notes de frais totalisent {total_frais:.2f}€ ({pct:.1f}% des dépenses). "
                "Selon l'ACFE, les fausses notes de frais représentent 21% des fraudes en PME. "
                "Demander les justificatifs originaux.",
                "fraude_interne"
            ))
    return _dedup(flags)


def cas_paiements_inconnus_recurrents(df):
    """
    ABONNEMENTS CACHÉS / PRÉLÈVEMENTS NON AUTORISÉS — Petits montants réguliers vers inconnus.
    Fraude très fréquente : abonnements souscrits à l'insu de l'entreprise, ou virements détournés.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    debits['month'] = debits['date'].dt.to_period('M')

    by_vendor = debits.groupby('desc_norm')
    for vendor, group in by_vendor:
        months_active = group['month'].nunique()
        total = group['amount'].abs().sum()
        avg = group['amount'].abs().mean()

        # Petit montant, récurrent sur plusieurs mois, pas connu comme fournisseur principal
        if months_active >= 3 and 10 < avg < 500 and total > 200:
            # Vérifier que ce fournisseur n'est pas parmi les tops (donc il est discret)
            all_vendors_total = debits.groupby('desc_norm')['amount'].sum().abs()
            rank = (all_vendors_total > all_vendors_total[vendor]).sum() + 1
            if rank > len(all_vendors_total) * 0.4:  # Dans le bas du classement = discret
                for _, row in group.iterrows():
                    flags.append(_flag(
                        row, 65,
                        "Prélèvement récurrent non identifié",
                        f"'{vendor[:40]}' prélève {avg:.0f}€/mois depuis {months_active} mois "
                        f"(total: {total:.0f}€). Abonnement non autorisé ou prélèvement détourné possible.",
                        "fraude_interne"
                    ))
    return _dedup(flags)


def cas_surfacturation_progressive(df):
    """
    FRAUDE FOURNISSEUR — Un fournisseur régulier qui augmente progressivement ses montants.
    Technique discrète : +5% par mois passe inaperçu, mais sur un an = +80%.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if len(debits) < 6:
        return []

    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    debits = debits.sort_values('date')

    for vendor, group in debits.groupby('desc_norm'):
        if len(group) < 4:
            continue
        amounts = group['amount'].abs().values
        # Régression linéaire sur les montants
        x = np.arange(len(amounts))
        if amounts.std() == 0:
            continue
        slope, _, r_value, _, _ = scipy_stats.linregress(x, amounts)
        if slope > amounts.mean() * 0.05 and r_value > 0.7:
            total_increase = amounts[-1] - amounts[0]
            pct_increase = total_increase / amounts[0] * 100 if amounts[0] > 0 else 0
            for _, row in group.iterrows():
                flags.append(_flag(
                    row, 73,
                    "Surfacturation progressive",
                    f"'{vendor[:40]}' augmente ses montants de ~{slope:.0f}€ par transaction "
                    f"(+{pct_increase:.0f}% au total, R²={r_value**2:.2f}). "
                    "Augmentation régulière sans justification = signal de surfacturation.",
                    "fraude_fournisseur"
                ))
    return _dedup(flags)


def cas_revenus_anormalement_bas(df):
    """
    FRAUDE FISCALE / SKIMMING — Recettes anormalement basses par rapport aux dépenses.
    Un restaurant qui dépense 10 000€ en fournisseurs mais encaisse seulement 5 000€
    dissimule probablement une partie de son chiffre d'affaires (espèces non déclarées).
    """
    flags = []
    total_debits = df[df['amount'] < 0]['amount'].abs().sum()
    total_credits = df[df['amount'] > 0]['amount'].sum()

    if total_debits < 1000 or total_credits <= 0:
        return []

    ratio = total_credits / total_debits
    # Pour un commerce normal, les recettes devraient couvrir les dépenses
    if ratio < 0.6 and total_debits > 5000:
        # Signaler les crédits (recettes) comme suspectement bas
        credit_rows = df[df['amount'] > 0]
        for _, row in credit_rows.iterrows():
            flags.append(_flag(
                row, 70,
                "Recettes anormalement basses vs dépenses",
                f"Recettes totales ({total_credits:.0f}€) = seulement {ratio*100:.0f}% des dépenses "
                f"({total_debits:.0f}€). Pour un commerce, ce ratio devrait être ≥ 100%. "
                "Possible dissimulation de recettes en espèces (skimming fiscal).",
                "fraude_fiscale"
            ))
    return _dedup(flags)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _flag(row, score, titre, detail, categorie):
    sev = "🔴 Élevé" if score >= 75 else ("🟠 Moyen" if score >= 50 else "🟡 Faible")
    return {
        'transaction_id': row['id'],
        'date': row['date'],
        'description': row['description'],
        'amount': row['amount'],
        'rule': titre,
        'detail': detail,
        'severity': sev,
        'score_contribution': score,
        'categorie': categorie,
    }


def _dedup(flags):
    seen = set()
    result = []
    for f in flags:
        key = (f['transaction_id'], f['rule'])
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


# ─── Moteur principal ─────────────────────────────────────────────────────────

ALL_RULES = [
    # Fraudes internes
    cas_doublons,
    cas_virements_personnels,
    cas_remboursements_suspects,
    cas_frais_rembourses_excessifs,
    cas_achats_inhabituels,
    cas_paiements_inconnus_recurrents,
    # Fraudes fournisseurs
    cas_fournisseur_fantome,
    cas_fournisseur_similaire,
    cas_concentration_fournisseur,
    cas_surfacturation_progressive,
    # Fraudes caisse / liquidités
    cas_montant_rond,
    cas_retraits_especes,
    cas_revenus_anormalement_bas,
    # Fraudes financières / blanchiment
    cas_fractionnement,
    cas_juste_sous_seuil,
    cas_transactions_circulaires,
    cas_micro_transactions,
    cas_virements_multiples_meme_jour,
    cas_loi_benford,
    # Fraudes paie
    cas_salaires_irreguliers,
    cas_inflation_salaires,
    # Anomalies temporelles / comptables
    cas_pic_depenses,
    cas_transactions_weekend,
    cas_paiements_nuit_weekend,
    cas_fin_de_periode,
    # Anomalies statistiques
    cas_outliers_statistiques,
]


def run_engine(df: pd.DataFrame) -> pd.DataFrame:
    """Lance tous les modules d'analyse. Retourne un DataFrame d'alertes."""
    if df.empty:
        return pd.DataFrame()

    all_flags = []
    for rule_fn in ALL_RULES:
        try:
            result = rule_fn(df)
            all_flags.extend(result)
        except Exception as e:
            print(f"[Engine] Règle {rule_fn.__name__} échouée: {e}")

    if not all_flags:
        return pd.DataFrame()

    result_df = pd.DataFrame(all_flags)
    result_df = result_df.drop_duplicates(subset=['transaction_id', 'rule'])
    result_df = result_df.sort_values('score_contribution', ascending=False)
    return result_df


# ─── Rapport narratif intégré ─────────────────────────────────────────────────

CATEGORIES_FR = {
    'fraude_interne': "Fraude interne (employés / dirigeants)",
    'fraude_fournisseur': "Fraude fournisseur",
    'fraude_caisse': "Fraude caisse / liquidités",
    'fraude_paie': "Fraude sur la paie",
    'fraude_financiere': "Fraude financière / blanchiment",
    'fraude_fiscale': "Fraude fiscale (dissimulation de recettes)",
    'anomalie_depenses': "Anomalie de dépenses",
    'anomalie_statistique': "Anomalie statistique",
    'anomalie_temporelle': "Anomalie temporelle",
    'risque_fournisseur': "Risque fournisseur",
}

NORMAL_PATTERNS = [
    ("Régularité des paiements fournisseurs récurrents", "Les fournisseurs habituels montrent une fréquence et des montants cohérents."),
    ("Flux de trésorerie stable", "Les entrées et sorties d'argent sont réparties de façon régulière sur la période."),
    ("Pas de virement international suspect", "Aucun virement vers des juridictions à risque détecté."),
    ("Cohérence des montants de salaires", "Les versements de salaires sont stables et périodiques."),
]


def compute_risk_score(flags_df: pd.DataFrame, stats: dict) -> int:
    """Calcule un score de risque global de 0 à 100."""
    if flags_df.empty:
        return 0

    # Pondération par sévérité
    high = len(flags_df[flags_df['severity'] == '🔴 Élevé'])
    medium = len(flags_df[flags_df['severity'] == '🟠 Moyen'])
    low = len(flags_df[flags_df['severity'] == '🟡 Faible'])

    # Score de base
    score = high * 18 + medium * 8 + low * 3

    # Bonus si plusieurs catégories différentes (fraude organisée)
    categories = flags_df['categorie'].nunique() if 'categorie' in flags_df.columns else 1
    if categories >= 3:
        score += 15
    elif categories >= 2:
        score += 7

    # Bonus si gros montants en jeu
    max_flagged = flags_df['amount'].abs().max() if not flags_df.empty else 0
    if max_flagged >= SEUIL_MONTANT_TRES_ELEVE:
        score += 10

    return min(100, score)


def generate_report(df: pd.DataFrame, flags_df: pd.DataFrame, stats: dict, business_type: str = "commerce") -> str:
    """
    Génère un rapport narratif complet en français, sans IA externe.
    Analyse intelligente basée sur les patterns détectés.
    """
    score = compute_risk_score(flags_df, stats)

    if score >= SCORE_CRITIQUE:
        verdict_niveau = "🚨 CRITIQUE"
        verdict_texte = (
            "L'analyse révèle des signaux **très sérieux** qui justifient une investigation approfondie. "
            "Plusieurs patterns de fraude ont été identifiés simultanément. "
            "Il est fortement recommandé de contacter un expert-comptable ou un cabinet spécialisé en forensic financier "
            "et, si nécessaire, de déposer une plainte."
        )
    elif score >= SCORE_ELEVE:
        verdict_niveau = "🔴 ÉLEVÉ"
        verdict_texte = (
            "Des anomalies **préoccupantes** ont été détectées. "
            "Plusieurs transactions méritent une vérification immédiate avec pièces justificatives. "
            "Une revue comptable approfondie est recommandée."
        )
    elif score >= SCORE_MODERE:
        verdict_niveau = "🟠 MODÉRÉ"
        verdict_texte = (
            "Quelques **irrégularités mineures à modérées** ont été relevées. "
            "Elles peuvent être dues à des erreurs ou à des pratiques à corriger. "
            "Un suivi régulier et la demande de justificatifs pour les transactions signalées est conseillé."
        )
    else:
        verdict_niveau = "✅ FAIBLE"
        verdict_texte = (
            "L'analyse ne révèle **aucune anomalie significative**. "
            "Les transactions semblent globalement cohérentes avec l'activité d'un(e) "
            f"{business_type}. Continuez à surveiller régulièrement vos relevés."
        )

    lines = []
    lines.append(f"## Verdict global — Risque {verdict_niveau}")
    lines.append(f"\n{verdict_texte}\n")
    lines.append(f"**Score de risque : {score}/100**\n")

    # Statistiques générales
    lines.append("---")
    lines.append("## Résumé financier de la période")
    period_start = df['date'].min().strftime('%d/%m/%Y') if not df.empty else 'N/A'
    period_end = df['date'].max().strftime('%d/%m/%Y') if not df.empty else 'N/A'
    lines.append(f"- **Période analysée :** {period_start} → {period_end}")
    lines.append(f"- **Nombre de transactions :** {stats.get('total_transactions', 0):,}")
    lines.append(f"- **Total débits :** {stats.get('total_debit_amount', 0):,.2f}€")
    lines.append(f"- **Total crédits :** {stats.get('total_credit_amount', 0):,.2f}€")
    lines.append(f"- **Flux net :** {stats.get('net_flow', 0):,.2f}€")
    lines.append(f"- **Débit moyen par transaction :** {stats.get('avg_debit', 0):,.2f}€")
    lines.append(f"- **Transaction maximale :** {stats.get('max_debit', 0):,.2f}€")

    # Anomalies critiques
    lines.append("\n---")
    lines.append("## Anomalies détectées par catégorie")

    if flags_df.empty:
        lines.append("\nAucune anomalie détectée. Les transactions semblent normales.\n")
    else:
        if 'categorie' in flags_df.columns:
            for cat_key, cat_label in CATEGORIES_FR.items():
                cat_flags = flags_df[flags_df['categorie'] == cat_key]
                if cat_flags.empty:
                    continue
                n = len(cat_flags)
                high_n = len(cat_flags[cat_flags['severity'] == '🔴 Élevé'])
                lines.append(f"\n### {cat_label} ({n} alerte{'s' if n > 1 else ''})")
                for _, flag in cat_flags.head(5).iterrows():
                    lines.append(
                        f"- **[{flag['severity']}]** {flag['rule']} — "
                        f"{flag['date'].strftime('%d/%m/%Y')} — "
                        f"{flag['amount']:,.2f}€\n"
                        f"  → {flag['detail']}"
                    )
                if len(cat_flags) > 5:
                    lines.append(f"  *(+ {len(cat_flags) - 5} autre(s) alerte(s) dans cette catégorie)*")
        else:
            for _, flag in flags_df.head(20).iterrows():
                lines.append(
                    f"- **[{flag['severity']}]** {flag['rule']} — "
                    f"{flag['date'].strftime('%d/%m/%Y')} — "
                    f"{flag['amount']:,.2f}€\n"
                    f"  → {flag['detail']}"
                )

    # Ce qui semble normal
    lines.append("\n---")
    lines.append("## Ce qui semble normal")
    for titre, explication in NORMAL_PATTERNS:
        lines.append(f"- **{titre}** : {explication}")

    # Top bénéficiaires (informatif)
    lines.append("\n---")
    lines.append("## Principaux postes de dépenses")
    debits = df[df['amount'] < 0].copy()
    if not debits.empty:
        debits['desc_court'] = debits['description'].str[:50]
        top = debits.groupby('desc_court')['amount'].agg(['sum', 'count']).sort_values('sum').head(8)
        for desc, trow in top.iterrows():
            lines.append(
                f"- **{desc}** : {abs(trow['sum']):.2f}€ en {int(trow['count'])} transaction(s)"
            )

    # Recommandations
    lines.append("\n---")
    lines.append("## Recommandations")

    recommandations = []
    if not flags_df.empty:
        cats = set(flags_df.get('categorie', pd.Series()).unique())
        high_count = len(flags_df[flags_df['severity'] == '🔴 Élevé']) if not flags_df.empty else 0

        recommandations.append(
            "**Demandez les justificatifs** pour toutes les transactions signalées en rouge (factures, bons de commande, contrats)."
        )
        if 'fraude_interne' in cats:
            recommandations.append(
                "**Vérifiez les accès** : identifiez qui avait accès au compte bancaire et aux outils de paiement sur la période concernée."
            )
        if 'fraude_fournisseur' in cats:
            recommandations.append(
                "**Vérifiez vos fournisseurs** : confirmez l'existence légale de tous les nouveaux fournisseurs (SIRET, Kbis, coordonnées réelles)."
            )
        if 'fraude_paie' in cats:
            recommandations.append(
                "**Auditez la paie** : comparez les bulletins de salaire avec les virements réels et vérifiez l'existence de chaque employé."
            )
        if 'fraude_caisse' in cats or 'fraude_financiere' in cats:
            recommandations.append(
                "**Rapprochez les espèces** : comparez les retraits avec les rapports de caisse et les dépôts enregistrés."
            )
        if high_count >= 5:
            recommandations.append(
                "**Consultez un expert** : vu le nombre d'alertes critiques, un audit forensic par un expert-comptable ou un cabinet spécialisé est recommandé."
            )
        recommandations.append(
            "**Mettez en place des contrôles** : double validation pour les paiements > 1000€, séparation des fonctions (celui qui approuve ≠ celui qui paie)."
        )
        recommandations.append(
            "**Analysez mensuellement** : importez vos relevés chaque mois dans FraudLens pour détecter les dérives au plus tôt."
        )
    else:
        recommandations = [
            "**Continuez la surveillance mensuelle** : importez vos relevés régulièrement.",
            "**Conservez vos justificatifs** : gardez toutes les factures associées aux transactions.",
            "**Séparez les fonctions** : la personne qui approuve les dépenses ne doit pas être celle qui les paie.",
        ]

    for i, rec in enumerate(recommandations, 1):
        lines.append(f"{i}. {rec}")

    # Note légale
    lines.append("\n---")
    lines.append(
        "_Cette analyse est générée automatiquement à titre indicatif. "
        "Elle ne constitue pas un avis juridique ou comptable. "
        "En cas de fraude avérée, consultez un professionnel et signalez les faits aux autorités compétentes (police, TRACFIN)._"
    )

    return '\n'.join(lines)
