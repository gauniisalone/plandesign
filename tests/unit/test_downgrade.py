"""KAN-75 : descente du format vers la version de production (3da4d52, schéma 0.16.0), pour
un retour arrière sans restaurer l'archive. Marches, arbre de données (idempotent, --dry-run),
commande, et la garde : tout champ ajouté depuis la production est retiré par une marche, ou
toléré par l'ancien code (raison dite)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openplan.cli.main import main
from openplan.pipeline.reference import camera_basis, with_basis
from openplan.scene.downgrade import REMOVED_PATHS, downgrade_data, downgrade_tree
from openplan.scene.factory import new_project
from openplan.scene.migrate import UnsupportedSchemaVersionError, load_project
from openplan.scene.store import ProjectStore
from openplan.schemas.common import SCHEMA_VERSION
from openplan.schemas.scene import ImageRef, Project
from tests.format_paths import current_paths

FIXTURES = Path(__file__).parents[1] / "fixtures"
SNAPSHOTS = [
    FIXTURES / "format_paths_3da4d52.json",
    FIXTURES / "format_paths_a1c630f.json",
    FIXTURES / "format_paths_14e8870.json",
]
"""Instantanés des chemins de champs des productions (MEP 5, 3da4d52, schéma 0.16.0 ; MEP 13,
a1c630f, schéma 0.17.0 ; MEP 15, 14e8870, schéma 0.18.0), produits par
`python -m tests.format_paths` avec le code de chaque version en PYTHONPATH."""
TOLERATED: dict[str, dict[str, str]] = {
    "events.jsonl": {
        "": "une ligne d'un type ou d'un champ inconnu est ignorée par l'ancien code "
        "(pipeline/events.py : « jamais une erreur »)",
    },
}
"""Champs nouveaux laissés en place : l'ancien code les relit sans erreur (raison dite)."""


def _with_confidence() -> Project:
    project = new_project("Grenier")
    ref = ImageRef(
        path="images/x.jpg", sha256="0" * 64, width_px=10, height_px=10, exif_stripped=True
    )
    project = project.model_copy(update={"images": {ref.id: ref}})
    level = next(iter(project.levels))
    camera = camera_basis(ref.id, 0.5, 1.45, entered=True)
    reference = camera.model_copy(update={"source": "reference", "previous": camera})
    return with_basis(project, level, reference)


def _dump(project: Project) -> dict[str, object]:
    data: dict[str, object] = json.loads(project.model_dump_json())
    return data


def test_the_step_removes_the_confidence_everywhere_and_is_idempotent() -> None:
    data = _dump(_with_confidence())
    assert data["schema_version"] == SCHEMA_VERSION == "0.19.0"
    down, removed, lost = downgrade_data(data, "0.16.0")
    assert down["schema_version"] == "0.16.0" and removed == 4  # la mesure et sa « previous »
    assert "zones de sol" in lost[0] and "liens" in lost[1] and "confiance" in lost[2]
    text = json.dumps(down)
    assert "confidence" not in text
    again, removed_again, _ = downgrade_data(down, "0.16.0")
    assert again == down and removed_again == 0
    # La remontée relit le projet descendu sans perte d'autre chose que la confiance.
    assert load_project(json.loads(json.dumps(down))).levels


def test_a_newer_project_is_refused_with_its_reason() -> None:
    with pytest.raises(UnsupportedSchemaVersionError, match="plus récent"):
        downgrade_data({"schema_version": "9.0.0"}, "0.16.0")


def test_the_tree_dry_run_then_for_real_then_again(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data" / "projects")
    store.save(_with_confidence())
    store.save(new_project("Sans échelle"))
    file = next(
        p for p in (tmp_path / "data").rglob("project.json") if "confidence" in p.read_text()
    )
    before = file.read_bytes()
    dry = downgrade_tree(tmp_path / "data", "0.16.0", dry_run=True)
    assert len(dry.changed) == 2 and dry.removed == 4 and not dry.errors
    assert file.read_bytes() == before, "--dry-run n'écrit rien"
    done = downgrade_tree(tmp_path / "data", "0.16.0")
    assert len(done.changed) == 2 and done.removed == 4
    assert json.loads(file.read_text())["schema_version"] == "0.16.0"
    assert "confidence" not in file.read_text()
    again = downgrade_tree(tmp_path / "data", "0.16.0")
    assert again.changed == [] and again.unchanged == 2


def test_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.save(_with_confidence())
    assert main(["downgrade", "--to", "0.16.0", "--dry-run", str(tmp_path)]) == 0
    assert "1 projet(s) à descendre" in capsys.readouterr().out
    assert json.loads(project.read_text())["schema_version"] == "0.19.0"
    assert main(["downgrade", "--to", "0.16.0", str(tmp_path)]) == 0
    assert json.loads(project.read_text())["schema_version"] == "0.16.0"
    assert main(["downgrade", "--to", "0.1.0", str(tmp_path)]) == 2


def test_board_links_go_down_with_the_project_to_0_17(tmp_path: Path) -> None:
    """KAN-91 : 0.18.0 → 0.17.0 retire du `board.json` les liens, les paires coupées et les
    étiquettes ignorées ; le reste du tableau (cartes, appartenance `frame`) ne bouge pas."""
    from uuid import uuid4

    from openplan.pipeline.board import save_board
    from openplan.schemas.board import Board, BoardItem, BoardLink

    store = ProjectStore(tmp_path / "projects")
    path = store.save(new_project("Cartes"))
    frame = BoardItem(kind="frame", x=0, y=0, w=10, h=10, role="photos")
    photo = BoardItem(
        kind="photo", x=1, y=1, w=2, h=2, ref=str(uuid4()), frame=frame.id, ignored_tags=["m:lin"]
    )
    plan = BoardItem(kind="plan", x=20, y=0, w=10, h=10, ref=str(uuid4()))
    link = BoardLink(source=frame.id, target=plan.id, kind="feeds_plan")
    save_board(
        path.parent,
        Board(items=[frame, photo, plan], links=[link], cut_links=[f"{uuid4()}>{uuid4()}"]),
    )
    done = downgrade_tree(tmp_path, "0.17.0")
    assert len(done.changed) == 1 and done.removed == 3 and not done.errors
    board = json.loads((path.parent / "board.json").read_text())
    assert "links" not in board and "cut_links" not in board
    assert all("ignored_tags" not in i for i in board["items"])
    assert board["items"][1]["frame"] == str(frame.id), "l'appartenance reste"
    assert json.loads(path.read_text())["schema_version"] == "0.17.0"
    again = downgrade_tree(tmp_path, "0.17.0")
    assert again.changed == [] and again.unchanged == 1


def test_the_media_index_loses_placed_even_when_project_json_was_not_rewritten(
    tmp_path: Path,
) -> None:
    """`placed` (25 oct.) vit dans `media/index.json`, qu'un envoi écrit sans réécrire
    `project.json` : l'index descend selon la version visée, même pour un projet resté en
    0.18.0 sur le disque ; sans `placed`, rien ne change."""
    store = ProjectStore(tmp_path / "projects")
    path = store.save(new_project("Fichiers"))
    data = json.loads(path.read_text())
    data["schema_version"] = "0.18.0"  # jamais réécrit depuis la montée
    path.write_text(json.dumps(data))
    media = path.parent / "media"
    media.mkdir()
    entry = {"id": "00000000-0000-4000-8000-000000000001", "name": "a.txt", "placed": True}
    (media / "index.json").write_text(json.dumps([entry, {**entry, "placed": False}]))
    dry = downgrade_tree(tmp_path, "0.18.0", dry_run=True)
    assert len(dry.changed) == 1 and dry.removed == 2
    assert "placed" in (media / "index.json").read_text(), "--dry-run n'écrit rien"
    done = downgrade_tree(tmp_path, "0.18.0")
    assert done.removed == 2 and "placed" not in (media / "index.json").read_text()
    assert json.loads(path.read_text())["schema_version"] == "0.18.0"
    again = downgrade_tree(tmp_path, "0.18.0")
    assert again.changed == [] and again.unchanged == 1


def test_floor_zones_go_down_to_0_18_with_the_undo_history_that_knew_them() -> None:
    """KAN-92 : 0.19.0 → 0.18.0 retire les zones de sol ; l'historique d'annulation d'un projet
    qui en avait est vidé (un Ctrl+Z les ferait revenir dans l'ancienne application). Un projet
    sans zone garde son historique ; une épaisseur de mur réglée reste."""
    from openplan.scene.ops import AddFloorZone, UpdateElement, apply_op
    from openplan.schemas.common import Point2
    from openplan.schemas.elements import Wall
    from tests.factories import rect_room_project

    project = rect_room_project()
    level = next(iter(project.levels.values()))
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    thick = apply_op(
        project, level.id, UpdateElement(element_id=wall.id, fields={"thickness": 0.3})
    )
    plain = _dump(thick)
    _, removed, _ = downgrade_data(plain, "0.18.0")
    assert removed == 0 and len(plain["history"]) == 1, "sans zone, l'historique reste"  # type: ignore[arg-type]
    corners = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]
    zoned = apply_op(thick, level.id, AddFloorZone(polygon=[Point2(x=x, y=y) for x, y in corners]))
    down, removed, lost = downgrade_data(_dump(zoned), "0.18.0")
    assert down["schema_version"] == "0.18.0" and "zones de sol" in lost[0]
    text = json.dumps(down)
    assert "floor_zone" not in text and down["history"] == [] and down["history_cursor"] == 0
    assert removed == 1 + 2, "la zone et les deux pas d'historique"
    elements = down["levels"][str(level.id)]["elements"]
    assert elements[str(wall.id)]["thickness"] == 0.3, "l'épaisseur réglée reste"
    assert load_project(json.loads(text)).levels


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.stem.split("_")[-1])
def test_every_field_added_since_production_is_removed_or_tolerated(snapshot: Path) -> None:
    """Un ticket qui ajoute un champ à un fichier relu par un modèle strict doit l'ajouter à
    une marche de descente (REMOVED_PATHS), ou dire ici pourquoi l'ancien code le tolère."""
    production = json.loads(snapshot.read_text(encoding="utf-8"))
    missing: list[str] = []
    for file, paths in current_paths().items():
        added = set(paths) - set(production.get(file) or [])
        removed = REMOVED_PATHS.get(file, set())
        tolerated = TOLERATED.get(file, {})
        for path in sorted(added):
            if any(
                path == r or path.startswith((r + ".", r + "=", r + "[", r + "{")) for r in removed
            ):
                continue
            if any(path.startswith(t) for t in tolerated):
                continue
            missing.append(f"{file} : {path}")
    assert not missing, "champs nouveaux sans descente :\n" + "\n".join(missing)
