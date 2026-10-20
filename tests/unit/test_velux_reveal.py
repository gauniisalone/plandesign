"""Chantier H, lot H2 : velux à ébrasement (caisson biais) et soleil qui entre par le velux."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openplan.geom.roof import derived_roofs
from openplan.geom.velux import (
    CHEVETRE_DEPTH_M,
    REVEAL_DEPTH_M,
    reveal_depth,
    reveal_solids,
    roof_panels,
    timber_settings,
)
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.render import SKYLIGHT_TURN_DEG, daylight_spec, through_skylights
from openplan.pipeline.scene3d import (
    bake_level,
    reveal_finish,
)
from openplan.scene.ops import AddRoofWindow, SetIntent, apply_op
from openplan.schemas.elements import RoofPlane, RoofWindow, Room
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


def _with_velux(project: Project) -> tuple[Project, RoofPlane]:
    level = only_level(project)
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    after = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=2.6, distance=0.9))
    return after, roof


def test_reveal_has_a_level_head_a_plumb_sill_and_two_cheeks() -> None:
    project, roof = _with_velux(attic_project())
    level = only_level(project)
    panel = next(p for p in roof_panels(level, WallGraph.from_level(level)) if p.roof.id == roof.id)
    lintel, sill, cheek0, cheek1 = reveal_solids(panel, (2.6, 3.38, 0.9, 1.88))
    # linteau : ses quatre arêtes basses à la même hauteur (horizontal)
    assert len({round(z, 4) for z in lintel[:4, 2]}) == 1
    # appui : vertical, les points intérieurs à l'aplomb des points du bord du trou
    assert sill[0][0] == pytest.approx(sill[3][0]) and sill[0][1] == pytest.approx(sill[3][1])
    assert sill[3][2] < sill[0][2]
    # profondeur : le bord intérieur à la profondeur du caisson sous la toiture (REVEAL_DEPTH_M
    # sans charpente, jusqu'au bas des pannes sous une charpente apparente, KAN-63)
    full = reveal_depth(panel.roof)
    assert full == (CHEVETRE_DEPTH_M if timber_settings(panel.roof)[0] else REVEAL_DEPTH_M)
    depth = float((panel.vertices[0] - sill[3]) @ panel.normal)
    assert depth == pytest.approx(full, abs=1e-6)
    assert cheek0.shape == cheek1.shape == (8, 3)
    # Le linteau part vers le faîtage, sous la pente : son bord intérieur est sous la toiture
    # (pas un caisson démesuré : 1,6 m au plus pour 22 cm, 2,0 m pour le chevêtre de KAN-63).
    inner = lintel[3]
    assert float((panel.vertices[0] - inner) @ panel.normal) == pytest.approx(full)
    span = max(
        float(np.linalg.norm(solid[i] - solid[j]))
        for solid in (lintel, sill, cheek0, cheek1)
        for i in range(8)
        for j in range(8)
    )
    assert span < (2.0 if full > REVEAL_DEPTH_M else 1.6), span


def test_bake_adds_the_reveal_in_planks_or_plaster_by_style() -> None:
    project, _ = _with_velux(attic_project())
    level = only_level(project)
    window = next(e for e in level.elements.values() if isinstance(e, RoofWindow))
    bake = bake_level(project, level.id, style="realistic")
    reveal = [p for p in bake.polyhedra if p.kind == "reveal" and p.element_id == window.id]
    assert len(reveal) == 4
    assert {p.finish for p in reveal} == {reveal_finish(level)} == {"wood_boards"}
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    white = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "scandinave"}))
    assert reveal_finish(only_level(white)) == "plaster"


def test_sun_turns_toward_the_velux_slope_within_60_degrees() -> None:
    project, roof = _with_velux(attic_project())
    level = only_level(project)
    derived = derived_roofs(level, WallGraph.from_level(level))[roof.id]
    outward = math.atan2(-derived.inward.y, -derived.inward.x)
    for preset in ("matin", "midi", "soir"):
        spec = daylight_spec(preset, level.north_deg)
        before = math.atan2(spec["sun"]["direction"][1], spec["sun"]["direction"][0])
        turned = through_skylights(spec, level)
        dx, dy, dz = turned["sun"]["direction"]
        after = math.atan2(dy, dx)
        assert dz == pytest.approx(spec["sun"]["direction"][2]), "hauteur du soleil inchangée"
        moved = abs(math.degrees(math.atan2(math.sin(after - before), math.cos(after - before))))
        assert moved <= SKYLIGHT_TURN_DEG + 1e-6
        gap_before = abs(math.atan2(math.sin(outward - before), math.cos(outward - before)))
        gap_after = abs(math.atan2(math.sin(outward - after), math.cos(outward - after)))
        assert gap_after <= gap_before + 1e-9, "plus près de la face du velux"
    plain = attic_project()
    spec = daylight_spec("midi", only_level(plain).north_deg)
    assert through_skylights(spec, only_level(plain)) == spec, "sans velux, rien ne bouge"
