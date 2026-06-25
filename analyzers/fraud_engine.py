"""
Moteur de détection de fraude — basé sur les recherches ACFE 2024, TRACFIN 2025,
forensic accounting (ISACA, Journal of Accountancy, PMC).

Méthodes :
- Règles métier déterministes (meilleur ratio signal/bruit sur TPE/PME < 500 txns)
- IQR/MAD robuste (remplace le Z-score, validé pour petits volumes)
- Loi de Benford avec test KS (uniquement si > 200 transactions)
- Vélocité et patterns temporels (ACFE Anti-Fraud Data Analytics Tests)

NE PAS utiliser : Z-score (faux positifs massifs sur données non-normales < 500 txns)
"""

import pandas as pd
import numpy as np
from datetime import timedelta
from scipy import stats as scipy_stats
from difflib import SequenceMatcher


# ─── Seuils réglementaires France (TRACFIN / Code monétaire et financier) ─────
SEUIL_COSI_TRACFIN     = 10_000   # € — COSI obligatoire (Communication Systématique)
SEUIL_ESPECES_PRO      = 1_000    # € — Limite légale paiement espèces inter-pros
SEUIL_APPROBATION_PME  = 500      # € — Seuil d'approbation direction classique PME

# Zones de structuring (juste en dessous des seuils TRACFIN)
SEUILS_STRUCTURING = [
    (9_000,  10_000),  # Zone COSI
    (4_500,   5_000),  # Seuil interne courant
    (2_700,   3_000),  # Seuil courant
    (900,    1_000),   # Seuil espèces
    (450,      500),   # Seuil approbation PME
]

# Sévérités
SEV_CRITIQUE = "🔴 Critique"
SEV_ELEVE    = "🔴 Élevé"
SEV_MOYEN    = "🟠 Moyen"
SEV_FAIBLE   = "🟡 Faible"

SCORE_CRITIQUE = 70
SCORE_ELEVE    = 45
SCORE_MODERE   = 20


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 1 — FRAUDES INTERNES (employés / dirigeants)
# Source : ACFE 2024 Report — 43% des fraudes PME sont internes
# ═══════════════════════════════════════════════════════════════════════════════

def cas_doublons_exacts(df):
    """
    ACFE Test #1 — Doublons de paiement.
    Même montant + même bénéficiaire dans un délai de 7 jours.
    Un doublon = possible erreur. Deux = suspect. Trois = fraude probable.
    """
    flags = []
    df = df.copy()
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:80]
    seen = set()

    for i, row in df.iterrows():
        key = (round(abs(row['amount']), 2), row['desc_norm'])
        if key in seen:
            continue
        dupes = df[
            (df.index != i) &
            (df['amount'].round(2) == round(row['amount'], 2)) &
            (df['desc_norm'] == row['desc_norm']) &
            ((df['date'] - row['date']).abs() <= timedelta(days=7))
        ]
        if not dupes.empty:
            seen.add(key)
            n = len(dupes) + 1
            score = 90 if n >= 3 else 82
            for idx in [i] + list(dupes.index):
                r = df.loc[idx]
                flags.append(_flag(r, score, "Doublon de paiement",
                    f"{n} transactions identiques de {abs(r['amount']):.2f}€ vers "
                    f"'{r['description'][:50]}' en 7 jours. "
                    f"{'Fraude probable' if n >= 3 else 'Très suspect'} — double comptabilisation ou fournisseur fantôme.",
                    "fraude_interne"))
    return _dedup(flags)


def cas_virements_personnels(df):
    """Virements explicitement marqués comme personnels depuis compte professionnel."""
    mots = ['perso', 'personnel', 'particulier', 'pret perso', 'cpte perso', 'compte perso']
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0:
            continue
        desc = row['description'].lower()
        if any(m in desc for m in mots):
            flags.append(_flag(row, 88,
                "Virement vers compte personnel",
                f"'{row['description'][:60]}' — virement depuis compte professionnel "
                f"vers compte personnel. Détournement de fonds potentiel.",
                "fraude_interne"))
    return flags


def cas_frais_excessifs(df):
    """
    ACFE : Les notes de frais fictives représentent 21% des fraudes en PME.
    Seuil : > 15% des débits totaux OU > 3 000€ sur la période.
    """
    mots = ['frais', 'note de frais', 'ndf', 'remb frais', 'indemnité', 'indemnite', 'expense']
    frais = [row for _, row in df.iterrows()
             if row['amount'] < 0 and any(m in row['description'].lower() for m in mots)]
    if not frais:
        return []

    total_frais  = sum(abs(r['amount']) for r in frais)
    total_debits = df[df['amount'] < 0]['amount'].abs().sum()
    pct = total_frais / total_debits * 100 if total_debits > 0 else 0

    if pct < 15 and total_frais < 3_000:
        return []

    flags = []
    for row in frais:
        flags.append(_flag(row, 72,
            "Notes de frais excessives (ACFE)",
            f"Total frais remboursés : {total_frais:,.0f}€ = {pct:.1f}% des dépenses. "
            f"Selon l'ACFE, seuil d'alerte à 15%. Demander tous les justificatifs originaux.",
            "fraude_interne"))
    return _dedup(flags)


def cas_achats_non_professionnels(df):
    """Dépenses personnelles imputées au compte professionnel."""
    categories = {
        'bijou': "bijouterie", 'joaill': "bijouterie",
        'casino': "casino", 'paris sportif': "paris sportifs", 'pmu': "pari mutuel",
        'spa': "spa/bien-être", 'coiff': "coiffeur",
        'vetement': "vêtements", 'vêtement': "vêtements", 'zara': "boutique mode",
        'fnac': "électronique grand public", 'darty': "électroménager",
    }
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0:
            continue
        desc = row['description'].lower()
        for mot, cat in categories.items():
            if mot in desc and abs(row['amount']) > 50:
                flags.append(_flag(row, 70,
                    f"Dépense personnelle — {cat}",
                    f"'{row['description'][:60]}' : achat en {cat} depuis le compte professionnel. "
                    f"Montant : {abs(row['amount']):.2f}€.",
                    "fraude_interne"))
                break
    return flags


def cas_prelevements_caches(df):
    """
    Petits prélèvements récurrents discrets vers un bénéficiaire inconnu.
    Technique de détournement progressif : 150€/mois × 24 mois = 3 600€ invisible.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    debits['month'] = debits['date'].dt.to_period('M')

    all_totals = debits.groupby('desc_norm')['amount'].sum().abs()

    for vendor, group in debits.groupby('desc_norm'):
        months = group['month'].nunique()
        avg    = group['amount'].abs().mean()
        total  = group['amount'].abs().sum()
        rank   = (all_totals > all_totals[vendor]).sum()
        is_minor = rank > len(all_totals) * 0.35  # Pas dans le top 35% des fournisseurs

        if months >= 3 and 15 < avg < 600 and total > 150 and is_minor:
            for _, row in group.iterrows():
                flags.append(_flag(row, 65,
                    "Prélèvement récurrent non identifié",
                    f"'{vendor[:40]}' : {avg:.0f}€/mois pendant {months} mois "
                    f"(total : {total:.0f}€). Abonnement non autorisé ou détournement discret.",
                    "fraude_interne"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 2 — FRAUDES FOURNISSEURS (ghost vendors, surfacturation)
# Source : ACFE — fraude fournisseur = 19% des cas, perte médiane 117 000 €
# ═══════════════════════════════════════════════════════════════════════════════

def cas_fournisseur_fantome(df):
    """
    Premier gros paiement vers bénéficiaire jamais vu.
    Ghost vendor classique : compte créé, 1-2 paiements, puis compte fermé.
    Seuil ACFE : > 1 500 € pour un premier virement.
    """
    flags = []
    df = df.copy().sort_values('date')
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:80]
    seen = {}

    for _, row in df.iterrows():
        desc = row['desc_norm']
        amt  = abs(row['amount'])
        if desc not in seen:
            seen[desc] = row['date']
            if amt >= 1_500 and row['amount'] < 0:
                flags.append(_flag(row, 78,
                    "Nouveau fournisseur — premier gros paiement",
                    f"Premier paiement de {amt:,.2f}€ vers '{row['description'][:50]}' "
                    f"— bénéficiaire jamais apparu dans l'historique. "
                    f"Vérifier SIRET, Kbis et existence réelle.",
                    "fraude_fournisseur"))
    return flags


def cas_fournisseur_nom_similaire(df):
    """
    Deux fournisseurs au nom quasi-identique = possible fournisseur fantôme.
    Ex : 'METRO CASH CARRY' et 'METRO CASH CARR' (typosquatting).
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:50]
    vendors = debits['desc_norm'].unique().tolist()
    seen_pairs = set()

    for i, v1 in enumerate(vendors):
        for v2 in vendors[i+1:]:
            if len(v1) < 6 or len(v2) < 6:
                continue
            pair = tuple(sorted([v1, v2]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            sim = SequenceMatcher(None, v1, v2).ratio()
            if 0.82 <= sim < 1.0:
                for vendor in [v1, v2]:
                    for _, row in debits[debits['desc_norm'] == vendor].iterrows():
                        flags.append(_flag(row, 82,
                            "Fournisseurs quasi-identiques (typosquatting)",
                            f"'{v1[:35]}' et '{v2[:35]}' ont une similarité de {sim*100:.0f}%. "
                            f"Technique classique : un vrai fournisseur, l'autre est fictif. "
                            f"Vérifier les IBAN et SIRET des deux.",
                            "fraude_fournisseur"))
    return _dedup(flags)


def cas_concentration_fournisseur(df):
    """
    Un fournisseur représente >40% des débits totaux.
    Risque de surfacturation ou de complicité.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    total = debits['amount'].abs().sum()
    if total == 0:
        return []

    by_vendor = debits.groupby('desc_norm')['amount'].agg(['sum', 'count'])
    for vendor, vrow in by_vendor.iterrows():
        pct = abs(vrow['sum']) / total * 100
        if pct > 40 and abs(vrow['sum']) > 5_000:
            for _, row in debits[debits['desc_norm'] == vendor].iterrows():
                flags.append(_flag(row, 65,
                    f"Concentration excessive — {pct:.0f}% des dépenses",
                    f"'{vendor[:40]}' représente {pct:.1f}% de tous les débits "
                    f"({abs(vrow['sum']):,.0f}€ en {int(vrow['count'])} transactions). "
                    f"Risque de surfacturation ou de complicité.",
                    "fraude_fournisseur"))
    return _dedup(flags)


def cas_surfacturation_progressive(df):
    """
    Un fournisseur augmente régulièrement ses montants (slope significatif).
    +5%/mois passe inaperçu mais fait +80% sur un an.
    Détection : régression linéaire sur les montants du fournisseur.
    """
    flags = []
    debits = df[df['amount'] < 0].copy().sort_values('date')
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for vendor, group in debits.groupby('desc_norm'):
        if len(group) < 4:
            continue
        amounts = group['amount'].abs().values
        if amounts.std() == 0:
            continue
        x = np.arange(len(amounts))
        slope, _, r, _, _ = scipy_stats.linregress(x, amounts)
        if slope > amounts.mean() * 0.04 and r > 0.65:
            increase = amounts[-1] - amounts[0]
            pct = increase / amounts[0] * 100 if amounts[0] > 0 else 0
            for _, row in group.iterrows():
                flags.append(_flag(row, 73,
                    f"Surfacturation progressive (+{pct:.0f}%)",
                    f"'{vendor[:40]}' : facturation en hausse de {slope:.0f}€ par transaction "
                    f"(+{pct:.0f}% au total, corrélation R={r:.2f}). "
                    f"Augmentation sans avenant signé = signal de surfacturation.",
                    "fraude_fournisseur"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 3 — STRUCTURING & FRAUDE FINANCIÈRE (TRACFIN)
# Source : TRACFIN 2025 — 215 410 déclarations de soupçon en 2024 (+13,2%)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_structuring(df):
    """
    TRACFIN — Fractionnement intentionnel pour passer sous les seuils.
    Pattern : plusieurs transactions vers le même bénéficiaire,
    chacune sous un seuil, mais le total le dépasse.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for low, high in SEUILS_STRUCTURING:
        for vendor, group in debits.groupby('desc_norm'):
            group = group.sort_values('date')
            for start_i in range(len(group)):
                window = group[
                    (group['date'] >= group.iloc[start_i]['date']) &
                    (group['date'] <= group.iloc[start_i]['date'] + timedelta(days=30))
                ]
                if len(window) < 3:
                    continue
                total = window['amount'].abs().sum()
                max_single = window['amount'].abs().max()
                # Chaque transaction sous le seuil, mais le total le dépasse
                if total >= high * 0.85 and max_single < high:
                    for _, row in window.iterrows():
                        flags.append(_flag(row, 88,
                            f"Structuring — contournement seuil {high:,}€",
                            f"{len(window)} paiements à '{vendor[:40]}' totalisant {total:,.0f}€ "
                            f"en 30 jours, chacun sous {high:,}€. "
                            f"Technique de structuring (smurfing) pour éviter le seuil TRACFIN.",
                            "fraude_financiere"))
                    break
    return _dedup(flags)


def cas_juste_sous_seuil(df):
    """
    ACFE Test — Transactions dans la zone 'juste sous un seuil' réglementaire.
    Zone d'alerte : entre (seuil - 5%) et seuil.
    """
    flags = []
    seuils_plats = [500, 1_000, 3_000, 5_000, 10_000, 15_000]
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        if amt < 100:
            continue
        for s in seuils_plats:
            marge = s * 0.05  # 5% sous le seuil
            if s - marge <= amt < s:
                pct_sous = (s - amt) / s * 100
                flags.append(_flag(row, 80,
                    f"Montant juste sous le seuil de {s:,}€ ({pct_sous:.1f}% en dessous)",
                    f"{amt:,.2f}€ = {s - amt:.2f}€ sous le seuil de {s:,}€. "
                    f"Positionnement intentionnel pour éviter un contrôle. "
                    f"Technique détectée par TRACFIN et l'ACFE.",
                    "fraude_financiere"))
                break
    return flags


def cas_transactions_circulaires(df):
    """
    Blanchiment — argent qui sort puis revient à ±5% dans les 30 jours.
    Signe de round-tripping ou gonflement artificiel du CA.
    """
    flags = []
    df = df.copy().sort_values('date')
    debits  = df[df['amount'] < 0]
    credits = df[df['amount'] > 0]

    for _, row_out in debits.iterrows():
        amt = abs(row_out['amount'])
        if amt < 500:
            continue
        retour = credits[
            (credits['amount'].between(amt * 0.95, amt * 1.05)) &
            (credits['date'] > row_out['date']) &
            (credits['date'] <= row_out['date'] + timedelta(days=30))
        ]
        if not retour.empty:
            flags.append(_flag(row_out, 85,
                "Transaction circulaire (aller-retour)",
                f"Débit de {amt:,.2f}€ suivi d'un crédit quasi-identique "
                f"({retour.iloc[0]['amount']:,.2f}€) dans les 30 jours. "
                f"Schéma de round-tripping ou blanchiment.",
                "fraude_financiere"))
    return _dedup(flags)


def cas_virements_multiples_meme_jour(df):
    """
    Plusieurs gros virements vers des bénéficiaires différents le même jour.
    Signal de prise de contrôle de compte (Account Takeover Fraud).
    """
    flags = []
    debits = df[(df['amount'] < 0) & (df['amount'].abs() >= 1_000)].copy()
    if debits.empty:
        return []
    debits['date_only'] = debits['date'].dt.date

    for day, group in debits.groupby('date_only'):
        if len(group) >= 3 and group['amount'].abs().sum() >= SEUIL_COSI_TRACFIN:
            total = group['amount'].abs().sum()
            for _, row in group.iterrows():
                flags.append(_flag(row, 78,
                    "Multiples virements importants — même jour",
                    f"Le {day} : {len(group)} virements ≥ 1 000€ totalisant {total:,.0f}€ "
                    f"vers des bénéficiaires différents. "
                    f"Pattern caractéristique d'une fraude au compte (Account Takeover).",
                    "fraude_financiere"))
    return _dedup(flags)


def cas_card_testing(df):
    """
    Test de carte volée : nombreuses micro-transactions (< 5€) vers le même bénéficiaire.
    Le fraudeur teste si la carte fonctionne avant une grosse fraude.
    """
    flags = []
    micro = df[df['amount'].abs() < 5].copy()
    if len(micro) < 5:
        return []
    micro['desc_norm'] = micro['description'].str.lower().str.strip().str[:60]

    for vendor, group in micro.groupby('desc_norm'):
        if len(group) >= 5:
            for _, row in group.iterrows():
                flags.append(_flag(row, 76,
                    "Card testing — micro-transactions suspectes",
                    f"{len(group)} transactions inférieures à 5€ vers '{vendor[:40]}'. "
                    f"Pattern classique de test de carte volée avant fraude importante.",
                    "fraude_financiere"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 4 — FRAUDE CAISSE & SKIMMING
# Source : ACFE — skimming = 11,9% des fraudes PME, perte médiane 53 000 €
# ═══════════════════════════════════════════════════════════════════════════════

def cas_montants_ronds(df):
    """
    ACFE Test — Taux de montants ronds anormalement élevé.
    Dépenses réelles = rarement rondes. Fraudes manuelles = souvent rondes.
    Seuil ACFE : si > 25% des transactions se terminent par ,00 → signal.
    """
    flags = []
    debits = df[df['amount'] < 0]
    if len(debits) < 5:
        return []

    # Taux global de montants ronds
    n_ronds = sum(1 for _, r in debits.iterrows() if abs(r['amount']) % 100 == 0 and abs(r['amount']) >= 100)
    taux = n_ronds / len(debits) * 100

    for _, row in debits.iterrows():
        amt = abs(row['amount'])
        if amt >= 200 and amt % 100 == 0:
            context = f"Taux de montants ronds sur ce relevé : {taux:.0f}% (seuil ACFE : 25%)." if taux > 25 else ""
            flags.append(_flag(row,
                75 if taux > 25 else 58,
                "Montant rond suspect",
                f"Transaction de {amt:,.0f}€ exactement. "
                f"Les fraudes manuelles tendent vers des montants ronds (facile à justifier verbalement). "
                f"{context}",
                "fraude_caisse"))
    return flags


def cas_retraits_especes(df):
    """
    Retraits espèces répétés ou importants.
    Seuil TRACFIN COSI : 10 000€ cumulés/mois.
    Limite légale espèces inter-pros : 1 000€.
    """
    mots = ['retrait', 'espece', 'espèce', 'dab', 'atm', 'cash', 'billet']
    retraits = [row for _, row in df.iterrows()
                if row['amount'] < 0 and any(m in row['description'].lower() for m in mots)]
    if not retraits:
        return []

    total = sum(abs(r['amount']) for r in retraits)
    flags = []

    for row in retraits:
        amt = abs(row['amount'])
        score = 80 if total > SEUIL_COSI_TRACFIN else (68 if amt > SEUIL_ESPECES_PRO else 50)
        flags.append(_flag(row, score,
            "Retrait espèces" + (" — dépassement seuil TRACFIN" if total > SEUIL_COSI_TRACFIN else ""),
            f"Retrait de {amt:,.0f}€. Total espèces sur la période : {total:,.0f}€. "
            f"{'⚠️ Dépasse le seuil COSI TRACFIN de 10 000€ (déclaration obligatoire).' if total > SEUIL_COSI_TRACFIN else ''} "
            f"Limite légale paiements espèces entre professionnels : {SEUIL_ESPECES_PRO:,}€.",
            "fraude_caisse"))
    return _dedup(flags)


def cas_skimming_recettes(df):
    """
    ACFE — Skimming : ratio recettes/dépenses anormalement bas.
    Pour un commerce viable, les recettes doivent couvrir les dépenses.
    Si le ratio est < 70% → possible dissimulation de recettes en espèces.
    Perte médiane skimming : 53 000€ (ACFE 2024).
    """
    total_debits  = df[df['amount'] < 0]['amount'].abs().sum()
    total_credits = df[df['amount'] > 0]['amount'].sum()

    if total_debits < 2_000 or total_credits <= 0:
        return []

    ratio = total_credits / total_debits
    if ratio >= 0.70:
        return []

    flags = []
    for _, row in df[df['amount'] > 0].iterrows():
        flags.append(_flag(row, 75,
            f"Skimming probable — recettes = {ratio*100:.0f}% des dépenses",
            f"Recettes ({total_credits:,.0f}€) couvrent seulement {ratio*100:.0f}% des dépenses "
            f"({total_debits:,.0f}€). Ratio normal ≥ 100% pour un commerce. "
            f"Possible dissimulation de recettes espèces (skimming fiscal). "
            f"Comparer avec les Z-rapports du terminal CB.",
            "fraude_fiscale"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 5 — FRAUDE À LA PAIE
# Source : ACFE — payroll fraud = 8,5% des cas, perte médiane 120 000 €, détection : 18 mois
# ═══════════════════════════════════════════════════════════════════════════════

def cas_irrégularites_salaires(df):
    """
    Ghost employees et doublons de paie.
    Signal : même mois avec >2 versements de salaire, ou masse salariale anormale.
    """
    mots = ['salaire', 'paie', 'paye', 'acompte salaire', 'avance salaire']
    sal = [row for _, row in df.iterrows()
           if row['amount'] < 0 and any(m in row['description'].lower() for m in mots)]
    if len(sal) < 2:
        return []

    sal_df = pd.DataFrame(sal).sort_values('date')
    sal_df['month'] = sal_df['date'].dt.to_period('M')
    sal_df['desc_norm'] = sal_df['description'].str.lower().str.strip().str[:60]

    flags = []
    # Test 1 : même mois, même bénéficiaire, 2+ paiements
    for (month, emp), group in sal_df.groupby(['month', 'desc_norm']):
        if len(group) >= 2:
            total = group['amount'].abs().sum()
            for _, row in group.iterrows():
                flags.append(_flag(row, 85,
                    "Doublon de salaire",
                    f"'{emp[:40]}' reçoit {len(group)} versements en {month} "
                    f"(total : {total:,.0f}€). Ghost employee ou doublon de paie.",
                    "fraude_paie"))

    # Test 2 : inflation progressive (paie padding)
    for emp, group in sal_df.groupby('desc_norm'):
        if len(group) < 3:
            continue
        amounts = group.sort_values('date')['amount'].abs().values
        diffs = np.diff(amounts)
        if np.all(diffs > 0) and diffs.mean() > 30:
            drift = amounts[-1] - amounts[0]
            for _, row in group.iterrows():
                flags.append(_flag(row, 77,
                    f"Inflation progressive du salaire (+{drift:,.0f}€)",
                    f"'{emp[:40]}' : salaire en hausse de {diffs.mean():.0f}€/mois "
                    f"(+{drift:,.0f}€ au total). "
                    f"Sans avenant signé, c'est un signal de manipulation de paie.",
                    "fraude_paie"))

    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 6 — ANOMALIES TEMPORELLES & VÉLOCITÉ
# Source : ACFE Anti-Fraud Data Analytics Tests (vélocité = top 8 des tests)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_velocite_fournisseur(df):
    """
    ACFE Test #5 — Vélocité : un fournisseur qui accélère soudainement.
    Normal : 1-2 paiements/mois. Suspect : 6-8 paiements/mois sans explication.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    debits['month'] = debits['date'].dt.to_period('M')

    for vendor, group in debits.groupby('desc_norm'):
        monthly = group.groupby('month').size()
        if len(monthly) < 2:
            continue
        mean_freq = monthly.mean()
        for month, count in monthly.items():
            if count >= max(mean_freq * 3, 5):
                spike_txns = group[group['month'] == month]
                for _, row in spike_txns.iterrows():
                    flags.append(_flag(row, 72,
                        f"Pic de vélocité fournisseur ({count}×/mois)",
                        f"'{vendor[:40]}' : {count} paiements en {month} "
                        f"(normale : {mean_freq:.1f}/mois). "
                        f"Multiplication par {count/mean_freq:.1f} — suspect.",
                        "anomalie_activite"))
    return _dedup(flags)


def cas_pic_depenses_hebdomadaire(df):
    """
    Une semaine avec des dépenses 3× supérieures à la normale.
    Utilise la MAD (Median Absolute Deviation) — robuste aux outliers,
    recommandé par l'ISACA pour les audits sur petits volumes.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if len(debits) < 10:
        return []

    debits['week'] = debits['date'].dt.to_period('W')
    weekly = debits.groupby('week')['amount'].sum().abs()

    if len(weekly) < 3:
        return []

    median_w = weekly.median()
    mad_w    = (weekly - median_w).abs().median()

    if mad_w == 0:
        return []

    for week, total in weekly.items():
        # Seuil MAD : score > 3.5 (équivalent de 3.5 sigma en distribution normale)
        mad_score = abs(total - median_w) / (1.4826 * mad_w)
        if mad_score > 3.5:
            spike_txns = debits[debits['week'] == week]
            for _, row in spike_txns.iterrows():
                flags.append(_flag(row, 68,
                    f"Pic de dépenses (MAD score : {mad_score:.1f})",
                    f"Semaine du {week.start_time.strftime('%d/%m/%Y')} : "
                    f"{total:,.0f}€ vs médiane de {median_w:,.0f}€ "
                    f"(score MAD : {mad_score:.1f} — seuil : 3.5). "
                    f"Méthode IQR/MAD recommandée par l'ISACA.",
                    "anomalie_activite"))
    return _dedup(flags)


def cas_reprise_apres_silence(df):
    """
    Transaction après une longue absence d'activité.
    Signal de prise de contrôle de compte ou d'activité dissimulée.
    """
    flags = []
    df_sorted = df.sort_values('date').copy()
    df_sorted['gap'] = df_sorted['date'].diff().dt.days

    gaps = df_sorted['gap'].dropna()
    if gaps.empty:
        return []

    median_gap = gaps.median()
    mad_gap    = (gaps - median_gap).abs().median()

    if mad_gap == 0:
        return []

    for _, row in df_sorted.iterrows():
        if pd.isna(row['gap']):
            continue
        gap_score = abs(row['gap'] - median_gap) / (1.4826 * mad_gap + 1)
        if gap_score > 5 and row['gap'] > max(median_gap * 4, 30):
            flags.append(_flag(row, 55,
                f"Reprise après silence de {row['gap']:.0f} jours",
                f"Aucune activité pendant {row['gap']:.0f} jours "
                f"(normale : {median_gap:.0f} jours). "
                f"Possible prise de contrôle du compte ou activité suspendue anormalement.",
                "anomalie_activite"))
    return flags


def cas_fin_de_mois_suspect(df):
    """
    ACFE — Manipulation comptable de fin de période.
    Concentration anormale de transactions les 3 derniers jours du mois.
    """
    flags = []
    df = df.copy()
    df['dom'] = df['date'].dt.day
    df['month'] = df['date'].dt.to_period('M')

    end_mask = df['dom'] >= 28
    end_txns = df[end_mask]
    if end_txns.empty:
        return []

    avg_daily = df['amount'].abs().sum() / max(df['date'].nunique(), 1)
    if avg_daily == 0:
        return []

    for month, group in end_txns.groupby('month'):
        n_days = max(group['dom'].nunique(), 1)
        avg_end = group['amount'].abs().sum() / n_days
        if avg_end > avg_daily * 5 and group['amount'].abs().sum() > 2_000:
            for _, row in group.iterrows():
                flags.append(_flag(row, 68,
                    "Pic de transactions en fin de mois",
                    f"Activité 5× supérieure à la normale les derniers jours de {month}. "
                    f"Technique de manipulation comptable avant clôture (ACFE Test #8 — cohérence saisonnière).",
                    "anomalie_activite"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOC 7 — LOI DE BENFORD (uniquement si > 200 transactions)
# Source : ISACA Journal 2011, Journal of Accountancy 2022
# Test KS (Kolmogorov-Smirnov) — plus fiable que chi-2 sur petits volumes
# Seuil : MAD > 0.015 = risque élevé (Journal of Accountancy)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_benford(df):
    """
    Loi de Benford — détection de montants fabriqués.
    Appliquée UNIQUEMENT si > 200 transactions (fiabilité KS).
    Utilise MAD (Mean Absolute Deviation) — seuil 0.015 = risque.
    Ne s'applique pas aux montants fixes (loyers, salaires fixes).
    """
    amounts = df['amount'].abs()
    # Exclure les montants fixes (même montant > 3 fois = abonnement/loyer)
    freq = amounts.value_counts()
    repeated = set(freq[freq > 3].index)
    amounts = amounts[~amounts.isin(repeated)]
    amounts = amounts[amounts >= 10]

    if len(amounts) < 200:
        return []

    # Distribution observée
    first_digits = amounts.apply(lambda x: int(str(f"{x:.2f}").replace('.', '').lstrip('0')[0]))
    observed = {d: (first_digits == d).sum() / len(first_digits) for d in range(1, 10)}

    # Distribution théorique Benford
    expected = {d: np.log10(1 + 1/d) for d in range(1, 10)}

    # MAD (Mean Absolute Deviation from Benford)
    mad = np.mean([abs(observed.get(d, 0) - expected[d]) for d in range(1, 10)])

    if mad < 0.015:  # Seuil Journal of Accountancy
        return []

    # Test KS (Kolmogorov-Smirnov)
    obs_vals = [observed.get(d, 0) for d in range(1, 10)]
    exp_vals = [expected[d] for d in range(1, 10)]
    ks_stat, ks_pvalue = scipy_stats.ks_2samp(obs_vals, exp_vals)

    if ks_pvalue > 0.05 and mad < 0.025:
        return []

    # Identifier le chiffre le plus déviant
    deviations = {d: observed.get(d, 0) - expected[d] for d in range(1, 10)}
    most_deviant = max(deviations, key=lambda d: abs(deviations[d]))
    dev_pct = deviations[most_deviant] * 100
    exp_pct = expected[most_deviant] * 100

    flags = []
    suspect_txns = df[df['amount'].abs().apply(
        lambda x: str(f"{x:.2f}").replace('.', '').lstrip('0')[:1] == str(most_deviant)
        if x >= 10 else False
    )]

    for _, row in suspect_txns.head(15).iterrows():
        flags.append(_flag(row, 78 if mad > 0.025 else 65,
            f"Loi de Benford — chiffre '{most_deviant}' anormal",
            f"Le chiffre '{most_deviant}' apparaît dans {observed.get(most_deviant,0)*100:.1f}% "
            f"des montants (attendu : {exp_pct:.1f}%). "
            f"MAD = {mad:.4f} (seuil : 0.015). Test KS p-value = {ks_pvalue:.4f}. "
            f"Technique utilisée par le fisc (DGFiP) et l'ACFE.",
            "fraude_financiere"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS & MOTEUR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def _flag(row, score, titre, detail, categorie):
    if score >= 85:
        sev = SEV_CRITIQUE
    elif score >= 70:
        sev = SEV_ELEVE
    elif score >= 50:
        sev = SEV_MOYEN
    else:
        sev = SEV_FAIBLE
    return {
        'transaction_id':    row['id'],
        'date':              row['date'],
        'description':       row['description'],
        'amount':            row['amount'],
        'rule':              titre,
        'detail':            detail,
        'severity':          sev,
        'score_contribution': score,
        'categorie':         categorie,
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


ALL_RULES = [
    # Fraude interne
    cas_doublons_exacts,
    cas_virements_personnels,
    cas_frais_excessifs,
    cas_achats_non_professionnels,
    cas_prelevements_caches,
    # Fraude fournisseurs
    cas_fournisseur_fantome,
    cas_fournisseur_nom_similaire,
    cas_concentration_fournisseur,
    cas_surfacturation_progressive,
    # Fraude financière / TRACFIN
    cas_structuring,
    cas_juste_sous_seuil,
    cas_transactions_circulaires,
    cas_virements_multiples_meme_jour,
    cas_card_testing,
    # Fraude caisse / skimming
    cas_montants_ronds,
    cas_retraits_especes,
    cas_skimming_recettes,
    # Fraude paie
    cas_irrégularites_salaires,
    # Anomalies temporelles (IQR/MAD — pas Z-score)
    cas_velocite_fournisseur,
    cas_pic_depenses_hebdomadaire,
    cas_reprise_apres_silence,
    cas_fin_de_mois_suspect,
    # Loi de Benford (> 200 transactions uniquement)
    cas_benford,
]


def run_engine(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    all_flags = []
    for fn in ALL_RULES:
        try:
            all_flags.extend(fn(df))
        except Exception as e:
            print(f"[Engine] {fn.__name__} : {e}")
    if not all_flags:
        return pd.DataFrame()
    result = pd.DataFrame(all_flags)
    result = result.drop_duplicates(subset=['transaction_id', 'rule'])
    result = result.sort_values('score_contribution', ascending=False)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SCORING & RAPPORT
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORIES_FR = {
    'fraude_interne':    "Fraude interne (employés / dirigeants)",
    'fraude_fournisseur':"Fraude fournisseur (ghost vendors, surfacturation)",
    'fraude_caisse':     "Fraude caisse / liquidités",
    'fraude_paie':       "Fraude sur la paie",
    'fraude_financiere': "Fraude financière / blanchiment (TRACFIN)",
    'fraude_fiscale':    "Fraude fiscale (dissimulation de recettes)",
    'anomalie_activite': "Anomalie d'activité",
}


def compute_risk_score(flags_df: pd.DataFrame, stats: dict) -> int:
    if flags_df.empty:
        return 0
    critique = len(flags_df[flags_df['severity'] == SEV_CRITIQUE])
    eleve    = len(flags_df[flags_df['severity'] == SEV_ELEVE])
    moyen    = len(flags_df[flags_df['severity'] == SEV_MOYEN])
    faible   = len(flags_df[flags_df['severity'] == SEV_FAIBLE])

    score = critique * 22 + eleve * 14 + moyen * 7 + faible * 2

    cats = flags_df['categorie'].nunique() if 'categorie' in flags_df.columns else 1
    if cats >= 4:
        score += 20
    elif cats >= 3:
        score += 10
    elif cats >= 2:
        score += 5

    max_amt = flags_df['amount'].abs().max() if not flags_df.empty else 0
    if max_amt >= SEUIL_COSI_TRACFIN:
        score += 12
    elif max_amt >= 5_000:
        score += 6

    return min(100, score)


def generate_report(df: pd.DataFrame, flags_df: pd.DataFrame, stats: dict, business_type: str = "commerce") -> str:
    score = compute_risk_score(flags_df, stats)
    n_flags = len(flags_df) if not flags_df.empty else 0

    if score >= SCORE_CRITIQUE:
        verdict_niv = "🚨 CRITIQUE"
        verdict_txt = (
            "L'analyse révèle des **signaux très sérieux** nécessitant une investigation immédiate. "
            "Plusieurs typologies de fraude ont été détectées simultanément. "
            "Il est fortement recommandé de contacter un expert-comptable forensic "
            "et, si nécessaire, de signaler les faits à TRACFIN ou aux autorités."
        )
    elif score >= SCORE_ELEVE:
        verdict_niv = "🔴 ÉLEVÉ"
        verdict_txt = (
            "Des **anomalies préoccupantes** ont été détectées. "
            "Plusieurs transactions méritent une vérification immédiate avec pièces justificatives. "
            "Un audit comptable approfondi est recommandé."
        )
    elif score >= SCORE_MODERE:
        verdict_niv = "🟠 MODÉRÉ"
        verdict_txt = (
            "Quelques **irrégularités** ont été relevées. "
            "Elles peuvent être des erreurs ou des pratiques à corriger. "
            "Demandez les justificatifs pour les transactions signalées."
        )
    else:
        verdict_niv = "✅ FAIBLE"
        verdict_txt = (
            "L'analyse ne révèle **aucune anomalie significative**. "
            f"Les transactions semblent cohérentes avec l'activité d'un(e) {business_type.lower()}."
        )

    lines = []
    lines.append(f"## Verdict — Risque {verdict_niv}\n\n{verdict_txt}\n\n**Score de risque : {score}/100**\n")
    lines.append("---")
    lines.append("## Résumé financier")
    lines.append(f"- **Période :** {df['date'].min().strftime('%d/%m/%Y')} → {df['date'].max().strftime('%d/%m/%Y')}")
    lines.append(f"- **Transactions :** {stats.get('total_transactions',0):,}")
    lines.append(f"- **Total débits :** {stats.get('total_debit_amount',0):,.2f}€")
    lines.append(f"- **Total crédits :** {stats.get('total_credit_amount',0):,.2f}€")
    lines.append(f"- **Flux net :** {stats.get('net_flow',0):,.2f}€")

    if not flags_df.empty:
        lines.append("\n---")
        lines.append("## Anomalies par catégorie")
        for cat_key, cat_label in CATEGORIES_FR.items():
            cat_flags = flags_df[flags_df['categorie'] == cat_key] if 'categorie' in flags_df.columns else pd.DataFrame()
            if cat_flags.empty:
                continue
            lines.append(f"\n### {cat_label} ({len(cat_flags)} alerte(s))")
            for _, flag in cat_flags.head(6).iterrows():
                lines.append(
                    f"- **[{flag['severity']}]** {flag['rule']} — "
                    f"{flag['date'].strftime('%d/%m/%Y')} — {flag['amount']:,.2f}€\n"
                    f"  → {flag['detail']}"
                )
            if len(cat_flags) > 6:
                lines.append(f"  *(+ {len(cat_flags)-6} autre(s))*")

    lines.append("\n---")
    lines.append("## Recommandations")
    recs = ["**Demandez les justificatifs** pour chaque transaction signalée en rouge (factures, bons de commande, contrats)."]
    if not flags_df.empty and 'categorie' in flags_df.columns:
        cats = set(flags_df['categorie'].unique())
        if 'fraude_interne' in cats:
            recs.append("**Vérifiez les accès bancaires** : qui avait accès au compte sur la période concernée ?")
        if 'fraude_fournisseur' in cats:
            recs.append("**Vérifiez les fournisseurs** : SIRET valide, Kbis, coordonnées réelles, IBAN professionnel.")
        if 'fraude_paie' in cats:
            recs.append("**Auditez la paie** : bulletins de salaire vs virements réels, liste des employés actifs.")
        if 'fraude_financiere' in cats or score >= SCORE_CRITIQUE:
            recs.append("**Consultez un expert forensic** et envisagez une déclaration de soupçon à TRACFIN.")
        recs.append("**Mettez en place des contrôles** : double validation pour les paiements > 1 000€, séparation des fonctions.")
    for i, r in enumerate(recs, 1):
        lines.append(f"{i}. {r}")

    lines.append("\n---")
    lines.append("_Analyse basée sur les référentiels ACFE 2024, TRACFIN 2025, ISACA. "
                 "Ne constitue pas un avis juridique. En cas de fraude avérée, consultez un professionnel._")

    return '\n'.join(lines)
