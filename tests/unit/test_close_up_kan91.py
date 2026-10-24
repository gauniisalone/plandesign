"""KAN-91 L5 : alerte « gros plan » après l'import. Cas synthétiques, sans les photos privées :
une pièce vue de son coin voit loin ; un recoin de 1,6 × 1,2 m photographié de près, non, et
son plan est plus étroit que 2 m."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.protocols import DepthMap, ImageArray
from openplan.pipeline import autoplan
from openplan.pipeline.autoplan import run_autoplan
from openplan.pipeline.confidence import CLOSE_UP_REACH, close_up
from openplan.pipeline.depthplan import depth_reach, observe_room
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from tests.factories import only_level
from tests.synthetic_depth import Face, SyntheticCamera, box_faces, render_depth

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"
needs_fixture = pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")


def _camera(
    position: tuple[float, float, float], pitch_deg: float = -15.0, w: int = 960, h: int = 720
) -> SyntheticCamera:
    return SyntheticCamera(
        position=position,
        yaw=math.radians(45),
        pitch=math.radians(pitch_deg),
        focal_px=0.6 * w,
        width_px=w,
        height_px=h,
    )


Scene = tuple[list[Face], SyntheticCamera]
ROOM: Scene = (box_faces(4.0, 3.5, 2.5), _camera((0.6, 0.5, 1.5)))
"""Une chambre vue de son coin : la photo voit le fond."""
NOOK: Scene = (box_faces(1.6, 1.2, 2.5), _camera((0.3, 0.25, 1.5), pitch_deg=-35.0))
"""Un recoin (le bureau d'un angle de chambre) photographié de près, vers le bas."""


def test_close_up_needs_both_signs() -> None:
    assert close_up(True, 1.2)
    assert close_up(True, None)  # pas de profondeur pour dire le contraire
    assert not close_up(True, 2.0)  # un vrai réduit, photographié de loin
    assert not close_up(False, 1.0)  # plan assez large : pas un détail
    assert CLOSE_UP_REACH == 1.5


@pytest.mark.parametrize(("scene", "far"), [(ROOM, True), (NOOK, False)])
def test_depth_reach_separates_the_room_from_the_nook(scene: Scene, far: bool) -> None:
    faces, camera = scene
    depth = render_depth(faces, camera, noise=0.01, seed=5)
    obs, _ = observe_room(depth, camera.focal_px, camera.principal_point)
    assert obs is not None
    reach = depth_reach(depth, obs.camera_height_m)
    assert reach is not None
    assert (reach >= CLOSE_UP_REACH) is far, f"portée {reach:.2f}"
    assert depth_reach(np.full((4, 4), np.nan), 1.5) is None


class SceneDepth:
    """Profondeur exacte de la scène, rendue à la taille de l'image importée."""

    model_id = "scene@0"

    def __init__(self, scene: Scene) -> None:
        self.faces, self.camera = scene

    def predict(self, image: ImageArray) -> DepthMap:
        cam = self.camera
        camera = SyntheticCamera(
            position=cam.position,
            yaw=cam.yaw,
            pitch=cam.pitch,
            focal_px=0.6 * image.width,
            width_px=image.width,
            height_px=image.height,
        )
        depth = render_depth(self.faces, camera, noise=0.01, seed=6)
        return DepthMap(depth=depth, metric=True, model_id=self.model_id)


@needs_fixture
@pytest.mark.parametrize(("scene", "alert"), [(ROOM, False), (NOOK, True)])
def test_autoplan_flags_the_close_up(
    scene: Scene,
    alert: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seule la profondeur propose un plan (les lignes de la fixture décrivent une autre pièce).
    def no_lines(*_a: object, **_k: object) -> None:
        raise DetectionError("lignes écartées par le test")

    monkeypatch.setattr(autoplan, "detect_and_propose", no_lines)
    project, ref, _ = import_image(
        new_project("gros plan"), tmp_path, FIXTURE.read_bytes(), original_name="p.png"
    )
    run = run_autoplan(
        project,
        tmp_path,
        only_level(project).id,
        ref.id,
        tmp_path / "runs",
        depth=SceneDepth(scene),
        camera_height_m=1.5,
    )
    assert run.method == "depth"
    assert run.close_up is alert
    keys = {f.key for f in run.estimate_confidence.factors} if run.estimate_confidence else set()
    assert ("room" in keys) is alert
