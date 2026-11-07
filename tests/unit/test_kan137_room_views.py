"""KAN-137 : une vue calculée par pièce, sur des plans synthétiques (aucun projet réel) :
rectangle, pièce en L, comble, logements de plusieurs pièces (plans générés), pièce meublée.
Pour chaque vue : caméra dans la pièce, hors des murs et des meubles, sous le rampant, à
hauteur d'œil, focale de 24 à 28 mm, part du sol visible, au moins deux murs, la fenêtre."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from shapely.geometry import Point

from openplan.pipeline.generated import generate_plan
from openplan.pipeline.mainroom import rooms_of
from openplan.pipeline.programme import read_programme
from openplan.pipeline.room_views import (
    ADDED_KEY,
    AREA_KEY,
    EYE_LOW_M,
    EYE_MIN_M,
    ON_KEY,
    RANK_KEY,
    ROOM_KEY,
    ROOM_NAME_KEY,
    SMALL_ROOM_M2,
    TALL_NEAR_M,
    RoomView,
    add_room_view,
    apply_room_views,
    is_room_view,
    plan_room_view,
    plan_room_views,
    room_names,
)
from openplan.pipeline.views import (
    FURNITURE_MARGIN_M,
    HEADROOM_M,
    TALL_FURNITURE_M,
    WALL_MARGIN_M,
    _footprint,
    _light_points,
    _room,
    views_to_render,
)
from openplan.scene.factory import new_project
from openplan.scene.ops import AddFurniture, UpdateElement, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Camera, Furniture, Room
from openplan.schemas.scene import Level, Project
from tests.factories import attic_project, l_room_project, only_level, rect_room_project

PROGRAMMES = {
    "t3": "Un appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau",
    "t4": "T4 de 80 m², trois chambres, wc séparés",
    "maison": "Maison de plain-pied de 110 m², trois chambres, un bureau, une buanderie",
}


def _generated(key: str) -> Project:
    reading = read_programme(PROGRAMMES[key], None)
    assert reading.programme is not None
    project = new_project("Essai")
    after, _ = generate_plan(project, only_level(project).id, reading.programme)
    return after


def _furnished_rect() -> Project:
    """Chambre 4 × 3 : un lampadaire dans chaque angle, là où la caméra irait d'abord, et une
    armoire contre le mur de la fenêtre."""
    project = rect_room_project()
    level = only_level(project)
    for x, y in ((0.55, 0.55), (3.45, 0.55), (3.45, 2.45), (0.55, 2.45)):
        project = apply_op(
            project, level.id, AddFurniture(asset_id="floor_lamp", position=Point2(x=x, y=y))
        )
    return apply_op(
        project,
        level.id,
        AddFurniture(asset_id="wardrobe_150", position=Point2(x=1.0, y=2.5), yaw=math.pi),
    )


PLANS: dict[str, tuple[Callable[[], Project], float]] = {
    "rectangle": (rect_room_project, 0.60),
    "L": (l_room_project, 0.50),
    "comble": (lambda: attic_project(camera=False), 0.45),
    # Quatre lampadaires et une armoire cachent du sol : 40 % (plan ajouté, hors maquette).
    "meublee": (_furnished_rect, 0.40),
    **{key: ((lambda k=key: _generated(k)), 0.60) for key in PROGRAMMES},
}
"""Plan et part du sol visible exigée (note de méthode de la maquette 2fb2691, § 5)."""


def _check(level: Level, room: Room, view: RoomView, floor_min: float) -> None:
    seen = _room(level, room)
    here = Point(view.x, view.y)
    # Dans la pièce, à 0,3 m des murs, hors des meubles (0,2 m) et de la trémie.
    assert seen.stand.contains(here), view
    assert seen.polygon.exterior.distance(here) >= WALL_MARGIN_M - 1e-6
    for f in seen.furniture:
        assert _footprint(f).distance(here) >= FURNITURE_MARGIN_M - 1e-6, (f.asset_id, view)
    # Hauteur d'œil 1,50 à 1,60 m (1,40 m au plus bas, dit), toujours sous le rampant.
    assert (EYE_LOW_M if view.low else EYE_MIN_M) - 1e-6 <= view.z <= 1.60
    assert view.z <= seen.clearance(view.x, view.y) - HEADROOM_M + 1e-6
    # Grand-angle réaliste : 24 à 28 mm, inclinaison légère vers le bas.
    assert 24.0 <= view.focal_mm <= 28.0
    assert -math.radians(6.0) - 1e-9 <= view.pitch <= -math.radians(3.0) + 1e-9
    # Aucun meuble haut à moins de 1 m devant l'objectif.
    for f in seen.furniture:
        if f.bbox.height * f.transform.scale <= TALL_FURNITURE_M:
            continue
        fx, fy = f.transform.position.x, f.transform.position.y
        ahead = (fx - view.x) * math.cos(view.yaw) + (fy - view.y) * math.sin(view.yaw)
        near = _footprint(f).distance(here) < TALL_NEAR_M
        assert not (near and ahead > 0 and abs(_bearing(view, fx, fy)) < math.radians(30)), f
    # Ce que la vue montre.
    assert view.walls_seen >= 2, view
    if view.area_m2 >= SMALL_ROOM_M2:
        assert view.floor_seen >= floor_min, view
        if _light_points(seen):
            assert view.light_in_view, view


def _bearing(view: RoomView, x: float, y: float) -> float:
    a = math.atan2(y - view.y, x - view.x) - view.yaw
    return math.atan2(math.sin(a), math.cos(a))


@pytest.mark.parametrize("key", list(PLANS))
def test_every_room_gets_a_view_that_shows_most_of_it(key: str) -> None:
    make, floor_min = PLANS[key]
    level = only_level(make())
    views, skipped = plan_room_views(level)
    rooms = {str(r.id): r for r in rooms_of(level)}
    assert not skipped, skipped
    assert sorted(v.room_id for v in views) == sorted(rooms)
    for view in views:
        _check(level, rooms[view.room_id], view, floor_min)


def test_same_plan_same_views() -> None:
    level = only_level(_generated("t4"))
    assert plan_room_views(level) == plan_room_views(level)


def test_the_attic_view_stands_under_the_ridge() -> None:
    level = only_level(attic_project(camera=False))
    (view,) = plan_room_views(level)[0]
    room = rooms_of(level)[0]
    assert _room(level, room).clearance(view.x, view.y) >= view.z + HEADROOM_M


def test_an_added_view_differs_from_the_first() -> None:
    level = only_level(rect_room_project())
    room = rooms_of(level)[0]
    first = plan_room_view(level, room, "Chambre", 11.0)
    assert first is not None
    second = plan_room_view(level, room, "Chambre", 11.0, avoid=[(first.x, first.y, first.yaw)])
    assert second is not None
    moved = math.hypot(second.x - first.x, second.y - first.y)
    turned = abs(
        math.degrees(math.atan2(math.sin(second.yaw - first.yaw), math.cos(second.yaw - first.yaw)))
    )
    assert moved >= 1.0 or turned >= 35.0


def test_room_names_fall_back_to_the_type_and_number_the_twins() -> None:
    level = only_level(_generated("t4"))
    names = room_names(level)
    beds = sorted(n for n in names.values() if n.startswith("Chambre"))
    assert len(beds) == 3 and len(set(names.values())) == len(names)
    unnamed = level.model_copy(
        update={
            "elements": {
                k: e.model_copy(update={"name": None}) if isinstance(e, Room) else e
                for k, e in level.elements.items()
            }
        }
    )
    plain = room_names(unnamed)
    assert sorted(n for n in plain.values() if n.startswith("Chambre")) == [
        "Chambre 1",
        "Chambre 2",
        "Chambre 3",
    ]
    assert all(n for n in plain.values())


def _room_cams(project: Project) -> list[Camera]:
    return [e for e in only_level(project).elements.values() if is_room_view(e)]  # type: ignore[misc]


def test_room_views_go_into_the_plan_small_rooms_unticked() -> None:
    project = _generated("t4")
    level = only_level(project)
    after, report = apply_room_views(project, level.id, actor="user")
    cams = _room_cams(after)
    rooms = {str(r.id): r for r in rooms_of(level)}
    # KAN-160 : chaque pièce a sa vue, et au plus une 2e quand la 1re ne voit pas assez.
    assert len(cams) == len(report.added) and not report.skipped
    per_room: dict[str, list[Camera]] = {}
    for cam in cams:
        per_room.setdefault(str(cam.metadata[ROOM_KEY]), []).append(cam)
    assert sorted(per_room) == sorted(rooms)
    assert all(len(v) <= 2 for v in per_room.values())
    names = room_names(level)
    for cam in cams:
        meta = cam.metadata
        base = names[str(meta[ROOM_KEY])]
        wanted = base if meta[RANK_KEY] == 1 else f"{base} · vue 2"
        assert cam.name == wanted == meta[ROOM_NAME_KEY]
        assert cam.image_id is None and cam.source is Source.GENERATED
        area = meta[AREA_KEY]
        assert isinstance(area, float)
        assert meta[ON_KEY] is (area >= SMALL_ROOM_M2), (cam.name, area)
    assert any(cam.metadata[ON_KEY] is False for cam in cams), "WC et placard décochés"
    assert after.history[-1].label == f"Vues par pièce ({len(cams)})"


def test_recompute_keeps_the_ticks_the_hand_moved_view_and_the_added_one() -> None:
    project = _generated("t3")
    level_id = only_level(project).id
    project, _ = apply_room_views(project, level_id, actor="user")
    cams = sorted(_room_cams(project), key=lambda c: c.name or "")
    ticked, moved = cams[0], cams[1]
    flipped = not ticked.metadata[ON_KEY]
    project = apply_op(
        project,
        level_id,
        UpdateElement(
            element_id=ticked.id, fields={"metadata": {**ticked.metadata, ON_KEY: flipped}}
        ),
    )
    project = apply_op(
        project,
        level_id,
        UpdateElement(element_id=moved.id, fields={"yaw": moved.yaw + 0.3}),
    )
    room_id = UUID(str(moved.metadata[ROOM_KEY]))
    project, extra = add_room_view(project, level_id, room_id, actor="user")
    assert extra.metadata[ADDED_KEY] is True and extra.name.endswith("· vue 2")  # type: ignore[union-attr]
    after, report = apply_room_views(project, level_id, actor="user")
    by_id = {c.id: c for c in _room_cams(after)}
    assert by_id[moved.id].source is Source.USER_DEFINED  # gardée, jamais recalculée
    assert extra.id in by_id
    same_room = [c for c in by_id.values() if c.metadata[ROOM_KEY] == str(room_id)]
    assert len(same_room) == 2, "la vue à la main tient lieu de vue calculée, l'ajoutée reste"
    again = next(c for c in by_id.values() if c.metadata[ROOM_KEY] == ticked.metadata[ROOM_KEY])
    assert again.metadata[ON_KEY] is flipped
    assert any("modifiée à la main" in k for k in report.kept)
    assert any("vue ajoutée" in k for k in report.kept)


def test_render_all_views_of_the_local_render_leaves_room_views_out() -> None:
    project = _generated("t3")
    level_id = only_level(project).id
    project, _ = apply_room_views(project, level_id, actor="user")
    assert not any(is_room_view(c) for c in views_to_render(only_level(project)))


def test_the_routes_propose_and_add_room_views(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openplan.api.app import create_app
    from openplan.api.settings import Settings
    from openplan.scene.store import ProjectStore

    store = ProjectStore(tmp_path / "data")
    project = _generated("t3")
    store.save(project)
    pid = project.id
    level = only_level(project)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        base = f"/api/projects/{pid}/levels/{level.id}/views/rooms"
        r = client.post(base)
        assert r.status_code == 200, r.text
        body = r.json()
        # KAN-160 : une vue par pièce, plus une 2e pour celles qui en ont besoin.
        assert len(rooms_of(level)) <= len(body["views"]["added"]) <= 2 * len(rooms_of(level))
        room = next(r for r in rooms_of(level) if r.room_type == "living_room")
        added = client.post(f"{base}/{room.id}/add")
        assert added.status_code == 200, added.text
        cam = added.json()["project"]["levels"][str(level.id)]["elements"][
            added.json()["camera_id"]
        ]
        assert cam["metadata"][ROOM_KEY] == str(room.id)
        unknown = client.post(f"{base}/{uuid4()}/add")
        assert unknown.status_code in (404, 409), unknown.text


def test_furniture_is_never_under_the_camera() -> None:
    level = only_level(_furnished_rect())
    (view,) = plan_room_views(level)[0]
    for f in (e for e in level.elements.values() if isinstance(e, Furniture)):
        assert not _footprint(f).buffer(FURNITURE_MARGIN_M - 1e-6).contains(Point(view.x, view.y))
