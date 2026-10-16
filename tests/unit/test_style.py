"""V1 (note 10, point 3) : l'ambiance choisit les matières par surface et le bois des meubles ;
les choix de l'utilisateur priment ; velux comptés comme lumière ; tapis réduits."""

from __future__ import annotations

import pytest

from openplan.pipeline.layouts import _smaller, build_context, expand_brief
from openplan.pipeline.scene3d import bake_level
from openplan.scene.ops import AddFurniture, AddRoofWindow, SetIntent, UpdateElement, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Floor, RoofPlane, Room, Wall
from openplan.schemas.intent import COLORS
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


def _room(project: Project) -> Room:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Room))


def _styled(project: Project, **fields: object) -> Project:
    return apply_op(
        project, only_level(project).id, SetIntent(room_id=_room(project).id, fields=fields)
    )


def _finishes(project: Project) -> dict[str, set[str]]:
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    walls = {e.id: e for e in level.elements.values() if isinstance(e, Wall)}
    out: dict[str, set[str]] = {}
    for p in bake.prisms:
        key = "wall:" + (walls[p.element_id].name or "") if p.element_id in walls else p.kind
        out.setdefault(key, set()).add(p.finish)
    for poly in bake.polyhedra:
        key = (
            "wall:" + (walls[poly.element_id].name or "") if poly.element_id in walls else poly.kind
        )
        out.setdefault(key, set()).add(poly.finish)
    return out


def _with_armchair(project: Project) -> Project:
    return apply_op(
        project,
        only_level(project).id,
        AddFurniture(asset_id="armchair", position=Point2(x=3.0, y=2.5)),
    )


def test_without_intent_the_materials_stay_as_before() -> None:
    project = _with_armchair(attic_project())
    f = _finishes(project)
    assert f["wall:Pignon"] == {"stone"} or any("stone" in v for k, v in f.items() if "Pignon" in k)
    assert f["timber"] == {"timber"}
    bake = bake_level(project, only_level(project).id, style="realistic")
    assert all(p.woods is None for p in bake.prisms if p.model)


def test_scandinavian_whitens_walls_slopes_timber_and_furniture() -> None:
    project = _styled(_with_armchair(attic_project()), style="scandinave")
    f = _finishes(project)
    gables = set().union(*(v for k, v in f.items() if "Pignon" in k))
    assert gables == {"limewash"}
    assert f["roof"] == {"painted_wood"} and f["timber"] == {"painted_wood"}
    assert f["floor"] == {"wood_floor"}
    bake = bake_level(project, only_level(project).id, style="realistic")
    chair = next(p for p in bake.prisms if p.model)
    assert chair.woods == {"wood": "painted_wood", "woodDark": "wood_floor"}


def test_a_finish_chosen_by_hand_beats_the_style() -> None:
    project = attic_project()
    level = only_level(project)
    gable = next(
        e for e in level.elements.values() if isinstance(e, Wall) and "Pignon" in (e.name or "")
    )
    project = apply_op(
        project,
        level.id,
        UpdateElement(
            element_id=gable.id,
            fields={"metadata": {"finish": "stone", "finish_source": "user_defined"}},
        ),
    )
    styled = _styled(project, style="contemporain")
    bake = bake_level(styled, level.id, style="realistic")
    mine = {p.finish for p in [*bake.prisms, *bake.polyhedra] if p.element_id == gable.id}
    others = {
        p.finish
        for p in [*bake.prisms, *bake.polyhedra]
        if p.element_id != gable.id and p.kind == "wall"
    }
    assert mine == {"stone"}, "choisi à la main : la pierre reste"
    assert "limewash" in others, "le reste suit le style"


def test_intent_materials_override_the_style_and_palette_colours_the_fabric() -> None:
    project = _styled(
        _with_armchair(attic_project()),
        style="naturel",
        materials={"floor": "concrete_floor"},
        palette=["terracotta", "lin", "noyer"],
    )
    assert _finishes(project)["floor"] == {"concrete_floor"}
    bake = bake_level(project, only_level(project).id, style="realistic")
    chair = next(p for p in bake.prisms if p.model)
    assert chair.tints is not None
    r, g, b = COLORS["terracotta"]
    assert chair.tints["carpet"] == pytest.approx(
        ((r / 255) ** 2.2, (g / 255) ** 2.2, (b / 255) ** 2.2)
    )
    assert any(isinstance(e, Floor) for e in only_level(project).elements.values())


def test_skylights_count_as_light_for_the_layout() -> None:
    project = attic_project()
    level = only_level(project)
    before = len(build_context(project, level.id, None).landmarks.windows)
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    project = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=2.0, distance=1.0))
    assert len(build_context(project, level.id, None).landmarks.windows) == before + 1


def test_a_rug_shrinks_within_the_catalogue_range() -> None:
    project = attic_project()
    level = only_level(project)
    ctx = build_context(project, level.id, None)
    (rug,), _ = expand_brief(LayoutBrief(items=[BriefItem(category="rug")]), ctx)
    sizes = _smaller(rug)
    assert sizes and all(s.width < rug.width for s in sizes)
    assert all(s.width >= rug.asset.width.min - 1e-9 for s in sizes)
    assert all(s.depth >= rug.asset.depth.min - 1e-9 for s in sizes)
