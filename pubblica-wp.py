#!/usr/bin/env python3
"""CLI standalone per convertire una bozza Markdown in un articolo WordPress
Gutenberg (con dati gioco da BoardGameGeek e prezzo da alcuni negozi online)
e pubblicarlo come bozza. Un solo file, lanciabile su qualsiasi macchina con
Python 3 + requests + beautifulsoup4 installati.
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin

try:
    import requests
except ImportError:
    sys.exit("Manca il pacchetto 'requests'. Installa con: pip install requests beautifulsoup4")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Manca il pacchetto 'beautifulsoup4'. Installa con: pip install requests beautifulsoup4")


# ==========================================================================
# Caricamento .env (parser minimale, nessuna dipendenza da python-dotenv)
# ==========================================================================

def load_env_file(path):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# ==========================================================================
# Client BGG (porting di bgg.py)
# ==========================================================================

BGG_BASE_URL = "https://boardgamegeek.com/xmlapi2"


class BGGError(Exception):
    pass


def _bgg_headers():
    token = os.environ.get("BGG_API_TOKEN")
    if not token:
        raise BGGError("BGG_API_TOKEN non configurato (vedi --env / file .env)")
    return {"Authorization": f"Bearer {token}"}


def bgg_search_games(query):
    resp = requests.get(
        f"{BGG_BASE_URL}/search",
        params={"query": query, "type": "boardgame"},
        headers=_bgg_headers(),
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


def _bgg_fetch_thing_xml(game_id):
    for _ in range(5):
        resp = requests.get(
            f"{BGG_BASE_URL}/thing",
            params={"id": game_id, "stats": 1},
            headers=_bgg_headers(),
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


def _bgg_format_players(root_item):
    lo_el = root_item.find("minplayers")
    hi_el = root_item.find("maxplayers")
    lo = lo_el.get("value") if lo_el is not None else None
    hi = hi_el.get("value") if hi_el is not None else None
    if not lo:
        return ""
    if not hi or hi == lo:
        return lo
    return f"{lo}-{hi}"


def _bgg_format_duration(root_item):
    lo_el = root_item.find("minplaytime")
    hi_el = root_item.find("maxplaytime")
    lo = lo_el.get("value") if lo_el is not None else None
    hi = hi_el.get("value") if hi_el is not None else None
    if not lo:
        return ""
    if not hi or hi == lo:
        return f"{lo} min"
    return f"{lo}-{hi} min"


def _bgg_format_age(root_item):
    age_el = root_item.find("minage")
    age = age_el.get("value") if age_el is not None else None
    if not age or age == "0":
        return ""
    return f"{age}+"


def bgg_get_game(game_id):
    xml_text = _bgg_fetch_thing_xml(game_id)
    root = ET.fromstring(xml_text)
    item = root.find("item")
    if item is None:
        raise BGGError("Gioco non trovato su BGG")

    name_el = item.find("name[@type='primary']")
    name = name_el.get("value") if name_el is not None else ""

    designers = [link.get("value") for link in item.findall("link[@type='boardgamedesigner']")]
    publishers = [link.get("value") for link in item.findall("link[@type='boardgamepublisher']")]

    return {
        "name": name,
        "authors": ", ".join(designers),
        "publisher": publishers[0] if publishers else "",
        "players": _bgg_format_players(item),
        "age": _bgg_format_age(item),
        "duration": _bgg_format_duration(item),
    }


# ==========================================================================
# Ricerca prezzo (porting di pricing.py)
# ==========================================================================

PRICE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
PRICE_TIMEOUT = 8


def _price_get(url):
    resp = requests.get(url, headers={"User-Agent": PRICE_USER_AGENT}, timeout=PRICE_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _price_parse(raw):
    if raw is None:
        return None
    s = raw.replace("€", "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _price_pick_best_match(candidates, query):
    q = query.strip().lower()
    matches = [c for c in candidates if q in c["title"].lower()]
    if not matches:
        return None
    return min(matches, key=lambda c: len(c["title"]))


def _search_dungeondice(query):
    url = f"https://www.dungeondice.it/it/search?controller=search&s={quote(query)}"
    soup = BeautifulSoup(_price_get(url), "html.parser")
    candidates = []
    for card in soup.select("article.e-list-product"):
        title_el = card.select_one("h3.e-list-product-title")
        price_el = card.select_one('[itemprop="price"]')
        if not title_el or not price_el:
            continue
        price = _price_parse(price_el.get("content"))
        if price is None:
            continue
        link_el = title_el.find_parent("a")
        link = link_el["href"] if link_el and link_el.has_attr("href") else url
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _price_pick_best_match(candidates, query)


def _search_lsgiochi(query):
    url = f"https://www.lsgiochi.it/search?q={quote(query)}"
    soup = BeautifulSoup(_price_get(url), "html.parser")
    candidates = []
    for card in soup.select("li.product-gallery__item"):
        title_el = card.select_one("a.product-gallery__name")
        price_el = card.select_one(".product-price__price")
        if not title_el or not price_el:
            continue
        price = _price_parse(price_el.get_text())
        if price is None:
            continue
        link = urljoin(url, title_el.get("href", url))
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _price_pick_best_match(candidates, query)


def _search_magicmerchant(query):
    url = f"https://www.magicmerchant.it/it/search?controller=search&s={quote(query)}"
    soup = BeautifulSoup(_price_get(url), "html.parser")
    candidates = []
    for card in soup.select("article.e-list-product, div.product-miniature"):
        title_el = card.select_one("h3, .product-title")
        price_el = card.select_one('[itemprop="price"]')
        if not title_el or not price_el:
            continue
        price = _price_parse(price_el.get("content") or price_el.get_text())
        if price is None:
            continue
        link_el = card.select_one("a")
        link = link_el["href"] if link_el and link_el.has_attr("href") else url
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _price_pick_best_match(candidates, query)


PRICE_STORES = [
    ("DungeonDice", _search_dungeondice),
    ("LS Giochi", _search_lsgiochi),
    ("MagicMerchant", _search_magicmerchant),
]


def search_price(title):
    details = []
    for store_name, fetch in PRICE_STORES:
        try:
            match = fetch(title)
        except Exception:
            match = None
        if match:
            details.append({
                "store": store_name,
                "price": match["price"],
                "title": match["title"],
                "url": match["url"],
            })

    if not details:
        return {"average": None, "rounded": None, "label": None, "details": []}

    average = sum(d["price"] for d in details) / len(details)
    rounded = round(average / 5) * 5
    return {
        "average": round(average, 2),
        "rounded": rounded,
        "label": f"{rounded}€ circa",
        "details": details,
    }


# ==========================================================================
# Client WordPress (porting di wp.py)
# ==========================================================================

class WPError(Exception):
    pass


def _wp_config():
    url = os.environ.get("WP_URL", "").rstrip("/")
    user = os.environ.get("WP_USER")
    app_password = os.environ.get("WP_APP_PASSWORD")
    if not url or not user or not app_password:
        raise WPError(
            "Configurazione WordPress mancante: imposta WP_URL, WP_USER e "
            "WP_APP_PASSWORD nel file .env"
        )
    return url, user, app_password


def _wp_parse_json(resp):
    return json.loads(resp.content.decode("utf-8-sig"))


def wp_create_draft_post(title, content):
    url, user, app_password = _wp_config()

    resp = requests.post(
        f"{url}/wp-json/wp/v2/posts",
        json={"title": title, "content": content, "status": "draft"},
        auth=(user, app_password),
        timeout=20,
    )
    if resp.status_code == 401:
        raise WPError("Credenziali WordPress non valide (controlla WP_USER e WP_APP_PASSWORD)")
    if not resp.ok:
        try:
            detail = _wp_parse_json(resp).get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise WPError(f"Errore WordPress ({resp.status_code}): {detail}")

    data = _wp_parse_json(resp)
    return {
        "id": data.get("id"),
        "edit_url": f"{url}/wp-admin/post.php?post={data.get('id')}&action=edit",
    }


# ==========================================================================
# Template WordPress di default (porting di templates/index.html:245-349)
# ==========================================================================

DEFAULT_TEMPLATE = """[intro]
<!-- wp:paragraph -->
<p>[rt_reading_time label="⌛" postfix="minuti" postfix_singular="minuto"]</p>
<!-- /wp:paragraph -->

<fieldset style="background: #FFEAE9; border: 4px double #940F04; width: auto; padding: 35px;">
<legend style="color: #003366; font-weight: bold; font-size: larger">Per chi va di fretta</legend>
<ul>
<li>primo elemento</li>
<li>secondo elemento</li>
<li>terzo elemento</li>
</ul>
</fieldset>

[cap]

<!-- wp:heading {"textAlign":"center","level":1} -->
<h1 class="wp-block-heading has-text-align-center">Titolo</h1>
<!-- /wp:heading -->

<!-- wp:image {"id":20991,"sizeSlug":"full","linkDestination":"none","align":"center"} -->
<figure class="wp-block-image aligncenter size-full"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/trauma-tram.jpg" alt="" class="wp-image-20991"/></figure>
<!-- /wp:image -->

<!-- taglia da qui -->
<div class="container">
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/autore-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Autori</p>
<p class="value">[autori]</p>
</div>
</div>
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/editore-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Editore</p>
<p class="value">[editore]</p>
</div>
</div>
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/persone-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Giocatori</p>
<p class="value">[giocatori]</p>
</div>
</div>
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/eta-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Età</p>
<p class="value">[eta]</p>
</div>
</div>
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/tempo-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Durata</p>
<p class="value">[durata]</p>
</div>
</div>
<div class="box">
<div class="icon"><img src="https://www.volpegiocosa.it/wp-content/uploads/2024/08/costo-bg.png" alt="Icona" /></div>
<div class="content">
<p class="title">Costo</p>
<p class="value">[costo]</p>
</div>
</div>
</div>
<!-- fino a qui -->

[corpo]

<!-- wp:group {"layout":{"type":"constrained"}} -->
<div class="wp-block-group">

<!-- wp:html -->
<fieldset style="background: #ADD8E6; border: 4px double #940F04; width: auto; padding: 35px;">
  <legend style="color: #003366; font-weight: bold; font-size: larger">Link utili</legend>
  <ul>
    <li><a href="https://www.html.it/" target="_blank">Link 1</a></li>
    <li><a href="https://www.example.com/" target="_blank">Link 2</a></li>
    <li><a href="https://www.example.com/" target="_blank">Link 3</a></li>
    <li><a href="https://www.example.com/" target="_blank">Link 4</a></li>
    <li><a href="https://www.example.com/" target="_blank">Link 5</a></li>
  </ul>
</fieldset>
<!-- /wp:html -->

<!-- wp:heading -->
<h2 class="wp-block-heading"><strong>In conclusione</strong></h2>
<!-- /wp:heading -->

[finale]

</div>
<!-- /wp:group -->

<!-- taglia da qui -->
<!-- wp:paragraph -->
<p>[mi_piace]</p>
<!-- /wp:paragraph -->

<!-- wp:post-date {"textAlign":"right"} /-->
<!-- fino a qui -->"""


# ==========================================================================
# Conversione Markdown -> Gutenberg (porting della logica JS in index.html)
# ==========================================================================

CUSTOM_MARKERS = {"[intro]", "[/intro]", "[cap]", "[/cap]", "[corpo]", "[/corpo]", "[finale]", "[/finale]"}

# Placeholder temporanei (Unicode Private Use Area) usati per proteggere i tag
# generati dalla formattazione dall'escape HTML successivo.
_LINK_START, _LINK_MID, _LINK_END = "", "", ""
_BOLD_START, _BOLD_END = "", ""
_ITALIC_START, _ITALIC_END = "", ""
_EM_START, _EM_END = "", ""

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BOLD_RE = re.compile(r"\*(.*?)\*")
_ITALIC_RE = re.compile(r"_(.*?)_")
_EM_RE = re.compile(r"§§(.*?)§§")


def is_custom_marker(line):
    return line in CUSTOM_MARKERS


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def apply_formatting(text):
    formatted = text
    formatted = _LINK_RE.sub(lambda m: f"{_LINK_START}{m.group(2)}{_LINK_MID}{m.group(1)}{_LINK_END}", formatted)
    formatted = _BOLD_RE.sub(lambda m: f"{_BOLD_START}{m.group(1)}{_BOLD_END}", formatted)
    formatted = _ITALIC_RE.sub(lambda m: f"{_ITALIC_START}{m.group(1)}{_ITALIC_END}", formatted)
    formatted = _EM_RE.sub(lambda m: f"{_EM_START}{m.group(1)}{_EM_END}", formatted)

    formatted = escape_html(formatted)

    formatted = formatted.replace(_LINK_START, '<a href="')
    formatted = formatted.replace(_LINK_MID, '" target="_blank" rel="noopener noreferrer">')
    formatted = formatted.replace(_LINK_END, "</a>")
    formatted = formatted.replace(_BOLD_START, "<strong>")
    formatted = formatted.replace(_BOLD_END, "</strong>")
    formatted = formatted.replace(_ITALIC_START, "<i>")
    formatted = formatted.replace(_ITALIC_END, "</i>")
    formatted = formatted.replace(_EM_START, "<em>")
    formatted = formatted.replace(_EM_END, "</em>")

    return formatted


_HEADER_RES = [(lvl, re.compile(r"^" + ("#" * lvl) + r"\s+(.*?)\s*#*\s*$")) for lvl in (4, 3, 2, 1)]


def convert_markdown_to_html(raw_text):
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    html_lines = []
    in_paragraph = False
    in_list = False

    for line in lines:
        trimmed = line.strip()

        if is_custom_marker(trimmed):
            if in_paragraph:
                html_lines[-1] += "</p>"
                in_paragraph = False
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(trimmed)
            continue

        if not trimmed:
            if in_paragraph:
                html_lines[-1] += "</p>"
                in_paragraph = False
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        is_header = False
        for lvl, regex in _HEADER_RES:
            match = regex.match(trimmed)
            if match:
                if in_paragraph:
                    html_lines[-1] += "</p>"
                    in_paragraph = False
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                header_text = match.group(1).strip()
                html_lines.append(f"<h{lvl}>{escape_html(header_text)}</h{lvl}>")
                is_header = True
                break
        if is_header:
            continue

        if trimmed.startswith("+ "):
            if in_paragraph:
                html_lines[-1] += "</p>"
                in_paragraph = False
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append("<li>" + apply_formatting(trimmed[2:]) + "</li>")
            continue

        if not in_paragraph:
            html_lines.append("<p>" + apply_formatting(trimmed))
            in_paragraph = True
        else:
            html_lines[-1] += "<br>" + apply_formatting(trimmed)

    if in_paragraph:
        html_lines[-1] += "</p>"
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def extract_section(content, tag):
    end_tag = "[/" + tag[1:]
    s = content.find(tag)
    e = content.find(end_tag)
    if s == -1 or e == -1:
        return ""
    return content[s + len(tag):e].strip()


_GB_HEADING_RE = re.compile(r"^<h(\d)>(.*)</h\d>$")
_GB_PARAGRAPH_RE = re.compile(r"^<p>(.*)</p>$")


def convert_to_gutenberg(html_block):
    out = []
    for raw_line in html_block.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        h_match = _GB_HEADING_RE.match(line)
        if h_match:
            lvl, txt = h_match.group(1), h_match.group(2)
            out.append(f'<!-- wp:heading {{"level":{lvl}}} -->\n<h{lvl} class="wp-block-heading">{txt}</h{lvl}>\n<!-- /wp:heading -->')
            continue

        p_match = _GB_PARAGRAPH_RE.match(line)
        if p_match:
            txt = p_match.group(1)
            out.append(f"<!-- wp:paragraph -->\n<p>{txt}</p>\n<!-- /wp:paragraph -->")
            continue

        out.append(line)

    return "\n\n".join(out)


SECTION_TAGS = ["intro", "cap", "corpo", "finale"]


def convert_document(md_text, template_text, game_fields):
    html_intermedio = convert_markdown_to_html(md_text)

    sections = {}
    for tag in SECTION_TAGS:
        raw = extract_section(html_intermedio, f"[{tag}]")
        sections[tag] = convert_to_gutenberg(raw)

    final_wp = template_text
    for tag in SECTION_TAGS:
        final_wp = final_wp.replace(f"[{tag}]", sections.get(tag, ""), 1)

    for key, value in game_fields.items():
        final_wp = final_wp.replace(f"[{key}]", value or "", 1)

    final_wp = final_wp.replace("\r\n", "\n").replace("\r", "\n")
    return final_wp


# ==========================================================================
# CLI
# ==========================================================================

EPILOG = """\
esempi:
  # Conversione + ricerca dati gioco su BGG/prezzo + pubblicazione (con conferma)
  python3 pubblica-wp.py bozza.md --game "Wingspan"

  # Titolo articolo diverso dal titolo del gioco cercato su BGG
  python3 pubblica-wp.py bozza.md --game "Wingspan" --title "Wingspan: la recensione"

  # Solo conversione del testo, senza cercare dati su BGG o prezzo
  python3 pubblica-wp.py bozza.md --title "Articolo senza scheda gioco"

  # Override manuale di alcuni campi (hanno sempre priorità sui dati trovati)
  python3 pubblica-wp.py bozza.md --game "Wingspan" --costo "45€ circa" --eta "10+"

  # Template personalizzato e percorso di output specifico
  python3 pubblica-wp.py bozza.md --game "Wingspan" --template mio_template.html -o output/wingspan.txt

  # File .env in una posizione diversa da quella di default (./.env)
  python3 pubblica-wp.py bozza.md --game "Wingspan" --env /percorso/mio.env

Variabili richieste nel file .env (vedi .env.example):
  BGG_API_TOKEN                     token per le API di BoardGameGeek
  WP_URL, WP_USER, WP_APP_PASSWORD  credenziali del sito WordPress di destinazione
"""


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="pubblica-wp.py",
        description=(
            "Converte una bozza Markdown in un articolo WordPress Gutenberg, "
            "arricchendolo (opzionalmente) con dati gioco da BoardGameGeek e "
            "con il prezzo trovato su alcuni negozi online, e pubblica il "
            "risultato come bozza su WordPress. Fa da riga di comando tutto "
            "quello che fa l'interfaccia grafica del progetto."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="percorso del file Markdown da convertire (es. bozza.md)")
    parser.add_argument(
        "-g", "--game", metavar="TITOLO",
        help="titolo del gioco: se indicato, viene cercato su BoardGameGeek e nei negozi online per compilare autori, editore, giocatori, età, durata e costo",
    )
    parser.add_argument(
        "-t", "--title", metavar="TITOLO",
        help="titolo dell'articolo WordPress; se omesso viene usato --game (o il nome canonico trovato su BGG)",
    )
    parser.add_argument(
        "--template", metavar="FILE",
        help="file con un template WordPress personalizzato; se omesso viene usato il template incorporato nello script",
    )
    parser.add_argument(
        "--env", metavar="FILE", default=".env",
        help="percorso del file .env con le credenziali (default: ./.env)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="percorso dove salvare una copia locale del contenuto convertito (default: <input>.wp.txt)",
    )
    parser.add_argument("--autori", metavar="TESTO", help="override manuale del campo Autori")
    parser.add_argument("--editore", metavar="TESTO", help="override manuale del campo Editore")
    parser.add_argument("--giocatori", metavar="TESTO", help="override manuale del campo Giocatori")
    parser.add_argument("--eta", metavar="TESTO", help="override manuale del campo Età")
    parser.add_argument("--durata", metavar="TESTO", help="override manuale del campo Durata")
    parser.add_argument("--costo", metavar="TESTO", help="override manuale del campo Costo")
    return parser


def prompt_bgg_choice(results):
    print(f"Trovati {len(results)} risultati su BGG:")
    for i, item in enumerate(results, start=1):
        label = f"{item['name']} ({item['year']})" if item.get("year") else item["name"]
        print(f"  {i}. {label}")
    while True:
        choice = input("Scegli il numero del gioco corretto (invio per annullare): ").strip()
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]["id"]
        print("Scelta non valida, riprova.")


def fetch_game_data(game_query):
    """Ricerca BGG + prezzo. Ritorna (game_fields, nome_canonico_o_None)."""
    game_fields = {"autori": "", "editore": "", "giocatori": "", "eta": "", "durata": "", "costo": ""}
    canonical_name = None

    print(f"Ricerca su BGG per '{game_query}'...")
    try:
        results = bgg_search_games(game_query)
    except BGGError as e:
        print(f"  Avviso: ricerca BGG saltata ({e})")
        results = []

    game_id = None
    if len(results) == 1:
        game_id = results[0]["id"]
    elif len(results) > 1:
        game_id = prompt_bgg_choice(results)
    elif not results:
        print("  Nessun risultato trovato su BGG.")

    if game_id:
        try:
            data = bgg_get_game(game_id)
            game_fields["autori"] = data.get("authors", "")
            game_fields["editore"] = data.get("publisher", "")
            game_fields["giocatori"] = data.get("players", "")
            game_fields["eta"] = data.get("age", "")
            game_fields["durata"] = data.get("duration", "")
            canonical_name = data.get("name") or None
            print(f"  Dati di \"{data.get('name')}\" caricati da BGG.")
        except BGGError as e:
            print(f"  Avviso: recupero dettagli BGG fallito ({e})")

    price_query = canonical_name or game_query
    print(f"Ricerca prezzo per '{price_query}'...")
    price = search_price(price_query)
    if price["details"]:
        game_fields["costo"] = price["label"]
        for d in price["details"]:
            print(f"  {d['store']}: {d['price']:.2f}€")
        print(f"  Prezzo medio arrotondato: {price['label']}")
    else:
        print("  Nessun prezzo trovato nei negozi configurati. Puoi indicarlo con --costo.")

    return game_fields, canonical_name


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    load_env_file(args.env)

    if not os.path.isfile(args.input):
        sys.exit(f"Errore: file non trovato: {args.input}")
    with open(args.input, "r", encoding="utf-8") as f:
        md_text = f.read()

    if args.template:
        if not os.path.isfile(args.template):
            sys.exit(f"Errore: file template non trovato: {args.template}")
        with open(args.template, "r", encoding="utf-8") as f:
            template_text = f.read()
    else:
        template_text = DEFAULT_TEMPLATE

    game_fields = {"autori": "", "editore": "", "giocatori": "", "eta": "", "durata": "", "costo": ""}
    canonical_name = None
    if args.game:
        game_fields, canonical_name = fetch_game_data(args.game)

    overrides = {
        "autori": args.autori,
        "editore": args.editore,
        "giocatori": args.giocatori,
        "eta": args.eta,
        "durata": args.durata,
        "costo": args.costo,
    }
    for key, value in overrides.items():
        if value is not None:
            game_fields[key] = value

    title = args.title or canonical_name or args.game
    if not title:
        sys.exit("Errore: specifica un titolo per l'articolo con --title (oppure --game).")

    final_wp = convert_document(md_text, template_text, game_fields)

    output_path = args.output or (os.path.splitext(args.input)[0] + ".wp.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_wp)
    print(f"Output salvato in: {output_path}")

    answer = input(f"Pubblicare la bozza su WordPress con titolo \"{title}\"? [s/N] ").strip().lower()
    if answer not in ("s", "si", "sì", "y", "yes"):
        print("Pubblicazione annullata. Il file convertito resta salvato in locale.")
        return

    try:
        result = wp_create_draft_post(title, final_wp)
    except WPError as e:
        sys.exit(f"Errore nella pubblicazione: {e}")

    print(f"Bozza creata con successo (id {result['id']}): {result['edit_url']}")


if __name__ == "__main__":
    main()
