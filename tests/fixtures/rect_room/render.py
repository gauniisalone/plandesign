"""Produit la fixture « pièce rectangulaire » : scène, caméra connue, rendu Blender.

    uv run python -m tests.fixtures.rect_room.render

Écrit scene.json (projet), camera.json (vérité terrain RenderCamera) et image.png dans ce
dossier. Nécessite Blender (voir docs/contributing/setup.md). Rendu Cycles CPU, 64
échantillons avec débruitage : environ une minute.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import run_blender
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import only_level, rect_room_project

HERE = Path(__file__).resolve().parent
CAMERA = RenderCamera(
    position=Point3(x=0.7, y=0.6, z=1.4),
    yaw=0.8727,  # 50° : vers le coin opposé
    pitch=-0.2094,  # −12° : le sol occupe le bas de l'image, comme sur une photo de pièce
    focal_px=900.0,  # ≠ 0,8 × largeur : la focale doit venir des points de fuite, pas du défaut
    width_px=1280,
    height_px=960,
)


def main() -> None:
    project = rect_room_project(name="Fixture pièce rectangulaire")
    level = only_level(project)
    (HERE / "scene.json").write_text(project.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (HERE / "camera.json").write_text(CAMERA.model_dump_json(indent=2) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_blender(
            project,
            level.id,
            Path(tmp),
            blender_bin=Settings.from_env().blender_bin,
            camera=CAMERA,
            samples=64,
            denoise=True,
        )
        preview = next(a for a in result.artifacts if a.name == "preview.png")
        shutil.copyfile(preview.path, HERE / "image.png")
        report = json.loads((Path(tmp) / "blender_report.json").read_text(encoding="utf-8"))
    print(
        f"image.png rendue en {report['duration_s']:.1f} s par Blender {report['blender_version']}"
    )


if __name__ == "__main__":
    main()
