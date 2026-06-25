"""
Statistical anomaly detection for bank statement transactions.
Uses z-score, IQR, and time-series methods.
"""

import pandas as pd
import numpy as np
from scipy import stats


def compute_statistics(df: pd.DataFrame) -> dict:
    """Compute general statistics on the transaction dataset."""
    if df.empty:
        return {}

    debits = df[df['amount'] < 0]['amount'].abs()
    credits = df[df['amount'] > 0]['amount']

    stats_dict = {
        'total_transactions': len(df),
        'total_debits': len(debits),
        'total_credits': len(credits),
        'total_debit_amount': debits.sum() if not debits.empty else 0,
        'total_credit_amount': credits.sum() if not credits.empty else 0,
        'net_flow': df['amount'].sum(),
        'avg_debit': debits.mean() if not debits.empty else 0,
        'avg_credit': credits.mean() if not credits.empty else 0,
        'max_debit': debits.max() if not debits.empty else 0,
        'max_credit': credits.max() if not credits.empty else 0,
        'std_debit': debits.std() if not debits.empty else 0,
        'date_range_days': (df['date'].max() - df['date'].min()).days if len(df) > 1 else 0,
    }
    return stats_dict


def detect_outliers_zscore(df: pd.DataFrame, threshold: float = 3.0) -> pd.DataFrame:
    """Flag transactions whose amount is more than `threshold` standard deviations from the mean."""
    if df.empty or len(df) < 5:
        return pd.DataFrame()

    df = df.copy()
    df['abs_amount'] = df['amount'].abs()
    z_scores = np.abs(stats.zscore(df['abs_amount']))
    df['z_score'] = z_scores

    outliers = df[z_scores > threshold].copy()
    if outliers.empty:
        return pd.DataFrame()

    flags = []
    for _, row in outliers.iterrows():
        flags.append({
            'transaction_id': row['id'],
            'date': row['date'],
            'description': row['description'],
            'amount': row['amount'],
            'rule': 'Anomalie statistique (Z-score)',
            'detail': f"Montant {abs(row['amount']):,.2f}€ — {row['z_score']:.1f} écarts-types au-dessus de la moyenne",
            'severity': '🔴 Élevé' if row['z_score'] > 5 else '🟠 Moyen',
            'z_score': row['z_score'],
        })
    return pd.DataFrame(flags)


def detect_outliers_iqr(df: pd.DataFrame, multiplier: float = 2.5) -> pd.DataFrame:
    """Flag transactions outside the IQR fence (robust to skewed distributions)."""
    if df.empty or len(df) < 10:
        return pd.DataFrame()

    df = df.copy()
    amounts = df['amount'].abs()
    Q1 = amounts.quantile(0.25)
    Q3 = amounts.quantile(0.75)
    IQR = Q3 - Q1
    upper_fence = Q3 + multiplier * IQR

    if IQR == 0:
        return pd.DataFrame()

    outliers = df[amounts > upper_fence].copy()
    if outliers.empty:
        return pd.DataFrame()

    flags = []
    for _, row in outliers.iterrows():
        amt = abs(row['amount'])
        flags.append({
            'transaction_id': row['id'],
            'date': row['date'],
            'description': row['description'],
            'amount': row['amount'],
            'rule': 'Anomalie statistique (IQR)',
            'detail': f"Montant {amt:,.2f}€ dépasse le plafond normal de {upper_fence:,.2f}€ (méthode IQR)",
            'severity': '🟠 Moyen',
        })
    return pd.DataFrame(flags)


def detect_temporal_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Detect unusual gaps in activity or sudden bursts."""
    if df.empty or len(df) < 10:
        return pd.DataFrame()

    df = df.copy().sort_values('date')
    df['days_since_prev'] = df['date'].diff().dt.days

    flags = []
    mean_gap = df['days_since_prev'].mean()
    std_gap = df['days_since_prev'].std()

    if pd.isna(std_gap) or std_gap == 0:
        return pd.DataFrame()

    # Flag transactions that come after a very long silence (possible account takeover)
    silence_threshold = mean_gap + 3 * std_gap
    long_silences = df[df['days_since_prev'] > max(silence_threshold, 30)]

    for _, row in long_silences.iterrows():
        flags.append({
            'transaction_id': row['id'],
            'date': row['date'],
            'description': row['description'],
            'amount': row['amount'],
            'rule': 'Reprise après long silence',
            'detail': f"Aucune activité pendant {row['days_since_prev']:.0f} jours avant cette transaction (normale: {mean_gap:.0f} jours)",
            'severity': '🟡 Faible',
        })

    if flags:
        return pd.DataFrame(flags)
    return pd.DataFrame()


def monthly_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Return monthly aggregated stats for charting."""
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['month'] = df['date'].dt.to_period('M')

    monthly = df.groupby('month').agg(
        total_in=('amount', lambda x: x[x > 0].sum()),
        total_out=('amount', lambda x: x[x < 0].sum()),
        count=('amount', 'count'),
        net=('amount', 'sum'),
    ).reset_index()
    monthly['month'] = monthly['month'].dt.to_timestamp()
    monthly['total_out'] = monthly['total_out'].abs()
    return monthly


def run_all_stats(df: pd.DataFrame) -> dict:
    """Run all statistical analyses."""
    return {
        'stats': compute_statistics(df),
        'zscore_flags': detect_outliers_zscore(df),
        'iqr_flags': detect_outliers_iqr(df),
        'temporal_flags': detect_temporal_anomalies(df),
        'monthly': monthly_analysis(df),
    }
