"""B2d (KAN-11) : bail d'édition du plan. Prise implicite au premier geste et prolongation,
409 quand une autre personne le tient, expiration, libération à la déconnexion, « Demander la
main » relayé au seul tenant, écritures du système jamais bloquées, curseur en mètres."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from openplan.api.accounts.lease import LEASE_S, LEASED_ROUTES, PROJECT_WIDE
from openplan.api.guards import PROJECT_ROUTES, L
from tests.accounts.helpers import ADMIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client, _ws


def _pair(tmp_path: Path, url: str) -> tuple[TestClient, TestClient, str, str]:
    """Le propriétaire et une éditrice sur le même projet ; renvoie aussi l'id du plan."""
    owner, _service, mailer = accounts_client(tmp_path, url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Plan à deux"}).json()
    pid = project["id"]
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()["token"]
    editor = _client(owner.app)
    sign_in(editor, mailer, "gilles@example.org")
    assert editor.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    level = next(iter(project["levels"]))
    return owner, editor, pid, level


def _ops(client: TestClient, pid: str, level: str) -> Any:
    # Un pas vide : il passe par la garde (et le bail) sans rien changer.
    return client.post(f"/api/projects/{pid}/levels/{level}/ops", json={})


def _next(ws: Any) -> dict[str, Any]:
    """Le prochain message, sans les avis « changed » (une écriture les envoie aussi)."""
    while True:
        message: dict[str, Any] = ws.receive_json()
        if message["type"] != "changed":
            return message


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_first_gesture_takes_the_lease_and_the_other_gets_409(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, editor, pid, level = _pair(tmp_path, migrated_url)
    clock = _Clock()
    owner.app.state.leases.clock = clock  # type: ignore[attr-defined]
    # Seul : aucune différence (la garde laisse passer).
    assert _ops(owner, pid, level).status_code == 200
    held = owner.app.state.leases.holder(UUID(pid), level)  # type: ignore[attr-defined]
    assert held is not None and held.name == "admin"
    # L'autre : 409 avec le nom, sur le plan comme sur annuler / rétablir.
    refused = _ops(editor, pid, level)
    assert refused.status_code == 409 and refused.json()["detail"] == "admin modifie ce plan"
    assert editor.post(f"/api/projects/{pid}/undo").status_code == 409
    assert editor.post(f"/api/projects/{pid}/levels/{level}/lease").status_code == 409
    # Chaque geste prolonge : juste avant l'expiration, un geste, et le bail tient encore.
    clock.now += LEASE_S - 1
    assert _ops(owner, pid, level).status_code == 200
    clock.now += LEASE_S - 1
    assert _ops(editor, pid, level).status_code == 409
    # Deux minutes sans geste : « Prendre la main » réussit.
    clock.now += LEASE_S + 1
    taken = editor.post(f"/api/projects/{pid}/levels/{level}/lease")
    assert taken.status_code == 200 and taken.json()["holder"]["name"] == "gilles"
    assert _ops(owner, pid, level).status_code == 409


def test_giving_back_and_disconnecting_free_the_plan(tmp_path: Path, migrated_url: str) -> None:
    owner, editor, pid, level = _pair(tmp_path, migrated_url)
    assert _ops(owner, pid, level).status_code == 200
    # Quitter l'onglet Plan : le bail est rendu.
    assert owner.delete(f"/api/projects/{pid}/levels/{level}/lease").status_code == 204
    assert _ops(editor, pid, level).status_code == 200  # Gilles le prend
    # Gilles perd sa dernière connexion : le plan est libre, et c'est annoncé.
    with _ws(owner, pid) as watch:
        watch.receive_json()
        with _ws(editor, pid) as his:
            hello = his.receive_json()
            assert hello["leases"][level]["holder"]["name"] == "gilles"
            watch.receive_json()  # son arrivée
        events = [watch.receive_json(), watch.receive_json()]
        assert {"type": "lease", "level_id": level, "holder": None} in events
    assert _ops(owner, pid, level).status_code == 200


def test_asking_for_the_plan_tells_the_holder_only(tmp_path: Path, migrated_url: str) -> None:
    owner, editor, pid, level = _pair(tmp_path, migrated_url)
    with _ws(owner, pid) as mine, _ws(editor, pid) as his:
        _next(mine)
        _next(his)
        mine.receive_json()  # son arrivée
        assert _ops(owner, pid, level).status_code == 200
        taken = _next(mine)
        assert taken["type"] == "lease" and taken["holder"]["name"] == "admin"
        assert _next(his) == taken
        asked = editor.post(f"/api/projects/{pid}/levels/{level}/lease/request")
        assert asked.status_code == 204
        assert _next(mine) == {"type": "lease.request", "level_id": level, "from": "gilles"}
        his.send_text('{"type":"ping"}')
        assert _next(his) == {"type": "pong"}  # lui n'a rien reçu d'autre
        # Personne n'arrache la main, pas même le propriétaire : elle se rend.
        assert owner.delete(f"/api/projects/{pid}/levels/{level}/lease").status_code == 204
        assert _next(his) == {"type": "lease", "level_id": level, "holder": None}


def test_plan_cursors_carry_the_plan_id(tmp_path: Path, migrated_url: str) -> None:
    owner, editor, pid, level = _pair(tmp_path, migrated_url)
    with _ws(owner, pid) as mine, _ws(editor, pid) as his:
        me = mine.receive_json()["you"]["user_id"]
        his.receive_json()
        mine.receive_json()
        mine.send_json({"type": "cursor", "x": 2.5, "y": 1.25, "level_id": level})
        assert his.receive_json() == {
            "type": "cursor",
            "user_id": me,
            "x": 2.5,
            "y": 1.25,
            "level_id": level,
        }
        mine.send_json({"type": "cursor", "x": 1, "y": 1, "level_id": 7})
        his.send_text('{"type":"ping"}')
        assert his.receive_json() == {"type": "pong"}  # id de plan invalide : ignoré


def test_system_writes_are_never_leased_and_every_leased_route_is_guarded() -> None:
    # Les calculs (chemin automatique, détection, rendus) n'écrivent pas par ces routes.
    for route in ("/autopilot", "/images/{image_id}/autoplan", "/detect", "/render/all"):
        assert ("POST", L + route) not in LEASED_ROUTES
    assert set(PROJECT_ROUTES) >= LEASED_ROUTES | PROJECT_WIDE


def test_local_mode_has_no_lease(tmp_path: Path) -> None:
    from openplan.api.app import create_app
    from openplan.api.settings import Settings

    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    project = local.post("/api/projects", json={"name": "Seul"}).json()
    level = next(iter(project["levels"]))
    assert (
        local.post(f"/api/projects/{project['id']}/levels/{level}/ops", json={}).status_code == 200
    )
    assert local.post(f"/api/projects/{project['id']}/levels/{level}/lease").status_code == 404
