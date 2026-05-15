"""
Página: Previsão de Casos
Mostra o output dos modelos R (ARIMA, Prophet, Holt-Winters, Ensemble)
com banda de confiança e comparação entre modelos.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from api_client import fetch_forecast

st.set_page_config(page_title="Previsão · Pandemic Platform", layout="wide")

st.title("📈 Previsão de Casos — Modelos Estatísticos em R")
st.markdown(
    "Os modelos rodam no microserviço R (plumber) via `POST /forecast`. "
    "O FastAPI orquestra a chamada e persiste os resultados no PostgreSQL."
)
st.divider()

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
col_scope, col_model, col_horizon, col_btn = st.columns([2, 2, 2, 1])

with col_scope:
    scope = st.selectbox("Recorte", ["brasil", "parana", "maringa"],
                         format_func=lambda x: {"brasil": "🇧🇷 Brasil",
                                                 "parana": "🌿 Paraná",
                                                 "maringa": "🏙️ Maringá"}[x])
with col_model:
    model = st.selectbox("Modelo", ["prophet", "arima", "holtwinters", "ensemble"],
                         format_func=lambda x: {
                             "prophet": "Prophet (Facebook)",
                             "arima": "ARIMA (Box-Jenkins)",
                             "holtwinters": "Holt-Winters",
                             "ensemble": "Ensemble (média)"
                         }[x])
with col_horizon:
    horizon = st.slider("Dias à frente", min_value=7, max_value=90, value=30, step=7)

with col_btn:
    st.markdown("<br>", unsafe_allow_html=True)
    run = st.button("▶ Executar", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Fetch and render
# ---------------------------------------------------------------------------
if run or True:  # auto-load on page open
    with st.spinner(f"Rodando modelo {model.upper()} no microserviço R..."):
        result = fetch_forecast(scope, model, horizon)

    if not result or not result.get("forecast"):
        st.warning("Sem dados de previsão. Verifique se o ETL foi executado.")
        st.stop()

    meta = result.get("meta", {})
    fdf  = pd.DataFrame(result["forecast"])
    fdf["date"] = pd.to_datetime(fdf["date"])

    # KPI row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Modelo", model.upper())
    m2.metric("Dias previstos", horizon)
    m3.metric("Pico previsto", f"{int(fdf['predicted'].max()):,}".replace(",", "."))
    m4.metric("Tempo (R)", f"{meta.get('elapsed_ms', '—')} ms",
              help="Tempo de execução no microserviço R plumber")

    st.divider()

    # Main forecast chart
    fig = go.Figure()

    # Confidence band (shaded area)
    fig.add_trace(go.Scatter(
        x=pd.concat([fdf["date"], fdf["date"].iloc[::-1]]),
        y=pd.concat([fdf["upper"], fdf["lower"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(59,130,246,0.15)",
        line=dict(color="rgba(255,255,255,0)"),
        name=f"IC {int(fdf['confidence_level'].iloc[0]*100)}%",
        hoverinfo="skip",
    ))

    # Point forecast line
    fig.add_trace(go.Scatter(
        x=fdf["date"], y=fdf["predicted"],
        name="Previsão",
        line=dict(color="#1d4ed8", width=2.5),
        mode="lines+markers",
        marker=dict(size=4),
        hovertemplate="%{x|%d/%m/%Y}<br>Previsto: %{y:,.0f}<extra></extra>",
    ))

    fig.update_layout(
        title=f"Previsão de novos casos — {scope.title()} · {model.upper()} · {horizon} dias",
        height=420, hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(title="Data", showgrid=False),
        yaxis=dict(title="Novos casos previstos", gridcolor="#f1f5f9"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Compare all models in one chart (fetch the others with demo data)
    st.markdown("### Comparação entre modelos")

    all_models = ["prophet", "arima", "holtwinters"]
    model_colors = {"prophet": "#1d4ed8", "arima": "#dc2626", "holtwinters": "#16a34a"}
    fig2 = go.Figure()
    for m in all_models:
        other = fetch_forecast(scope, m, horizon)
        if other and other.get("forecast"):
            odf = pd.DataFrame(other["forecast"])
            odf["date"] = pd.to_datetime(odf["date"])
            fig2.add_trace(go.Scatter(
                x=odf["date"], y=odf["predicted"],
                name=m.upper(),
                line=dict(color=model_colors.get(m, "#6b7280"), width=2,
                          dash="dot" if m != model else "solid"),
                hovertemplate="%{x|%d/%m/%Y}<br>" + m.upper() + ": %{y:,.0f}<extra></extra>",
            ))

    fig2.update_layout(
        title="Comparação: Prophet vs ARIMA vs Holt-Winters",
        height=300, hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(title="Data", showgrid=False),
        yaxis=dict(title="Novos casos previstos", gridcolor="#f1f5f9"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    st.plotly_chart(fig2, use_container_width=True)

    with st.expander("📋 Dados brutos da previsão"):
        st.dataframe(
            fdf.rename(columns={
                "date": "Data", "predicted": "Previsto",
                "lower": "Limite inf.", "upper": "Limite sup.",
                "model": "Modelo", "confidence_level": "IC"
            }),
            use_container_width=True,
            hide_index=True,
        )

    # Model explanation
    st.divider()
    explanations = {
        "prophet": (
            "**Prophet** (Facebook/Meta) é um modelo aditivo com componentes de "
            "tendência, sazonalidade semanal/anual e feriados brasileiros. "
            "Robusto a dados faltantes e quebras estruturais — ideal para os picos de COVID."
        ),
        "arima": (
            "**ARIMA** (Box-Jenkins) usa `auto.arima()` do pacote `forecast` para "
            "selecionar automaticamente os parâmetros (p, d, q) via AICc. "
            "Melhor para séries estacionárias nas fases de declínio da pandemia."
        ),
        "holtwinters": (
            "**Holt-Winters** (suavização exponencial) é o modelo mais simples dos três. "
            "Captura tendência + sazonalidade semanal. Serve como baseline de comparação."
        ),
        "ensemble": (
            "**Ensemble** calcula a média aritmética das previsões pontuais e dos "
            "intervalos de confiança de ARIMA + Prophet + Holt-Winters. "
            "Reduz a variância de qualquer modelo individual."
        ),
    }
    st.info(explanations.get(model, ""))
    st.caption(f"R version: {meta.get('r_version', '—')} · "
               f"Gerado em: {meta.get('generated_at', '—')} · "
               f"Cache: {'sim' if meta.get('cached') else 'não'}")
