"""Essai multiphoto du grenier (25 oct.) : la confrontation d'une photo de plus (2.5,
`multiview.add_view`) ne lit que les arêtes sol-mur d'une pièce droite. Sur un plan de combles,
elle déplaçait un mur supposé sur deux arêtes de lignes (largeur de 7,04 m ramenée à 0,33 m).
Sur un plan des combles ou de la profondeur, la photo reçoit toujours sa caméra (décision
produit : cône de vision, « Rendre depuis la photo ») mais ni murs ni pentes ne bougent ; un
plan par lignes est confronté comme avant. Cas synthétique : les combles 6 × 5 m de
test_autoplan, puis une 2ᵉ photo."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from openplan.pipeline import attic_scene, depthplan
from openplan.pipeline.attic_fusion import fuse_attic
from openplan.pipeline.autoplan import run_autoplan
from openplan.pipeline.depthplan import observe_room, propose_floorplan_from_depth
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.ingestion import import_image
from openplan.pipeline.multiview import NOT_FROM_LINES, POSED_ONLY, confrontable
from openplan.schemas import Camera, Wall
from openplan.schemas.elements import RoofPlane
from openplan.schemas.scene import Project
from tests.attic_scenes import AXIAL_A, AXIAL_B, OBLIQUE, Attic, camera, project_with_photos
from tests.factories import only_level
from tests.synthetic_depth import box_faces, render_depth
from tests.unit.test_autoplan import FIXTURE, AtticDepth, _photo, needs_fixture
from tests.unit.test_depthplan import CAMERA


def _plan(project: Project) -> list[tuple[float, float]]:
    """Les coins du plan (positions des sommets des murs), triés."""
    level = only_level(project)
    ids = {
        v
        for e in level.elements.values()
        if isinstance(e, Wall)
        for v in (e.start_vertex, e.end_vertex)
    }
    return sorted(
        (round(level.vertices[v].position.x, 6), round(level.vertices[v].position.y, 6))
        for v in ids
    )


def _cameras(project: Project) -> int:
    """Caméras de photos (une par photo posée sur le plan)."""
    return sum(
        1
        for e in only_level(project).elements.values()
        if isinstance(e, Camera) and e.image_id is not None
    )


def _roofs(project: Project) -> list[str]:
    return sorted(
        e.model_dump_json(exclude={"id"})
        for e in only_level(project).elements.values()
        if isinstance(e, RoofPlane)
    )


def test_the_steps_that_lines_cannot_read() -> None:
    assert {attic_scene.STEP, depthplan.STEP} == NOT_FROM_LINES


@needs_fixture
def test_a_second_photo_leaves_the_attic_plan_alone(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    first = next(iter(project.images))
    width = project.images[first].width_px
    attic = run_autoplan(
        project,
        tmp_path,
        level.id,
        first,
        tmp_path / "runs",
        depth=AtticDepth(focal_px=0.66 * width, pitch=0.35),
    )
    assert attic.method == "attic"
    assert not confrontable(only_level(attic.project))
    before = _plan(attic.project)
    assert len(before) == 4

    assert _cameras(attic.project) == 1

    # 2ᵉ photo : elle reçoit sa caméra (décision produit), le plan des combles ne bouge pas.
    project2, ref2, _ = import_image(
        attic.project, tmp_path, FIXTURE.read_bytes(), original_name="p2.png"
    )
    again = run_autoplan(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    assert _plan(again.project) == before
    assert _cameras(again.project) == 2
    view = next(s for s in again.stages if s.key == "view")
    assert view.status == "done" and view.detail == "caméra posée, plan inchangé"
    assert POSED_ONLY in again.notices
    # Les pans de toit non plus.
    assert _roofs(again.project) == _roofs(attic.project)

    # Même par la route de détection (add_view appelé directement).
    run = detect_and_propose(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    assert _plan(run.project) == before
    assert _cameras(run.project) == 2
    assert POSED_ONLY in run.floorplan.warnings


@needs_fixture
def test_a_second_photo_on_a_depth_plan_gets_its_camera(tmp_path: Path) -> None:
    """Le cas de slice.spec avec les poids installés : plan tiré de la profondeur, puis une
    2ᵉ photo ; elle doit recevoir sa caméra (2 caméras), sans toucher aux murs."""
    project, level = _photo(tmp_path)
    first = next(iter(project.images))
    depth = render_depth(box_faces(4.0, 3.0, 2.5), CAMERA, noise=0.01, seed=1)
    obs, _ = observe_room(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert obs is not None
    planned, _ = propose_floorplan_from_depth(project, level.id, obs, image_id=first)
    assert not confrontable(only_level(planned))
    before = _plan(planned)
    assert _cameras(planned) == 1
    project2, ref2, _ = import_image(
        planned, tmp_path, FIXTURE.read_bytes(), original_name="p2.png"
    )
    run = detect_and_propose(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    assert _plan(run.project) == before
    assert _cameras(run.project) == 2
    assert POSED_ONLY in run.floorplan.warnings


def _cameras_by_photo(project: Project) -> dict[UUID, int]:
    counts: dict[UUID, int] = {}
    for e in only_level(project).elements.values():
        if isinstance(e, Camera) and e.image_id is not None:
            counts[e.image_id] = counts.get(e.image_id, 0) + 1
    return counts


def test_multiview_and_the_attic_fusion_do_not_step_on_each_other(tmp_path: Path) -> None:
    """KAN-95 et multiview ensemble : combles recoupés sur deux photos, puis une 3ᵉ photo
    ajoutée (multiview) : sa caméra est posée, le plan recoupé ne bouge pas ; puis le recoupement
    sur les trois photos : une seule caméra par photo (celle posée par multiview est reprise, pas
    doublée), et le plan recoupé reste hors de portée de multiview."""
    project, level, ids = project_with_photos(
        tmp_path, Attic(), [camera(*c) for c in (AXIAL_A, OBLIQUE, AXIAL_B)]
    )
    first = fuse_attic(project, tmp_path, level, ids[:2])
    assert first.applied
    fused = first.project
    assert not confrontable(fused.levels[level])
    before = _plan(fused)
    assert _cameras_by_photo(fused) == {ids[0]: 1, ids[1]: 1}

    added = detect_and_propose(fused, tmp_path, level, ids[2], tmp_path / "runs")
    assert _plan(added.project) == before
    assert _roofs(added.project) == _roofs(fused)
    assert _cameras_by_photo(added.project) == {ids[0]: 1, ids[1]: 1, ids[2]: 1}
    assert POSED_ONLY in added.floorplan.warnings

    again = fuse_attic(added.project, tmp_path, level, ids)
    assert again.applied
    assert _cameras_by_photo(again.project) == {ids[0]: 1, ids[1]: 1, ids[2]: 1}
    assert not confrontable(again.project.levels[level])


@needs_fixture
def test_a_plan_from_lines_is_still_confronted(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    first = next(iter(project.images))
    lines = run_autoplan(project, tmp_path, level.id, first, tmp_path / "runs")
    assert lines.method == "lines"
    assert confrontable(only_level(lines.project))
    project2, ref2, _ = import_image(
        lines.project, tmp_path, FIXTURE.read_bytes(), original_name="p2.png"
    )
    again = run_autoplan(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    view = next(s for s in again.stages if s.key == "view")
    assert view.status == "done" and again.method == "view"
    assert POSED_ONLY not in again.notices
