"""KAN-101 : la page publique d'un rendu partagé, sans compte (`/api/share/<jeton>`).

Module léger (ni base, ni comptes) : l'intergiciel de sécurité y lit le préfixe, la CSP de la
page et la forme du chemin à écrire au journal (le jeton n'y est jamais écrit). Les routes et
les liens eux-mêmes sont dans `openplan.api.accounts.share_links`.

La page ne montre que l'image du rendu, ce qui l'a guidé (sans nom d'image) et le nom
PlanDesign ; le nom du projet seulement si l'auteur l'a laissé coché. Aucune donnée
personnelle, aucun script, aucune ressource extérieure.
"""

from __future__ import annotations

import base64
import hashlib
import re
from html import escape

from openplan.brand import APP_NAME

SHARE_PREFIX = "/api/share/"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
"""`secrets.token_urlsafe(32)` : 43 caractères (256 bits)."""
_LOGGED = re.compile(r"^/api/share/[^/]+")

PAGE_STYLE = (
    "body{margin:0;font-family:system-ui,sans-serif;background:#f6f4f1;color:#222}"
    "main{max-width:1100px;margin:0 auto;padding:16px}"
    "header{font-weight:600;font-size:1.1em;margin:0 0 12px}"
    "img{display:block;max-width:100%;height:auto;margin:0 auto;border-radius:6px}"
    "p{margin:8px 0 0;color:#555}"
    "footer{margin-top:16px;font-size:.85em;color:#777}"
)
_STYLE_HASH = base64.b64encode(hashlib.sha256(PAGE_STYLE.encode()).digest()).decode()
SHARE_CSP = (
    "default-src 'none'; img-src 'self'; "
    f"style-src 'sha256-{_STYLE_HASH}'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
"""Rien d'autre que l'image du même site et la feuille de style en ligne de la page
(empreinte) : ni script, ni connexion, ni cadre, ni formulaire."""
ROBOTS = "noindex, nofollow, noarchive, noimageindex"
SHARE_HEADERS: dict[str, str] = {
    "X-Robots-Tag": ROBOTS,
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}
"""En-têtes propres aux réponses publiques (page, image, refus) ; l'intergiciel ajoute la CSP
ci-dessus, `X-Content-Type-Options: nosniff` et `X-Frame-Options: DENY`."""
NOT_FOUND_TEXT = "Ce lien n'existe pas ou n'est plus valide."
TOO_MANY_TEXT = "Trop de demandes : réessayez dans une minute."


def is_share_path(path: str) -> bool:
    return path.startswith(SHARE_PREFIX)


def logged_form(path: str) -> str:
    """Le chemin tel qu'il entre au journal des accès : le jeton y est remplacé."""
    return _LOGGED.sub("/api/share/…", path)


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="robots" content="{ROBOTS}">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{escape(title)}</title><style>{PAGE_STYLE}</style></head>"
        f"<body><main><header>{escape(APP_NAME)}</header>{body}</main></body></html>"
    )


def render_page(image_url: str, guided: str | None, project_name: str | None) -> str:
    """La page d'un rendu partagé ; tout texte est échappé."""
    parts = [f'<img src="{escape(image_url)}" alt="Rendu réaliste">']
    if guided:
        parts.append(f"<p>{escape(guided)}</p>")
    if project_name:
        parts.append(f"<p>Projet : {escape(project_name)}</p>")
    parts.append(f"<footer>Rendu partagé en lecture seule avec {escape(APP_NAME)}.</footer>")
    return _page(f"Rendu · {APP_NAME}", "".join(parts))


def message_page(text: str) -> str:
    """Refus (404, 429) : la même page, quelle qu'en soit la raison."""
    return _page(APP_NAME, f"<p>{escape(text)}</p>")
