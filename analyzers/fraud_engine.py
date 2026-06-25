"""
Moteur de détection de fraude v5 — calibré pour minimiser les faux positifs.

Principe fondamental (ACFE / forensic accounting) :
  Une règle ne doit flagguer que ce qu'une entreprise NORMALE ne peut pas expliquer facilement.

Ce qui est NORMAL dans un restaurant/commerce :
  - Montants ronds (loyer, abonnements, contrats) → NE PAS flagguer
  - Paiements weekend (restaurants livrent le samedi matin) → NE PAS flagguer
  - Un fournisseur dominant (Metro, Sysco) → NE PAS flagguer
  - Retraits DAB < 1 000€ → NE PAS flagguer
  - Nouveaux fournisseurs < 5 000€ → NE PAS flagguer

Ce qui NE PEUT PAS s'expliquer normalement :
  - Paiement en double exact vers le même bénéficiaire
  - Virement explicitement marqué "perso" depuis compte pro
  - 3+ paiements qui contournent un seuil réglementaire (structuring)
  - Deux fournisseurs au nom quasi-identique (typosquatting)
  - Argent qui sort puis revient (round-tripping)
  - Même employé payé 2 fois le même mois

Sources : ACFE 2024, TRACFIN 2025, Journal of Accountancy, ISACA.
"""

import pandas as pd
import numpy as np
from datetime import timedelta
from scipy import stats as scipy_stats
from difflib import SequenceMatcher


# ── Seuils réglementaires ──────────────────────────────────────────────────────
SEUIL_COSI        = 10_000
SEUIL_ESPECES_PRO =  1_000

SEUILS_STRUCTURING = [
    (9_000, 10_000),
    (4_500,  5_000),
    (900,    1_000),
]

SEV_CRITIQUE = "🔴 Critique"
SEV_ELEVE    = "🔴 Élevé"
SEV_MOYEN    = "🟠 Moyen"
SEV_FAIBLE   = "🟡 Faible"


# ═══════════════════════════════════════════════════════════════════════════════
# RÈGLES HAUTE CONFIANCE
# ═══════════════════════════════════════════════════════════════════════════════

def cas_doublons_exacts(df):
    """
    Paiement en double : même montant EXACT + même bénéficiaire dans les 7 jours.
    Impossible à justifier commercialement. Précision ACFE : ~85-90%.
    """
    flags = []
    df = df.copy().sort_values('date')
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:60]
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
            for idx in [i] + list(dupes.index):
                r = df.loc[idx]
                flags.append(_flag(r, 88 if n >= 3 else 80,
                    f"Doublon de paiement ({n}×)",
                    f"{n} paiements identiques de {abs(r['amount']):.2f}€ vers "
                    f"'{r['description'][:50]}' en 7 jours. "
                    f"Un doublon ne peut pas s'expliquer commercialement — "
                    f"double comptabilisation ou faux fournisseur.",
                    "fraude_interne"))
    return _dedup(flags)


def cas_virements_personnels(df):
    """
    Virement explicitement 'perso' ou 'personnel' depuis compte pro.
    Signal direct, impossible à confondre.
    """
    mots = ['perso', 'personnel', 'particulier', 'pret perso', 'cpte perso',
            'compte perso', 'virement perso', 'pret personnel']
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0:
            continue
        desc = row['description'].lower()
        if any(m in desc for m in mots):
            flags.append(_flag(row, 90,
                "Virement personnel depuis compte pro",
                f"'{row['description'][:60]}' — transfert personnel de {abs(row['amount']):.0f}€ "
                f"depuis le compte professionnel. Détournement de fonds potentiel.",
                "fraude_interne"))
    return flags


def cas_structuring(df):
    """
    Structuring : 3+ paiements vers le même bénéficiaire en 30 jours,
    chacun sous un seuil, mais le total le dépasse.
    Très difficile à justifier légitimement.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for low, high in SEUILS_STRUCTURING:
        for vendor, group in debits.groupby('desc_norm'):
            group = group.sort_values('date')
            for start_i in range(len(group)):
                start_date = group.iloc[start_i]['date']
                window = group[
                    (group['date'] >= start_date) &
                    (group['date'] <= start_date + timedelta(days=30))
                ]
                if len(window) < 3:
                    continue
                total      = window['amount'].abs().sum()
                max_single = window['amount'].abs().max()
                if total >= high * 0.85 and max_single < high * 0.95:
                    for _, row in window.iterrows():
                        flags.append(_flag(row, 87,
                            f"Structuring — seuil {high:,}€ contourné",
                            f"{len(window)} paiements à '{vendor[:35]}' : {total:,.0f}€ en 30 jours, "
                            f"chacun sous {high:,}€. Technique de fractionnement pour éviter TRACFIN.",
                            "fraude_financiere"))
                    break
    return _dedup(flags)


def cas_fournisseur_typosquatting(df):
    """
    Deux fournisseurs au nom quasi-identique (similarité 82-99%).
    Technique classique de fraude fournisseur : vrai Metro + faux 'Metr0'.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:50]
    vendors = [v for v in debits['desc_norm'].unique() if len(v) >= 6]
    seen_pairs = set()

    for i, v1 in enumerate(vendors):
        for v2 in vendors[i+1:]:
            pair = tuple(sorted([v1, v2]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            sim = SequenceMatcher(None, v1, v2).ratio()
            if 0.82 <= sim < 1.0:
                for vendor in [v1, v2]:
                    for _, row in debits[debits['desc_norm'] == vendor].iterrows():
                        flags.append(_flag(row, 85,
                            "Fournisseurs quasi-identiques (typosquatting)",
                            f"'{v1[:35]}' et '{v2[:35]}' ressemblent à {sim*100:.0f}%. "
                            f"Un vrai + un faux fournisseur. Vérifier SIRET et IBAN des deux.",
                            "fraude_fournisseur"))
    return _dedup(flags)


def cas_transactions_circulaires(df):
    """
    Argent qui sort puis revient à ±5% dans les 30 jours (round-tripping).
    Seuil 1 000€ minimum pour éviter les remboursements normaux.
    """
    flags = []
    df = df.copy().sort_values('date')
    debits  = df[(df['amount'] < 0) & (df['amount'].abs() >= 1_000)]
    credits = df[(df['amount'] > 0) & (df['amount'] >= 1_000)]

    for _, row_out in debits.iterrows():
        amt = abs(row_out['amount'])
        retour = credits[
            (credits['amount'].between(amt * 0.95, amt * 1.05)) &
            (credits['date'] > row_out['date']) &
            (credits['date'] <= row_out['date'] + timedelta(days=30))
        ]
        if not retour.empty:
            flags.append(_flag(row_out, 83,
                "Transaction circulaire (aller-retour)",
                f"Sortie de {amt:,.0f}€ suivie d'un retour de "
                f"{retour.iloc[0]['amount']:,.0f}€ en 30 jours. "
                f"Round-tripping ou blanchiment. Vérifier l'origine du retour.",
                "fraude_financiere"))
    return _dedup(flags)


def cas_doublon_salaire(df):
    """
    Même salarié reçoit 2+ versements le même mois.
    Impossible à justifier — doublon de paie ou employé fantôme.
    """
    mots_sal = ['salaire', 'paie', 'paye', 'acompte salaire']
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().str.contains('|'.join(mots_sal), na=False)]
    if len(sal) < 2:
        return []

    sal['desc_norm'] = sal['description'].str.lower().str.strip().str[:60]
    sal['month']     = sal['date'].dt.to_period('M')
    flags = []

    for (month, emp), group in sal.groupby(['month', 'desc_norm']):
        if len(group) >= 2:
            total = group['amount'].abs().sum()
            for _, row in group.iterrows():
                flags.append(_flag(row, 85,
                    "Doublon de salaire",
                    f"'{emp[:40]}' : {len(group)} versements en {month} "
                    f"(total : {total:,.0f}€). Doublon de paie ou employé fantôme.",
                    "fraude_paie"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# RÈGLES MOYENNE CONFIANCE
# ═══════════════════════════════════════════════════════════════════════════════

def cas_virements_multiples_meme_jour(df):
    """
    5+ virements importants (> 1 000€) le même jour vers des bénéficiaires DIFFÉRENTS.
    Seuil élevé pour ne pas flagguer les jours normaux de règlements multiples.
    """
    flags = []
    debits = df[(df['amount'] < 0) & (df['amount'].abs() >= 1_000)].copy()
    if debits.empty:
        return []
    debits['date_only'] = debits['date'].dt.date
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for day, group in debits.groupby('date_only'):
        n_vendors = group['desc_norm'].nunique()
        total = group['amount'].abs().sum()
        if len(group) >= 5 and n_vendors >= 4 and total >= SEUIL_COSI:
            for _, row in group.iterrows():
                flags.append(_flag(row, 75,
                    f"{len(group)} virements importants le même jour",
                    f"Le {day} : {len(group)} virements ≥ 1 000€ vers {n_vendors} bénéficiaires "
                    f"différents (total {total:,.0f}€). Pattern d'Account Takeover ou fraude interne.",
                    "fraude_financiere"))
    return _dedup(flags)


def cas_nouveau_gros_fournisseur(df):
    """
    Premier paiement > 5 000€ vers un bénéficiaire jamais vu.
    Seuil élevé — changer de petit fournisseur est NORMAL.
    Un premier paiement de 8 000€ à une société inconnue, non.
    """
    flags = []
    df = df.copy().sort_values('date')
    df['desc_norm'] = df['description'].str.lower().str.strip().str[:80]
    known = set()

    for _, row in df.iterrows():
        desc = row['desc_norm']
        amt  = abs(row['amount'])
        if desc not in known:
            known.add(desc)
            if amt >= 5_000 and row['amount'] < 0:
                flags.append(_flag(row, 68,
                    f"Premier paiement important — {amt:,.0f}€",
                    f"Première apparition de '{row['description'][:50]}' : {amt:,.0f}€. "
                    f"Vérifier SIRET, Kbis et IBAN. "
                    f"Normal si c'est un fournisseur connu — sinon investiguer.",
                    "fraude_fournisseur"))
    return flags


def cas_achats_personnels_evidents(df):
    """
    Dépenses clairement personnelles sur compte pro.
    Mots-clés très spécifiques — seulement ce qui est CLAIREMENT non-professionnel.
    """
    categories_perso = {
        'casino': "casino", 'paris sportif': "paris sportifs", ' pmu ': "pari mutuel",
        'fdj': "loterie", 'bijouterie': "bijouterie", 'joaillerie': "bijouterie",
        'coiffeur': "coiffeur", ' spa ': "spa/bien-être",
    }
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0 or abs(row['amount']) < 30:
            continue
        desc = ' ' + row['description'].lower() + ' '
        for mot, cat in categories_perso.items():
            if mot in desc:
                flags.append(_flag(row, 72,
                    f"Dépense personnelle — {cat}",
                    f"'{row['description'][:60]}' : {cat} ({abs(row['amount']):.0f}€) "
                    f"sur compte professionnel.",
                    "fraude_interne"))
                break
    return flags


def cas_retraits_especes_excessifs(df):
    """
    Retraits espèces seulement si :
    - Un seul retrait > 3 000€ (très anormal), OU
    - Total cumulé > seuil COSI TRACFIN (10 000€)
    Les petits retraits DAB réguliers sont NORMAUX.
    """
    mots = ['retrait', 'dab', 'atm', 'cash retrait', 'retrait espece']
    retraits = df[
        (df['amount'] < 0) &
        (df['description'].str.lower().str.contains('|'.join(mots), na=False))
    ]
    if retraits.empty:
        return []

    total = retraits['amount'].abs().sum()
    flags = []

    for _, row in retraits.iterrows():
        amt = abs(row['amount'])
        if amt > 3_000:
            flags.append(_flag(row, 75,
                f"Retrait espèces important — {amt:,.0f}€",
                f"Retrait unique de {amt:,.0f}€ en espèces. "
                f"Un retrait normal dépasse rarement 500€. Vérifier l'utilisation.",
                "fraude_caisse"))
        elif total > SEUIL_COSI and amt > 800:
            flags.append(_flag(row, 65,
                f"Retraits espèces cumulés — {total:,.0f}€ (> seuil TRACFIN)",
                f"Total retraits : {total:,.0f}€ > {SEUIL_COSI:,}€ COSI TRACFIN. "
                f"Déclaration de soupçon obligatoire.",
                "fraude_caisse"))
    return _dedup(flags)


def cas_surfacturation_progressive(df):
    """
    Fournisseur qui augmente ses factures régulièrement.
    Seuil strict : 5+ factures, pente > 5% de la moyenne, R > 0.80.
    """
    flags = []
    debits = df[df['amount'] < 0].copy().sort_values('date')
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for vendor, group in debits.groupby('desc_norm'):
        if len(group) < 5:
            continue
        amounts = group['amount'].abs().values
        if amounts.std() == 0 or amounts.mean() == 0:
            continue
        x = np.arange(len(amounts))
        slope, _, r, _, _ = scipy_stats.linregress(x, amounts)
        pct_drift = slope / amounts.mean()

        if pct_drift > 0.05 and r > 0.80:
            drift_total = amounts[-1] - amounts[0]
            for _, row in group.iterrows():
                flags.append(_flag(row, 70,
                    f"Surfacturation progressive (+{pct_drift*100:.0f}%/facture)",
                    f"'{vendor[:40]}' : +{slope:.0f}€ par facture "
                    f"(+{drift_total:,.0f}€ total, R={r:.2f}). "
                    f"Sans avenant contractuel = signal de surfacturation.",
                    "fraude_fournisseur"))
    return _dedup(flags)


def cas_inflation_salaire(df):
    """
    Salaire en hausse constante sur 4+ mois, +15% total minimum.
    """
    mots_sal = ['salaire', 'paie', 'paye']
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().str.contains('|'.join(mots_sal), na=False)]
    if sal.empty:
        return []
    sal['desc_norm'] = sal['description'].str.lower().str.strip().str[:60]
    flags = []

    for emp, group in sal.groupby('desc_norm'):
        if len(group) < 4:
            continue
        amounts = group.sort_values('date')['amount'].abs().values
        diffs   = np.diff(amounts)
        if np.all(diffs > 0) and (amounts[-1] - amounts[0]) / amounts[0] > 0.15:
            drift = amounts[-1] - amounts[0]
            for _, row in group.iterrows():
                flags.append(_flag(row, 65,
                    f"Inflation salariale — +{(amounts[-1]-amounts[0])/amounts[0]*100:.0f}%",
                    f"'{emp[:40]}' : hausse constante sur {len(group)} mois "
                    f"(+{drift:,.0f}€ total). Vérifier les avenants signés.",
                    "fraude_paie"))
    return _dedup(flags)


def cas_juste_sous_seuil(df):
    """
    Montant entre 95% et 100% d'un seuil réglementaire important.
    Score FAIBLE seul — sert surtout à renforcer d'autres alertes.
    Seulement pour montants > 1 000€ pour éviter les coïncidences.
    """
    seuils = [1_000, 3_000, 5_000, 10_000]
    flags = []
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        if amt < 1_000:
            continue
        for s in seuils:
            if s * 0.95 <= amt < s:
                flags.append(_flag(row, 38,
                    f"Montant proche du seuil {s:,}€",
                    f"{amt:,.0f}€ = {s - amt:.0f}€ sous le seuil de {s:,}€. "
                    f"Signal faible seul — préoccupant si combiné avec d'autres anomalies.",
                    "fraude_financiere"))
                break
    return flags


# ═══════════════════════════════════════════════════════════════════════════════
# LOI DE BENFORD — Uniquement > 200 transactions, seuil strict
# ═══════════════════════════════════════════════════════════════════════════════

def cas_benford(df):
    """
    Loi de Benford avec test KS + MAD > 0.025 (seuil strict).
    Uniquement si > 200 transactions.
    """
    amounts = df['amount'].abs()
    freq = amounts.value_counts()
    repeated = set(freq[freq > 3].index)
    amounts = amounts[~amounts.isin(repeated) & (amounts >= 10)]

    if len(amounts) < 200:
        return []

    first_digits = amounts.apply(
        lambda x: int(str(f"{x:.2f}").replace('.', '').lstrip('0')[0])
    )
    observed = {d: (first_digits == d).sum() / len(first_digits) for d in range(1, 10)}
    expected = {d: np.log10(1 + 1/d) for d in range(1, 10)}

    mad = np.mean([abs(observed.get(d, 0) - expected[d]) for d in range(1, 10)])
    if mad < 0.025:
        return []

    obs_vals = [observed.get(d, 0) for d in range(1, 10)]
    exp_vals = [expected[d] for d in range(1, 10)]
    _, ks_pvalue = scipy_stats.ks_2samp(obs_vals, exp_vals)
    if ks_pvalue > 0.05:
        return []

    deviations   = {d: observed.get(d, 0) - expected[d] for d in range(1, 10)}
    most_deviant = max(deviations, key=lambda d: abs(deviations[d]))

    suspect_txns = df[df['amount'].abs().apply(
        lambda x: str(f"{x:.2f}").replace('.', '').lstrip('0')[:1] == str(most_deviant)
        if x >= 10 else False
    )]

    flags = []
    for _, row in suspect_txns.head(10).iterrows():
        flags.append(_flag(row, 72,
            f"Loi de Benford — chiffre '{most_deviant}' anormal",
            f"Chiffre '{most_deviant}' : {observed.get(most_deviant,0)*100:.1f}% "
            f"vs {expected[most_deviant]*100:.1f}% attendu. MAD={mad:.4f}. "
            f"Technique utilisée par le fisc (DGFiP).",
            "fraude_financiere"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS & MOTEUR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def _flag(row, score, titre, detail, categorie):
    if score >= 85:
        sev = SEV_CRITIQUE
    elif score >= 65:
        sev = SEV_ELEVE
    elif score >= 40:
        sev = SEV_MOYEN
    else:
        sev = SEV_FAIBLE
    return {
        'transaction_id':     row['id'],
        'date':               row['date'],
        'description':        row['description'],
        'amount':             row['amount'],
        'rule':               titre,
        'detail':             detail,
        'severity':           sev,
        'score_contribution': score,
        'categorie':          categorie,
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
    cas_doublons_exacts,
    cas_virements_personnels,
    cas_structuring,
    cas_fournisseur_typosquatting,
    cas_transactions_circulaires,
    cas_doublon_salaire,
    cas_virements_multiples_meme_jour,
    cas_nouveau_gros_fournisseur,
    cas_achats_personnels_evidents,
    cas_retraits_especes_excessifs,
    cas_surfacturation_progressive,
    cas_inflation_salaire,
    cas_juste_sous_seuil,
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


CATEGORIES_FR = {
    'fraude_interne':       "Fraude interne (employés / dirigeants)",
    'fraude_fournisseur':   "Fraude fournisseur",
    'fraude_caisse':        "Fraude caisse / espèces",
    'fraude_paie':          "Fraude sur la paie",
    'fraude_financiere':    "Fraude financière / TRACFIN",
    'anomalie_statistique': "Anomalie statistique",
}


def compute_risk_score(flags_df: pd.DataFrame, stats: dict) -> int:
    if flags_df.empty:
        return 0

    critique = (flags_df['severity'] == SEV_CRITIQUE).sum()
    eleve    = (flags_df['severity'] == SEV_ELEVE).sum()
    moyen    = (flags_df['severity'] == SEV_MOYEN).sum()
    faible   = (flags_df['severity'] == SEV_FAIBLE).sum()

    score = critique * 18 + eleve * 10 + moyen * 5 + faible * 1

    if 'categorie' in flags_df.columns:
        cats = flags_df['categorie'].nunique()
        if cats >= 3:
            score += 10
        elif cats >= 2:
            score += 5

    return min(100, score)


def generate_report(df: pd.DataFrame, flags_df: pd.DataFrame, stats: dict, business_type: str = "commerce") -> str:
    score   = compute_risk_score(flags_df, stats)
    n_flags = len(flags_df) if not flags_df.empty else 0

    if score >= 60:
        verdict_niv = "🚨 ÉLEVÉ"
        verdict_txt = ("Des **anomalies significatives** ont été détectées. "
                       "Plusieurs transactions méritent une vérification immédiate.")
    elif score >= 30:
        verdict_niv = "🟠 MODÉRÉ"
        verdict_txt = "Quelques **irrégularités** relevées. Vérifiez les justificatifs."
    else:
        verdict_niv = "✅ NORMAL"
        verdict_txt = (f"L'analyse ne révèle aucune anomalie significative. "
                       f"Le relevé semble cohérent avec l'activité d'un(e) {business_type.lower()}.")

    lines = [
        f"## Verdict — Risque {verdict_niv}\n\n{verdict_txt}\n\n**Score : {score}/100**\n",
        "---",
        "## Résumé financier",
        f"- **Transactions :** {stats.get('total_transactions', 0):,}",
        f"- **Débits :** {stats.get('total_debit_amount', 0):,.2f}€",
        f"- **Crédits :** {stats.get('total_credit_amount', 0):,.2f}€",
        f"- **Flux net :** {stats.get('net_flow', 0):,.2f}€",
    ]

    if not flags_df.empty:
        lines += ["\n---", "## Anomalies détectées"]
        for cat_key, cat_label in CATEGORIES_FR.items():
            if 'categorie' not in flags_df.columns:
                break
            cat_flags = flags_df[flags_df['categorie'] == cat_key]
            if cat_flags.empty:
                continue
            lines.append(f"\n### {cat_label} ({len(cat_flags)})")
            for _, flag in cat_flags.head(5).iterrows():
                lines.append(
                    f"- **[{flag['severity']}]** {flag['rule']} — "
                    f"{flag['date'].strftime('%d/%m/%Y')} — {flag['amount']:,.2f}€\n"
                    f"  → {flag['detail']}"
                )

    lines += ["\n---",
              "_Analyse ACFE 2024, TRACFIN 2025, ISACA. Ne constitue pas un avis juridique._"]
    return '\n'.join(lines)
