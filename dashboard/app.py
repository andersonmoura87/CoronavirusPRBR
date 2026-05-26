"""
pandemic-data-platform — Streamlit dashboard entrypoint.

Multi-page app using Streamlit's native pages/ directory.
This file is the home page; detailed views live in pages/.

Design decisions:
  - st.set_page_config must be the first Streamlit call in the entrypoint.
  - All heavy data fetching is in api_client.py with @st.cache_data.
  - Plotly is used for all charts: interactive, embeddable, no JS build step.
  - The colour palette follows the WHO COVID-19 visual identity for credibility
    (blues for cases, reds for deaths, greens for vaccinations).
"""

import streamlit as st
from api_client import fetch_health, fetch_cases, fetch_vaccination
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Page configuration — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Pandemic Data Platform",
    page_icon="🦠",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": "https://github.com/OWNER/pandemic-data-platform",
        "Report a bug": "https://github.com/OWNER/pandemic-data-platform/issues",
        "About": "Epidemiological analytics platform built with FastAPI, R, and Streamlit.",
    },
)

# ---------------------------------------------------------------------------
# Custom CSS — minimal overrides for a clean, professional look
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Hide default Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Metric card styling */
    [data-testid="metric-container"] {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px 16px;
    }

    /* Section headers */
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #1e40af;
        margin: 1rem 0 0.5rem 0;
        padding-bottom: 4px;
        border-bottom: 2px solid #bfdbfe;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — navigation and filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://www.who.int/images/default-source/wpro/_who-logo.png",
             width=80)
    st.title("Pandemic Platform")
    st.caption("Análise epidemiológica do Brasil")
    st.divider()

    scope = st.selectbox(
        "Recorte geográfico",
        options=["brasil", "parana", "maringa"],
        format_func=lambda x: {"brasil": "🇧🇷 Brasil", "parana": "🌿 Paraná",
                                "maringa": "🏙️ Maringá"}[x],
    )

    date_range = st.date_input(
        "Período",
        value=(date(2020, 3, 1), date(2023, 12, 31)),
        min_value=date(2020, 1, 1),
        max_value=date.today(),
        format="DD/MM/YYYY",
    )
    start_date = str(date_range[0]) if isinstance(date_range, tuple) and len(date_range) > 0 else None
    end_date   = str(date_range[1]) if isinstance(date_range, tuple) and len(date_range) > 1 else None

    st.divider()

    # Health check indicator
    health = fetch_health()
    if health and health.get("status") == "ok":
        r_label = health.get("r_service", "—")
        if r_label == "demo":
            st.warning("Modo demo", icon="⚡")
        else:
            st.success("API online", icon="✅")
        st.caption(f"R service: {r_label}")
    else:
        st.error("API offline", icon="🔴")

    st.caption("Fontes: brasil.io · OpenDataSUS · IBGE · BCB")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("🦠 Pandemic Data Platform")
st.markdown(
    "Plataforma de análise epidemiológica do Brasil — dados públicos, "
    "modelos estatísticos em R, API em produção."
)
st.divider()

# ---------------------------------------------------------------------------
# KPI cards — top-level summary metrics
# ---------------------------------------------------------------------------
with st.spinner("Carregando dados..."):
    cases_data = fetch_cases(scope, start_date, end_date, limit=3000)
    vac_data   = fetch_vaccination(
        state="PR" if scope in ("parana", "maringa") else None,
        start_date=start_date, end_date=end_date
    )

col1, col2, col3, col4 = st.columns(4)

if cases_data and cases_data.get("data"):
    df = pd.DataFrame(cases_data["data"])
    df["date"] = pd.to_datetime(df["date"])

    total_confirmed = df["confirmed"].max() if "confirmed" in df.columns else 0
    total_deaths    = df["deaths"].max()    if "deaths"    in df.columns else 0
    peak_daily      = df["new_confirmed"].max() if "new_confirmed" in df.columns else 0
    death_rate      = (total_deaths / total_confirmed * 100) if total_confirmed else 0

    col1.metric("Casos confirmados",    f"{int(total_confirmed or 0):,}".replace(",", "."))
    col2.metric("Óbitos",               f"{int(total_deaths or 0):,}".replace(",", "."))
    col3.metric("Pico diário",          f"{int(peak_daily or 0):,}".replace(",", "."))
    col4.metric("Letalidade",           f"{death_rate:.2f}%")
else:
    for col in (col1, col2, col3, col4):
        col.metric("—", "Sem dados")

st.divider()

# ---------------------------------------------------------------------------
# Casos — agregado mensal (ondas epidêmicas visíveis)
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Novos casos — visão mensal</p>', unsafe_allow_html=True)

if cases_data and cases_data.get("data"):
    df = pd.DataFrame(cases_data["data"])
    df["date"] = pd.to_datetime(df["date"])

    # Monthly aggregate — sums new_confirmed and new_deaths across all cities
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    monthly = (
        df.groupby("month")
        .agg(new_confirmed=("new_confirmed", "sum"), new_deaths=("new_deaths", "sum"))
        .reset_index()
    )
    monthly["label"] = monthly["month"].dt.strftime("%b/%y")

    fig = go.Figure()

    # Area fill for cases — shows wave shape clearly
    fig.add_trace(go.Scatter(
        x=monthly["label"], y=monthly["new_confirmed"],
        name="Novos casos (mensal)",
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.18)",
        line=dict(color="#3b82f6", width=2),
        mode="lines",
        hovertemplate="%{x}<br>Casos: %{y:,.0f}<extra></extra>",
    ))

    # Deaths as a line on same axis (scaled up ×10 for visibility, labelled)
    fig.add_trace(go.Scatter(
        x=monthly["label"], y=monthly["new_deaths"] * 10,
        name="Óbitos ×10",
        line=dict(color="#ef4444", width=2, dash="dot"),
        mode="lines",
        hovertemplate="%{x}<br>Óbitos: %{customdata:,.0f}<extra></extra>",
        customdata=monthly["new_deaths"],
    ))

    # Annotate peak month
    peak_idx = monthly["new_confirmed"].idxmax()
    fig.add_annotation(
        x=monthly.loc[peak_idx, "label"],
        y=monthly.loc[peak_idx, "new_confirmed"],
        text=f"Pico: {int(monthly.loc[peak_idx, 'new_confirmed']):,}".replace(",", "."),
        showarrow=True, arrowhead=2, arrowcolor="#1d4ed8",
        font=dict(size=11, color="#1d4ed8"),
        bgcolor="white", bordercolor="#1d4ed8", borderwidth=1,
        ay=-40,
    )

    fig.update_layout(
        height=360, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, title="Mês",
                   tickangle=-45, tickfont=dict(size=10)),
        yaxis=dict(title="Novos casos / mês", gridcolor="#f1f5f9"),
        margin=dict(l=0, r=0, t=30, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Linha tracejada vermelha = óbitos ×10 (escala multiplicada para caber no mesmo eixo)")
else:
    st.info("Sem dados de casos para o período selecionado.")

# ---------------------------------------------------------------------------
# Vacinação — stacked bar mensal (como no canvas)
# ---------------------------------------------------------------------------
st.markdown('<p class="section-header">Vacinação — doses por mês</p>', unsafe_allow_html=True)

if vac_data and vac_data.get("data"):
    vdf = pd.DataFrame(vac_data["data"])
    vdf["date"] = pd.to_datetime(vdf["date"])

    # Monthly aggregate
    vdf["month"] = vdf["date"].dt.to_period("M").dt.to_timestamp()
    vmonthly = (
        vdf.groupby("month")
        .agg(dose_1=("dose_1", "sum"), dose_2=("dose_2", "sum"),
             dose_reforco=("dose_reforco", "sum"))
        .reset_index()
    )
    vmonthly["label"] = vmonthly["month"].dt.strftime("%b/%y")

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=vmonthly["label"], y=vmonthly["dose_1"],
        name="1ª dose", marker_color="#86efac",
        hovertemplate="%{x}<br>1ª dose: %{y:,.0f}<extra></extra>",
    ))
    fig2.add_trace(go.Bar(
        x=vmonthly["label"], y=vmonthly["dose_2"],
        name="2ª dose", marker_color="#22c55e",
        hovertemplate="%{x}<br>2ª dose: %{y:,.0f}<extra></extra>",
    ))
    fig2.add_trace(go.Bar(
        x=vmonthly["label"], y=vmonthly["dose_reforco"],
        name="Reforço", marker_color="#15803d",
        hovertemplate="%{x}<br>Reforço: %{y:,.0f}<extra></extra>",
    ))

    fig2.update_layout(
        barmode="stack", height=300,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, title="Mês",
                   tickangle=-45, tickfont=dict(size=10)),
        yaxis=dict(title="Doses aplicadas / mês", gridcolor="#f1f5f9"),
        margin=dict(l=0, r=0, t=30, b=60),
    )
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("Sem dados de vacinação. Execute o ETL para Paraná primeiro.")

# ---------------------------------------------------------------------------
# Footer with navigation hint
# ---------------------------------------------------------------------------
st.divider()
st.markdown(
    "👈 Use o menu lateral para acessar **Previsão de Casos** (modelos R), "
    "**Correlação Econômica** e **Vacinação** detalhada."
)
