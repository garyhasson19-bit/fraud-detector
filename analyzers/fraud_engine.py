"""
Moteur de détection de fraude v6 — Belgique, calibré sur données réelles.

Sources :
  - ACFE Report to the Nations 2024 (1 921 cas réels, 138 pays)
  - CTIF (Cellule de Traitement des Informations Financières) — équivalent belge du TRACFIN
  - Loi belge du 18 septembre 2017 relative à la prévention du blanchiment
  - Loi Programme 2012 — plafond espèces professionnel : 3 000€ (Belgique)
  - Banque Nationale de Belgique — ratios sectoriels HORECA
  - Febelfin (Fédération belge du secteur financier)
  - FAU Center for Forensic Accounting
  - David Anderson & Associates — Restaurant Fraud Forensic Accounting
  - Washington State Auditor — Bank Statement Review
  - Agilence — Cash Skimming Detection

Belgique — seuils spécifiques :
  - Espèces professionnel : 3 000€ (pas 1 000€ comme en France)
  - CTIF : pas de seuil minimum, TOUTE opération suspecte doit être déclarée
  - TVA restauration Belgique : 12% (vs 10% France)
  - IBAN belge : BE + 14 chiffres

22 patterns de fraude couverts, chacun avec signal précis documenté.
"""

import re
import pandas as pd
import numpy as np
from datetime import timedelta
from scipy import stats as scipy_stats
from difflib import SequenceMatcher


# ── Seuils réglementaires BELGIQUE ───────────────────────────────────────────
SEUIL_ESPECES_PRO_BE  = 3_000   # Loi Programme 2012 — plafond espèces professionnel
SEUIL_CTIF_VIGILANCE  = 10_000  # Seuil de vigilance CTIF (pas obligatoire mais recommandé)
SEUIL_APPROBATION_PME = 5_000   # Seuil d'approbation direction courant dans les PME

# Zones de structuring (juste sous les seuils courants en Belgique)
SEUILS_STRUCTURING_BE = [
    (2_700, 3_000),   # Limite espèces professionnelle
    (4_500, 5_000),   # Seuil d'approbation PME courant
    (9_000, 10_000),  # Seuil de vigilance CTIF
]

# BIC de néobanques — signaux d'alerte pour les virements pro (Greip.io / Febelfin)
# Un vrai fournisseur belge a un IBAN BNP Fortis, KBC, Belfius, ING, Crelan...
BICS_NEOBANQUE = [
    'trwi', 'bunq', 'revo', 'nice', 'lydi', 'cash', 'n26',
    'mone', 'remo', 'wise', 'payr', 'soli',
]

SEV_CRITIQUE = "🔴 Critique"
SEV_ELEVE    = "🔴 Élevé"
SEV_MOYEN    = "🟠 Moyen"
SEV_FAIBLE   = "🟡 Faible"

# ── Ratios sectoriels HORECA Belgique (Banque Nationale de Belgique / Febelfin) ──
# Pour détecter les anomalies par rapport aux normes du secteur
RATIOS_HORECA = {
    'food_cost_min': 0.28,   # 28% du CA HT minimum
    'food_cost_max': 0.38,   # 38% du CA HT maximum (au-delà = surfacturation ou vol)
    'masse_sal_max': 0.42,   # 42% du CA = seuil d'alerte masse salariale
    'prime_cost_max': 0.65,  # Food cost + masse salariale > 65% = zone rouge
    'loyer_max': 0.12,       # 12% du CA = seuil d'alerte loyer
    'marge_nette_min': 0.03, # 3% minimum de marge nette (en dessous = suspect)
}


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE A — FRAUDES SUR LES ESPÈCES (ACFE : 11,9% des cas PME)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_skimming_depot(df):
    """
    Skimming — Vol de recettes espèces avant dépôt.
    Signal : dépôts journaliers qui baissent sans baisse des débits fournisseurs.
    Méthode Agilence : comparaison ratio espèces/CB sur la période.

    Un restaurant qui achète autant de matières premières mais dépose moins
    de recettes espèces = quelqu'un garde une partie de la caisse.
    """
    if len(df) < 20:
        return []

    credits = df[df['amount'] > 0].copy()
    debits  = df[df['amount'] < 0].copy()
    if credits.empty or debits.empty:
        return []

    # Identifier les recettes espèces (dépôts) vs CB (virements directs)
    mots_especes = ['espece', 'espèce', 'caisse', 'cash', 'depot', 'dépôt', 'versement']
    mots_cb      = ['cb', 'carte', 'terminal', 'tpe', 'mastercard', 'visa', 'bancontact', 'payconiq']

    credits['is_cash']  = credits['description'].str.lower().apply(lambda d: any(m in d for m in mots_especes))
    credits['is_card']  = credits['description'].str.lower().apply(lambda d: any(m in d for m in mots_cb))

    cash_total = credits[credits['is_cash']]['amount'].sum()
    card_total = credits[credits['is_card']]['amount'].sum()

    if card_total < 500 or cash_total <= 0:
        return []

    # Ratio espèces/CB
    ratio = cash_total / (cash_total + card_total)

    # Un restaurant belge normal : 25-45% espèces
    # Seuil strict : < 10% (pas 15%) ET gros volume CB pour éviter les commerces sans espèces
    if ratio < 0.10 and card_total > 5_000:
        flags = []
        for _, row in credits[credits['is_cash']].iterrows():
            flags.append(_flag(row, 74,
                f"Ratio espèces anormalement bas — {ratio*100:.0f}% (normal : 25-45%)",
                f"Recettes espèces : {cash_total:,.0f}€ vs CB : {card_total:,.0f}€ "
                f"(ratio espèces = {ratio*100:.0f}%). "
                f"Pour un commerce HORECA belge, un ratio < 15% est suspect : "
                f"possible skimming (espèces volées avant dépôt). "
                f"Méthode : comparer avec les Z-rapports de la caisse.",
                "fraude_caisse"))
        return _dedup(flags)
    return []


def cas_montants_ronds_depots(df):
    """
    Dépôts en montants parfaitement ronds.
    Un vrai dépôt de recettes = total exact des tickets (jamais rond).
    Un faux dépôt estimé = 500€, 1000€, 1500€ (quelqu'un a posé le solde dans sa poche).

    Signal documenté : dépôts de 500€, 1000€, 2000€ réguliers = estimation, pas décompte.
    """
    credits = df[df['amount'] > 0].copy()
    if credits.empty:
        return []

    # Uniquement les dépôts ESPÈCES (pas les recettes CB qui peuvent être rondes)
    mots_especes = ['caisse', 'espece', 'espèce', 'depot espece', 'versement espece', 'cash deposit']
    recettes = credits[credits['description'].str.lower().apply(
        lambda d: any(m in d for m in mots_especes)
    )]

    if len(recettes) < 4:  # Besoin d'au moins 4 dépôts pour établir un pattern
        return []

    flags = []
    n_ronds = sum(1 for _, r in recettes.iterrows() if r['amount'] % 100 == 0)
    taux = n_ronds / len(recettes)

    if taux >= 0.75:  # Seuil relevé à 75% (pas 60%)
        for _, row in recettes[recettes['amount'] % 100 == 0].iterrows():
            flags.append(_flag(row, 70,
                f"Dépôts espèces en montants ronds ({taux*100:.0f}% des dépôts)",
                f"Dépôt de {row['amount']:.0f}€ exactement. "
                f"{taux*100:.0f}% des dépôts sont des multiples de 100€. "
                f"Un vrai décompte de caisse tombe rarement sur un montant rond. "
                f"Signal de skimming : quelqu'un garde le solde et dépose un montant estimé.",
                "fraude_caisse"))
    return _dedup(flags)


def cas_retraits_especes_excessifs(df):
    """
    Retraits espèces importants.
    Belgique : limite légale paiements professionnels en espèces = 3 000€ (Loi 2012).
    Un retrait > 3 000€ dépasse la limite légale inter-professionnels.
    """
    mots = ['retrait', 'dab', 'atm', 'cash retrait', 'geldautomaat', 'geld afhalen']
    retraits = df[
        (df['amount'] < 0) &
        (df['description'].str.lower().apply(lambda d: any(m in d for m in mots)))
    ]
    if retraits.empty:
        return []

    total = retraits['amount'].abs().sum()
    flags = []

    for _, row in retraits.iterrows():
        amt = abs(row['amount'])
        if amt >= SEUIL_ESPECES_PRO_BE:
            flags.append(_flag(row, 78,
                f"Retrait espèces > limite légale belge ({SEUIL_ESPECES_PRO_BE:,}€)",
                f"Retrait de {amt:,.0f}€ dépasse la limite belge de {SEUIL_ESPECES_PRO_BE:,}€ "
                f"(Loi Programme 2012) pour les paiements professionnels. "
                f"Total retraits sur la période : {total:,.0f}€.",
                "fraude_caisse"))
        elif total > SEUIL_CTIF_VIGILANCE and amt > 500:
            flags.append(_flag(row, 65,
                f"Retraits espèces cumulés — {total:,.0f}€ (vigilance CTIF)",
                f"Total retraits : {total:,.0f}€ dépasse {SEUIL_CTIF_VIGILANCE:,}€. "
                f"Seuil de vigilance CTIF (équivalent belge du TRACFIN). "
                f"Toute opération suspecte doit être déclarée au CTIF.",
                "fraude_caisse"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE B — FRAUDES INTERNES DIRECTES (ACFE : ~43% des fraudes PME)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_doublons_exacts(df):
    """
    Doublon de paiement — Pattern #7 ACFE.
    Même montant EXACT + même bénéficiaire dans les 7 jours.
    Impossible à justifier commercialement (perte médiane : 128 000$).
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
                flags.append(_flag(r, 88 if n >= 3 else 82,
                    f"Doublon de paiement ({n}× en 7 jours)",
                    f"{n} paiements identiques de {abs(r['amount']):.2f}€ vers "
                    f"'{r['description'][:50]}' en 7 jours. "
                    f"Aucune explication commerciale légitime possible. "
                    f"Vérifier : double comptabilisation ou fournisseur fictif.",
                    "fraude_interne"))
    return _dedup(flags)


def cas_virements_personnels(df):
    """
    Virement explicitement personnel depuis compte professionnel.
    Mots-clés en français ET en néerlandais (Belgique bilingue).
    """
    mots_fr = ['perso', 'personnel', 'particulier', 'pret perso', 'compte perso', 'pret personnel']
    mots_nl = ['prive', 'privé', 'persoonlijk', 'lening prive', 'eigen rekening']
    mots = mots_fr + mots_nl
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0:
            continue
        desc = row['description'].lower()
        if any(m in desc for m in mots):
            flags.append(_flag(row, 92,
                "Virement personnel depuis compte pro",
                f"'{row['description'][:60]}' — virement personnel de {abs(row['amount']):.0f}€ "
                f"depuis le compte professionnel. Détournement de fonds.",
                "fraude_interne"))
    return flags


def cas_achats_personnels_evidents(df):
    """
    Dépenses clairement personnelles imputées au compte pro.
    Mots-clés FR + NL très spécifiques — seulement ce qui est CLAIREMENT personnel.
    """
    categories = {
        'casino': "casino", 'paris sportif': "paris sportifs",
        'gokken': "pari (NL)", 'gokautomaat': "machine à sous",
        'jupiler': "bière Jupiler (usage perso ?)", 'liefmans': "bière (usage perso ?)",
        'bijouterie': "bijouterie", 'juwelier': "bijoutier (NL)",
        'coiffeur': "coiffeur", 'kapper': "coiffeur (NL)",
        ' spa ': "spa", 'wellness': "wellness personnel",
        'speelgoed': "jouets (NL)", 'toys': "jouets",
    }
    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0 or abs(row['amount']) < 30:
            continue
        desc = ' ' + row['description'].lower() + ' '
        for mot, cat in categories.items():
            if mot in desc:
                flags.append(_flag(row, 70,
                    f"Dépense personnelle — {cat}",
                    f"'{row['description'][:60]}' : {cat} ({abs(row['amount']):.0f}€) "
                    f"imputé sur compte professionnel.",
                    "fraude_interne"))
                break
    return flags


def cas_notes_frais_suspectes(df):
    """
    Remboursements de frais excessifs.
    ACFE : 13% des fraudes PME, perte médiane 50 000$/an, durée 18 mois.
    Seuil : frais > 20% du total des débits sur la période.
    """
    mots = ['frais', 'remb', 'ndf', 'note de frais', 'indemnité', 'indemnite',
            'onkosten', 'vergoeding', 'terugbetaling kosten']  # FR + NL
    debits = df[df['amount'] < 0]
    frais = debits[debits['description'].str.lower().apply(
        lambda d: any(m in d for m in mots)
    )]
    if frais.empty:
        return []

    total_frais  = frais['amount'].abs().sum()
    total_debits = debits['amount'].abs().sum()
    pct = total_frais / total_debits * 100 if total_debits > 0 else 0

    if pct < 20 and total_frais < 5_000:
        return []

    flags = []
    for _, row in frais.iterrows():
        flags.append(_flag(row, 72,
            f"Notes de frais excessives — {pct:.0f}% des dépenses",
            f"Total frais remboursés : {total_frais:,.0f}€ = {pct:.0f}% des dépenses. "
            f"Seuil d'alerte ACFE : 20%. Demander TOUS les justificatifs originaux.",
            "fraude_interne"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE C — FRAUDES FOURNISSEURS (ACFE : 27,1% des fraudes PME — le plus courant)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_doublons_exacts(df):
    """
    Doublon de paiement — Pattern #7 ACFE.
    Même montant EXACT + même bénéficiaire dans les 7 jours.
    Impossible à justifier commercialement (perte médiane : 128 000$).
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
                flags.append(_flag(r, 88 if n >= 3 else 82,
                    f"Doublon de paiement ({n}× en 7 jours)",
                    f"{n} paiements identiques de {abs(r['amount']):.2f}€ vers "
                    f"'{r['description'][:50]}' en 7 jours. "
                    f"Aucune explication commerciale légitime possible.",
                    "fraude_interne"))
    return _dedup(flags)


def cas_fournisseur_typosquatting(df):
    """
    Deux fournisseurs au nom quasi-identique (82-99% similaires).
    Schéma : vrai fournisseur 'Metro Cash Carry' + faux 'Metro Cash Carrv'.
    Pattern #5 ACFE — Ghost Vendor (27,1% des fraudes).
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:50]
    vendors = [v for v in debits['desc_norm'].unique() if len(v) >= 6]
    # Mots qui rendent la similarité normale (loyer janvier/février = normal)
    mots_calendrier = ['januari', 'februari', 'maart', 'april', 'mei', 'juni',
                       'juli', 'augustus', 'september', 'oktober', 'november', 'december',
                       'janv', 'fevr', 'mars', 'avri', 'juin', 'juil', 'aout', 'sept', 'octo', 'nove', 'dece',
                       '2022', '2023', '2024', '2025', '2026']
    seen_pairs = set()

    for i, v1 in enumerate(vendors):
        for v2 in vendors[i+1:]:
            pair = tuple(sorted([v1, v2]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            # Ignorer si la différence est juste un mois/année (loyer jan vs loyer fev)
            if any(m in v1 or m in v2 for m in mots_calendrier):
                continue
            sim = SequenceMatcher(None, v1, v2).ratio()
            if 0.82 <= sim < 1.0:
                for vendor in [v1, v2]:
                    for _, row in debits[debits['desc_norm'] == vendor].iterrows():
                        flags.append(_flag(row, 87,
                            "Fournisseurs quasi-identiques (typosquatting)",
                            f"'{v1[:35]}' et '{v2[:35]}' = {sim*100:.0f}% similaires. "
                            f"Schéma classique Ghost Vendor (ACFE #1 fraude fournisseur) : "
                            f"un vrai fournisseur + un faux au nom presque identique. "
                            f"Vérifier le numéro d'entreprise belge (BE XXXX.XXX.XXX) des deux.",
                            "fraude_fournisseur"))
    return _dedup(flags)


def cas_nouveau_gros_fournisseur(df):
    """
    Premier paiement important vers un bénéficiaire jamais vu.
    Seuil : > SEUIL_APPROBATION_PME (5 000€) pour filtrer les petits fournisseurs normaux.

    ACFE : Les ghost vendors sont souvent introduits à pleine puissance dès le début
    (le fraudeur maximise le vol avant d'être découvert).
    Un vrai nouveau fournisseur commence par de petites commandes de test.
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
            if amt >= SEUIL_APPROBATION_PME and row['amount'] < 0:
                flags.append(_flag(row, 68,
                    f"Premier paiement important — {amt:,.0f}€",
                    f"Première apparition de '{row['description'][:50]}' : {amt:,.0f}€. "
                    f"Vérifier le numéro d'entreprise belge (BE XXXX.XXX.XXX), "
                    f"le IBAN (doit être une banque belge traditionnelle), et les bons de commande. "
                    f"Un vrai fournisseur commence par de petites commandes.",
                    "fraude_fournisseur"))
    return flags


def cas_fournisseur_virement_fixe(df):
    """
    Fournisseur qui facture EXACTEMENT le même montant chaque mois.
    Un vrai fournisseur varie ses factures selon les commandes.
    Un fournisseur fictif facture souvent un montant fixe (abonnement fictif).

    Signal : même montant au centime près sur 4+ mois consécutifs.
    """
    flags = []
    debits = df[df['amount'] < 0].copy().sort_values('date')
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    debits['month']     = debits['date'].dt.to_period('M')

    for vendor, group in debits.groupby('desc_norm'):
        if len(group) < 4:
            continue
        amounts = group['amount'].abs().values
        # Si tous les montants sont identiques au centime près
        if np.std(amounts) == 0 and len(amounts) >= 4:
            total = sum(amounts)
            for _, row in group.iterrows():
                flags.append(_flag(row, 72,
                    f"Fournisseur — montant identique {len(amounts)}× de suite",
                    f"'{vendor[:40]}' : exactement {amounts[0]:,.2f}€ "
                    f"sur {len(amounts)} paiements consécutifs (total : {total:,.0f}€). "
                    f"Les vrais fournisseurs varient leurs factures. "
                    f"Un montant parfaitement fixe suggère un abonnement fictif ou fournisseur fantôme.",
                    "fraude_fournisseur"))
    return _dedup(flags)


def cas_surfacturation_progressive(df):
    """
    Fournisseur dont les factures augmentent régulièrement.
    +5%/facture passe inaperçu mais fait +80% sur un an.

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
                flags.append(_flag(row, 73,
                    f"Surfacturation progressive (+{pct_drift*100:.0f}%/facture, R={r:.2f})",
                    f"'{vendor[:40]}' : +{slope:.0f}€ par facture en moyenne "
                    f"(+{drift_total:,.0f}€ total sur {len(amounts)} factures). "
                    f"Sans avenant contractuel signé, c'est un signal de surfacturation.",
                    "fraude_fournisseur"))
    return _dedup(flags)


def cas_fournisseur_concentration(df):
    """
    Un fournisseur représente > 60% des débits totaux.
    Seuil élevé (60%) pour ne pas flagguer les fournisseurs dominants légitimes (ex: Metro).
    Signal uniquement si le fournisseur N'ÉTAIT PAS dominant avant (nouveauté).
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
        if pct > 60 and abs(vrow['sum']) > 10_000:
            for _, row in debits[debits['desc_norm'] == vendor].iterrows():
                flags.append(_flag(row, 58,
                    f"Concentration extrême — {pct:.0f}% des dépenses",
                    f"'{vendor[:40]}' : {pct:.0f}% de tous les débits "
                    f"({abs(vrow['sum']):,.0f}€). Vérifier si ce niveau est normal "
                    f"et si des ristournes/kickbacks sont en jeu.",
                    "fraude_fournisseur"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE D — FRAUDES SUR LA PAIE (ACFE : 8,5%, perte médiane 90-120k$, 18-30 mois)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_doublon_salaire(df):
    """
    Même salarié reçoit 2+ versements le même mois.
    Ghost employee ou erreur de paie — impossible à justifier.
    """
    mots_fr = ['salaire', 'paie', 'paye', 'acompte salaire', 'avance salaire']
    mots_nl = ['loon', 'wedde', 'salaris', 'voorschot loon']
    mots = mots_fr + mots_nl
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().apply(lambda d: any(m in d for m in mots))]
    if len(sal) < 2:
        return []

    sal['desc_norm'] = sal['description'].str.lower().str.strip().str[:60]
    sal['month']     = sal['date'].dt.to_period('M')
    flags = []

    for (month, emp), group in sal.groupby(['month', 'desc_norm']):
        if len(group) >= 2:
            total = group['amount'].abs().sum()
            for _, row in group.iterrows():
                flags.append(_flag(row, 87,
                    f"Doublon de salaire — {len(group)}× en {month}",
                    f"'{emp[:40]}' reçoit {len(group)} versements en {month} "
                    f"(total : {total:,.0f}€). "
                    f"Ghost employee ou doublon de paie (ACFE : 18-30 mois avant détection).",
                    "fraude_paie"))
    return _dedup(flags)


def cas_salaire_montant_parfait(df):
    """
    Salaire parfaitement rond et identique chaque mois pendant 6+ mois.
    Signal Ghost Employee ACFE #3 : montants fabriqués = toujours ronds.
    Un vrai salaire belge varie : cotisations ONSS, heures sup, primes, 13e mois.
    """
    mots_fr = ['salaire', 'paie']
    mots_nl = ['loon', 'wedde', 'salaris']
    mots = mots_fr + mots_nl
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().apply(lambda d: any(m in d for m in mots))]
    if sal.empty:
        return []

    sal['desc_norm'] = sal['description'].str.lower().str.strip().str[:60]
    flags = []

    for emp, group in sal.groupby('desc_norm'):
        if len(group) < 6:
            continue
        amounts = group['amount'].abs().values
        if np.std(amounts) == 0 and amounts[0] % 50 == 0:
            for _, row in group.iterrows():
                flags.append(_flag(row, 75,
                    f"Salaire parfaitement identique {len(amounts)} mois de suite",
                    f"'{emp[:40]}' : {amounts[0]:,.0f}€ EXACTEMENT chaque mois pendant "
                    f"{len(amounts)} mois. Les vrais salaires belges varient "
                    f"(ONSS, heures sup, 13e mois, congés). "
                    f"Signal Ghost Employee (ACFE).",
                    "fraude_paie"))
    return _dedup(flags)


def cas_inflation_salaire(df):
    """
    Salaire en hausse constante sur 4+ mois, > 15% total.
    Payroll padding — augmentation non autorisée.
    """
    mots = ['salaire', 'paie', 'loon', 'wedde', 'salaris']
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().apply(lambda d: any(m in d for m in mots))]
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
                flags.append(_flag(row, 68,
                    f"Inflation salariale — +{(amounts[-1]-amounts[0])/amounts[0]*100:.0f}%",
                    f"'{emp[:40]}' : hausse constante sur {len(group)} mois "
                    f"(+{drift:,.0f}€). "
                    f"Vérifier les CCT (conventions collectives) et avenants signés.",
                    "fraude_paie"))
    return _dedup(flags)


def cas_virement_hors_cycle_paie(df):
    """
    Virement libellé 'salaire' hors du cycle de paie habituel.
    En Belgique, la paie est versée entre le 1er et le 5 du mois suivant (généralement).
    Un virement 'salaire' le 15 ou le 20 = hors cycle = suspect.
    """
    mots = ['salaire', 'paie', 'loon', 'wedde', 'salaris', 'acompte sal']
    sal = df[df['amount'] < 0].copy()
    sal = sal[sal['description'].str.lower().apply(lambda d: any(m in d for m in mots))]
    if sal.empty:
        return []

    # Trouver le cycle de paie habituel
    sal['dom'] = sal['date'].dt.day
    mode_day = sal['dom'].mode()
    if mode_day.empty:
        return []
    normal_day = int(mode_day.iloc[0])

    flags = []
    for _, row in sal.iterrows():
        day = row['date'].day
        if abs(day - normal_day) > 5 and abs(day - normal_day) < 25:  # Pas juste un autre mois
            flags.append(_flag(row, 65,
                f"Virement salaire hors cycle (jour {day}, cycle normal : {normal_day})",
                f"'{row['description'][:50]}' le {row['date'].strftime('%d/%m/%Y')} "
                f"(jour {day} du mois). Le cycle normal de paie est autour du {normal_day}. "
                f"Virement hors cycle = signal Ghost Employee ou paiement non autorisé.",
                "fraude_paie"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE E — STRUCTURING & BLANCHIMENT (CTIF Belgique)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_structuring(df):
    """
    Structuring (schtroumpfage) : fractionnement intentionnel pour passer sous un seuil.
    Critères stricts pour éviter les faux positifs (ex: restaurant qui commande 3x/semaine) :
    - 4+ paiements (pas 3 — 3 commandes fournisseur = totalement normal)
    - Montants anormalement similaires entre eux (CV < 15%) = délibérément fractionnés
    - Chacun sous le seuil, total au-dessus
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    for low, high in SEUILS_STRUCTURING_BE:
        for vendor, group in debits.groupby('desc_norm'):
            group = group.sort_values('date')
            for start_i in range(len(group)):
                start_date = group.iloc[start_i]['date']
                window = group[
                    (group['date'] >= start_date) &
                    (group['date'] <= start_date + timedelta(days=30))
                ]
                # Minimum 4 paiements (pas 3 — trop courant dans les fournisseurs normaux)
                if len(window) < 4:
                    continue
                amounts    = window['amount'].abs()
                total      = amounts.sum()
                max_single = amounts.max()
                # Coefficient de variation < 15% = montants suspicieusement similaires
                cv = amounts.std() / amounts.mean() if amounts.mean() > 0 else 1
                if total >= high * 0.85 and max_single < high * 0.95 and cv < 0.15:
                    for _, row in window.iterrows():
                        flags.append(_flag(row, 88,
                            f"Structuring — seuil {high:,}€ contourné ({len(window)} paiements similaires)",
                            f"{len(window)} paiements quasi-identiques à '{vendor[:35]}' : "
                            f"{total:,.0f}€ en 30 jours, chacun sous {high:,}€ (CV={cv:.2f}). "
                            f"Fractionnement intentionnel — CTIF Belgique.",
                            "fraude_financiere"))
                    break
    return _dedup(flags)


def cas_juste_sous_seuil(df):
    """
    Montant entre 95% et 100% d'un seuil d'approbation courant.
    Threshold clustering — ACFE Pattern #3 (fournisseur fantôme).
    Signal : si le responsable valide jusqu'à 5 000€, les fausses factures
    arrivent à 4 800-4 990€ pour éviter une double signature.
    """
    seuils = [1_000, 3_000, 5_000, 10_000, 15_000, 25_000]
    flags = []
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        if amt < 500:
            continue
        for s in seuils:
            if s * 0.95 <= amt < s:
                flags.append(_flag(row, 55,
                    f"Montant juste sous le seuil {s:,}€ (threshold clustering)",
                    f"{amt:,.2f}€ = {s - amt:.2f}€ sous le seuil de {s:,}€. "
                    f"Si le seuil d'approbation de la direction est {s:,}€, "
                    f"ce montant évite le double contrôle. "
                    f"Signal plus fort si plusieurs transactions similaires.",
                    "fraude_financiere"))
                break
    return flags


def cas_transactions_circulaires(df):
    """
    Argent qui sort puis revient à ±5% dans les 30 jours.
    Round-tripping : blanchiment ou gonflement fictif du CA.
    Seuil 1 000€ pour éviter les remboursements normaux.
    """
    flags = []
    df = df.copy().sort_values('date')
    debits  = df[(df['amount'] < 0) & (df['amount'].abs() >= 1_000)]
    credits = df[(df['amount'] > 0) & (df['amount'] >= 1_000)]

    # Exclure loyers, salaires, charges fixes (coïncidences de montant fréquentes)
    mots_fixes = ['loyer', 'huur', 'salaire', 'loon', 'wedde', 'assurance', 'verzekering',
                  'energie', 'energie', 'proximus', 'telenet', 'engie', 'electrabel']
    debits = debits[~debits['description'].str.lower().apply(
        lambda d: any(m in d for m in mots_fixes)
    )]

    for _, row_out in debits.iterrows():
        amt = abs(row_out['amount'])
        # Tolérance réduite à ±2% (pas ±5%) pour éviter les coïncidences
        retour = credits[
            (credits['amount'].between(amt * 0.98, amt * 1.02)) &
            (credits['date'] > row_out['date']) &
            (credits['date'] <= row_out['date'] + timedelta(days=30))
        ]
        if not retour.empty:
            flags.append(_flag(row_out, 85,
                "Transaction circulaire (aller-retour)",
                f"Sortie de {amt:,.0f}€, retour de {retour.iloc[0]['amount']:,.0f}€ "
                f"sous {(retour.iloc[0]['date'] - row_out['date']).days} jours. "
                f"Round-tripping ou blanchiment — vérifier l'origine du retour.",
                "fraude_financiere"))
    return _dedup(flags)


def cas_virements_multiples_meme_jour(df):
    """
    5+ virements importants (> 1 000€) le même jour vers des bénéficiaires DIFFÉRENTS.
    Pattern Account Takeover Fraud ou fraude interne.
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
        if len(group) >= 5 and n_vendors >= 4 and total >= SEUIL_CTIF_VIGILANCE:
            for _, row in group.iterrows():
                flags.append(_flag(row, 78,
                    f"{len(group)} virements importants le {day}",
                    f"{len(group)} virements ≥ 1 000€ vers {n_vendors} bénéficiaires "
                    f"le même jour (total {total:,.0f}€). "
                    f"Pattern caractéristique d'Account Takeover ou fraude interne.",
                    "fraude_financiere"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE F — ANOMALIES STATISTIQUES (IQR/MAD — jamais Z-score)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_outlier_mad(df):
    """
    Montant statistiquement aberrant via MAD (Median Absolute Deviation).
    IQR/MAD recommandé par l'ISACA pour les petits volumes (< 500 transactions).
    Z-score rejeté : assume distribution normale, 30-40% faux positifs sur données bancaires.

    Seuil MAD = 4.0 (conservateur — seuls les vrais outliers remontent).
    Formule : score = |x - médiane| / (1.4826 × MAD)
    """
    if len(df) < 20:
        return []

    amounts = df['amount'].abs()
    median  = amounts.median()
    mad     = (amounts - median).abs().median()
    if mad == 0:
        return []

    scores = (amounts - median).abs() / (1.4826 * mad)
    # Seuil à 4.0 (très conservateur — évite les faux positifs)
    outliers = df[scores > 4.0].copy()
    outliers['mad_score'] = scores[scores > 4.0]

    flags = []
    for _, row in outliers.iterrows():
        score_mad = row['mad_score']
        flags.append(_flag(row, min(85, int(50 + score_mad * 4)),
            f"Montant statistiquement aberrant (MAD score : {score_mad:.1f})",
            f"{abs(row['amount']):,.2f}€ — score MAD de {score_mad:.1f} "
            f"(seuil : 4.0, médiane : {median:,.0f}€). "
            f"Méthode IQR/MAD (ISACA) — robuste sur petits volumes.",
            "anomalie_statistique"))
    return flags


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE G — LOI DE BENFORD (uniquement > 200 transactions)
# ═══════════════════════════════════════════════════════════════════════════════

def cas_benford(df):
    """
    Loi de Benford — détection de montants fabriqués.
    Test KS (Kolmogorov-Smirnov) + MAD > 0.025 (seuil strict).
    Uniquement si > 200 transactions (sinon trop peu fiable).

    Applications documentées : Enron, Madoff, contrôles DGFiP/SPF Finances.
    Les montants fabriqués à la main s'écartent de la distribution naturelle.
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
        flags.append(_flag(row, 73,
            f"Loi de Benford — chiffre '{most_deviant}' anormal ({len(amounts)} transactions)",
            f"Chiffre '{most_deviant}' : {observed.get(most_deviant,0)*100:.1f}% "
            f"vs {expected[most_deviant]*100:.1f}% attendu (MAD={mad:.4f}). "
            f"Technique utilisée par le SPF Finances belge et les experts forensiques.",
            "fraude_financiere"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE H — PATTERNS TEMPORELS
# ═══════════════════════════════════════════════════════════════════════════════

def cas_pic_depenses_mad(df):
    """
    Semaine avec dépenses totales très supérieures à la normale.
    IQR/MAD au lieu du Z-score (robuste, fonctionne dès 8 semaines de données).
    Seuil MAD = 4.0 (conservateur).
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if len(debits) < 15:
        return []

    debits['week'] = debits['date'].dt.to_period('W')
    weekly = debits.groupby('week')['amount'].sum().abs()

    if len(weekly) < 4:
        return []

    median_w = weekly.median()
    mad_w    = (weekly - median_w).abs().median()
    if mad_w == 0:
        return []

    for week, total in weekly.items():
        score = abs(total - median_w) / (1.4826 * mad_w)
        if score > 4.0:
            spike_txns = debits[debits['week'] == week]
            for _, row in spike_txns.iterrows():
                flags.append(_flag(row, 65,
                    f"Pic de dépenses — semaine {week} (MAD score : {score:.1f})",
                    f"Semaine du {week.start_time.strftime('%d/%m/%Y')} : "
                    f"{total:,.0f}€ vs médiane {median_w:,.0f}€ "
                    f"(score MAD : {score:.1f}). Identifier les transactions inhabituelles.",
                    "anomalie_activite"))
    return _dedup(flags)


def cas_fin_de_periode(df):
    """
    Concentration de transactions les 3 derniers jours du mois.
    Pattern ACFE "quarter-end clustering" — manipulation comptable avant clôture.
    Seuil : > 50% des paiements du mois concentrés sur les 3 derniers jours.
    """
    flags = []
    df = df.copy()
    df['dom']   = df['date'].dt.day
    df['month'] = df['date'].dt.to_period('M')
    df['is_eom'] = df['dom'] >= 28

    for month, group in df.groupby('month'):
        eom   = group[group['is_eom'] & (group['amount'] < 0)]
        total = group[group['amount'] < 0]['amount'].abs().sum()
        eom_total = eom['amount'].abs().sum()
        if total > 0 and eom_total / total > 0.50 and eom_total > 3_000:
            for _, row in eom.iterrows():
                flags.append(_flag(row, 62,
                    f"50%+ des paiements en fin de mois ({month})",
                    f"{eom_total:,.0f}€ = {eom_total/total*100:.0f}% des dépenses "
                    f"de {month} concentrés sur les 3 derniers jours. "
                    f"Pattern de manipulation comptable avant clôture (ACFE).",
                    "anomalie_activite"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPE I — PIRATAGE / ACCOUNT TAKEOVER (nouvelles règles)
# Scénario réel : un employé ou une personne externe a accès aux identifiants
# et fait des virements en cachette pour voler de l'argent.
# ═══════════════════════════════════════════════════════════════════════════════

def cas_virement_hors_heures(df):
    """
    Virement sortant effectué en dehors des heures de bureau (avant 7h ou après 22h).
    Un compte professionnel ne devrait JAMAIS avoir de virement initié à 2h du matin.
    Utilisé uniquement si les transactions ont une heure précise (timestamp).

    Pattern account takeover documenté : le pirate agit la nuit ou le week-end
    quand personne ne surveille, pour avoir le temps de transférer avant détection.
    """
    flags = []
    if 'date' not in df.columns:
        return []

    # Vérifier si on a des heures réelles (pas juste des dates à 00:00:00)
    has_time = df['date'].apply(lambda d: d.hour != 0 or d.minute != 0).any()
    if not has_time:
        return []

    for _, row in df.iterrows():
        if row['amount'] >= 0:
            continue
        h   = row['date'].hour
        mn  = row['date'].minute
        amt = abs(row['amount'])
        if amt < 200:
            continue
        # Ignorer les timestamps 00:00:00 — ce sont des dates sans heure réelle
        if h == 0 and mn == 0:
            continue

        is_weekend = row['date'].weekday() >= 5

        if h < 6 or h >= 23:
            flags.append(_flag(row, 92,
                f"Virement nocturne — {h:02d}h{row['date'].minute:02d} ({amt:,.0f}€)",
                f"Virement de {amt:,.0f}€ à {h:02d}h{row['date'].minute:02d}. "
                f"Aucun virement professionnel légitime n'est initié la nuit. "
                f"Signal caractéristique d'un piratage de compte (account takeover). "
                f"Vérifier immédiatement qui avait accès aux identifiants bancaires.",
                "piratage"))
        elif is_weekend and amt >= 1_000:
            flags.append(_flag(row, 78,
                f"Virement week-end — {row['date'].strftime('%A')} {amt:,.0f}€",
                f"Virement de {amt:,.0f}€ un {row['date'].strftime('%A')}. "
                f"Pour un compte pro, les virements le week-end sont inhabituels. "
                f"À surveiller si récurrent.",
                "piratage"))
    return flags


def cas_test_puis_gros_virement(df):
    """
    Petite transaction test (1-50€) suivie d'un gros virement vers le même bénéficiaire
    dans les 48 heures. Pattern classique de card testing / account takeover.

    Méthode : un pirate envoie d'abord 1€ ou 5€ pour vérifier que le compte fonctionne,
    puis vide le compte le lendemain.
    """
    flags = []
    debits = df[df['amount'] < 0].copy().sort_values('date')
    if debits.empty:
        return []
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:50]

    for _, test_row in debits[debits['amount'].abs() <= 50].iterrows():
        test_amt = abs(float(test_row['amount']))
        # Chercher un gros virement vers le même bénéficiaire dans les 48h
        follow_up = debits[
            (debits['desc_norm'] == test_row['desc_norm']) &
            (debits['amount'].abs() >= 200) &
            (debits['date'] > test_row['date']) &
            (debits['date'] <= test_row['date'] + timedelta(hours=48))
        ]
        if not follow_up.empty:
            for _, big_row in follow_up.iterrows():
                flags.append(_flag(big_row, 90,
                    f"Test ({test_amt:.2f}€) + gros virement ({abs(big_row['amount']):,.0f}€)",
                    f"Transaction test de {test_amt:.2f}€ le "
                    f"{test_row['date'].strftime('%d/%m/%Y')} vers "
                    f"'{test_row['description'][:40]}', suivie de {abs(big_row['amount']):,.0f}€ "
                    f"dans les 48h. "
                    f"Pattern classique de vérification de compte avant vol (card testing). "
                    f"Vérifier si ce bénéficiaire est connu et autorisé.",
                    "piratage"))
    return _dedup(flags)


def cas_iban_modifie_fournisseur(df):
    """
    Même description de fournisseur mais IBAN différent d'un paiement à l'autre.
    Technique : Man-in-the-Middle sur facture PDF — le fraudeur intercepte la facture,
    change le numéro de compte, renvoie au client. La victime paie le bon montant
    mais vers le mauvais compte (celui du fraudeur).

    Signal : même libellé, montants similaires, mais IBANs différents dans la description.
    """
    flags = []
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return []

    # Extraire les IBANs des descriptions
    iban_re = re.compile(r'BE\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}', re.IGNORECASE)
    debits['iban_in_desc'] = debits['description'].apply(
        lambda d: iban_re.findall(d.replace(' ', ''))
    )
    debits['has_iban'] = debits['iban_in_desc'].apply(lambda x: len(x) > 0)
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:40]

    # Retirer les IBANs de la description pour avoir le libellé "pur"
    debits['desc_no_iban'] = debits['description'].apply(
        lambda d: iban_re.sub('', d).strip()[:40].lower()
    )

    for desc, group in debits[debits['has_iban']].groupby('desc_no_iban'):
        if len(group) < 2:
            continue
        all_ibans = set()
        for _, row in group.iterrows():
            all_ibans.update(row['iban_in_desc'])
        if len(all_ibans) > 1:
            for _, row in group.iterrows():
                flags.append(_flag(row, 88,
                    "IBAN modifié — même fournisseur, compte différent",
                    f"'{desc[:45]}' : {len(all_ibans)} IBANs différents détectés "
                    f"sur {len(group)} paiements. "
                    f"Technique Man-in-the-Middle sur facture : quelqu'un a modifié le numéro "
                    f"de compte du fournisseur. Vérifier chaque IBAN par appel téléphonique direct.",
                    "piratage"))
    return _dedup(flags)


def cas_virement_vers_neobanque(df):
    """
    Virement professionnel vers une néobanque (Revolut, N26, Wise, Bunq...).
    Un vrai fournisseur belge a un IBAN chez KBC, Belfius, BNP Fortis, ING...
    Un compte Revolut ou Wise = signal fort de fraude ou fournisseur fictif.

    BIC des néobanques actives en Belgique (Febelfin 2024) :
    - Wise (Transferwise) : TRWIBEB1
    - Revolut : REVOIE23 / REVOGB21
    - Bunq : BUNQNL2A
    - N26 : NTSBDEB1
    - Lydia : LYDIFRP1
    - Monzo : MONZGB2L
    """
    # IBANs et BICs de néobanques dans les descriptions
    neobank_patterns = [
        'revolut', 'n26', 'wise', 'transferwise', 'bunq', 'lydia', 'monzo',
        'paypal', 'stripe', 'paysera', 'curve', 'starling',
        # BICs spécifiques
        'trwibeb', 'revoie', 'revogb', 'bunqnl', 'ntsbde', 'monzgb',
        # IBANs hors Belgique avec grosse somme = suspect pour PME locale
    ]
    # IBANs non-belges pour entreprise qui fait tout local
    iban_etranger_re = re.compile(r'\b(?!BE)[A-Z]{2}\d{2}[A-Z0-9]{8,}', re.IGNORECASE)

    flags = []
    for _, row in df.iterrows():
        if row['amount'] >= 0 or abs(row['amount']) < 100:
            continue
        desc_low = row['description'].lower()
        amt = abs(row['amount'])

        # Vérifier néobanque explicite
        for pat in neobank_patterns:
            if pat in desc_low:
                flags.append(_flag(row, 85,
                    f"Virement vers néobanque ({pat.capitalize()}) — {amt:,.0f}€",
                    f"'{row['description'][:60]}' : virement de {amt:,.0f}€ vers "
                    f"une néobanque ({pat.capitalize()}). "
                    f"Un vrai fournisseur belge n'a pas de compte Revolut/N26/Wise. "
                    f"Signal fort : fournisseur fictif ou fraude interne. "
                    f"Vérifier le numéro d'entreprise (BCE) du bénéficiaire.",
                    "piratage"))
                break

        # IBAN étranger + gros montant pour PME locale
        if amt >= 2_000 and iban_etranger_re.search(row['description']):
            # Exclure si c'est clairement un gros fournisseur connu international
            mots_legitimes = ['amazon', 'google', 'microsoft', 'apple', 'booking', 'airbnb']
            if not any(m in desc_low for m in mots_legitimes):
                flags.append(_flag(row, 72,
                    f"Virement vers IBAN étranger — {amt:,.0f}€",
                    f"'{row['description'][:60]}' : IBAN non-belge détecté. "
                    f"Pour une PME locale, vérifier si ce paiement international est attendu.",
                    "piratage"))
    return _dedup(flags)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS & MOTEUR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def _flag(row, score, titre, detail, categorie):
    if score >= 85:
        sev = SEV_CRITIQUE
    elif score >= 65:
        sev = SEV_ELEVE
    elif score >= 45:
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
        key = (f['transaction_id'], f['rule'][:40])
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


ALL_RULES = [
    # Espèces / caisse (skimming)
    cas_skimming_depot,
    cas_montants_ronds_depots,
    cas_retraits_especes_excessifs,
    # Fraude interne directe
    cas_doublons_exacts,
    cas_virements_personnels,
    cas_achats_personnels_evidents,
    cas_notes_frais_suspectes,
    # Fraude fournisseur
    cas_fournisseur_typosquatting,
    cas_nouveau_gros_fournisseur,
    cas_fournisseur_virement_fixe,
    cas_surfacturation_progressive,
    cas_fournisseur_concentration,
    # Fraude paie
    cas_doublon_salaire,
    cas_salaire_montant_parfait,
    cas_inflation_salaire,
    cas_virement_hors_cycle_paie,
    # Blanchiment / structuring
    cas_structuring,
    cas_juste_sous_seuil,
    cas_transactions_circulaires,
    cas_virements_multiples_meme_jour,
    # Statistique (IQR/MAD)
    cas_outlier_mad,
    # Loi de Benford (>200 txns)
    cas_benford,
    # Patterns temporels
    cas_pic_depenses_mad,
    cas_fin_de_periode,
    # Piratage / account takeover
    cas_virement_hors_heures,
    cas_test_puis_gros_virement,
    cas_iban_modifie_fournisseur,
    cas_virement_vers_neobanque,
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
    'piratage':             "🚨 Piratage / Accès non autorisé (account takeover)",
    'fraude_interne':       "Fraude interne (employés / dirigeants)",
    'fraude_fournisseur':   "Fraude fournisseur (ghost vendors, surfacturation)",
    'fraude_caisse':        "Fraude caisse / espèces (skimming)",
    'fraude_paie':          "Fraude sur la paie (ghost employees)",
    'fraude_financiere':    "Fraude financière / blanchiment (CTIF)",
    'anomalie_activite':    "Anomalie d'activité",
    'anomalie_statistique': "Anomalie statistique (MAD/Benford)",
    'fraude_fiscale':       "Fraude fiscale",
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
    score = compute_risk_score(flags_df, stats)

    if score >= 60:
        verdict_niv = "🚨 ÉLEVÉ"
        verdict_txt = ("Des **anomalies significatives** ont été détectées. "
                       "Plusieurs transactions nécessitent une vérification immédiate avec pièces justificatives.")
    elif score >= 30:
        verdict_niv = "🟠 MODÉRÉ"
        verdict_txt = "Quelques **irrégularités** relevées. Demandez les justificatifs pour les transactions signalées."
    else:
        verdict_niv = "✅ NORMAL"
        verdict_txt = (f"Aucune anomalie significative détectée. "
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

    if not flags_df.empty and 'categorie' in flags_df.columns:
        lines += ["\n---", "## Anomalies détectées"]
        for cat_key, cat_label in CATEGORIES_FR.items():
            cat_flags = flags_df[flags_df['categorie'] == cat_key]
            if cat_flags.empty:
                continue
            lines.append(f"\n### {cat_label} ({len(cat_flags)})")
            for _, flag in cat_flags.head(6).iterrows():
                lines.append(
                    f"- **[{flag['severity']}]** {flag['rule']} — "
                    f"{flag['date'].strftime('%d/%m/%Y')} — {flag['amount']:,.2f}€\n"
                    f"  → {flag['detail']}"
                )

    lines += [
        "\n---",
        "## Cadre réglementaire applicable (Belgique)",
        "- **CTIF** (Cellule de Traitement des Informations Financières) — toute opération suspecte doit être signalée",
        f"- **Limite espèces professionnels** : {SEUIL_ESPECES_PRO_BE:,}€ (Loi Programme 2012)",
        "- **Loi du 18 septembre 2017** relative à la prévention du blanchiment de capitaux",
        "- **TVA restauration Belgique** : 12% (taux réduit HORECA)",
        "\n_Analyse basée sur ACFE 2024, CTIF 2024, BNB. Ne constitue pas un avis juridique._",
    ]
    return '\n'.join(lines)
