"""
Export analysis results to Excel report.
"""

import pandas as pd
import io
from datetime import datetime


def generate_excel_report(
    df: pd.DataFrame,
    all_flags: pd.DataFrame,
    stats: dict,
    ai_analysis: str,
    monthly: pd.DataFrame,
) -> bytes:
    """Generate a complete Excel report with multiple sheets."""
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Summary
        summary_data = {
            'Indicateur': [
                'Période analysée',
                'Nombre total de transactions',
                'Nombre de débits',
                'Nombre de crédits',
                'Total débits (€)',
                'Total crédits (€)',
                'Flux net (€)',
                'Débit moyen (€)',
                'Débit maximum (€)',
                'Nombre d\'alertes',
            ],
            'Valeur': [
                f"{df['date'].min().date()} → {df['date'].max().date()}" if not df.empty else 'N/A',
                stats.get('total_transactions', 0),
                stats.get('total_debits', 0),
                stats.get('total_credits', 0),
                f"{stats.get('total_debit_amount', 0):,.2f}",
                f"{stats.get('total_credit_amount', 0):,.2f}",
                f"{stats.get('net_flow', 0):,.2f}",
                f"{stats.get('avg_debit', 0):,.2f}",
                f"{stats.get('max_debit', 0):,.2f}",
                len(all_flags) if not all_flags.empty else 0,
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Résumé', index=False)

        # Sheet 2: All transactions
        if not df.empty:
            export_df = df[['id', 'date', 'description', 'debit', 'credit', 'amount', 'page']].copy()
            export_df.columns = ['ID', 'Date', 'Description', 'Débit (€)', 'Crédit (€)', 'Montant net (€)', 'Page PDF']
            export_df.to_excel(writer, sheet_name='Toutes les transactions', index=False)

        # Sheet 3: Flagged transactions
        if not all_flags.empty:
            flags_export = all_flags[['date', 'description', 'amount', 'rule', 'detail', 'severity']].copy()
            flags_export.columns = ['Date', 'Description', 'Montant (€)', 'Règle', 'Détail', 'Sévérité']
            flags_export.to_excel(writer, sheet_name='Alertes', index=False)

        # Sheet 4: Monthly stats
        if not monthly.empty:
            monthly_export = monthly.copy()
            monthly_export['month'] = monthly_export['month'].dt.strftime('%Y-%m')
            monthly_export.columns = ['Mois', 'Entrées (€)', 'Sorties (€)', 'Nb transactions', 'Net (€)']
            monthly_export.to_excel(writer, sheet_name='Statistiques mensuelles', index=False)

        # Sheet 5: AI Analysis
        ai_df = pd.DataFrame({'Analyse IA (Claude)': [ai_analysis]})
        ai_df.to_excel(writer, sheet_name='Analyse IA', index=False)

    return output.getvalue()
