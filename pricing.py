"""Ricerca (best-effort) del prezzo di un gioco su alcuni negozi online italiani.

Ogni negozio ha selettori HTML dedicati, verificati manualmente sul sito reale
(tranne MagicMerchant, protetto da una challenge anti-bot che ne ha impedito la
verifica: il selettore è una stima e potrebbe non funzionare). Se un negozio
cambia struttura o blocca la richiesta, la ricerca fallisce silenziosamente per
quel negozio soltanto: non impedisce di usare gli altri.
"""
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 8


def _get(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_price(raw):
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


def _pick_best_match(candidates, query):
    q = query.strip().lower()
    matches = [c for c in candidates if q in c["title"].lower()]
    if not matches:
        return None
    # tra i risultati che contengono il titolo cercato, quello con il nome più
    # corto è di solito il gioco base (le espansioni hanno nomi più lunghi)
    return min(matches, key=lambda c: len(c["title"]))


def _search_dungeondice(query):
    url = f"https://www.dungeondice.it/it/search?controller=search&s={quote(query)}"
    soup = BeautifulSoup(_get(url), "html.parser")
    candidates = []
    for card in soup.select("article.e-list-product"):
        title_el = card.select_one("h3.e-list-product-title")
        price_el = card.select_one('[itemprop="price"]')
        if not title_el or not price_el:
            continue
        price = _parse_price(price_el.get("content"))
        if price is None:
            continue
        link_el = title_el.find_parent("a")
        link = link_el["href"] if link_el and link_el.has_attr("href") else url
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _pick_best_match(candidates, query)


def _search_lsgiochi(query):
    url = f"https://www.lsgiochi.it/search?q={quote(query)}"
    soup = BeautifulSoup(_get(url), "html.parser")
    candidates = []
    for card in soup.select("li.product-gallery__item"):
        title_el = card.select_one("a.product-gallery__name")
        price_el = card.select_one(".product-price__price")
        if not title_el or not price_el:
            continue
        price = _parse_price(price_el.get_text())
        if price is None:
            continue
        link = urljoin(url, title_el.get("href", url))
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _pick_best_match(candidates, query)


def _search_magicmerchant(query):
    # Pattern stimato (probabile PrestaShop, come DungeonDice) e non verificabile:
    # il sito risponde con una challenge Cloudflare a qualunque richiesta automatica.
    url = f"https://www.magicmerchant.it/it/search?controller=search&s={quote(query)}"
    soup = BeautifulSoup(_get(url), "html.parser")
    candidates = []
    for card in soup.select("article.e-list-product, div.product-miniature"):
        title_el = card.select_one("h3, .product-title")
        price_el = card.select_one('[itemprop="price"]')
        if not title_el or not price_el:
            continue
        price = _parse_price(price_el.get("content") or price_el.get_text())
        if price is None:
            continue
        link_el = card.select_one("a")
        link = link_el["href"] if link_el and link_el.has_attr("href") else url
        candidates.append({"title": title_el.get_text(strip=True), "price": price, "url": link})
    return _pick_best_match(candidates, query)


STORES = [
    ("DungeonDice", _search_dungeondice),
    ("LS Giochi", _search_lsgiochi),
    ("MagicMerchant", _search_magicmerchant),
]


def search_price(title):
    """Interroga i negozi configurati e fa la media dei prezzi trovati, arrotondata ai 5€."""
    details = []
    for store_name, fetch in STORES:
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
