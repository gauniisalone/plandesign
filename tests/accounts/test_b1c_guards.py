"""B1c : droits route par route, tout refusé par défaut, fichiers servis par Storage."""

from __future__ import annotations

import io
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.accounts.service import AccountService
from openplan.api.app import create_app
from openplan.api.guards import GLOBAL_ROUTES, PROJECT_ROUTES, ProjectMember, route_guard
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in
from tests.unit.test_security import _api_routes

ROUTES_DIR = Path(__file__).parents[2] / "openplan" / "api" / "routes"


def _is_guard(call: object) -> bool:
    return call is route_guard or isinstance(call, ProjectMember)


def _walk(routes: list[object], prefix: str, inherited: list[object]) -> list[Guarded]:
    """Routes de l'application avec les gardes héritées de leur inclusion (FastAPI récent
    garde les routeurs inclus dans des `_IncludedRouter` avec leur contexte d'inclusion)."""
    out: list[Guarded] = []
    for r in routes:
        if isinstance(r, APIRoute):
            path = r.path if r.path.startswith(prefix) else prefix + r.path
            out.append((path, r, inherited))
        elif hasattr(r, "original_router"):
            ctx = r.include_context
            deps = [d.dependency for d in ctx.dependencies]
            out.extend(_walk(list(r.original_router.routes), prefix + ctx.prefix, inherited + deps))
        elif hasattr(r, "routes"):
            out.extend(_walk(list(r.routes), prefix + getattr(r, "prefix", ""), inherited))
    return out


Guarded = tuple[str, APIRoute, list[object]]


def _guards(route: APIRoute, inherited: list[object]) -> list[object]:
    found = [c for c in inherited if _is_guard(c)]
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if _is_guard(dep.call):
            found.append(dep.call)
        stack.extend(dep.dependencies)
    return found


def _all_routes(apps: list[FastAPI]) -> dict[tuple[str, str], tuple[APIRoute, list[object]]]:
    out: dict[tuple[str, str], tuple[APIRoute, list[object]]] = {}
    for app in apps:
        seen = _walk(list(app.routes), "", [])
        assert len(seen) == len(_api_routes(app))
        for path, route, inherited in seen:
            for method in route.methods or ():
                out[(method, path)] = (route, inherited)
    return out


def test_every_api_route_has_a_guard_and_a_declared_rule(tmp_path: Path, migrated_url: str) -> None:
    """Audit automatique (note 17, § 4) : une route sans garde, une route absente des tables,
    ou une entrée de table sans route font échouer ce test."""
    local = create_app(settings=Settings(data_dir=tmp_path / "local"))
    accounts, service, _ = accounts_client(tmp_path / "comptes", migrated_url)
    routes = _all_routes([local, accounts.app])  # type: ignore[list-item]
    api = {k: r for k, r in routes.items() if k[1].startswith("/api")}
    assert len(api) > 120, "le parcours doit voir toutes les routes, routeurs inclus"
    problems: list[str] = []
    for (method, path), (route, inherited) in sorted(api.items(), key=lambda kv: kv[0]):
        guards = _guards(route, inherited)
        if not guards:
            problems.append(f"{method} {path} : sans garde")
        key = (method, path)
        if "{project_id}" in path and key not in PROJECT_ROUTES:
            if not any(isinstance(g, ProjectMember) for g in guards):
                problems.append(f"{method} {path} : route de projet sans rôle déclaré")
        elif "{project_id}" not in path and key not in GLOBAL_ROUTES:
            problems.append(f"{method} {path} : ni rôle ni exemption listée")
    for key in [*PROJECT_ROUTES, *GLOBAL_ROUTES]:
        if key not in api:
            problems.append(f"{key} : dans une table mais aucune route")
    assert not problems, "\n".join(problems)
    service.engine.dispose()


def test_project_files_are_served_only_through_storage() -> None:
    """Aucune route ne sert un fichier par elle-même : `served_file` (Storage + garde)
    seulement ; le catalogue (ressources du paquet) est la seule exception."""
    offenders = [
        p.name
        for p in ROUTES_DIR.glob("*.py")
        if p.name != "catalog.py" and re.search(r"FileResponse|StaticFiles", p.read_text())
    ]
    assert offenders == []


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (180, 160, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def team(tmp_path: Path, migrated_url: str) -> tuple[dict[str, TestClient], AccountService, UUID]:
    owner, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    clients = {"owner": owner}
    sign_in(owner, mailer, "owner@example.org")
    for name in ("viewer", "commenter", "editor", "stranger"):
        client = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        sign_in(client, mailer, f"{name}@example.org")
        clients[name] = client
    created = owner.post("/api/projects", json={"name": "Grenier"})
    assert created.status_code == 201, created.text
    project_id = UUID(created.json()["id"])
    owner_user = service.user_by_email("owner@example.org")
    assert owner_user is not None
    for role in ("viewer", "commenter", "editor"):
        user = service.user_by_email(f"{role}@example.org")
        assert user is not None
        service.set_member(project_id, user.id, role, added_by=owner_user.id)
    return clients, service, project_id


def test_each_role_on_a_sample_of_routes(
    team: tuple[dict[str, TestClient], AccountService, UUID],
) -> None:
    clients, service, pid = team
    base = f"/api/projects/{pid}"

    def code(who: str, method: str, url: str, **kw: object) -> int:
        return clients[who].request(method, url, **kw).status_code  # type: ignore[arg-type]

    # Lecture : tout membre ; un non-membre reçoit la même 404 qu'un projet inexistant.
    for who in ("owner", "editor", "commenter", "viewer"):
        assert code(who, "GET", base) == 200, who
        assert code(who, "GET", f"{base}/board") == 200, who
    stranger = clients["stranger"].get(base)
    missing = clients["stranger"].get(f"/api/projects/{uuid4()}")
    assert stranger.status_code == missing.status_code == 404
    assert stranger.json()["detail"].startswith("projet inconnu")
    assert code("stranger", "GET", "/api/projects/pas-un-uuid") == 404
    # La liste ne montre que les projets dont on est membre.
    assert [p["id"] for p in clients["viewer"].get("/api/projects").json()] == [str(pid)]
    assert clients["stranger"].get("/api/projects").json() == []
    # Modification : éditeur et propriétaire ; lecteur et commentateur 403 ; inconnu 404.
    measure = {
        "kind": "distance",
        "value_m": 3.1,
    }
    for who, expected in (("viewer", 403), ("commenter", 403), ("stranger", 404)):
        assert code(who, "POST", f"{base}/undo") == expected, who
        upload = {"file": ("p.jpg", _jpeg())}
        assert code(who, "POST", f"{base}/images", files=upload) == expected, who
        assert code(who, "POST", f"{base}/measurements", json=measure) == expected, who
    rename = clients["editor"].patch(base, json={"name": "Grenier aménagé"})
    assert rename.status_code == 200, rename.text
    uploaded = clients["editor"].post(f"{base}/images", files={"file": ("p.jpg", _jpeg())})
    assert uploaded.status_code == 201, uploaded.text
    image_id = uploaded.json()["image"]["id"]
    # Fichiers : servis par Storage après la garde, à tout membre, à personne d'autre.
    for who in ("viewer", "commenter"):
        served = clients[who].get(f"{base}/images/{image_id}/file")
        assert served.status_code == 200 and served.content[:2] == b"\xff\xd8", who
    assert code("stranger", "GET", f"{base}/images/{image_id}/file") == 404
    assert code("viewer", "GET", f"{base}/thumbnail") == 200
    # Confidentialité et suppression : propriétaire seulement.
    privacy = clients["owner"].get(base).json()["privacy"]
    privacy["design_journal"] = not privacy["design_journal"]
    assert code("editor", "PATCH", base, json={"privacy": privacy}) == 403
    assert code("owner", "PATCH", base, json={"privacy": privacy}) == 200
    assert code("editor", "DELETE", f"{base}/journal") == 403
    assert code("editor", "DELETE", base) == 403
    assert code("owner", "DELETE", base) == 204
    assert code("owner", "GET", base) == 404
    owner_user = service.user_by_email("owner@example.org")
    assert owner_user is not None and service.project_ids_of(owner_user.id) == set()


def test_server_settings_are_for_admins_and_unknown_routes_are_refused(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(client, mailer, "marie@example.org")
    assert client.get("/api/home/settings").status_code == 200
    assert client.put("/api/home/settings", json={"gemini_text": True}).status_code == 403
    assert client.get("/api/doctor").status_code == 403
    assert client.get("/api/catalog").status_code == 200
    # Une route qui n'est dans aucune table est refusée, même à une session valide.
    app = client.app
    assert isinstance(app, FastAPI)
    app.add_api_route("/api/nouvelle", lambda: {"ok": True}, dependencies=[Depends(route_guard)])
    assert client.get("/api/nouvelle").status_code == 403
    admin = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(admin, mailer, ADMIN)
    assert admin.get("/api/doctor").status_code == 200
    assert admin.put("/api/home/settings", json={"gemini_text": False}).status_code == 200
    service.engine.dispose()
