"""KAN-160 (décision d'augus, 7 nov.) : les vues calculées sont le point de vue par défaut de
« Rendre ». Sur des scènes de test seulement (aucune donnée réelle) :
- dans des combles cloisonnés (le cas des combles d'augus, en synthèse), aucune vue n'est face à
  la cloison, et une 2e vue montre l'autre côté ;
- une 2e vue seulement quand la 1re ne voit pas assez (L, combles cloisonnés), jamais pour une
  pièce simple ;
- la 2e vue est posée cochée comme sa pièce, dit « Vue calculée 2/2 », et « Recalculer » lui rend
  la 2e vue (jamais une copie de la 1re) ;
- une vue réglée à la main n'est jamais remplacée, 2e vue comprise.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point

from openplan.geom.roof import derived_roofs, ridge_line
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.mainroom import rooms_of
from openplan.pipeline.room_views import (
    ASPECT,
    DISTINCT_POS_M,
    DISTINCT_YAW_DEG,
    ON_KEY,
    RANK_KEY,
    ROOM_KEY,
    SIZES,
    apply_room_views,
    is_room_view,
    plan_room_view_pair,
    plan_room_views,
    reset_room_view,
)
from openplan.pipeline.views import _room
from openplan.scene.ops import AddWall, UpdateElement, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Camera, Wall
from openplan.schemas.scene import Project
from tests.factories import l_room_project, only_level, rect_room_project


def _attic(length: float = 6.0, width: float = 5.0) -> Project:
    from openplan.pipeline.attic import Estimate
    from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
    from openplan.scene.factory import new_project

    def est(v: float) -> Estimate:
        return Estimate(value=v, sigma=0.0, source=Source.USER_DEFINED)

    p = new_project("Combles test")
    geometry = AtticGeometry(
        length=est(length),
        width=est(width),
        ridge_height=est(3.2),
        eave_height=est(1.0),
        camera_height=None,
        gable_distance=None,
    )
    after, _ = apply_attic(p, only_level(p).id, geometry)
    return after


def _partitioned_attic(from_gable: float = 2.5, door: float = 0.9) -> Project:
    """Combles 6 × 5 m, une cloison en travers à 2,5 m du pignon, passage de 0,9 m sous le
    faîtage : la pièce reste une, mais la cloison cache ce qui est derrière elle."""
    project = _attic()
    level = only_level(project)
    graph = WallGraph.from_level(level)
    ridge = ridge_line(list(derived_roofs(level, graph).values()))
    assert ridge is not None
    (px, py), (dx, dy) = ridge
    x0, y0, x1, y1 = graph.room_polygon(rooms_of(level)[0].id).bounds
    if abs(dx) > abs(dy):
        x = x0 + from_gable
        segs = [((x, y0), (x, py - door / 2)), ((x, py + door / 2), (x, y1))]
    else:
        y = y0 + from_gable
        segs = [((x0, y), (px - door / 2, y)), ((px + door / 2, y), (x1, y))]
    for (ax, ay), (bx, by) in segs:
        project = apply_op(
            project,
            level.id,
            AddWall(start=Point2(x=ax, y=ay), end=Point2(x=bx, y=by), thickness=0.1),
        )
    return project


def _partition_share(project: Project, x: float, y: float, yaw: float, focal_px: float) -> float:
    """Part du cadre dont le premier obstacle, à moins de 3 m, est la cloison."""
    level = only_level(project)
    room = rooms_of(level)[0]
    graph = WallGraph.from_level(level)
    inner = [
        graph.wall_polygon(w.id)
        for w in level.elements.values()
        if isinstance(w, Wall) and w.id in graph.walls and w.id not in set(room.boundary_walls)
    ]
    assert inner, "la scène a une cloison"
    free = _room(level, room).free.buffer(0.02)
    half = math.atan(SIZES[ASPECT][0] / 2.0 / focal_px)
    n, hits = 0, 0
    a = yaw - half
    while a <= yaw + half:
        far = (x + 3.0 * math.cos(a), y + 3.0 * math.sin(a))
        seen = LineString([(x, y), far]).intersection(free)
        parts = getattr(seen, "geoms", [seen])
        reach = max((p.length for p in parts if p.distance(Point(x, y)) < 0.05), default=0.0)
        if reach < 2.95:
            end = Point(x + reach * math.cos(a), y + reach * math.sin(a))
            hits += any(w.distance(end) < 0.15 for w in inner)
        n += 1
        a += math.radians(2)
    return hits / max(1, n)


def _pair(project: Project) -> list:  # type: ignore[type-arg]
    level = only_level(project)
    room = rooms_of(level)[0]
    return plan_room_view_pair(level, room, "Pièce", 10.0)


def test_in_a_partitioned_attic_no_view_faces_the_partition_and_a_second_shows_behind() -> None:
    project = _partitioned_attic()
    first, second = _pair(project)
    assert _partition_share(project, first.x, first.y, first.yaw, first.focal_px) < 0.2
    assert (first.rank, first.of, second.rank, second.of) == (1, 2, 2, 2)
    moved = math.hypot(second.x - first.x, second.y - first.y)
    turned = abs(
        math.degrees(math.atan2(math.sin(second.yaw - first.yaw), math.cos(second.yaw - first.yaw)))
    )
    assert moved >= DISTINCT_POS_M or turned >= DISTINCT_YAW_DEG
    assert first.note.startswith("Vue calculée 1/2 · ")
    assert second.note.startswith("Vue calculée 2/2 · ")
    assert second.room_name == "Pièce · vue 2"


def test_a_second_view_only_when_the_first_does_not_see_enough() -> None:
    assert len(_pair(l_room_project())) == 2
    assert len(_pair(rect_room_project())) == 1
    assert len(_pair(_attic())) == 1
    one = _pair(rect_room_project())[0]
    assert one.note.startswith("Vue calculée · ") and (one.rank, one.of) == (1, 1)


def test_plan_room_views_keeps_one_view_per_room_unless_asked() -> None:
    level = only_level(l_room_project())
    single, _ = plan_room_views(level)
    both, _ = plan_room_views(level, second=True)
    assert len(single) == 1 and len(both) == 2
    # La 1re vue est la même ; seule sa note dit « 1/2 » quand une 2e existe.
    assert (single[0].x, single[0].y, single[0].yaw) == (both[0].x, both[0].y, both[0].yaw)
    assert (single[0].of, both[0].of, both[1].rank) == (1, 2, 2)


def test_same_plan_same_views_second_included() -> None:
    project = _partitioned_attic()
    assert _pair(project) == _pair(project)


def _cams(project: Project) -> list[Camera]:
    return [e for e in only_level(project).elements.values() if is_room_view(e)]  # type: ignore[misc]


def test_the_second_view_is_placed_ticked_like_its_room_and_recalculated_as_the_second() -> None:
    project = l_room_project()
    lid = only_level(project).id
    project, report = apply_room_views(project, lid, actor="user")
    cams = sorted(_cams(project), key=lambda c: int(c.metadata[RANK_KEY]))  # type: ignore[arg-type]
    assert [c.metadata[RANK_KEY] for c in cams] == [1, 2] and len(report.added) == 2
    assert cams[0].metadata[ON_KEY] is cams[1].metadata[ON_KEY] is True
    assert cams[0].metadata[ROOM_KEY] == cams[1].metadata[ROOM_KEY]
    second = cams[1]
    # À la main, puis « Recalculer » : elle redevient la 2e vue, pas une copie de la 1re.
    project = apply_op(
        project, lid, UpdateElement(element_id=second.id, fields={"yaw": second.yaw + 0.4})
    )
    project, back = reset_room_view(project, lid, second.id, actor="user")
    assert back.source is Source.GENERATED and back.metadata[RANK_KEY] == 2
    assert (back.position.x, back.position.y, back.yaw) == (
        second.position.x,
        second.position.y,
        second.yaw,
    )
    first = cams[0]
    assert (back.position.x, back.position.y, back.yaw) != (
        first.position.x,
        first.position.y,
        first.yaw,
    )


def test_a_view_set_by_hand_is_never_replaced_second_view_included() -> None:
    project = l_room_project()
    lid = only_level(project).id
    project, _ = apply_room_views(project, lid, actor="user")
    first = next(c for c in _cams(project) if c.metadata[RANK_KEY] == 1)
    project = apply_op(
        project, lid, UpdateElement(element_id=first.id, fields={"yaw": first.yaw + 0.3})
    )
    after, report = apply_room_views(project, lid, actor="user")
    cams = _cams(after)
    hand = [c for c in cams if c.source is Source.USER_DEFINED]
    assert [c.id for c in hand] == [first.id]
    # La vue à la main tient lieu des vues calculées de sa pièce : aucune 2e vue recalculée.
    assert len(cams) == 1 and not report.added
    assert any("modifiée à la main" in k for k in report.kept)
