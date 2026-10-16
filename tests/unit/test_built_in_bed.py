"""Chantier H, lot H1 : lit encastré sous pente (meuble paramétrique `bed_builtin`)."""

from __future__ import annotations

import math

import pytest

from openplan.geom.built_in_bed import (
    BUILT_IN_BED,
    MATTRESS_TOP_M,
    MIN_HEADROOM_M,
    drawer_count,
    parts,
    pillow_point,
)
from openplan.geom.furniture_symbols import symbol_for
from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.assistant_phrases import understand
from openplan.pipeline.layouts import (
    LayoutBody,
    ZoneBriefBody,
    brief_from_intent,
    build_context,
    classic_beds,
    generate_layouts,
)
from openplan.pipeline.scene3d import bake_level
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, OpError, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import bed_headroom_at, check_layout, clearance_at
from tests.factories import attic_project, only_level


def test_parts_head_base_mattress_and_drawers_on_both_sides() -> None:
    boxes = parts(1.4, 2.05)
    kinds = [b.part for b in boxes]
    assert kinds[:3] == ["head", "base", "mattress"]
    assert kinds.count("drawer") == 2 * drawer_count(2.05) == 6
    mattress = boxes[2]
    assert mattress.z1 == pytest.approx(MATTRESS_TOP_M) == pytest.approx(0.55)
    head = boxes[0]
    assert head.v0 == pytest.approx(-1.025) and head.z1 == pytest.approx(0.80)


def test_catalog_card_and_symbol() -> None:
    item = load_catalog().get("bed_builtin")
    assert item is not None and item.procedural == "built_in_bed" and item.model is None
    assert item.room == "chambre" and item.width.min == 0.9 and item.width.max == 1.6
    assert symbol_for(BUILT_IN_BED).paths, "un symbole propre, pas le générique"


def _attic_points() -> tuple[Project, Level, list[DerivedRoof]]:
    project = attic_project()
    level = only_level(project)
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    return project, level, roofs


def _place(project, level, x: float, y: float, yaw: float):  # type: ignore[no-untyped-def]
    return apply_op(
        project,
        level.id,
        AddFurniture(asset_id="bed_builtin", position=Point2(x=x, y=y), yaw=yaw, width=0.9),
    )


def _pose_against_low_wall(level, roofs, offset: float) -> tuple[float, float, float]:  # type: ignore[no-untyped-def]
    """Lit perpendiculaire à l'égout du premier versant, tête vers la jambette, à `offset`."""
    roof = roofs[0]
    (a, b) = roof.eave
    ex, ey = b.x - a.x, b.y - a.y
    n = math.hypot(ex, ey)
    ux, uy = ex / n, ey / n
    # vers l'intérieur : le point du milieu du niveau
    xs = [v.position.x for v in level.vertices.values()]
    ys = [v.position.y for v in level.vertices.values()]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
    ix, iy = -uy, ux
    if (cx - mx) * ix + (cy - my) * iy < 0:
        ix, iy = -ix, -iy
    depth = 2.05
    x, y = mx + ix * (offset + depth / 2), my + iy * (offset + depth / 2)
    yaw = math.atan2(-ix, iy)  # face avant (pied) vers l'intérieur : (−sin, cos) = (ix, iy)
    return x, y, yaw


def test_headroom_rule_refuses_a_bed_too_close_to_the_low_wall() -> None:
    project, level, roofs = _attic_points()
    near = _pose_against_low_wall(level, roofs, 0.0)
    free = bed_headroom_at(level, roofs, near[0], near[1], near[2], 2.05)
    if free >= MIN_HEADROOM_M:
        pytest.skip("combles d'essai trop hauts pour la règle")
    with pytest.raises(OpError, match=r"0[,.]90"):
        _place(project, level, *near)
    # Plus loin de la jambette, l'oreiller a sa hauteur : accepté, sans remarque.
    for offset in (0.5, 0.8, 1.1, 1.4):
        x, y, yaw = _pose_against_low_wall(level, roofs, offset)
        if bed_headroom_at(level, roofs, x, y, yaw, 2.05) >= MIN_HEADROOM_M:
            after = _place(project, level, x, y, yaw)
            assert not [v for v in check_layout(after) if v.type == "bed_headroom"]
            px, py = pillow_point(x, y, yaw, 2.05)
            assert clearance_at((px, py), roofs, level) - MATTRESS_TOP_M >= MIN_HEADROOM_M
            return
    pytest.fail("aucune place ne laisse 0,90 m au-dessus du matelas")


def test_generator_uses_built_in_beds_in_attics_with_headroom() -> None:
    project, level, roofs = _attic_points()
    brief = brief_from_intent(project, level.id, ZoneBriefBody())
    assert any(i.category == BUILT_IN_BED for i in brief.items)
    run = generate_layouts(project, level.id, LayoutBody(brief=brief, variants=1, budget_s=20))
    best = run.proposals[0]
    beds = [f for f in best.furniture if f.category == BUILT_IN_BED]
    assert beds, "les combles d'essai laissent la place d'un lit encastré"
    for bed in beds:
        t = bed.transform
        free = bed_headroom_at(level, roofs, t.position.x, t.position.y, t.yaw, bed.bbox.depth)
        assert free >= MIN_HEADROOM_M - 1e-6
    assert build_context(project, level.id, None).landmarks.low_zone is not None


def test_classic_fallback_brief_swaps_each_built_in_bed() -> None:
    project, level, _ = _attic_points()
    brief = brief_from_intent(project, level.id, ZoneBriefBody())
    classic = classic_beds(brief)
    cats = [i.category for i in classic.items]
    # une rangée de lits encastrés (H5) revient en lits simples, un seul en lit double
    assert BUILT_IN_BED not in cats and "nightstand" in cats
    assert "bed_single" in cats or "bed_double" in cats


def test_bake_splits_the_bed_into_wood_and_linen_parts() -> None:
    project, level, roofs = _attic_points()
    x, y, yaw = _pose_against_low_wall(level, roofs, 1.4)
    after = _place(project, level, x, y, yaw)
    bake = bake_level(after, level.id, style="realistic")
    bed = next(
        e for e in only_level(after).elements.values() if getattr(e, "category", "") == BUILT_IN_BED
    )
    prisms = [p for p in bake.prisms if p.element_id == bed.id]
    assert len(prisms) == 3 + 2 * drawer_count(2.05)
    assert {p.finish for p in prisms} >= {"linen"}
    assert len({p.name for p in prisms}) == len(prisms), "chaque morceau a son nom"


def test_assistant_understands_a_built_in_bed() -> None:
    reply = understand("ajoute un lit encastré sous la pente", load_catalog().categories)
    assert reply is not None
    assert reply.intents[0].kind == "add_furniture" and reply.intents[0].category == BUILT_IN_BED
