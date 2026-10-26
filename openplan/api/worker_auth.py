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

Porte de l'intergiciel (`WorkerGate`), AVANT toute lecture du corps :
- seuls les chemins exacts de l'API du poste (`WORKER_PATH`, sans encodage ni `//` ni `..`)
  quittent le contrôle de session ; tout autre chemin, même commençant par `/api/worker`,
  reste soumis à la session (401 sans elle) ;
- 401 sans le jeton (429 par adresse IP au-delà de `FAILED_AUTH_LIMIT` refus), 411 sans
  longueur annoncée pour un envoi, 413 au-delà du plafond, 429 au-delà d'une prise toutes les
  `RATE_WINDOW_S` secondes par jeton ;
- aucun en-tête CORS : le site ni aucune autre origine ne peuvent appeler ces routes depuis
  un navigateur.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.schemas.render_queue import MAX_IMAGE_BYTES, MAX_STYLE_IMAGES

WORKER_PREFIX = "/api/worker/"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
WORKER_PATH = re.compile(
    rf"/api/worker/(?:claim|jobs/{_UUID}/"
    rf"(?:files/(?:project|bake|camera)|style/[0-{MAX_STYLE_IMAGES - 1}]|result|fail|beat))"
)
"""Les seuls chemins de l'API du poste (comparés en entier, sur le chemin décodé ET brut)."""
WORKER_REFUSED = "jeton du poste de rendu requis"
MAX_WORKER_BODY = MAX_IMAGE_BYTES + 64 * 1024
"""Le plus gros envoi du poste : une image et son résultat (en-têtes multipart compris)."""
RATE_LIMITED = frozenset({"/api/worker/claim"})
RATE_WINDOW_S = 5.0
"""Le poste appelle `claim` toutes les 10 à 15 s : plus d'un appel par 5 s et par jeton = 429."""
FAILED_AUTH_LIMIT = 10
FAILED_AUTH_WINDOW_S = 600.0
"""Par adresse IP : au-delà de 10 jetons absents ou faux en 10 min, 429 (l'Infra sort ces
routes de la prison fail2ban : c'est l'application qui protège)."""


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def matched_hash(hashes: tuple[str, ...], header: str) -> str | None:
    """L'empreinte reconnue du jeton porté par `Authorization: Bearer …`, None sinon."""
    scheme, _, candidate = header.partition(" ")
    candidate = candidate.strip()
    if scheme.lower() != "bearer" or not candidate or len(candidate) > 256:
        return None
    digest = token_hash(candidate)
    found: str | None = None
    for expected in hashes:  # toutes comparées, sans arrêt au premier trouvé
        if hmac.compare_digest(expected, digest) and found is None:
            found = expected
    return found


def is_worker_path(request: Request) -> bool:
    """Chemin exact de l'API du poste, sans rien d'encodé ni de normalisable autrement."""
    path: str = request.scope.get("path", "")
    raw = request.scope.get("raw_path")
    raw_text = raw.decode("latin-1") if isinstance(raw, bytes | bytearray) else path
    return WORKER_PATH.fullmatch(path) is not None and raw_text == path


class WorkerGate:
    def __init__(
        self, hashes: tuple[str, ...], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.hashes = hashes
        self.limiter = SlidingWindowLimiter(1, RATE_WINDOW_S, clock)
        self.failures = SlidingWindowLimiter(FAILED_AUTH_LIMIT, FAILED_AUTH_WINDOW_S, clock)

    def refusal(self, request: Request) -> Response | None:
        """None si la requête peut passer ; sinon la réponse de refus (corps jamais lu)."""
        digest = matched_hash(self.hashes, request.headers.get("authorization", ""))
        if digest is None:
            client = request.client.host if request.client else "-"
            if not self.failures.allow(client):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "trop d'essais refusés, réessayer plus tard"},
                    headers={"Retry-After": str(int(FAILED_AUTH_WINDOW_S))},
                )
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
        path = request.scope.get("path", "")
        if path in RATE_LIMITED and not self.limiter.allow(f"{digest[:16]}:{path}"):
            return JSONResponse(
                status_code=429,
                content={"detail": "trop d'appels du poste de rendu"},
                headers={"Retry-After": str(int(RATE_WINDOW_S))},
            )
        return None

    async def passage(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Le refus, ou la réponse de la route (intergiciel de session)."""
        refused = self.refusal(request)
        return refused if refused is not None else await call_next(request)


def require_worker(request: Request) -> None:
    """Garde de la route : 401 sans le jeton du poste (ou sans empreinte configurée)."""
    hashes: tuple[str, ...] = request.app.state.settings.realistic.worker_token_sha256
    if matched_hash(hashes, request.headers.get("authorization", "")) is None:
        raise HTTPException(
            status_code=401, detail=WORKER_REFUSED, headers={"WWW-Authenticate": "Bearer"}
        )
