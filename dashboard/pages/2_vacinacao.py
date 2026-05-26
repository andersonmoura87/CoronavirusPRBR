"""
Página: Vacinação
Doses aplicadas por mês (1ª, 2ª, reforço), acumulado e distribuição por fabricante.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from api_client import fetch_vaccination

st.set_page_config(page_title="Vacinação · Pandemic Platform", layout="wide")

st.title("💉 Vacinação COVID-19")
st.markdown("Fonte: OpenDataSUS — doses aplicadas por estado e tipo de dose.")
st.divider()

# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
with col1:
    state = st.selectbox("Estado", ["PR", "SP", "RJ", "MG", "RS"],
                         help="Filtra por estado (dados disponíveis conforme ETL executado)")
with col2:
    start = st.date_input("De", value=pd.Timestamp("2021-01-01"))
with col3:
    end = st.date_input("Até", value=pd.Timestamp("2023-12-31"))

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
with st.spinner("Carregando dados de vacinação..."):
    data = fetch_vaccination(state=state, start_date=str(start), end_date=str(end))

if not data or not data.get("data"):
    st.warning("Sem dados de vacinação para o filtro selecionado. "
               "Execute `python -m etl.ingest` primeiro.")
    st.stop()

vdf = pd.DataFrame(data["data"])
vdf["date"] = pd.to_datetime(vdf["date"])
vdf = vdf.sort_values("date")

# Monthly aggregate
vdf["month"] = vdf["date"].dt.to_period("M").dt.to_timestamp()
monthly = (
    vdf.groupby("month")
    .agg(dose_1=("dose_1", "sum"), dose_2=("dose_2", "sum"),
         dose_reforco=("dose_reforco", "sum"))
    .reset_index()
)
monthly["total"]  = monthly["dose_1"] + monthly["dose_2"] + monthly["dose_reforco"]
monthly["label"]  = monthly["month"].dt.strftime("%b/%y")

# KPIs
total_d1  = int(monthly["dose_1"].sum())
total_d2  = int(monthly["dose_2"].sum())
total_ref = int(monthly["dose_reforco"].sum())
total     = total_d1 + total_d2 + total_ref
peak_month_label = monthly.loc[monthly["total"].idxmax(), "label"]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total de doses",  f"{total:,}".replace(",", "."))
c2.metric("1ª dose",         f"{total_d1:,}".replace(",", "."))
c3.metric("2ª dose",         f"{total_d2:,}".replace(",", "."))
c4.metric("Reforço",         f"{total_ref:,}".replace(",", "."))
c5.metric("Mês de pico",     peak_month_label)
st.divider()

# ---------------------------------------------------------------------------
# Stacked bar — monthly doses by type
# ---------------------------------------------------------------------------
st.markdown("### Doses mensais por tipo")
st.caption(f"Estado: {state} · dados mensais agregados — barras empilhadas mostram composição por dose")

fig = go.Figure()
fig.add_trace(go.Bar(
    x=monthly["label"], y=monthly["dose_1"],
    name="1ª dose",
    marker_color="#86efac",
    hovertemplate="%{x}<br>1ª dose: %{y:,.0f}<extra></extra>",
))
fig.add_trace(go.Bar(
    x=monthly["label"], y=monthly["dose_2"],
    name="2ª dose",
    marker_color="#22c55e",
    hovertemplate="%{x}<br>2ª dose: %{y:,.0f}<extra></extra>",
))
fig.add_trace(go.Bar(
    x=monthly["label"], y=monthly["dose_reforco"],
    name="Reforço",
    marker_color="#15803d",
    hovertemplate="%{x}<br>Reforço: %{y:,.0f}<extra></extra>",
))

# Annotate peak month
peak_idx = monthly["total"].idxmax()
fig.add_annotation(
    x=monthly.loc[peak_idx, "label"],
    y=monthly.loc[peak_idx, "total"],
    text=f"Pico: {int(monthly.loc[peak_idx, 'total']):,}".replace(",", "."),
    showarrow=True, arrowhead=2, arrowcolor="#166534",
    font=dict(size=11, color="#166534"),
    bgcolor="white", bordercolor="#166534", borderwidth=1,
    ay=-40,
)

fig.update_layout(
    barmode="stack", height=380,
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    plot_bgcolor="white", paper_bgcolor="white",
    xaxis=dict(showgrid=False, title="Mês",
               tickangle=-45, tickfont=dict(size=10)),
    yaxis=dict(title="Doses aplicadas / mês", gridcolor="#f1f5f9"),
    hovermode="x unified",
    margin=dict(l=0, r=0, t=30, b=60),
)
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Cumulative + monthly total — dual view side by side
# ---------------------------------------------------------------------------
st.markdown("### Acumulado vs volume mensal")
col_cum, col_share = st.columns([3, 2], gap="large")

with col_cum:
    # Cumulative area chart
    cum = monthly.copy()
    for c in ("dose_1", "dose_2", "dose_reforco"):
        cum[f"{c}_cum"] = cum[c].cumsum()

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=cum["label"], y=cum["dose_1_cum"],
        name="1ª dose", fill="tozeroy",
        fillcolor="rgba(134,239,172,0.3)",
        line=dict(color="#86efac", width=2),
        hovertemplate="%{x}<br>1ª dose acum.: %{y:,.0f}<extra></extra>",
    ))
    fig2.add_trace(go.Scatter(
        x=cum["label"], y=cum["dose_2_cum"],
        name="2ª dose", fill="tozeroy",
        fillcolor="rgba(34,197,94,0.3)",
        line=dict(color="#22c55e", width=2),
        hovertemplate="%{x}<br>2ª dose acum.: %{y:,.0f}<extra></extra>",
    ))
    fig2.add_trace(go.Scatter(
        x=cum["label"], y=cum["dose_reforco_cum"],
        name="Reforço", fill="tozeroy",
        fillcolor="rgba(21,128,61,0.3)",
        line=dict(color="#15803d", width=2),
        hovertemplate="%{x}<br>Reforço acum.: %{y:,.0f}<extra></extra>",
    ))
    fig2.update_layout(
        height=300, hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, title="Mês",
                   tickangle=-45, tickfont=dict(size=10)),
        yaxis=dict(title="Doses acumuladas", gridcolor="#f1f5f9"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=0, r=0, t=20, b=60),
    )
    st.plotly_chart(fig2, use_container_width=True)

with col_share:
    # Donut: total doses composition
    total_doses = [total_d1, total_d2, total_ref]
    labels       = ["1ª dose", "2ª dose", "Reforço"]
    colors_pie   = ["#86efac", "#22c55e", "#15803d"]

    fig3 = go.Figure(go.Pie(
        labels=labels, values=total_doses,
        marker=dict(colors=colors_pie),
        hole=0.55,
        hovertemplate="%{label}<br>%{value:,.0f} doses (%{percent})<extra></extra>",
        textinfo="percent+label",
        textfont=dict(size=12),
    ))
    fig3.update_layout(
        title=dict(text="Composição total", font=dict(size=13)),
        height=300,
        showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig3, use_container_width=True)
