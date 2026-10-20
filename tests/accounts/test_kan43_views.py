"""KAN-43 : une vue de l'espace de travail par personne. Deux membres, deux vues ; rien
d'enregistré : le cadrage de départ (sauf le propriétaire, qui reprend l'ancienne vue commune) ;
le lecteur garde la sienne ; un non-membre, 404 ; un membre retiré perd la sienne ; le tableau
n'est pas touché, personne n'est prévenu, rien au journal ; l'ancien PATCH {view} est rangé comme
ma vue ; valeurs absurdes bornées ; le mode local."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.board import BOARD_FILE
from openplan.pipeline.board_views import VIEWS_FILE
from openplan.pipeline.events import EVENTS_FILE
from tests.accounts.helpers import sign_in
from tests.accounts.test_b2a_live import _client, _ws
from tests.accounts.test_kan51_comments import _team

DEFAULT = {"x": 0.0, "y": 0.0, "zoom": 1.0}


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _view(client: TestClient, pid: str) -> object:
    return client.get(f"/api/projects/{pid}/view").json()


def test_one_view_per_member_reader_included(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, service = _team(tmp_path, migrated_url)
    project_dir = owner.app.state.store.project_dir(__import__("uuid").UUID(pid))  # type: ignore[attr-defined]
    owner.get(f"/api/projects/{pid}/board")
    board_before = (project_dir / BOARD_FILE).read_bytes()
    # Rien d'enregistré : null, et le tableau me rend le cadrage de départ.
    assert _view(m["editor"], pid) is None
    assert m["editor"].get(f"/api/projects/{pid}/board").json()["view"] == DEFAULT
    # Chacun la sienne, le lecteur compris ; le tableau n'est pas touché.
    put = owner.put(f"/api/projects/{pid}/view", json={"x": 10, "y": 20, "zoom": 2})
    assert put.status_code == 200, put.text
    assert (
        m["viewer"]
        .put(f"/api/projects/{pid}/view", json={"x": -5, "y": 7, "zoom": 0.5})
        .status_code
        == 200
    )
    assert _view(owner, pid) == {"x": 10.0, "y": 20.0, "zoom": 2.0}
    assert _view(m["viewer"], pid) == {"x": -5.0, "y": 7.0, "zoom": 0.5}
    assert _view(m["editor"], pid) is None
    assert m["viewer"].get(f"/api/projects/{pid}/board").json()["view"]["zoom"] == 0.5
    assert (project_dir / BOARD_FILE).read_bytes() == board_before
    assert not (project_dir / EVENTS_FILE).read_text(encoding="utf-8").count("board.changed")
    # Valeurs absurdes : bornées (zoom dans la plage de l'interface, x et y finis).
    owner.put(f"/api/projects/{pid}/view", json={"x": 1e30, "y": -1e30, "zoom": 1000})
    assert _view(owner, pid) == {"x": 1e7, "y": -1e7, "zoom": 4.0}
    # Un non-membre : 404 ; un membre retiré perd sa vue.
    stranger = _client(owner.app)
    sign_in(stranger, owner.app.state.accounts.mailer, "x@example.org")  # type: ignore[attr-defined]
    assert stranger.put(f"/api/projects/{pid}/view", json=DEFAULT).status_code == 404
    gone = service.user_by_email("viewer@example.org")
    assert owner.delete(f"/api/projects/{pid}/members/{gone.id}").status_code == 200
    views = json.loads((project_dir / VIEWS_FILE).read_text(encoding="utf-8"))["views"]
    assert str(gone.id) not in views and len(views) == 1


def test_my_view_is_not_announced(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    with _ws(m["editor"], pid) as his:
        his.receive_json()  # hello
        owner.put(f"/api/projects/{pid}/view", json={"x": 1, "y": 2, "zoom": 1.5})
        # Ancien onglet : un PATCH avec la vue seule ; rangée comme MA vue, rien d'annoncé.
        owner.patch(f"/api/projects/{pid}/board/items", json={"view": {"x": 3, "y": 4, "zoom": 1}})
        his.send_text('{"type":"ping"}')
        assert his.receive_json() == {"type": "pong"}  # rien d'autre n'est arrivé avant
    assert _view(owner, pid) == {"x": 3.0, "y": 4.0, "zoom": 1.0}
    assert _view(m["editor"], pid) is None


def test_legacy_common_view_goes_to_the_owner_only(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    owner.get(f"/api/projects/{pid}/board")
    project_dir = owner.app.state.store.project_dir(__import__("uuid").UUID(pid))  # type: ignore[attr-defined]
    path = project_dir / BOARD_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["view"] = {"x": 120.0, "y": -40.0, "zoom": 0.8}  # la vue commune d'avant KAN-43
    path.write_text(json.dumps(data), encoding="utf-8")
    assert owner.get(f"/api/projects/{pid}/board").json()["view"] == data["view"]
    assert m["editor"].get(f"/api/projects/{pid}/board").json()["view"] == DEFAULT
    assert not (project_dir / VIEWS_FILE).exists()  # une lecture n'écrit rien
    # Dès qu'il enregistre la sienne, c'est elle.
    owner.put(f"/api/projects/{pid}/view", json={"x": 1, "y": 1, "zoom": 1})
    assert owner.get(f"/api/projects/{pid}/board").json()["view"] == {
        "x": 1.0,
        "y": 1.0,
        "zoom": 1.0,
    }


def test_local_mode_is_the_same(tmp_path: Path) -> None:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    assert local.get(f"/api/projects/{pid}/board").json()["view"] == DEFAULT
    local.put(f"/api/projects/{pid}/view", json={"x": 5, "y": 6, "zoom": 1.25})
    assert local.get(f"/api/projects/{pid}/board").json()["view"] == {
        "x": 5.0,
        "y": 6.0,
        "zoom": 1.25,
    }
    assert _view(local, pid) == {"x": 5.0, "y": 6.0, "zoom": 1.25}
    # L'archive du projet n'emporte pas les vues.
    import io
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(local.get(f"/api/projects/{pid}/exports/archive").content))
    assert VIEWS_FILE not in archive.namelist()
