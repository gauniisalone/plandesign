"""KAN-95 (P2) : pièces droites vues par plusieurs photos, sur scènes synthétiques (aucune photo
réelle). Recalage par murs et coins vus en commun ; rien n'est inventé : sans mur en commun, le
plan vient de la meilleure photo seule, et c'est dit ; un gros plan ne compte jamais."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.pipeline.depthplan import WallObservation
from openplan.pipeline.photo_fusion import read_photos
from openplan.pipeline.reference import room_size
from openplan.pipeline.room_fusion import (
    MERGE_ROOMS,
    NO_COMMON,
    SAME_WALLS,
    _analyse,
    _Reading,
    _room_dims,
    _union,
    corners,
    fuse_room,
    rotate_wall,
)
from openplan.schemas.common import Source
from openplan.schemas.elements import Wall
from tests.room_scenes import project_with_room_photos, rectangle

ROOM = rectangle(4.0, 3.5)
OPPOSITE = [(0.5, 0.5, 1.5, 40.0, -12.0), (3.5, 3.0, 1.5, 220.0, -12.0)]
"""Deux coins opposés, regard en diagonale : chaque photo voit le coin d'en face et ses deux
murs ; elles partagent les quatre murs deux à deux."""
FACING = [(2.0, 0.5, 1.5, 90.0, -5.0), (2.0, 3.0, 1.5, 270.0, -5.0)]
"""Face à face, chacune voit le mur d'en face : aucun mur ni coin en commun."""
CLOSE = (3.0, 1.75, 1.5, 0.0, -45.0)
"""À 1 m du mur, regard vers son pied : un gros plan."""


def test_rotate_wall_turns_points_a_quarter_and_back() -> None:
    w = WallObservation("x", 2.0, (-1.0, 0.5), 100, 0.01)
    once = rotate_wall(w, 1)  # (2, y) → (−y, 2) : mur y = 2, étendue en x de −0,5 à 1
    assert once.axis == "y" and once.offset == 2.0 and once.span == (-0.5, 1.0)
    twice = rotate_wall(w, 2)
    assert twice.axis == "x" and twice.offset == -2.0
    assert rotate_wall(w, 4) == w


def test_corners_are_where_both_walls_stop() -> None:
    a = WallObservation("x", 3.0, (0.0, 2.0), 100, 0.01)
    b = WallObservation("y", 2.0, (1.0, 3.0), 100, 0.01)
    c = WallObservation("y", 1.0, (1.0, 3.0), 100, 0.01)  # croise a au milieu : pas un coin
    found = corners([a, b, c])
    assert found == [(3.0, 2.0, -1, -1)]


def _readings(
    tmp_path: Path, cams: list[tuple[float, float, float, float, float]]
) -> list[_Reading]:
    project, _level, ids = project_with_room_photos(tmp_path, ROOM, cams)
    readings = [_Reading(**vars(r)) for r in read_photos(project, tmp_path, ids, None)]
    _analyse(readings, 1.5)
    return readings


def test_photos_from_opposite_corners_register_on_their_shared_walls(tmp_path: Path) -> None:
    union = _union(_readings(tmp_path, OPPOSITE))
    assert union is not None and len(union.order) == 2
    dims = _room_dims(union.walls)
    assert dims is not None
    assert sorted(dims[:2]) == pytest.approx(sorted((ROOM.length, ROOM.width)), rel=0.05)


def test_photos_facing_each_other_do_not_register(tmp_path: Path) -> None:
    union = _union(_readings(tmp_path, FACING))
    assert union is not None and len(union.order) == 1  # rien n'est inventé


def test_no_common_wall_keeps_the_best_photo_and_says_so(tmp_path: Path) -> None:
    project, level, ids = project_with_room_photos(tmp_path, ROOM, FACING)
    run = fuse_room(project, tmp_path, level, ids)
    f = run.fusion
    assert f is not None and not f.registered
    n = ids.index(f.best_photo) + 1 if f.best_photo else 0
    assert run.notices[0] == NO_COMMON.format(n=n)
    assert run.applied  # niveau vide : le plan de la meilleure photo y est posé
    size = room_size(run.project, level)
    assert size is not None
    assert sorted((size.length_m, size.width_m)) == pytest.approx(
        sorted((ROOM.length, ROOM.width)), rel=0.05
    )
    # Le plan est déjà celui de la meilleure photo : relancé, il ne change pas.
    again = fuse_room(run.project, tmp_path, level, ids)
    assert not again.applied and again.project == run.project
    assert again.notices[0] == NO_COMMON.format(n=n)


def test_shared_walls_are_said_and_the_plan_is_the_best_photo(tmp_path: Path) -> None:
    project, level, ids = project_with_room_photos(tmp_path, ROOM, OPPOSITE)
    run = fuse_room(project, tmp_path, level, ids)
    f = run.fusion
    assert f is not None
    assert not MERGE_ROOMS and not f.registered
    assert f.summary == SAME_WALLS.format(n=ids.index(f.best_photo) + 1 if f.best_photo else 0)
    others = [c for c in f.photos if not c.kept]
    assert others and "mêmes murs" in others[0].reason


def test_a_close_up_never_shrinks_the_room(tmp_path: Path) -> None:
    """P2d : le gros plan est analysé d'abord (son plan est minuscule) ; la pièce vient de la
    photo qui voit la pièce, jamais du gros plan."""
    good = (0.5, 0.5, 1.5, 40.0, -12.0)
    project, level, ids = project_with_room_photos(tmp_path, ROOM, [CLOSE, good])
    run = fuse_room(project, tmp_path, level, ids)
    f = run.fusion
    assert f is not None and f.best_photo == ids[1]
    close = next(c for c in f.photos if c.image_id == ids[0])
    assert close.close_up and not close.kept and "gros plan" in close.reason
    assert any("gros plan" in n for n in run.notices)
    size = room_size(run.project, level)
    assert size is not None and min(size.length_m, size.width_m) > 2.5


def test_a_hand_edited_plan_is_never_replaced(tmp_path: Path) -> None:
    project, level, ids = project_with_room_photos(tmp_path, ROOM, FACING)
    built = fuse_room(project, tmp_path, level, ids).project
    lvl = built.levels[level]
    wall = next(e for e in lvl.elements.values() if isinstance(e, Wall))
    edited = built.model_copy(
        update={
            "levels": {
                level: lvl.model_copy(
                    update={
                        "elements": {
                            **lvl.elements,
                            wall.id: wall.model_copy(update={"source": Source.USER_DEFINED}),
                        },
                        "scale_basis": None,
                    }
                )
            }
        }
    )
    run = fuse_room(edited, tmp_path, level, list(reversed(ids)))
    assert not run.applied and run.project == edited


def test_the_route_fuses_a_room_plan_from_its_linked_photos(tmp_path: Path) -> None:
    import shutil

    from openplan.api.routes.attic_fusion import fuse_members
    from openplan.scene.store import ProjectStore

    work = tmp_path / "work"
    project, level, ids = project_with_room_photos(work, ROOM, FACING)
    project = fuse_room(project, work, level, ids).project  # plan estimé d'une photo
    store = ProjectStore(tmp_path / "projects")
    store.save(project)
    shutil.copytree(work / "images", store.project_dir(project.id) / "images", dirs_exist_ok=True)
    run = fuse_members(store, project.id, level, None)
    assert run is not None and not run.applied  # pièce droite : fuse_room, rien de changé
    assert any(n.startswith("Ces photos ne voient pas de murs en commun") for n in run.notices)
