"""Essai multiphoto du grenier (25 oct.) : la confrontation d'une photo de plus (2.5,
`multiview.add_view`) ne lit que les arêtes sol-mur d'une pièce droite. Sur un plan de combles,
elle déplaçait un mur supposé sur deux arêtes de lignes (largeur de 7,04 m ramenée à 0,33 m).
Un plan des combles ou de la profondeur n'est plus touché ; un plan par lignes, si. Cas
synthétique : les combles 6 × 5 m de test_autoplan, puis une 2ᵉ photo."""

from __future__ import annotations

from pathlib import Path

from openplan.pipeline import attic_scene, depthplan
from openplan.pipeline.autoplan import run_autoplan
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.ingestion import import_image
from openplan.pipeline.multiview import NOT_CONFRONTED, NOT_FROM_LINES, confrontable
from openplan.schemas import Wall
from openplan.schemas.scene import Project
from tests.factories import only_level
from tests.unit.test_autoplan import FIXTURE, AtticDepth, _photo, needs_fixture


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

    # 2ᵉ photo : gardée, mais le plan des combles ne bouge pas.
    project2, ref2, _ = import_image(
        attic.project, tmp_path, FIXTURE.read_bytes(), original_name="p2.png"
    )
    again = run_autoplan(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    assert _plan(again.project) == before
    assert ref2.id in again.project.images
    view = next(s for s in again.stages if s.key == "view")
    assert view.status == "skipped"
    assert NOT_CONFRONTED in again.notices

    # Même par la route de détection (add_view appelé directement) : rien ne bouge.
    run = detect_and_propose(project2, tmp_path, level.id, ref2.id, tmp_path / "runs")
    assert _plan(run.project) == before
    assert NOT_CONFRONTED in run.floorplan.warnings


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
    assert NOT_CONFRONTED not in again.notices
