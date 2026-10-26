"""KAN-94, lot L1 : poteaux et fermes intermédiaires, côté serveur. Pose et limites dites,
hauteur automatique d'un poteau (sous le bois, sous la pente, au plafond), ferme placée depuis
l'un ou l'autre pignon, poutre abaissée sous la pente, déplacement le long du faîtage seul,
reprojection quand le faîtage change (gardée et signalée, jamais supprimée en silence),
historique, 3D, aménagement et exports."""

from __future__ import annotations

import io
import math
from uuid import UUID

import ezdxf
import pytest

from openplan.geom.truss import NO_RIDGE, gable_face, place_truss, ridge_span, truss_parts
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.export import render_dxf, render_pdf, render_svg
from openplan.pipeline.layouts import build_context
from openplan.pipeline.scene3d import bake_level, post_top
from openplan.scene import history
from openplan.scene.ops import (
    POST_OUTSIDE,
    AddPost,
    AddTruss,
    OpError,
    RemoveElement,
    UpdateElement,
    apply_op,
)
from openplan.schemas.common import Point2
from openplan.schemas.elements import Post, RoofPlane, Room, Truss, Wall
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import check_layout
from tests.factories import attic_project, only_level, rect_room_project


def _one[T](level: Level, kind: type[T]) -> T:
    (found,) = [e for e in level.elements.values() if isinstance(e, kind)]
    return found


def _set(project: Project, level_id: UUID, eid: UUID, **fields: object) -> Project:
    return apply_op(project, level_id, UpdateElement(element_id=eid, fields=fields))  # type: ignore[arg-type]


def _gables(level: Level) -> list[Wall]:
    """Les deux murs en travers du faîtage, du premier au second bout."""
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    out = []
    for wall in (e for e in level.elements.values() if isinstance(e, Wall)):
        try:
            gable_face(level, graph, ridge, wall.id)
        except ValueError:
            continue
        out.append(wall)
    assert len(out) == 2
    return sorted(out, key=lambda w: gable_face(level, graph, ridge, w.id)[0])


def _attic_truss(**kw: object) -> tuple[Project, UUID, Truss]:
    project = attic_project(user_defined=True, camera=False)
    level = only_level(project)
    after = apply_op(project, level.id, AddTruss(**kw))  # type: ignore[arg-type]
    return after, level.id, _one(after.levels[level.id], Truss)


# ------------------------------------------------------------------ poteaux
def test_a_post_is_placed_in_a_room_in_one_undoable_step() -> None:
    project = rect_room_project()
    level = only_level(project)
    after = apply_op(project, level.id, AddPost(position=Point2(x=2.0, y=1.5)))
    post = _one(after.levels[level.id], Post)
    room = _one(after.levels[level.id], Room)
    assert (post.width, post.depth, post.top, post.room) == (0.15, 0.12, None, room.id)
    assert after.history[-1].label == "Poser un poteau"
    assert history.undo(after).levels[level.id] == project.levels[level.id]
    with pytest.raises(OpError, match=POST_OUTSIDE):
        apply_op(project, level.id, AddPost(position=Point2(x=5.0, y=1.5)))


def test_post_limits_are_stated_in_french() -> None:
    project = rect_room_project()
    level = only_level(project)
    after = apply_op(project, level.id, AddPost(position=Point2(x=2.0, y=1.5)))
    post = _one(after.levels[level.id], Post)
    with pytest.raises(OpError, match="plafond du niveau"):
        _set(after, level.id, post.id, top=level.height + 0.5)
    with pytest.raises(OpError):
        _set(after, level.id, post.id, width=0.60)  # au plus 40 cm
    with pytest.raises(OpError, match=POST_OUTSIDE):
        _set(after, level.id, post.id, position={"x": 9.0, "y": 1.5})


def test_a_lone_post_outside_an_attic_rises_to_the_level_ceiling() -> None:
    """PO (b) : pas de bois au-dessus, pas de pente : jusqu'au plafond du niveau."""
    project = rect_room_project()
    level = only_level(project)
    after = apply_op(project, level.id, AddPost(position=Point2(x=2.0, y=1.5)))
    level = after.levels[level.id]
    post = _one(level, Post)
    assert post_top(level, WallGraph.from_level(level), post) == pytest.approx(level.height)


def test_a_post_rises_to_the_timber_above_it_or_its_given_top() -> None:
    project = attic_project(user_defined=True, camera=False)
    level = only_level(project)
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    middle = ridge.at((ridge.t0 + ridge.t1) / 2.0)
    under_ridge = apply_op(project, level.id, AddPost(position=Point2(x=middle[0], y=middle[1])))
    lv = under_ridge.levels[level.id]
    top = post_top(lv, graph, _one(lv, Post))
    # Sous la faîtière (25 cm sous les chevrons) : plus bas que le faîtage, plus haut que 2,5 m.
    assert 2.5 < top < level.height - 0.2
    nx, ny = ridge.normal
    aside = Point2(x=middle[0] + nx * 1.5, y=middle[1] + ny * 1.5)
    lower = apply_op(project, level.id, AddPost(position=aside))
    lv2 = lower.levels[level.id]
    assert post_top(lv2, graph, _one(lv2, Post)) < top - 0.5, "sous la pente, plus bas"
    given = _set(under_ridge, level.id, _one(lv, Post).id, top=2.10)
    lv3 = given.levels[level.id]
    assert post_top(lv3, graph, _one(lv3, Post)) == pytest.approx(2.10)


# ------------------------------------------------------------------ fermes
def test_a_truss_needs_a_ridge() -> None:
    project = rect_room_project()
    with pytest.raises(OpError, match="faîtage"):
        apply_op(project, only_level(project).id, AddTruss())
    assert "faîtage" in NO_RIDGE


def test_a_truss_defaults_to_mid_ridge_and_is_one_undoable_element() -> None:
    after, level_id, truss = _attic_truss()
    level = after.levels[level_id]
    ridge = ridge_span(level, WallGraph.from_level(level))
    assert ridge is not None
    t = ridge.along((truss.position.x, truss.position.y))
    assert t == pytest.approx((ridge.t0 + ridge.t1) / 2.0, abs=1e-6)
    assert (truss.spacing, truss.beam_height, truss.beam_depth) == (1.0, 2.0, 0.20)
    assert after.history[-1].label == "Ajouter une ferme intermédiaire"
    before = level.model_copy(
        update={"elements": {k: e for k, e in level.elements.items() if k != truss.id}}
    )
    assert history.undo(after).levels[level_id] == before
    removed = apply_op(after, level_id, RemoveElement(element_id=truss.id))
    assert not any(isinstance(e, Truss) for e in removed.levels[level_id].elements.values())
    assert history.undo(removed).levels[level_id] == level


def test_the_distance_can_be_given_from_either_gable() -> None:
    project = attic_project(user_defined=True, camera=False)
    level = only_level(project)
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    first, second = _gables(level)
    a, _ = gable_face(level, graph, ridge, first.id)
    b, _ = gable_face(level, graph, ridge, second.id)
    inside = b - a
    one = apply_op(project, level.id, AddTruss(distance=2.0, from_wall=first.id))
    other = apply_op(project, level.id, AddTruss(distance=inside - 2.0, from_wall=second.id))
    p = _one(one.levels[level.id], Truss).position
    q = _one(other.levels[level.id], Truss).position
    assert math.hypot(p.x - q.x, p.y - q.y) < 1e-6
    assert ridge.along((p.x, p.y)) == pytest.approx(a + 2.0)
    side = next(
        w
        for w in level.elements.values()
        if isinstance(w, Wall) and w.id not in {first.id, second.id}
    )
    with pytest.raises(OpError, match="n'est pas un pignon"):
        apply_op(project, level.id, AddTruss(distance=2.0, from_wall=side.id))
    with pytest.raises(OpError, match="hors du faîtage"):
        apply_op(project, level.id, AddTruss(distance=inside + 1.0, from_wall=first.id))


def test_a_beam_too_high_for_the_slope_is_lowered_and_said() -> None:
    after, _, truss = _attic_truss(beam_height=2.9)
    assert truss.beam_height < 2.9
    assert "poutre abaissée à" in after.history[-1].label
    with pytest.raises(OpError, match="écartement de 4,50 m trop grand"):
        _attic_truss(spacing=4.5)


def test_a_truss_only_moves_along_the_ridge() -> None:
    after, level_id, truss = _attic_truss()
    level = after.levels[level_id]
    ridge = ridge_span(level, WallGraph.from_level(level))
    assert ridge is not None
    nx, ny = ridge.normal
    dx, dy = ridge.direction
    target = {
        "x": truss.position.x + dx * 0.8 + nx * 1.2,
        "y": truss.position.y + dy * 0.8 + ny * 1.2,
    }
    moved = _set(after, level_id, truss.id, position=target)
    placed = _one(moved.levels[level_id], Truss)
    # Le pas en travers est ignoré : le point reste sur l'axe, 0,80 m plus loin.
    t0 = ridge.along((truss.position.x, truss.position.y))
    assert ridge.along((placed.position.x, placed.position.y)) == pytest.approx(t0 + 0.8)
    off = (placed.position.x - ridge.origin[0]) * nx + (placed.position.y - ridge.origin[1]) * ny
    assert off == pytest.approx(0.0, abs=1e-6)
    assert moved.history[-1].label == "Déplacer la ferme intermédiaire"
    first, _ = _gables(level)
    again = _set(moved, level_id, truss.id, distance=1.5, from_wall=str(first.id))
    assert again.history[-1].label == "Déplacer la ferme intermédiaire"
    wider = _set(again, level_id, truss.id, spacing=1.4)
    assert wider.history[-1].label == "Modifier la ferme intermédiaire"


# ---------------------------------------------- PO (a) : le faîtage change
def _roofs(level: Level) -> list[RoofPlane]:
    return [e for e in level.elements.values() if isinstance(e, RoofPlane)]


def test_a_truss_follows_the_ridge_when_a_slope_is_retouched() -> None:
    after, level_id, _ = _attic_truss()
    roof = _roofs(after.levels[level_id])[0]
    retouched = _set(after, level_id, roof.id, eave_height=roof.eave_height + 0.4)
    level = retouched.levels[level_id]
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    parts = truss_parts(level, graph, _one(level, Truss))
    assert not isinstance(parts, str)
    nx, ny = ridge.normal
    off = (parts.centre[0] - ridge.origin[0]) * nx + (parts.centre[1] - ridge.origin[1]) * ny
    assert off == pytest.approx(0.0, abs=1e-6), "reprojetée sur le nouvel axe"
    assert not [v for v in check_layout(retouched) if v.type == "truss_off_ridge"]


def test_a_truss_without_ridge_is_kept_and_flagged() -> None:
    after, level_id, truss = _attic_truss()
    roof = _roofs(after.levels[level_id])[0]
    gone = apply_op(after, level_id, RemoveElement(element_id=roof.id))
    level = gone.levels[level_id]
    assert truss.id in level.elements, "jamais supprimée en silence"
    flagged = [v for v in check_layout(gone) if v.type == "truss_off_ridge"]
    assert flagged and flagged[0].element_ids == [truss.id]
    assert "plus de faîtage" in flagged[0].message
    bake = bake_level(gone, level_id)
    assert not any(p.element_id == truss.id for p in bake.polyhedra), "plus dessinée"


def test_a_truss_off_a_shortened_ridge_is_kept_and_flagged() -> None:
    after, level_id, truss = _attic_truss(distance=0.6)
    level = after.levels[level_id]
    ridge = ridge_span(level, WallGraph.from_level(level))
    assert ridge is not None
    dx, dy = ridge.direction
    shortened = after
    for roof in _roofs(level):
        # Les deux égouts raccourcis de 1,5 m côté premier pignon.
        ends = sorted([roof.eave_start, roof.eave_end], key=lambda p: ridge.along((p.x, p.y)))
        moved = {"x": ends[0].x + dx * 1.5, "y": ends[0].y + dy * 1.5}
        key = "eave_start" if ends[0] == roof.eave_start else "eave_end"
        shortened = _set(shortened, level_id, roof.id, **{key: moved})
    assert truss.id in shortened.levels[level_id].elements
    flagged = [v for v in check_layout(shortened) if v.type == "truss_off_ridge"]
    assert flagged and "hors du faîtage" in flagged[0].message


def test_an_ops_body_without_op_stays_detect_rooms(tmp_path: object) -> None:
    """Le corps de la route /ops est choisi dans l'union par ordre quand « op » manque : « {} »
    (le pas vide) reste DetectRooms, jamais AddPost ni AddTruss, même sous un faîtage où une
    ferme se poserait."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from openplan.api.app import create_app
    from openplan.api.settings import Settings

    assert isinstance(tmp_path, Path)
    app = create_app(settings=Settings(data_dir=tmp_path / "local"))
    client = TestClient(app)
    project = attic_project(user_defined=True, camera=False)
    app.state.store.save(project)
    level = only_level(project)
    r = client.post(f"/api/projects/{project.id}/levels/{level.id}/ops", json={})
    assert r.status_code == 200, r.text
    after = app.state.store.load(project.id)
    elements = after.levels[level.id].elements.values()
    assert not any(isinstance(e, Post | Truss) for e in elements)
    # DetectRooms ne trouve rien de neuf : aucun pas d'historique (une ferme en aurait ajouté un).
    assert len(after.history) == len(project.history)


# ------------------------------------------------------------------ 3D, aménagement, exports
def test_posts_and_trusses_are_baked_in_wood() -> None:
    after, level_id, truss = _attic_truss()
    level = after.levels[level_id]
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    middle = ridge.at(ridge.t0 + 1.0)
    with_post = apply_op(after, level_id, AddPost(position=Point2(x=middle[0], y=middle[1])))
    bake = bake_level(with_post, level_id)
    pieces = [p for p in bake.polyhedra if p.element_id == truss.id]
    assert len(pieces) == 3, "deux poteaux et une poutre"
    assert {p.kind for p in pieces} == {"timber"}
    beam = max(pieces, key=lambda p: min(v.z for v in p.vertices))
    parts = place_truss(ridge, truss)
    assert not isinstance(parts, str)
    assert min(v.z for v in beam.vertices) == pytest.approx(level.elevation + parts.beam_height)
    post = _one(with_post.levels[level_id], Post)
    assert [p.kind for p in bake.polyhedra if p.element_id == post.id] == ["timber"]


def test_furniture_layouts_avoid_posts() -> None:
    from shapely.geometry import Point

    project = rect_room_project()
    level = only_level(project)
    after = apply_op(project, level.id, AddPost(position=Point2(x=2.0, y=1.5)))
    room = _one(after.levels[level.id], Room)
    ctx = build_context(after, level.id, room.id)
    assert ctx.forbidden.contains(Point(2.0, 1.5))


def test_posts_and_trusses_are_drawn_in_every_export() -> None:
    after, level_id, _ = _attic_truss()
    svg = render_svg(after, level_id)
    assert svg.count('class="timber-post"') == 2 and 'class="timber-beam"' in svg
    assert "poutre à 2,00 m" in svg
    assert render_pdf(after, level_id).startswith(b"%PDF")
    doc = ezdxf.read(io.StringIO(render_dxf(after, level_id)))
    layer = [e for e in doc.modelspace() if e.dxf.layer == "CHARPENTE"]
    assert sum(e.dxftype() == "LWPOLYLINE" for e in layer) == 3
    assert any(e.dxftype() == "TEXT" and e.dxf.text == "poutre à 2,00 m" for e in layer)
