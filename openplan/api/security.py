"""Sécurité de l'API quand elle est exposée (cadrage 0.4, incrément 4.6).

- **Jetons nommés** : `OPENPLAN_API_TOKENS="augus:…, marie:…"` ; hors boucle locale chaque
  requête `/api` (sauf `/api/health`) porte `Authorization: Bearer <jeton>` ; le nom du jeton
  devient l'acteur `user:<nom>` de l'historique du projet. Révoquer = retirer la ligne et
  relancer. Le jeton n'est jamais lu dans l'URL ni écrit dans une réponse ou un journal.
- **Journal des accès** (`<data>/access.jsonl`, API exposée seulement) : date, acteur, méthode,
  chemin sans paramètres, code, durée. Ni jeton, ni contenu, ni adresse d'origine.
- **En-têtes** sur toutes les réponses : CSP stricte (le front n'a aucune dépendance externe),
  pas d'inclusion dans un cadre, pas de référent, pas de reniflage de type.
- **Cookie de lecture** (app.plandesign.fr) : les `<img src>`, `<a download>` et chargeurs du
  front ne peuvent pas porter l'en-tête ; `POST /api/session` (avec le Bearer) pose un cookie
  `openplan_read` HttpOnly, Secure, SameSite=Lax, Path=/api, 12 h, dont la valeur est un HMAC
  (secret du serveur, nom, jeton) et jamais le jeton. L'intergiciel l'accepte **seulement pour
  GET et HEAD**, et jamais sur les quelques routes GET qui écrivent (`COOKIE_DENIED`) : toute
  mutation exige le Bearer, un site tiers ne peut donc rien modifier avec le cookie. Changer un
  jeton invalide le cookie ; `DELETE /api/session` l'efface.
Sur la boucle locale : pas de jeton, pas de journal, pas de cookie, en-têtes quand même.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from openplan.api.access import (
    LOGIN_REQUIRED,
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    AccountsBackend,
)
from openplan.api.guards import route_guard
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.settings import Settings
from openplan.scene.history import USER_ACTOR, user_actor
from openplan.schemas.common import StrictModel

ACCESS_LOG = "access.jsonl"
READ_COOKIE = "openplan_read"
READ_COOKIE_MAX_AGE = 12 * 3600
"""Durée du cookie de lecture ; le front le renouvelle tant que l'onglet vit."""
READ_SECRET_FILE = "read_cookie.secret"
"""Secret du serveur (32 octets aléatoires, créé une fois dans le dossier des données, 600)."""
COOKIE_DENIED = (
    re.compile(r"^/api/projects/?$"),
    re.compile(r"^/api/projects/[^/]+/renders/[^/]+/pick$"),
)
"""Routes GET qui écrivent (la liste renomme les anciens noms de fichier ; la désignation dans
un rendu note le journal local) : refusées au cookie, Bearer seulement. Un test vérifie que
toute route GET/HEAD dont la source écrit est ici."""
SAFE_METHODS = frozenset({"GET", "HEAD"})
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), geolocation=(), microphone=()",
}
_NO_CSP_PREFIXES = ("/docs", "/redoc")
"""La documentation interactive de FastAPI charge ses scripts d'un CDN : pas de CSP là."""
UNAUTHORIZED_DETAIL = "jeton d'accès requis (Authorization: Bearer …)"


class TokenMissingError(RuntimeError):
    """L'API écouterait hors de la boucle locale sans aucun jeton configuré."""


def match_token(tokens: dict[str, str], header: str) -> str | None:
    """Nom du jeton porté par `Authorization: Bearer …`, None sinon. Tous les jetons sont
    comparés en temps constant, sans arrêt au premier trouvé."""
    scheme, _, candidate = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    candidate = candidate.strip()
    found: str | None = None
    for name, token in tokens.items():
        if hmac.compare_digest(candidate, token) and found is None:
            found = name
    return found


def cookie_allowed(method: str, path: str) -> bool:
    """Le cookie de lecture ne vaut que pour une lecture : GET/HEAD, hors routes qui écrivent."""
    return method.upper() in SAFE_METHODS and not any(p.match(path) for p in COOKIE_DENIED)


def read_secret(data_dir: Path) -> bytes:
    """Le secret du cookie de lecture, créé une fois (jamais dans le dépôt ni les journaux)."""
    path = data_dir / READ_SECRET_FILE
    if path.is_file():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode()
    path.write_bytes(value)
    # Système de fichiers sans droits POSIX (montage Windows) : rien d'autre à faire.
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return value


def read_cookie_value(secret: bytes, name: str, token: str) -> str:
    """`nom:HMAC-SHA256(secret, nom:jeton)` : dérivé du jeton, il ne le révèle pas et meurt
    avec lui (jeton changé ou retiré → HMAC différent)."""
    mac = hmac.new(secret, f"{name}:{token}".encode(), hashlib.sha256).hexdigest()
    return f"{name}:{mac}"


def match_read_cookie(secret: bytes, tokens: dict[str, str], value: str) -> str | None:
    """Nom du jeton porté par un cookie de lecture valide, None sinon (temps constant)."""
    name, sep, mac = value.partition(":")
    token = tokens.get(name) if sep else None
    if token is None:
        return None
    expected = read_cookie_value(secret, name, token).partition(":")[2]
    return name if hmac.compare_digest(mac, expected) else None


class SessionState(StrictModel):
    """Réponse de `POST /api/session` : le cookie est posé (API exposée) ou sans objet."""

    exposed: bool
    cookie: bool
    name: str | None = None
    expires_in_s: int = 0


class AccessLog:
    """Une ligne JSON par requête `/api` (hors santé et suivi de tâche)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def write(self, *, actor: str, method: str, path: str, status: int, ms: float) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "actor": actor,
            "method": method,
            "path": path,
            "status": status,
            "ms": round(ms),
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def logged_path(path: str) -> bool:
    return path.startswith("/api/") and path != "/api/health" and "/jobs/" not in path


def with_security_headers(response: Response, path: str) -> Response:
    for key, value in SECURITY_HEADERS.items():
        if key == "Content-Security-Policy" and path.startswith(_NO_CSP_PREFIXES):
            continue
        response.headers.setdefault(key, value)
    return response


ACCOUNTS_PUBLIC = frozenset(
    {"/api/health", "/api/auth/email/start", "/api/auth/email/callback", "/api/auth/logout"}
)
"""Comptes actifs : les seules routes `/api` ouvertes sans session (note 17, § 4)."""
AUTH_PREFIX = "/api/auth/"
AUTH_RATE_EXEMPT = frozenset({"/api/auth/me"})
"""`/api/auth/me` est lu à chaque chargement du front : hors de la limite des tentatives."""
AUTH_RATE_LIMIT = 10
AUTH_RATE_WINDOW_S = 60.0
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ACCOUNTS_REFERRER_POLICY = "same-origin"
"""Avec `no-referrer`, le navigateur envoie `Origin: null` sur un POST (formulaire ou fetch) :
le contrôle d'origine refuserait l'application elle-même. `same-origin` n'envoie rien aux
autres sites."""


def request_origin(request: Request) -> str | None:
    """L'origine déclarée : `Origin`, à défaut celle du `Referer` ; None si aucune."""
    origin = request.headers.get("origin")
    if origin:
        return origin.strip().lower()
    referer = request.headers.get("referer", "")
    scheme, sep, rest = referer.partition("://")
    if not sep:
        return None
    return f"{scheme.lower()}://{rest.split('/', 1)[0].lower()}"


def install_security(app: FastAPI, settings: Settings) -> None:
    """Un seul intergiciel : jeton (ou session) → acteur, journal des accès, en-têtes."""
    accounts: AccountsBackend | None = getattr(app.state, "accounts", None)
    if accounts is not None:
        _install_accounts_guard(app, settings, accounts)
        return
    tokens = settings.tokens
    exposed = not settings.local_only
    if exposed and not tokens:
        msg = (
            f"l'API écouterait sur {settings.host} sans jeton : refusé. Créer un jeton nommé "
            "(`openplan token <nom>`) et le mettre dans OPENPLAN_API_TOKENS (ou OPENPLAN_API_TOKEN)"
        )
        raise TokenMissingError(msg)
    log = AccessLog(settings.data_dir / ACCESS_LOG) if exposed else None
    secret = read_secret(settings.data_dir) if exposed else b""

    @app.post("/api/session", tags=["security"], dependencies=[Depends(route_guard)])
    def open_session(request: Request, response: Response) -> SessionState:
        """Pose le cookie de lecture (API exposée ; l'intergiciel a déjà exigé le Bearer) :
        les images, téléchargements et le GLB du front passent ensuite sans en-tête, en lecture
        seulement. Sur la boucle locale : rien à poser."""
        if not exposed:
            return SessionState(exposed=False, cookie=False)
        name = match_token(tokens, request.headers.get("authorization", ""))
        if name is None:  # le cookie seul ne peut pas se renouveler lui-même
            response.delete_cookie(READ_COOKIE, path="/api")
            return SessionState(exposed=True, cookie=False)
        response.set_cookie(
            READ_COOKIE,
            read_cookie_value(secret, name, tokens[name]),
            max_age=READ_COOKIE_MAX_AGE,
            path="/api",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return SessionState(exposed=True, cookie=True, name=name, expires_in_s=READ_COOKIE_MAX_AGE)

    @app.delete(
        "/api/session", tags=["security"], status_code=204, dependencies=[Depends(route_guard)]
    )
    def close_session() -> Response:
        """Efface le cookie de lecture (déconnexion de l'onglet)."""
        gone = Response(status_code=204)
        gone.delete_cookie(READ_COOKIE, path="/api")
        return gone

    @app.middleware("http")
    async def _guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        path = request.url.path
        actor = USER_ACTOR
        if exposed and path.startswith("/api/") and path != "/api/health":
            name = match_token(tokens, request.headers.get("authorization", ""))
            if name is None and cookie_allowed(request.method, path):
                name = match_read_cookie(secret, tokens, request.cookies.get(READ_COOKIE, ""))
            if name is None:
                refused = JSONResponse(
                    status_code=401,
                    content={"detail": UNAUTHORIZED_DETAIL},
                    headers={"WWW-Authenticate": "Bearer"},
                )
                if log is not None and logged_path(path):
                    log.write(
                        actor="-",
                        method=request.method,
                        path=path,
                        status=401,
                        ms=(time.perf_counter() - started) * 1000,
                    )
                return with_security_headers(refused, path)
            actor = user_actor(name)
        request.state.actor = actor
        response = await call_next(request)
        if log is not None and logged_path(path):
            log.write(
                actor=actor,
                method=request.method,
                path=path,
                status=response.status_code,
                ms=(time.perf_counter() - started) * 1000,
            )
        return with_security_headers(response, path)


def _install_accounts_guard(app: FastAPI, settings: Settings, accounts: AccountsBackend) -> None:
    """Comptes actifs (note 17, § 3) : la session `pd_session` remplace le jeton et le cookie
    de lecture ; contrôle de l'origine sur toute mutation ; 401 sans session hors des routes
    publiques ; limite des tentatives sur `/api/auth/*`. Les droits par projet sont vérifiés
    ensuite, route par route (`openplan.api.guards`)."""
    exposed = not settings.local_only
    log = AccessLog(settings.data_dir / ACCESS_LOG) if exposed else None
    allowed_origin = settings.public_origin
    limiter = SlidingWindowLimiter(AUTH_RATE_LIMIT, AUTH_RATE_WINDOW_S)

    def finish(response: Response, request: Request, actor: str, started: float) -> Response:
        path = request.url.path
        if log is not None and logged_path(path):
            log.write(
                actor=actor,
                method=request.method,
                path=path,
                status=response.status_code,
                ms=(time.perf_counter() - started) * 1000,
            )
        response.headers["Referrer-Policy"] = ACCOUNTS_REFERRER_POLICY
        return with_security_headers(response, path)

    @app.middleware("http")
    async def _session_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        path = request.url.path
        request.state.user = None
        if not path.startswith("/api/"):
            return finish(await call_next(request), request, "-", started)
        if request.method in MUTATING and request_origin(request) != allowed_origin:
            refused = JSONResponse(status_code=403, content={"detail": "origine refusée"})
            return finish(refused, request, "-", started)
        if path.startswith(AUTH_PREFIX) and path not in AUTH_RATE_EXEMPT:
            client = request.client.host if request.client else "-"
            if not limiter.allow(client):
                slow = JSONResponse(
                    status_code=429,
                    content={"detail": "trop de tentatives, réessayer dans une minute"},
                    headers={"Retry-After": str(int(AUTH_RATE_WINDOW_S))},
                )
                return finish(slow, request, "-", started)
        raw = request.cookies.get(SESSION_COOKIE, "")
        resolved = await run_in_threadpool(accounts.resolve_session, raw) if raw else None
        actor = "-"
        if resolved is not None:
            request.state.user = resolved.user
            actor = user_actor(str(resolved.user.id))
            request.state.actor = actor
        elif path not in ACCOUNTS_PUBLIC:
            refused = JSONResponse(status_code=401, content={"detail": LOGIN_REQUIRED})
            if raw:  # cookie expiré ou révoqué : il s'efface
                refused.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
            return finish(refused, request, "-", started)
        response = await call_next(request)
        if (
            resolved is not None
            and resolved.renewed
            and SESSION_COOKIE not in str(response.headers.get("set-cookie", ""))
        ):
            response.set_cookie(
                SESSION_COOKIE,
                raw,
                max_age=SESSION_MAX_AGE_S,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
        return finish(response, request, actor, started)
