"""Sécurité de l'API exposée (4.6) : jetons nommés, journal des accès, en-têtes, une tâche
lourde à la fois par utilisateur, refus de démarrer exposé sans jeton."""

from __future__ import annotations

import inspect
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openplan.api.app import TokenMissingError, create_app
from openplan.api.jobs import JobBusyError, JobRegistry
from openplan.api.security import (
    COOKIE_DENIED,
    CSP,
    READ_COOKIE,
    AccessLog,
    cookie_allowed,
    match_read_cookie,
    match_token,
    read_cookie_value,
    read_secret,
)
from openplan.api.settings import Settings, parse_tokens
from openplan.scene.history import USER_ACTOR, check_policy, is_user, user_actor
from tests.factories import rect_room_project

TOKEN_A = "a" * 24
TOKEN_B = "b" * 24


def test_parse_tokens_accepts_lists_and_refuses_bad_entries() -> None:
    assert parse_tokens(None) == {}
    assert parse_tokens(f"augus:{TOKEN_A}, marie:{TOKEN_B}") == {"augus": TOKEN_A, "marie": TOKEN_B}
    assert parse_tokens(f"augus:{TOKEN_A}\nmarie:{TOKEN_B}") == {"augus": TOKEN_A, "marie": TOKEN_B}
    for bad in ("sansdeuxpoints", "court:abc", f":{TOKEN_A}", f"nom avec espace:{TOKEN_A}"):
        with pytest.raises(ValueError, match="OPENPLAN_API_TOKENS"):
            parse_tokens(bad)
    settings = Settings(api_token="legacy-token-1234567", api_tokens={"augus": TOKEN_A})
    assert settings.tokens == {"augus": TOKEN_A, "invité": "legacy-token-1234567"}
    env = {"OPENPLAN_API_TOKENS": f"augus:{TOKEN_A}", "OPENPLAN_HOST": "0.0.0.0"}
    assert Settings.from_env(env, dotenv=None).tokens == {"augus": TOKEN_A}


def test_match_token_is_by_name_and_only_with_bearer() -> None:
    tokens = {"augus": TOKEN_A, "marie": TOKEN_B}
    assert match_token(tokens, f"Bearer {TOKEN_B}") == "marie"
    assert match_token(tokens, f"bearer  {TOKEN_A} ") == "augus"
    assert match_token(tokens, f"Basic {TOKEN_A}") is None
    assert match_token(tokens, "Bearer " + "c" * 24) is None
    assert match_token(tokens, "") is None


def test_named_user_has_user_rights_in_history() -> None:
    assert is_user(USER_ACTOR) and is_user(user_actor("marie")) and not is_user("pipeline:x")
    project = rect_room_project()
    # Un utilisateur nommé peut modifier une mesure (interdit à un pipeline).
    changed = project.model_copy(update={"measurements": {}})
    check_policy(project, changed, user_actor("marie"))
    with pytest.raises(Exception, match="mesures"):
        check_policy(project, changed, "pipeline:floorplan")


class _Done(BaseModel):
    ok: bool = True


def test_one_heavy_job_at_a_time_per_owner() -> None:
    registry = JobRegistry()
    gate = threading.Event()

    def slow() -> _Done:
        gate.wait(5)
        return _Done()

    first = registry.submit("render", slow, device="cpu", expected_s=30, message="", owner="user:a")
    with pytest.raises(JobBusyError, match="une tâche lourde à la fois") as exc:
        registry.submit("render", slow, device="cpu", expected_s=30, message="", owner="user:a")
    assert exc.value.retry_after_s > 0
    # Un autre utilisateur n'est pas bloqué (il attend le fil, sans refus).
    other = registry.submit("render", slow, device="cpu", expected_s=30, message="", owner="user:b")
    gate.set()
    for _ in range(200):
        if registry.get(first.id).status == "done" and registry.get(other.id).status == "done":
            break
        time.sleep(0.01)
    assert registry.get(first.id).status == "done"
    again = registry.submit("render", slow, device="cpu", expected_s=30, message="", owner="user:a")
    assert again.kind == "render"
    registry.shutdown()


def test_access_log_has_no_token_and_no_content(tmp_path: Path) -> None:
    log = AccessLog(tmp_path / "x" / "access.jsonl")
    log.write(actor="user:marie", method="POST", path="/api/projects", status=200, ms=12.4)
    lines = (tmp_path / "x" / "access.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert entry["actor"] == "user:marie" and entry["status"] == 200 and entry["ms"] == 12
    assert set(entry) == {"ts", "actor", "method", "path", "status", "ms"}


_WRITES = re.compile(
    r"store\.save\(|\.write_text\(|\.write_bytes\(|\.mkdir\(|history\.commit\(|apply_op\(|"
    r"jobs\.submit|shutil\.|\.unlink\(|\.rename\(|journal\.record\(|home_settings\.save\("
)


def _api_routes(app: object) -> list[tuple[str, APIRoute]]:
    """Toutes les routes de l'application, routeurs inclus (FastAPI récent les garde dans
    des `_IncludedRouter` avec `original_router`)."""

    def walk(routes: list[object], prefix: str) -> list[tuple[str, APIRoute]]:
        out: list[tuple[str, APIRoute]] = []
        for r in routes:
            if isinstance(r, APIRoute):
                out.append((r.path if r.path.startswith(prefix) else prefix + r.path, r))
            elif hasattr(r, "original_router"):
                sub = getattr(getattr(r, "include_context", None), "prefix", "") or "/api"
                out.extend(walk(list(r.original_router.routes), prefix + sub))
            elif hasattr(r, "routes"):
                out.extend(walk(list(r.routes), prefix + getattr(r, "prefix", "")))
        return out

    return walk(list(app.routes), "")  # type: ignore[attr-defined]


def _template_to_path(template: str) -> str:
    return re.sub(r"\{[^}]+\}", "0123", template)


def test_every_get_route_that_writes_is_denied_to_the_read_cookie(tmp_path: Path) -> None:
    """Audit automatique : le cookie de lecture ne vaut que pour GET/HEAD ; une route GET dont la
    source écrit (sauvegarde, fichier, journal, tâche) doit être dans COOKIE_DENIED."""
    app = create_app(settings=Settings(data_dir=tmp_path / "data"))
    seen = 0
    writers: list[str] = []
    for path, route in _api_routes(app):
        if not path.startswith("/api") or not (set(route.methods or ()) & {"GET", "HEAD"}):
            continue
        seen += 1
        if _WRITES.search(inspect.getsource(route.endpoint)):
            writers.append(path)
            assert not cookie_allowed("GET", _template_to_path(path)), (
                f"{path} écrit mais accepterait le cookie : l'ajouter à COOKIE_DENIED"
            )
        else:
            assert cookie_allowed("GET", _template_to_path(path)), path
    assert seen > 40, "le parcours des routeurs inclus doit voir toutes les routes"
    assert len(writers) == len(COOKIE_DENIED) == 2, writers
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert not cookie_allowed(method, "/api/projects/0123")


def test_read_cookie_is_a_derived_value_bound_to_the_token(tmp_path: Path) -> None:
    secret = read_secret(tmp_path / "data")
    assert secret == read_secret(tmp_path / "data") and len(secret) == 64
    assert (tmp_path / "data" / "read_cookie.secret").stat().st_mode & 0o777 == 0o600
    tokens = {"marie": TOKEN_A}
    value = read_cookie_value(secret, "marie", TOKEN_A)
    assert TOKEN_A not in value and value.startswith("marie:")
    assert match_read_cookie(secret, tokens, value) == "marie"
    # Jeton changé ou retiré : le cookie ne vaut plus rien ; un autre secret non plus.
    assert match_read_cookie(secret, {"marie": TOKEN_B}, value) is None
    assert match_read_cookie(secret, {}, value) is None
    assert match_read_cookie(b"autre", tokens, value) is None
    for bad in ("", "marie", "marie:", "bob:" + value.partition(":")[2], value + "0"):
        assert match_read_cookie(secret, tokens, bad) is None


def test_read_cookie_reads_only_and_never_mutates(tmp_path: Path) -> None:
    """API exposée : POST /api/session pose le cookie (attributs stricts) ; avec le cookie seul,
    GET d'un projet passe, la liste (qui écrit) et tout POST rendent 401 ; DELETE l'efface ; en
    mode local, rien n'est posé."""
    marie = "marie-token-0123456789abcdef"
    exposed = Settings(data_dir=tmp_path / "data", host="0.0.0.0", api_tokens={"marie": marie})
    auth = {"Authorization": f"Bearer {marie}"}
    with TestClient(create_app(settings=exposed)) as c:
        assert c.post("/api/session").status_code == 401, "le cookie ne naît que du Bearer"
        opened = c.post("/api/session", headers=auth)
        assert opened.status_code == 200 and opened.json()["cookie"] is True
        assert opened.json()["name"] == "marie" and opened.json()["expires_in_s"] == 43200
        raw = opened.headers["set-cookie"]
        assert raw.startswith(f"{READ_COOKIE}=") and marie not in raw
        low = raw.lower()
        for attr in ("httponly", "secure", "samesite=lax", "path=/api", "max-age=43200"):
            assert attr in low, raw
        value = raw.split(";")[0].partition("=")[2]
        pid = c.post("/api/projects", json={"name": "de marie"}, headers=auth).json()["id"]
        # Le client de test ne garde pas un cookie Secure en http : posé à la main.
        c.cookies.set(READ_COOKIE, value)
        assert c.get(f"/api/projects/{pid}").status_code == 200
        assert c.get(f"/api/projects/{pid}/board").status_code == 200
        assert c.get("/api/projects").status_code == 401, "la liste écrit : Bearer seulement"
        refused = c.post("/api/projects", json={"name": "x"})
        assert refused.status_code == 401 and refused.headers["www-authenticate"] == "Bearer"
        assert c.patch(f"/api/projects/{pid}", json={"name": "y"}).status_code == 401
        assert c.delete(f"/api/projects/{pid}").status_code == 401
        assert c.post("/api/session").status_code == 401, "le cookie ne se renouvelle pas seul"
        c.cookies.set(READ_COOKIE, "marie:faux")
        assert c.get(f"/api/projects/{pid}").status_code == 401
        c.cookies.clear()
        assert c.get(f"/api/projects/{pid}").status_code == 401, "sans rien : refusé"
        closed = c.delete("/api/session", headers=auth)
        assert closed.status_code == 204 and "max-age=0" in closed.headers["set-cookie"].lower()
    # Le journal des accès attribue la lecture par cookie au même acteur, sans valeur.
    log = (tmp_path / "data" / "access.jsonl").read_text(encoding="utf-8")
    assert "user:marie" in log and value not in log and marie not in log
    # Jeton changé : le cookie d'avant ne vaut plus.
    rotated = Settings(data_dir=tmp_path / "data", host="0.0.0.0", api_tokens={"marie": TOKEN_B})
    with TestClient(create_app(settings=rotated)) as c:
        c.cookies.set(READ_COOKIE, value)
        assert c.get(f"/api/projects/{pid}").status_code == 401
    # Boucle locale : rien à poser, rien n'exige le cookie.
    with TestClient(create_app(settings=Settings(data_dir=tmp_path / "data"))) as c:
        r = c.post("/api/session")
        assert r.status_code == 200 and r.json() == {
            "exposed": False,
            "cookie": False,
            "name": None,
            "expires_in_s": 0,
        }
        assert "set-cookie" not in r.headers
        assert c.get(f"/api/projects/{pid}").status_code == 200


def test_headers_on_every_response_and_exposed_without_token_refused(tmp_path: Path) -> None:
    with TestClient(create_app(settings=Settings(data_dir=tmp_path / "data"))) as c:
        r = c.get("/api/health")
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "camera=(self)" in r.headers["permissions-policy"]
        body = r.json()
        assert body["exposed"] is False and body["tokens"] == 0 and "weights" in body
        assert body["device"] in ("cpu", "cuda") and isinstance(body["blender"], bool)
        # La documentation interactive charge un CDN : pas de CSP là, les autres en-têtes oui.
        docs = c.get("/docs")
        assert "content-security-policy" not in docs.headers and docs.headers["x-frame-options"]
    with pytest.raises(TokenMissingError, match="openplan token"):
        create_app(settings=Settings(data_dir=tmp_path / "data", host="0.0.0.0"))
