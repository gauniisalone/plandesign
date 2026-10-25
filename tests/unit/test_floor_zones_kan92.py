"""KAN-92, lot L1 : zones de sol surélevées et limites de Pousser/Tirer, côté serveur. Forme et
place de la zone, limites dites (épaisseur 5 à 60 cm, hauteur jusqu'au plafond, zone 0 à
1,20 m), un pas d'historique par geste, 3D (zone, meuble posé dessus, porte d'un mur épaissi),
surface de la pièce, aménagement automatique, exports SVG, PDF et DXF."""

from __future__ import annotations

import io
from uuid import UUID

import ezdxf
import pytest

from openplan.geom.floor_zones import FLAT_M, lift_at, quad_problem
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.export import render_dxf, render_pdf, render_svg, zone_hatch
from openplan.pipeline.layouts import build_context
from openplan.pipeline.scene3d import bake_level
from openplan.scene import history
from openplan.scene.ops import (
    ZONE_OUTSIDE,
    AddFloorZone,
    AddFurniture,
    OpError,
    UpdateElement,
    apply_op,
)
from openplan.schemas.common import Point2
from openplan.schemas.elements import FloorZone, Furniture, Opening, Room, Wall
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project

SQUARE = [Point2(x=1.0, y=1.0), Point2(x=2.5, y=1.0), Point2(x=2.5, y=2.2), Point2(x=1.0, y=2.2)]


def _zone(project: Project) -> tuple[Project, UUID, FloorZone]:
    level = only_level(project)
    after = apply_op(project, level.id, AddFloorZone(polygon=SQUARE))
    zone = next(e for e in after.levels[level.id].elements.values() if isinstance(e, FloorZone))
    return after, level.id, zone


def _set(project: Project, level_id: UUID, eid: UUID, **fields: object) -> Project:
    return apply_op(project, level_id, UpdateElement(element_id=eid, fields=fields))  # type: ignore[arg-type]


def _wall(project: Project) -> Wall:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Wall))


# ------------------------------------------------------------------ forme et place
def test_a_zone_is_a_simple_quad_of_a_sensible_size() -> None:
    assert quad_problem(SQUARE) is None
    assert "4 côtés" in (quad_problem(SQUARE[:3]) or "")
    bowtie = [SQUARE[0], SQUARE[2], SQUARE[1], SQUARE[3]]
    assert "se croisent" in (quad_problem(bowtie) or "")
    tiny = [Point2(x=1.0, y=1.0), Point2(x=1.1, y=1.0), Point2(x=1.1, y=1.1), Point2(x=1.0, y=1.1)]
    assert "trop petite" in (quad_problem(tiny) or "")


def test_a_zone_is_drawn_in_a_room_in_one_undoable_step() -> None:
    project = rect_room_project()
    after, level_id, zone = _zone(project)
    room = next(e for e in after.levels[level_id].elements.values() if isinstance(e, Room))
    assert zone.room == room.id and zone.elevation == 0.0 and zone.source == "user_defined"
    assert len(after.history) == len(project.history) + 1
    assert after.history[-1].label == "Dessiner une zone de sol"
    assert history.undo(after).levels[level_id] == project.levels[level_id]
    outside = [Point2(x=p.x + 3.5, y=p.y) for p in SQUARE]  # la pièce fait 4 × 3 m
    with pytest.raises(OpError, match=ZONE_OUTSIDE):
        apply_op(project, level_id, AddFloorZone(polygon=outside))


def test_raising_and_reshaping_a_zone_keeps_its_limits() -> None:
    project, level_id, zone = _zone(rect_room_project())
    raised = _set(project, level_id, zone.id, elevation=0.20)
    assert raised.levels[level_id].elements[zone.id].elevation == pytest.approx(0.20)  # type: ignore[union-attr]
    assert raised.history[-1].label == "Élever la zone de sol"
    assert history.undo(raised).levels[level_id] == project.levels[level_id], "un seul pas"
    for bad in (1.50, -0.10):
        with pytest.raises(OpError, match="zone de sol de 0 à 1,20 m"):
            _set(project, level_id, zone.id, elevation=bad)
    moved = [{"x": p.x + 3.5, "y": p.y} for p in SQUARE]
    with pytest.raises(OpError, match=ZONE_OUTSIDE):
        _set(project, level_id, zone.id, polygon=moved)


# ------------------------------------------------------------------ murs
def test_wall_thickness_and_height_have_their_limits_said_in_words() -> None:
    project = rect_room_project(height=2.5)
    level_id = only_level(project).id
    wall = _wall(project)
    thick = _set(project, level_id, wall.id, thickness=0.30)
    assert thick.history[-1].label == "Épaisseur du mur"
    with pytest.raises(OpError, match=r"de 5 cm à 60 cm \(demandé : 72 cm\)"):
        _set(project, level_id, wall.id, thickness=0.72)
    with pytest.raises(OpError, match=r"demandé : 4 cm"):
        _set(project, level_id, wall.id, thickness=0.04)
    low = _set(project, level_id, wall.id, height=1.10)
    assert low.history[-1].label == "Hauteur du mur"
    with pytest.raises(OpError, match="jusqu'au plafond du niveau, 2,5"):
        _set(project, level_id, wall.id, height=2.80)


def test_a_door_follows_a_thicker_wall_and_the_inner_area_shrinks() -> None:
    """Écart 3 : la porte prend l'épaisseur du mur, sans rien stocker. Écart 2 : épaissir un
    mur réduit la surface intérieure (calculée aux faces intérieures), c'est attendu."""
    project = rect_room_project()
    level = only_level(project)
    door = next(e for e in level.elements.values() if isinstance(e, Opening) and e.kind == "door")
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    before = WallGraph.from_level(level).room_area(room.id)
    thick = _set(project, level.id, door.host_wall, thickness=0.40)
    baked = next(o for o in bake_level(thick, level.id).openings if o.element_id == door.id)
    assert baked.wall_thickness == pytest.approx(0.40)
    after = WallGraph.from_level(thick.levels[level.id]).room_area(room.id)
    assert after < before


# ------------------------------------------------------------------ 3D, surface, aménagement
def test_the_zone_is_baked_and_a_piece_of_furniture_on_it_goes_up() -> None:
    project, level_id, zone = _zone(rect_room_project())
    flat = next(p for p in bake_level(project, level_id).prisms if p.element_id == zone.id)
    assert flat.kind == "floor" and flat.z1 - flat.z0 == pytest.approx(FLAT_M), "attrapable à 0 m"
    sofa = apply_op(
        project, level_id, AddFurniture(asset_id="sofa_2", position=Point2(x=1.75, y=1.6))
    )
    raised = _set(sofa, level_id, zone.id, elevation=0.40)
    prisms = bake_level(raised, level_id).prisms
    furniture = next(
        e for e in raised.levels[level_id].elements.values() if isinstance(e, Furniture)
    )
    box = next(p for p in prisms if p.element_id == furniture.id)
    stored = furniture.transform.position.z
    assert box.z0 == pytest.approx(stored + 0.40), "le meuble monte avec la zone"
    assert stored == 0.0, "rien n'est stocké"
    zone_prism = next(p for p in prisms if p.element_id == zone.id)
    assert zone_prism.z1 == pytest.approx(0.40)
    assert lift_at([raised.levels[level_id].elements[zone.id]], 0.2, 0.2) == 0.0  # type: ignore[list-item]


def test_a_zone_leaves_the_room_area_alone_and_layouts_keep_off_its_edge() -> None:
    project = rect_room_project()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    before = WallGraph.from_level(level).room_area(room.id)
    with_zone, level_id, zone = _zone(project)
    raised = _set(with_zone, level_id, zone.id, elevation=0.20)
    assert WallGraph.from_level(raised.levels[level_id]).room_area(room.id) == before
    ctx = build_context(raised, level_id, room.id)
    edge_middle = (1.75, 1.0)  # milieu du bord bas de la zone
    from shapely.geometry import Point

    assert ctx.forbidden.contains(Point(*edge_middle)), "jamais à cheval sur la marche"
    assert not ctx.forbidden.contains(Point(1.75, 1.6)), "sur la zone : permis"


# ------------------------------------------------------------------ exports
def test_the_zone_is_drawn_in_every_export() -> None:
    project, level_id, zone = _zone(rect_room_project())
    raised = _set(project, level_id, zone.id, elevation=0.20)
    stored = raised.levels[level_id].elements[zone.id]
    assert isinstance(stored, FloorZone) and len(zone_hatch(stored)) > 5
    svg = render_svg(raised, level_id)
    assert 'class="floor-zone"' in svg and "+20 cm" in svg and "floor-zone-hatch" in svg
    assert render_pdf(raised, level_id).startswith(b"%PDF")
    doc = ezdxf.read(io.StringIO(render_dxf(raised, level_id)))
    layer = [e for e in doc.modelspace() if e.dxf.layer == "ZONES_SOL"]
    assert any(e.dxftype() == "TEXT" and e.dxf.text == "+20 cm" for e in layer)
    assert sum(e.dxftype() == "LINE" for e in layer) > 5
