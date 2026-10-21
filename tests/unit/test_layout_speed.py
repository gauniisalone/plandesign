"""KAN-69, KAN-74 : le générateur va plus vite sans changer ses réponses (caches et préfiltres
exacts)."""

from __future__ import annotations

import random

import shapely
from shapely import affinity
from shapely.geometry import Polygon, box

from openplan.pipeline.layouts import (
    OVERLAP_EPS_M2,
    Placed,
    _clear_of,
    _front,
    _overlaps,
    _polygon,
    _pool,
    _static,
    build_context,
    candidates,
    expand_brief,
    feasible,
    score_candidate,
)
from tests.factories import only_level
from tests.unit.test_timber_reveal import _attic, _with_windows
from tests.unit.test_velux_chevetre import DESK_UNDER_VELUX


def _rect(x: float, y: float, w: float, d: float, angle: float) -> Polygon:
    return affinity.rotate(box(x, y, x + w, y + d), angle, origin="centroid")


def test_overlap_prefilter_matches_the_area_test() -> None:
    rng = random.Random(7)
    fixed = _rect(1.0, 1.0, 1.2, 0.6, 15.0)
    shapely.prepare(fixed)
    for _ in range(400):
        other = _rect(
            rng.uniform(-0.5, 2.5),
            rng.uniform(-0.5, 2.5),
            rng.uniform(0.1, 1.5),
            0.5,
            rng.uniform(0.0, 90.0),
        )
        expected = other.intersection(fixed).area > OVERLAP_EPS_M2
        assert _overlaps(other, fixed) == expected
        assert _overlaps(fixed, other) == expected
    # Contact par un bord : aire commune nulle, pas de recouvrement.
    assert not _overlaps(box(0, 0, 1, 1), box(1, 0, 2, 1))


def test_cached_front_and_candidate_pool_match_a_fresh_computation() -> None:
    project = _with_windows(_attic(45))
    ctx = build_context(project, only_level(project).id, None)
    wants, _ = expand_brief(DESK_UNDER_VELUX, ctx)
    want = wants[0]
    pool = _pool(want, ctx)
    assert [(x, y, yaw) for x, y, yaw, _ in pool] == candidates(want, ctx)
    assert all(p.equals(_polygon(x, y, yaw, want)) for x, y, yaw, p in pool)
    assert _pool(want, ctx) is pool  # calculés une seule fois par génération
    x, y, yaw, polygon = pool[len(pool) // 2]
    placed = Placed(want, x, y, yaw, polygon, 0.0)
    assert placed.front.equals(_front(x, y, yaw, want))
    assert ctx.inner.equals(ctx.room_polygon.buffer(1e-3))


def test_cached_static_parts_give_the_same_answers() -> None:
    """KAN-74 : pièce, pente et parties fixes du score en mémoire, meubles posés recalculés : même
    faisabilité et même score (au bit près) que `feasible` et `score_candidate`."""
    project = _with_windows(_attic(45))
    ctx = build_context(project, only_level(project).id, None)
    wants, _ = expand_brief(DESK_UNDER_VELUX, ctx)
    rng = random.Random(3)
    placed: list[Placed] = []
    for want in wants[:2]:
        pool = _pool(want, ctx)
        x, y, yaw, polygon = next(c for c in pool if feasible(c[3], want, ctx, placed))
        placed.append(Placed(want, x, y, yaw, polygon, 0.0))
    want = wants[2]
    pool = _pool(want, ctx)
    checked = 0
    for index in rng.sample(range(len(pool)), min(300, len(pool))):
        x, y, yaw, polygon = pool[index]
        static = _static(want, ctx, index, pool[index])
        expected = feasible(polygon, want, ctx, placed)
        got = static.parts is not None and _clear_of(polygon, static.parts.box, want, ctx, placed)
        assert got == expected
        if static.parts is not None:
            checked += 1
            score, _, _ = score_candidate(x, y, yaw, polygon, want, ctx, placed)
            assert static.parts.total(polygon, placed) == score
    assert checked > 20
    assert _static(want, ctx, index, pool[index]) is _static(want, ctx, index, pool[index])
