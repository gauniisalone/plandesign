"""KAN-136 : le prompt d'une vue nomme ce que CETTE vue voit, et rien d'autre.

Essai MH3 (6 nov., scènes synthétiques) : avec « converted attic bedroom » et sans nommer le
poteau ni la porte-fenêtre, Z-Image ajoutait des lits, plaquait le poteau au pignon (la pièce
perdait 56 % de sa longueur) et changeait la porte-fenêtre en fenêtre à allège ; nommer une
porte-fenêtre hors champ la faisait inventer sur le mauvais pignon. La vue du faîtage, à
l'horizontale, donnait 47 % de l'image au toit.

Scène de test : combles de 7 × 7,5 m (faîtage 3,5 m, égout 1,0 m), poteau à mi-faîtage,
porte-fenêtre à deux vantaux au pignon A, trémie avec garde-corps près du pignon B."""

from __future__ import annotations

import math
from dataclasses import replace
from uuid import UUID

import pytest

from openplan.geom.truss import Ridge as RidgeSpan
from openplan.geom.truss import gable_face, ridge_span
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.finish import (
    ATTIC_BEDROOM,
    ATTIC_ROOM,
    _room_word,
    finish_prompt,
    room_lead,
)
from openplan.pipeline.guides import VISIBLE_MIN_PX, visible_pixels
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import (
    OPENING_WORDS,
    POST_WORDS,
    RAILING_WORDS,
    scene_words,
)
from openplan.pipeline.views import RIDGE, ROOF_MAX, _room, plan_shares, plan_views
from openplan.scene.catalog import load_catalog
from openplan.scene.factory import new_project
from openplan.scene.ops import AddFurniture, AddOpening, AddPost, AddStairs, apply_op
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import Point2, Point3, Source
from openplan.schemas.elements import OpeningKind, Swing, Wall
from openplan.schemas.scene import Project
from tests.factories import only_level

WIDTH, LENGTH, RIDGE_H, EAVE_H = 7.0, 7.5, 3.5, 1.0
FRENCH = f"a {OPENING_WORDS[OpeningKind.FRENCH_WINDOW][0]}"
POST = f"a {POST_WORDS[0]}"
RAILING = f"a {RAILING_WORDS[0]}"


def _est(v: float) -> Estimate:
    return Estimate(value=v, sigma=0.0, source=Source.USER_DEFINED)


def _attic(*, stairs: bool = True) -> tuple[Project, UUID, RidgeSpan, float, float]:
    project = new_project("KAN-136 combles")
    lid = only_level(project).id
    geo = AtticGeometry(
        length=_est(LENGTH),
        width=_est(WIDTH),
        ridge_height=_est(RIDGE_H),
        eave_height=_est(EAVE_H),
        camera_height=None,
        gable_distance=None,
    )
    project, _ = apply_attic(project, lid, geo)
    level = project.levels[lid]
    graph = WallGraph.from_level(level)
    ridge = ridge_span(level, graph)
    assert ridge is not None
    gables = []
    for w in (e for e in level.elements.values() if isinstance(e, Wall)):
        try:
            t, _ = gable_face(level, graph, ridge, w.id)
        except ValueError:
            continue
        gables.append((t, w))
    gables.sort(key=lambda g: g[0])
    (ta, wall_a), (tb, _) = gables
    mid = ridge.at((ta + tb) / 2.0)
    project = apply_op(
        project, lid, AddPost(position=Point2(x=mid[0], y=mid[1]), width=0.16, depth=0.16)
    )
    a = level.vertices[wall_a.start_vertex].position
    b = level.vertices[wall_a.end_vertex].position
    project = apply_op(
        project,
        lid,
        AddOpening(
            host_wall=wall_a.id,
            kind=OpeningKind.FRENCH_WINDOW,
            offset=(math.hypot(b.x - a.x, b.y - a.y) - 1.40) / 2,
            width=1.40,
            height=2.15,
            leaves=2,
            swing=Swing(hinge="left", direction="inward"),
        ),
    )
    if stairs:
        ux, uy = ridge.direction
        nx, ny = ridge.normal
        c = ridge.at(tb - 2.4)
        pos = Point2(x=c[0] + nx * 1.6, y=c[1] + ny * 1.6)
        project = apply_op(project, lid, AddStairs(position=pos, yaw=math.atan2(uy, ux)))
    return project, lid, ridge, ta, tb


def _camera(ridge: RidgeSpan, t: float, toward: float, pitch: float = -6.0) -> RenderCamera:
    """Sur l'axe du faîtage en `t`, regard vers le point de l'axe en `toward`, œil à 1,5 m."""
    x, y = ridge.at(t)
    tx, ty = ridge.at(toward)
    return RenderCamera(
        position=Point3(x=x, y=y, z=1.5),
        yaw=math.atan2(ty - y, tx - x),
        pitch=math.radians(pitch),
        focal_px=28.8 / 36 * 1280,
        width_px=1280,
        height_px=960,
    )


def _prompt(project: Project, lid: UUID, bake: Bake, camera: RenderCamera | None) -> str:
    words = scene_words(bake, project, lid, camera)
    return finish_prompt(project, None, lid, scene=words, engine="zimage")


@pytest.fixture(scope="module")
def scene() -> tuple[Project, UUID, RidgeSpan, float, float, Bake]:
    project, lid, ridge, ta, tb = _attic()
    return project, lid, ridge, ta, tb, bake_level(project, lid, style="realistic")


def test_what_the_view_sees_is_named(scene) -> None:  # type: ignore[no-untyped-def]
    """Depuis le pignon B vers le pignon A : poteau, porte-fenêtre et garde-corps sont vus."""
    project, lid, ridge, ta, tb, bake = scene
    cam = _camera(ridge, tb - 0.5, ta)
    words = scene_words(bake, project, lid, cam)
    assert POST in words.structure, words.structure
    assert FRENCH in words.structure, "la porte-fenêtre vue est nommée par son type"
    prompt = _prompt(project, lid, bake, cam)
    assert POST in prompt and FRENCH in prompt, prompt


def test_what_is_out_of_frame_is_not_named(scene) -> None:  # type: ignore[no-untyped-def]
    """Depuis le pignon A vers le pignon B : la porte-fenêtre est dans le dos, jamais nommée
    (la nommer la faisait inventer sur le mauvais pignon)."""
    project, lid, ridge, ta, tb, bake = scene
    cam = _camera(ridge, ta + 0.5, tb)
    opening = next(o for o in bake.openings)
    assert visible_pixels(bake, cam).get(str(opening.element_id), 0) < VISIBLE_MIN_PX
    words = scene_words(bake, project, lid, cam)
    assert FRENCH not in words.structure and "french" not in " ".join(words.structure)
    assert RAILING in words.structure, "le garde-corps de la trémie, lui, est devant"
    assert "french" not in _prompt(project, lid, bake, cam)


def test_without_a_camera_no_structure_is_named(scene) -> None:  # type: ignore[no-untyped-def]
    """Sans caméra, on ne sait pas ce que la vue voit : rien n'est nommé de la structure."""
    project, lid, _, _, _, bake = scene
    assert scene_words(bake, project, lid).structure == []


def test_a_post_against_a_wall_is_not_said_in_the_middle() -> None:
    project, lid, ridge, ta, tb = _attic(stairs=False)
    level = project.levels[lid]
    # À 25 cm du pignon A, hors de l'axe (sur l'axe, le poteau du milieu le cacherait).
    x, y = ridge.at(ta + 0.25)
    nx, ny = ridge.normal
    project = apply_op(project, lid, AddPost(position=Point2(x=x + nx * 1.5, y=y + ny * 1.5)))
    bake = bake_level(project, lid, style="realistic")
    words = scene_words(bake, project, lid, _camera(ridge, tb - 0.5, ta))
    assert any("post against the wall" in w for w in words.structure), words.structure
    assert POST in words.structure, "le poteau de mi-faîtage reste au milieu"
    assert level.kind == "attic"


def test_no_bedroom_without_a_visible_bed(scene) -> None:  # type: ignore[no-untyped-def]
    """Combles sans lit : « converted attic room », plus « converted attic bedroom » d'office."""
    project, lid, ridge, ta, tb, bake = scene
    prompt = _prompt(project, lid, bake, _camera(ridge, tb - 0.5, ta))
    assert ATTIC_ROOM in prompt and "bedroom" not in prompt, prompt
    assert _room_word(project, lid)[0] == ATTIC_ROOM, "sans vue : le plan n'a pas de lit"


def test_bedroom_only_when_the_bed_is_seen(scene) -> None:  # type: ignore[no-untyped-def]
    project, lid, ridge, ta, tb, _ = scene
    asset = next(a.id for a in load_catalog().assets if a.category == "bed_double")
    bx, by = ridge.at(ta + 1.6)
    nx, ny = ridge.normal
    with_bed = apply_op(
        project,
        lid,
        AddFurniture(asset_id=asset, position=Point2(x=bx + nx * 1.5, y=by + ny * 1.5), yaw=0.0),
    )
    bake = bake_level(with_bed, lid, style="realistic")
    seen = _prompt(with_bed, lid, bake, _camera(ridge, tb - 0.5, ta))
    assert ATTIC_BEDROOM in seen and "a double bed" in seen, seen
    # Au-delà du lit, regard vers le pignon B : le lit est dans le dos.
    behind = _prompt(with_bed, lid, bake, _camera(ridge, ta + 2.8, tb))
    assert "bedroom" not in behind and ATTIC_ROOM in behind, "le lit est dans le dos"
    assert _room_word(with_bed, lid)[0] == ATTIC_BEDROOM, "sans vue : le lit du plan suffit"


@pytest.mark.parametrize(
    ("word", "attic", "seen", "said"),
    [
        ("bedroom", False, frozenset(), "room"),
        ("bedroom", False, frozenset({"bed_single"}), "bedroom"),
        ("kitchen", False, frozenset({"chair"}), "room"),
        ("kitchen", False, frozenset({"kitchen_sink"}), "kitchen"),
        ("living room", False, frozenset({"sofa"}), "living room"),
        ("room", True, frozenset(), ATTIC_ROOM),
        ("home office", True, frozenset({"desk"}), "home office"),
        ("room", False, frozenset(), "room"),
    ],
)
def test_no_room_type_is_invented(word: str, attic: bool, seen: frozenset[str], said: str) -> None:
    assert room_lead(word, attic, seen) == said


def test_the_ridge_view_looks_down_like_the_other_attic_views() -> None:
    """La vue du faîtage passe par `_attic_tilt` : objectif baissé, toit ≤ 40 % (47 % avant)."""
    project, lid, _, _, _ = _attic(stairs=False)
    views, _ = plan_views(project, lid, ridge=True)
    (view,) = [v for v in views if v.axis == RIDGE]
    assert view.pitch < 0.0, "objectif baissé"
    room = _room(project.levels[lid])
    flat = plan_shares(room, replace(view, pitch=0.0), "4:5")
    tilted = plan_shares(room, view, "4:5")
    assert flat.roof > ROOF_MAX, f"à l'horizontale, {flat.roof:.0%} de toit"
    assert tilted.roof <= ROOF_MAX + 0.02, f"{tilted.roof:.0%} de toit"
