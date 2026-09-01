import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Fantacalcio AI",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# STILE
# --------------------------------------------------------------------------
st.markdown("""
<style>
.main-title {
    font-size: 40px; font-weight: 800;
    background: linear-gradient(90deg, #10b981, #3b82f6);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 0px;
}
.subtitle { color: #9ca3af; font-size: 15px; margin-top: 0px; }
[data-testid="stMetric"] {
    background-color: rgba(59,130,246,0.08);
    border: 1px solid rgba(59,130,246,0.2);
    border-radius: 12px; padding: 12px 16px;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-title">⚽ Fantacalcio AI</p>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Motore di valutazione giocatori — Fantacalcio classico + Modificatore Difesa</p>', unsafe_allow_html=True)
st.write("")

# --------------------------------------------------------------------------
# CARICAMENTO DATI
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("📂 Dati")
    uploaded = st.file_uploader(
        "Carica il CSV aggiornato (esportato dal notebook Colab)",
        type=["csv"],
        help="Se non carichi nulla, viene mostrato l'ultimo dato disponibile nell'app.",
    )
    st.caption("Aggiorna qui ogni volta che rilanci la pipeline dati — non serve toccare GitHub.")

@st.cache_data
def load_default():
    return pd.read_csv("results_sample.csv")

if uploaded is not None:
    df = pd.read_csv(uploaded)
    st.sidebar.success(f"✅ {len(df)} giocatori caricati dal tuo file")
else:
    df = load_default()
    st.sidebar.info("ℹ️ Stai vedendo i dati di esempio inclusi nell'app — carica il tuo CSV per i dati reali.")

df.columns = [c.strip() for c in df.columns]

# --------------------------------------------------------------------------
# FILTRI
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("🔍 Filtri")
    ruoli_disponibili = sorted(df["Ruolo"].dropna().unique().tolist())
    ruolo_sel = st.multiselect("Ruolo", ruoli_disponibili, default=ruoli_disponibili)

    squadre_disponibili = sorted(df["Squadra"].dropna().unique().tolist())
    squadra_sel = st.multiselect("Squadra", squadre_disponibili, default=[])

    prezzo_max = int(df["Prezzo"].max()) if "Prezzo" in df.columns else 500
    prezzo_range = st.slider("Fascia prezzo (crediti)", 0, prezzo_max, (0, prezzo_max))

    ricerca = st.text_input("Cerca giocatore")

    ordina_per = st.selectbox(
        "Ordina per",
        ["Valore_per_credito", "Valore_stagionale", "Pt_giornata", "Prezzo", "FVM"],
        index=0,
    )
    top_n = st.slider("Quanti mostrarne", 5, 200, 30)

# applica filtri
f = df.copy()
if ruolo_sel:
    f = f[f["Ruolo"].isin(ruolo_sel)]
if squadra_sel:
    f = f[f["Squadra"].isin(squadra_sel)]
f = f[(f["Prezzo"] >= prezzo_range[0]) & (f["Prezzo"] <= prezzo_range[1])]
if ricerca:
    f = f[f["Nome"].str.contains(ricerca, case=False, na=False)]
f = f.sort_values(ordina_per, ascending=False).head(top_n)

# --------------------------------------------------------------------------
# METRICHE RIASSUNTIVE
# --------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Giocatori mostrati", len(f))
c2.metric("Prezzo medio", f"{f['Prezzo'].mean():.0f}" if len(f) else "-")
c3.metric("Miglior valore/credito", f"{f['Valore_per_credito'].max():.2f}" if len(f) else "-")
c4.metric("Top pick", f.iloc[0]["Nome"] if len(f) else "-")

st.write("")

# --------------------------------------------------------------------------
# TABELLA PRINCIPALE
# --------------------------------------------------------------------------
tab1, tab2 = st.tabs(["📋 Classifica", "📈 Prezzo vs Valore atteso"])

with tab1:
    st.dataframe(
        f[["Nome", "Ruolo", "Squadra", "Prezzo", "FVM", "Pt_giornata", "Valore_stagionale", "Valore_per_credito"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Prezzo": st.column_config.NumberColumn("Prezzo", format="%d"),
            "FVM": st.column_config.NumberColumn("FVM", format="%d"),
            "Pt_giornata": st.column_config.NumberColumn("Pt/giornata", format="%.2f"),
            "Valore_stagionale": st.column_config.NumberColumn("Valore stagionale", format="%.1f"),
            "Valore_per_credito": st.column_config.ProgressColumn(
                "Valore/credito",
                format="%.2f",
                min_value=0,
                max_value=float(df["Valore_per_credito"].max()) if len(df) else 1,
            ),
        },
    )

with tab2:
    st.caption("I giocatori sopra la diagonale rendono più di quanto costano — sono le occasioni da cercare.")
    chart_df = f[["Nome", "Prezzo", "Valore_stagionale", "Ruolo"]].rename(
        columns={"Prezzo": "Prezzo (crediti)", "Valore_stagionale": "Valore stagionale atteso"}
    )
    st.scatter_chart(chart_df, x="Prezzo (crediti)", y="Valore stagionale atteso", color="Ruolo")

st.caption("I dati mostrati sono generati dalla pipeline statistica collegata a Understat + le regole della tua lega. Carica un CSV nuovo dalla barra laterale per aggiornarli.")
