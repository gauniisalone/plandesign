"""KAN-74 : un égout à 0 m (ou inconnu), au-delà des murs, comme dans le vrai grenier : les murs
bas se reconnaissent à la hauteur réelle de la pente, et le générateur y pose des lits encastrés
tête sous le rampant, avec 0,90 m à l'oreiller et 1,80 m devant le pied."""

from __future__ import annotations

import math

import pytest

from openplan.geom.built_in_bed import BUILT_IN_BED, MIN_HEADROOM_M, STANDING_M, standing_at
from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.layouts import (
    LayoutBody,
    _bed_setbacks,
    _head_is_lower,
    build_context,
    expand_brief,
    generate_layouts,
    want_for,
)
from openplan.schemas.common import Point2
from openplan.schemas.elements import RoofPlane
from openplan.schemas.scene import Project
from openplan.validation.layout import bed_headroom_at
from tests.factories import only_level
from tests.unit.test_timber_reveal import _attic
from tests.unit.test_velux_chevetre import BUILT_IN


def _eave_outside(pitch_deg: float) -> Project:
    """Les combles d'essai, chaque égout ramené à 0 m en le reculant au-delà du mur : la pente
    est exactement la même, seule la ligne d'égout change (comme le vrai grenier)."""
    project = _attic(pitch_deg)
    level = only_level(project)
    for rid, roof in derived_roofs(level, WallGraph.from_level(level)).items():
        element = level.elements[rid]
        assert isinstance(element, RoofPlane)
        d = element.eave_height / math.tan(element.pitch)
        dx, dy = -roof.inward.x * d, -roof.inward.y * d
        level.elements[rid] = element.model_copy(
            update={
                "eave_start": Point2(x=element.eave_start.x + dx, y=element.eave_start.y + dy),
                "eave_end": Point2(x=element.eave_end.x + dx, y=element.eave_end.y + dy),
                "eave_height": 0.0,
            }
        )
    return project


@pytest.mark.parametrize("pitch", [33, 45])
def test_low_walls_are_found_from_the_real_slope(pitch: float) -> None:
    attic = _attic(pitch)
    usual = build_context(attic, only_level(attic).id, None)
    outside = _eave_outside(pitch)
    ctx = build_context(outside, only_level(outside).id, None)
    assert [f.eave for f in ctx.faces] == [f.eave for f in usual.faces]
    assert sum(f.eave for f in ctx.faces) == 2


def test_bed_setbacks_reach_the_headroom_of_the_real_slope() -> None:
    project = _eave_outside(33)
    level = only_level(project)
    ctx = build_context(project, level.id, None)
    wants, _ = expand_brief(BUILT_IN, ctx)
    bed = next(w for w in wants if w.asset.category == BUILT_IN_BED)
    face = next(f for f in ctx.faces if f.eave)
    setbacks = _bed_setbacks(face, ctx)
    assert setbacks == tuple(sorted(setbacks)) and len(setbacks) >= 9
    fitting = []
    for s in setbacks:
        back = bed.depth / 2.0 + 0.02 + s
        x = face.start[0] + face.unit[0] * face.length / 2 + face.inward[0] * back
        y = face.start[1] + face.unit[1] * face.length / 2 + face.inward[1] * back
        yaw = math.atan2(face.inward[1], face.inward[0]) - math.pi / 2.0
        if bed_headroom_at(ctx.level, ctx.roofs, x, y, yaw, bed.depth) >= MIN_HEADROOM_M - 1e-6:
            fitting.append(s)
    assert fitting, "au moins un recul laisse 0,90 m à l'oreiller"


@pytest.mark.parametrize("pitch", [33, 45])
def test_generator_puts_built_in_beds_head_under_the_slope(pitch: float) -> None:
    project = _eave_outside(pitch)
    level = only_level(project)
    run = generate_layouts(
        project, level.id, LayoutBody(brief=BUILT_IN, variants=3, seed=4, budget_s=30)
    )
    assert run.proposals
    ctx = build_context(project, level.id, None)
    for proposal in run.proposals:
        beds = [f for f in proposal.furniture if f.category == BUILT_IN_BED]
        assert beds, "un lit encastré, pas le repli en lits simples"
        for bed in beds:
            t = bed.transform
            x, y, yaw, depth = t.position.x, t.position.y, t.yaw, bed.bbox.depth
            assert bed_headroom_at(ctx.level, ctx.roofs, x, y, yaw, depth) >= MIN_HEADROOM_M - 1e-6
            assert _head_is_lower(x, y, yaw, want_for(bed, BUILT_IN, ctx), ctx)
            assert standing_at(ctx.roofs, ctx.level.height, x, y, yaw, depth) >= STANDING_M - 1e-6
