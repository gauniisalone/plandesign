"""KAN-79 : profondeur calculée dans le navigateur (mode comptes, serveur sans GPU).

Le navigateur fait passer Depth Anything V2 Metric Indoor Small (ONNX fp16, le même modèle
que celui du serveur) sur la photo et envoie la carte réduite, dans la grille que garde déjà
KAN-66 : un pixel sur `step` de la photo, `step = ceil(grand côté / 640)`, en float16. Le
serveur la valide, la range à la même place que la profondeur qu'il calcule lui-même
(`images/<id>.depth.npy` et `.depth.json`), avec la focale et le point principal estimés sur la
photo (lignes de fuite, sans modèle). L'estimation du plan la relit ensuite par
`StoredDepthEstimator`, sans aucun modèle de profondeur sur le serveur.

Refus (`ClientDepthRefusedError`, 422 avec la raison) : taille de photo différente de celle
du projet, nombre de valeurs différent de la grille attendue, valeur non finie, valeur hors de
la plage du modèle (sa sortie est bornée à 20 m), carte uniforme.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.neural.web_depth import WEB_MODEL_ID
from openplan.adapters.protocols import AdapterUnavailableError, DepthMap, ImageArray
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.reference import DEPTH_MAX_SIDE, depth_paths
from openplan.schemas.numbers import decimal
from openplan.schemas.scene import Project

DEPTH_RANGE_M = (0.01, 20.0)
"""Sortie du modèle : sigmoïde × 20 m. Un peu de marge pour l'arrondi float16."""
RANGE_SLACK_M = 0.02
MIN_SPREAD_M = 0.01
"""Écart minimal entre la plus petite et la plus grande valeur : en dessous, carte uniforme."""
MAX_BYTES = DEPTH_MAX_SIDE * DEPTH_MAX_SIDE * 2
"""Au plus une grille de 640 × 640 en float16 (~0,8 Mo)."""


class ClientDepthRefusedError(ValueError):
    """Profondeur envoyée refusée ; le message est montré tel quel (422)."""


def grid_step(width: int, height: int) -> int:
    """Un pixel sur `step` est gardé (même règle que `reference.write_depth`)."""
    return max(1, math.ceil(max(width, height) / DEPTH_MAX_SIDE))


def grid_shape(width: int, height: int) -> tuple[int, int]:
    """(hauteur, largeur) de `depth[::step, ::step]` pour une photo `width` × `height`."""
    step = grid_step(width, height)
    return math.ceil(height / step), math.ceil(width / step)


def parse_client_depth(
    raw: bytes, project: Project, image_id: UUID, width: int, height: int
) -> NDArray[np.float16]:
    """Contrôle la carte envoyée (float16 petit-boutiste, ligne par ligne) ; refus sinon."""
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    if (width, height) != (ref.width_px, ref.height_px):
        msg = (
            f"taille de photo différente : {width} × {height} reçue, "
            f"{ref.width_px} × {ref.height_px} attendue"
        )
        raise ClientDepthRefusedError(msg)
    rows, cols = grid_shape(width, height)
    expected = rows * cols * 2
    if len(raw) != expected:
        msg = (
            f"profondeur de {len(raw)} octets : {cols} × {rows} valeurs float16 attendues "
            f"({expected} octets)"
        )
        raise ClientDepthRefusedError(msg)
    grid = np.frombuffer(raw, dtype="<f2").reshape(rows, cols)
    values = grid.astype(np.float32)
    if not np.isfinite(values).all():
        msg = "profondeur refusée : valeurs non finies"
        raise ClientDepthRefusedError(msg)
    low, high = float(values.min()), float(values.max())
    if low < DEPTH_RANGE_M[0] or high > DEPTH_RANGE_M[1] + RANGE_SLACK_M:
        msg = (
            "profondeur hors de la plage plausible (0,01 à 20 m) : "
            f"de {decimal(low)} à {decimal(high)} m"
        )
        raise ClientDepthRefusedError(msg)
    if high - low < MIN_SPREAD_M:
        msg = "profondeur uniforme : ce n'est pas la carte d'une pièce"
        raise ClientDepthRefusedError(msg)
    return np.ascontiguousarray(grid.astype(np.float16))


def store_client_depth(
    project: Project, project_dir: Path, image_id: UUID, grid: NDArray[np.float16]
) -> tuple[int, int]:
    """Range la carte (déjà dans la grille gardée) et la caméra estimée sur la photo, au
    format de `reference.write_depth`. Renvoie (largeur, hauteur) de la grille."""
    ref = project.images[image_id]
    image = load_image(project_dir, ref)
    camera = VanishingPointCameraEstimator().analyse(image).camera
    step = grid_step(ref.width_px, ref.height_px)
    npy, meta = depth_paths(project_dir, image_id)
    npy.parent.mkdir(parents=True, exist_ok=True)
    tmp = npy.with_name(npy.name + ".tmp")
    with tmp.open("wb") as f:
        np.save(f, grid)
    tmp.replace(npy)
    rows, cols = grid.shape
    meta_tmp = meta.with_name(meta.name + ".tmp")
    meta_tmp.write_text(
        json.dumps(
            {
                "focal_px": camera.focal_px / step,
                "principal": [
                    camera.principal_point[0] / step,
                    camera.principal_point[1] / step,
                ],
                "scale": [1.0 / step, 1.0 / step],
                "image_size": [ref.width_px, ref.height_px],
                "depth_size": [cols, rows],
                "model_id": WEB_MODEL_ID,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta_tmp.replace(meta)
    return cols, rows


def has_stored_depth(project_dir: Path, image_id: UUID) -> bool:
    return all(path.is_file() for path in depth_paths(project_dir, image_id))


def _stored(project_dir: Path, image_id: UUID) -> tuple[NDArray[np.float32], tuple[float, float]]:
    npy, meta = depth_paths(project_dir, image_id)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        depth = np.load(npy, allow_pickle=False).astype(np.float32)
        scale = (float(data["scale"][0]), float(data["scale"][1]))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        msg = "profondeur de la photo absente : relancez l'analyse de la photo"
        raise AdapterUnavailableError(msg) from exc
    return depth, scale


class StoredDepthEstimator:
    """Implémente DepthEstimator sans modèle : la profondeur gardée pour la photo, ramenée à la
    taille de la photo (interpolation bilinéaire, chaque pixel lu à sa place dans la grille)."""

    model_id = WEB_MODEL_ID
    from_store = True
    """La chaîne automatique ne réécrit pas une profondeur qu'elle vient de relire."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def predict(self, image: ImageArray) -> DepthMap:
        import cv2

        grid, (sx, sy) = _stored(self.project_dir, image.image_id)
        xs = (np.arange(image.width, dtype=np.float32) * np.float32(sx))[None, :]
        ys = (np.arange(image.height, dtype=np.float32) * np.float32(sy))[:, None]
        map_x = np.ascontiguousarray(np.broadcast_to(xs, (image.height, image.width)))
        map_y = np.ascontiguousarray(np.broadcast_to(ys, (image.height, image.width)))
        depth = cv2.remap(
            grid, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        return DepthMap(
            depth=np.asarray(depth, dtype=np.float32), metric=True, model_id=self.model_id
        )
