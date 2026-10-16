"""P5a : l'assistant unifié. Une phrase → ambiance de la pièce en contexte et gestes sur le
plan, en un tour ; chaque partie a son issue et sa raison ; un échec partiel garde ce qui a
réussi ; « annuler » défait tout le tour ; la case Gemini vaut pour le fil du projet."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openplan.adapters.gateway import ModelGateway
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.say import readers_for, target_room
from openplan.scene import history
from openplan.scene.ops import AddFurniture, apply_op
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Point2
from openplan.schemas.elements import Furniture, Room
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _client(tmp_path: Path, project: Project) -> TestClient:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))


def _say(client: TestClient, project: Project, text: str, **extra: object) -> dict:  # type: ignore[type-arg]
    lid = only_level(project).id
    r = client.post(
        f"/api/projects/{project.id}/levels/{lid}/assistant/say", json={"text": text, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_one_sentence_sets_the_ambiance_and_adds_furniture(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    out = _say(client, project, "plus chaleureux, et ajoute un bureau")
    turn = out["turn"]
    kinds = [(p["kind"], p["status"]) for p in turn["parts"]]
    assert ("ambiance", "done") in kinds and ("action", "done") in kinds, kinds
    assert turn["reader"] == "phrases" and turn["committed"] == 2
    level = next(iter(out["project"]["levels"].values()))
    room = next(e for e in level["elements"].values() if e["type"] == "room")
    assert "chaleureux" in room["intent"]["mood"]
    assert any(
        e["type"] == "furniture" and e["category"] == "desk" for e in level["elements"].values()
    )
    # Le bureau est un geste : il n'est pas recopié en envie de meuble dans l'ambiance.
    assert not any(f.get("category") == "desk" for f in room["intent"].get("furniture") or [])


def test_a_partial_failure_keeps_what_worked_and_undo_reverts_the_whole_turn(
    tmp_path: Path,
) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    out = _say(client, project, "lumière du soir, et enlève l'armoire")
    parts = out["turn"]["parts"]
    assert parts[0]["kind"] == "ambiance" and parts[0]["status"] == "done"
    refused = [p for p in parts if p["kind"] == "action"]
    assert refused and refused[0]["status"] != "done" and refused[0]["reason"]
    assert out["turn"]["committed"] == 1
    store = ProjectStore(tmp_path / "data")
    after = store.load(project.id)
    for _ in range(out["turn"]["committed"]):
        after = history.undo(after)
    room = next(e for e in only_level(after).elements.values() if isinstance(e, Room))
    assert room.intent is None or room.intent.light.time != "soir"


def test_not_understood_is_said(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    out = _say(_client(tmp_path, project), project, "zorglub quantique")
    (part,) = out["turn"]["parts"]
    assert part["status"] == "refused" and "pas compris" in part["summary"]
    assert out["turn"]["committed"] == 0


def test_context_furniture_targets_its_room(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    level = only_level(project)
    project = apply_op(
        project, level.id, AddFurniture(asset_id="sofa_2", position=Point2(x=2.5, y=2.0))
    )
    sofa = next(e for e in only_level(project).elements.values() if isinstance(e, Furniture))
    room = target_room(only_level(project), sofa.id)
    assert room is not None and room.type == "room"


class _Remote:
    remote = True
    model_id = "gemini-test"
    destination = "example.invalid"


def test_the_gemini_case_gates_the_remote_model(tmp_path: Path) -> None:
    gateway = ModelGateway(tmp_path / "net.jsonl")
    off = readers_for(_Remote(), False, gateway)  # type: ignore[arg-type]
    assert off.model is None and "Gemini" in off.notice
    on = readers_for(_Remote(), True, gateway)  # type: ignore[arg-type]
    assert on.model is not None and not on.notice
    assert readers_for(None, True, gateway).model is None


def test_the_thread_is_read_back_from_the_journal(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    _say(client, project, "plus chaleureux")
    _say(client, project, "zorglub quantique")
    thread = client.get(f"/api/projects/{project.id}/assistant/thread").json()
    assert thread["journal_enabled"] is True
    assert [t["text"] for t in thread["turns"]] == ["plus chaleureux", "zorglub quantique"]
    assert thread["turns"][0]["parts"][0]["status"] == "done"
    store = ProjectStore(tmp_path / "data")
    current = store.load(project.id)
    store.save(
        current.model_copy(
            update={"privacy": current.privacy.model_copy(update={"design_journal": False})}
        )
    )
    off = client.get(f"/api/projects/{project.id}/assistant/thread").json()
    assert off == {"journal_enabled": False, "turns": []}


def test_clauses_split_mixed_sentences() -> None:
    from openplan.pipeline.say import clauses

    assert clauses("lumière du soir, et enlève l'armoire") == [
        "lumière du soir",
        "enlève l'armoire",
    ]
    assert clauses("plus chaleureux. Ajoute un bureau ; puis retire le tapis") == [
        "plus chaleureux",
        "Ajoute un bureau",
        "retire le tapis",
    ]


def test_named_furniture_without_a_verb_is_a_wish_not_an_addition(tmp_path: Path) -> None:
    """« sans tapis », « deux lits » : envies de l'ambiance ; seul un verbe d'ajout pose un
    meuble (« ajoute un bureau »)."""
    project = rect_room_project(5.0, 4.0)
    out = _say(_client(tmp_path, project), project, "chambre cosy, sans tapis, deux lits")
    level = next(iter(out["project"]["levels"].values()))
    assert not [e for e in level["elements"].values() if e["type"] == "furniture"]
    kinds = [p["kind"] for p in out["turn"]["parts"]]
    assert kinds == ["ambiance"], out["turn"]["parts"]
    room = next(e for e in level["elements"].values() if e["type"] == "room")
    assert "rug" in room["intent"]["avoid"]


def test_the_home_instruction_opens_the_thread(tmp_path: Path) -> None:
    """P5c : la consigne écrite sur l'accueil (journal « autopilot ») est le premier tour."""
    from openplan.pipeline import journal
    from openplan.pipeline.journal import JournalEvent

    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    store = ProjectStore(tmp_path / "data")
    journal.record(
        store.project_dir(project.id),
        project,
        JournalEvent(
            project_id=project.id, kind="autopilot", text="grenier chaleureux", by="phrases"
        ),
    )
    _say(client, project, "plus lumineux")
    turns = client.get(f"/api/projects/{project.id}/assistant/thread").json()["turns"]
    assert [t["text"] for t in turns] == ["grenier chaleureux", "plus lumineux"]
    assert "accueil" in turns[0]["parts"][0]["summary"]


def test_an_unread_clause_is_said_not_dropped(tmp_path: Path) -> None:
    """Chaque partie a son ✓ ou son ✗ : une proposition incomprise n'est pas passée sous
    silence."""
    project = rect_room_project(5.0, 4.0)
    out = _say(_client(tmp_path, project), project, "plus chaleureux, et fais voler un dragon")
    parts = out["turn"]["parts"]
    assert parts[0]["kind"] == "ambiance" and parts[0]["status"] == "done"
    assert parts[-1]["status"] == "refused" and "dragon" in parts[-1]["summary"]
    assert "pas compris" in parts[-1]["reason"]


def _density(out: dict) -> str:  # type: ignore[type-arg]
    level = next(iter(out["project"]["levels"].values()))
    room = next(e for e in level["elements"].values() if e["type"] == "room")
    return str(room["intent"]["decor_density"])


def test_adding_a_decor_object_makes_the_decor_denser(tmp_path: Path) -> None:
    """« ajoute une plante » : pas de plante au catalogue, la densité du décor monte d'un cran
    (normal → riche), dit en ✓ ; au plus fourni, un ✗ avec la raison, jamais « pas compris »."""
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    out = _say(client, project, "plus chaleureux, et ajoute une plante")
    parts = out["turn"]["parts"]
    assert [(p["kind"], p["status"]) for p in parts] == [("ambiance", "done")] * 2, parts
    assert "décor plus fourni" in parts[1]["summary"]
    assert out["turn"]["committed"] == 2 and _density(out) == "riche"
    again = _say(client, project, "ajoute des coussins")
    (part,) = again["turn"]["parts"]
    assert part["status"] == "refused" and "déjà au plus fourni" in part["reason"]
    assert _density(again) == "riche"


def test_a_decor_word_set_by_the_sentence_wins(tmp_path: Path) -> None:
    """« sobre, ajoute un cadre » : la phrase fixe la densité, le décor ne la remonte pas ; un
    tapis reste un meuble du catalogue."""
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    out = _say(client, project, "sobre, et ajoute un cadre")
    assert _density(out) == "sobre"
    assert all("pas compris" not in p["reason"] for p in out["turn"]["parts"])
    rug = _say(client, project, "ajoute un tapis")
    level = next(iter(rug["project"]["levels"].values()))
    assert any(e.get("category", "").startswith("rug") for e in level["elements"].values())
