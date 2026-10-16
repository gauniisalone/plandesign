"""Chantier H, lot H3 : textiles (plaid drapé à franges, couette de lin, tapis de jute)."""

from __future__ import annotations

import math

from openplan.pipeline.decor import (
    DRAPE_DROP_M,
    JUTE_DEPTH_M,
    THROW_BIAS_DEG,
    dress_level,
)
from openplan.scene.ops import AddFurniture, SetIntent, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Room
from tests.factories import only_level, rect_room_project


def _bedroom(asset: str, style: str = "naturel"):  # type: ignore[no-untyped-def]
    project = rect_room_project(5.0, 4.5)
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    project = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": style}))
    project = apply_op(
        project,
        level.id,
        AddFurniture(asset_id=asset, position=Point2(x=2.5, y=1.2), yaw=0.0),
    )
    return only_level(project)


def test_throw_drapes_over_the_sides_at_a_slight_angle() -> None:
    decor = dress_level(_bedroom("bed_double_160"))
    throw = next(d for d in decor if d.kind == "throw")
    assert throw.drape == 1.6 / 2
    assert throw.width == 1.6 + 2 * DRAPE_DROP_M
    assert math.isclose(throw.yaw, math.radians(THROW_BIAS_DEG))


def test_built_in_bed_gets_a_soft_linen_duvet() -> None:
    decor = dress_level(_bedroom("bed_builtin"))
    duvet = next(d for d in decor if d.kind == "duvet")
    assert duvet.drape == 1.4 / 2 and duvet.z == 0.55
    assert next(d for d in decor if d.kind == "throw").z > duvet.z


def test_jute_rug_at_the_foot_of_the_bed_when_the_floor_is_free() -> None:
    level = _bedroom("bed_double_160")
    rug = next(d for d in dress_level(level) if d.kind == "jute_rug")
    assert rug.depth == JUTE_DEPTH_M and rug.width == 1.6 + 0.4
    assert rug.y > 1.2 + 1.0, "devant le pied du lit (face avant +y)"
    assert rug.accent is None, "jute tressée pour une ambiance naturelle"
    wool = next(
        d for d in dress_level(_bedroom("bed_double_160", "industriel")) if d.kind == "jute_rug"
    )
    assert wool.accent is not None, "laine unie pour une ambiance industrielle"
