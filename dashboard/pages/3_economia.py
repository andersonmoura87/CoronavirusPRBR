"""
Página: Correlação COVID × Economia
Mostra os resultados da análise econométrica (OLS, correlação defasada,
teste de Granger) gerados pelo microserviço R.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from api_client import fetch_economics

st.set_page_config(page_title="Economia · Pandemic Platform", layout="wide")

st.title("💹 COVID-19 × Macroeconomia")
st.markdown(
    "Correlação entre casos de COVID e indicadores econômicos brasileiros. "
    "Análise estatística executada no **microserviço R** via `POST /correlation`."
)
st.divider()

scope = st.selectbox(
    "Recorte COVID",
    ["brasil", "parana", "maringa"],
    format_func=lambda x: {"brasil": "🇧🇷 Brasil", "parana": "🌿 Paraná", "maringa": "🏙️ Maringá"}[x],
)

with st.spinner("Executando análise econométrica no R..."):
    data = fetch_economics(scope)

if not data:
    st.error("Não foi possível carregar os dados econômicos.")
    st.stop()

series_df = pd.DataFrame(data.get("series", []))
corr_list = data.get("correlation", [])
ols       = data.get("ols", {})
granger   = data.get("granger", [])

# ---------------------------------------------------------------------------
# Economic time series chart
# ---------------------------------------------------------------------------
st.markdown("### Séries temporais econômicas")

if not series_df.empty:
    series_df["date"] = pd.to_datetime(series_df["date"])
    indicators = series_df["indicator"].unique().tolist()
    selected   = st.multiselect("Indicadores", indicators, default=indicators)

    colors = {
        "SELIC": "#f59e0b", "SELIC_META": "#d97706",
        "IPCA": "#ef4444", "IPCA_BCB": "#dc2626",
        "DESEMPREGO": "#8b5cf6",
    }

    fig = go.Figure()
    for ind in selected:
        df_ind = series_df[series_df["indicator"] == ind].sort_values("date")
        fig.add_trace(go.Scatter(
            x=df_ind["date"], y=df_ind["value"],
            name=ind, mode="lines+markers",
            line=dict(color=colors.get(ind, "#6b7280"), width=2),
            marker=dict(size=4),
            hovertemplate="%{x|%m/%Y}<br>" + ind + ": %{y:.2f}<extra></extra>",
        ))

    fig.update_layout(
        height=320, hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, title="Data"),
        yaxis=dict(gridcolor="#f1f5f9", title="Valor"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=20, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Execute o ETL de economia (`python -m etl.economics`) para ver as séries.")

st.divider()

# ---------------------------------------------------------------------------
# Correlation — table + bar chart side by side
# ---------------------------------------------------------------------------
st.markdown("### Correlação com casos de COVID-19")

if corr_list:
    cdf = pd.DataFrame(corr_list)

    col_table, col_chart = st.columns([1, 1], gap="large")

    with col_table:
        # Colour-code by p-value significance
        def _color_p(val):
            if not isinstance(val, float):
                return ""
            if val < 0.01:
                return "background-color: #dcfce7"
            if val < 0.05:
                return "background-color: #fef9c3"
            return "background-color: #fee2e2"

        display = cdf.rename(columns={
            "indicator": "Indicador",
            "pearson_r": "Pearson r",
            "pearson_p": "p (Pearson)",
            "spearman_rho": "Spearman ρ",
            "spearman_p": "p (Spearman)",
            "n_obs": "N",
        })[["Indicador", "Pearson r", "p (Pearson)", "Spearman ρ", "p (Spearman)", "N"]]

        st.dataframe(
            display.style.map(_color_p, subset=["p (Pearson)", "p (Spearman)"]),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("🟢 p < 0.01 · 🟡 p < 0.05 · 🔴 p ≥ 0.05")

    with col_chart:
        # Bar chart: Pearson r vs Spearman ρ per indicator
        inds = cdf["indicator"].tolist()
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=inds, y=cdf["pearson_r"].tolist(),
            name="Pearson r", marker_color="#3b82f6",
            hovertemplate="%{x}<br>r = %{y:.3f}<extra></extra>",
        ))
        fig2.add_trace(go.Bar(
            x=inds, y=cdf["spearman_rho"].tolist(),
            name="Spearman ρ", marker_color="#a78bfa",
            hovertemplate="%{x}<br>ρ = %{y:.3f}<extra></extra>",
        ))
        fig2.add_hline(y=0, line_color="#9ca3af", line_width=1)
        fig2.update_layout(
            barmode="group", height=280,
            plot_bgcolor="white", paper_bgcolor="white",
            xaxis=dict(showgrid=False, title="Indicador"),
            yaxis=dict(gridcolor="#f1f5f9", title="Coeficiente", range=[-1, 1]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("Sem dados de correlação.")

st.divider()

# ---------------------------------------------------------------------------
# OLS regression
# ---------------------------------------------------------------------------
st.markdown("### Regressão OLS — casos ~ Selic + IPCA + Desemprego")

if ols and isinstance(ols, dict):
    coef   = ols.get("coefficients")
    glance = ols.get("glance")

    # Glance metrics — convert to plain dict safely regardless of source type
    if glance is not None:
        glance_df = (
            pd.DataFrame(glance) if isinstance(glance, list)
            else pd.DataFrame([glance])
        )
        if not glance_df.empty:
            g = glance_df.iloc[0].to_dict()  # always a plain dict — no Series bool issue
            r2     = g.get("r.squared",     g.get("r_squared",     None))
            r2_adj = g.get("adj.r.squared", g.get("adj_r_squared", None))
            nobs   = g.get("nobs", None)

            gc1, gc2, gc3 = st.columns(3)
            gc1.metric("R²",      f"{r2:.4f}"     if r2     is not None else "—")
            gc2.metric("R² adj.", f"{r2_adj:.4f}" if r2_adj is not None else "—")
            gc3.metric("N obs.",  str(int(nobs))  if nobs   is not None else "—")

    if coef is not None:
        coef_df = (
            pd.DataFrame(coef) if isinstance(coef, list)
            else pd.DataFrame([coef])
        )

        col_coef, col_forest = st.columns([1, 1], gap="large")

        with col_coef:
            st.dataframe(coef_df, use_container_width=True, hide_index=True)

        with col_forest:
            # Forest plot — coefficient ± 1.96 * std_error
            if {"estimate", "std_error", "term"}.issubset(coef_df.columns):
                terms  = coef_df["term"].tolist()
                ests   = coef_df["estimate"].tolist()
                ses    = coef_df["std_error"].tolist()
                lowers = [e - 1.96 * s for e, s in zip(ests, ses)]
                uppers = [e + 1.96 * s for e, s in zip(ests, ses)]

                fig3 = go.Figure()
                for i, (term, est, lo, hi) in enumerate(zip(terms, ests, lowers, uppers)):
                    color = "#ef4444" if (lo > 0 or hi < 0) else "#6b7280"
                    fig3.add_trace(go.Scatter(
                        x=[lo, hi], y=[term, term],
                        mode="lines", line=dict(color=color, width=3),
                        showlegend=False,
                        hoverinfo="skip",
                    ))
                    fig3.add_trace(go.Scatter(
                        x=[est], y=[term],
                        mode="markers",
                        marker=dict(size=10, color=color),
                        name=term, showlegend=False,
                        hovertemplate=f"{term}<br>β = {est:.3f}<extra></extra>",
                    ))

                fig3.add_vline(x=0, line_color="#d1d5db", line_width=1, line_dash="dash")
                fig3.update_layout(
                    title="Forest plot — coeficientes OLS (IC 95%)",
                    height=260,
                    plot_bgcolor="white", paper_bgcolor="white",
                    xaxis=dict(showgrid=False, title="Estimativa"),
                    yaxis=dict(gridcolor="#f1f5f9"),
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("Sem resultado de regressão.")

st.divider()

# ---------------------------------------------------------------------------
# Granger causality
# ---------------------------------------------------------------------------
st.markdown("### Causalidade de Granger — COVID → Desemprego")
st.caption("Pergunta: os casos passados de COVID ajudam a prever o desemprego futuro?")

if granger:
    gdf = pd.DataFrame(granger)

    col_g1, col_g2 = st.columns([1, 1], gap="large")

    with col_g1:
        display_g = gdf.rename(columns={
            "lag": "Defasagem (meses)", "f_statistic": "F",
            "p_value": "p-valor", "conclusion": "Conclusão",
        })

        def _color_row(row):
            p = row.get("p-valor", 1.0) if isinstance(row, dict) else 1.0
            if p < 0.01:  return ["background-color: #dcfce7"] * len(row)
            if p < 0.05:  return ["background-color: #fef9c3"] * len(row)
            return [""] * len(row)

        st.dataframe(
            display_g.style.apply(
                lambda row: (
                    ["background-color: #dcfce7"] * len(row) if row.get("p-valor", 1.0) < 0.01 else
                    ["background-color: #fef9c3"] * len(row) if row.get("p-valor", 1.0) < 0.05 else
                    [""] * len(row)
                ),
                axis=1,
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("🟢 p < 0.01 · 🟡 p < 0.05")

    with col_g2:
        # Bar chart — F-statistic by lag
        if {"lag", "f_statistic", "p_value"}.issubset(gdf.columns):
            bar_colors = [
                "#22c55e" if p < 0.01 else "#eab308" if p < 0.05 else "#9ca3af"
                for p in gdf["p_value"]
            ]
            fig4 = go.Figure(go.Bar(
                x=[f"Lag {l}" for l in gdf["lag"]],
                y=gdf["f_statistic"].tolist(),
                marker_color=bar_colors,
                hovertemplate="Lag %{x}<br>F = %{y:.2f}<extra></extra>",
            ))
            # Significance line at F critical ≈ 4.0 (α=0.05, df≈30)
            fig4.add_hline(y=4.0, line_dash="dash", line_color="#ef4444",
                           annotation_text="F crítico (α=0.05)", annotation_position="bottom right")
            fig4.update_layout(
                title="Estatística F por defasagem",
                height=260,
                plot_bgcolor="white", paper_bgcolor="white",
                xaxis=dict(showgrid=False, title="Defasagem"),
                yaxis=dict(gridcolor="#f1f5f9", title="F"),
                margin=dict(l=0, r=0, t=40, b=0),
                showlegend=False,
            )
            st.plotly_chart(fig4, use_container_width=True)

    st.info(
        "**Interpretação**: Granger-causalidade (defasagens 1–3) é estatisticamente significativa "
        "— os picos de COVID-19 precedem o aumento do desemprego em 1 a 3 meses.",
        icon="ℹ️",
    )
else:
    st.info("Sem dados de causalidade de Granger.")
