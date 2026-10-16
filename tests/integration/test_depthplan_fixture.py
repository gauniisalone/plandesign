"""Pièce en L rendue par Blender, profondeur apprise (Depth Anything), plan proposé.

Deux critères de la roadmap mesurés ensemble, sur le même rendu :
- 2.2 : corrélation de Spearman ≥ 0,9 entre la profondeur estimée et la passe de profondeur
  Cycles (vérité, distance au plan caméra) ; le rapport d'échelle médian est imprimé ;
- 2.3 : IoU ≥ 0,7 entre le polygone proposé depuis la profondeur (hauteur de prise de vue
  saisie) et la pièce vraie.
Ignoré sans Blender ou sans les poids (`openplan models pull …`). Rendu à la volée, rien de
commis.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from openplan.adapters.neural.depth_anything import MODEL_ID
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, is_installed
from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import find_blender, run_blender
from openplan.pipeline.depthplan import run_depthplan
from openplan.pipeline.ingestion import import_image, load_image
from openplan.scene.factory import new_project
from openplan.schemas import Camera, Room, Source, Wall
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import L_ROOM, l_room_project, only_level

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / DEFAULT_WEIGHTS_ROOT
BLENDER = find_blender(Settings.from_env().blender_bin)
pytestmark = [
    pytest.mark.blender,
    pytest.mark.slow,
    pytest.mark.skipif(BLENDER is None, reason="Blender absent"),
    pytest.mark.skipif(not is_installed(MODEL_ID, WEIGHTS), reason="poids de profondeur absents"),
]
HEIGHT = 1.4
FOCAL_35MM = 19  # ≈ 507 px pour 960 px de large : grand angle, comme la pièce en L synthétique
CAMERA = RenderCamera(
    position=Point3(x=1.0, y=0.5, z=HEIGHT),
    yaw=math.radians(45),
    pitch=math.radians(-12),
    focal_px=FOCAL_35MM / 36 * 960,
    width_px=960,
    height_px=720,
)


def _jpeg_with_focal(png: Path) -> bytes:
    from PIL import Image

    from tests.unit.test_ingestion import EXIF_IFD
    from tests.unit.test_ingestion import FOCAL_35MM as TAG

    exif = Image.Exif()
    exif.get_ifd(EXIF_IFD)[TAG] = FOCAL_35MM
    buffer = io.BytesIO()
    with Image.open(png) as img:
        img.convert("RGB").save(buffer, format="JPEG", exif=exif.tobytes(), quality=95)
    return buffer.getvalue()


def _room_polygon(level: object) -> Polygon:
    from openplan.schemas import Level

    assert isinstance(level, Level)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    walls = {w.id: w for w in level.elements.values() if isinstance(w, Wall)}
    return Polygon(
        [
            (
                level.vertices[walls[wid].start_vertex].position.x,
                level.vertices[walls[wid].start_vertex].position.y,
            )
            for wid in room.boundary_walls
        ]
    )


def test_depth_anything_on_the_l_room_render_and_plan_from_depth(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from scipy.stats import spearmanr

    from openplan.adapters.neural.depth_anything import DepthAnythingMetric

    room = l_room_project()
    rendered = run_blender(
        room,
        only_level(room).id,
        tmp_path / "render",
        blender_bin=str(BLENDER),
        camera=CAMERA,
        samples=32,
        denoise=True,
        gpu=True,
        depth=True,
    )
    names = {a.name for a in rendered.artifacts}
    assert "depth_z.npy" in names, rendered.warnings
    truth_depth = np.load(tmp_path / "render" / "depth_z.npy")
    assert truth_depth.shape == (720, 960)
    # Contrôle de la passe : au centre, le rayon (lacet 45°, tangage −12°) sort par le mur
    # y = 6 − 0,075 ou x = 6 − 0,075 ; profondeur z attendue ≈ 6,6 m.
    centre = float(truth_depth[360, 480])
    assert 5.5 < centre < 7.5, centre

    project = new_project("pièce en L")
    preview = next(a for a in rendered.artifacts if a.name == "preview.png")
    project, ref, _ = import_image(
        project, tmp_path, _jpeg_with_focal(preview.path), original_name="l.jpg"
    )
    estimator = DepthAnythingMetric(WEIGHTS)
    estimated = estimator.predict(load_image(tmp_path, ref)).depth
    valid = np.isfinite(truth_depth) & np.isfinite(estimated) & (truth_depth < 19.0)
    rho = float(spearmanr(estimated[valid].ravel()[::7], truth_depth[valid].ravel()[::7]).statistic)
    ratio = float(np.median(estimated[valid] / truth_depth[valid]))
    print(f"2.2 : Spearman {rho:.3f}, rapport d'échelle médian estimé/vrai {ratio:.2f}")
    assert rho >= 0.9

    level_id = only_level(project).id
    run = run_depthplan(
        project,
        tmp_path,
        level_id,
        ref.id,
        tmp_path / "runs",
        estimator=estimator,
        camera_height_m=HEIGHT,
    )
    level = only_level(run.project)
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    # Vérité (faces intérieures : murs centrés, demi-épaisseur 0,075) dans le repère du plan :
    # le repère du plan tourne d'un quart de tour près ; on essaie les quatre et on garde le
    # meilleur, l'ambiguïté d'orientation étant traitée par 2.5 (« tourner la vue »).
    interior = Polygon(L_ROOM).buffer(-0.075, join_style="mitre")
    proposed = _room_polygon(level)
    best = 0.0
    for k in range(4):
        angle = k * math.pi / 2
        pts = []
        for x, y in interior.exterior.coords:
            dx, dy = x - CAMERA.position.x, y - CAMERA.position.y
            rx = dx * math.cos(angle) - dy * math.sin(angle) + camera.position.x
            ry = dx * math.sin(angle) + dy * math.cos(angle) + camera.position.y
            pts.append((rx, ry))
        candidate = Polygon(pts)
        for mirrored in (candidate, Polygon([(2 * camera.position.x - x, y) for x, y in pts])):
            iou = proposed.intersection(mirrored).area / proposed.union(mirrored).area
            best = max(best, iou)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    seen = sum(w.source is Source.ESTIMATED for w in walls)
    print(
        f"2.3 : {len(walls)} murs ({seen} vus), hauteur de caméra trouvée "
        f"{run.camera_height_m:.2f} m, IoU {best:.2f}, {run.result.warnings}"
    )
    assert best >= 0.7, f"IoU {best:.2f}"
