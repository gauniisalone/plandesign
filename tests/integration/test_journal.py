"""V2 (note 10, § 2) : journal local des demandes. Consigne → ligne ; ce qu'on en fait
(corrigé, annulé) rattaché à la demande ; jamais d'image ni de coordonnée ; coupé = rien
d'écrit ; vidé sur demande ; export JSONL relisible."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.cli.main import main
from openplan.pipeline import journal
from openplan.pipeline.journal import JournalEvent
from openplan.scene.store import ProjectStore
from openplan.schemas.elements import Room
from tests.factories import attic_project, only_level


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, ProjectStore, str, str, str]]:
    store = ProjectStore(tmp_path / "data")
    project = attic_project()
    store.save(project)
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        yield client, store, str(project.id), str(level.id), str(room.id)


def _lines(store: ProjectStore, pid: str) -> list[dict[str, Any]]:
    path = journal.journal_path(store.project_dir(__import__("uuid").UUID(pid)))
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _instruction(client: TestClient, pid: str, lid: str, rid: str, text: str) -> dict[str, Any]:
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/rooms/{rid}/instruction", json={"text": text}
    )
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def test_instruction_correction_and_undo_are_linked(setup: Any) -> None:
    client, store, pid, lid, rid = setup
    _instruction(client, pid, lid, rid, "combles scandinaves cosy, poutres blanches")
    (first,) = _lines(store, pid)
    assert first["kind"] == "instruction" and first["by"] == "phrases"
    assert first["text"].startswith("combles scandinaves")
    assert first["translation"]["style"] == "scandinave"
    assert first["history_ids"], "le pas d'historique produit est noté"

    # Corrigé : l'utilisateur change l'humeur dans la fiche.
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "set_intent", "room_id": rid, "fields": {"mood": ["lumineux"]}},
    )
    assert r.status_code == 200
    corrected = _lines(store, pid)[-1]
    assert corrected["kind"] == "intent_corrected" and corrected["fields"] == ["mood"]
    assert corrected["request_id"] == first["id"]

    # Annulé deux fois : la correction, puis la consigne, chacune rattachée à sa demande.
    client.post(f"/api/projects/{pid}/undo")
    client.post(f"/api/projects/{pid}/undo")
    undo_correction, undo_instruction = _lines(store, pid)[-2:]
    assert undo_correction["kind"] == "undo" and undo_correction["request_id"] == corrected["id"]
    assert undo_instruction["request_id"] == first["id"]


def test_no_image_path_or_coordinate_in_any_line(setup: Any) -> None:
    client, store, pid, lid, rid = setup
    _instruction(client, pid, lid, rid, "chambre chaleureuse, deux lits, tons terracotta")
    client.post(
        f"/api/projects/{pid}/levels/{lid}/assistant/say",
        json={"text": "ajoute un fauteuil près de la fenêtre"},
    )
    raw = journal.journal_path(store.project_dir(__import__("uuid").UUID(pid))).read_text()
    for line in raw.splitlines():
        data = json.loads(line)
        JournalEvent.model_validate(data)  # schéma v1 documenté
        text = json.dumps(data)
        for forbidden in ('"x":', '"y":', '"position"', '"image', ".jpg", ".png", "/home/"):
            assert forbidden not in text, (forbidden, line)
    assert {d["kind"] for d in map(json.loads, raw.splitlines())} >= {"instruction", "assistant"}


def test_a_line_that_would_leak_is_not_written(tmp_path: Path) -> None:
    project = attic_project()
    event = JournalEvent(
        project_id=project.id, kind="instruction", text="comme /home/user/photo.jpg"
    )
    assert not journal.record(tmp_path, project, event)
    assert not journal.journal_path(tmp_path).exists()


def test_switched_off_writes_nothing_and_clear_empties(setup: Any) -> None:
    client, store, pid, lid, rid = setup
    _instruction(client, pid, lid, rid, "salon chalet")
    assert client.get(f"/api/projects/{pid}/journal").json()["events"] == 1
    project = client.get(f"/api/projects/{pid}").json()
    privacy = {**project["privacy"], "design_journal": False}
    assert client.patch(f"/api/projects/{pid}", json={"privacy": privacy}).status_code == 200
    _instruction(client, pid, lid, rid, "loft industriel")
    assert len(_lines(store, pid)) == 1, "coupé : rien de plus"
    assert client.delete(f"/api/projects/{pid}/journal").json()["events"] == 0
    assert _lines(store, pid) == []


def test_export_is_jsonl_and_readable(setup: Any, capsys: pytest.CaptureFixture[str]) -> None:
    client, store, pid, lid, rid = setup
    _instruction(client, pid, lid, rid, "bureau scandinave lumineux")
    code = main(["journal", "export", pid, "--data-dir", str(store.root)])
    assert code == 0
    out = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(out) == 1
    assert JournalEvent.model_validate_json(out[0]).kind == "instruction"
    assert main(["journal", "export", "--all", "--data-dir", str(store.root)]) == 0
