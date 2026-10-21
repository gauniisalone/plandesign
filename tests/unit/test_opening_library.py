"""E5 : bibliothèque d'ouvertures (double porte, coulissante, galandage, pliante,
porte-fenêtre, baie coulissante) : symboles, zones à dégager, règles, exports, assistant."""

from __future__ import annotations

import io

import pytest

from openplan.geom.opening_symbols import leaves_of, opening_symbol, stack_span
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.assistant_phrases import understand
from openplan.pipeline.export import render_dxf, render_pdf, render_svg
from openplan.scene.migrate import load_project
from openplan.scene.ops import AddFurniture, AddOpening, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Opening, OpeningKind, Swing, Wall
from openplan.schemas.scene import Project
from openplan.validation.layout import check_layout, door_zone, slide_zone
from tests.factories import only_level, rect_room_project


def _bottom_wall(project: Project) -> Wall:
    level = only_level(project)
    return next(e for e in level.elements.values() if isinstance(e, Wall) and e.name == "W1")


def _with(kind: OpeningKind, offset: float = 1.6, width: float = 1.0, **extra: object) -> Project:
    """Pièce 6 × 4 m sans ses ouvertures d'usine, plus une ouverture sur W1 (y = 0)."""
    project = rect_room_project(6.0, 4.0)
    level = only_level(project)
    elements = {k: e for k, e in level.elements.items() if not isinstance(e, Opening)}
    project = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    glass = (OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW)
    swing = None if kind in glass else Swing(hinge="left", direction="inward")
    return apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=_bottom_wall(project).id,
            kind=kind,
            offset=offset,
            width=width,
            height=2.04,
            swing=extra.pop("swing", swing),  # type: ignore[arg-type]
            **extra,  # type: ignore[arg-type]
        ),
    )


def _opening(project: Project) -> Opening:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Opening))


def test_double_door_has_two_leaves_split_one_third() -> None:
    project = _with(OpeningKind.DOUBLE_DOOR, width=1.2, split=1 / 3)
    o = _opening(project)
    leaves = leaves_of(o, (1.6, 0.0), (2.8, 0.0), (1.0, 0.0))
    assert [round(leaf.width, 3) for leaf in leaves] == [0.4, 0.8]
    sym = opening_symbol(o, (0.0, 0.0), (1.0, 0.0), 0.075)
    assert len(sym.arcs) == 2 and sum(1 for line in sym.lines if line.style == "leaf") == 2
    zone = door_zone(WallGraph.from_level(only_level(project)), o, {})
    assert zone is not None
    assert zone.area == pytest.approx(3.14159 / 4 * (0.4**2 + 0.8**2), rel=0.05)


def test_french_window_draws_glass_and_one_or_two_leaves() -> None:
    single = opening_symbol(
        _opening(_with(OpeningKind.FRENCH_WINDOW)), (0.0, 0.0), (1.0, 0.0), 0.075
    )
    double = opening_symbol(
        _opening(_with(OpeningKind.FRENCH_WINDOW, width=1.4, leaves=2)),
        (0.0, 0.0),
        (1.0, 0.0),
        0.075,
    )
    assert len(single.arcs) == 1 and len(double.arcs) == 2
    assert sum(1 for line in single.lines if line.style == "glass") == 2


def test_sliding_door_rail_and_free_strip_along_the_wall() -> None:
    project = _with(OpeningKind.SLIDING_DOOR, offset=2.0, width=0.83)
    o = _opening(project)
    assert stack_span(o) == pytest.approx((2.0 - 0.83, 2.0))
    sym = opening_symbol(o, (0.0, 0.0), (1.0, 0.0), 0.075)
    assert not sym.arcs, "pas de débattement"
    rail = next(line for line in sym.lines if line.style == "rail")
    xs = [p[0] for p in rail.points]
    assert min(xs) == pytest.approx(2.0 - 0.83) and max(xs) == pytest.approx(2.83)
    zone = slide_zone(WallGraph.from_level(only_level(project)), o)
    assert zone is not None and zone.bounds[0] == pytest.approx(1.17, abs=0.01)
    # Une table de chevet contre le mur, là où le vantail s'efface : refusé (dure).
    blocked = apply_op(
        project,
        only_level(project).id,
        AddFurniture(asset_id="nightstand", position=Point2(x=1.5, y=0.3)),
    )
    hit = [v for v in check_layout(blocked) if v.type == "sliding_zone_blocked"]
    assert hit and hit[0].severity == "high"
    # Devant la baie (pas sur le trajet du vantail) : pas cette règle.
    aside = apply_op(
        project,
        only_level(project).id,
        AddFurniture(asset_id="nightstand", position=Point2(x=4.5, y=0.3)),
    )
    assert not [v for v in check_layout(aside) if v.type == "sliding_zone_blocked"]


def test_sliding_and_pocket_doors_need_free_wall_on_their_side() -> None:
    near_corner = _with(OpeningKind.POCKET_DOOR, offset=0.3, width=0.83)
    room = [v for v in check_layout(near_corner) if v.type == "slide_no_room"]
    assert room and room[0].severity == "medium" and "à gauche" in room[0].message
    other_side = _with(
        OpeningKind.POCKET_DOOR,
        offset=0.3,
        width=0.83,
        swing=Swing(hinge="right", direction="inward"),
    )
    assert not [v for v in check_layout(other_side) if v.type == "slide_no_room"]


def test_folding_door_zone_is_the_folded_panels_strip() -> None:
    project = _with(OpeningKind.FOLDING_DOOR, width=0.9)
    o = _opening(project)
    zone = door_zone(WallGraph.from_level(only_level(project)), o, {})
    assert zone is not None and zone.area < 0.9 * 0.9 * 3.14159 / 4
    sym = opening_symbol(o, (0.0, 0.0), (1.0, 0.0), 0.075)
    assert len(sym.lines[0].points) == 5, "zigzag de quatre panneaux"


def test_sliding_window_counts_as_glazing_not_as_a_door() -> None:
    project = _with(OpeningKind.SLIDING_WINDOW, width=2.4)
    o = _opening(project)
    assert o.swing is None
    sym = opening_symbol(o, (0.0, 0.0), (1.0, 0.0), 0.075)
    assert sum(1 for line in sym.lines if line.style == "glass") == 2
    with pytest.raises(ValueError, match="porte"):
        Opening(
            kind=OpeningKind.WINDOW,
            host_wall=o.host_wall,
            offset=0,
            width=1,
            height=1,
            swing=Swing(hinge="left", direction="inward"),
            source=Source.USER_DEFINED,
            confidence=1.0,
        )


@pytest.mark.parametrize("kind", [k for k in OpeningKind if k is not OpeningKind.PASSAGE])
def test_every_kind_exports_to_svg_pdf_dxf(kind: OpeningKind) -> None:
    import ezdxf

    project = _with(kind, width=1.4 if kind is OpeningKind.DOUBLE_DOOR else 0.9)
    level_id = only_level(project).id
    svg = render_svg(project, level_id)
    assert f"opening {kind.value}" in svg
    assert render_pdf(project, level_id).startswith(b"%PDF-")
    doc = ezdxf.read(io.StringIO(render_dxf(project, level_id)))
    assert len(doc.modelspace().query('*[layer=="OUVERTURES"]')) >= 1


def test_a_030_project_loads_unchanged() -> None:
    project = rect_room_project()
    data = project.model_dump(mode="json")
    data["schema_version"] = "0.3.0"
    loaded = load_project(data)
    door = next(
        e for e in only_level(loaded).elements.values() if isinstance(e, Opening) and e.swing
    )
    assert door.leaves == 1 and door.split == 0.5 and loaded.schema_version == "0.17.0"


@pytest.mark.parametrize(
    ("text", "kind", "leaves"),
    [
        ("ajoute une double porte sur le mur du fond", "double_door", None),
        ("mets une porte coulissante", "sliding_door", None),
        ("ajoute une porte à galandage sur le mur de gauche", "pocket_door", None),
        ("ajoute une baie vitrée sur le mur du fond", "sliding_window", None),
        ("ajoute une porte-fenêtre double sur le mur de droite", "french_window", 2),
        ("ajoute une porte pliante", "folding_door", None),
        ("ajoute une fenêtre sur le mur du fond", "window", None),
        ("ajoute une porte sur le mur du fond", "door", None),
    ],
)
def test_assistant_understands_the_library(text: str, kind: str, leaves: int | None) -> None:
    reply = understand(text, ["bed", "desk"])
    assert reply is not None
    intent = reply.intents[0]
    assert intent.kind == "add_opening" and intent.opening == kind and intent.leaves == leaves
