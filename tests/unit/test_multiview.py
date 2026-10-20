"""Multi-photos (2.5a) : une photo de plus confronte la scène, confirme les murs inférés
qu'elle voit avec leur vis-à-vis, et signale les contradictions sans rien casser."""

from __future__ import annotations

import math
from uuid import UUID, uuid4

import numpy as np
import pytest

from openplan.adapters.heuristic.box_detector import BoxAnalysis, FloorEdge
from openplan.adapters.protocols import CameraParameters
from openplan.pipeline.floorplan import ACTOR, DetectionError
from openplan.pipeline.multiview import (
    ADOPT_STEP,
    ORIENTATION_KEY,
    ROTATE_STEP,
    STEP,
    USER_ORIENTED_KEY,
    Observation,
    add_view,
    adopt_view,
    best_placement,
    fit_axis,
    observations_from_metadata,
    observe,
    rotate_view,
)
from openplan.scene.factory import new_project
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import UpdateElement, apply_op, detect_rooms
from openplan.schemas import Camera, ImageRef, Project, Source, Vertex, Wall
from openplan.schemas.common import Point2, Point3
from openplan.validation.consistency import (
    DISTANCES_KEY,
    EXTENTS_KEY,
    check_views,
    walls_box,
)
from openplan.validation.invariants import validate_invariants
from tests.factories import by_name, only_level

H = 1.4
NAMES = ("Mur bas", "Mur droit", "Mur haut", "Mur gauche")


def _image() -> ImageRef:
    return ImageRef(
        path="images/x.png", sha256="0" * 64, width_px=960, height_px=720, exif_stripped=True
    )


def photo1_scene(
    sources: tuple[Source, ...] = (
        Source.INFERRED,
        Source.ESTIMATED,
        Source.ESTIMATED,
        Source.INFERRED,
    ),
) -> tuple[Project, UUID]:
    """Scène telle que la laisse la proposition mono-photo : rectangle 3,725 × 2,825 dont
    deux murs vus (droit, haut) et deux supposés (bas, gauche), caméra « Photo » en (0,5 ; 0,5).
    Renvoie le projet et l'identifiant d'une seconde image importée."""
    project = new_project("multi")
    level = only_level(project)
    corners = [(0.0, 0.0), (3.725, 0.0), (3.725, 2.825), (0.0, 2.825)]
    vertices = [
        Vertex(position=Point2(x=x, y=y), source=Source.ESTIMATED, confidence=0.4)
        for x, y in corners
    ]
    walls = [
        Wall(
            name=NAMES[i],
            start_vertex=vertices[i].id,
            end_vertex=vertices[(i + 1) % 4].id,
            source=sources[i],
            confidence=0.45 if sources[i] is Source.ESTIMATED else 0.2,
        )
        for i in range(4)
    ]
    first, second = _image(), _image()
    camera = Camera(
        name="Photo",
        position=Point3(x=0.5, y=0.5, z=H),
        yaw=0.87,
        pitch=-0.2,
        image_id=first.id,
        source=Source.ESTIMATED,
        confidence=0.5,
    )
    level = level.model_copy(
        update={
            "vertices": {v.id: v for v in vertices},
            "elements": {**{w.id: w for w in walls}, camera.id: camera},
        }
    )
    level = detect_rooms(level)
    project = project.model_copy(
        update={"levels": {level.id: level}, "images": {first.id: first, second.id: second}}
    )
    return project, second.id


def analysis(
    edges: list[tuple[str, float]], *, h: float = H, yaw: float | None = math.pi / 2
) -> BoxAnalysis:
    """Arêtes sol-mur en mètres (caméra à l'origine), converties en hauteurs de caméra."""
    return BoxAnalysis(
        camera=CameraParameters(
            focal_px=560.0,
            principal_point=(480.0, 360.0),
            pitch=-0.2094,
            roll=0.0,
            yaw_hint=yaw,
            confidence=0.5,
            model_id="vp_camera@0.1",
        ),
        frame=None,
        segments=np.zeros((0, 4)),
        families={},
        floor_edges=[
            FloorEdge(
                axis=axis,  # type: ignore[arg-type]
                offset=metres / h,
                endpoints_px=((0.0, 0.0), (1.0, 1.0)),
                weight_px=100.0,
                support=0.5,
            )
            for axis, metres in edges
        ],
    )


# Photo 2 regarde le mur haut depuis le milieu de la pièce et voit les deux murs latéraux :
# arêtes « y » (parallèles à Y) à x = ±1,925 et arête « x » (mur de face) à y = +2,625.
SECOND_VIEW = [("y", -1.925), ("y", 1.925), ("x", 2.625)]


def test_observe_reads_edges_like_the_single_photo_proposal() -> None:
    both = observe(analysis(SECOND_VIEW).edges("y"), H)
    assert (both.lo, both.hi) == pytest.approx((-1.925, 1.925)) and both.extent == pytest.approx(
        3.85
    )
    front = observe(analysis(SECOND_VIEW).edges("x"), H)
    assert front.lo is None and front.hi == pytest.approx(2.625)
    assert observe([], H) == Observation()
    flipped = both.flipped()
    assert (flipped.lo, flipped.hi) == pytest.approx((-1.925, 1.925))
    assert front.flipped().lo == pytest.approx(-2.625) and front.flipped().hi is None


def test_fit_axis_moves_only_inferred_walls() -> None:
    seen = Observation(
        lo=-1.0,
        hi=3.0,
        lo_edge=analysis(SECOND_VIEW).floor_edges[0],
        hi_edge=analysis(SECOND_VIEW).floor_edges[1],
    )
    fit = fit_axis(seen, 0.0, 3.5, movable_min=True, movable_max=False)
    assert fit.position == pytest.approx(0.5) and fit.moves["min"][0] == pytest.approx(-0.5)
    fit = fit_axis(seen, 0.0, 3.5, movable_min=False, movable_max=True)
    assert fit.position == pytest.approx(1.0) and fit.moves["max"][0] == pytest.approx(4.0)
    fit = fit_axis(seen, 0.0, 3.5, movable_min=False, movable_max=False)
    assert not fit.moves and fit.residual == pytest.approx(0.5 / 3.5)
    assert fit.position == pytest.approx((1.0 + 0.5) / 2)
    # Un seul mur vu : la caméra est placée par rapport à lui, sans résidu.
    assert fit_axis(
        Observation(hi=2.0), 0.0, 3.5, movable_min=False, movable_max=False
    ).position == pytest.approx(1.5)
    assert fit_axis(
        Observation(lo=-2.0), 0.0, 3.5, movable_min=False, movable_max=False
    ).position == pytest.approx(2.0)
    # Hors de la pièce : pénalisé.
    assert (
        fit_axis(Observation(hi=6.0), 0.0, 3.5, movable_min=False, movable_max=False).residual > 5
    )


def test_add_view_confirms_the_inferred_side_wall_and_places_the_camera() -> None:
    project, image_id = photo1_scene()
    level_id = only_level(project).id
    after, result = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H
    )
    assert result.step == STEP and result.patch and after.history[-1].actor == ACTOR
    level = only_level(after)
    left = by_name(level, "Mur gauche", Wall)
    assert left.source is Source.ESTIMATED and left.provenance is not None
    assert left.provenance.image_ids == [image_id] and left.provenance.step == STEP
    assert level.vertices[left.start_vertex].position.x == pytest.approx(3.725 - 3.85)
    assert by_name(level, "Mur bas", Wall).source is Source.INFERRED, "un seul mur vu sur Y"
    assert by_name(level, "Mur droit", Wall).source is Source.ESTIMATED
    box = walls_box(level)
    assert box is not None and box[2] - box[0] == pytest.approx(3.85)
    camera = by_name(level, "Photo 2", Camera)
    assert camera.image_id == image_id and camera.position.z == H
    assert camera.position.x == pytest.approx(3.725 - 1.925)
    # Pièce symétrique : la photo peut regarder vers le mur haut ou vers le mur bas.
    assert camera.metadata["orientation_ambiguous"] is True
    assert camera.position.y == pytest.approx(2.825 - 2.625) or camera.position.y == pytest.approx(
        2.625
    )
    assert camera.metadata[EXTENTS_KEY] == {"x": 3.85}
    distances = camera.metadata[DISTANCES_KEY]
    assert isinstance(distances, dict) and set(distances) <= {"x-", "x+", "y-", "y+"}
    assert any("ambiguë" in a.text for a in result.assumptions)
    assert any("Murs confirmés" in w and "Mur gauche" in w for w in result.warnings)
    report = validate_invariants(after)
    assert report.valid and not [v for v in report.violations if v.type == "inconsistent_views"]
    # La pièce dérivée suit les murs déplacés.
    assert len(level.elements) == len(only_level(project).elements) + 1


def test_add_view_flags_a_contradiction_without_moving_estimated_walls() -> None:
    """Quatre murs estimés ; la même photo à une hauteur de caméra surestimée de 15 % voit
    4,43 m entre les murs latéraux contre 3,725 m : rien ne bouge, la vue est signalée."""
    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    after, result = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    level = only_level(after)
    assert walls_box(level) == walls_box(only_level(project)), "aucun mur estimé n'a bougé"
    violations = [v for v in check_views(after) if v.type == "inconsistent_views"]
    assert len(violations) == 1 and violations[0].severity == "medium"
    camera = by_name(level, "Photo 2", Camera)
    assert violations[0].element_ids == [camera.id]
    assert "Photo 2" in violations[0].message and "+19 %" in violations[0].message
    assert "Mur gauche et Mur droit" in violations[0].message
    assert any("Photo 2" in w for w in result.warnings)
    assert validate_invariants(after).valid, "une contradiction n'invalide pas la scène"
    # À la bonne hauteur, 3,85 m contre 3,725 m : dans la tolérance, rien à signaler.
    fine, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H
    )
    assert check_views(fine) == []


def test_contradiction_message_names_walls_and_gap_in_cm() -> None:
    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    after, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    (violation,) = check_views(after)
    text = violation.message
    assert text.startswith("Photo « Photo 2 » voit Mur gauche et Mur droit à 4,43 m")
    assert "dans le plan ils sont à 3,73 m" in text and "écart +70 cm" in text
    assert "Garder la photo" in text and "l'écarter" in text


def test_excluded_view_keeps_its_camera_but_no_longer_weighs() -> None:
    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    after, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    assert check_views(after)
    camera = by_name(only_level(after), "Photo 2", Camera)
    excluded = apply_op(
        after,
        level_id,
        UpdateElement(
            element_id=camera.id, fields={"metadata": {**camera.metadata, "excluded": True}}
        ),
    )
    assert check_views(excluded) == []
    kept = by_name(only_level(excluded), "Photo 2", Camera)
    assert kept.image_id == image_id and kept.metadata[EXTENTS_KEY] == {"x": 4.4275}
    assert image_id in excluded.images, "écarter n'est pas supprimer"
    back = apply_op(
        excluded,
        level_id,
        UpdateElement(
            element_id=camera.id, fields={"metadata": {**kept.metadata, "excluded": False}}
        ),
    )
    assert len(check_views(back)) == 1


def test_adopt_view_moves_the_least_certain_wall_to_match_the_photo() -> None:
    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    level = only_level(project)
    # Le mur gauche est le moins sûr des deux murs latéraux.
    left = by_name(level, "Mur gauche", Wall)
    project = project.model_copy(
        update={
            "levels": {
                level_id: level.model_copy(
                    update={
                        "elements": {
                            **level.elements,
                            left.id: left.model_copy(update={"confidence": 0.3}),
                        }
                    }
                )
            }
        }
    )
    viewed, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    camera = by_name(only_level(viewed), "Photo 2", Camera)
    after, result = adopt_view(viewed, level_id, camera.id)
    assert result.step == ADOPT_STEP and result.patch and after.history[-1].actor == USER_ACTOR
    assert "Mur gauche" in result.warnings[0]
    moved = by_name(only_level(after), "Mur gauche", Wall)
    assert moved.provenance is not None and moved.provenance.image_ids == [image_id]
    assert only_level(after).vertices[moved.start_vertex].position.x == pytest.approx(
        3.725 - 4.4275
    )
    assert by_name(only_level(after), "Mur droit", Wall) == by_name(
        only_level(viewed), "Mur droit", Wall
    )
    assert check_views(after) == [] and validate_invariants(after).valid
    # Une seconde fois : plus rien à déplacer, projet inchangé.
    same, again = adopt_view(after, level_id, camera.id)
    assert same == after and not again.patch and "rien à déplacer" in again.warnings[0]


def test_adopt_view_refuses_when_both_walls_are_the_users() -> None:
    project, image_id = photo1_scene(
        (Source.ESTIMATED, Source.USER_DEFINED, Source.ESTIMATED, Source.USER_DEFINED)
    )
    level_id = only_level(project).id
    viewed, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    camera = by_name(only_level(viewed), "Photo 2", Camera)
    with pytest.raises(DetectionError, match="font autorité"):
        adopt_view(viewed, level_id, camera.id)
    first = by_name(only_level(viewed), "Photo", Camera)
    with pytest.raises(DetectionError, match="deux murs opposés"):
        adopt_view(viewed, level_id, first.id)
    with pytest.raises(DetectionError, match="caméra d'une photo"):
        adopt_view(viewed, level_id, by_name(only_level(viewed), "Mur haut", Wall).id)


def test_rotate_view_turns_the_camera_and_recontrols_consistency() -> None:
    """Pièce symétrique : la photo 2 est lue dans l'une des deux orientations équivalentes.
    Un quart de tour : les 3,85 m vus tombent sur l'axe court (2,825) → contradiction dite,
    aucun mur ne bouge ; deux quarts de plus : orientation opposée, cohérente, caméra
    replacée en miroir, visée tournée de 180°."""
    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    viewed, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H
    )
    camera = by_name(only_level(viewed), "Photo 2", Camera)
    assert check_views(viewed) == []
    observed = observations_from_metadata(camera)
    assert observed["x"].extent == pytest.approx(3.85) and observed["y"].extent is None
    turned, result = rotate_view(viewed, level_id, camera.id)
    assert result.step == ROTATE_STEP and result.patch and turned.history[-1].actor == USER_ACTOR
    assert walls_box(only_level(turned)) == walls_box(only_level(viewed)), "aucun mur déplacé"
    after = by_name(only_level(turned), "Photo 2", Camera)
    assert after.metadata[USER_ORIENTED_KEY] is True
    assert after.metadata[ORIENTATION_KEY] == (int(camera.metadata[ORIENTATION_KEY]) + 1) % 4  # type: ignore[call-overload]
    assert after.metadata[EXTENTS_KEY] == {"y": 3.85}
    assert math.degrees(after.yaw - camera.yaw) % 360 == pytest.approx(90.0, abs=1e-6)
    violations = [v for v in check_views(turned) if v.type == "inconsistent_views"]
    assert len(violations) == 1 and violations[0].element_ids == [after.id]
    assert any("+36 %" in w for w in result.warnings), result.warnings
    assert any("quart de tour" in w for w in result.warnings)
    # Un quart de plus (demi-tour en tout) : l'autre orientation équivalente, cohérente,
    # caméra en miroir.
    opposite, result = rotate_view(turned, level_id, camera.id)
    mirrored = by_name(only_level(opposite), "Photo 2", Camera)
    assert check_views(opposite) == [] and mirrored.metadata[EXTENTS_KEY] == {"x": 3.85}
    assert math.degrees(mirrored.yaw - camera.yaw) % 360 == pytest.approx(180.0, abs=1e-6)
    assert mirrored.position.x == pytest.approx(3.725 - camera.position.x)
    assert mirrored.position.y == pytest.approx(2.825 - camera.position.y)
    assert not any("voit" in w or "hors de la pièce" in w for w in result.warnings)
    # Quatre quarts au total : retour à la position de départ.
    back, _ = rotate_view(opposite, level_id, camera.id, quarter_turns=2)
    again = by_name(only_level(back), "Photo 2", Camera)
    assert again.position.x == pytest.approx(camera.position.x)
    assert again.metadata[ORIENTATION_KEY] == camera.metadata[ORIENTATION_KEY]
    assert validate_invariants(back).valid


def test_rotate_view_refuses_what_has_nothing_to_turn() -> None:
    project, image_id = photo1_scene()
    level_id = only_level(project).id
    first = by_name(only_level(project), "Photo", Camera)
    with pytest.raises(DetectionError, match="pas d'observation à tourner"):
        rotate_view(project, level_id, first.id)
    viewed, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H
    )
    camera = by_name(only_level(viewed), "Photo 2", Camera)
    with pytest.raises(DetectionError, match="multiple de 4"):
        rotate_view(viewed, level_id, camera.id, quarter_turns=4)
    with pytest.raises(DetectionError, match="caméra d'une photo"):
        rotate_view(viewed, level_id, by_name(only_level(viewed), "Mur haut", Wall).id)
    with pytest.raises(DetectionError, match="niveau inconnu"):
        rotate_view(viewed, uuid4(), camera.id)


def test_contradiction_can_be_read_as_a_longer_room_while_a_wall_is_inferred() -> None:
    """Limite documentée : avec le mur bas encore inféré, la vue à mauvaise hauteur est lue
    comme une pièce plus profonde (orientation tournée d'un quart de tour) et ce mur bouge."""
    project, image_id = photo1_scene()
    level_id = only_level(project).id
    after, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.3
    )
    camera = by_name(only_level(after), "Photo 2", Camera)
    assert camera.metadata["orientation_quarter_turns"] in (1, 3)
    assert by_name(only_level(after), "Mur bas", Wall).source is Source.ESTIMATED
    assert check_views(after) == []


def test_add_view_prefers_the_orientation_matching_the_long_axis() -> None:
    """Les deux murs vus sont à 3,85 m l'un de l'autre : c'est l'axe X (3,725) de la scène,
    pas l'axe Y (2,825), même si le mur bas est inféré et pourrait être déplacé."""
    project, _ = photo1_scene()
    level = only_level(project)
    view = analysis(SECOND_VIEW)
    placement, ambiguous = best_placement(
        level, observe(view.edges("y"), H), observe(view.edges("x"), H)
    )
    assert placement.quarter_turns in (0, 2) and ambiguous
    assert "min" in placement.fits["x"].moves and not placement.fits["y"].moves


def test_add_view_without_edges_or_walls() -> None:
    project, image_id = photo1_scene()
    level_id = only_level(project).id
    same, result = add_view(project, level_id, analysis([]), image_id=image_id)
    assert (
        same == project
        and result.confidence == 0
        and any("rien à confronter" in w for w in result.warnings)
    )
    empty = new_project("vide")
    with pytest.raises(DetectionError, match="pas de murs"):
        add_view(empty, only_level(empty).id, analysis(SECOND_VIEW), image_id=image_id)
    with pytest.raises(DetectionError, match="image inconnue"):
        add_view(project, level_id, analysis(SECOND_VIEW), image_id=uuid4())
    with pytest.raises(DetectionError, match="niveau inconnu"):
        add_view(project, uuid4(), analysis(SECOND_VIEW), image_id=image_id)


def test_user_defined_walls_are_never_moved() -> None:
    project, image_id = photo1_scene(
        (Source.INFERRED, Source.ESTIMATED, Source.ESTIMATED, Source.USER_DEFINED)
    )
    level_id = only_level(project).id
    after, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H
    )
    assert walls_box(only_level(after)) == walls_box(only_level(project))
    assert by_name(only_level(after), "Mur gauche", Wall).source is Source.USER_DEFINED
    # 3,85 m vus contre 3,725 m posés par l'utilisateur : 3 %, dans la tolérance.
    assert check_views(after) == []
    camera = by_name(only_level(after), "Photo 2", Camera)
    assert camera.metadata[EXTENTS_KEY] == {"x": 3.85}
