"""D4 : tapis traversable, habillage du rendu (lampes, plantes, cadres)."""

from __future__ import annotations

import pytest

from openplan.pipeline.decor import (
    DECOR_BOOKS,
    DECOR_FLOOR_PLANT,
    DECOR_LAMP,
    MATTRESS_TOP,
    PALETTES,
    PENDANT_MIN_BOTTOM_M,
    ambiance,
    dress_level,
)
from openplan.pipeline.layouts import LayoutBody, generate_layouts
from openplan.pipeline.scene3d import bake_level
from openplan.scene.catalog import asset
from openplan.scene.ops import AddFurniture, AddOpening, UpdateElement, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Furniture, Room, Wall
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.validation.layout import check_layout
from tests.factories import ATTIC, attic_project, only_level, rect_room_project


def test_a_rug_is_walkable_but_a_desk_is_not() -> None:
    assert asset("rug_rect").walkable and asset("rug_round").walkable
    assert not asset("desk_140").walkable
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bed_single_90", position=Point2(x=2.0, y=1.5), yaw=1.5708),
    )
    with_rug = apply_op(
        project, level_id, AddFurniture(asset_id="rug_rect", position=Point2(x=2.0, y=1.5))
    )
    assert not [v for v in check_layout(with_rug) if v.type == "furniture_overlap"]
    with_desk = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    assert [v for v in check_layout(with_desk) if v.type == "furniture_overlap"]


def test_the_generator_lays_a_rug_under_the_furniture() -> None:
    project = rect_room_project(5.0, 4.0)
    level_id = only_level(project).id
    brief = LayoutBrief(items=[BriefItem(category="bed_double"), BriefItem(category="rug")])
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=1, seed=1))
    assert run.proposals, run.result.warnings
    assert {f.category for f in run.proposals[0].furniture} == {"bed_double", "rug"}


def test_dressing_lights_nightstands_and_frames_beds_against_a_wall() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    # Lit adossé au mur du fond (y = 3), face vers la pièce ; chevet à côté.
    project = apply_op(
        project,
        level_id,
        AddFurniture(
            asset_id="bed_single_90",
            position=Point2(x=2.0, y=3.0 - 0.075 - 0.02 - 1.0),
            yaw=3.14159265,
        ),
    )
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="nightstand", position=Point2(x=3.2, y=2.6), yaw=3.14159265),
    )
    decor = dress_level(only_level(project))
    lamps = [d for d in decor if d.kind == "table_lamp"]
    frames = [d for d in decor if d.kind == "frame"]
    assert len(lamps) == 1 and lamps[0].model == DECOR_LAMP and lamps[0].light_w > 0
    assert lamps[0].z == pytest.approx(asset("nightstand").height.default)
    assert len(frames) == 1 and frames[0].y > 2.85, "contre le mur du fond"
    assert bake_level(project, level_id, style="realistic").decor
    assert not bake_level(project, level_id).decor, "habillage du rendu réaliste seulement"


def test_no_frame_where_the_slope_comes_down_too_low() -> None:
    project = attic_project()  # égouts à 1 m sur les murs y = 0 et y = 5
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bed_single_90", position=Point2(x=3.0, y=0.075 + 0.02 + 1.0)),
    )
    assert not [d for d in dress_level(only_level(project)) if d.kind == "frame"]


def test_a_rug_asked_alone_goes_under_existing_furniture() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bed_double_160", position=Point2(x=2.0, y=1.5), yaw=1.5708),
    )
    brief = LayoutBrief(items=[BriefItem(category="rug")])
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=1, seed=1))
    assert run.proposals and run.proposals[0].furniture[0].category == "rug"


# ------------------------------------------------------------------ C3 : habillage enrichi
def _with_bed(project, asset_id: str = "bed_double_160"):  # type: ignore[no-untyped-def]
    level_id = only_level(project).id
    return apply_op(
        project,
        level_id,
        AddFurniture(asset_id=asset_id, position=Point2(x=2.0, y=1.5), yaw=0.0),
    )


def test_beds_get_cushions_and_a_throw_on_the_duvet() -> None:
    project = _with_bed(rect_room_project())
    decor = dress_level(only_level(project))
    pillows = [d for d in decor if d.kind == "pillow"]
    throws = [d for d in decor if d.kind == "throw"]
    bed_h = asset("bed_double_160").height.default
    assert len(pillows) == 2 and len(throws) == 1
    assert all(p.z == pytest.approx(MATTRESS_TOP * bed_h) for p in pillows)
    # Lit en (2 ; 1,5), face avant vers +y : la tête est côté −y, le plaid côté pied.
    assert all(p.y < 1.5 for p in pillows) and throws[0].y > 1.5
    assert pillows[0].color == PALETTES["naturel"].accent


def test_the_room_ambiance_changes_colours_and_dosage() -> None:
    project = _with_bed(rect_room_project())
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    assert ambiance(level) == "naturel", "défaut"
    chalet = apply_op(
        project,
        level.id,
        UpdateElement(element_id=room.id, fields={"metadata": {"ambiance": "chalet"}}),
    )
    assert ambiance(only_level(chalet)) == "chalet"
    throw = next(d for d in dress_level(only_level(chalet)) if d.kind == "throw")
    assert throw.color == PALETTES["chalet"].accent
    bake = bake_level(chalet, level.id, style="realistic")
    assert bake.palette is not None and bake.palette.style == "chalet"
    assert bake_level(chalet, level.id).palette is None
    styles = {p.rug for p in PALETTES.values()}
    assert len(styles) == len(PALETTES), "un tapis par ambiance"
    for pal in PALETTES.values():
        contrast = sum(abs(a - b) for a, b in zip(pal.rug, pal.rug_stripe, strict=True))
        assert contrast > 0.6, f"rayures du tapis trop peu contrastées ({pal.style})"


def test_pendant_hangs_under_the_ridge_and_clears_heads() -> None:
    project = attic_project()  # faîtage 3,2 m
    decor = dress_level(only_level(project))
    pendants = [d for d in decor if d.kind == "pendant"]
    assert len(pendants) == 1
    p = pendants[0]
    assert p.z >= PENDANT_MIN_BOTTOM_M - 1e-9
    assert p.z + p.height == pytest.approx(ATTIC["ridge_height"], abs=0.05), "accrochée au faîtage"
    assert p.light_w > 0 and p.material == PALETTES["naturel"].pendant


def test_floor_plants_stand_in_free_corners_with_headroom() -> None:
    project = _with_bed(rect_room_project())
    level = only_level(project)
    plants = [d for d in dress_level(level) if d.kind == "floor_plant"]
    assert 1 <= len(plants) <= PALETTES["naturel"].floor_plants
    bed = next(e for e in level.elements.values() if isinstance(e, Furniture))
    for p in plants:
        assert p.model == DECOR_FLOOR_PLANT
        far = (
            abs(p.x - bed.transform.position.x) > 0.8 + 0.2
            or abs(p.y - bed.transform.position.y) > 1.0 + 0.2
        )
        assert far, "pas dans le lit"
    # Sous des pentes, jamais dans les coins d'égout (égout à 1 m) : seulement là où la plante
    # tient debout sur toute son emprise, au milieu des pignons sous le faîtage.
    attic = attic_project()
    for p in [d for d in dress_level(only_level(attic)) if d.kind == "floor_plant"]:
        assert 1.2 <= p.y <= 3.8, "loin des égouts (y = 0 et y = 5)"


def _curtains_of(project, level_id):  # type: ignore[no-untyped-def]
    return [
        d for d in bake_level(project, level_id, style="realistic").decor if d.kind == "curtain"
    ]


def test_curtains_frame_each_window_on_the_room_side() -> None:
    project = rect_room_project()
    level = only_level(project)
    before = _curtains_of(project, level.id)
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    project = apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=wall.id, kind="window", offset=1.0, width=1.0, height=1.2, sill_height=0.9
        ),
    )
    curtains = _curtains_of(project, level.id)
    assert len(curtains) - len(before) == 2, "deux rideaux pour la nouvelle fenêtre"
    rooms = [e for e in only_level(project).elements.values() if isinstance(e, Room)]
    from shapely.geometry import Point

    from openplan.geom.wallgraph import WallGraph

    poly = WallGraph.from_level(only_level(project)).room_polygon(rooms[0].id)
    assert all(poly.contains(Point(c.x, c.y)) for c in curtains), "côté pièce"
    assert all(c.height > 2.0 for c in curtains), "du sol au-dessus de la baie"


def test_books_fill_the_open_shelves() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bookshelf_80", position=Point2(x=2.0, y=0.3), yaw=0.0),
    )
    books = [d for d in dress_level(only_level(project)) if d.kind == "books"]
    assert len(books) == 3 and all(b.model == DECOR_BOOKS for b in books)
    assert len({round(b.z, 2) for b in books}) == 3, "une rangée par tablette"
