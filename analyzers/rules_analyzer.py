"""
Business rules for fraud detection in bank statements.
Each rule returns a list of flagged transactions with a reason and severity.
"""

import pandas as pd
import numpy as np
from datetime import timedelta


SEVERITY_HIGH = "🔴 Élevé"
SEVERITY_MEDIUM = "🟠 Moyen"
SEVERITY_LOW = "🟡 Faible"


def flag_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Same amount + same description within 7 days = suspicious duplicate."""
    flags = []
    df = df.copy()
    df['desc_clean'] = df['description'].str.lower().str.strip()

    for i, row in df.iterrows():
        window = df[
            (df.index != i) &
            (df['amount'] == row['amount']) &
            (df['desc_clean'] == row['desc_clean']) &
            ((df['date'] - row['date']).abs() <= timedelta(days=7))
        ]
        if not window.empty:
            flags.append({
                'transaction_id': row['id'],
                'date': row['date'],
                'description': row['description'],
                'amount': row['amount'],
                'rule': 'Doublon suspect',
                'detail': f"Transaction identique détectée {len(window)} fois dans un délai de 7 jours",
                'severity': SEVERITY_HIGH,
            })
    return pd.DataFrame(flags)


def flag_round_amounts(df: pd.DataFrame, threshold: float = 1000.0) -> pd.DataFrame:
    """Round amounts above threshold are suspicious (cash manipulation)."""
    flags = []
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        if amt >= threshold and amt % 100 == 0:
            flags.append({
                'transaction_id': row['id'],
                'date': row['date'],
                'description': row['description'],
                'amount': row['amount'],
                'rule': 'Montant rond élevé',
                'detail': f"Montant rond de {amt:,.0f}€ — possible manipulation de liquidités",
                'severity': SEVERITY_MEDIUM if amt < 5000 else SEVERITY_HIGH,
            })
    return pd.DataFrame(flags)


def flag_unusual_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Transactions at unusual times (weekend or late night) for a business."""
    flags = []
    for _, row in df.iterrows():
        dow = row['date'].weekday()  # 0=Mon, 6=Sun
        if dow == 6:  # Sunday
            flags.append({
                'transaction_id': row['id'],
                'date': row['date'],
                'description': row['description'],
                'amount': row['amount'],
                'rule': 'Transaction dimanche',
                'detail': "Transaction un dimanche — inhabituel pour un restaurant/commerce",
                'severity': SEVERITY_LOW,
            })
    return pd.DataFrame(flags)


def flag_frequent_small_transactions(df: pd.DataFrame, window_days: int = 7, count_threshold: int = 10) -> pd.DataFrame:
    """Many small transactions to the same beneficiary = structuring attempt."""
    flags = []
    df = df.copy()
    df['desc_clean'] = df['description'].str.lower().str.strip()

    grouped = df.groupby('desc_clean')
    for desc, group in grouped:
        if len(group) < count_threshold:
            continue
        # Check within rolling windows
        group = group.sort_values('date')
        for i in range(len(group)):
            window_end = group.iloc[i]['date'] + timedelta(days=window_days)
            window_txns = group[
                (group['date'] >= group.iloc[i]['date']) &
                (group['date'] <= window_end)
            ]
            if len(window_txns) >= count_threshold:
                for _, row in window_txns.iterrows():
                    flags.append({
                        'transaction_id': row['id'],
                        'date': row['date'],
                        'description': row['description'],
                        'amount': row['amount'],
                        'rule': 'Fractionnement suspect',
                        'detail': f"{len(window_txns)} transactions vers '{desc[:50]}' en {window_days} jours — possible fractionnement",
                        'severity': SEVERITY_HIGH,
                    })
                break  # Don't re-flag same group

    if flags:
        return pd.DataFrame(flags).drop_duplicates(subset=['transaction_id'])
    return pd.DataFrame()


def flag_new_large_beneficiary(df: pd.DataFrame, large_threshold: float = 2000.0, min_history_days: int = 90) -> pd.DataFrame:
    """First large payment to a never-seen-before beneficiary."""
    flags = []
    df = df.copy()
    df = df.sort_values('date')
    df['desc_clean'] = df['description'].str.lower().str.strip()

    seen_beneficiaries = {}
    for _, row in df.iterrows():
        desc = row['desc_clean']
        date = row['date']
        amt = abs(row['amount'])

        if desc not in seen_beneficiaries:
            seen_beneficiaries[desc] = date
            if amt >= large_threshold:
                flags.append({
                    'transaction_id': row['id'],
                    'date': row['date'],
                    'description': row['description'],
                    'amount': row['amount'],
                    'rule': 'Nouveau bénéficiaire, montant élevé',
                    'detail': f"Premier paiement de {amt:,.2f}€ vers un bénéficiaire jamais vu auparavant",
                    'severity': SEVERITY_HIGH,
                })

    return pd.DataFrame(flags)


def flag_velocity_spike(df: pd.DataFrame, multiplier: float = 3.0, window_days: int = 7) -> pd.DataFrame:
    """Total spending in a week is X times the average weekly spending."""
    flags = []
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df = df.sort_values('date')
    debits = df[df['amount'] < 0].copy()
    if debits.empty:
        return pd.DataFrame()

    debits['week'] = debits['date'].dt.to_period('W')
    weekly = debits.groupby('week')['amount'].sum().abs()
    mean_weekly = weekly.mean()
    std_weekly = weekly.std()

    if pd.isna(std_weekly) or std_weekly == 0:
        return pd.DataFrame()

    for week, total in weekly.items():
        if total > mean_weekly * multiplier:
            week_start = week.start_time
            week_end = week.end_time
            spike_txns = debits[
                (debits['date'] >= week_start) &
                (debits['date'] <= week_end)
            ]
            for _, row in spike_txns.iterrows():
                flags.append({
                    'transaction_id': row['id'],
                    'date': row['date'],
                    'description': row['description'],
                    'amount': row['amount'],
                    'rule': 'Pic de dépenses',
                    'detail': f"Semaine avec {total:,.2f}€ de dépenses vs moyenne de {mean_weekly:,.2f}€ ({multiplier:.0f}x la normale)",
                    'severity': SEVERITY_MEDIUM,
                })

    if flags:
        return pd.DataFrame(flags).drop_duplicates(subset=['transaction_id'])
    return pd.DataFrame()


def flag_just_below_threshold(df: pd.DataFrame, thresholds: list = [1000, 5000, 10000]) -> pd.DataFrame:
    """Amounts just below common reporting thresholds (999, 4999, 9999...)."""
    flags = []
    margin = 50  # within 50€ below threshold
    for _, row in df.iterrows():
        amt = abs(row['amount'])
        for t in thresholds:
            if t - margin <= amt < t:
                flags.append({
                    'transaction_id': row['id'],
                    'date': row['date'],
                    'description': row['description'],
                    'amount': row['amount'],
                    'rule': f'Juste sous le seuil de {t:,}€',
                    'detail': f"Montant de {amt:,.2f}€ — {t - amt:.2f}€ sous le seuil de {t:,}€ (structuring possible)",
                    'severity': SEVERITY_HIGH,
                })
    return pd.DataFrame(flags)


def run_all_rules(df: pd.DataFrame) -> pd.DataFrame:
    """Run all rules and return combined flagged transactions."""
    if df.empty:
        return pd.DataFrame()

    results = []
    rules = [
        flag_duplicates,
        flag_round_amounts,
        flag_unusual_hours,
        flag_frequent_small_transactions,
        flag_new_large_beneficiary,
        flag_velocity_spike,
        flag_just_below_threshold,
    ]

    for rule_fn in rules:
        try:
            result = rule_fn(df)
            if not result.empty:
                results.append(result)
        except Exception as e:
            print(f"Rule {rule_fn.__name__} failed: {e}")

    if not results:
        return pd.DataFrame()

    combined = pd.concat(results, ignore_index=True)
    return combined
