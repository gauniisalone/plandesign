"""T1 : la note Exigences du tableau. Le tableau porte toujours une note `requirements` (une
seule, rôle `prompt`) ; son contenu vit dans le projet : `pending_answers` et
`pending_instruction` par PATCH, lus par le chemin automatique, effacés quand il les applique ;
l'ancienne note de la demande (B1) devient cette note à sa place."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.board import sync_board
from openplan.scene.store import ProjectStore
from openplan.schemas.board import Board, BoardItem
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _client(tmp_path: Path, project: Project) -> TestClient:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))


def test_board_always_has_one_requirements_note(tmp_path: Path) -> None:
    project = rect_room_project(4.0, 3.0)
    client = _client(tmp_path, project)
    board = client.get(f"/api/projects/{project.id}/board").json()
    notes = [i for i in board["items"] if i["kind"] == "requirements"]
    assert len(notes) == 1 and notes[0]["role"] == "prompt" and notes[0]["text"] == "Exigences"
    assert not any(i["kind"] == "note" and i["role"] == "prompt" for i in board["items"])
    # Une consigne gardée ne crée pas de seconde note : le contenu est dans le projet.
    client.patch(f"/api/projects/{project.id}", json={"pending_instruction": "bois et lin"})
    again = client.get(f"/api/projects/{project.id}/board").json()
    assert sum(1 for i in again["items"] if i["role"] == "prompt") == 1


def test_old_prompt_note_becomes_the_requirements_note(tmp_path: Path) -> None:
    project = rect_room_project(4.0, 3.0).model_copy(update={"pending_instruction": "deux lits"})
    old = Board(
        items=[
            BoardItem(
                kind="note", x=10.0, y=20.0, w=300.0, h=170.0, text="deux lits", role="prompt"
            )
        ]
    )
    synced, changed = sync_board(old, project, tmp_path / "runs")
    assert changed
    prompt_items = [i for i in synced.items if i.role == "prompt"]
    assert len(prompt_items) == 1
    (note,) = prompt_items
    assert (
        note.kind == "requirements"
        and note.text == "Exigences"
        and (note.x, note.y) == (10.0, 20.0)
    )


def test_pending_answers_roundtrip_and_clearing(tmp_path: Path) -> None:
    project = rect_room_project(4.0, 3.0)
    client = _client(tmp_path, project)
    pid = project.id
    answers = {
        "space": "combles",
        "area_m2": 24,
        "bedrooms": 1,
        "style": "naturel",
        "mood": ["chaleureux"],
    }
    r = client.patch(f"/api/projects/{pid}", json={"pending_answers": answers})
    assert r.status_code == 200, r.text
    kept = r.json()["pending_answers"]
    assert kept["space"] == "combles" and kept["area_m2"] == 24 and kept["mood"] == ["chaleureux"]
    assert client.get(f"/api/projects/{pid}").json()["pending_answers"]["bedrooms"] == 1
    # Un objet sans réponse efface ; un objet invalide est refusé.
    assert (
        client.patch(f"/api/projects/{pid}", json={"pending_answers": {}}).json()["pending_answers"]
        is None
    )
    assert (
        client.patch(f"/api/projects/{pid}", json={"pending_answers": {"bedrooms": 99}}).status_code
        == 422
    )
    # Les réponses gardées ne périment pas les rendus (réglage, pas géométrie).
    from openplan.pipeline.stale import PROJECT_SETTINGS

    assert "pending_answers" in PROJECT_SETTINGS


def test_autopilot_reads_the_kept_answers_and_clears_them(tmp_path: Path) -> None:
    """Sans corps, le chemin automatique prend les réponses de la note ; une fois appliquées,
    elles n'attendent plus (comme la consigne)."""
    from openplan.pipeline.autopilot import HomeRequest
    from openplan.schemas.elements import Room
    from openplan.schemas.home import GuidedAnswers

    project = rect_room_project(4.0, 3.0).model_copy(
        update={
            "pending_instruction": "chaleureux",
            "pending_answers": GuidedAnswers(style="chalet", bedrooms=1),
        }
    )
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    request = HomeRequest(
        instruction="chaleureux", actor="user:test", answers=project.pending_answers
    )
    after = request.apply(project, level.id, room, request.reading(project))
    applied = after.levels[level.id].elements[room.id]
    assert isinstance(applied, Room) and applied.intent is not None
    assert applied.intent.style == "chalet"
    assert after.pending_instruction is None and after.pending_answers is None
