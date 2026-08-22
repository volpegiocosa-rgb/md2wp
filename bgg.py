"""Client per la BGG XML API2 (richiede un token nell'header Authorization)."""
import os
import time
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://boardgamegeek.com/xmlapi2"


class BGGError(Exception):
    pass


def _headers():
    token = os.environ.get("BGG_API_TOKEN")
    if not token:
        raise BGGError("BGG_API_TOKEN non configurato (vedi file .env)")
    return {"Authorization": f"Bearer {token}"}


def search_games(query):
    """Cerca giochi su BGG per titolo. Ritorna [{id, name, year}, ...]."""
    resp = requests.get(
        f"{BASE_URL}/search",
        params={"query": query, "type": "boardgame"},
        headers=_headers(),
        timeout=10,
    )
    if resp.status_code == 401:
        raise BGGError("Token BGG non valido o scaduto")
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    results = []
    for item in root.findall("item"):
        name_el = item.find("name")
        year_el = item.find("yearpublished")
        results.append({
            "id": item.get("id"),
            "name": name_el.get("value") if name_el is not None else "?",
            "year": year_el.get("value") if year_el is not None else None,
        })
    return results


def _fetch_thing_xml(game_id):
    # La API a volte risponde 202 mentre prepara i dati: si ritenta a breve.
    for attempt in range(5):
        resp = requests.get(
            f"{BASE_URL}/thing",
            params={"id": game_id, "stats": 1},
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code == 401:
            raise BGGError("Token BGG non valido o scaduto")
        if resp.status_code == 202:
            time.sleep(1.5)
            continue
        resp.raise_for_status()
        return resp.text
    raise BGGError("BGG non ha risposto in tempo utile, riprova")


def _format_players(root_item):
    lo_el = root_item.find("minplayers")
    hi_el = root_item.find("maxplayers")
    lo = lo_el.get("value") if lo_el is not None else None
    hi = hi_el.get("value") if hi_el is not None else None
    if not lo:
        return ""
    if not hi or hi == lo:
        return lo
    return f"{lo}-{hi}"


def _format_duration(root_item):
    lo_el = root_item.find("minplaytime")
    hi_el = root_item.find("maxplaytime")
    lo = lo_el.get("value") if lo_el is not None else None
    hi = hi_el.get("value") if hi_el is not None else None
    if not lo:
        return ""
    if not hi or hi == lo:
        return f"{lo} min"
    return f"{lo}-{hi} min"


def _format_age(root_item):
    age_el = root_item.find("minage")
    age = age_el.get("value") if age_el is not None else None
    if not age or age == "0":
        return ""
    return f"{age}+"


def get_game(game_id):
    """Recupera i dati di un gioco da BGG e li ritorna nel formato usato dal template."""
    xml_text = _fetch_thing_xml(game_id)
    root = ET.fromstring(xml_text)
    item = root.find("item")
    if item is None:
        raise BGGError("Gioco non trovato su BGG")

    name_el = item.find("name[@type='primary']")
    name = name_el.get("value") if name_el is not None else ""

    designers = [
        link.get("value")
        for link in item.findall("link[@type='boardgamedesigner']")
    ]
    publishers = [
        link.get("value")
        for link in item.findall("link[@type='boardgamepublisher']")
    ]

    return {
        "name": name,
        "authors": ", ".join(designers),
        "publisher": publishers[0] if publishers else "",
        "players": _format_players(item),
        "age": _format_age(item),
        "duration": _format_duration(item),
    }
