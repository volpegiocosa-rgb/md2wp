"""Client per la REST API di WordPress (autenticazione via Application Password)."""
import json
import os

import requests


class WPError(Exception):
    pass


def _config():
    url = os.environ.get("WP_URL", "").rstrip("/")
    user = os.environ.get("WP_USER")
    app_password = os.environ.get("WP_APP_PASSWORD")
    if not url or not user or not app_password:
        raise WPError(
            "Configurazione WordPress mancante: imposta WP_URL, WP_USER e "
            "WP_APP_PASSWORD nel file .env"
        )
    return url, user, app_password


def _parse_json(resp):
    # Alcuni siti (plugin/tema) inseriscono un BOM UTF-8 prima del JSON,
    # che fa fallire resp.json(): si decodifica esplicitamente con utf-8-sig.
    return json.loads(resp.content.decode("utf-8-sig"))


def create_draft_post(title, content):
    """Crea un nuovo articolo in bozza su WordPress con titolo e contenuto Gutenberg forniti."""
    url, user, app_password = _config()

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
            detail = _parse_json(resp).get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise WPError(f"Errore WordPress ({resp.status_code}): {detail}")

    data = _parse_json(resp)
    return {
        "id": data.get("id"),
        "edit_url": f"{url}/wp-admin/post.php?post={data.get('id')}&action=edit",
    }
