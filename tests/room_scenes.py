"""KAN-95 (P2) : pièces droites synthétiques vues par plusieurs photos (profondeur exacte, aucune
photo réelle) : rectangle, pièce en L, pièce à mur oblique.

Même principe que `tests/attic_scenes.py` : image de bruit importée (focale de repli
0,8 × grand côté, celle des caméras), profondeur gardée comme après une estimation (KAN-66).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np

from openplan.pipeline.ingestion import import_image
from openplan.pipeline.reference import write_depth
from openplan.scene.factory import new_project
from openplan.schemas.scene import Project
from tests.attic_scenes import HEIGHT_PX, WIDTH_PX, _noise_png, camera
from tests.synthetic_depth import Face, SyntheticCamera, box_faces, render_depth, render_depth_union

HEIGHT = 2.5


@dataclass(frozen=True)
class Room:
    """Pièce : cellules convexes (une pour un rectangle, deux pour un L) et sa vérité."""

    cells: tuple[tuple[Face, ...], ...]
    length: float
    width: float
    area: float

    def depth(self, cam: SyntheticCamera, noise: float = 0.0, seed: int = 0) -> np.ndarray:
        if len(self.cells) == 1:
            return render_depth(list(self.cells[0]), cam, noise=noise, seed=seed)
        return render_depth_union([list(c) for c in self.cells], cam, noise=noise, seed=seed)


def rectangle(x: float = 4.0, y: float = 3.5) -> Room:
    return Room((tuple(box_faces(x, y, HEIGHT)),), max(x, y), min(x, y), x * y)


def l_room() -> Room:
    """L : 5 × 3 m plus 2,5 × 3 m au-dessus de la partie gauche (emprise 5 × 6 m)."""
    a = tuple(box_faces(5.0, 3.0, HEIGHT))
    b = (
        Face((0.0, 0.0, -1.0), 0.0),
        Face((0.0, 0.0, 1.0), HEIGHT),
        Face((-1.0, 0.0, 0.0), 0.0),
        Face((1.0, 0.0, 0.0), 2.5),
        Face((0.0, -1.0, 0.0), -2.9),
        Face((0.0, 1.0, 0.0), 6.0),
    )
    return Room((a, b), 6.0, 5.0, 5.0 * 3.0 + 2.5 * 3.0)


def oblique(tilt_deg: float = 15.0) -> Room:
    """Rectangle 4,5 × 3,5 m dont le mur droit (x = 4,5 au pied) est de biais."""
    t = math.radians(tilt_deg)
    faces = [f for f in box_faces(4.5, 3.5, HEIGHT) if f.outward != (1.0, 0.0, 0.0)]
    # Mur droit passant par (4,5 ; 0), tourné de t : normale (cos t, sin t).
    faces.append(Face((math.cos(t), math.sin(t), 0.0), 4.5 * math.cos(t)))
    top = 4.5 - 3.5 * math.tan(t)
    area = 3.5 * (4.5 + top) / 2.0
    return Room((tuple(faces),), 4.5, 3.5, area)


def project_with_room_photos(
    project_dir: Path,
    room: Room,
    cams: list[tuple[float, float, float, float, float]],
    *,
    noise: float = 0.0,
    depth_scales: list[float] | None = None,
) -> tuple[Project, UUID, list[UUID]]:
    project = new_project("Pièce synthétique")
    ids: list[UUID] = []
    for i, spec in enumerate(cams):
        cam = camera(*spec)
        project, ref, _ = import_image(
            project, project_dir, _noise_png(50 + i), original_name=f"r{i}.png"
        )
        depth = room.depth(cam, noise=noise, seed=i) * np.float32(
            depth_scales[i] if depth_scales else 1.0
        )
        write_depth(
            project_dir, ref.id, depth, cam.focal_px, cam.principal_point, (WIDTH_PX, HEIGHT_PX)
        )
        ids.append(ref.id)
    return project, next(iter(project.levels)), ids
