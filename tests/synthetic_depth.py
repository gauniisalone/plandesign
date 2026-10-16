"""Cartes de profondeur synthétiques exactes : intérieur convexe vu par une caméra connue.

Une pièce convexe est l'intersection de demi-espaces n_out·p ≤ c. Depuis une caméra à
l'intérieur, la profondeur d'un pixel est la plus petite distance de sortie parmi les faces que
le rayon traverse vers l'extérieur. Le rayon est pris avec une composante avant égale à 1, si
bien que la distance t est directement la profondeur « z » (distance au plan image), celle que
renvoient les modèles de profondeur.

Conventions de caméra : celles de `RenderCamera` et de la fixture (lacet depuis l'axe X,
antihoraire vu du dessus ; tangage positif vers le haut ; image x à droite, y vers le bas).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Float = NDArray[np.float64]


@dataclass(frozen=True)
class Face:
    outward: tuple[float, float, float]
    offset: float
    """Intérieur : outward · p ≤ offset (outward non nécessairement unitaire)."""


@dataclass(frozen=True)
class SyntheticCamera:
    position: tuple[float, float, float]
    yaw: float
    pitch: float
    focal_px: float
    width_px: int
    height_px: int

    @property
    def principal_point(self) -> tuple[float, float]:
        return (self.width_px / 2.0, self.height_px / 2.0)

    def basis(self) -> tuple[Float, Float, Float]:
        yaw, p = self.yaw, self.pitch
        forward = np.array([math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p)])
        right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
        up = np.array([-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p)])
        return right, up, forward


def attic_faces(
    *,
    length: float = 6.0,
    width: float = 5.0,
    ridge_height: float = 3.2,
    eave_height: float = 1.0,
) -> list[Face]:
    """Combles symétriques : faîtage le long de X à y = 0, pignons en x = 0 et x = length,
    murs latéraux (jambettes) en y = ±width/2 jusqu'à eave_height, deux pentes jusqu'au faîtage."""
    half = width / 2.0
    slope = (ridge_height - eave_height) / half  # pente : z = ridge − slope·|y|
    return [
        Face((0.0, 0.0, -1.0), 0.0),
        Face((-1.0, 0.0, 0.0), 0.0),
        Face((1.0, 0.0, 0.0), length),
        Face((0.0, 1.0, 0.0), half),
        Face((0.0, -1.0, 0.0), half),
        Face((0.0, slope, 1.0), ridge_height),
        Face((0.0, -slope, 1.0), ridge_height),
    ]


def box_faces(width: float, depth: float, height: float) -> list[Face]:
    """Pièce rectangulaire [0, width] × [0, depth] × [0, height]."""
    return [
        Face((0.0, 0.0, -1.0), 0.0),
        Face((0.0, 0.0, 1.0), height),
        Face((-1.0, 0.0, 0.0), 0.0),
        Face((1.0, 0.0, 0.0), width),
        Face((0.0, -1.0, 0.0), 0.0),
        Face((0.0, 1.0, 0.0), depth),
    ]


def _exit_depth(faces: list[Face], origins: Float, directions: Float) -> Float:
    """Distance de sortie d'une cellule convexe pour des rayons (origines et directions par
    pixel) ; composante avant = 1 donc c'est une profondeur."""
    depth = np.full(directions.shape[:2], np.inf)
    for face in faces:
        n = np.asarray(face.outward, dtype=np.float64)
        denominator = directions @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (face.offset - origins @ n) / denominator
        t = np.where(denominator > 1e-12, t, np.inf)
        depth = np.minimum(depth, t)
    return depth


def _inside(faces: list[Face], points: Float, eps: float = 1e-6) -> NDArray[np.bool_]:
    ok = np.ones(points.shape[:2], dtype=bool)
    for face in faces:
        n = np.asarray(face.outward, dtype=np.float64)
        ok &= points @ n <= face.offset + eps
    return ok


def render_depth_union(
    cells: list[list[Face]],
    camera: SyntheticCamera,
    *,
    noise: float = 0.0,
    seed: int = 0,
) -> NDArray[np.float32]:
    """Profondeur d'une pièce non convexe = union de cellules convexes (pièce en L) : le rayon
    sort de sa cellule et continue s'il entre dans une autre, jusqu'à quatre passages."""
    right, up, forward = camera.basis()
    cx, cy = camera.principal_point
    vs, us = np.mgrid[0 : camera.height_px, 0 : camera.width_px]
    dx = (us - cx) / camera.focal_px
    dy = (vs - cy) / camera.focal_px
    directions = dx[..., None] * right - dy[..., None] * up + forward
    origin = np.asarray(camera.position, dtype=np.float64)
    origins = np.broadcast_to(origin, directions.shape).copy()
    shape = directions.shape[:2]
    total = np.zeros(shape)
    current = np.full(shape, -1)
    for k, cell in enumerate(cells):
        current = np.where(_inside(cell, origins) & (current < 0), k, current)
    if (current < 0).any():
        msg = "la caméra doit être dans une des cellules"
        raise ValueError(msg)
    active = np.ones(shape, dtype=bool)
    eps = 1e-4
    for _ in range(4):
        step = np.full(shape, np.inf)
        for k, cell in enumerate(cells):
            mask = active & (current == k)
            if mask.any():
                step = np.where(mask, _exit_depth(cell, origins, directions), step)
        hit = active & np.isfinite(step)
        total = np.where(hit, total + step, total)
        advance = np.where(hit, step + eps, 0.0)
        exits = origins + advance[..., None] * directions
        next_cell = np.full(shape, -1)
        for k, cell in enumerate(cells):
            enters = hit & (current != k) & _inside(cell, exits) & (next_cell < 0)
            next_cell = np.where(enters, k, next_cell)
        goes_on = next_cell >= 0
        if not goes_on.any():
            break
        origins = np.where(goes_on[..., None], exits, origins)
        total = np.where(goes_on, total + eps, total)
        current = np.where(goes_on, next_cell, current)
        active = goes_on
    depth = total
    rng = np.random.default_rng(seed)
    if noise > 0:
        depth = depth * (1.0 + noise * rng.standard_normal(depth.shape))
    return depth.astype(np.float32)


def render_depth(
    faces: list[Face],
    camera: SyntheticCamera,
    *,
    noise: float = 0.0,
    outlier_fraction: float = 0.0,
    seed: int = 0,
) -> NDArray[np.float32]:
    """Profondeur z (H, W) exacte, puis bruit relatif gaussien et points aberrants optionnels."""
    right, up, forward = camera.basis()
    cx, cy = camera.principal_point
    vs, us = np.mgrid[0 : camera.height_px, 0 : camera.width_px]
    dx = (us - cx) / camera.focal_px
    dy = (vs - cy) / camera.focal_px
    directions = dx[..., None] * right - dy[..., None] * up + forward  # composante avant = 1
    origin = np.asarray(camera.position, dtype=np.float64)
    depth = _exit_depth(faces, np.broadcast_to(origin, directions.shape), directions)
    rng = np.random.default_rng(seed)
    if noise > 0:
        depth = depth * (1.0 + noise * rng.standard_normal(depth.shape))
    if outlier_fraction > 0:
        mask = rng.random(depth.shape) < outlier_fraction
        depth = np.where(mask, rng.uniform(0.5, 10.0, depth.shape), depth)
    return depth.astype(np.float32)
