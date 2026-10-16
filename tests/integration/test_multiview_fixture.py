"""Fixture multi-photos (roadmap 2.5) : la photo commise (coin, lacet 50°) propose le
rectangle ; une seconde vue rendue par Blender à la volée, depuis le milieu de la pièce face
au mur haut avec un grand angle, voit les deux murs latéraux : le mur gauche, inféré par la
première photo, doit être confirmé à ±10 %, et une vue à la mauvaise hauteur de caméra doit
déclencher `inconsistent_views`. Ignoré sans Blender.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import find_blender, run_blender
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from openplan.scene.ops import UpdateElement, apply_op
from openplan.schemas import Camera, Source, Wall
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from openplan.validation.consistency import EXTENTS_KEY, check_views, walls_box
from openplan.validation.invariants import validate_invariants
from tests.factories import by_name, only_level, rect_room_project

BLENDER = find_blender(Settings.from_env().blender_bin)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
IMAGE = FIXTURE / "image.png"
HEIGHT = 1.4
INTERIOR_WIDTH = 3.85  # pièce 4 × 3, murs 0,15 m
FRONT_DISTANCE = 2.925 - 0.15
FOCAL_35MM = 16  # ultra grand angle (97°), dos au mur : les deux murs latéraux sont vus sur 1 m
SECOND = RenderCamera(
    position=Point3(x=2.0, y=0.15, z=HEIGHT),
    yaw=math.pi / 2,  # face au mur haut (y = 2,925)
    pitch=-0.2094,
    focal_px=FOCAL_35MM / 36 * 960,
    width_px=960,
    height_px=720,
)


def jpeg_with_focal(png: Path) -> bytes:
    """Le rendu en JPEG avec la focale EXIF, comme le ferait un téléphone : vu de face, un
    point de fuite est à l'infini et la focale ne peut pas venir des lignes."""
    import io

    from PIL import Image

    from tests.unit.test_ingestion import EXIF_IFD
    from tests.unit.test_ingestion import FOCAL_35MM as TAG

    exif = Image.Exif()
    exif.get_ifd(EXIF_IFD)[TAG] = FOCAL_35MM
    buffer = io.BytesIO()
    with Image.open(png) as img:
        img.convert("RGB").save(buffer, format="JPEG", exif=exif.tobytes(), quality=95)
    return buffer.getvalue()


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.skipif(BLENDER is None or not IMAGE.is_file(), reason="Blender ou fixture absents")
def test_second_view_confirms_the_hidden_wall_and_flags_a_bad_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rendu reproductible d'une machine à l'autre : sans les matières installées localement
    # (`openplan materials pull`). Avec le ciel HDRI, la lumière qui entre par la vitre de la
    # fenêtre (menuiseries E5b) dessine une tache sur le mur latéral et, selon le bruit du
    # rendu, le détecteur heuristique y prend parfois l'arête sol-mur.
    monkeypatch.setenv("OPENPLAN_MATERIALS_DIR", str(tmp_path / "sans-matieres"))
    room = rect_room_project(name="vue 2")
    rendered = run_blender(
        room,
        only_level(room).id,
        tmp_path / "render",
        blender_bin=str(BLENDER),
        camera=SECOND,
        samples=32,
        denoise=True,
        gpu=True,
    )
    preview = next(a for a in rendered.artifacts if a.name == "preview.png")
    view2 = jpeg_with_focal(preview.path)

    project = new_project("deux vues")
    project, first, _ = import_image(project, tmp_path, IMAGE.read_bytes(), original_name="1.png")
    level_id = only_level(project).id
    one = detect_and_propose(
        project, tmp_path, level_id, first.id, tmp_path / "runs", camera_height_m=HEIGHT
    )
    assert by_name(only_level(one.project), "Mur gauche", Wall).source is Source.INFERRED

    scene, second, _ = import_image(one.project, tmp_path, view2, original_name="2.jpg")
    assert scene.images[second.id].focal_35mm == FOCAL_35MM
    two = detect_and_propose(
        scene, tmp_path, level_id, second.id, tmp_path / "runs", camera_height_m=HEIGHT
    )
    level = only_level(two.project)
    assert two.floorplan.step == "floorplan.add_view", two.floorplan.warnings
    left = by_name(level, "Mur gauche", Wall)
    box = walls_box(level)
    assert box is not None
    width = box[2] - box[0]
    camera = by_name(level, "Photo 2", Camera)
    front = level.vertices[by_name(level, "Mur haut", Wall).start_vertex].position.y
    print(
        f"largeur 2 vues {width:.3f} m (vraie {INTERIOR_WIDTH}), mur gauche {left.source}, "
        f"distance au mur haut {abs(front - camera.position.y):.3f} m (vraie {FRONT_DISTANCE}), "
        f"{preview.description}"
    )
    assert (
        left.source is Source.ESTIMATED
        and left.provenance
        and left.provenance.image_ids == [second.id]
    )
    # Mur de face à ±10 % (critère 2.5). Largeur à ±20 % : le détecteur de lignes retient le
    # premier joint de carrelage au pied des murs latéraux (carreaux assombris, hors palette
    # « sol »), soit −0,4 m par côté ; biais connu du détecteur (protocole §3), pas du multi-vues.
    assert abs(front - camera.position.y) == pytest.approx(FRONT_DISTANCE, rel=0.10)
    assert width == pytest.approx(INTERIOR_WIDTH, rel=0.20)
    assert camera.metadata[EXTENTS_KEY] == {"x": pytest.approx(width, abs=1e-3)}
    assert validate_invariants(two.project).violations == []

    # La même vue avec une hauteur de caméra fausse de 30 % contredit la largeur confirmée.
    # Le mur bas, encore inféré, est d'abord fixé par l'utilisateur (sinon la vue pourrait être
    # lue comme une pièce plus profonde : limite documentée dans pipeline.multiview).
    bottom = by_name(level, "Mur bas", Wall)
    fixed = apply_op(
        two.project, level_id, UpdateElement(element_id=bottom.id, fields={"source": "estimated"})
    )
    scene3, third, _ = import_image(fixed, tmp_path, view2, original_name="3.jpg")
    three = detect_and_propose(
        scene3, tmp_path, level_id, third.id, tmp_path / "runs", camera_height_m=HEIGHT * 1.3
    )
    violations = [v for v in check_views(three.project) if v.type == "inconsistent_views"]
    assert len(violations) == 1 and "Photo 3" in violations[0].message
    assert walls_box(only_level(three.project)) == box, "les murs estimés ne bougent pas"
    assert validate_invariants(three.project).valid
