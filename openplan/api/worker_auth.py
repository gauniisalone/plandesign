"""Jeton du poste de rendu (KAN-89) : `Authorization: Bearer <jeton>` sur les routes
`/api/worker/*`, et seulement là.

Choix : l'empreinte SHA-256 du jeton dans une variable d'environnement
(OPENPLAN_WORKER_TOKEN_SHA256), plutôt qu'en base :
- il n'y a qu'un poste et qu'un jeton : ni table, ni écran d'administration, ni route de plus à
  protéger ;
- le serveur ne connaît jamais le jeton (une fuite du `.env` ou d'une sauvegarde ne donne
  rien d'utilisable), et le dépôt ne contient ni l'un ni l'autre ;
- révoquer = retirer l'empreinte et relancer le conteneur (une action de l'Infra, tracée) ;
  plusieurs empreintes, séparées par des virgules, permettent une rotation sans coupure.
Le jeton (32 octets aléatoires) n'est jamais lu dans l'URL, ni écrit dans une réponse ou un
journal ; toutes les empreintes sont comparées en temps constant.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from openplan.schemas.render_queue import MAX_IMAGE_BYTES

WORKER_PREFIX = "/api/worker/"
WORKER_REFUSED = "jeton du poste de rendu requis"
MAX_WORKER_BODY = MAX_IMAGE_BYTES + 64 * 1024
"""Le plus gros envoi du poste : une image et son résultat (en-têtes multipart compris)."""


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def matches(hashes: tuple[str, ...], header: str) -> bool:
    scheme, _, candidate = header.partition(" ")
    candidate = candidate.strip()
    if scheme.lower() != "bearer" or not candidate or len(candidate) > 256:
        return False
    digest = token_hash(candidate)
    found = False
    for expected in hashes:  # toutes comparées, sans arrêt au premier trouvé
        found = hmac.compare_digest(expected, digest) or found
    return found


def worker_refusal(request: Request, hashes: tuple[str, ...]) -> Response | None:
    """Intergiciel (avant toute lecture du corps) : 401 sans le jeton, 411 sans longueur
    annoncée pour un envoi, 413 au-delà du plafond ; None si la requête peut passer."""
    if not matches(hashes, request.headers.get("authorization", "")):
        return JSONResponse(
            status_code=401,
            content={"detail": WORKER_REFUSED},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if request.method in ("POST", "PUT"):
        raw = request.headers.get("content-length")
        if raw is None or not raw.isdigit():
            return JSONResponse(status_code=411, content={"detail": "longueur requise"})
        if int(raw) > MAX_WORKER_BODY:
            return JSONResponse(status_code=413, content={"detail": "envoi trop gros"})
    return None


def require_worker(request: Request) -> None:
    """401 sans le jeton du poste (ou sans empreinte configurée : API du poste fermée)."""
    hashes: tuple[str, ...] = request.app.state.settings.realistic.worker_token_sha256
    if not matches(hashes, request.headers.get("authorization", "")):
        raise HTTPException(
            status_code=401, detail=WORKER_REFUSED, headers={"WWW-Authenticate": "Bearer"}
        )
