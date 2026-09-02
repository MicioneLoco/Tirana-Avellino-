import streamlit as st
import pandas as pd
import json

@st.cache_data
def load_compatibilita():
    c = pd.read_csv("team_compatibility.csv", index_col="Squadra")
    return c

@st.cache_data
def load_strategie():
    with open("budget_strategies.json", encoding="utf-8") as fh:
        return json.load(fh)

MAPPA_RUOLO_JSON = {"P": "POR", "D": "DIF", "C": "CEN", "A": "ATT"}

TEAM_CODE = {
    "Atalanta": "ATA", "Bologna": "BOL", "Cagliari": "CAG", "Como": "COM",
    "Fiorentina": "FIO", "Frosinone": "FRO", "Genoa": "GEN", "Inter": "INT",
    "Juventus": "JUV", "Lazio": "LAZ", "Lecce": "LEC", "Milan": "MIL",
    "Monza": "MON", "Napoli": "NAP", "Parma": "PAR", "Roma": "ROM",
    "Sassuolo": "SAS", "Torino": "TOR", "Udinese": "UDI", "Venezia": "VEN",
}

def compatibilita_media(squadra_giocatore, squadre_possedute, matrice):
    codice = TEAM_CODE.get(squadra_giocatore)
    if codice is None or codice not in matrice.index or not squadre_possedute:
        return None
    codici_posseduti = [TEAM_CODE.get(s) for s in squadre_possedute if TEAM_CODE.get(s)]
    codici_posseduti = [c for c in codici_posseduti if c in matrice.columns and c != codice]
    if not codici_posseduti:
        return None
    return round(matrice.loc[codice, codici_posseduti].astype(float).mean(), 1)

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
# STATO ASTA (persiste durante la sessione)
# --------------------------------------------------------------------------
if "picks" not in st.session_state:
    st.session_state.picks = []  # ogni pick: {nome, ruolo, squadra, prezzo, chi}

with st.sidebar:
    st.header("⚙️ Regole rosa")
    budget_totale = st.number_input("Crediti totali", value=500, step=10)
    n_portieri = st.number_input("Portieri", value=3, min_value=1, step=1)
    n_difensori = st.number_input("Difensori", value=8, min_value=1, step=1)
    n_centrocampisti = st.number_input("Centrocampisti", value=8, min_value=1, step=1)
    n_attaccanti = st.number_input("Attaccanti", value=6, min_value=1, step=1)
    SLOT = {"P": n_portieri, "D": n_difensori, "C": n_centrocampisti, "A": n_attaccanti}

    st.header("🧠 Strategia budget")
    strategie = load_strategie()
    nome_strategia = st.selectbox("Che tattica stai seguendo?", list(strategie.keys()), index=1)
    strategia_attiva = strategie[nome_strategia]
    st.caption("Definisce quanto puntare sul prossimo slot di ogni ruolo, presa dal tuo Excel di strategie.")

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
    top_n = st.slider("Quanti mostrarne", 5, 600, 30)

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
tab1, tab2, tab3, tab4 = st.tabs(["📋 Classifica", "📈 Prezzo vs Valore atteso", "🎯 Assistente Asta", "🤝 Abbinamenti"])

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

with tab3:
    st.caption("Segna chi prende chi durante l'asta — l'app ricalcola budget, slot rimasti, e ti aggiorna i migliori target ancora disponibili.")

    colA, colB = st.columns([1, 1])

    # ---------------------------------------------------------------
    # FORM: REGISTRA UN GIOCATORE PRESO
    # ---------------------------------------------------------------
    with colA:
        st.subheader("➕ Registra un giocatore preso")
        gia_presi = {p["Nome"] for p in st.session_state.picks}
        disponibili = df[~df["Nome"].isin(gia_presi)].sort_values("Nome")

        with st.form("form_pick", clear_on_submit=True):
            nome_scelto = st.selectbox("Giocatore", disponibili["Nome"].tolist())
            prezzo_pagato = st.number_input("Prezzo pagato (crediti)", min_value=1, value=1, step=1)
            chi = st.radio("Chi lo ha preso?", ["Io", "Un'altra squadra"], horizontal=True)
            submitted = st.form_submit_button("Registra")

            if submitted:
                riga = df[df["Nome"] == nome_scelto].iloc[0]
                st.session_state.picks.append({
                    "Nome": nome_scelto, "Ruolo": riga["Ruolo"], "Squadra": riga["Squadra"],
                    "Prezzo": prezzo_pagato, "Chi": chi,
                })
                st.rerun()

        c_reset, c_undo = st.columns(2)
        if c_undo.button("↩️ Annulla ultimo") and st.session_state.picks:
            st.session_state.picks.pop()
            st.rerun()
        if c_reset.button("🗑️ Azzera tutto"):
            st.session_state.picks = []
            st.rerun()

        st.write("")
        st.subheader("💾 Salva / recupera l'asta")
        st.caption("Se ricarichi la pagina perdi lo stato — scarica il progresso e ricaricalo per riprendere.")
        import json
        stato_json = json.dumps(st.session_state.picks, ensure_ascii=False, indent=2)
        st.download_button("⬇️ Scarica progresso asta", stato_json, file_name="asta_in_corso.json", mime="application/json")
        stato_caricato = st.file_uploader("⬆️ Ricarica un'asta salvata", type=["json"], key="carica_stato")
        if stato_caricato is not None:
            st.session_state.picks = json.load(stato_caricato)
            st.rerun()

    # ---------------------------------------------------------------
    # CALCOLO STATO ASTA
    # ---------------------------------------------------------------
    miei = [p for p in st.session_state.picks if p["Chi"] == "Io"]
    tutti_presi = {p["Nome"] for p in st.session_state.picks}

    speso = sum(p["Prezzo"] for p in miei)
    budget_rimanente = budget_totale - speso

    presi_per_ruolo = {r: sum(1 for p in miei if p["Ruolo"] == r) for r in SLOT}
    slot_rimanenti = {r: max(SLOT[r] - presi_per_ruolo[r], 0) for r in SLOT}
    slot_rimanenti_totali = sum(slot_rimanenti.values())

    # riserva minima: 1 credito per ogni slot ancora da riempire (tranne quello che stai per comprare)
    with colB:
        st.subheader("📊 Situazione attuale")
        m1, m2, m3 = st.columns(3)
        m1.metric("Budget rimanente", f"{budget_rimanente} cr.")
        m2.metric("Slot da riempire", slot_rimanenti_totali)
        m3.metric("Giocatori tuoi", len(miei))

        st.write("**Slot rimanenti per ruolo:**")
        st.write(" · ".join(f"{r}: {slot_rimanenti[r]}" for r in ["P", "D", "C", "A"]))

        if slot_rimanenti_totali > 0:
            riserva_minima = max(slot_rimanenti_totali - 1, 0) * 1
            budget_max_ora = max(budget_rimanente - riserva_minima, 0)
            st.metric("💰 Budget massimo spendibile ORA su un giocatore", f"{budget_max_ora} cr.",
                       help="Budget rimanente meno una riserva minima di 1 credito per ogni altro slot ancora da riempire.")

        if miei:
            st.write("**La tua rosa finora:**")
            st.dataframe(pd.DataFrame(miei)[["Nome", "Ruolo", "Squadra", "Prezzo"]], hide_index=True, use_container_width=True)

    st.divider()

    # ---------------------------------------------------------------
    # TARGET SUGGERITI (dinamico, in base a cosa manca)
    # ---------------------------------------------------------------
    st.subheader("🎯 Migliori target ancora disponibili")

    ruoli_da_coprire = [r for r in ["P", "D", "C", "A"] if slot_rimanenti[r] > 0]
    if not ruoli_da_coprire:
        st.success("Rosa completa su tutti i ruoli! 🎉")
    else:
        ruolo_focus = st.selectbox("Mostra target per ruolo", ruoli_da_coprire)

        # target della strategia per il PROSSIMO slot di questo ruolo
        chiave_json = MAPPA_RUOLO_JSON[ruolo_focus]
        slot_gia_presi_ruolo = presi_per_ruolo[ruolo_focus]
        lista_slot_strategia = strategia_attiva.get(chiave_json, [])
        target_strategia = None
        if slot_gia_presi_ruolo < len(lista_slot_strategia):
            target_strategia = lista_slot_strategia[slot_gia_presi_ruolo]

        if target_strategia:
            st.info(f"📌 Secondo la strategia **{nome_strategia}**, per il tuo prossimo {ruolo_focus} "
                    f"({target_strategia['etichetta']}) punta a circa **{target_strategia['target_crediti']:.0f} crediti**.")

        squadre_possedute = [p["Squadra"] for p in miei]
        matrice_compat = load_compatibilita()

        pool = df[(~df["Nome"].isin(tutti_presi)) & (df["Ruolo"] == ruolo_focus)].copy()
        pool["Compatibilità con la rosa"] = pool["Squadra"].apply(
            lambda sq: compatibilita_media(sq, squadre_possedute, matrice_compat)
        )
        pool = pool.sort_values("Valore_per_credito", ascending=False).head(15)
        if slot_rimanenti_totali > 0:
            pool["Budget max consigliato"] = pool["Prezzo"].apply(lambda p: min(p, budget_max_ora))

        colonne = ["Nome", "Squadra", "Prezzo", "Valore_stagionale", "Valore_per_credito", "Compatibilità con la rosa"]
        if slot_rimanenti_totali > 0:
            colonne.append("Budget max consigliato")

        st.dataframe(
            pool[colonne], hide_index=True, use_container_width=True,
            column_config={
                "Compatibilità con la rosa": st.column_config.NumberColumn(
                    "Compatibilità con la rosa", format="%.0f%%",
                    help="Media di quanto le squadre dei tuoi giocatori già presi si abbinano bene (calendari/turni) con la squadra di questo giocatore. Più alto = meglio.",
                ),
            },
        )
        if squadre_possedute:
            st.caption("Compatibilità calcolata sulla tua matrice reale (foglio 'Abb. ATT'). Vuota se non hai ancora preso nessuno di quel ruolo/incrocio.")
        else:
            st.caption("La compatibilità comparirà appena avrai in rosa almeno un giocatore.")

with tab4:
    st.caption("Compatibilità tra squadre (calendario/turni) — dalla tua matrice reale. Più alto = si abbinano meglio (utile per staffette portiere, coperture, o evitare due giocatori sempre 'spenti' nella stessa giornata).")
    matrice_compat = load_compatibilita()

    squadra_riferimento = st.selectbox("Parti da una squadra", sorted(matrice_compat.index.tolist()))
    riga = matrice_compat.loc[squadra_riferimento].drop(squadra_riferimento).astype(float).sort_values(ascending=False)
    top_compat = riga.head(8).reset_index()
    top_compat.columns = ["Squadra", "Compatibilità"]

    codice_to_nome = {v: k for k, v in TEAM_CODE.items()}
    top_compat["Squadra"] = top_compat["Squadra"].map(codice_to_nome).fillna(top_compat["Squadra"])

    st.write(f"**Squadre più compatibili con {codice_to_nome.get(squadra_riferimento, squadra_riferimento)}:**")
    st.dataframe(
        top_compat, hide_index=True, use_container_width=True,
        column_config={"Compatibilità": st.column_config.ProgressColumn("Compatibilità", format="%.0f%%", min_value=60, max_value=100)},
    )

    with st.expander("📊 Vedi la matrice completa"):
        matrice_display = matrice_compat.rename(index=codice_to_nome, columns=codice_to_nome)
        st.dataframe(matrice_display, use_container_width=True)

st.caption("I dati mostrati sono generati dalla pipeline statistica collegata a Understat + le regole della tua lega. Carica un CSV nuovo dalla barra laterale per aggiornarli.")
