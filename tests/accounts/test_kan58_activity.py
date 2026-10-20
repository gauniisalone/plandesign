"""KAN-58 : le journal d'activité d'un projet. Le détail des gestes sur l'espace de travail
(posé, modifié, retiré, et le nom de l'élément quand il n'y en a qu'un), le libellé des gestes
sur le plan (annuler compris), une vue seule qui n'est pas une modification, tout le journal
(mes gestes, résolu, rouvert), les filtres, les pages, les droits (lecteur compris) et le mode
local identique."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.accounts.helpers import sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan51_comments import _team, _thread


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _activity(client: TestClient, pid: str, **params: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/projects/{pid}/activity", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _note(text: str) -> dict[str, Any]:
    return {"id": str(uuid4()), "kind": "note", "x": 0, "y": 0, "w": 200, "h": 120, "text": text}


def _local(tmp_path: Path) -> tuple[TestClient, str]:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    local.get(f"/api/projects/{pid}/board")  # mise en page de départ (pas un geste)
    return local, pid


def test_board_detail_and_a_view_is_not_a_change(tmp_path: Path) -> None:
    local, pid = _local(tmp_path)
    url = f"/api/projects/{pid}/board/items"
    assert _activity(local, pid) == []  # la mise en page de départ n'est pas un geste
    sofa = _note("Canapé")
    local.patch(url, json={"set": [sofa]})
    [line] = _activity(local, pid)
    assert line["type"] == "board.changed" and line["mine"] is True
    assert (line["added"], line["changed"], line["removed"]) == (1, 0, 0)
    assert line["label"] == "Canapé" and line["item_kind"] == "note"
    assert line["item_id"] == sofa["id"] and line["actor_name"] == "Vous"
    # Zoomer ou déplacer la vue : rien.
    local.patch(url, json={"view": {"x": 10, "y": 20, "zoom": 1.5}})
    assert len(_activity(local, pid)) == 1
    # Le même élément déplacé puis retiré : une ligne regroupée, l'objet reste.
    local.patch(url, json={"set": [{**sofa, "x": 300}]})
    local.patch(url, json={"delete": [sofa["id"]]})
    [line] = _activity(local, pid)
    assert (line["count"], line["added"], line["changed"], line["removed"]) == (3, 1, 1, 1)
    assert line["label"] == "Canapé"
    # Deux éléments dans un même geste : le détail, sans objet.
    local.patch(url, json={"set": [_note("Lampe"), _note("Tapis")]})
    [line] = _activity(local, pid)
    assert line["count"] == 4 and line["added"] == 3 and line["label"] is None


def test_an_automatic_board_sync_is_not_a_gesture(tmp_path: Path) -> None:
    """`board.json` réécrit hors des routes de l'espace de travail (synchronisation
    automatique) : rien au journal. Seul `project.json` y compte, comme modification du plan."""
    from uuid import UUID

    from openplan.api import change_events
    from openplan.pipeline.events import EVENTS_FILE

    project_dir = tmp_path / "p"
    project_dir.mkdir()
    (project_dir / "board.json").write_text("{}", encoding="utf-8")
    (project_dir / "project.json").write_text('{"history": [], "history_cursor": 0}')
    pid, path = UUID(int=1), "/api/projects/x/levels/y/starter"
    before = change_events.stamps(project_dir)
    (project_dir / "board.json").write_text('{"items": []}', encoding="utf-8")
    assert change_events.record(project_dir, pid, "fanny", path, before) is False
    assert not (project_dir / EVENTS_FILE).exists()
    before = change_events.stamps(project_dir)
    (project_dir / "project.json").write_text(
        '{"history": [{"label": "Plan de départ"}], "history_cursor": 1}', encoding="utf-8"
    )
    assert change_events.record(project_dir, pid, "fanny", path, before) is True
    assert '"label":"Plan de départ"' in (project_dir / EVENTS_FILE).read_text(encoding="utf-8")


def test_plan_label_undo_and_filters(tmp_path: Path) -> None:
    local, pid = _local(tmp_path)
    level = next(iter(local.get(f"/api/projects/{pid}").json()["levels"]))
    op = {"op": "add_wall", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 0}}
    assert local.post(f"/api/projects/{pid}/levels/{level}/ops", json=op).status_code == 200
    [line] = _activity(local, pid, kind="plan")
    assert line["type"] == "plan.changed" and line["level_id"] == level
    assert line["label"]  # le libellé du geste, tiré de l'historique
    done = line["label"]
    # Annuler : une nouvelle modification, qui le dit (regroupée avec la précédente, 10 min).
    assert local.post(f"/api/projects/{pid}/undo").status_code == 200
    events = (tmp_path / "local").rglob("events.jsonl")
    raw = next(events).read_text(encoding="utf-8")
    assert f"Annulé : {done}" in raw
    # Filtres : par sorte, par personne.
    local.patch(f"/api/projects/{pid}/board/items", json={"set": [_note("Canapé")]})
    assert {x["type"] for x in _activity(local, pid, kind="board")} == {"board.changed"}
    assert {x["type"] for x in _activity(local, pid, kind="plan")} == {"plan.changed"}
    assert len(_activity(local, pid, who="local")) == 2
    assert _activity(local, pid, who="quelqu'un") == []


def test_whole_journal_for_every_member_reader_included(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    tid = _thread(m["commenter"], pid).json()["id"]
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/resolve")
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/reopen")
    owner.patch(f"/api/projects/{pid}/board/items", json={"set": [_note("Canapé")]})
    # Le lecteur lit tout, y compris ce que la cloche ne notifie pas (résolu, rouvert), et
    # les gestes du propriétaire.
    seen = _activity(m["viewer"], pid)
    types = [x["type"] for x in seen]
    assert types[:4] == ["board.changed", "comment.reopened", "comment.resolved", "comment.created"]
    assert types.count("member.joined") == 3
    assert seen[0]["actor_name"] == "admin" and seen[0]["mine"] is False
    # Le propriétaire voit ses propres gestes ; un inconnu, rien (404 sans révéler le projet).
    assert _activity(owner, pid)[0]["mine"] is True
    stranger = _client(owner.app)
    sign_in(stranger, owner.app.state.accounts.mailer, "x@example.org")  # type: ignore[attr-defined]
    assert stranger.get(f"/api/projects/{pid}/activity").status_code == 404
    # Le journal ne s'écrit que par le serveur : pas de route d'écriture.
    assert owner.post(f"/api/projects/{pid}/activity", json={}).status_code in (404, 405)


def test_alternating_plan_and_board_gestures_group_per_kind(tmp_path: Path) -> None:
    """Les pages de 50 et « Voir plus » sont celles de la cloche (tests/unit/test_bell.py)."""
    local, pid = _local(tmp_path)
    level = next(iter(local.get(f"/api/projects/{pid}").json()["levels"]))
    for i in range(30):  # plan et tableau alternés
        local.post(
            f"/api/projects/{pid}/levels/{level}/ops",
            json={"op": "add_wall", "start": {"x": i, "y": 0}, "end": {"x": i, "y": 3}},
        )
        local.patch(f"/api/projects/{pid}/board/items", json={"set": [_note(f"n{i}")]})
    first = local.get(f"/api/projects/{pid}/activity").json()
    # Plan et tableau : deux lignes regroupées seulement (même auteur, moins de 10 min).
    assert len(first["items"]) == 2 and first["next"] is None
    assert sorted(x["count"] for x in first["items"]) == [30, 30]
