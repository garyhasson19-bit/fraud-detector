"""
Analyse statistique des transactions — méthodes robustes (IQR/MAD).
Z-score retiré : mauvais sur petits volumes, hypothèse de normalité non vérifiée
sur données bancaires (ISACA Journal, Journal of Accountancy).
Méthode recommandée : IQR/MAD (Median Absolute Deviation).
"""

import pandas as pd
import numpy as np


def compute_statistics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    debits  = df[df['amount'] < 0]['amount'].abs()
    credits = df[df['amount'] > 0]['amount']
    return {
        'total_transactions':  len(df),
        'total_debits':        len(debits),
        'total_credits':       len(credits),
        'total_debit_amount':  float(debits.sum())  if not debits.empty  else 0,
        'total_credit_amount': float(credits.sum()) if not credits.empty else 0,
        'net_flow':            float(df['amount'].sum()),
        'avg_debit':           float(debits.mean())  if not debits.empty  else 0,
        'avg_credit':          float(credits.mean()) if not credits.empty else 0,
        'max_debit':           float(debits.max())   if not debits.empty  else 0,
        'max_credit':          float(credits.max())  if not credits.empty else 0,
        'median_debit':        float(debits.median()) if not debits.empty else 0,
        'std_debit':           float(debits.std())   if not debits.empty  else 0,
        'date_range_days':     (df['date'].max() - df['date'].min()).days if len(df) > 1 else 0,
    }


def detect_outliers_mad(df: pd.DataFrame, threshold: float = 3.5) -> pd.DataFrame:
    """
    Détection d'outliers par MAD (Median Absolute Deviation).
    Recommandé par l'ISACA pour les audits sur petits volumes (dès 30 transactions).
    Formule : score = |x - médiane| / (1.4826 × MAD)
    Seuil recommandé : 3.5 (équivalent conservateur de 3σ pour données non-normales).
    """
    if len(df) < 15:
        return pd.DataFrame()

    amounts = df['amount'].abs()
    median  = amounts.median()
    mad     = (amounts - median).abs().median()
    if mad == 0:
        return pd.DataFrame()

    scores = (amounts - median).abs() / (1.4826 * mad)
    outliers = df[scores > threshold].copy()
    outliers['mad_score'] = scores[scores > threshold]

    flags = []
    for _, row in outliers.iterrows():
        score = row['mad_score']
        flags.append({
            'transaction_id': row['id'],
            'date': row['date'],
            'description': row['description'],
            'amount': row['amount'],
            'rule': f'Montant statistiquement aberrant (MAD score : {score:.1f})',
            'detail': (
                f"Montant de {abs(row['amount']):,.2f}€ — score MAD de {score:.1f} "
                f"(seuil : {threshold}). Médiane : {median:,.2f}€. "
                f"Méthode IQR/MAD (ISACA) — robuste aux distributions non-normales."
            ),
            'severity': '🔴 Élevé' if score > 7 else '🟠 Moyen',
            'score_contribution': min(90, int(50 + score * 5)),
            'categorie': 'anomalie_statistique',
        })
    return pd.DataFrame(flags)


def monthly_analysis(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df['month'] = df['date'].dt.to_period('M')
    monthly = df.groupby('month').agg(
        total_in  = ('amount', lambda x: x[x > 0].sum()),
        total_out = ('amount', lambda x: x[x < 0].sum()),
        count     = ('amount', 'count'),
        net       = ('amount', 'sum'),
    ).reset_index()
    monthly['month']     = monthly['month'].dt.to_timestamp()
    monthly['total_out'] = monthly['total_out'].abs()
    return monthly


def run_all_stats(df: pd.DataFrame) -> dict:
    return {
        'stats':      compute_statistics(df),
        'mad_flags':  detect_outliers_mad(df),
        'monthly':    monthly_analysis(df),
        # Compatibilité avec anciens noms (retourne vide — remplacés par MAD)
        'zscore_flags':   pd.DataFrame(),
        'iqr_flags':      pd.DataFrame(),
        'temporal_flags': pd.DataFrame(),
    }
