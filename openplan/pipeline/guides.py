"""Arêtes de guidage d'une vue, calculées depuis la scène (bake) et la caméra, sans Blender
(KAN-88, repris de l'essai KAN-76 qui a donné le 4/5 de Z-Image).

Rastérisation simple au z-buffer (numpy, processeur, quelques secondes) des solides du bake :
murs, sols, pentes, charpente, meubles (leur modèle du catalogue), ouvertures (cadre, vitre ou
vantail) et décor. Une arête est tracée où l'objet change, où la normale plie (plus de 30°) et
où la profondeur saute (plus de 4 %). Contrairement au Canny du rendu, la texture (joints de
pierre, veinage) ne fait pas d'arête : le ControlNet suit la géométrie, le prompt les matières.
Même convention de caméra que blender/scripts/build_scene.py (`camera.json`)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from openplan.schemas.bake import Bake, BakedOpening, RenderCamera

Array = NDArray[Any]
RASTER_SCALE = 0.75
"""Rastérisé aux 3/4 de la taille de génération puis agrandi au plus proche : traits de 1 à
2 px, comme les guides de l'essai (576 × 768 pour 768 × 1024)."""
CREASE_COS = 0.87
DEPTH_JUMP = 0.04


@dataclass(frozen=True)
class ViewGuides:
    edges: NDArray[np.uint8]
    """H×W, 255 sur les arêtes."""
    element: NDArray[np.int32]
    """H×W, indice de l'élément vu (-1 : fond), voir `labels`."""
    labels: list[str]
    """Identifiant d'élément du bake (ou `decor-<k>`) de chaque indice."""


def _box(
    center: Array, ax: Array, ay: Array, hx: float, hy: float, z0: float, z1: float
) -> tuple[Array, Array]:
    c = center[:2]
    pts = [c + sx * hx * ax + sy * hy * ay for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    v = np.array([[p[0], p[1], z0] for p in pts] + [[p[0], p[1], z1] for p in pts], float)
    f = np.array(
        [
            [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
        ]
    )  # fmt: skip
    return v, f


def _opening_parts(o: BakedOpening) -> list[tuple[Array, Array]]:
    """Cadre (montants, traverse) et vitre ou vantail fermé, dans l'épaisseur du mur."""
    c = np.array([o.center.x, o.center.y], float)
    u = np.array(o.direction, float)
    n = np.array(o.normal, float)
    w, h, z0 = o.width, o.height, o.center.z
    t = max(o.wall_thickness, 0.06)
    m = 0.06
    cc = np.array([*c, 0.0])
    parts = [
        _box(
            np.array([*(c + side * (w / 2 - m / 2) * u), 0]), u, n, m / 2, t / 2 + 0.01, z0, z0 + h
        )
        for side in (-1, 1)
    ]
    parts.append(_box(cc, u, n, w / 2, t / 2 + 0.01, z0 + h - m, z0 + h))
    if o.kind == "window":
        parts.append(_box(cc, u, n, w / 2, t / 2 + 0.01, z0, z0 + m))
        parts.append(_box(cc, u, n, w / 2 - m, 0.01, z0 + m, z0 + h - m))
        parts.append(_box(cc, u, n, 0.025, 0.03, z0 + m, z0 + h - m))
    else:
        parts.append(_box(cc, u, n, w / 2 - m, 0.02, z0, z0 + h - m))
    return parts


@dataclass(frozen=True)
class SceneMesh:
    vertices: Array
    faces: Array
    face_element: NDArray[np.int32]
    labels: list[str]


def scene_mesh(bake: Bake) -> SceneMesh:
    """Triangles de la scène (repère du plan, z vers le haut) et l'élément de chaque face."""
    import trimesh

    from openplan.pipeline import scene3d

    verts: list[Array] = []
    faces: list[Array] = []
    owner: list[NDArray[np.int32]] = []
    labels: list[str] = []
    offset = 0

    def add(v: Array, f: Array, label: str) -> None:
        nonlocal offset
        verts.append(np.asarray(v, float))
        faces.append(np.asarray(f, np.int64) + offset)
        owner.append(np.full(len(f), len(labels), np.int32))
        labels.append(label)
        offset += len(v)

    by_element: dict[str, list[tuple[Array, Array]]] = {}
    for prism in bake.prisms:
        mesh = scene3d.furniture_mesh(prism)
        if mesh is None:
            mesh = scene3d._prism_mesh(prism)
        if mesh is not None:
            by_element.setdefault(str(prism.element_id), []).append(
                (np.asarray(mesh.vertices), np.asarray(mesh.faces))
            )
    for poly in bake.polyhedra:
        v = np.array([(p.x, p.y, p.z) for p in poly.vertices], float)
        by_element.setdefault(str(poly.element_id), []).append((v, np.array(poly.faces)))
    for o in bake.openings:
        by_element.setdefault(str(o.element_id), []).extend(_opening_parts(o))
    for element_id, items in by_element.items():
        vs, fs, off = [], [], 0
        for v, f in items:
            vs.append(v)
            fs.append(f + off)
            off += len(v)
        add(np.concatenate(vs), np.concatenate(fs), element_id)
    for k, d in enumerate(bake.decor):
        mesh_vf: tuple[Array, Array] | None = None
        if d.model:
            try:
                unit = scene3d._unit_model(d.model).copy()
                scale = (max(d.width, 0.01), max(d.depth, 0.01), max(d.height, 0.01))
                unit.apply_scale(scale)  # type: ignore[no-untyped-call]
                rotation = trimesh.transformations.rotation_matrix
                turn = rotation(d.yaw, [0, 0, 1])  # type: ignore[no-untyped-call]
                unit.apply_transform(turn)
                unit.apply_translation((d.x, d.y, d.z))
                mesh_vf = (np.asarray(unit.vertices), np.asarray(unit.faces))
            except (OSError, ValueError):
                mesh_vf = None
        if mesh_vf is None:
            ax = np.array([math.cos(d.yaw), math.sin(d.yaw)])
            ay = np.array([-math.sin(d.yaw), math.cos(d.yaw)])
            mesh_vf = _box(
                np.array([d.x, d.y, 0.0]),
                ax,
                ay,
                max(d.width, 0.02) / 2,
                max(d.depth, 0.02) / 2,
                d.z,
                d.z + max(d.height, 0.02),
            )
        add(mesh_vf[0], mesh_vf[1], f"decor-{k}")
    if not verts:
        empty = np.zeros((0, 3))
        return SceneMesh(empty, np.zeros((0, 3), np.int64), np.zeros(0, np.int32), [])
    return SceneMesh(np.concatenate(verts), np.concatenate(faces), np.concatenate(owner), labels)


def camera_basis(camera: RenderCamera) -> tuple[Array, Array, Array, Array]:
    """Position, droite, haut, avant (convention de build_scene.py)."""
    yaw, pitch = camera.yaw, camera.pitch
    f = np.array(
        [math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)]
    )
    r = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    u = np.cross(r, f)
    if camera.roll:
        cr, sr = math.cos(camera.roll), math.sin(camera.roll)
        r, u = cr * r + sr * u, -sr * r + cr * u
    p = np.array([camera.position.x, camera.position.y, camera.position.z], float)
    return p, r, u, f


def _clip_near(tris: Array, ids: Array, near: float) -> tuple[Array, Array]:
    """Coupe les triangles au plan proche (un ou deux sommets derrière la caméra)."""
    behind = tris[:, :, 2] < near
    nb = behind.sum(1)
    out_t, out_i = [tris[nb == 0]], [ids[nb == 0]]
    rows = np.arange(3)
    for count in (1, 2):
        sel = nb == count
        if not sel.any():
            continue
        t, i, b = tris[sel], ids[sel], behind[sel]
        lone = np.argmax(b, 1) if count == 1 else np.argmin(b, 1)
        order = (lone[:, None] + rows[None, :]) % 3
        t = np.take_along_axis(t, order[:, :, None], 1)
        a, bb, c = t[:, 0], t[:, 1], t[:, 2]

        def cut(p: Array, q: Array) -> Array:
            s = ((near - p[:, 2]) / (q[:, 2] - p[:, 2]))[:, None]
            return np.asarray(p + s * (q - p), float)

        ab, ac = cut(a, bb), cut(a, c)
        if count == 1:
            out_t += [np.stack([ab, bb, c], 1), np.stack([ab, c, ac], 1)]
            out_i += [i, i]
        else:
            out_t.append(np.stack([a, ab, ac], 1))
            out_i.append(i)
    return np.concatenate(out_t), np.concatenate(out_i)


def rasterize(  # noqa: PLR0915 - balayage vectorisé d'un seul tenant
    mesh: SceneMesh,
    camera: RenderCamera,
    width: int,
    height: int,
    near: float = 0.05,
    chunk: int = 4_000_000,
) -> tuple[NDArray[np.float32], NDArray[np.int64]]:
    """Profondeur (inf : fond) et face vue (-1 : fond) de chaque pixel."""
    w, h = width, height
    depth_out = np.full((h, w), np.inf, np.float32)
    face_out = np.full((h, w), -1, np.int64)
    if len(mesh.faces) == 0:
        return depth_out, face_out
    p, r, u, f = camera_basis(camera)
    vc = (mesh.vertices - p) @ np.stack([r, u, f], 1)
    tris, ids = _clip_near(vc[mesh.faces], np.arange(len(mesh.faces)), near)
    if len(tris) == 0:
        return depth_out, face_out
    focal = camera.focal_px * w / camera.width_px
    z = tris[:, :, 2]
    xs = w / 2 + focal * tris[:, :, 0] / z
    ys = h / 2 - focal * tris[:, :, 1] / z
    iz = 1.0 / z
    x0 = np.clip(np.ceil(xs.min(1) - 0.5), 0, w - 1).astype(np.int64)
    x1 = np.clip(np.floor(xs.max(1) - 0.5), 0, w - 1).astype(np.int64)
    y0 = np.clip(np.ceil(ys.min(1) - 0.5), 0, h - 1).astype(np.int64)
    y1 = np.clip(np.floor(ys.max(1) - 0.5), 0, h - 1).astype(np.int64)
    denom = (xs[:, 1] - xs[:, 0]) * (ys[:, 2] - ys[:, 0]) - (xs[:, 2] - xs[:, 0]) * (
        ys[:, 1] - ys[:, 0]
    )
    ok = (
        (xs.max(1) >= 0)
        & (xs.min(1) <= w)
        & (ys.max(1) >= 0)
        & (ys.min(1) <= h)
        & (x1 >= x0)
        & (y1 >= y0)
        & (np.abs(denom) > 1e-9)
    )
    sel = np.nonzero(ok)[0]
    if len(sel) == 0:
        return depth_out, face_out
    # Balayage par lignes : pour chaque (triangle, ligne), l'intervalle couvert est calculé
    # exactement ; seuls les pixels couverts sont générés.
    bh = (y1 - y0 + 1)[sel]
    rt = np.repeat(sel, bh)
    first = np.concatenate([[0], np.cumsum(bh)[:-1]])
    row = y0[rt] + (np.arange(int(bh.sum())) - np.repeat(first, bh))
    cy_r = row + 0.5
    xl = np.full(len(rt), np.inf)
    xr = np.full(len(rt), -np.inf)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        pa, pb = xs[rt, a], xs[rt, b]
        qa, qb = ys[rt, a], ys[rt, b]
        lo, hi = np.minimum(qa, qb), np.maximum(qa, qb)
        hit_row = (cy_r >= lo) & (cy_r <= hi) & (hi > lo)
        t = np.where(hit_row, (cy_r - qa) / np.where(hi > lo, qb - qa, 1.0), 0.0)
        xi = pa + t * (pb - pa)
        xl = np.where(hit_row, np.minimum(xl, xi), xl)
        xr = np.where(hit_row, np.maximum(xr, xi), xr)
    pl = np.clip(np.ceil(xl - 0.5), 0, w - 1)
    pr = np.clip(np.floor(xr - 0.5), 0, w - 1)
    valid = np.isfinite(xl) & np.isfinite(xr) & (xr - 0.5 >= 0) & (xl - 0.5 <= w - 1) & (pr >= pl)
    rt, row = rt[valid], row[valid]
    pl, pr = pl[valid].astype(np.int64), pr[valid].astype(np.int64)
    span = pr - pl + 1
    empty = np.iinfo(np.int64).max
    buf = np.full(w * h, empty, np.int64)
    csum = np.cumsum(span)
    start = 0
    while start < len(rt):
        base = csum[start - 1] if start else 0
        end = max(int(np.searchsorted(csum, base + chunk, side="right")), start + 1)
        cnt = span[start:end]
        local = np.repeat(np.arange(end - start), cnt)
        f0 = np.concatenate([[0], np.cumsum(cnt)[:-1]])
        px = pl[start:end][local] + (np.arange(int(cnt.sum())) - np.repeat(f0, cnt))
        py = row[start:end][local]
        tri = rt[start:end][local]
        cx, cy = px + 0.5, py + 0.5
        xa, xb, xc = xs[tri, 0], xs[tri, 1], xs[tri, 2]
        ya, yb, yc = ys[tri, 0], ys[tri, 1], ys[tri, 2]
        d = denom[tri]
        b1 = np.clip(((cx - xa) * (yc - ya) - (xc - xa) * (cy - ya)) / d, 0.0, 1.0)
        b2 = np.clip(((xb - xa) * (cy - ya) - (cx - xa) * (yb - ya)) / d, 0.0, 1.0)
        b0 = np.clip(1.0 - b1 - b2, 0.0, 1.0)
        izp = b0 * iz[tri, 0] + b1 * iz[tri, 1] + b2 * iz[tri, 2]
        zz = (1.0 / np.maximum(izp, 1e-9)).astype(np.float32)
        key = (zz.view(np.uint32).astype(np.int64) << 32) | tri.astype(np.int64)
        np.minimum.at(buf, py * w + px, key)
        start = end
    hit = buf != empty
    tri_idx = np.where(hit, buf & 0xFFFFFFFF, -1).astype(np.int64)
    zbits = (buf >> 32).astype(np.uint32)
    depth = np.where(hit, zbits.view(np.float32), np.inf).astype(np.float32)
    face = np.where(hit, ids[np.clip(tri_idx, 0, None)], -1).astype(np.int64)
    return depth.reshape(h, w), face.reshape(h, w)


def edge_map(element: Array, normal: Array, z: Array) -> NDArray[np.uint8]:
    """Arêtes : changement d'élément, pli de normale, saut de profondeur."""
    h, w = element.shape
    e = np.zeros((h, w), bool)
    zf = np.where(np.isfinite(z), z, 1e6)
    for dy, dx in ((0, 1), (1, 0)):
        a = (slice(0, h - dy), slice(0, w - dx))
        b = (slice(dy, h), slice(dx, w))
        diff = element[a] != element[b]
        dot = np.abs((normal[a] * normal[b]).sum(-1))
        diff |= (dot < CREASE_COS) & (element[a] >= 0)
        diff |= np.abs(zf[a] - zf[b]) / np.minimum(zf[a], zf[b]) > DEPTH_JUMP
        e[a] |= diff
    return (e * 255).astype(np.uint8)


VISIBLE_MIN_PX = 200
"""Un meuble est « vu » (et nommé dans la description) dès que 200 px de lui sont visibles,
mesurés sur une image de 768 px de grand côté (critère de l'essai KAN-84b, retenu par le PO)."""
VISIBLE_LONG_SIDE = 768


def visible_pixels(bake: Bake, camera: RenderCamera) -> dict[str, int]:
    """Pixels visibles (non cachés) de chaque élément du bake dans la vue, à 768 px de grand
    côté ; un élément absent du dictionnaire n'est pas vu."""
    scale = VISIBLE_LONG_SIDE / max(camera.width_px, camera.height_px)
    w, h = max(16, round(camera.width_px * scale)), max(16, round(camera.height_px * scale))
    mesh = scene_mesh(bake)
    _, face = rasterize(mesh, camera, w, h)
    hit = face[face >= 0]
    if hit.size == 0:
        return {}
    counts = np.bincount(mesh.face_element[hit], minlength=len(mesh.labels))
    return {mesh.labels[i]: int(c) for i, c in enumerate(counts) if c}


def view_guides(bake: Bake, camera: RenderCamera, size: tuple[int, int]) -> ViewGuides:
    """Arêtes (et élément vu par pixel) de la vue `camera`, à la taille `size` (largeur,
    hauteur) : rastérisées aux 3/4 puis agrandies au plus proche."""
    width, height = size
    rw, rh = max(16, round(width * RASTER_SCALE)), max(16, round(height * RASTER_SCALE))
    mesh = scene_mesh(bake)
    z, face = rasterize(mesh, camera, rw, rh)
    hit = face >= 0
    fc = np.clip(face, 0, None)
    if len(mesh.faces):
        tri = mesh.vertices[mesh.faces]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        element = np.where(hit, mesh.face_element[fc], -1).astype(np.int32)
        normal = np.where(hit[..., None], n[fc], 0.0)
    else:
        element = np.full((rh, rw), -1, np.int32)
        normal = np.zeros((rh, rw, 3))
    edges = edge_map(element, normal, z)
    rows = np.minimum((np.arange(height) * rh / height).astype(int), rh - 1)
    cols = np.minimum((np.arange(width) * rw / width).astype(int), rw - 1)
    return ViewGuides(
        edges=edges[rows][:, cols],
        element=element[rows][:, cols],
        labels=mesh.labels,
    )
