"""
Pipeline automatica: scarica le statistiche da Understat, le combina col
listone e le regole della lega, e sovrascrive results_sample.csv.
Pensato per girare da solo via GitHub Actions (non serve intervento manuale).
"""

import sys
import pandas as pd
from unidecode import unidecode
from difflib import get_close_matches
import soccerdata as sd

LISTONE_PATH = "listone.xlsx"
OUTPUT_PATH = "results_sample.csv"
GIORNATE_RIMANENTI = 34  # aggiorna man mano che la stagione avanza

# ---------------------------------------------------------------------
# REGOLE LEGA
# ---------------------------------------------------------------------
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


def main():
    print("⏳ Carico il listone...")
    listone = pd.read_excel(LISTONE_PATH, sheet_name="Tutti", skiprows=1)
    listone = listone.rename(columns={"R": "Ruolo", "Qt.A": "Prezzo"})
    listone = listone.dropna(subset=["Nome"]).reset_index(drop=True)
    print(f"✅ {len(listone)} giocatori nel listone")

    print("⏳ Scarico statistiche da Understat...")
    understat = sd.Understat(leagues="ITA-Serie A", seasons="2025-2026")
    stats = understat.read_player_season_stats().reset_index()
    stats["_squadra_norm"] = stats["team"].apply(norm)
    print(f"✅ {len(stats)} giocatori da Understat")

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

        matches = max(riga["matches"], 1)
        minuti_medi = riga["minutes"] / matches
        novanta = max(riga["minutes"] / 90, 0.1)

        xg_90 = riga["xg"] / novanta
        xa_90 = riga["xa"] / novanta
        prob_titolarita = min(minuti_medi / 75, 1.0)

        voto_atteso = 6.0 + min(xg_90 + xa_90, 1.0) * 0.6
        rigorista = is_rigorista(nome, squadra)

        punteggio = voto_atteso
        punteggio += xg_90 * BONUS_MALUS["gol"]
        punteggio += xa_90 * BONUS_MALUS["assist"]
        if rigorista:
            punteggio += 0.15 * (0.8 * BONUS_MALUS["rigore_segnato"] + 0.2 * BONUS_MALUS["rigore_sbagliato"])
        if ruolo in ("D", "P"):
            punteggio += modificatore_difesa(6.2)  # placeholder finché non colleghiamo i voti reali di squadra

        punteggio *= prob_titolarita

        valore_stagionale = round(punteggio * GIORNATE_RIMANENTI, 1)
        valore_per_credito = round(valore_stagionale / prezzo, 3) if prezzo else 0

        risultati.append({
            "Nome": nome, "Ruolo": ruolo, "Squadra": squadra, "Prezzo": prezzo, "FVM": fvm,
            "Pt_giornata": round(punteggio, 2), "Valore_stagionale": valore_stagionale,
            "Valore_per_credito": valore_per_credito,
        })

    df_out = pd.DataFrame(risultati).sort_values("Valore_per_credito", ascending=False)
    df_out.to_csv(OUTPUT_PATH, index=False)
    print(f"✅ Scritto {OUTPUT_PATH} con {len(df_out)} giocatori")


if __name__ == "__main__":
    main()
