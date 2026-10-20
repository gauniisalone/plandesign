"""KAN-62 : la hauteur de prise de vue par défaut de l'app (1,50 m) n'est jamais « saisie ».
Sans saisie, elle est `inferred` et dite « supposée (par défaut) » ; une vraie saisie reste
`user_defined` et « saisie ». Les valeurs calculées ne changent pas, seulement l'étiquette."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openplan.pipeline.attic import AtticEstimate, Estimate, analyse_attic, rescale
from openplan.pipeline.autoplan import run_autoplan
from openplan.pipeline.depthplan import observe_room, propose_floorplan_from_depth
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.schemas import Camera, RoofPlane, Source
from tests.control_attic import CAMERA_HEIGHT, EAVE, GABLE_DISTANCE, LENGTH, RIDGE, WIDTH
from tests.factories import only_level
from tests.synthetic_depth import SyntheticCamera, attic_faces, box_faces, render_depth
from tests.unit.test_autoplan import AtticDepth, _photo, needs_fixture
from tests.unit.test_depthplan import CAMERA as ROOM_CAMERA
from tests.unit.test_depthplan import _project_with_image


def _values(est: AtticEstimate) -> tuple[object, ...]:
    """Tout ce qui est calculé : valeurs et incertitudes, sans les étiquettes."""
    pick = lambda e: None if e is None else (round(e.value, 9), round(e.sigma, 9))  # noqa: E731
    return (
        pick(est.camera_height),
        pick(est.ridge_height),
        tuple(pick(e) for e in est.eave_heights),
        pick(est.width),
        pick(est.usable_width),
        pick(est.gable_distance),
        pick(est.eave_bound),
        round(est.scale_factor, 9),
    )


def test_rescale_labels_entered_or_default_with_the_same_values() -> None:
    est = AtticEstimate(
        camera_height=Estimate(value=0.9, sigma=0.2),
        ridge_height=Estimate(value=2.2, sigma=0.4),
        eave_heights=[Estimate(value=0.6, sigma=0.1)],
    )
    entered = rescale(est, 1.5)
    default = rescale(est, DEFAULT_CAMERA_HEIGHT_M, entered=False)
    assert _values(entered) == _values(default)
    assert entered.camera_height is not None and default.camera_height is not None
    assert entered.camera_height.source is Source.USER_DEFINED
    assert entered.camera_height.note == "hauteur de prise de vue saisie 1,50 m"
    assert default.camera_height.source is Source.INFERRED
    assert default.camera_height.note == "hauteur de prise de vue supposée 1,50 m (par défaut)"
    assert default.scale_note is not None and "saisie" not in default.scale_note
    assert "supposée 1,50 m (par défaut)" in default.scale_note


def _raised_attic(entered: bool) -> tuple[AtticEstimate, list[str]]:
    cam = SyntheticCamera(
        position=(LENGTH - GABLE_DISTANCE, 0.0, CAMERA_HEIGHT),
        yaw=0.0,
        pitch=math.radians(15),
        focal_px=420.0,
        width_px=640,
        height_px=480,
    )
    faces = attic_faces(length=LENGTH, width=WIDTH, ridge_height=RIDGE, eave_height=EAVE)
    est, _p, cls, _px, warnings = analyse_attic(
        render_depth(faces, cam),
        cam.focal_px,
        cam.principal_point,
        camera_height_m=CAMERA_HEIGHT,
        camera_height_entered=entered,
    )
    assert cls is not None
    return est, warnings


def test_kan45_fallback_says_supposed_for_the_default_height() -> None:
    """Le repli de KAN-45 (sol hors champ) avec la hauteur par défaut : « supposée »."""
    entered, w_entered = _raised_attic(True)
    default, w_default = _raised_attic(False)
    assert _values(entered) == _values(default)
    assert entered.camera_height is not None and default.camera_height is not None
    assert entered.camera_height.source is Source.USER_DEFINED
    assert default.camera_height.source is Source.INFERRED
    assert default.camera_height.note is not None and "supposée" in default.camera_height.note
    assert any("Sol hors champ" in w and "hauteur saisie" in w for w in w_entered)
    assert any("Sol hors champ" in w and "hauteur supposée" in w for w in w_default)
    assert not any("saisie" in w for w in w_default)


def test_depthplan_assumption_says_supposed_for_the_default_height() -> None:
    depth = render_depth(box_faces(4.0, 3.0, 2.5), ROOM_CAMERA, noise=0.01, seed=1)
    obs, _ = observe_room(depth, ROOM_CAMERA.focal_px, ROOM_CAMERA.principal_point)
    assert obs is not None
    results = {}
    for entered in (True, False):
        project, ref = _project_with_image()
        level_id = only_level(project).id  # type: ignore[arg-type]
        after, result = propose_floorplan_from_depth(
            project,  # type: ignore[arg-type]
            level_id,
            obs,
            image_id=ref.id,
            camera_height_m=DEFAULT_CAMERA_HEIGHT_M,
            camera_height_entered=entered,
        )
        results[entered] = (only_level(after), result)
    texts = {k: " ".join(a.text for a in r.assumptions) for k, (_l, r) in results.items()}
    assert "hauteur de prise de vue saisie (1.50 m)" in texts[True]
    assert "hauteur de prise de vue supposée (par défaut) (1.50 m)" in texts[False]
    assert "saisie" not in texts[False]
    sizes = {
        k: sorted((round(v.position.x, 6), round(v.position.y, 6)) for v in level.vertices.values())
        for k, (level, _r) in results.items()
    }
    assert sizes[True] == sizes[False], "mêmes cotes"


@needs_fixture
def test_autoplan_default_height_is_never_user_defined(tmp_path: Path) -> None:
    """Dans l'app, la route autoplan ne passe aucune hauteur : la Caméra des combles et
    l'hypothèse disent « supposée ». Avec une hauteur saisie (même valeur) : « saisie ».
    Les combles construits sont identiques."""
    runs = {}
    for entered in (False, True):
        work = tmp_path / ("saisie" if entered else "defaut")
        work.mkdir()
        project, level = _photo(work)
        image_id = next(iter(project.images))
        ref = project.images[image_id]
        runs[entered] = run_autoplan(
            project,
            work,
            level.id,
            image_id,
            work / "runs",
            depth=AtticDepth(focal_px=0.66 * ref.width_px),
            camera_height_m=DEFAULT_CAMERA_HEIGHT_M if entered else None,
        )
    for entered, run in runs.items():
        assert run.method == "attic"
        camera = next(e for e in only_level(run.project).elements.values() if isinstance(e, Camera))
        word = "saisie" if entered else "supposée"
        assert camera.source is (Source.USER_DEFINED if entered else Source.INFERRED)
        assert any(f"Hauteur de l'appareil {word} 1.50 m" in a.text for a in run.result.assumptions)
    shapes = {
        k: sorted(
            (round(r.eave_height, 6), round(r.pitch, 6), round(r.eave_start.x, 6))
            for r in only_level(run.project).elements.values()
            if isinstance(r, RoofPlane)
        )
        for k, run in runs.items()
    }
    assert shapes[True] == shapes[False], "mêmes combles"
    heights = {k: only_level(run.project).height for k, run in runs.items()}
    assert heights[True] == pytest.approx(heights[False])
