"""Suivi des erreurs du serveur en ligne (D4, note D0 validée par le PO) : GlitchTip, par le SDK
Sentry. Rien ne s'active sans `OPENPLAN_SENTRY_DSN` non vide : sans elle, `sentry_sdk` n'est même
pas importé et rien ne part. L'application locale et le paquet distribué n'envoient donc jamais
rien.

Avec la variable, seul part ce qui aide à corriger un plantage : type d'exception, pile d'appels
(sans variables locales), message filtré et tronqué, méthode et route de la requête, User-Agent.
`before_send` retire tout le reste : corps, cookies, en-têtes, paramètres, utilisateur, contextes
libres, messages des fils d'Ariane, chemins personnels et chemins des projets, noms d'images,
jetons, identifiants et texte cité (consignes, noms de projet)."""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openplan import __version__

DSN_ENV = "OPENPLAN_SENTRY_DSN"
ENV_ENV = "OPENPLAN_ENV"
SHA_ENV = "OPENPLAN_GIT_SHA"
MAX_MESSAGE = 200
TEST_MESSAGE = "erreur de test volontaire (openplan sentry-test)"

_KEPT_HEADERS = frozenset({"user-agent"})
_KEPT_CONTEXTS = frozenset({"runtime", "os", "trace"})
_KEPT_REQUEST = frozenset({"method", "url", "headers"})
_KEPT_BREADCRUMB_DATA = frozenset({"method", "status_code", "url"})

_state: dict[str, bool] = {"active": False}

# Chemins : code de l'application gardé à partir de « openplan/ » ou du paquet installé ;
# données d'un projet réduites à <projet> ; tout autre chemin personnel réduit à son nom de base
# (sauf image : <image>).
_IMAGE = r"[^\s/\\'\"«»]+\.(?:jpe?g|png|webp|heic|heif|gif|bmp|tiff?|avif)\b"
_RE_IMAGE = re.compile(_IMAGE, re.IGNORECASE)
_RE_APP_DATA = re.compile(r"/app/data/[^\s'\"«»]*")
_RE_HOME = re.compile(r"(?:/home/|/Users/|/root/|[A-Za-z]:\\Users\\)[^\s'\"«»]*")
_RE_BEARER = re.compile(r"(?i)\b(bearer|token|basic)(\s+|=|:\s*)[A-Za-z0-9._~+/=-]+")
_RE_UUID = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I)
_RE_SECRET = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{24,}\b")
_RE_QUOTED = re.compile(r"«[^»]*»|\"[^\"]*\"|“[^”]*”")
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def dsn() -> str | None:
    """Le DSN configuré, ou None (variable absente ou vide)."""
    value = os.environ.get(DSN_ENV, "").strip()
    return value or None


def is_active() -> bool:
    """Le suivi a été activé dans ce processus (pour les mentions du front)."""
    return _state["active"]


def dsn_host(value: str) -> str:
    """L'hôte du DSN, seul montré (jamais la clé)."""
    return urlsplit(value).hostname or "hôte inconnu"


def release() -> str:
    sha = os.environ.get(SHA_ENV, "").strip()[:12]
    return f"openplan@{__version__}" + (f"+{sha}" if sha else "")


def _code_path(path: str) -> str:
    for marker in ("/openplan/", "/site-packages/", "\\openplan\\"):
        if marker in path:
            keep = path.split(marker, 1)[1]
            return ("openplan/" if "openplan" in marker else "") + keep.replace("\\", "/")
    base = re.split(r"[/\\]", path.rstrip("/\\"))[-1]
    if _RE_IMAGE.fullmatch(base):
        return "<image>"
    return base or "<chemin>"


def scrub_text(text: str, *, quotes: bool = True) -> str:
    """Le texte sans chemin personnel, chemin de projet, image, jeton, identifiant ni adresse
    électronique ; avec `quotes`, sans texte cité non plus (consignes, noms de projet)."""
    text = _RE_APP_DATA.sub("<projet>", text)
    text = _RE_HOME.sub(lambda m: _code_path(m.group(0)), text)
    text = _RE_IMAGE.sub("<image>", text)
    text = _RE_BEARER.sub(lambda m: f"{m.group(1)} <masqué>", text)
    text = _RE_EMAIL.sub("<adresse>", text)
    text = _RE_UUID.sub("<id>", text)
    text = _RE_SECRET.sub("<masqué>", text)
    return _RE_QUOTED.sub("«…»", text) if quotes else text


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


def scrub_url(url: str) -> str:
    """L'URL sans paramètres ni fragment, identifiants masqués."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, scrub_text(parts.path), "", ""))


def _scrub_frames(exception: Mapping[str, Any]) -> None:
    frames = (exception.get("stacktrace") or {}).get("frames") or []
    for frame in frames:
        frame.pop("vars", None)
        for key in ("abs_path", "filename"):
            path = frame.get(key)
            if isinstance(path, str) and ("/" in path or "\\" in path):
                frame[key] = _code_path(path)
        # Le code source reste lisible ; seuls chemins, jetons et identifiants y sont masqués.
        for key in ("pre_context", "post_context"):
            if isinstance(frame.get(key), list):
                frame[key] = [scrub_text(line, quotes=False) for line in frame[key]]
        if isinstance(frame.get("context_line"), str):
            frame["context_line"] = scrub_text(frame["context_line"], quotes=False)


def _scrub_request(request: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: v for k, v in request.items() if k in _KEPT_REQUEST}
    if isinstance(out.get("url"), str):
        out["url"] = scrub_url(out["url"])
    headers = out.get("headers")
    if isinstance(headers, Mapping):
        out["headers"] = {k: v for k, v in headers.items() if k.lower() in _KEPT_HEADERS}
    else:
        out.pop("headers", None)
    return out


def _scrub_breadcrumb(crumb: dict[str, Any]) -> dict[str, Any] | None:
    # Journaux Python : catégorie = nom du journal, type « log » ; jetés quel que soit le nom.
    category = str(crumb.get("category") or "")
    if crumb.get("type") == "log" or category == "logging":
        return None
    crumb.pop("message", None)
    data = crumb.get("data")
    if isinstance(data, Mapping):
        kept = {k: v for k, v in data.items() if k in _KEPT_BREADCRUMB_DATA}
        if isinstance(kept.get("url"), str):
            kept["url"] = scrub_url(kept["url"])
        crumb["data"] = kept
    else:
        crumb.pop("data", None)
    return crumb


def before_breadcrumb(crumb: dict[str, Any], _hint: object = None) -> dict[str, Any] | None:
    """Journaux jetés ; requêtes HTTP gardées sans message, sans paramètres, identifiants
    masqués."""
    return _scrub_breadcrumb(crumb)


def before_send(event: dict[str, Any], _hint: object = None) -> dict[str, Any]:
    """L'unique filtre avant envoi : voir le docstring du module."""
    for key in ("extra", "user", "server_name", "modules", "tags", "fingerprint"):
        event.pop(key, None)
    if isinstance(event.get("request"), dict):
        event["request"] = _scrub_request(event["request"])
    contexts = event.get("contexts")
    if isinstance(contexts, Mapping):
        event["contexts"] = {k: v for k, v in contexts.items() if k in _KEPT_CONTEXTS}
    for exception in (event.get("exception") or {}).get("values") or []:
        if isinstance(exception.get("value"), str):
            exception["value"] = _truncate(scrub_text(exception["value"]))
        _scrub_frames(exception)
    for thread in (event.get("threads") or {}).get("values") or []:
        _scrub_frames(thread)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        logentry.pop("params", None)
        if isinstance(logentry.get("message"), str):
            logentry["message"] = _truncate(scrub_text(logentry["message"]))
        logentry.pop("formatted", None)
    if isinstance(event.get("message"), str):
        event["message"] = _truncate(scrub_text(event["message"]))
    if isinstance(event.get("transaction"), str):
        event["transaction"] = scrub_text(event["transaction"])
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, Mapping) else crumbs
    if isinstance(values, list):
        kept = [c for c in (_scrub_breadcrumb(dict(c)) for c in values) if c is not None]
        event["breadcrumbs"] = {"values": kept}
    return event


def _import_sdk() -> Any:
    return importlib.import_module("sentry_sdk")


def init_monitoring(import_sdk: Callable[[], Any] = _import_sdk) -> bool:
    """Active le suivi si `OPENPLAN_SENTRY_DSN` est défini et le SDK installé ; sinon ne fait
    rien (et n'importe rien). Rend True quand le suivi est actif."""
    value = dsn()
    if value is None:
        return False
    if _state["active"]:
        return True
    try:
        sdk = import_sdk()
    except ImportError:
        print(f"{DSN_ENV} est défini mais sentry-sdk n'est pas installé : suivi inactif.")
        return False
    sdk.init(
        dsn=value,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        environment=os.environ.get(ENV_ENV, "").strip() or "production",
        release=release(),
        server_name="",
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
    )
    _state["active"] = True
    return True


def send_test_error(import_sdk: Callable[[], Any] = _import_sdk) -> str | None:
    """Envoie une erreur volontaire (vérification de la réception) ; rend son identifiant."""
    if not init_monitoring(import_sdk):
        return None
    sdk = import_sdk()
    try:
        raise RuntimeError(TEST_MESSAGE)
    except RuntimeError as exc:
        event_id = sdk.capture_exception(exc)
    sdk.flush(timeout=10)
    return str(event_id) if event_id else None
