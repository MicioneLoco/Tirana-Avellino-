"""
Pipeline v3: motore di proiezione con forma recente pesata, difficoltà
avversario, indicatore di affidabilità e gestione indisponibili.

Nota onesta sul "voto reale": Fantacalcio.it non espone un'API pubblica
stabile, e le librerie reverse-engineered che la usano sono fragili (possono
rompersi ad ogni redesign del sito, e potrebbero avere le stesse protezioni
anti-bot di FBref). Non la colleghiamo qui per non rischiare di rompere
l'automazione che già funziona. Il voto resta una stima calibrata dalla
produttività reale (xG/xA) — meno preciso di un voto vero, ma stabile.
"""

import json

import pandas as pd
from unidecode import unidecode
from difflib import get_close_matches
import soccerdata as sd

LISTONE_PATH = "listone.xlsx"
OUTPUT_PATH = "results_sample.csv"
INDISPONIBILI_PATH = "indisponibili.json"
GIORNATE_RIMANENTI = 34
GIORNATE_RECENTI_PER_FORMA = 5   # quante ultime giornate pesano di più
PESO_FORMA_RECENTE = 0.6         # 60% forma recente, 40% media stagionale

BONUS_MALUS = {
    "gol": 3, "assist": 1,
    "rigore_segnato": 3, "rigore_sbagliato": -3,
    "ammonizione": -0.5, "espulsione": -1,
}

MODIFICATORE_DIFESA_SCALE = [
    (6.00, 0.0), (6.25, 1.0), (6.50, 1.5), (6.75, 2.0),
    (7.00, 3.0), (7.25, 4.0), (7.50, 5.0), (float("inf"), 6.0),
]

def modificatore_difesa(media):
    if media is None or media < 6.0:
        return 0.0
    for soglia, bonus in MODIFICATORE_DIFESA_SCALE:
        if media < soglia:
            return bonus
    return MODIFICATORE_DIFESA_SCALE[-1][1]

RIGORISTI = {
    "Atalanta": ["Scamacca", "De Ketelaere", "Samardzic"],
    "Bologna": ["Orsolini", "Dovbyk", "Bernardeschi"],
    "Cagliari": ["Fazzini", "Mina", "Deiola"],
    "Como": ["Da Cunha", "Paz", "Douvikas"],
    "Fiorentina": ["Gudmundsson", "Mandragora", "Kean"],
    "Frosinone": ["Calò", "Raimondo"],
    "Genoa": ["Colombo", "Messias", "Vitinha"],
    "Inter": ["Calhanoglu", "Martinez L.", "Zielinski"],
    "Juventus": ["Yildiz", "Locatelli", "Kolo Muani"],
    "Lazio": ["Zaccagni", "Cataldi", "Taylor"],
    "Lecce": ["Geubbels", "Stulic"],
    "Milan": ["Gonçalo Ramos", "Pulisic"],
    "Monza": ["Pessina", "Cutrone", "Petagna"],
    "Napoli": ["De Bruyne", "Hojlund"],
    "Parma": ["Touré", "Bernabé"],
    "Roma": ["Malen", "Dybala", "Soulé"],
    "Sassuolo": ["Berardi", "Pinamonti"],
    "Torino": ["Vlasic", "Zapata", "Simeone"],
    "Udinese": ["Davis", "Solet", "Zaniolo"],
    "Venezia": ["Adams", "Rrahmani"],
}

def is_rigorista(nome, squadra):
    return nome in RIGORISTI.get(squadra, [])


def norm(s):
    return unidecode(str(s)).lower().strip()


def carica_indisponibili():
    try:
        with open(INDISPONIBILI_PATH, encoding="utf-8") as f:
            lista = json.load(f)
        return {(norm(x["nome"]), x.get("squadra", "")): x for x in lista}
    except FileNotFoundError:
        return {}


def calcola_forza_squadre_e_prossimo_avversario(schedule: pd.DataFrame):
    schedule = schedule.copy()
    giocate = schedule[schedule["is_result"] == True]  # noqa: E712

    righe = []
    for _, m in giocate.iterrows():
        righe.append({"team": m["home_team"], "xg_for": m["home_xg"], "xg_against": m["away_xg"]})
        righe.append({"team": m["away_team"], "xg_for": m["away_xg"], "xg_against": m["home_xg"]})
    forza = pd.DataFrame(righe).groupby("team").mean(numeric_only=True) if righe else pd.DataFrame(columns=["xg_for", "xg_against"])

    media_lega_for = forza["xg_for"].mean() if len(forza) else 1.3
    media_lega_against = forza["xg_against"].mean() if len(forza) else 1.3

    da_giocare = schedule[schedule["is_result"] == False].sort_values("date")  # noqa: E712
    prossimo = {}
    for _, m in da_giocare.iterrows():
        if m["home_team"] not in prossimo:
            prossimo[m["home_team"]] = m["away_team"]
        if m["away_team"] not in prossimo:
            prossimo[m["away_team"]] = m["home_team"]

    return forza, media_lega_for, media_lega_against, prossimo


def difficolta_per_ruolo(squadra, prossimo, forza, media_for, media_against, ruolo):
    """Ritorna un moltiplicatore ~1.0 (neutro), >1 partita favorevole, <1 sfavorevole."""
    avversario = prossimo.get(squadra)
    if avversario is None or avversario not in forza.index:
        return 1.0, avversario

    xg_against_avversario = forza.loc[avversario, "xg_against"]
    xg_for_avversario = forza.loc[avversario, "xg_for"]

    if ruolo in ("C", "A"):
        mult = xg_against_avversario / media_against if media_against else 1.0
    else:
        mult = media_for / xg_for_avversario if xg_for_avversario else 1.0

    return max(0.6, min(mult, 1.5)), avversario


def calcola_forma_recente(understat, schedule):
    giocate = schedule[schedule["is_result"] == True].sort_values("date")  # noqa: E712
    if giocate.empty:
        return pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])

    date_uniche = sorted(giocate["date"].unique())
    n_giornate = min(GIORNATE_RECENTI_PER_FORMA, len(date_uniche))
    ultime_date = date_uniche[-n_giornate:] if n_giornate else []
    recenti = giocate[giocate["date"].isin(ultime_date)]
    match_ids = recenti["game_id"].tolist()

    if not match_ids:
        return pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])

    print(f"⏳ Scarico dettaglio delle ultime {len(match_ids)} partite per la forma recente...")
    dettaglio = understat.read_player_match_stats(match_id=match_ids)

    agg = dettaglio.groupby("player").agg(
        minuti_recenti=("minutes", "sum"),
        xg_recente=("xg", "sum"),
        xa_recente=("xa", "sum"),
    ).reset_index()
    agg["novanta_recenti"] = (agg["minuti_recenti"] / 90).clip(lower=0.3)
    agg["xg_recente_90"] = agg["xg_recente"] / agg["novanta_recenti"]
    agg["xa_recente_90"] = agg["xa_recente"] / agg["novanta_recenti"]
    return agg[["player", "xg_recente_90", "xa_recente_90"]]


def main():
    print("⏳ Carico il listone...")
    listone = pd.read_excel(LISTONE_PATH, sheet_name="Tutti", skiprows=1)
    listone = listone.rename(columns={"R": "Ruolo", "Qt.A": "Prezzo"})
    listone = listone.dropna(subset=["Nome"]).reset_index(drop=True)
    print(f"✅ {len(listone)} giocatori nel listone")

    understat = sd.Understat(leagues="ITA-Serie A", seasons="2025-2026")

    print("⏳ Scarico statistiche stagionali da Understat...")
    stats = understat.read_player_season_stats().reset_index()
    stats["_squadra_norm"] = stats["team"].apply(norm)
    print(f"✅ {len(stats)} giocatori da Understat")

    print("⏳ Scarico il calendario...")
    schedule = understat.read_schedule()
    schedule["date"] = pd.to_datetime(schedule["date"])
    forza, media_for, media_against, prossimo = calcola_forza_squadre_e_prossimo_avversario(schedule)

    try:
        forma_recente = calcola_forma_recente(understat, schedule)
    except Exception as e:
        print(f"⚠️  Non sono riuscito a scaricare la forma recente ({e}), uso solo la media stagionale.")
        forma_recente = pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])

    indisponibili = carica_indisponibili()
    if indisponibili:
        print(f"⚠️  {len(indisponibili)} giocatori segnati come indisponibili")

    def trova_match(nome_listone, squadra_listone):
        candidati = stats[stats["_squadra_norm"] == norm(squadra_listone)]
        if candidati.empty:
            candidati = stats
        nomi = candidati["player"].tolist()
        match = get_close_matches(norm(nome_listone), [norm(n) for n in nomi], n=1, cutoff=0.55)
        if not match:
            return None
        idx = [norm(n) for n in nomi].index(match[0])
        return nomi[idx]

    risultati = []
    for _, row in listone.iterrows():
        nome, squadra, ruolo, prezzo, fvm = row["Nome"], row["Squadra"], row["Ruolo"], row["Prezzo"], row.get("FVM")

        nome_stat = trova_match(nome, squadra)
        if nome_stat is None:
            continue

        riga = stats[stats["player"] == nome_stat]
        if riga.empty:
            continue
        riga = riga.iloc[0]

        matches_stagione = max(riga["matches"], 1)
        minuti_medi = riga["minutes"] / matches_stagione
        novanta = max(riga["minutes"] / 90, 0.1)

        xg_90_stagione = riga["xg"] / novanta
        xa_90_stagione = riga["xa"] / novanta

        riga_recente = forma_recente[forma_recente["player"] == nome_stat] if len(forma_recente) else forma_recente
        if len(riga_recente):
            xg_recente = riga_recente.iloc[0]["xg_recente_90"]
            xa_recente = riga_recente.iloc[0]["xa_recente_90"]
            xg_90 = PESO_FORMA_RECENTE * xg_recente + (1 - PESO_FORMA_RECENTE) * xg_90_stagione
            xa_90 = PESO_FORMA_RECENTE * xa_recente + (1 - PESO_FORMA_RECENTE) * xa_90_stagione
        else:
            xg_90, xa_90 = xg_90_stagione, xa_90_stagione

        prob_titolarita = min(minuti_medi / 75, 1.0)

        chiave_indisp = (norm(nome), squadra)
        info_indisponibile = indisponibili.get(chiave_indisp)
        if info_indisponibile:
            prob_titolarita = 0.0

        voto_atteso = 6.0 + min(xg_90 + xa_90, 1.0) * 0.6
        rigorista = is_rigorista(nome, squadra)

        punteggio = voto_atteso
        punteggio += xg_90 * BONUS_MALUS["gol"]
        punteggio += xa_90 * BONUS_MALUS["assist"]
        if rigorista:
            punteggio += 0.15 * (0.8 * BONUS_MALUS["rigore_segnato"] + 0.2 * BONUS_MALUS["rigore_sbagliato"])
        if ruolo in ("D", "P"):
            punteggio += modificatore_difesa(6.2)

        mult_difficolta, avversario = difficolta_per_ruolo(squadra, prossimo, forza, media_for, media_against, ruolo)
        punteggio *= mult_difficolta
        punteggio *= prob_titolarita

        valore_stagionale = round(punteggio * GIORNATE_RIMANENTI, 1)
        valore_per_credito = round(valore_stagionale / prezzo, 3) if prezzo else 0

        if matches_stagione >= 8:
            affidabilita = "Alta"
        elif matches_stagione >= 3:
            affidabilita = "Media"
        else:
            affidabilita = "Bassa"

        risultati.append({
            "Nome": nome, "Ruolo": ruolo, "Squadra": squadra, "Prezzo": prezzo, "FVM": fvm,
            "Pt_giornata": round(punteggio, 2), "Valore_stagionale": valore_stagionale,
            "Valore_per_credito": valore_per_credito,
            "Affidabilita": affidabilita,
            "Prossimo_avversario": avversario or "-",
            "Indisponibile": bool(info_indisponibile),
            "Motivo_indisponibilita": info_indisponibile.get("motivo", "") if info_indisponibile else "",
        })

    df_out = pd.DataFrame(risultati).sort_values("Valore_per_credito", ascending=False)
    df_out.to_csv(OUTPUT_PATH, index=False)
    print(f"✅ Scritto {OUTPUT_PATH} con {len(df_out)} giocatori")


if __name__ == "__main__":
    main()
