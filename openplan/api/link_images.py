"""Images récupérées d'après une adresse (KAN-98 L3) : ce que partagent le collage d'un lien
(`routes.board`, POST …/media/link) et le module navigateur (`accounts.extension`, KAN-102).

- une limite par compte : 10 adresses par minute, collages et ajouts du module confondus
  (accord du PO, 28 oct.) : ces routes ne servent pas à sonder des sites en série ;
- le nom du fichier gardé, tiré de l'adresse de l'image et de son type réel.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Request

from openplan.api.ratelimit import SlidingWindowLimiter

LINK_LIMIT = 10
LINK_WINDOW_S = 60.0
"""10 liens par minute et par compte (accord du PO, 28 oct.)."""


def link_limiter(request: Request) -> SlidingWindowLimiter:
    """La limite de l'application (une seule, posée sur son état au premier appel)."""
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, "link_limiter", None)
    if limiter is None:
        limiter = SlidingWindowLimiter(LINK_LIMIT, LINK_WINDOW_S)
        request.app.state.link_limiter = limiter
    return limiter


def link_name(image_url: str, media_type: str) -> str:
    """Le nom du fichier gardé : la fin de l'adresse de l'image, avec l'extension de son type
    réel (« salon%20bleu.webp » donne « salon-bleu.png » pour un PNG)."""
    stem = Path(unquote(urlsplit(image_url).path)).stem
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.")[:80] or "image-du-lien"
    return f"{stem}.{media_type.removeprefix('image/').replace('jpeg', 'jpg')}"
