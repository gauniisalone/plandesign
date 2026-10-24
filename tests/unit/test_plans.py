"""T7e : plusieurs plans par projet (« + Nouveau plan »)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openplan.pipeline.board import sync_board
from openplan.pipeline.render import RenderEntry
from openplan.pipeline.stale import stale_cameras, step_scope
from openplan.scene import history
from openplan.scene.migrate import load_project
from openplan.scene.ops import SetIntent, apply_op
from openplan.scene.plans import NewPlan, PlanError, add_plan, stack_of, storey_of
from openplan.schemas.common import SCHEMA_VERSION
from openplan.schemas.elements import Camera, Room
from openplan.schemas.scene import Level
from openplan.validation.invariants import validate_invariants
from tests.factories import attic_project, only_level, rect_room_project


def test_variant_is_a_copy_with_fresh_ids_one_undoable_step() -> None:
    project = rect_room_project()
    base = only_level(project)
    after, lid = add_plan(
        project, NewPlan(name="Variante bureau", kind="variant", source_level_id=base.id)
    )
    variant = after.levels[lid]
    assert variant.name == "Variante bureau" and variant.variant_of == base.id
    assert len(variant.elements) == len(base.elements)
    assert len(variant.vertices) == len(base.vertices)
    # Identifiants neufs : rien de partagé avec l'original (les rendus suivent les caméras).
    assert not set(variant.elements) & set(base.elements)
    assert not set(variant.vertices) & set(base.vertices)
    # Les murs de la copie relient les sommets de la copie.
    for element in variant.elements.values():
        start = getattr(element, "start_vertex", None)
        if start is not None:
            assert start in variant.vertices
    assert validate_invariants(after).valid
    assert after.history_cursor == project.history_cursor + 1
    assert set(history.undo(after).levels) == {base.id}


def test_storey_is_empty_and_stacked_above_the_highest_storey() -> None:
    project = rect_room_project(height=2.6)
    base = only_level(project)
    after, lid = add_plan(project, NewPlan(name="Étage", kind="storey"))
    storey = after.levels[lid]
    assert storey.variant_of is None and not storey.elements and not storey.vertices
    assert storey.elevation == pytest.approx(base.elevation + 2.6)
    assert storey.kind == "upper"
    # Un étage de plus se pose au-dessus des deux.
    again, lid2 = add_plan(after, NewPlan(name="Combles", kind="storey"))
    assert again.levels[lid2].elevation == pytest.approx(storey.elevation + storey.height)


def test_empty_plan_is_an_empty_variant_of_the_storey() -> None:
    project = rect_room_project()
    base = only_level(project)
    after, variant_id = add_plan(
        project, NewPlan(name="Autre idée", kind="variant", source_level_id=base.id)
    )
    # Demandé depuis une variante : la variante vide pointe vers l'étage, jamais en chaîne.
    after, lid = add_plan(after, NewPlan(name="Vide", kind="empty", source_level_id=variant_id))
    empty = after.levels[lid]
    assert empty.variant_of == base.id and not empty.elements
    assert empty.elevation == base.elevation and empty.height == base.height
    assert storey_of(after, empty).id == base.id


def test_stack_shows_storeys_and_replaces_by_the_variant_alone() -> None:
    project = rect_room_project()
    base = only_level(project)
    after, up = add_plan(project, NewPlan(name="Étage", kind="storey"))
    after, var = add_plan(after, NewPlan(name="V", kind="variant", source_level_id=base.id))
    assert [lv.id for lv in stack_of(after, after.levels[base.id])] == [base.id, up]
    assert [lv.id for lv in stack_of(after, after.levels[var])] == [var, up]
    assert [lv.id for lv in stack_of(after, after.levels[up])] == [base.id, up]


def test_unknown_source_is_refused() -> None:
    with pytest.raises(PlanError):
        add_plan(rect_room_project(), NewPlan(name="X", kind="variant", source_level_id=uuid4()))


def test_orphan_variant_is_an_invariant_violation() -> None:
    project = rect_room_project()
    base = only_level(project)
    broken = project.model_copy(
        update={"levels": {base.id: base.model_copy(update={"variant_of": uuid4()})}}
    )
    report = validate_invariants(broken)
    assert not report.valid
    assert any(v.type == "orphan_variant" for v in report.violations)


def _camera(level: Level) -> UUID:
    return next(e.id for e in level.elements.values() if isinstance(e, Camera))


def _seed(runs: Path, camera: UUID, level_id: UUID | None, name: str, at: datetime) -> str:
    run = uuid4()
    blender = runs / str(run) / "blender"
    blender.mkdir(parents=True)
    (blender / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    entry = RenderEntry(
        run_id=run,
        camera_id=camera,
        camera_name=name,
        samples=16,
        device="cpu",
        render_s=1.0,
        width_px=64,
        height_px=48,
        created_at=at,
        level_id=level_id,
    )
    (blender / "render.json").write_text(entry.model_dump_json(), encoding="utf-8")
    return str(run)


def test_variant_edits_only_stale_the_variant_views() -> None:
    project = attic_project()
    base = only_level(project)
    after, var = add_plan(project, NewPlan(name="V", kind="variant", source_level_id=base.id))
    at = after.history[after.history_cursor - 1].timestamp
    a, b = _camera(base), _camera(after.levels[var])
    renders = [
        RenderEntry(
            run_id=uuid4(),
            camera_id=c,
            camera_name="Vue",
            samples=16,
            device="cpu",
            render_s=1.0,
            width_px=64,
            height_px=48,
            created_at=at,
        )
        for c in (a, b)
    ]
    assert stale_cameras(after, renders) == set()
    room = next(e for e in after.levels[var].elements.values() if isinstance(e, Room))
    moody = apply_op(after, var, SetIntent(room_id=room.id, fields={"style": "chalet"}))
    assert step_scope(moody.history[-1], moody).levels == {str(var)}
    assert stale_cameras(moody, renders) == {b}


def test_board_has_one_row_per_plan_and_follows_undo(tmp_path: Path) -> None:
    project = attic_project()
    base = only_level(project)
    after, var = add_plan(
        project, NewPlan(name="Variante bureau", kind="variant", source_level_id=base.id)
    )
    runs = tmp_path / "runs"
    now = datetime.now(UTC)
    mine = _seed(runs, _camera(base), base.id, "Ensemble", now - timedelta(minutes=2))
    other = _seed(runs, _camera(after.levels[var]), None, "Ensemble bis", now)
    board, _ = sync_board(None, after, runs)
    frames = [i for i in board.items if i.kind == "frame" and i.role == "renders"]
    plans = {i.ref: i for i in board.items if i.kind == "plan"}
    assert len(frames) == 2 and set(plans) == {str(base.id), str(var)}
    first = next(f for f in frames if f.ref is None)
    second = next(f for f in frames if f.ref == str(var))
    assert first.text == f"Rendus · {base.name}" and second.text == "Rendus · Variante bureau"
    # Chaque cadre Rendus à droite de son plan, à sa hauteur ; la variante dessous.
    assert second.y == plans[str(var)].y and second.x > plans[str(var)].x
    assert plans[str(var)].y >= first.y + first.h
    by_ref = {i.ref: i for i in board.items if i.kind == "render"}
    assert by_ref[mine].frame == first.id and by_ref[other].frame == second.id

    # Ctrl+Z : le plan s'en va, son cadre aussi ; son rendu revient au premier cadre.
    undone = history.undo(after)
    board2, changed = sync_board(board, undone, runs)
    assert changed
    frames2 = [i for i in board2.items if i.kind == "frame" and i.role == "renders"]
    assert [f.ref for f in frames2] == [None] and frames2[0].text == "Rendus"
    renders2 = {i.ref: i for i in board2.items if i.kind == "render"}
    assert renders2[other].frame == frames2[0].id and renders2[mine].frame == frames2[0].id


def test_each_plan_gets_its_own_photo_card_and_photos_stay_put(tmp_path: Path) -> None:
    """KAN-91 : une carte « Photos de la pièce · niveau » par plan, reliée à son plan ; la carte
    d'avant reste celle du premier plan avec ses photos ; Ctrl+Z retire la carte vide."""
    project = rect_room_project()
    base = only_level(project)
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    first = next(i for i in board.items if i.kind == "frame" and i.role == "photos")
    assert first.ref is None and first.text == "Photos de la pièce"
    after, storey = add_plan(project, NewPlan(name="Étage", kind="storey"))
    board2, changed = sync_board(board, after, runs)
    assert changed
    cards = {i.ref: i for i in board2.items if i.kind == "frame" and i.role == "photos"}
    assert set(cards) == {None, str(storey)}
    assert cards[None].id == first.id and cards[None].text == f"Photos de la pièce · {base.name}"
    assert cards[str(storey)].text == "Photos de la pièce · Étage"
    plans = {i.ref: i.id for i in board2.items if i.kind == "plan"}
    feeds = {(k.source, k.target) for k in board2.links if k.kind == "feeds_plan"}
    assert feeds == {
        (first.id, plans[str(base.id)]),
        (cards[str(storey)].id, plans[str(storey)]),
    }
    assert sync_board(board2, after, runs) == (board2, False), "stable au deuxième passage"
    # Un titre renommé à la main n'est plus touché.
    renamed = board2.model_copy(
        update={
            "items": [
                i.model_copy(update={"text": "Étage, côté jardin"})
                if i.ref == str(storey) and i.kind == "frame"
                else i
                for i in board2.items
            ]
        }
    )
    kept, _ = sync_board(renamed, after, runs)
    assert any(i.text == "Étage, côté jardin" for i in kept.items)
    # Ctrl+Z : le plan s'en va, sa carte vide aussi ; le titre de la première redevient court.
    undone, _ = sync_board(board2, history.undo(after), runs)
    left = [i for i in undone.items if i.kind == "frame" and i.role == "photos"]
    assert [i.ref for i in left] == [None] and left[0].text == "Photos de la pièce"


def test_014_project_loads_with_every_level_a_storey() -> None:
    data = rect_room_project().model_dump(mode="json")
    data["schema_version"] = "0.14.0"
    for level in data["levels"].values():
        level.pop("variant_of", None)
    loaded = load_project(data)
    assert loaded.schema_version == SCHEMA_VERSION == "0.18.0"
    assert all(lv.variant_of is None for lv in loaded.levels.values())
