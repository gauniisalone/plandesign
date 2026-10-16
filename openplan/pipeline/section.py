"""Coupe verticale cotée (E3) : la scène 3D tranchée par un plan vertical, en PDF.

Le plan est `x = valeur` ou `y = valeur` dans le repère du niveau. La scène réaliste (murs,
dalles, pentes, charpente, escaliers, meubles) est coupée par trimesh ; les contours obtenus
sont dessinés à une échelle normalisée : structure coupée en gris plein, meubles au trait,
cotes de hauteur (sol, plafond ou faîtage) et de largeur (hors tout et intérieur), cartouche.
Même géométrie que l'aperçu 3D et le rendu : reconstruction estimée, pas un relevé.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import numpy as np

from openplan.brand import APP_NAME
from openplan.geom.wallgraph import GeometryError
from openplan.pipeline.export import (
    MARGIN_PT,
    PAGE_SIZES,
    PT_PER_M,
    TITLE_BLOCK_PT,
    _title_block,
    choose_scale,
    format_length,
)
from openplan.pipeline.scene3d import bake_level, build_scene
from openplan.schemas.elements import Fixture, Floor, Furniture, RoofPlane
from openplan.schemas.scene import Project

SectionAxis = Literal["x", "y"]
STRUCTURE = frozenset({"wall", "floor", "ceiling", "roof_plane", "beam", "stairs"})


@dataclass
class SectionProfile:
    """Contours de la coupe, en mètres : u le long de la coupe, v la hauteur."""

    axis: SectionAxis
    value: float
    structure: list[list[tuple[float, float]]] = field(default_factory=list)
    furniture: list[list[tuple[float, float]]] = field(default_factory=list)
    floor_span: tuple[float, float] | None = None
    """Étendue du sol coupé (largeur intérieure)."""
    has_roof: bool = False

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        pts = [p for line in self.structure + self.furniture for p in line]
        if not pts:
            return None
        us, vs = zip(*pts, strict=True)
        return min(us), min(vs), max(us), max(vs)


def section_profile(
    project: Project, level_id: UUID, axis: SectionAxis, value: float
) -> SectionProfile:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    scene = build_scene(bake_level(project, level_id, style="realistic"))
    normal = np.array([1.0, 0.0, 0.0]) if axis == "x" else np.array([0.0, 1.0, 0.0])
    origin = normal * value
    keep = (1, 2) if axis == "x" else (0, 2)
    profile = SectionProfile(axis=axis, value=value)
    for name, mesh in scene.geometry.items():
        element = level.elements.get(UUID(name.split("#")[0])) if _is_uuid(name) else None
        if element is None:
            continue
        path = mesh.section(plane_origin=origin, plane_normal=normal)
        if path is None:
            continue
        lines = [
            [(float(p[keep[0]]), float(p[keep[1]])) for p in loop]
            for loop in path.discrete
            if len(loop) >= 2
        ]
        if isinstance(element, Furniture | Fixture):
            profile.furniture.extend(lines)
            continue
        if element.type not in STRUCTURE:
            continue
        profile.structure.extend(lines)
        if isinstance(element, RoofPlane):
            profile.has_roof = True
        if isinstance(element, Floor) and lines:
            us = [u for line in lines for u, _ in line]
            span = (min(us), max(us))
            if profile.floor_span is None or span[1] - span[0] > (
                profile.floor_span[1] - profile.floor_span[0]
            ):
                profile.floor_span = span
    return profile


def _is_uuid(name: str) -> bool:
    try:
        UUID(name.split("#", maxsplit=1)[0])
    except ValueError:
        return False
    return True


def render_section_pdf(
    project: Project, level_id: UUID, axis: SectionAxis, value: float, *, page_format: str = "A4"
) -> bytes:
    """La coupe verticale cotée en PDF vectoriel (échelle normalisée, cartouche)."""
    from reportlab.pdfgen import canvas as pdfcanvas

    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    profile = section_profile(project, level_id, axis, value)
    page = PAGE_SIZES.get(page_format.upper(), PAGE_SIZES["A4"])
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=page, pageCompression=0)
    label = f"Coupe {'x' if axis == 'x' else 'y'} = {format_length(value, 'm')}"
    c.setTitle(f"{project.name} — {level.name} — {label}")
    c.setAuthor(APP_NAME)
    bounds = profile.bounds
    if bounds is None:
        c.setFont("Helvetica", 12)
        c.drawString(MARGIN_PT, page[1] / 2, f"{label} : le plan ne traverse rien.")
        _title_block(c, page, project.name, f"{level.name} — {label}", None)
        c.showPage()
        c.save()
        return buffer.getvalue()
    umin, vmin, umax, vmax = bounds
    pad = 0.8
    scale = choose_scale(umax - umin + 2 * pad, vmax - vmin + 2 * pad, page)
    k = PT_PER_M / scale
    ox = MARGIN_PT + (page[0] - 2 * MARGIN_PT - (umax - umin) * k) / 2.0 - umin * k
    oy = MARGIN_PT + TITLE_BLOCK_PT + pad * k - vmin * k

    def sx(u: float) -> float:
        return ox + u * k

    def sy(v: float) -> float:
        return oy + v * k

    def path(line: list[tuple[float, float]], *, fill: bool) -> None:
        p = c.beginPath()
        p.moveTo(sx(line[0][0]), sy(line[0][1]))
        for u, v in line[1:]:
            p.lineTo(sx(u), sy(v))
        closed = (
            len(line) > 2 and abs(line[0][0] - line[-1][0]) + abs(line[0][1] - line[-1][1]) < 1e-6
        )
        if closed:
            p.close()
        c.drawPath(p, fill=int(fill and closed), stroke=1)

    c.setStrokeColorRGB(0.1, 0.1, 0.1)
    c.setFillColorRGB(0.62, 0.62, 0.62)
    c.setLineWidth(0.7)
    for line in profile.structure:
        path(line, fill=True)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.setLineWidth(0.35)
    for line in profile.furniture:
        path(line, fill=False)
    _dimensions(c, profile, bounds, sx, sy, project.units_display)
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.drawString(MARGIN_PT, page[1] - MARGIN_PT, label)
    _title_block(c, page, project.name, f"{level.name} — {label}", scale)
    c.showPage()
    c.save()
    return buffer.getvalue()


def _dimensions(
    c: Any,
    profile: SectionProfile,
    bounds: tuple[float, float, float, float],
    sx: Any,
    sy: Any,
    units: str,
) -> None:
    """Cotes : hauteur du sol au point le plus haut (faîtage ou plafond) à gauche ; largeur
    hors tout et largeur intérieure (sol coupé) en bas."""
    umin, vmin, umax, vmax = bounds
    c.setStrokeColorRGB(0.69, 0.0, 0.13)
    c.setFillColorRGB(0.69, 0.0, 0.13)
    c.setLineWidth(0.5)
    c.setFont("Helvetica", 7)
    x = sx(umin) - 18
    c.line(x, sy(0.0), x, sy(vmax))
    for v in (0.0, vmax):
        c.line(x - 3, sy(v), x + 3, sy(v))
    word = "faîtage" if profile.has_roof else "hauteur"
    c.saveState()
    c.translate(x - 4, (sy(0.0) + sy(vmax)) / 2)
    c.rotate(90)
    c.drawCentredString(0, 0, f"{word} {format_length(vmax, units)}")
    c.restoreState()
    rows = [((umin, umax), "hors tout")]
    if profile.floor_span is not None:
        rows.append((profile.floor_span, "intérieur"))
    for i, ((a, b), what) in enumerate(rows):
        y = sy(vmin) - 14 - 14 * i
        c.line(sx(a), y, sx(b), y)
        for u in (a, b):
            c.line(sx(u), y - 3, sx(u), y + 3)
        c.drawCentredString((sx(a) + sx(b)) / 2, y + 3, f"{what} {format_length(b - a, units)}")
