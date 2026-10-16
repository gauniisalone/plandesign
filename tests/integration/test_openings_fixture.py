"""OWLv2 sur la fixture : la fenêtre de l'image commise, la porte d'un rendu à la volée.

Critère 2.4a : l'ouverture est trouvée là où elle est (IoU ≥ 0,5 avec sa boîte projetée).
La largeur en mètres relève de 2.4b. Ignoré sans les poids (`openplan models pull
owlv2_base@1.0`) ; la partie porte est ignorée sans Blender.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.neural.openings import MODEL_ID, box_iou
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, is_installed
from openplan.adapters.protocols import ImageArray
from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import find_blender, run_blender
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import only_level, rect_room_project

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / DEFAULT_WEIGHTS_ROOT
BLENDER = find_blender(Settings.from_env().blender_bin)
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not is_installed(MODEL_ID, WEIGHTS), reason="poids OWLv2 absents"),
]
# Fixture rect_room : fenêtre sur le mur W3 (y = 3, parcouru de (4,3) vers (0,3), face
# intérieure 2,925), offset 1,2 et largeur 1,2 donc x de 2,8 à 1,6, de 0,9 à 2,15 m de haut ;
# porte sur le mur y = 0 (face 0,075), x de 0,5 à 1,33, de 0 à 2,04 m.
WINDOW = [(1.6, 2.925, 0.9), (2.8, 2.925, 0.9), (2.8, 2.925, 2.15), (1.6, 2.925, 2.15)]
DOOR = [(0.5, 0.075, 0.0), (1.33, 0.075, 0.0), (1.33, 0.075, 2.04), (0.5, 0.075, 2.04)]


def project(
    cam: RenderCamera, points: list[tuple[float, float, float]]
) -> tuple[int, int, int, int]:
    """Boîte image (x0, y0, x1, y1) des points 3D, conventions de `RenderCamera`."""
    yaw, p = cam.yaw, cam.pitch
    forward = np.array([math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p)])
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    up = np.array([-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p)])
    origin = np.array([cam.position.x, cam.position.y, cam.position.z])
    us, vs = [], []
    for point in points:
        d = np.asarray(point) - origin
        z = float(d @ forward)
        us.append(cam.width_px / 2 + cam.focal_px * float(d @ right) / z)
        vs.append(cam.height_px / 2 - cam.focal_px * float(d @ up) / z)
    return (int(min(us)), int(min(vs)), int(max(us)), int(max(vs)))


def _detect(png: Path) -> list:
    pytest.importorskip("torch")
    from PIL import Image

    from openplan.adapters.neural.openings import OwlV2OpeningDetector

    with Image.open(png) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return OwlV2OpeningDetector(WEIGHTS).detect(ImageArray(image_id=uuid4(), pixels=pixels))


@pytest.mark.skipif(not (FIXTURE / "image.png").is_file(), reason="fixture absente")
def test_committed_fixture_window_is_a_black_hole() -> None:
    """Sur l'image commise (fond de monde presque noir), la fenêtre n'est qu'un trou noir :
    OWLv2 y voit une ouverture de porte. Constat consigné, pas un critère : la fenêtre d'un
    rendu au fond ciel est jugée par le test suivant, et sur photo réelle par 2.4b."""
    cam = RenderCamera.model_validate(json.loads((FIXTURE / "camera.json").read_text()))
    expected = project(cam, WINDOW)
    elements = _detect(FIXTURE / "image.png")
    print("fixture :", [(e.category, e.bbox_px, round(e.score, 2), e.label) for e in elements])
    assert elements, "rien trouvé sur la fixture"
    best = max(box_iou(e.bbox_px, expected) for e in elements)
    print(f"fenêtre attendue {expected}, meilleur IoU toutes catégories {best:.2f}")
    assert best >= 0.5, "le trou de la fenêtre doit au moins être localisé"


@pytest.mark.blender
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
def test_window_is_found_on_a_render_facing_it(tmp_path: Path) -> None:
    room = rect_room_project()
    cam = RenderCamera(
        position=Point3(x=1.8, y=0.7, z=1.4),
        yaw=math.pi / 2,  # face au mur y = 3 et à sa fenêtre
        pitch=0.0,
        focal_px=560.0,
        width_px=960,
        height_px=720,
    )
    rendered = run_blender(
        room,
        only_level(room).id,
        tmp_path,
        blender_bin=str(BLENDER),
        camera=cam,
        samples=32,
        denoise=True,
        gpu=True,
    )
    preview = next(a for a in rendered.artifacts if a.name == "preview.png")
    expected = project(cam, WINDOW)
    elements = _detect(preview.path)
    print("fenêtre :", [(e.category, e.bbox_px, round(e.score, 2), e.label) for e in elements])
    windows = [e for e in elements if e.category == "window"]
    assert windows, "aucune fenêtre trouvée"
    best = max(box_iou(w.bbox_px, expected) for w in windows)
    print(f"fenêtre attendue {expected}, meilleur IoU {best:.2f}")
    assert best >= 0.5


@pytest.mark.blender
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
def test_window_width_is_recovered_from_a_render(tmp_path: Path) -> None:
    """Critère 2.4b : boîte OWLv2 → projection par la caméra de la photo → largeur de la
    fenêtre à ±15 % (offset et appui à ±15 cm), ouverture `observed`."""
    from openplan.pipeline.ingestion import import_image
    from openplan.pipeline.openings import run_openings
    from openplan.schemas import Camera, Opening, Source
    from tests.factories import by_name

    room = rect_room_project()
    cam = RenderCamera(
        position=Point3(x=1.6, y=0.6, z=1.4),
        yaw=math.radians(80),  # face au mur y = 3, léger biais
        pitch=math.radians(-6),
        focal_px=620.0,
        width_px=960,
        height_px=720,
    )
    rendered = run_blender(
        room,
        only_level(room).id,
        tmp_path,
        blender_bin=str(BLENDER),
        camera=cam,
        samples=32,
        denoise=True,
        gpu=True,
    )
    preview = next(a for a in rendered.artifacts if a.name == "preview.png")
    # La scène de projection : la pièce sans sa fenêtre (sinon « déjà présente ») et la caméra
    # de la photo aux vraies valeurs (2.4b juge la projection, pas la pose).
    level = only_level(room)
    scene, ref, _ = import_image(room, tmp_path, preview.path.read_bytes(), original_name="w.png")
    camera = Camera(
        name="Photo",
        position=cam.position,
        yaw=cam.yaw,
        pitch=cam.pitch,
        focal_px=cam.focal_px,
        image_id=ref.id,
        source=Source.ESTIMATED,
        confidence=0.6,
    )
    elements = {
        k: v for k, v in level.elements.items() if k != by_name(level, "window", Opening).id
    }
    elements[camera.id] = camera
    scene = scene.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    pytest.importorskip("torch")
    from openplan.adapters.neural.openings import OwlV2OpeningDetector

    run = run_openings(
        scene, tmp_path, level.id, ref.id, tmp_path / "runs", detector=OwlV2OpeningDetector(WEIGHTS)
    )
    for o in run.observations:
        print(o.model_dump_json())
    windows = [o for o in run.observations if o.filtered is None and o.kind == "window"]
    assert windows, "aucune fenêtre gardée"
    best = max(windows, key=lambda o: o.score)
    assert best.host_wall_name == "W3"
    assert best.width_m == pytest.approx(1.2, rel=0.15), best
    assert best.offset_m == pytest.approx(1.2, abs=0.15), best
    assert best.sill_m == pytest.approx(0.9, abs=0.15), best
    added = only_level(run.project).elements[best.element_id]  # type: ignore[index]
    assert isinstance(added, Opening) and added.source is Source.OBSERVED


@pytest.mark.blender
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
def test_door_is_found_on_a_render_facing_it(tmp_path: Path) -> None:
    room = rect_room_project()
    cam = RenderCamera(
        position=Point3(x=2.0, y=2.4, z=1.4),
        yaw=math.atan2(0.075 - 2.4, 0.915 - 2.0),  # regarde le milieu de la porte
        pitch=math.radians(-5),
        focal_px=560.0,
        width_px=960,
        height_px=720,
    )
    rendered = run_blender(
        room,
        only_level(room).id,
        tmp_path,
        blender_bin=str(BLENDER),
        camera=cam,
        samples=32,
        denoise=True,
        gpu=True,
    )
    preview = next(a for a in rendered.artifacts if a.name == "preview.png")
    expected = project(cam, DOOR)
    elements = _detect(preview.path)
    print("porte :", [(e.category, e.bbox_px, round(e.score, 2), e.label) for e in elements])
    doors = [e for e in elements if e.category == "door"]
    assert doors, "aucune porte trouvée"
    best = max(box_iou(d.bbox_px, expected) for d in doors)
    print(f"porte attendue {expected}, meilleur IoU {best:.2f}")
    assert best >= 0.5
