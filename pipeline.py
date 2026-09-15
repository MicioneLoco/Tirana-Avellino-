"""
Pipeline v4: FantacalcioPedia come fonte principale del "voto" (fantamedia
reale degli ultimi anni, non piu' una stima da xG), con rilevamento
infortuni automatico. Understat resta solo per calendario/forma recente,
dove FPEDIA non arriva.

Se FPEDIA non risponde o cambia struttura, ogni giocatore per cui lo
scraping fallisce torna al vecchio calcolo basato su Understat, invece di
far fallire tutta la pipeline.
"""

import json
import time
import random
import base64
import concurrent.futures

import requests
from bs4 import BeautifulSoup
import pandas as pd
from unidecode import unidecode
from difflib import get_close_matches
import soccerdata as sd

LISTONE_PATH = "listone.xlsx"
OUTPUT_PATH = "results_sample.csv"
INDISPONIBILI_PATH = "indisponibili.json"
GIORNATE_RIMANENTI = 34
GIORNATE_RECENTI_PER_FORMA = 5
PESO_FORMA_RECENTE = 0.3  # ora la fantamedia reale e' la base, la forma recente pesa meno

FPEDIA_BASEURL = base64.b64decode("aHR0cHM6Ly93d3cuZmFudGFjYWxjaW9wZWRpYS5jb20=").decode()
FPEDIA_LISTA_URL = f"{FPEDIA_BASEURL}/lista-calciatori-serie-a/"
FPEDIA_RUOLI = ["portieri", "difensori", "centrocampisti", "trequartisti", "attaccanti"]
FPEDIA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
FPEDIA_MAX_WORKERS = 5

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


def fpedia_lista_url_giocatori():
    urls = []
    for ruolo in FPEDIA_RUOLI:
        try:
            r = requests.get(FPEDIA_LISTA_URL + ruolo + "/", headers=FPEDIA_HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            for art in soup.find_all("article"):
                a = art.find("a")
                if a and a.get("href"):
                    urls.append(a.get("href"))
        except Exception as e:
            print(f"⚠️  FPEDIA: impossibile leggere la lista '{ruolo}' ({e})")
    return list(dict.fromkeys(urls))


def fpedia_attributi_giocatore(url):
    try:
        time.sleep(random.uniform(0.3, 1.2))
        r = requests.get(url, headers=FPEDIA_HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        attributi = {"url": url}
        attributi["Nome"] = soup.select_one("h1").get_text().strip()

        sel = " div.col_one_fourth:nth-of-type(n+2) div"
        blocchi = soup.select(sel)
        for b in blocchi:
            span = b.find("span")
            strong = b.find("strong")
            if span and strong:
                anno = strong.text.split(" ")[-1].strip()
                attributi[f"Fantamedia anno {anno}"] = span.text.strip()

        try:
            sel_pres = "div.col_one_fourth:nth-of-type(2) span.rouge"
            attributi["Presenze_corrente"] = soup.select_one(sel_pres).text.strip()
        except Exception:
            pass

        try:
            trend_icon = soup.select(sel)[0].find("i")
            classi = trend_icon.get("class", [])
            attributi["Trend"] = "UP" if "icon-arrow-up" in classi else "DOWN"
        except Exception:
            attributi["Trend"] = "STABLE"

        try:
            img = soup.select_one("img.inf_calc")
            titolo = img.get("title", "") if img else ""
            attributi["Infortunato"] = "Infortunato" in titolo
            attributi["Consigliato_prossima_giornata"] = "Consigliato per la giornata" in titolo
        except Exception:
            attributi["Infortunato"] = False
            attributi["Consigliato_prossima_giornata"] = False

        try:
            sel_sq = "#content > div > div.section.nobg.nomargin > div > div > div:nth-child(2) > div.col_three_fifth > div.promo.promo-border.promo-light.row > div:nth-child(3) > div:nth-child(1) > div > img"
            attributi["Squadra_fpedia"] = soup.select_one(sel_sq).get("title").split(":")[1].strip()
        except Exception:
            attributi["Squadra_fpedia"] = None

        try:
            attributi["Ruolo_fpedia"] = soup.select_one(".label12 span.label").get_text().strip()
        except Exception:
            attributi["Ruolo_fpedia"] = None

        return attributi
    except Exception as e:
        print(f"⚠️  FPEDIA: errore su {url}: {e}")
        return None


def scarica_fpedia():
    print("⏳ Scarico l'elenco giocatori da FantacalcioPedia...")
    urls = fpedia_lista_url_giocatori()
    print(f"✅ {len(urls)} pagine giocatore trovate")

    if not urls:
        return pd.DataFrame()

    risultati = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=FPEDIA_MAX_WORKERS) as executor:
        for i, attributi in enumerate(executor.map(fpedia_attributi_giocatore, urls)):
            if attributi:
                risultati.append(attributi)
            if (i + 1) % 50 == 0:
                print(f"   ...{i + 1}/{len(urls)} giocatori scaricati")

    print(f"✅ FPEDIA: {len(risultati)} giocatori scaricati con successo")
    return pd.DataFrame(risultati)


def fantamedia_pesata(riga_fpedia):
    colonne_anno = sorted(
        [c for c in riga_fpedia.index if c.startswith("Fantamedia anno")],
        reverse=True,
    )
    valori = []
    for c in colonne_anno[:2]:
        v = riga_fpedia.get(c)
        try:
            v = float(str(v).replace(",", "."))
            if v > 0:
                valori.append(v)
        except (TypeError, ValueError):
            continue
    if not valori:
        return None
    if len(valori) == 1:
        return valori[0]
    return round(valori[0] * 0.65 + valori[1] * 0.35, 2)


def costruisci_mappa_squadre(squadre_listone, squadre_understat):
    ALIAS_NOTI = {
        "milan": ["ac milan", "milan"], "parma": ["parma calcio 1913", "parma"],
        "inter": ["inter milan", "internazionale", "inter"], "roma": ["as roma", "roma"],
        "genoa": ["genoa cfc", "genoa"], "verona": ["hellas verona", "verona"],
    }
    mappa, usati = {}, set()
    nomi_understat_norm = {}
    for s in squadre_understat:
        nomi_understat_norm.setdefault(norm(s), s)

    rimasti = []
    for s in squadre_listone:
        n = norm(s)
        if n in nomi_understat_norm:
            mappa[s] = nomi_understat_norm[n]
            usati.add(n)
        else:
            rimasti.append(s)

    ancora_rimasti = []
    for s in rimasti:
        n = norm(s)
        trovato = False
        for alias in ALIAS_NOTI.get(n, []):
            if alias in nomi_understat_norm and alias not in usati:
                mappa[s] = nomi_understat_norm[alias]
                usati.add(alias)
                trovato = True
                break
        if not trovato:
            ancora_rimasti.append(s)

    disponibili = {n: orig for n, orig in nomi_understat_norm.items() if n not in usati}
    for s in ancora_rimasti:
        match = get_close_matches(norm(s), list(disponibili.keys()), n=1, cutoff=0.85)
        if match:
            mappa[s] = disponibili[match[0]]
            del disponibili[match[0]]
    return mappa


def calcola_forza_squadre_e_prossimo_avversario(schedule, squadra_map):
    schedule = schedule.copy()
    schedule["home_team"] = schedule["home_team"].map(lambda t: squadra_map.get(t, t))
    schedule["away_team"] = schedule["away_team"].map(lambda t: squadra_map.get(t, t))
    giocate = schedule[schedule["is_result"] == True]  # noqa: E712

    righe = []
    for _, m in giocate.iterrows():
        righe.append({"team": m["home_team"], "xg_for": m["home_xg"], "xg_against": m["away_xg"]})
        righe.append({"team": m["away_team"], "xg_for": m["away_xg"], "xg_against": m["home_xg"]})
    forza = pd.DataFrame(righe).groupby("team").mean(numeric_only=True) if righe else pd.DataFrame(columns=["xg_for", "xg_against"])

    media_for = forza["xg_for"].mean() if len(forza) else 1.3
    media_against = forza["xg_against"].mean() if len(forza) else 1.3

    da_giocare = schedule[schedule["is_result"] == False].sort_values("date")  # noqa: E712
    prossimo = {}
    for _, m in da_giocare.iterrows():
        prossimo.setdefault(m["home_team"], m["away_team"])
        prossimo.setdefault(m["away_team"], m["home_team"])
    print(f"ℹ️  Squadre con un prossimo avversario noto: {len(prossimo)}")
    return forza, media_for, media_against, prossimo


def difficolta_per_ruolo(squadra, prossimo, forza, media_for, media_against, ruolo):
    avversario = prossimo.get(squadra)
    if avversario is None or avversario not in forza.index:
        return 1.0, avversario
    xg_against_avv = forza.loc[avversario, "xg_against"]
    xg_for_avv = forza.loc[avversario, "xg_for"]
    if ruolo in ("C", "A"):
        mult = xg_against_avv / media_against if media_against else 1.0
    else:
        mult = media_for / xg_for_avv if xg_for_avv else 1.0
    return max(0.6, min(mult, 1.5)), avversario


def calcola_forma_recente(understat, schedule):
    giocate = schedule[schedule["is_result"] == True].sort_values("date")  # noqa: E712
    if giocate.empty:
        return pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])
    date_uniche = sorted(giocate["date"].unique())
    ultime_date = date_uniche[-min(GIORNATE_RECENTI_PER_FORMA, len(date_uniche)):]
    recenti = giocate[giocate["date"].isin(ultime_date)]
    match_ids = recenti["game_id"].tolist()
    if not match_ids:
        return pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])
    dettaglio = understat.read_player_match_stats(match_id=match_ids)
    agg = dettaglio.groupby("player").agg(
        minuti_recenti=("minutes", "sum"), xg_recente=("xg", "sum"), xa_recente=("xa", "sum"),
    ).reset_index()
    agg["novanta"] = (agg["minuti_recenti"] / 90).clip(lower=0.3)
    agg["xg_recente_90"] = agg["xg_recente"] / agg["novanta"]
    agg["xa_recente_90"] = agg["xa_recente"] / agg["novanta"]
    return agg[["player", "xg_recente_90", "xa_recente_90"]]


def main():
    print("⏳ Carico il listone...")
    listone = pd.read_excel(LISTONE_PATH, sheet_name="Tutti", skiprows=1)
    listone = listone.rename(columns={"R": "Ruolo", "Qt.A": "Prezzo"})
    listone = listone.dropna(subset=["Nome"]).reset_index(drop=True)
    print(f"✅ {len(listone)} giocatori nel listone")

    try:
        fpedia_df = scarica_fpedia()
    except Exception as e:
        print(f"⚠️  FPEDIA non disponibile ({e}), proseguo solo con Understat.")
        fpedia_df = pd.DataFrame()

    fpedia_by_norm_name = {}
    if len(fpedia_df):
        for _, r in fpedia_df.iterrows():
            fpedia_by_norm_name.setdefault(norm(r["Nome"]), r)

    def trova_fpedia(nome_listone, squadra_listone):
        if not fpedia_by_norm_name:
            return None
        target = norm(nome_listone)
        if target in fpedia_by_norm_name:
            return fpedia_by_norm_name[target]
        candidati_nomi = list(fpedia_by_norm_name.keys())
        contenuti = [n for n in candidati_nomi if target in n or n in target]
        pool = contenuti if contenuti else candidati_nomi
        match = get_close_matches(target, pool, n=1, cutoff=0.75)
        return fpedia_by_norm_name[match[0]] if match else None

    understat = sd.Understat(leagues="ITA-Serie A", seasons="2026-2027")
    stats = pd.DataFrame()
    forma_recente = pd.DataFrame(columns=["player", "xg_recente_90", "xa_recente_90"])
    forza, media_for, media_against, prossimo, mappa_squadre = None, 1.3, 1.3, {}, {}
    try:
        print("⏳ Scarico statistiche stagionali da Understat (xG di supporto)...")
        stats = understat.read_player_season_stats().reset_index()
        print(f"✅ {len(stats)} giocatori da Understat")

        print("⏳ Scarico il calendario...")
        schedule = understat.read_schedule()
        schedule["date"] = pd.to_datetime(schedule["date"])

        squadre_listone = listone["Squadra"].unique().tolist()
        squadre_understat = pd.unique(pd.concat([schedule["home_team"], schedule["away_team"], stats["team"]])).tolist()
        mappa_squadre = costruisci_mappa_squadre(squadre_listone, squadre_understat)
        non_mappate = [s for s in squadre_listone if s not in mappa_squadre]
        if non_mappate:
            print(f"⚠️  Squadre non abbinate a Understat (niente forma/avversario per loro, useranno solo FPEDIA): {non_mappate}")

        forza, media_for, media_against, prossimo = calcola_forza_squadre_e_prossimo_avversario(schedule, mappa_squadre)
        forma_recente = calcola_forma_recente(understat, schedule)
    except Exception as e:
        print(f"⚠️  Understat non disponibile ({e}) — proseguo solo con FPEDIA, niente calendario/forma recente.")

    def trova_understat(nome_listone, squadra_listone):
        if stats.empty:
            return None
        squadra_u = mappa_squadre.get(squadra_listone)
        if squadra_u is None:
            return None
        candidati = stats[stats["team"] == squadra_u]
        if candidati.empty:
            return None
        nomi = candidati["player"].tolist()
        nomi_norm = [norm(n) for n in nomi]
        target = norm(nome_listone)
        contenuti = [n for n in nomi_norm if target in n or n in target]
        if len(contenuti) == 1:
            return nomi[nomi_norm.index(contenuti[0])]
        pool = contenuti if contenuti else nomi_norm
        match = get_close_matches(target, pool, n=1, cutoff=0.6)
        return nomi[nomi_norm.index(match[0])] if match else None

    indisponibili_manuali = carica_indisponibili()
    if indisponibili_manuali:
        print(f"⚠️  {len(indisponibili_manuali)} giocatori segnati manualmente come indisponibili")

    prezzo_max_per_ruolo = listone.groupby("Ruolo")["Prezzo"].max().to_dict()

    risultati = []
    fonte_conteggio = {"fpedia": 0, "solo_understat": 0, "stima_generica": 0}

    for _, row in listone.iterrows():
        nome, squadra, ruolo, prezzo, fvm = row["Nome"], row["Squadra"], row["Ruolo"], row["Prezzo"], row.get("FVM")

        riga_fpedia = trova_fpedia(nome, squadra)
        nome_understat = trova_understat(nome, squadra)
        riga_understat = stats[stats["player"] == nome_understat].iloc[0] if nome_understat is not None and not stats.empty else None

        # nessun dato reale trovato: stima generica per non escludere il giocatore dal database,
        # ma chiaramente etichettata cosi' non la si scambia per un dato vero
        if riga_fpedia is None and riga_understat is None:
            fonte_conteggio["stima_generica"] += 1
            prezzo_max_ruolo = prezzo_max_per_ruolo.get(ruolo) or prezzo or 1
            frazione_prezzo = min(prezzo / prezzo_max_ruolo, 1.0) if prezzo_max_ruolo else 0.5
            voto_generico = 5.7 + frazione_prezzo * 0.9
            prob_tit_generica = 0.3 + frazione_prezzo * 0.5

            info_manuale = indisponibili_manuali.get((norm(nome), squadra))
            indisponibile = bool(info_manuale)
            if indisponibile:
                prob_tit_generica = 0.0

            punteggio = voto_generico
            if is_rigorista(nome, squadra):
                punteggio += 0.15 * (0.8 * BONUS_MALUS["rigore_segnato"] + 0.2 * BONUS_MALUS["rigore_sbagliato"])
            if ruolo in ("D", "P"):
                punteggio += modificatore_difesa(6.2)

            mult_difficolta, avversario = difficolta_per_ruolo(
                mappa_squadre.get(squadra, squadra), prossimo, forza, media_for, media_against, ruolo
            )
            punteggio *= mult_difficolta
            punteggio *= prob_tit_generica

            valore_stagionale = round(punteggio * GIORNATE_RIMANENTI, 1)
            valore_per_credito = round(valore_stagionale / prezzo, 3) if prezzo else 0

            if prob_tit_generica >= 0.75:
                stato = "Titolare"
            elif prob_tit_generica >= 0.4:
                stato = "Ballottaggio"
            else:
                stato = "Riserva"

            risultati.append({
                "Nome": nome, "Ruolo": ruolo, "Squadra": squadra, "Prezzo": prezzo, "FVM": fvm,
                "Pt_giornata": round(punteggio, 2), "Valore_stagionale": valore_stagionale,
                "Valore_per_credito": valore_per_credito,
                "Affidabilita": "Bassa", "Stato_titolarita": stato,
                "Prossimo_avversario": avversario or "-",
                "Indisponibile": indisponibile,
                "Motivo_indisponibilita": info_manuale.get("motivo", "") if info_manuale else "",
                "Fonte_dato": "Stima generica (nessun dato reale trovato)",
            })
            continue

        voto_da_fpedia = fantamedia_pesata(riga_fpedia) if riga_fpedia is not None else None
        if voto_da_fpedia is not None:
            voto_atteso = voto_da_fpedia
            fonte_conteggio["fpedia"] += 1
        else:
            voto_atteso = 6.0
            fonte_conteggio["solo_understat"] += 1

        xg_90 = xa_90 = 0.0
        prob_titolarita = 0.5
        if riga_understat is not None:
            matches_stagione = max(riga_understat["matches"], 1)
            minuti_medi = riga_understat["minutes"] / matches_stagione
            novanta = max(riga_understat["minutes"] / 90, 0.1)
            xg_90_stagione = riga_understat["xg"] / novanta
            xa_90_stagione = riga_understat["xa"] / novanta

            riga_recente = forma_recente[forma_recente["player"] == nome_understat] if len(forma_recente) else forma_recente
            if len(riga_recente):
                xg_90 = PESO_FORMA_RECENTE * riga_recente.iloc[0]["xg_recente_90"] + (1 - PESO_FORMA_RECENTE) * xg_90_stagione
                xa_90 = PESO_FORMA_RECENTE * riga_recente.iloc[0]["xa_recente_90"] + (1 - PESO_FORMA_RECENTE) * xa_90_stagione
            else:
                xg_90, xa_90 = xg_90_stagione, xa_90_stagione
            prob_titolarita = min(minuti_medi / 75, 1.0)
        elif riga_fpedia is not None:
            try:
                presenze = float(str(riga_fpedia.get("Presenze_corrente", "0")).replace(",", "."))
                prob_titolarita = min(presenze / 6, 1.0) if presenze else 0.5
            except (TypeError, ValueError):
                prob_titolarita = 0.5

        infortunato_fpedia = bool(riga_fpedia is not None and riga_fpedia.get("Infortunato"))
        info_manuale = indisponibili_manuali.get((norm(nome), squadra))
        indisponibile = infortunato_fpedia or bool(info_manuale)
        if indisponibile:
            prob_titolarita = 0.0

        rigorista = is_rigorista(nome, squadra)
        punteggio = voto_atteso
        punteggio += xg_90 * BONUS_MALUS["gol"]
        punteggio += xa_90 * BONUS_MALUS["assist"]
        if rigorista:
            punteggio += 0.15 * (0.8 * BONUS_MALUS["rigore_segnato"] + 0.2 * BONUS_MALUS["rigore_sbagliato"])
        if ruolo in ("D", "P") and voto_da_fpedia is None:
            punteggio += modificatore_difesa(6.2)

        mult_difficolta, avversario = difficolta_per_ruolo(
            mappa_squadre.get(squadra, squadra), prossimo, forza, media_for, media_against, ruolo
        )
        punteggio *= mult_difficolta
        punteggio *= prob_titolarita

        valore_stagionale = round(punteggio * GIORNATE_RIMANENTI, 1)
        valore_per_credito = round(valore_stagionale / prezzo, 3) if prezzo else 0

        if riga_understat is not None and riga_understat["matches"] >= 8:
            affidabilita = "Alta"
        elif voto_da_fpedia is not None:
            affidabilita = "Alta"
        elif riga_understat is not None and riga_understat["matches"] >= 3:
            affidabilita = "Media"
        else:
            affidabilita = "Bassa"

        if prob_titolarita >= 0.75:
            stato = "Titolare"
        elif prob_titolarita >= 0.4:
            stato = "Ballottaggio"
        else:
            stato = "Riserva"

        motivo = "Infortunato (da FantacalcioPedia)" if infortunato_fpedia else (info_manuale.get("motivo", "") if info_manuale else "")
        fonte_dato = "FPEDIA (fantamedia reale)" if voto_da_fpedia is not None else "Understat (stima da xG)"

        risultati.append({
            "Nome": nome, "Ruolo": ruolo, "Squadra": squadra, "Prezzo": prezzo, "FVM": fvm,
            "Pt_giornata": round(punteggio, 2), "Valore_stagionale": valore_stagionale,
            "Valore_per_credito": valore_per_credito,
            "Affidabilita": affidabilita, "Stato_titolarita": stato,
            "Prossimo_avversario": avversario or "-",
            "Indisponibile": indisponibile, "Motivo_indisponibilita": motivo,
            "Fonte_dato": fonte_dato,
        })

    print(f"ℹ️  Fonte del voto — FPEDIA: {fonte_conteggio['fpedia']}, solo Understat: {fonte_conteggio['solo_understat']}, stima generica: {fonte_conteggio['stima_generica']}")

    if not risultati:
        print("⚠️  Nessun giocatore nel listone.")
        pd.DataFrame(columns=["Nome", "Ruolo", "Squadra", "Prezzo", "FVM", "Pt_giornata", "Valore_stagionale",
                               "Valore_per_credito", "Affidabilita", "Stato_titolarita", "Prossimo_avversario",
                               "Indisponibile", "Motivo_indisponibilita", "Fonte_dato"]).to_csv(OUTPUT_PATH, index=False)
        return

    df_out = pd.DataFrame(risultati).sort_values("Valore_per_credito", ascending=False)
    df_out.to_csv(OUTPUT_PATH, index=False)
    print(f"✅ Scritto {OUTPUT_PATH} con {len(df_out)} giocatori (su {len(listone)} nel listone)")


if __name__ == "__main__":
    main()
