"""Combles validés → scène : éléments et sources, volumes 3D analytiques, plan 2D."""

from __future__ import annotations

import io
import math

import pytest
import trimesh

from openplan.geom.velux import (
    ROOF_THICKNESS,
)
from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic, weaker
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.export import render_svg
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.scene3d import (
    ROOF_SKIRT_M,
    bake_level,
    build_scene,
    glb_bytes,
)
from openplan.scene.factory import new_project
from openplan.schemas import Camera, Ceiling, RoofPlane, Room, Source, Wall
from tests.factories import ATTIC, attic_project, by_name, only_level

L, W, RIDGE, EAVE = ATTIC["length"], ATTIC["width"], ATTIC["ridge_height"], ATTIC["eave_height"]
T = 0.15
SLOPE = (RIDGE - EAVE) / (W / 2)


def test_apply_attic_creates_walls_roofs_room_and_camera() -> None:
    project = attic_project()
    level = only_level(project)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    roofs = [e for e in level.elements.values() if isinstance(e, RoofPlane)]
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    assert len(walls) == 4 and len(roofs) == 2 and len(rooms) == 1
    assert not any(isinstance(e, Ceiling) for e in level.elements.values()), "pas de plafond plat"
    assert rooms[0].name == "Combles" and rooms[0].room_type == "attic"
    assert all(r.rooms == [rooms[0].id] for r in roofs)
    assert level.height == pytest.approx(RIDGE) and level.height_source == "estimated"
    assert all(w.source == "estimated" and w.confidence == 0.4 for w in walls)
    eaves = [w for w in walls if w.name and w.name.startswith("Égout")]
    gables = [w for w in walls if w.name == "Pignon"]
    assert [w.height for w in eaves] == [EAVE, EAVE] and all(w.height is None for w in gables)
    assert roofs[0].pitch == pytest.approx(math.atan(SLOPE))
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    assert camera.position.x == pytest.approx(L - 4.5) and camera.position.z == pytest.approx(1.45)
    assert project.history[-1].actor == "user" and project.history[-1].label.startswith("Créer")
    assert all(w.provenance.step == "attic.apply" for w in walls)
    with pytest.raises(DetectionError, match="déjà des murs"):
        apply_attic(project, level.id, AtticGeometry(**_geometry()))


def test_sources_follow_the_weakest_input() -> None:
    assert weaker(Source.USER_DEFINED, Source.ESTIMATED) is Source.ESTIMATED
    assert weaker(Source.INFERRED, Source.ESTIMATED) is Source.INFERRED
    mixed = _geometry()
    mixed["width"] = Estimate(value=W, sigma=0.5, source=Source.INFERRED)
    mixed["ridge_height"] = Estimate(value=RIDGE, sigma=0.0, source=Source.USER_DEFINED)
    project = new_project("p")
    after, result = apply_attic(project, only_level(project).id, AtticGeometry(**mixed))
    level = only_level(after)
    gable = by_name(level, "Pignon", Wall)
    assert gable.source == "inferred" and gable.confidence == 0.2
    assert level.height_source == "user_defined"
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    assert roof.source == "inferred", "la pente hérite de la largeur inférée"
    assert result.step == "attic.apply" and result.confidence == 0.2 and result.patch


def test_geometry_validation() -> None:
    bad = _geometry()
    bad["eave_height"] = Estimate(value=RIDGE + 0.1, sigma=0.1)
    with pytest.raises(ValueError, match="plus bas que le faîtage"):
        AtticGeometry(**bad)
    bad = _geometry()
    bad["length"] = Estimate(value=80.0, sigma=0.1)
    with pytest.raises(ValueError, match="hors limites"):
        AtticGeometry(**bad)


def test_bake_clips_gables_under_the_roofs() -> None:
    project = attic_project()
    level = only_level(project)
    bake = bake_level(project, level.id)
    assert [p.kind for p in bake.prisms] == ["floor"], "les murs sous pente sont des polyèdres"
    kinds = sorted(p.kind for p in bake.polyhedra if p.kind != "timber")
    # Deux pentes, chacune prolongée sous son égout (bande + semelle sous le plancher).
    assert kinds == ["roof", "roof", "roof", "roof", "slab", "slab", "wall", "wall", "wall", "wall"]
    assert bake.bounds_max is not None
    assert bake.bounds_max.z == pytest.approx(RIDGE + ROOF_THICKNESS * math.cos(math.atan(SLOPE)))
    # Volumes mesurés sans la charpente apparente (C1, test_timber) : seulement l'enveloppe.
    bare = [p for p in bake.polyhedra if p.kind != "timber"]
    scene = build_scene(bake.model_copy(update={"polyhedra": bare}))
    gable = next(w for w in level.elements.values() if isinstance(w, Wall) and w.name == "Pignon")
    # Profil du pignon sous deux versants : rectangle W × égout + triangle de base W et de
    # hauteur (faîtage − égout).
    profile = W * EAVE + W * (RIDGE - EAVE) / 2.0
    assert abs(scene.geometry[str(gable.id)].volume) == pytest.approx(profile * T, rel=1e-3)
    eave_wall = by_name(level, "Égout bas", Wall)
    # Mur d'égout : boîte L × T × égout, moins le coin extérieur rasé par la pente.
    shaved = SLOPE * (T / 2) ** 2 / 2.0 * L
    assert abs(scene.geometry[str(eave_wall.id)].volume) == pytest.approx(
        L * T * EAVE - shaved, rel=1e-3
    )
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    slope_length = math.hypot(W / 2, RIDGE - EAVE)
    # La pente, sa bande prolongée sous l'égout (même plan, même épaisseur) et la semelle de
    # 3 cm sous le plancher, de part et d'autre de l'égout.
    skirt = ROOF_SKIRT_M / math.cos(roof.pitch)
    sill = L * 2 * (ROOF_SKIRT_M + 0.05) * 0.03
    assert abs(scene.geometry[str(roof.id)].volume) == pytest.approx(
        L * (slope_length + skirt) * ROOF_THICKNESS + sill, rel=1e-3
    )
    loaded = trimesh.load(io.BytesIO(glb_bytes(project, level.id)), file_type="glb")
    assert str(roof.id) in loaded.graph.nodes_geometry


def test_derived_roofs_and_svg_usable_line() -> None:
    project = attic_project()
    level = only_level(project)
    derived = compute_derived(level)
    assert len(derived.roofs) == 2 and len(derived.rooms) == 1
    low = next(r for r in derived.roofs.values() if r.eave[0].y == 0.0 and r.eave[1].y == 0.0)
    assert (low.inward.x, low.inward.y) == pytest.approx((0.0, 1.0))
    assert low.run_m == pytest.approx(W / 2)
    assert low.usable_offset_m == pytest.approx((1.8 - EAVE) / SLOPE)
    assert low.pitch_deg == pytest.approx(math.degrees(math.atan(SLOPE)))
    svg = render_svg(project, level.id)
    assert svg.count("1,80 m sous pente") == 2 and 'class="roof-low"' in svg
    # Égout déjà à 1,80 m : pas de zone basse ; faîtage sous 1,80 m : pas de ligne du tout.
    high_eave = _geometry()
    high_eave["eave_height"] = Estimate(value=2.0, sigma=0.1)
    high_eave["ridge_height"] = Estimate(value=3.0, sigma=0.1)
    p2 = new_project("h")
    after, _ = apply_attic(p2, only_level(p2).id, AtticGeometry(**high_eave))
    roofs = compute_derived(only_level(after)).roofs
    assert all(r.usable_offset_m == 0.0 for r in roofs.values())
    lowly = _geometry()
    lowly["eave_height"] = Estimate(value=0.2, sigma=0.1)
    lowly["ridge_height"] = Estimate(value=1.6, sigma=0.1)
    p3 = new_project("l")
    after, _ = apply_attic(p3, only_level(p3).id, AtticGeometry(**lowly))
    assert all(r.usable_offset_m is None for r in compute_derived(only_level(after)).roofs.values())
    assert 'class="roof-low"' not in render_svg(after, only_level(after).id)


def _geometry() -> dict[str, object]:
    return {
        "length": Estimate(value=L, sigma=0.3),
        "width": Estimate(value=W, sigma=0.3),
        "ridge_height": Estimate(value=RIDGE, sigma=0.3),
        "eave_height": Estimate(value=EAVE, sigma=0.1),
    }
