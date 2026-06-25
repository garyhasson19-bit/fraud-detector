"""
AI-powered fraud analysis using Claude.
Sends summarized transaction data (not raw PDFs) to the API.
"""

import anthropic
import pandas as pd
import json
from typing import Optional


def build_summary_for_ai(df: pd.DataFrame, flags: pd.DataFrame, stats: dict) -> str:
    """Build a concise text summary to send to Claude — no raw PII."""
    lines = []
    lines.append("=== RÉSUMÉ DES TRANSACTIONS ===")
    lines.append(f"Période: {df['date'].min().date()} → {df['date'].max().date()}")
    lines.append(f"Nombre total de transactions: {stats.get('total_transactions', 0)}")
    lines.append(f"Total débits: {stats.get('total_debit_amount', 0):,.2f}€")
    lines.append(f"Total crédits: {stats.get('total_credit_amount', 0):,.2f}€")
    lines.append(f"Flux net: {stats.get('net_flow', 0):,.2f}€")
    lines.append(f"Débit moyen: {stats.get('avg_debit', 0):,.2f}€")
    lines.append(f"Débit max: {stats.get('max_debit', 0):,.2f}€")
    lines.append("")

    # Top beneficiaries by total outflow
    df_copy = df.copy()
    df_copy['desc_short'] = df_copy['description'].str[:60]
    top_debits = (
        df_copy[df_copy['amount'] < 0]
        .groupby('desc_short')['amount']
        .agg(['sum', 'count'])
        .sort_values('sum')
        .head(15)
    )
    lines.append("=== TOP 15 BÉNÉFICIAIRES (DÉBITS) ===")
    for desc, row in top_debits.iterrows():
        lines.append(f"  {desc}: {abs(row['sum']):,.2f}€ ({int(row['count'])} fois)")
    lines.append("")

    # Flagged transactions
    if not flags.empty:
        lines.append(f"=== {len(flags)} ALERTES DÉTECTÉES ===")
        for _, flag in flags.iterrows():
            lines.append(
                f"[{flag['severity']}] {flag['rule']} | "
                f"Date: {flag['date'].date()} | "
                f"Montant: {flag['amount']:,.2f}€ | "
                f"Description: {str(flag['description'])[:60]} | "
                f"Détail: {flag['detail']}"
            )
    else:
        lines.append("=== AUCUNE ALERTE DÉTECTÉE PAR LES RÈGLES ===")

    return '\n'.join(lines)


def analyze_with_claude(
    df: pd.DataFrame,
    flags: pd.DataFrame,
    stats: dict,
    api_key: str,
    business_type: str = "restaurant/commerce",
) -> dict:
    """
    Send transaction summary to Claude for intelligent fraud analysis.
    Returns a structured analysis dict.
    """
    summary = build_summary_for_ai(df, flags, stats)

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = """Tu es un expert en détection de fraude financière et en analyse de relevés bancaires d'entreprise.
Tu analyses des données financières d'une petite entreprise (restaurant, commerce, TPE/PME) pour détecter:
- Des fraudes internes (employés, dirigeants)
- Des fraudes externes (fournisseurs fictifs, détournements)
- Des irrégularités comptables
- Des patterns anormaux

Sois précis, professionnel et structuré. Réponds en français. Ne révèle jamais de données personnelles dans ta réponse.
Priorise les risques du plus grave au moins grave."""

    user_prompt = f"""Analyse ce relevé bancaire d'un(e) {business_type} et identifie tous les risques de fraude potentiels.

{summary}

Fournis une analyse structurée avec:
1. **VERDICT GLOBAL** (Risque faible/modéré/élevé/critique) avec justification en 2-3 phrases
2. **ANOMALIES CRITIQUES** : Liste les 3-5 alertes les plus graves avec explication détaillée
3. **PATTERNS SUSPECTS** : Identifie des comportements récurrents ou des tendances inquiétantes
4. **CE QUI EST NORMAL** : Rassure sur les éléments qui semblent légitimes
5. **RECOMMANDATIONS** : 3-5 actions concrètes à mener (audit, documents à demander, autorités à contacter si nécessaire)
6. **SCORE DE RISQUE** : Note de 0 à 100 (0=aucun risque, 100=fraude certaine)

Format ta réponse en markdown pour une lecture facile."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    analysis_text = response.content[0].text

    # Extract risk score if present
    risk_score = None
    import re
    score_match = re.search(r'score\s*(?:de\s*)?risque\s*[:\-]?\s*(\d+)', analysis_text, re.IGNORECASE)
    if not score_match:
        score_match = re.search(r'\b(\d+)\s*/\s*100', analysis_text)
    if score_match:
        try:
            risk_score = int(score_match.group(1))
        except Exception:
            pass

    return {
        'analysis': analysis_text,
        'risk_score': risk_score,
        'tokens_used': response.usage.input_tokens + response.usage.output_tokens,
    }
