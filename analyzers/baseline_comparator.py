"""
Comparaison par rapport à une baseline personnalisée.

Principe (forensic accounting — baseline analysis) :
  1. Le gérant upload un relevé "normal" (période saine connue)
  2. Le système apprend les patterns spécifiques de CETTE entreprise
  3. Le relevé à analyser est comparé à la baseline — seules les VRAIES déviations sont signalées

C'est la méthode des auditeurs forensiques réels : établir d'abord ce qui est normal
pour CETTE entreprise spécifique, pas une moyenne générique.

Indicateurs calculés sur la baseline :
  - Fournisseurs connus (montant moyen, fréquence, jours habituels)
  - Distribution des montants (médiane, IQR par catégorie)
  - Ratios mensuels (entrées / sorties)
  - Patterns temporels (jours actifs, fréquence hebdomadaire)
"""

import pandas as pd
import numpy as np
from datetime import timedelta


def build_baseline(df_ref: pd.DataFrame) -> dict:
    """
    Construit la baseline à partir d'un relevé de référence (période saine).
    Retourne un dictionnaire d'indicateurs normaux pour cette entreprise.
    """
    if df_ref.empty:
        return {}

    baseline = {}

    # ── 1. Fournisseurs connus ────────────────────────────────────────────────
    debits = df_ref[df_ref['amount'] < 0].copy()
    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]

    vendor_stats = {}
    for vendor, group in debits.groupby('desc_norm'):
        amounts = group['amount'].abs()
        vendor_stats[vendor] = {
            'mean':    float(amounts.mean()),
            'median':  float(amounts.median()),
            'std':     float(amounts.std()) if len(amounts) > 1 else 0,
            'iqr':     float(amounts.quantile(0.75) - amounts.quantile(0.25)),
            'count':   int(len(amounts)),
            'days':    list(group['date'].dt.day_name().unique()),
            'total':   float(amounts.sum()),
        }
    baseline['vendors'] = vendor_stats
    baseline['known_vendors'] = set(vendor_stats.keys())

    # ── 2. Ratios financiers de référence ────────────────────────────────────
    total_debits  = debits['amount'].abs().sum()
    total_credits = df_ref[df_ref['amount'] > 0]['amount'].sum()
    n_months      = max(df_ref['date'].dt.to_period('M').nunique(), 1)

    baseline['avg_monthly_out']  = float(total_debits  / n_months)
    baseline['avg_monthly_in']   = float(total_credits / n_months)
    baseline['credit_debit_ratio'] = float(total_credits / total_debits) if total_debits > 0 else 1.0

    # ── 3. Distribution globale des montants ──────────────────────────────────
    all_amounts = debits['amount'].abs()
    baseline['amount_median'] = float(all_amounts.median()) if not all_amounts.empty else 0
    baseline['amount_q75']    = float(all_amounts.quantile(0.75)) if not all_amounts.empty else 0
    baseline['amount_q95']    = float(all_amounts.quantile(0.95)) if not all_amounts.empty else 0
    baseline['amount_iqr']    = float(all_amounts.quantile(0.75) - all_amounts.quantile(0.25)) if not all_amounts.empty else 0

    # ── 4. Activité par jour de la semaine ────────────────────────────────────
    dow_counts = debits.groupby(debits['date'].dt.day_name()).size()
    baseline['active_days'] = set(dow_counts.index.tolist())

    # ── 5. Fréquence hebdomadaire normale ────────────────────────────────────
    n_weeks = max((df_ref['date'].max() - df_ref['date'].min()).days / 7, 1)
    baseline['txns_per_week'] = float(len(debits) / n_weeks)

    return baseline


def compare_to_baseline(df_new: pd.DataFrame, baseline: dict) -> list[dict]:
    """
    Compare le relevé à analyser à la baseline.
    Ne flaggue que ce qui s'écarte SIGNIFICATIVEMENT de la normale de cette entreprise.
    """
    if not baseline or df_new.empty:
        return []

    flags = []
    debits = df_new[df_new['amount'] < 0].copy()
    if debits.empty:
        return []

    debits['desc_norm'] = debits['description'].str.lower().str.strip().str[:60]
    known_vendors = baseline.get('known_vendors', set())
    vendor_stats  = baseline.get('vendors', {})
    q95           = baseline.get('amount_q95', 0)

    # ── Test 1 : Fournisseur complètement nouveau avec gros montant ───────────
    for _, row in debits.iterrows():
        vendor = row['desc_norm']
        amt    = abs(row['amount'])
        # Nouveau fournisseur ET montant dépasse le 95e percentile de la baseline
        if vendor not in known_vendors and amt > max(q95, 500):
            flags.append({
                'transaction_id': row['id'], 'date': row['date'],
                'description': row['description'], 'amount': row['amount'],
                'rule': 'Nouveau fournisseur — dépasse la baseline',
                'detail': (
                    f"'{row['description'][:50]}' n'apparaît pas dans la période de référence. "
                    f"Montant {amt:,.0f}€ > 95e percentile de la baseline ({q95:,.0f}€). "
                    f"Vérifier si ce fournisseur est connu et autorisé."
                ),
                'severity': '🔴 Élevé', 'score_contribution': 70, 'categorie': 'fraude_fournisseur',
            })

    # ── Test 2 : Montant anormalement élevé chez un fournisseur connu ─────────
    for vendor, vstats in vendor_stats.items():
        group = debits[debits['desc_norm'] == vendor]
        if group.empty:
            continue
        normal_max = vstats['median'] + 3 * max(vstats['std'], vstats['median'] * 0.1)
        for _, row in group.iterrows():
            amt = abs(row['amount'])
            if amt > normal_max * 1.5 and amt > vstats['median'] * 2:
                pct_above = (amt - vstats['median']) / vstats['median'] * 100
                flags.append({
                    'transaction_id': row['id'], 'date': row['date'],
                    'description': row['description'], 'amount': row['amount'],
                    'rule': f'Surfacturation vs baseline (+{pct_above:.0f}%)',
                    'detail': (
                        f"'{vendor[:40]}' : {amt:,.0f}€ vs médiane baseline de {vstats['median']:,.0f}€ "
                        f"(+{pct_above:.0f}%). Sur {vstats['count']} paiements historiques, "
                        f"jamais dépasse {normal_max:,.0f}€. Demander la facture."
                    ),
                    'severity': '🔴 Élevé' if pct_above > 100 else '🟠 Moyen',
                    'score_contribution': 75 if pct_above > 100 else 55,
                    'categorie': 'fraude_fournisseur',
                })

    # ── Test 3 : Mois avec dépenses totales très au-dessus de la baseline ─────
    avg_monthly = baseline.get('avg_monthly_out', 0)
    if avg_monthly > 0:
        df_new_copy = df_new.copy()
        df_new_copy['month'] = df_new_copy['date'].dt.to_period('M')
        monthly_out = df_new_copy[df_new_copy['amount'] < 0].groupby('month')['amount'].sum().abs()

        for month, total_out in monthly_out.items():
            if total_out > avg_monthly * 1.8:
                pct = (total_out - avg_monthly) / avg_monthly * 100
                month_txns = df_new_copy[
                    (df_new_copy['month'] == month) & (df_new_copy['amount'] < 0)
                ]
                # Ne flaggue que la transaction la plus élevée du mois (pas toutes)
                if not month_txns.empty:
                    top_row = month_txns.loc[month_txns['amount'].abs().idxmax()]
                    flags.append({
                        'transaction_id': top_row['id'], 'date': top_row['date'],
                        'description': top_row['description'], 'amount': top_row['amount'],
                        'rule': f'Mois atypique vs baseline (+{pct:.0f}%)',
                        'detail': (
                            f"Mois {month} : {total_out:,.0f}€ de dépenses totales "
                            f"vs moyenne baseline de {avg_monthly:,.0f}€ (+{pct:.0f}%). "
                            f"Transaction la plus élevée du mois signalée. "
                            f"Identifier les dépenses inhabituelles."
                        ),
                        'severity': '🟠 Moyen', 'score_contribution': 50,
                        'categorie': 'anomalie_activite',
                    })

    # ── Test 4 : Ratio entrées/sorties très dégradé vs baseline ──────────────
    new_debits  = df_new[df_new['amount'] < 0]['amount'].abs().sum()
    new_credits = df_new[df_new['amount'] > 0]['amount'].sum()
    base_ratio  = baseline.get('credit_debit_ratio', 1.0)

    if new_debits > 0 and new_credits > 0:
        new_ratio = new_credits / new_debits
        if new_ratio < base_ratio * 0.60 and base_ratio > 0.5:
            for _, row in df_new[df_new['amount'] > 0].head(5).iterrows():
                flags.append({
                    'transaction_id': row['id'], 'date': row['date'],
                    'description': row['description'], 'amount': row['amount'],
                    'rule': f'Ratio recettes/dépenses dégradé vs baseline',
                    'detail': (
                        f"Ratio actuel : {new_ratio:.2f} vs baseline {base_ratio:.2f} "
                        f"(-{(1-new_ratio/base_ratio)*100:.0f}%). "
                        f"Recettes plus faibles que d'habitude — possible dissimulation ou "
                        f"baisse d'activité. Vérifier les Z-rapports CB."
                    ),
                    'severity': '🟠 Moyen', 'score_contribution': 55,
                    'categorie': 'fraude_fiscale',
                })

    # Dédoublonner
    seen = set()
    result = []
    for f in flags:
        key = (f['transaction_id'], f['rule'][:30])
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def baseline_summary(baseline: dict) -> dict:
    """Retourne un résumé lisible de la baseline pour affichage dans l'interface."""
    if not baseline:
        return {}
    return {
        'n_vendors':         len(baseline.get('known_vendors', [])),
        'avg_monthly_out':   baseline.get('avg_monthly_out', 0),
        'avg_monthly_in':    baseline.get('avg_monthly_in', 0),
        'credit_debit_ratio': baseline.get('credit_debit_ratio', 0),
        'amount_median':     baseline.get('amount_median', 0),
        'amount_q95':        baseline.get('amount_q95', 0),
    }
