"""
FraudLens — Analyse de relevés bancaires pour détecter les fraudes.
IA intégrée, 100% locale — aucune API externe requise.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from extractors.pdf_extractor import extract_pdf
from analyzers.fraud_engine import run_engine, generate_report, compute_risk_score
from analyzers.stats_analyzer import run_all_stats
from utils.report_generator import generate_excel_report


def build_executive_summary(df, flags_df, stats, risk_score, business_type):
    """Génère 4 à 6 points clés lisibles en 20 secondes."""
    points = []
    n_flags = len(flags_df) if not flags_df.empty else 0
    n_high  = len(flags_df[flags_df['severity'] == '🔴 Élevé']) if not flags_df.empty else 0
    net     = stats.get('net_flow', 0)
    total_d = stats.get('total_debit_amount', 0)
    total_c = stats.get('total_credit_amount', 0)
    avg_d   = stats.get('avg_debit', 0)

    # Montant total impliqué dans les alertes
    amount_at_risk = 0
    if not flags_df.empty and 'amount' in flags_df.columns:
        amount_at_risk = flags_df['amount'].abs().sum()

    # Point 1 — flux global
    if net < 0:
        points.append(("💸", f"L'entreprise a dépensé <b>{total_d:,.0f}€</b> pour <b>{total_c:,.0f}€</b> de rentrées — flux net <b style='color:#e94560'>{net:,.0f}€</b> sur la période."))
    else:
        points.append(("💰", f"L'entreprise a encaissé <b>{total_c:,.0f}€</b> et dépensé <b>{total_d:,.0f}€</b> — flux net <b style='color:#00d4aa'>+{net:,.0f}€</b> sur la période."))

    # Point 2 — alertes
    if n_flags == 0:
        points.append(("✅", "Aucune transaction suspecte détectée. Les mouvements semblent cohérents avec une activité normale."))
    elif n_high >= 5:
        points.append(("🚨", f"<b>{n_high} alertes critiques</b> sur {n_flags} au total — plusieurs patterns de fraude simultanés détectés. Investigation urgente recommandée."))
    elif n_high > 0:
        points.append(("⚠️", f"<b>{n_high} alerte(s) critique(s)</b> parmi {n_flags} signaux détectés — vérification avec justificatifs nécessaire."))
    else:
        points.append(("🟡", f"{n_flags} anomalie(s) mineures à modérées — pas de signal critique, mais un suivi est conseillé."))

    # Point 3 — montant à risque
    if amount_at_risk > 0:
        pct = amount_at_risk / total_d * 100 if total_d > 0 else 0
        points.append(("🔎", f"<b>{amount_at_risk:,.0f}€</b> de transactions sont impliquées dans des alertes, soit <b>{pct:.1f}%</b> du total des dépenses."))

    # Point 4 — catégories principales
    if not flags_df.empty and 'categorie' in flags_df.columns:
        top_cats = flags_df['categorie'].value_counts().head(2).index.tolist()
        CATS_FR = {
            'fraude_interne': 'fraude interne', 'fraude_fournisseur': 'fraude fournisseur',
            'fraude_caisse': 'fraude de caisse', 'fraude_paie': 'fraude sur la paie',
            'fraude_financiere': 'fraude financière', 'anomalie_depenses': 'anomalie de dépenses',
            'anomalie_statistique': 'anomalie statistique', 'anomalie_temporelle': 'anomalie temporelle',
            'risque_fournisseur': 'risque fournisseur',
        }
        cats_fr = [CATS_FR.get(c, c) for c in top_cats]
        points.append(("📂", f"Principaux types d'anomalies : <b>{' et '.join(cats_fr)}</b>."))

    # Point 5 — transaction moyenne vs normale
    if avg_d > 0:
        if avg_d > 3000:
            points.append(("📊", f"Le débit moyen est de <b>{avg_d:,.0f}€</b> par transaction — élevé pour un(e) {business_type.lower()}, à surveiller."))
        else:
            points.append(("📊", f"Le débit moyen est de <b>{avg_d:,.0f}€</b> par transaction — cohérent avec l'activité."))

    return points, amount_at_risk


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FraudLens — Détection de fraude",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        padding: 2rem 2rem 1.5rem;
        border-radius: 14px;
        margin-bottom: 1.5rem;
        text-align: center;
    }
    .main-header h1 { color: #e94560; margin: 0; font-size: 2.6rem; font-weight: 800; letter-spacing: -1px; }
    .main-header p  { color: #a8b2d8; margin: 0.5rem 0 0; font-size: 1.05rem; }
    .main-header .badge {
        display: inline-block;
        background: #00d4aa22;
        border: 1px solid #00d4aa55;
        color: #00d4aa;
        border-radius: 20px;
        padding: 0.2rem 0.9rem;
        font-size: 0.8rem;
        margin-top: 0.7rem;
    }

    .risk-critique { border-left: 6px solid #e94560; background: #2d1a20; padding: 1.2rem; border-radius: 10px; margin: 1rem 0; }
    .risk-eleve    { border-left: 6px solid #ff6b35; background: #2d2010; padding: 1.2rem; border-radius: 10px; margin: 1rem 0; }
    .risk-modere   { border-left: 6px solid #ffd700; background: #2d2b10; padding: 1.2rem; border-radius: 10px; margin: 1rem 0; }
    .risk-faible   { border-left: 6px solid #00d4aa; background: #102d25; padding: 1.2rem; border-radius: 10px; margin: 1rem 0; }

    .summary-box {
        background: #13172a;
        border: 1px solid #2d3561;
        border-radius: 12px;
        padding: 1.4rem 1.6rem;
        margin-bottom: 1rem;
    }
    .summary-box h3 { color: #e2e8f0; margin: 0 0 0.8rem; font-size: 1.1rem; }
    .summary-point {
        display: flex;
        align-items: flex-start;
        gap: 0.6rem;
        margin-bottom: 0.55rem;
        font-size: 0.97rem;
        color: #cbd5e1;
        line-height: 1.5;
    }
    .summary-point .icon { flex-shrink: 0; font-size: 1.1rem; margin-top: 0.05rem; }
    .amount-risk {
        background: #e9456022;
        border: 1px solid #e9456055;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        color: #e94560;
        font-weight: 700;
        font-size: 1.3rem;
        text-align: center;
    }
    .amount-ok {
        background: #00d4aa22;
        border: 1px solid #00d4aa55;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        color: #00d4aa;
        font-weight: 700;
        font-size: 1.3rem;
        text-align: center;
    }

    .upload-zone {
        border: 2px dashed #2d3561;
        border-radius: 14px;
        padding: 2.5rem;
        text-align: center;
        background: #0d1117;
    }
    div[data-testid="stSidebarContent"] { background: #0d1117; }
    .stTabs [data-baseweb="tab"] { font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Paramètres")
    st.markdown("---")

    business_type = st.selectbox(
        "Type d'établissement",
        ["Restaurant / Bar", "Commerce de détail", "Artisan / Prestataire de service",
         "Hôtel / Hébergement", "Boulangerie / Épicerie", "Autre TPE/PME"],
        help="Adapte les seuils d'alerte à votre secteur d'activité."
    )

    st.markdown("---")
    st.markdown("### Modules d'analyse")
    opt_rules = st.checkbox("Règles métier (16 cas)", value=True)
    opt_stats = st.checkbox("Analyse statistique", value=True)

    st.markdown("---")
    st.markdown("""
    **Confidentialité totale**
    - Tout s'exécute sur votre machine
    - Aucune donnée n'est envoyée sur internet
    - Aucun compte ou API requis
    """)
    st.markdown("---")
    st.caption("FraudLens v2.0 — IA intégrée 100% locale")


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>🔍 FraudLens</h1>
    <p>Analyse intelligente de relevés bancaires — Détection de fraude pour TPE/PME</p>
    <span class="badge">✅ IA intégrée — 100% local — Aucune API requise</span>
</div>
""", unsafe_allow_html=True)


# ── Upload ────────────────────────────────────────────────────────────────────
st.markdown("## 📄 Importer les relevés bancaires")
col_up1, col_up2 = st.columns([3, 1])
with col_up1:
    uploaded_files = st.file_uploader(
        "Glissez-déposez vos relevés PDF",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
with col_up2:
    st.info("📎 Plusieurs fichiers acceptés\n\nAnalyse croisée automatique")

if not uploaded_files:
    st.markdown("""
    <div class="upload-zone">
        <h3 style="color:#a8b2d8">Importez vos relevés PDF pour commencer</h3>
        <p style="color:#6272a4">
            FraudLens extrait automatiquement toutes les transactions<br>
            et les analyse avec <b>16 modules de détection de fraude intégrés</b>.<br><br>
            Compatible : BNP Paribas · Crédit Agricole · Société Générale · CIC · LCL · Banque Populaire · CaixaBank · Caisse d'Épargne
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🛡️ Cas de fraude couverts par le moteur intégré")
    cas_cols = st.columns(3)
    cas = [
        ("💸 Fraude interne", ["Doublons de paiement", "Dépenses personnelles", "Virements personnels", "Remboursements suspects"]),
        ("🏭 Fraude fournisseur", ["Fournisseur fictif", "Première grosse facture", "Concentration excessive", "Surfacturation"]),
        ("💰 Fraude caisse", ["Retraits espèces élevés", "Montants ronds suspects", "Fractionnement (smurfing)", "Juste sous les seuils"]),
        ("👔 Fraude paie", ["Employé fantôme", "Doublon de salaire", "Acomptes non autorisés", "Irrégularité mensuelle"]),
        ("🔍 Anomalies statistiques", ["Z-score aberrant", "Pic de dépenses", "Silence puis reprise", "Distribution anormale"]),
        ("⚖️ Fraude financière", ["Blanchiment (structuring)", "Virements multiples/jour", "Montants juste sous seuil", "Comportement inhabituel"]),
    ]
    for i, (titre, items) in enumerate(cas):
        with cas_cols[i % 3]:
            st.markdown(f"**{titre}**")
            for item in items:
                st.markdown(f"  - {item}")
            st.markdown("")
    st.stop()


# ── Extraction ────────────────────────────────────────────────────────────────
with st.spinner("Extraction des transactions depuis les PDF..."):
    all_dfs = []
    errors = []
    for f in uploaded_files:
        try:
            df_file = extract_pdf(f)
            if not df_file.empty:
                df_file['source_file'] = f.name
                all_dfs.append(df_file)
            else:
                errors.append(f.name)
        except Exception as e:
            errors.append(f"{f.name}: {e}")

for err in errors:
    st.warning(f"Impossible d'extraire: {err}")

if not all_dfs:
    st.error(
        "Aucune transaction extraite. Vérifiez que vos PDF ne sont pas protégés par mot de passe "
        "et qu'ils contiennent un tableau de transactions lisible."
    )
    st.stop()

df = pd.concat(all_dfs, ignore_index=True).sort_values('date').reset_index(drop=True)
df['id'] = df.index + 1

st.success(f"✅ **{len(df):,} transactions** extraites depuis **{len(all_dfs)} fichier(s)**")


# ── Analyse ───────────────────────────────────────────────────────────────────
with st.spinner("Analyse en cours — 16 modules de détection..."):
    stats_results = run_all_stats(df)
    stats = stats_results.get('stats', {})
    monthly = stats_results.get('monthly', pd.DataFrame())

    flags_df = pd.DataFrame()
    if opt_rules:
        flags_df = run_engine(df)

    if opt_stats:
        extra = []
        for key in ['zscore_flags', 'iqr_flags', 'temporal_flags']:
            f = stats_results.get(key, pd.DataFrame())
            if not f.empty:
                if 'categorie' not in f.columns:
                    f['categorie'] = 'anomalie_statistique'
                if 'score_contribution' not in f.columns:
                    f['score_contribution'] = 50
                extra.append(f)
        if extra:
            extra_df = pd.concat(extra, ignore_index=True)
            flags_df = pd.concat([flags_df, extra_df], ignore_index=True) if not flags_df.empty else extra_df

    if not flags_df.empty and 'transaction_id' in flags_df.columns:
        flags_df = flags_df.drop_duplicates(subset=['transaction_id', 'rule'])

    risk_score = compute_risk_score(flags_df, stats)
    report_text = generate_report(df, flags_df, stats, business_type)


# ── KPIs ──────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("## 📊 Vue d'ensemble")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Transactions", f"{stats.get('total_transactions', 0):,}")
k2.metric("Total débits", f"{stats.get('total_debit_amount', 0):,.0f} €")
k3.metric("Total crédits", f"{stats.get('total_credit_amount', 0):,.0f} €")
k4.metric("Flux net", f"{stats.get('net_flow', 0):,.0f} €")
n_flags = len(flags_df) if not flags_df.empty else 0
n_high  = len(flags_df[flags_df['severity'] == '🔴 Élevé']) if not flags_df.empty else 0
k5.metric("Alertes", f"{n_flags}", delta=f"{n_high} critiques" if n_high else None, delta_color="inverse")


# ── Score de risque ───────────────────────────────────────────────────────────
if risk_score >= 70:
    css, label = 'risk-critique', '🚨 RISQUE CRITIQUE'
elif risk_score >= 45:
    css, label = 'risk-eleve', '🔴 RISQUE ÉLEVÉ'
elif risk_score >= 20:
    css, label = 'risk-modere', '🟠 RISQUE MODÉRÉ'
else:
    css, label = 'risk-faible', '✅ RISQUE FAIBLE'

# Score bar
score_pct = risk_score
score_color = '#e94560' if risk_score >= 70 else ('#ff6b35' if risk_score >= 45 else ('#ffd700' if risk_score >= 20 else '#00d4aa'))

st.markdown(f"""
<div class="{css}">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <h2 style="margin:0">{label}</h2>
        <span style="font-size:2rem;font-weight:800;color:{score_color}">{risk_score}<span style="font-size:1rem;font-weight:400;color:#a8b2d8">/100</span></span>
    </div>
    <div style="background:#ffffff22;border-radius:6px;height:10px;width:100%">
        <div style="background:{score_color};border-radius:6px;height:10px;width:{score_pct}%"></div>
    </div>
    <p style="margin:0.5rem 0 0;opacity:0.8">{n_flags} alerte(s) détectée(s) — {n_high} critique(s)</p>
</div>
""", unsafe_allow_html=True)
st.markdown("")


# ── Résumé exécutif ───────────────────────────────────────────────────────────
summary_points, amount_at_risk = build_executive_summary(df, flags_df, stats, risk_score, business_type)

st.markdown("## 📋 Résumé — ce qu'il faut retenir")
sum_col1, sum_col2 = st.columns([3, 1])

with sum_col1:
    points_html = ''.join(
        f'<div class="summary-point"><span class="icon">{icon}</span><span>{text}</span></div>'
        for icon, text in summary_points
    )
    st.markdown(f'<div class="summary-box"><h3>Points clés</h3>{points_html}</div>', unsafe_allow_html=True)

with sum_col2:
    if amount_at_risk > 0:
        st.markdown(f'<div class="amount-risk">⚠️ Montant à risque<br>{amount_at_risk:,.0f} €</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="amount-ok">✅ Montant à risque<br>0 €</div>', unsafe_allow_html=True)
    st.markdown("")
    total_txns = stats.get('total_transactions', 0)
    pct_flagged = len(flags_df) / total_txns * 100 if total_txns > 0 and not flags_df.empty else 0
    st.metric("% transactions signalées", f"{pct_flagged:.1f}%")
    st.metric("Période analysée", f"{stats.get('date_range_days', 0)} jours")

st.markdown("")


# ── Onglets ───────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 Graphiques", "🚨 Alertes détaillées", "🏭 Bénéficiaires", "📝 Rapport complet", "📋 Toutes les transactions"])


# ── TAB 1 — Graphiques ────────────────────────────────────────────────────────
with tab1:
    if not monthly.empty:
        col1, col2 = st.columns(2)

        with col1:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=monthly['month'], y=monthly['total_in'],  name='Entrées', marker_color='#00d4aa'))
            fig.add_trace(go.Bar(x=monthly['month'], y=-monthly['total_out'], name='Sorties', marker_color='#e94560'))
            fig.add_trace(go.Scatter(x=monthly['month'], y=monthly['net'],   name='Net',    mode='lines+markers', line=dict(color='#ffd700', width=2)))
            fig.update_layout(title='Flux mensuels (€)', barmode='relative',
                              paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                              font_color='#a8b2d8', height=350)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            df2 = df.copy()
            df2['desc_short'] = df2['description'].str[:40]
            top = (df2[df2['amount'] < 0].groupby('desc_short')['amount'].sum().abs()
                   .sort_values(ascending=False).head(10).reset_index())
            if not top.empty:
                fig2 = px.bar(top, x='amount', y='desc_short', orientation='h',
                              title='Top 10 postes de dépenses (€)',
                              labels={'amount': 'Total (€)', 'desc_short': ''},
                              color='amount', color_continuous_scale='Reds')
                fig2.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                   font_color='#a8b2d8', height=350, showlegend=False)
                st.plotly_chart(fig2, use_container_width=True)

        col3, col4 = st.columns(2)
        with col3:
            fig3 = px.scatter(df, x='date', y='amount',
                              title='Transactions dans le temps',
                              color=df['amount'].apply(lambda x: 'Crédit' if x > 0 else 'Débit'),
                              color_discrete_map={'Crédit': '#00d4aa', 'Débit': '#e94560'},
                              hover_data=['description'])
            if not flags_df.empty and 'transaction_id' in flags_df.columns:
                flagged = df[df['id'].isin(flags_df['transaction_id'].unique())]
                if not flagged.empty:
                    fig3.add_trace(go.Scatter(
                        x=flagged['date'], y=flagged['amount'], mode='markers',
                        marker=dict(symbol='star', size=14, color='#ffd700', line=dict(color='#fff', width=1)),
                        name='⚠ Alertes', hovertext=flagged['description']
                    ))
            fig3.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                               font_color='#a8b2d8', height=350)
            st.plotly_chart(fig3, use_container_width=True)

        with col4:
            # Répartition des alertes par catégorie
            if not flags_df.empty and 'categorie' in flags_df.columns:
                cat_counts = flags_df['categorie'].value_counts().reset_index()
                cat_counts.columns = ['Catégorie', 'Nombre']
                CATS_FR = {
                    'fraude_interne': 'Fraude interne', 'fraude_fournisseur': 'Fraude fournisseur',
                    'fraude_caisse': 'Fraude caisse', 'fraude_paie': 'Fraude paie',
                    'fraude_financiere': 'Fraude financière', 'anomalie_depenses': 'Anomalie dépenses',
                    'anomalie_statistique': 'Anomalie stat.', 'anomalie_temporelle': 'Anomalie temporelle',
                    'risque_fournisseur': 'Risque fournisseur',
                }
                cat_counts['Catégorie'] = cat_counts['Catégorie'].map(CATS_FR).fillna(cat_counts['Catégorie'])
                fig4 = px.pie(cat_counts, values='Nombre', names='Catégorie',
                              title='Répartition des alertes par type',
                              color_discrete_sequence=px.colors.qualitative.Set3)
                fig4.update_layout(paper_bgcolor='rgba(0,0,0,0)', font_color='#a8b2d8', height=350)
                st.plotly_chart(fig4, use_container_width=True)
            else:
                amounts = df['amount'].abs()
                fig4 = px.histogram(amounts[amounts < amounts.quantile(0.95)],
                                    title='Distribution des montants',
                                    labels={'value': 'Montant (€)'}, nbins=40,
                                    color_discrete_sequence=['#7b5ea7'])
                fig4.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                                   font_color='#a8b2d8', height=350, showlegend=False)
                st.plotly_chart(fig4, use_container_width=True)
        # Heatmap dépenses par jour de la semaine
        st.markdown("#### Activité par jour de la semaine")
        df_heat = df.copy()
        df_heat['dow'] = df_heat['date'].dt.day_name()
        df_heat['month_label'] = df_heat['date'].dt.strftime('%b %Y')
        dow_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        dow_fr    = {'Monday': 'Lundi', 'Tuesday': 'Mardi', 'Wednesday': 'Mercredi',
                     'Thursday': 'Jeudi', 'Friday': 'Vendredi', 'Saturday': 'Samedi', 'Sunday': 'Dimanche'}
        debits_heat = df_heat[df_heat['amount'] < 0].copy()
        if not debits_heat.empty:
            pivot = (debits_heat.groupby(['month_label', 'dow'])['amount']
                     .sum().abs().reset_index()
                     .pivot(index='dow', columns='month_label', values='amount')
                     .reindex([d for d in dow_order if d in debits_heat['dow'].unique()])
                     .fillna(0))
            pivot.index = [dow_fr.get(d, d) for d in pivot.index]
            fig_heat = px.imshow(
                pivot, color_continuous_scale='Reds',
                labels=dict(x='Mois', y='Jour', color='Débits (€)'),
                title='Heatmap des dépenses par jour (€)',
                aspect='auto',
            )
            fig_heat.update_layout(paper_bgcolor='rgba(0,0,0,0)', font_color='#a8b2d8', height=300)
            st.plotly_chart(fig_heat, use_container_width=True)
    else:
        st.info("Pas assez de données pour afficher les graphiques.")


# ── TAB 2 — Alertes ───────────────────────────────────────────────────────────
with tab2:
    if flags_df.empty:
        st.success("✅ Aucune alerte détectée. Les transactions semblent normales.")
    else:
        st.markdown(f"### {len(flags_df)} alerte(s) — triées par sévérité")

        for sev in ['🔴 Élevé', '🟠 Moyen', '🟡 Faible']:
            group = flags_df[flags_df['severity'] == sev]
            if group.empty:
                continue
            with st.expander(f"{sev} — {len(group)} alerte(s)", expanded=(sev == '🔴 Élevé')):
                cols_show = [c for c in ['date', 'description', 'amount', 'rule', 'detail', 'categorie'] if c in group.columns]
                display = group[cols_show].copy()
                rename = {'date': 'Date', 'description': 'Description', 'amount': 'Montant (€)',
                          'rule': 'Type de fraude', 'detail': 'Explication', 'categorie': 'Catégorie'}
                display.columns = [rename.get(c, c) for c in cols_show]
                if 'Catégorie' in display.columns:
                    CATS_FR = {
                        'fraude_interne': 'Fraude interne', 'fraude_fournisseur': 'Fraude fournisseur',
                        'fraude_caisse': 'Fraude caisse', 'fraude_paie': 'Fraude paie',
                        'fraude_financiere': 'Fraude financière', 'anomalie_depenses': 'Anomalie dépenses',
                        'anomalie_statistique': 'Anomalie stat.', 'anomalie_temporelle': 'Anomalie temporelle',
                        'risque_fournisseur': 'Risque fournisseur',
                    }
                    display['Catégorie'] = display['Catégorie'].map(CATS_FR).fillna(display['Catégorie'])
                st.dataframe(display, use_container_width=True, hide_index=True)


# ── TAB 3 — Bénéficiaires ────────────────────────────────────────────────────
with tab3:
    st.markdown("### Analyse par bénéficiaire / poste de dépense")
    debits_b = df[df['amount'] < 0].copy()
    if debits_b.empty:
        st.info("Aucun débit trouvé.")
    else:
        debits_b['desc_norm'] = debits_b['description'].str[:60]
        benef = debits_b.groupby('desc_norm').agg(
            Total=('amount', lambda x: x.abs().sum()),
            Transactions=('amount', 'count'),
            Moyenne=('amount', lambda x: x.abs().mean()),
            Premier=('date', 'min'),
            Dernier=('date', 'max'),
        ).reset_index().sort_values('Total', ascending=False)
        benef.columns = ['Bénéficiaire', 'Total (€)', 'Nb transactions', 'Moyenne (€)', 'Première opération', 'Dernière opération']

        # Marquer ceux qui ont des alertes
        if not flags_df.empty and 'description' in flags_df.columns:
            flagged_descs = set(flags_df['description'].str[:60].str.lower())
            benef['⚠'] = benef['Bénéficiaire'].str.lower().apply(
                lambda d: '🚨' if d in flagged_descs else ''
            )

        total_all = benef['Total (€)'].sum()
        benef['% total'] = (benef['Total (€)'] / total_all * 100).round(1).astype(str) + '%'

        st.caption(f"{len(benef)} bénéficiaires distincts — {total_all:,.0f}€ de débits totaux")

        # Filtre
        search_b = st.text_input("Rechercher un bénéficiaire", "")
        if search_b:
            benef = benef[benef['Bénéficiaire'].str.contains(search_b, case=False, na=False)]

        st.dataframe(benef, use_container_width=True, hide_index=True, height=500)

        # Donut top 10
        top10 = benef.head(10)
        fig_donut = px.pie(top10, values='Total (€)', names='Bénéficiaire',
                           title='Répartition des 10 premiers postes de dépenses',
                           hole=0.45, color_discrete_sequence=px.colors.qualitative.Pastel)
        fig_donut.update_layout(paper_bgcolor='rgba(0,0,0,0)', font_color='#a8b2d8', height=380)
        st.plotly_chart(fig_donut, use_container_width=True)


# ── TAB 4 — Rapport narratif ──────────────────────────────────────────────────
with tab4:
    st.markdown(report_text)


# ── TAB 5 — Transactions ─────────────────────────────────────────────────────
with tab5:
    st.markdown(f"### {len(df):,} transactions extraites")

    c1, c2, c3 = st.columns(3)
    with c1:
        direction = st.selectbox("Direction", ["Toutes", "Débits uniquement", "Crédits uniquement"])
    with c2:
        search = st.text_input("Rechercher", "")
    with c3:
        flagged_only = st.checkbox("Alertes uniquement")

    filtered = df.copy()
    if direction == "Débits uniquement":
        filtered = filtered[filtered['amount'] < 0]
    elif direction == "Crédits uniquement":
        filtered = filtered[filtered['amount'] > 0]
    if search:
        filtered = filtered[filtered['description'].str.contains(search, case=False, na=False)]
    if flagged_only and not flags_df.empty and 'transaction_id' in flags_df.columns:
        filtered = filtered[filtered['id'].isin(flags_df['transaction_id'].unique())]

    display = filtered[['id', 'date', 'description', 'debit', 'credit', 'amount']].copy()
    display.columns = ['ID', 'Date', 'Description', 'Débit (€)', 'Crédit (€)', 'Montant (€)']
    st.dataframe(display, use_container_width=True, hide_index=True, height=500)


# ── Export ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("## 💾 Exporter le rapport")
c1, c2 = st.columns(2)

with c1:
    try:
        excel_bytes = generate_excel_report(df, flags_df, stats, report_text, monthly)
        st.download_button(
            "📥 Rapport Excel complet",
            data=excel_bytes,
            file_name=f"fraudlens_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        st.error(f"Erreur Excel: {e}")

with c2:
    if not flags_df.empty:
        csv = flags_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            "📥 Alertes CSV",
            data=csv,
            file_name=f"alertes_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.info("Aucune alerte à exporter.")

st.markdown("---")
st.caption("FraudLens v2.0 — Analyse 100% locale · Aucune donnée envoyée · Aucune API requise")
