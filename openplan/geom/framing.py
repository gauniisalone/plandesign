"""Cadrage mesuré sans rendu : ce qu'une caméra voit de la pièce, par la géométrie seule.

On lance un rayon par case d'une grille posée sur l'image (sténopé, même convention que le
rendu Blender : lacet en plan, tangage positif vers le haut, focale en pixels rapportée à la
largeur de l'image) et l'on note la première surface touchée : le sol, les pentes et le
plafond (« toit »), les murs, ou un meuble (boîte englobante). On en tire la part de l'image
occupée par chacune, et la hauteur de l'horizon (fraction depuis le haut de l'image).

Le volume de la pièce est le prisme de son emprise libre (murs retirés), coupé en haut par
chaque pente (`height_under_slope`) et par la hauteur du niveau. Les tapis comptent comme du
sol. Aucune dépendance au GPU ni à Blender : c'est la mesure qui règle les vues automatiques
sous les combles, et que les tests vérifient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry

from openplan.schemas.derived import DerivedRoof

Surface = Literal["floor", "roof", "wall", "furniture"]
FLOOR, ROOF, WALL, FURNITURE = 0, 1, 2, 3
LABELS: tuple[Surface, ...] = ("floor", "roof", "wall", "furniture")

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Box:
    """Meuble vu comme une boîte : centre en plan, lacet, demi-largeur (x local),
    demi-profondeur (y local), bas et haut."""

    cx: float
    cy: float
    yaw: float
    half_w: float
    half_d: float
    z0: float
    z1: float

    def corners(self) -> list[tuple[float, float, float]]:
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        out: list[tuple[float, float, float]] = []
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            px = self.cx + c * dx * self.half_w - s * dy * self.half_d
            py = self.cy + s * dx * self.half_w + c * dy * self.half_d
            out += [(px, py, self.z0), (px, py, self.z1)]
        return out


@dataclass(frozen=True)
class Pose:
    """Caméra : position, lacet, tangage (radians, positif vers le haut), focale (pixels pour
    `width` pixels de large) et taille de l'image."""

    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    focal_px: float
    width: int = 1280
    height: int = 960

    def horizon(self) -> float:
        """Hauteur de l'horizon dans l'image, en fraction depuis le haut (0,5 caméra de
        niveau ; plus petit quand on regarde vers le bas)."""
        return 0.5 + self.focal_px * math.tan(self.pitch) / self.height

    def with_pitch(self, pitch: float) -> Pose:
        return Pose(self.x, self.y, self.z, self.yaw, pitch, self.focal_px, self.width, self.height)

    def _basis(self) -> tuple[FloatArray, FloatArray, FloatArray]:
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        forward = np.array([cp * cy, cp * sy, sp])
        right = np.array([sy, -cy, 0.0])
        up = np.array([-sp * cy, -sp * sy, cp])
        return forward, right, up

    def project(self, point: tuple[float, float, float]) -> tuple[float, float] | None:
        """Position (u, v) dans l'image, en fraction de la largeur et de la hauteur depuis le
        coin haut gauche ; None derrière la caméra."""
        forward, right, up = self._basis()
        d = np.array(point) - np.array([self.x, self.y, self.z])
        depth = float(d @ forward)
        if depth <= 1e-6:
            return None
        u = 0.5 + self.focal_px * float(d @ right) / depth / self.width
        v = 0.5 - self.focal_px * float(d @ up) / depth / self.height
        return u, v


@dataclass(frozen=True)
class Shares:
    """Parts de l'image (somme 1) et horizon (fraction depuis le haut)."""

    floor: float
    roof: float
    wall: float
    furniture: float
    horizon: float


@dataclass
class FramingScene:
    """Ce que les rayons peuvent toucher : contour de la pièce, pentes, hauteur du niveau,
    meubles (les tapis n'y sont pas : ils comptent comme du sol)."""

    segments: FloatArray
    """(n, 4) : x0, y0, x1, y1 des bords de l'emprise libre (murs)."""
    roofs: list[DerivedRoof]
    top: float
    boxes: list[Box]

    @classmethod
    def build(
        cls, free: BaseGeometry, roofs: list[DerivedRoof], top: float, boxes: list[Box]
    ) -> FramingScene:
        segs: list[tuple[float, float, float, float]] = []
        for part in getattr(free, "geoms", [free]):
            rings = [part.exterior, *part.interiors] if hasattr(part, "exterior") else []
            for ring in rings:
                pts = list(ring.coords)
                segs += [(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)]
        return cls(np.array(segs, dtype=float).reshape(-1, 4), roofs, top, boxes)

    # -------------------------------------------------------------- lancer de rayons
    def _rays(self, pose: Pose, cols: int) -> FloatArray:
        rows = max(1, round(cols * pose.height / pose.width))
        forward, right, up = pose._basis()
        u = (np.arange(cols) + 0.5) / cols * pose.width - pose.width / 2.0
        v = (np.arange(rows) + 0.5) / rows * pose.height - pose.height / 2.0
        uu, vv = np.meshgrid(u, v)
        d = (
            forward[None, None, :] * pose.focal_px
            + right[None, None, :] * uu[..., None]
            - up[None, None, :] * vv[..., None]
        )
        d /= np.linalg.norm(d, axis=-1, keepdims=True)
        return np.asarray(d.reshape(-1, 3), dtype=float)

    def labels(self, pose: Pose, cols: int = 64) -> NDArray[np.int_]:
        """Surface vue par chaque case de la grille (rangées de haut en bas), en codes
        FLOOR, ROOF, WALL, FURNITURE."""
        rows = max(1, round(cols * pose.height / pose.width))
        d = self._rays(pose, cols)
        n = d.shape[0]
        ox, oy, oz = pose.x, pose.y, pose.z
        dx, dy, dz = d[:, 0], d[:, 1], d[:, 2]
        inf = np.full(n, np.inf)
        with np.errstate(divide="ignore", invalid="ignore"):
            t_floor = np.where(dz < -1e-9, -oz / dz, inf)
            t_roof = np.where(dz > 1e-9, (self.top - oz) / dz, inf)
            for r in self.roofs:
                tan = math.tan(r.pitch)
                mx = (r.eave[0].x + r.eave[1].x) / 2.0
                my = (r.eave[0].y + r.eave[1].y) / 2.0
                d0 = (ox - mx) * r.inward.x + (oy - my) * r.inward.y
                dd = dx * r.inward.x + dy * r.inward.y
                rise = dz - tan * dd
                t = np.where(rise > 1e-9, (r.eave_height + tan * d0 - oz) / rise, inf)
                t_roof = np.minimum(t_roof, np.where(t > 0, t, inf))
            t_wall = inf.copy()
            if len(self.segments):
                x0, y0, x1, y1 = (self.segments[:, k][None, :] for k in range(4))
                ex, ey = x1 - x0, y1 - y0
                den = dx[:, None] * ey - dy[:, None] * ex
                wx, wy = x0 - ox, y0 - oy
                t = (wx * ey - wy * ex) / den
                s = (wx * dy[:, None] - wy * dx[:, None]) / den
                ok = (np.abs(den) > 1e-12) & (t > 1e-6) & (s >= 0.0) & (s <= 1.0)
                t_wall = np.min(np.where(ok, t, np.inf), axis=1)
        stack = np.stack([t_floor, t_roof, t_wall])
        label = np.argmin(stack, axis=0)
        t_hit = np.min(stack, axis=0)
        for b in self.boxes:
            t_box = _hit_box(b, (ox, oy, oz), d)
            closer = t_box < t_hit
            label = np.where(closer, FURNITURE, label)
            t_hit = np.minimum(t_hit, t_box)
        return np.asarray(label.reshape(rows, cols))

    def shares(self, pose: Pose, cols: int = 64) -> Shares:
        lab = self.labels(pose, cols)
        total = float(lab.size)
        part = [float(np.count_nonzero(lab == k)) / total for k in range(4)]
        return Shares(
            floor=part[FLOOR],
            roof=part[ROOF],
            wall=part[WALL],
            furniture=part[FURNITURE],
            horizon=pose.horizon(),
        )


def _hit_box(b: Box, origin: tuple[float, float, float], d: FloatArray) -> FloatArray:
    """Distance d'entrée des rayons dans la boîte (inf si manquée) : méthode des dalles dans
    le repère du meuble."""
    c, s = math.cos(b.yaw), math.sin(b.yaw)
    ox, oy = origin[0] - b.cx, origin[1] - b.cy
    lo = np.array([c * ox + s * oy, -s * ox + c * oy, origin[2]])
    ld = np.stack([c * d[:, 0] + s * d[:, 1], -s * d[:, 0] + c * d[:, 1], d[:, 2]], axis=1)
    mins = np.array([-b.half_w, -b.half_d, b.z0])
    maxs = np.array([b.half_w, b.half_d, b.z1])
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / ld
        t1 = (mins - lo) * inv
        t2 = (maxs - lo) * inv
    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin > 1e-6)
    return np.where(hit, tmin, np.inf)
