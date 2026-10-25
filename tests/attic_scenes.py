"""KAN-95 : combles synthétiques vus par plusieurs photos (profondeur exacte, aucune photo réelle).

Chaque photo est une image de bruit (aucune ligne de fuite : focale de repli 0,8 × grand côté,
celle des caméras synthétiques) importée dans un projet, avec sa profondeur gardée comme après
une estimation (KAN-66). Repère : faîtage le long de X à y = 0, sol en z = 0, pignons en x = 0
et x = longueur.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np

from openplan.pipeline.ingestion import import_image
from openplan.pipeline.reference import write_depth
from openplan.scene.factory import new_project
from openplan.schemas.scene import Project
from tests.synthetic_depth import SyntheticCamera, attic_faces, render_depth

WIDTH_PX = 320
HEIGHT_PX = 240
FOCAL_PX = 0.8 * WIDTH_PX
"""Focale de repli de l'app pour une image sans EXIF ni lignes de fuite."""


@dataclass(frozen=True)
class Attic:
    length: float = 8.0
    width: float = 6.0
    ridge: float = 2.5
    eave: float = 0.0

    @property
    def pitch_deg(self) -> float:
        return math.degrees(math.atan((self.ridge - self.eave) / (self.width / 2.0)))


def camera(x: float, y: float, z: float, yaw_deg: float, pitch_deg: float) -> SyntheticCamera:
    return SyntheticCamera(
        position=(x, y, z),
        yaw=math.radians(yaw_deg),
        pitch=math.radians(pitch_deg),
        focal_px=FOCAL_PX,
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
    )


AXIAL_A = (1.5, 0.4, 1.5, 0.0, 10.0)
"""Dans l'axe, vers le pignon x = longueur."""
AXIAL_B = (6.5, -0.3, 1.5, 180.0, 10.0)
"""Dans l'axe, vers le pignon x = 0."""
OBLIQUE = (4.0, -1.5, 1.5, 70.0, 0.0)
"""De biais, vers le versant y > 0."""


def _noise_png(seed: int) -> bytes:
    from PIL import Image

    rng = np.random.default_rng(seed)
    pixels = rng.integers(90, 160, size=(HEIGHT_PX, WIDTH_PX, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def add_photo(
    project: Project,
    project_dir: Path,
    attic: Attic,
    cam: SyntheticCamera,
    *,
    seed: int,
    depth_scale: float = 1.0,
    noise: float = 0.0,
) -> tuple[Project, UUID]:
    """Une photo de plus : image de bruit importée, profondeur exacte gardée (`depth_scale` :
    erreur d'échelle du modèle de profondeur, `noise` : bruit relatif)."""
    project, ref, _ = import_image(
        project, project_dir, _noise_png(seed), original_name=f"p{seed}.png"
    )
    faces = attic_faces(
        length=attic.length, width=attic.width, ridge_height=attic.ridge, eave_height=attic.eave
    )
    depth = render_depth(faces, cam, noise=noise, seed=seed) * np.float32(depth_scale)
    write_depth(
        project_dir,
        ref.id,
        depth,
        cam.focal_px,
        cam.principal_point,
        (WIDTH_PX, HEIGHT_PX),
        model_id="synthetic",
    )
    return project, ref.id


def project_with_photos(
    project_dir: Path,
    attic: Attic,
    cams: list[SyntheticCamera],
    *,
    depth_scales: list[float] | None = None,
    attics: list[Attic] | None = None,
) -> tuple[Project, UUID, list[UUID]]:
    project = new_project("Combles synthétiques")
    ids: list[UUID] = []
    for i, cam in enumerate(cams):
        scale = depth_scales[i] if depth_scales else 1.0
        scene = attics[i] if attics else attic
        project, image_id = add_photo(
            project, project_dir, scene, cam, seed=i + 1, depth_scale=scale
        )
        ids.append(image_id)
    return project, next(iter(project.levels)), ids


def pixel_of(cam: SyntheticCamera, point: tuple[float, float, float]) -> tuple[float, float]:
    """Pixel d'un point de la scène vu par la caméra synthétique."""
    right, up, forward = cam.basis()
    d = np.asarray(point, dtype=np.float64) - np.asarray(cam.position, dtype=np.float64)
    z = float(d @ forward)
    cx, cy = cam.principal_point
    return cx + cam.focal_px * float(d @ right) / z, cy - cam.focal_px * float(d @ up) / z
