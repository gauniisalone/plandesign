"""KAN-95 : combles recoupés sur plusieurs photos (lots B et C), sur des combles synthétiques
(profondeur exacte, aucune photo réelle) : profil en « Λ » et pente votée, comble vu sous
plusieurs angles, photo aberrante écartée, mesure de référence posée sur une seule photo qui
vaut pour toutes, plan retouché jamais écrasé."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from openplan.geom.robust import robust_center, weighted_median
from openplan.pipeline.attic import analyse_attic, voted_pitch
from openplan.pipeline.attic_fusion import FusionRun, concordance, fuse_attic
from openplan.pipeline.confidence import ATTIC_SINGLE_ERROR, assess
from openplan.pipeline.reference import (
    FUSION_FACTOR_KEY,
    FUSION_KEY,
    ReferenceBody,
    apply_reference,
    load_depth,
    photo_factor,
    remove_reference,
)
from openplan.scene.history import USER_ACTOR
from openplan.schemas.common import Source
from openplan.schemas.elements import Camera, RoofPlane, Wall
from openplan.schemas.scene import Project, ReferenceSegment
from tests.attic_scenes import (
    AXIAL_A,
    AXIAL_B,
    FOCAL_PX,
    HEIGHT_PX,
    OBLIQUE,
    WIDTH_PX,
    Attic,
    camera,
    pixel_of,
    project_with_photos,
)
from tests.synthetic_depth import attic_faces, box_faces, render_depth

ATTIC = Attic()
THREE = [AXIAL_A, AXIAL_B, OBLIQUE]


def _fused(
    tmp_path: Path, cams: Sequence[tuple[float, ...]], attics: list[Attic] | None = None
) -> tuple[Project, list[UUID], FusionRun]:
    project, level, ids = project_with_photos(
        tmp_path, ATTIC, [camera(*c) for c in cams], attics=attics
    )
    return project, ids, fuse_attic(project, tmp_path, level, ids)


# ------------------------------------------------------------------ lot B
def test_b_profile_reads_the_ridge_and_the_planes_vote_the_pitch() -> None:
    cam = camera(*AXIAL_A)
    faces = attic_faces(
        length=ATTIC.length, width=ATTIC.width, ridge_height=ATTIC.ridge, eave_height=0.0
    )
    depth = render_depth(faces, cam)
    est, *_ = analyse_attic(depth, FOCAL_PX, cam.principal_point)
    assert est.camera_height is not None
    assert est.camera_height.value == pytest.approx(1.5, rel=0.02)
    assert est.ridge_profile is not None
    assert est.ridge_profile.ridge_z == pytest.approx(ATTIC.ridge, rel=0.05)
    assert est.ridge_height is not None and est.ridge_height.value == pytest.approx(
        ATTIC.ridge, rel=0.05
    )
    assert est.voted_pitch_deg == pytest.approx(ATTIC.pitch_deg, abs=2.0)
    assert est.along_ridge is not None and est.along_ridge > 0.9


def test_b_a_beam_under_the_ridge_does_not_flatten_it() -> None:
    """Poutre (ici un plafond étroit) sous le faîtage : le haut de l'enveloppe s'arrête à elle.
    Le faîtage n'est pas pris sur la poutre, mais à l'intersection des versants (grenier
    d'augus, photo mono : 1,86 m sur la poutre, 2,32 m au faîtage)."""
    from tests.synthetic_depth import Face

    cam = camera(*AXIAL_A)
    faces = [
        *attic_faces(
            length=ATTIC.length, width=ATTIC.width, ridge_height=ATTIC.ridge, eave_height=0.0
        ),
        Face((0.0, 0.0, 1.0), 2.1),
    ]
    est, *_ = analyse_attic(render_depth(faces, cam), FOCAL_PX, cam.principal_point)
    assert est.ridge_profile is not None and est.ridge_profile.under_beam
    assert est.ridge_height is not None
    assert est.ridge_height.value == pytest.approx(ATTIC.ridge, rel=0.05)


def test_b_a_flat_ceiling_gives_no_profile_and_no_ridge() -> None:
    cam = camera(1.0, 1.0, 1.5, 30.0, 0.0)
    depth = render_depth(box_faces(5.0, 4.0, 2.5), cam)
    est, *_ = analyse_attic(depth, FOCAL_PX, cam.principal_point)
    assert est.ridge_profile is None
    assert est.ridge_height is None


def test_b_robust_center_rejects_the_outlier_and_keeps_near_values() -> None:
    value, kept = robust_center([32.0, 33.0, 31.5, 60.0], [1.0, 1.0, 1.0, 1.0])
    assert value == pytest.approx(32.0, abs=1.0) and 3 not in kept
    # Des valeurs presque égales ne sont jamais des aberrantes (min_band).
    value, kept = robust_center([2.50, 2.51, 2.49], [1.0, 1.0, 1.0], min_band=0.1)
    assert kept == [0, 1, 2]
    assert weighted_median([1.0, 2.0, 10.0], [1.0, 1.0, 5.0]) == 10.0
    assert voted_pitch([]) is None


# ------------------------------------------------------------------ lot C
def test_c_attic_seen_from_three_angles(tmp_path: Path) -> None:
    _project, ids, run = _fused(tmp_path, THREE)
    assert run.applied, run.notices
    f = run.fusion
    assert f is not None
    assert f.pitch_deg == pytest.approx(ATTIC.pitch_deg, abs=2.0)
    assert f.ridge_height.value == pytest.approx(ATTIC.ridge, rel=0.05)
    assert f.width.value == pytest.approx(ATTIC.width, rel=0.08)
    assert f.eave_height.value == 0.0
    assert f.length.source is Source.INFERRED  # longueur déduite, jamais présentée comme vue
    assert f.used == 3 and f.summary.startswith("3 photos concordent à ±")
    assert f.spread is not None and f.spread <= 0.10
    # Contributions : chaque photo dit ce qu'elle apporte, la photo de biais la pente.
    reasons = {c.image_id: c.reason for c in f.photos}
    assert reasons[ids[2]].startswith("pente")
    assert sum(c.weight for c in f.photos) == pytest.approx(1.0, abs=0.01)
    level = next(iter(run.project.levels.values()))
    assert level.kind == "attic"
    assert level.height == pytest.approx(f.ridge_height.value)
    assert sum(isinstance(e, Wall) for e in level.elements.values()) == 4
    assert sum(isinstance(e, RoofPlane) for e in level.elements.values()) == 2
    cams = [e for e in level.elements.values() if isinstance(e, Camera)]
    assert sorted(c.image_id for c in cams) == sorted(ids)  # type: ignore[type-var]
    assert all(FUSION_KEY in c.metadata and FUSION_FACTOR_KEY in c.metadata for c in cams)
    assert level.scale_basis is not None and f.summary in level.scale_basis.text
    assert run.project.history[-1].label == "Combles recoupés sur 3 photos"
    assert run.confidence is not None
    assert any(x.key == "shape" for x in run.confidence.factors)


def test_c_an_aberrant_photo_is_rejected(tmp_path: Path) -> None:
    """Une photo d'un autre comble (faîtage 4 m) glissée dans la carte : écartée, le résultat
    et la concordance restent ceux des trois bonnes photos."""
    cams = [*THREE, (2.0, 0.0, 1.5, 0.0, 10.0)]
    attics = [ATTIC, ATTIC, ATTIC, Attic(ridge=4.0)]
    _project, ids, run = _fused(tmp_path, cams, attics=attics)
    f = run.fusion
    assert run.applied and f is not None
    bad = next(c for c in f.photos if c.image_id == ids[3])
    assert not bad.kept and bad.reason.startswith("écartée")
    assert f.ridge_height.value == pytest.approx(ATTIC.ridge, rel=0.05)
    assert f.width.value == pytest.approx(ATTIC.width, rel=0.08)
    assert f.used == 3 and f.spread is not None and f.spread <= 0.10


def test_p2d_a_close_up_photo_never_pulls_the_room_down(tmp_path: Path) -> None:
    """P2d (L5) : une photo prise à 0,5 m du pignon ne voit pas le fond de la pièce : écartée,
    avec sa raison ; le résultat reste celui des trois autres photos."""
    close = (7.0, 0.0, 1.5, 0.0, -45.0)  # à 1 m du pignon, regard vers le pied du mur
    _project, ids, run = _fused(tmp_path, [*THREE, close])
    f = run.fusion
    assert run.applied and f is not None
    bad = next(c for c in f.photos if c.image_id == ids[3])
    assert bad.close_up and not bad.kept and "gros plan" in bad.reason
    assert f.ridge_height.value == pytest.approx(ATTIC.ridge, rel=0.05)
    assert f.width.value == pytest.approx(ATTIC.width, rel=0.08)
    assert f.used == 3
    assert any("gros plan" in n for n in run.notices)


def _with_reference(
    project: Project, image_id: UUID, cam_spec: tuple[float, ...], length: float
) -> Project:
    """Mesure posée sur le pignon x = 0 à 0,30 m, d'un côté à l'autre (vue de la photo B)."""
    cam = camera(*cam_spec)
    half = length / 2.0
    p1 = pixel_of(cam, (0.0, -half, 0.3))
    p2 = pixel_of(cam, (0.0, half, 0.3))
    ref = project.images[image_id]
    segment = ReferenceSegment(p1=p1, p2=p2, length_m=length, author="test")
    return project.model_copy(
        update={
            "images": {
                **project.images,
                image_id: ref.model_copy(update={"reference_segments": [segment]}),
            }
        }
    )


LOW = [(x, y, 1.2, yaw, pitch) for x, y, _z, yaw, pitch in THREE]
"""Photos prises à 1,20 m : la hauteur supposée (1,50 m) grandit tout de 25 %."""


def test_c_a_reference_on_one_photo_holds_for_all(tmp_path: Path) -> None:
    project, level, ids = project_with_photos(tmp_path, ATTIC, [camera(*c) for c in LOW])
    plain = fuse_attic(project, tmp_path, level, ids)
    assert plain.fusion is not None
    assert plain.fusion.ridge_height.value == pytest.approx(ATTIC.ridge * 1.25, rel=0.06)
    referenced = _with_reference(project, ids[1], LOW[1], 3.0)
    run = fuse_attic(referenced, tmp_path, level, ids)
    f = run.fusion
    assert run.applied and f is not None and f.reference_image == ids[1]
    assert f.ridge_height.value == pytest.approx(ATTIC.ridge, rel=0.05)
    assert f.width.value == pytest.approx(ATTIC.width, rel=0.08)
    basis = run.project.levels[level].scale_basis
    assert basis is not None and basis.source == "reference" and basis.image_id == ids[1]
    assert any("vaut pour toutes" in n for n in run.notices)


def test_c_a_reference_after_fusion_on_any_photo_rescales_the_whole_plan(tmp_path: Path) -> None:
    project, level, ids = project_with_photos(tmp_path, ATTIC, [camera(*c) for c in LOW])
    run = fuse_attic(project, tmp_path, level, ids)
    assert run.applied
    fused = run.project
    before = fused.levels[level].height
    main_basis = fused.levels[level].scale_basis
    assert main_basis is not None
    other = next(i for i in ids if i != main_basis.image_id)
    assert photo_factor(fused.levels[level], other) is not None
    assert load_depth(tmp_path, other) is not None
    cam = camera(*LOW[ids.index(other)])
    # Une longueur vue par cette photo : le pignon qu'elle regarde, ou le sol sous elle.
    if ids.index(other) == 0:
        a, b = (ATTIC.length, -1.5, 0.3), (ATTIC.length, 1.5, 0.3)
    elif ids.index(other) == 1:
        a, b = (0.0, -1.5, 0.3), (0.0, 1.5, 0.3)
    else:
        a, b = (5.0, 0.5, 0.0), (5.0, 2.0, 0.0)
    length = math.dist(a, b)
    body = ReferenceBody(p1=pixel_of(cam, a), p2=pixel_of(cam, b), length_m=length)
    measured = apply_reference(fused, tmp_path, other, body, actor=USER_ACTOR, author="test")
    lvl = measured.levels[level]
    assert lvl.height == pytest.approx(ATTIC.ridge, rel=0.06)
    assert lvl.scale_basis is not None and lvl.scale_basis.source == "reference"
    assert "photos recoupées" in lvl.scale_basis.text
    # Les autres photos suivent : leur échelle a été recalée du même facteur.
    ratio = lvl.height / before
    for i in ids:
        f_before = photo_factor(fused.levels[level], i)
        f_after = photo_factor(lvl, i)
        assert f_before is not None and f_after is not None
        assert f_after / f_before == pytest.approx(ratio, rel=0.01)
    segment = measured.images[other].reference_segments[0].id
    back = remove_reference(measured, other, segment, actor=USER_ACTOR)
    assert back.levels[level].height == pytest.approx(before, rel=1e-6)


def test_c_a_hand_edited_plan_is_never_replaced(tmp_path: Path) -> None:
    _project, ids, run = _fused(tmp_path, THREE)
    assert run.applied
    level_id = next(iter(run.project.levels))
    level = run.project.levels[level_id]
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    edited = level.model_copy(
        update={
            "elements": {
                **level.elements,
                wall.id: wall.model_copy(update={"source": Source.USER_DEFINED}),
            }
        }
    )
    project2 = run.project.model_copy(update={"levels": {level_id: edited}})
    again = fuse_attic(project2, tmp_path, level_id, ids)
    assert not again.applied
    assert again.project == project2
    assert "retouché" in again.notices[0]


def test_c_photos_all_along_the_ridge_are_flagged(tmp_path: Path) -> None:
    """Sans photo de biais, les photos dans l'axe concordent sans prouver la pente : l'erreur
    de forme annoncée ne descend pas sous AXIAL_ONLY_ERROR, et c'est dit."""
    from openplan.pipeline.attic_fusion import AXIAL_ONLY_ERROR

    _project, _ids, run = _fused(tmp_path, [AXIAL_A, AXIAL_B])
    assert run.applied and run.fusion is not None
    assert "dans l'axe seulement" in run.fusion.summary
    assert any("Aucune photo de biais" in n for n in run.notices)
    assert run.confidence is not None and run.confidence.expected_error >= AXIAL_ONLY_ERROR


def test_c_one_photo_or_none_readable_is_said(tmp_path: Path) -> None:
    project, level, ids = project_with_photos(tmp_path, ATTIC, [camera(*AXIAL_A)])
    run = fuse_attic(project, tmp_path, level, ids)
    assert not run.applied and "Une seule photo" in run.notices[0]
    assert concordance(2, None) == "2 photos : pas assez pour recouper"
    assert concordance(4, 0.071) == "4 photos concordent à ±8 %"


def test_c_the_photos_card_linked_to_the_plan_is_what_is_fused(tmp_path: Path) -> None:
    """Route : les membres de la carte Photos reliée au plan (C1) sont recoupés, le plan
    recoupé est enregistré, annulable."""
    import shutil

    from openplan.api.routes.attic_fusion import fuse_members, member_photos
    from openplan.scene.store import ProjectStore

    work = tmp_path / "work"
    project, level, ids = project_with_photos(work, ATTIC, [camera(*c) for c in THREE])
    store = ProjectStore(tmp_path / "projects")
    store.save(project)
    shutil.copytree(work / "images", store.project_dir(project.id) / "images", dirs_exist_ok=True)
    assert sorted(member_photos(project, store, level)) == sorted(ids)
    # Niveau pas encore en combles : rien sans `force` (la chaîne automatique ne recoupe
    # que des combles).
    assert fuse_members(store, project.id, level, None) is None
    run = fuse_members(store, project.id, level, None, force=True)
    assert run is not None and run.applied
    saved = store.load(project.id)
    assert saved.levels[level].kind == "attic"
    assert saved.history[-1].label == "Combles recoupés sur 3 photos"


# ------------------------------------------------------------------ lot F (confiance)
def test_f_single_photo_attic_and_fused_confidence() -> None:
    plain = assess("assumed")
    single = assess("assumed", attic_single=True)
    assert single.expected_error == pytest.approx(
        math.hypot(plain.expected_error, ATTIC_SINGLE_ERROR), abs=1e-3
    )
    fused = assess("assumed", fused_photos=4, fusion_spread=0.23)
    assert fused.expected_error == pytest.approx(math.hypot(plain.expected_error, 0.23), abs=1e-3)
    assert "4 photos" in fused.reason
    tight = assess("reference", fused_photos=3, fusion_spread=0.01)
    assert any(f.key == "shape" and f.error == pytest.approx(0.05) for f in tight.factors)


def test_scene_helpers_are_consistent() -> None:
    cam = camera(*AXIAL_A)
    u, v = pixel_of(cam, (5.0, 0.4, 1.5 + math.tan(math.radians(10.0)) * 3.5))
    assert u == pytest.approx(WIDTH_PX / 2, abs=1.0) and v == pytest.approx(HEIGHT_PX / 2, abs=1.0)
    assert np.isfinite(FOCAL_PX)
