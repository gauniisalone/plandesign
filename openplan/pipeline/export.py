"""Exports ouverts : JSON (le projet lui-même), SVG et PDF du plan d'un niveau.

Le SVG et le PDF sont produits depuis la géométrie structurée (emprises, ouvertures, pièces,
pentes), pas depuis l'image. Les éléments estimés, inférés ou générés sont dessinés en
pointillé. Le PDF (reportlab, BSD) est à une échelle normalisée avec cartouche.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import UUID
from xml.sax.saxutils import escape

from openplan.brand import APP_NAME
from openplan.geom.furniture_symbols import place_symbol, symbol_for
from openplan.geom.opening_symbols import OpeningSymbol, opening_symbol
from openplan.geom.roof import roof_window_footprint
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.derived import compute_derived
from openplan.schemas.common import Point2, Source
from openplan.schemas.derived import DerivedLevel, DerivedRoom, DerivedStairs, DerivedWall
from openplan.schemas.elements import Furniture, Opening, OpeningKind, RoofWindow, Wall
from openplan.schemas.numbers import area, centimetres, decimal, length
from openplan.schemas.results import Artifact
from openplan.schemas.scene import Level, Project

PX_PER_M = 100.0
MARGIN_PX = 60.0
UNCERTAIN = frozenset({Source.ESTIMATED, Source.INFERRED, Source.GENERATED})

_STYLE = """
.wall { fill: #4a4a4a; stroke: #222; stroke-width: 1; }
.wall.uncertain { fill: #b8b8b8; stroke: #555; stroke-dasharray: 6 4; }
.opening { fill: #ffffff; stroke: #222; stroke-width: 1; }
.opening.uncertain { stroke-dasharray: 4 3; }
.window { fill: #dff1ff; }
.swing { fill: none; stroke: #222; stroke-width: 0.8; }
.opening-symbol { fill: none; stroke: #222; stroke-width: 0.8; }
.opening-symbol.leaf { stroke-width: 1.4; }
.opening-symbol.glass { stroke: #2a6fb0; stroke-width: 0.8; }
.opening-symbol.rail { stroke-width: 2.2; }
.opening-symbol.hidden { stroke-dasharray: 4 3; }
.opening.glazed { fill: #dff1ff; }
.room { fill: #f7f4ec; stroke: none; }
.room-label { font: 14px sans-serif; fill: #222; text-anchor: middle; }
.room-area { font: 11px sans-serif; fill: #555; text-anchor: middle; }
.dim { font: 11px sans-serif; fill: #b00020; text-anchor: middle; }
.dim.uncertain { fill: #8a6d00; font-style: italic; }
.legend { font: 10px sans-serif; fill: #666; }
.roof-low { fill: #f0a03a; fill-opacity: 0.18; stroke: none; }
.roof-eave { fill: none; stroke: #b86e12; stroke-width: 2; stroke-dasharray: 8 4; }
.roof-usable { fill: none; stroke: #b86e12; stroke-width: 1; stroke-dasharray: 3 3; }
.roof-label { font: 10px sans-serif; fill: #8a5a10; text-anchor: middle; }
.furniture-outline { fill: #ffffff; stroke: #555; stroke-width: 1; }
.roof-window { fill: #dff1ff; fill-opacity: 0.7; stroke: #2a6fb0; stroke-width: 1;
  stroke-dasharray: 5 3; }
.furniture-symbol { fill: none; stroke: #555; stroke-width: 0.7; }
.furniture.uncertain .furniture-outline,
.furniture.uncertain .furniture-symbol { stroke-dasharray: 4 3; }
.stairs-outline { fill: #ffffff; stroke: #222; stroke-width: 1.2; }
.stairs-tread { fill: none; stroke: #444; stroke-width: 0.7; }
.stairs-walk { fill: none; stroke: #222; stroke-width: 0.9; }
.stairs-cut { fill: none; stroke: #222; stroke-width: 0.9; }
.stairs-railing { fill: none; stroke: #222; stroke-width: 2.4; }
.stairs-label { font: 10px sans-serif; fill: #222; text-anchor: middle; }
"""
_DARK_STYLE = """
.wall { fill: #c9ccd6; stroke: #e6e8ef; }
.wall.uncertain { fill: #5b5f6b; stroke: #9aa0ad; }
.opening { fill: #1f2028; stroke: #d0d3dc; }
.window, .opening.glazed { fill: #23405c; }
.swing, .opening-symbol { stroke: #c8cbd4; }
.opening-symbol.glass { stroke: #7fb5ea; }
.room { fill: #262833; }
.room-label { fill: #e6e8ef; }
.room-area { fill: #a3a7b3; }
.dim { fill: #ff8a8a; }
.dim.uncertain { fill: #e0c060; }
.legend { fill: #9aa0ad; }
.roof-label { fill: #e0a860; }
.furniture-outline { fill: #2c2e38; stroke: #a3a7b3; }
.furniture-symbol { stroke: #a3a7b3; }
.roof-window { fill: #23405c; stroke: #7fb5ea; }
.stairs-outline { fill: #2c2e38; stroke: #d0d3dc; }
.stairs-tread { stroke: #a3a7b3; }
.stairs-walk, .stairs-cut, .stairs-railing { stroke: #d0d3dc; }
.stairs-label { fill: #e6e8ef; }
"""
"""Thème sombre (C1) : les couleurs de l'éditeur de plan en sombre, pour la vignette du plan
sur le tableau ; jamais pour un export à imprimer."""
SvgTheme = Literal["light", "dark"]
USABLE_LABEL = "1,80 m sous pente"


def area_label(room: DerivedRoom) -> str:
    """« 28,37 m² », complété par la surface à plus de 1,80 m quand des pentes la réduisent."""
    label = area(room.area_m2, 2)
    usable = room.usable_area_m2
    if usable is not None and usable < room.area_m2 - 1e-6:
        label += f" · {area(usable, 2)} à plus de 1,80 m"
    return label


def format_length(value_m: float, units: str) -> str:
    """Cote affichée dans l'unité du projet (« 1,20 m », « 120 cm », « 1200 mm »)."""
    return length(value_m, units)


def _dxf_area_label(room: DerivedRoom) -> str:
    """KAN-71 : le DXF est un format technique, il garde ses textes d'avant (« 28.37 m² »)."""
    label = f"{room.area_m2:.2f} m²"
    usable = room.usable_area_m2
    if usable is not None and usable < room.area_m2 - 1e-6:
        label += f" · {usable:.2f} m² à plus de 1,80 m"
    return label


def _dxf_length(value_m: float, units: str) -> str:
    """KAN-71 : cote du DXF, au point comme avant."""
    if units == "cm":
        return f"{value_m * 100:.0f} cm"
    if units == "mm":
        return f"{value_m * 1000:.0f} mm"
    return f"{value_m:.2f} m"


def dimension_angle(normal: Point2) -> float:
    """Angle (degrés, sens direct du plan) d'une cote posée le long de son mur, ramené dans
    ]−90°, 90°] pour se lire de gauche à droite ou de bas en haut."""
    angle = math.degrees(math.atan2(normal.x, -normal.y))  # direction du mur
    if angle > 90.0 or angle <= -90.0:
        angle += 180.0
    return angle


def is_oblique(normal: Point2) -> bool:
    """Mur ni horizontal ni vertical (KAN-29), à 0,1° près."""
    return min(abs(normal.x), abs(normal.y)) > math.sin(math.radians(0.1))


def render_svg(project: Project, level_id: UUID, theme: SvgTheme = "light") -> str:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    derived = compute_derived(level)
    graph = WallGraph.from_level(level)
    units = project.units_display

    if derived.bounds is None:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
            '<text x="10" y="40" class="legend">Niveau vide</text></svg>'
        )
    pad = 0.5  # mètres autour du plan pour les cotations
    minx, miny = derived.bounds.min.x - pad, derived.bounds.min.y - pad
    maxx, maxy = derived.bounds.max.x + pad, derived.bounds.max.y + pad
    width = (maxx - minx) * PX_PER_M + 2 * MARGIN_PX
    height = (maxy - miny) * PX_PER_M + 2 * MARGIN_PX

    def sx(x: float) -> float:
        return MARGIN_PX + (x - minx) * PX_PER_M

    def sy(y: float) -> float:
        return MARGIN_PX + (maxy - y) * PX_PER_M

    def pts(points: list[Point2]) -> str:
        return " ".join(f"{sx(p.x):.2f},{sy(p.y):.2f}" for p in points)

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        f"<style>{_STYLE}{_DARK_STYLE if theme == 'dark' else ''}</style>",
        f"<title>{escape(project.name)} — {escape(level.name)}</title>",
    ]
    # Pièces (sous les murs)
    for rid, room in derived.rooms.items():
        element = level.elements[rid]
        out.append(f'<polygon class="room" points="{pts(room.polygon)}"/>')
        cx, cy = sx(room.centroid.x), sy(room.centroid.y)
        name = escape(element.name or "Pièce")
        out.append(f'<text class="room-label" x="{cx:.1f}" y="{cy:.1f}">{name}</text>')
        out.append(
            f'<text class="room-area" x="{cx:.1f}" y="{cy + 16:.1f}">{area_label(room)}</text>'
        )
    # Pentes : zone sous 1,80 m hachurée entre l'égout et la ligne de hauteur utile
    for roof in derived.roofs.values():
        a, b = roof.eave
        out.append(f'<polyline class="roof-eave" points="{pts([a, b])}"/>')
        if roof.usable_offset_m is None or roof.usable_offset_m <= 0.0:
            continue
        d = roof.usable_offset_m
        a2 = Point2(x=a.x + roof.inward.x * d, y=a.y + roof.inward.y * d)
        b2 = Point2(x=b.x + roof.inward.x * d, y=b.y + roof.inward.y * d)
        out.append(f'<polygon class="roof-low" points="{pts([a, b, b2, a2])}"/>')
        out.append(f'<polyline class="roof-usable" points="{pts([a2, b2])}"/>')
        mx, my = (a2.x + b2.x) / 2.0, (a2.y + b2.y) / 2.0
        out.append(
            f'<text class="roof-label" x="{sx(mx):.1f}" y="{sy(my) - 3:.1f}">{USABLE_LABEL}</text>'
        )
    # Meubles (F4) : contour + symbole de leur famille, à la taille de leur boîte
    out.extend(
        _furniture_layer_svg(level, pts)
        + _roof_windows_svg(level, derived, pts)
        + _stairs_svg(derived, pts, sx, sy)
    )
    # Murs
    for wid, wall in derived.walls.items():
        element = graph.wall(wid)
        cls = "wall uncertain" if element.source in UNCERTAIN else "wall"
        out.append(f'<polygon class="{cls}" points="{pts(wall.polygon)}"/>')
    # Ouvertures
    for element in level.elements.values():
        if isinstance(element, Opening) and element.host_wall in derived.walls:
            out.extend(_opening_svg(graph, element, sx, sy))
    # Cotations
    out.extend(
        _dimension_svg(graph.wall(wid), wall, units, sx, sy) for wid, wall in derived.walls.items()
    )
    out.extend(
        [
            f'<text class="legend" x="{MARGIN_PX / 4:.0f}" y="{height - 8:.0f}">'
            "Trait plein : mesuré ou saisi · pointillé et ≈ : estimé, inféré ou généré · "
            "reconstruction estimée, pas un relevé certifié</text>",
            "</svg>",
        ]
    )
    return "\n".join(out)


def _dimension_svg(
    element: Wall,
    wall: DerivedWall,
    units: str,
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> str:
    """Cote d'un mur, côté gauche de son axe. Mur oblique (KAN-29) : la cote suit le mur
    (l'axe Y du SVG pointe vers le bas, d'où l'angle opposé) ; les murs droits gardent leur
    cote horizontale."""
    cls = "dim uncertain" if element.source in UNCERTAIN else "dim"
    gap = element.thickness / 2.0 + 0.18
    lx = sx(wall.midpoint.x + wall.normal.x * gap)
    ly = sy(wall.midpoint.y + wall.normal.y * gap)
    label = format_length(wall.length_m, units)
    if element.source in UNCERTAIN:
        label = f"≈ {label}"
    turn = ""
    if is_oblique(wall.normal):
        turn = f' transform="rotate({-dimension_angle(wall.normal):.1f} {lx:.1f} {ly:.1f})"'
    return f'<text class="{cls}" x="{lx:.1f}" y="{ly + 4:.1f}"{turn}>{label}</text>'


def _furniture_parts(
    furniture: Furniture,
) -> tuple[list[Point2], list[tuple[list[Point2], bool]]]:
    t, b = furniture.transform, furniture.bbox
    return place_symbol(
        symbol_for(furniture.category),
        t.position.x,
        t.position.y,
        t.yaw,
        b.width * t.scale,
        b.depth * t.scale,
    )


def roof_window_outlines(
    level: Level, derived: DerivedLevel
) -> list[tuple[RoofWindow, list[Point2]]]:
    """Emprise au sol de chaque velux (D1), d'après sa pente."""
    out: list[tuple[RoofWindow, list[Point2]]] = []
    for e in level.elements.values():
        if not isinstance(e, RoofWindow) or not e.visible or e.roof not in derived.roofs:
            continue
        roof = derived.roofs[e.roof]
        a, b = roof.eave
        corners = roof_window_footprint(
            (a.x, a.y),
            (b.x, b.y),
            (roof.inward.x, roof.inward.y),
            roof.pitch,
            e.offset,
            e.distance,
            e.width,
            e.length,
        )
        if corners:
            out.append((e, [Point2(x=x, y=y) for x, y in corners]))
    return out


def _roof_windows_svg(
    level: Level, derived: DerivedLevel, pts: Callable[[list[Point2]], str]
) -> list[str]:
    return [
        f'<polygon class="roof-window" points="{pts(outline)}"/>'
        for _, outline in roof_window_outlines(level, derived)
    ]


def _stairs_svg(
    derived: DerivedLevel,
    pts: Callable[[list[Point2]], str],
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> list[str]:
    """Escaliers (S1) : trémie, marches, ligne de foulée fléchée vers la montée, ligne de
    coupe, garde-corps en trait épais, et « n × h / g » au départ."""
    out: list[str] = []
    for sid, s in derived.stairs.items():
        out.append(f'<g class="stairs" data-element="{sid}">')
        out.append(f'<polygon class="stairs-outline" points="{pts(s.outline)}"/>')
        out.extend(f'<polygon class="stairs-tread" points="{pts(t)}"/>' for t in s.treads)
        out.append(f'<polyline class="stairs-walk" points="{pts(s.walk)}"/>')
        out.append(f'<polyline class="stairs-walk" points="{pts(s.arrow)}"/>')
        out.append(f'<polyline class="stairs-cut" points="{pts(s.cut)}"/>')
        out.extend(f'<polyline class="stairs-railing" points="{pts(r)}"/>' for r in s.railings)
        start = s.walk[0]
        out.append(
            f'<text class="stairs-label" x="{sx(start.x):.1f}" y="{sy(start.y) + 12:.1f}">'
            f"{stairs_label(s)}</text>"
        )
        out.append("</g>")
    return out


def stairs_label(s: DerivedStairs) -> str:
    """« 15 × 18,0 / 27 cm » : contremarches × hauteur / giron."""
    return f"{s.risers} × {decimal(s.riser_m * 100, 1)} / {centimetres(s.going_m)}"


def _furniture_layer_svg(level: Level, pts: Callable[[list[Point2]], str]) -> list[str]:
    return [
        line
        for element in level.elements.values()
        if isinstance(element, Furniture)
        for line in _furniture_svg(element, pts)
    ]


def _furniture_svg(furniture: Furniture, pts: Callable[[list[Point2]], str]) -> list[str]:
    outline, paths = _furniture_parts(furniture)
    cls = "furniture uncertain" if furniture.source in UNCERTAIN else "furniture"
    out = [
        f'<g class="{cls}" data-category="{escape(furniture.category)}">',
        f'<polygon class="furniture-outline" points="{pts(outline)}"/>',
    ]
    for points, closed in paths:
        tag = "polygon" if closed else "polyline"
        out.append(f'<{tag} class="furniture-symbol" points="{pts(points)}"/>')
    out.append("</g>")
    return out


def _opening_svg(
    graph: WallGraph,
    opening: Opening,
    fx: Callable[[float], float],
    fy: Callable[[float], float],
) -> list[str]:
    """Rectangle de l'ouverture dans l'épaisseur du mur, plus l'arc de battement des portes."""
    wall = graph.wall(opening.host_wall)
    a, _ = graph.wall_axis(wall.id)
    ux, uy = graph.wall_direction(wall.id)
    nx, ny = -uy, ux
    half = wall.thickness / 2.0 + 0.01
    p0 = (a.x + ux * opening.offset, a.y + uy * opening.offset)
    p1 = (p0[0] + ux * opening.width, p0[1] + uy * opening.width)
    corners = [
        Point2(x=p0[0] + nx * half, y=p0[1] + ny * half),
        Point2(x=p1[0] + nx * half, y=p1[1] + ny * half),
        Point2(x=p1[0] - nx * half, y=p1[1] - ny * half),
        Point2(x=p0[0] - nx * half, y=p0[1] - ny * half),
    ]
    cls = f"opening {opening.kind.value}"
    if opening.kind is OpeningKind.WINDOW:
        cls += " window"
    elif opening.kind in (OpeningKind.FRENCH_WINDOW, OpeningKind.SLIDING_WINDOW):
        cls += " glazed"
    if opening.source in UNCERTAIN:
        cls += " uncertain"
    points = " ".join(f"{fx(p.x):.2f},{fy(p.y):.2f}" for p in corners)
    parts = [f'<polygon class="{cls}" points="{points}"/>']
    sym = _symbol(graph, opening)
    for line in sym.lines:
        pts = " ".join(f"{fx(x):.2f},{fy(y):.2f}" for x, y in line.points)
        parts.append(f'<polyline class="opening-symbol {line.style}" points="{pts}"/>')
    for arc in sym.arcs:
        # Arc du bord libre (vantail fermé) vers le vantail ouvert. L'axe Y du SVG pointe
        # vers le bas : un arc en sens direct dans le plan se balaie en sens inverse ici.
        a0 = math.radians(arc.start_deg)
        a1 = math.radians(arc.start_deg + arc.extent_deg)
        cx, cy = arc.center
        sweep = 0 if arc.extent_deg > 0 else 1
        radius_px = arc.radius * PX_PER_M
        parts.append(
            f'<path class="swing" d="M {fx(cx + arc.radius * math.cos(a0)):.2f} '
            f"{fy(cy + arc.radius * math.sin(a0)):.2f} "
            f"A {radius_px:.2f} {radius_px:.2f} 0 0 {sweep} "
            f'{fx(cx + arc.radius * math.cos(a1)):.2f} {fy(cy + arc.radius * math.sin(a1)):.2f}"/>'
        )
    return parts


def _symbol(graph: WallGraph, opening: Opening) -> OpeningSymbol:
    """Symbole normalisé de l'ouverture (E5), en coordonnées du niveau."""
    wall = graph.wall(opening.host_wall)
    a, _ = graph.wall_axis(wall.id)
    return opening_symbol(opening, (a.x, a.y), graph.wall_direction(wall.id), wall.thickness / 2)


PAGE_SIZES: dict[str, tuple[float, float]] = {"A4": (841.89, 595.28), "A3": (1190.55, 841.89)}
"""Paysage, en points PDF (1/72 pouce)."""
PT_PER_M = 72.0 / 0.0254
SCALES = (50, 100, 200, 500, 1000)
"""Échelles normalisées essayées dans l'ordre : la première où le plan tient sur la page."""
MARGIN_PT = 36.0
TITLE_BLOCK_PT = 64.0
USABLE_LABEL_PDF = "1,80 m sous pente"


def _approx(label: str) -> str:
    """Les polices standard du PDF (WinAnsi) n'ont pas « ≈ » : on écrit « env. »."""
    return f"env. {label}"


def choose_scale(width_m: float, height_m: float, page: tuple[float, float]) -> int:
    """Plus grande échelle normalisée (1:50 d'abord) où le plan tient dans la zone de dessin."""
    avail_w = page[0] - 2 * MARGIN_PT
    avail_h = page[1] - 2 * MARGIN_PT - TITLE_BLOCK_PT
    for scale in SCALES:
        k = PT_PER_M / scale
        if width_m * k <= avail_w and height_m * k <= avail_h:
            return scale
    return SCALES[-1]


def render_pdf(project: Project, level_id: UUID, *, page_format: str = "A4") -> bytes:
    """Plan coté d'un niveau en PDF vectoriel, à une échelle normalisée, avec cartouche.

    Même géométrie que le SVG (pièces, murs, ouvertures, pentes, cotations) ; les éléments
    estimés, inférés ou générés sont en pointillé et cotés « env. ». Le cartouche rappelle
    que c'est une reconstruction estimée, pas un relevé certifié.
    """
    from reportlab.pdfgen import canvas as pdfcanvas

    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    page = PAGE_SIZES.get(page_format.upper(), PAGE_SIZES["A4"])
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=page, pageCompression=0)
    c.setTitle(f"{project.name} — {level.name}")
    c.setAuthor(APP_NAME)
    derived = compute_derived(level)
    graph = WallGraph.from_level(level)
    units = project.units_display
    if derived.bounds is None:
        c.setFont("Helvetica", 12)
        c.drawString(MARGIN_PT, page[1] / 2, "Niveau vide")
        _title_block(c, page, project.name, level.name, None)
        c.showPage()
        c.save()
        return buffer.getvalue()

    pad = 0.6
    minx, miny = derived.bounds.min.x - pad, derived.bounds.min.y - pad
    maxx, maxy = derived.bounds.max.x + pad, derived.bounds.max.y + pad
    scale = choose_scale(maxx - minx, maxy - miny, page)
    k = PT_PER_M / scale
    x0 = MARGIN_PT + ((page[0] - 2 * MARGIN_PT) - (maxx - minx) * k) / 2.0
    plan = _PdfPlan(c, k, x0 - minx * k, MARGIN_PT + TITLE_BLOCK_PT - miny * k)
    plan.rooms(level, derived)
    plan.roofs(derived)
    plan.furniture(level)
    plan.roof_windows(level, derived)
    plan.stairs(derived)
    plan.walls(graph, derived)
    for element in level.elements.values():
        if isinstance(element, Opening) and element.host_wall in derived.walls:
            _opening_pdf(c, graph, element, plan.sx, plan.sy, k)
    plan.dimensions(graph, derived, units)
    _title_block(c, page, project.name, level.name, scale)
    c.showPage()
    c.save()
    return buffer.getvalue()


class _PdfPlan:
    """Dessin du plan sur un canevas reportlab : mètres → points via `k`, `ox`, `oy`."""

    def __init__(self, c: Any, k: float, ox: float, oy: float) -> None:
        self.c, self.k, self.ox, self.oy = c, k, ox, oy

    def sx(self, x: float) -> float:
        return self.ox + x * self.k

    def sy(self, y: float) -> float:
        return self.oy + y * self.k

    def polygon(self, points: list[Point2], *, fill: bool, stroke: bool) -> None:
        if len(points) < 3:
            return
        p = self.c.beginPath()
        p.moveTo(self.sx(points[0].x), self.sy(points[0].y))
        for q in points[1:]:
            p.lineTo(self.sx(q.x), self.sy(q.y))
        p.close()
        self.c.drawPath(p, fill=int(fill), stroke=int(stroke))

    def path(self, points: list[Point2], *, closed: bool) -> None:
        if len(points) < 2:
            return
        p = self.c.beginPath()
        p.moveTo(self.sx(points[0].x), self.sy(points[0].y))
        for q in points[1:]:
            p.lineTo(self.sx(q.x), self.sy(q.y))
        if closed:
            p.close()
        self.c.drawPath(p, fill=0, stroke=1)

    def roof_windows(self, level: Level, derived: DerivedLevel) -> None:
        """Velux (D1) : emprise au sol en pointillé bleu, vue du dessus."""
        c = self.c
        c.setDash(4, 2)
        c.setLineWidth(0.5)
        c.setStrokeColorRGB(0.16, 0.44, 0.69)
        c.setFillColorRGB(0.87, 0.95, 1.0)
        for _, outline in roof_window_outlines(level, derived):
            self.polygon(outline, fill=True, stroke=True)
        c.setDash()

    def stairs(self, derived: DerivedLevel) -> None:
        """Escaliers (S1) : même symbole que le SVG."""
        c = self.c
        for s in derived.stairs.values():
            c.setStrokeColorRGB(0.13, 0.13, 0.13)
            c.setFillColorRGB(1.0, 1.0, 1.0)
            c.setLineWidth(0.6)
            self.polygon(s.outline, fill=True, stroke=True)
            c.setLineWidth(0.35)
            for t in s.treads:
                self.path(t, closed=True)
            c.setLineWidth(0.5)
            self.path(s.walk, closed=False)
            self.path(s.arrow, closed=False)
            self.path(s.cut, closed=False)
            c.setLineWidth(1.2)
            for r in s.railings:
                self.path(r, closed=False)
            c.setFont("Helvetica", 6)
            c.setFillColorRGB(0.13, 0.13, 0.13)
            c.drawCentredString(self.sx(s.walk[0].x), self.sy(s.walk[0].y) - 8, stairs_label(s))
        c.setLineWidth(0.5)

    def furniture(self, level: Level) -> None:
        """Meubles (F4) : contour blanc + symbole de leur famille ; pointillé si estimé."""
        c = self.c
        for element in level.elements.values():
            if not isinstance(element, Furniture):
                continue
            outline, paths = _furniture_parts(element)
            if element.source in UNCERTAIN:
                c.setDash(3, 2)
            else:
                c.setDash()
            c.setStrokeColorRGB(0.33, 0.33, 0.33)
            c.setFillColorRGB(1.0, 1.0, 1.0)
            c.setLineWidth(0.5)
            self.polygon(outline, fill=True, stroke=True)
            c.setLineWidth(0.35)
            for points, closed in paths:
                self.path(points, closed=closed)
        c.setDash()

    def rooms(self, level: Level, derived: DerivedLevel) -> None:
        c = self.c
        c.setFillColorRGB(0.97, 0.96, 0.93)
        for room in derived.rooms.values():
            self.polygon(room.polygon, fill=True, stroke=False)
        c.setFillColorRGB(0.13, 0.13, 0.13)
        for rid, room in derived.rooms.items():
            element = level.elements[rid]
            cx, cy = self.sx(room.centroid.x), self.sy(room.centroid.y)
            c.setFont("Helvetica", 10)
            c.drawCentredString(cx, cy, element.name or "Pièce")
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx, cy - 11, area_label(room))

    def roofs(self, derived: DerivedLevel) -> None:
        """Zone sous 1,80 m et lignes d'égout / de hauteur utile."""
        c = self.c
        for roof in derived.roofs.values():
            a, b = roof.eave
            d = roof.usable_offset_m
            if d is not None and d > 0.0:
                a2 = Point2(x=a.x + roof.inward.x * d, y=a.y + roof.inward.y * d)
                b2 = Point2(x=b.x + roof.inward.x * d, y=b.y + roof.inward.y * d)
                c.setFillColorRGB(0.98, 0.86, 0.68)
                self.polygon([a, b, b2, a2], fill=True, stroke=False)
                c.setStrokeColorRGB(0.72, 0.43, 0.07)
                c.setDash(3, 3)
                c.setLineWidth(0.6)
                c.line(self.sx(a2.x), self.sy(a2.y), self.sx(b2.x), self.sy(b2.y))
                c.setFillColorRGB(0.54, 0.35, 0.06)
                c.setFont("Helvetica", 7)
                c.drawCentredString(
                    self.sx((a2.x + b2.x) / 2), self.sy((a2.y + b2.y) / 2) + 2, USABLE_LABEL_PDF
                )
            c.setStrokeColorRGB(0.72, 0.43, 0.07)
            c.setDash(6, 3)
            c.setLineWidth(1.2)
            c.line(self.sx(a.x), self.sy(a.y), self.sx(b.x), self.sy(b.y))
        c.setDash()

    def walls(self, graph: WallGraph, derived: DerivedLevel) -> None:
        c = self.c
        for wid, wall in derived.walls.items():
            if graph.wall(wid).source in UNCERTAIN:
                c.setFillColorRGB(0.72, 0.72, 0.72)
                c.setStrokeColorRGB(0.33, 0.33, 0.33)
                c.setDash(4, 3)
            else:
                c.setFillColorRGB(0.29, 0.29, 0.29)
                c.setStrokeColorRGB(0.13, 0.13, 0.13)
                c.setDash()
            c.setLineWidth(0.6)
            self.polygon(wall.polygon, fill=True, stroke=True)
        c.setDash()

    def dimensions(self, graph: WallGraph, derived: DerivedLevel, units: str) -> None:
        """Le long du mur, côté intérieur, lisibles de gauche à droite ou de bas en haut."""
        c = self.c
        for wid, wall in derived.walls.items():
            element = graph.wall(wid)
            gap = element.thickness / 2.0 + 0.16
            lx = wall.midpoint.x + wall.normal.x * gap
            ly = wall.midpoint.y + wall.normal.y * gap
            label = format_length(wall.length_m, units)
            if element.source in UNCERTAIN:
                c.setFont("Helvetica-Oblique", 7)
                c.setFillColorRGB(0.54, 0.43, 0.0)
                label = _approx(label)
            else:
                c.setFont("Helvetica", 7)
                c.setFillColorRGB(0.69, 0.0, 0.13)
            angle = dimension_angle(wall.normal)
            c.saveState()
            c.translate(self.sx(lx), self.sy(ly))
            c.rotate(angle)
            c.drawCentredString(0.0, -2.5, label)
            c.restoreState()


def _opening_pdf(
    c: Any,
    graph: WallGraph,
    opening: Opening,
    fx: Callable[[float], float],
    fy: Callable[[float], float],
    k: float,
) -> None:
    wall = graph.wall(opening.host_wall)
    a, _ = graph.wall_axis(wall.id)
    ux, uy = graph.wall_direction(wall.id)
    nx, ny = -uy, ux
    half = wall.thickness / 2.0 + 0.01
    p0 = (a.x + ux * opening.offset, a.y + uy * opening.offset)
    p1 = (p0[0] + ux * opening.width, p0[1] + uy * opening.width)
    corners = [
        (p0[0] + nx * half, p0[1] + ny * half),
        (p1[0] + nx * half, p1[1] + ny * half),
        (p1[0] - nx * half, p1[1] - ny * half),
        (p0[0] - nx * half, p0[1] - ny * half),
    ]
    if opening.kind in (
        OpeningKind.WINDOW,
        OpeningKind.FRENCH_WINDOW,
        OpeningKind.SLIDING_WINDOW,
    ):
        c.setFillColorRGB(0.87, 0.95, 1.0)
    else:
        c.setFillColorRGB(1.0, 1.0, 1.0)
    c.setStrokeColorRGB(0.13, 0.13, 0.13)
    c.setDash(4, 3) if opening.source in UNCERTAIN else c.setDash()
    c.setLineWidth(0.6)
    p = c.beginPath()
    p.moveTo(fx(corners[0][0]), fy(corners[0][1]))
    for q in corners[1:]:
        p.lineTo(fx(q[0]), fy(q[1]))
    p.close()
    c.drawPath(p, fill=1, stroke=1)
    c.setDash()
    sym = _symbol(graph, opening)
    for line in sym.lines:
        c.setLineWidth({"leaf": 0.8, "rail": 1.2}.get(line.style, 0.4))
        if line.style == "glass":
            c.setStrokeColorRGB(0.16, 0.44, 0.69)
        else:
            c.setStrokeColorRGB(0.13, 0.13, 0.13)
        c.setDash(3, 2) if line.style == "hidden" else c.setDash()
        path = c.beginPath()
        path.moveTo(fx(line.points[0][0]), fy(line.points[0][1]))
        for x, y in line.points[1:]:
            path.lineTo(fx(x), fy(y))
        c.drawPath(path, fill=0, stroke=1)
    c.setDash()
    c.setStrokeColorRGB(0.13, 0.13, 0.13)
    c.setLineWidth(0.4)
    for arc in sym.arcs:
        r = arc.radius * k
        cx, cy = fx(arc.center[0]), fy(arc.center[1])
        c.arc(cx - r, cy - r, cx + r, cy + r, arc.start_deg, arc.extent_deg)


def _title_block(
    c: Any, page: tuple[float, float], project_name: str, level_name: str, scale: int | None
) -> None:
    """Cartouche : projet, niveau, date, échelle, légende et avertissement."""
    from datetime import date

    top = MARGIN_PT + TITLE_BLOCK_PT - 8
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.5)
    c.setDash()
    c.line(MARGIN_PT, top, page[0] - MARGIN_PT, top)
    c.setFillColorRGB(0.13, 0.13, 0.13)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(MARGIN_PT, top - 14, f"{project_name} — {level_name}")
    c.setFont("Helvetica", 8)
    echelle = f"Échelle 1:{scale}" if scale else "Sans échelle"
    c.drawString(MARGIN_PT, top - 26, f"{echelle} · {date.today().isoformat()} · {APP_NAME}")
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(
        MARGIN_PT,
        top - 38,
        "Trait plein : mesuré ou saisi · pointillé et « env. » : estimé, inféré ou généré · "
        "zone orange : hauteur sous pente < 1,80 m",
    )
    c.setFillColorRGB(0.69, 0.0, 0.13)
    c.drawString(
        MARGIN_PT, top - 50, "Reconstruction estimée depuis photos, pas un relevé certifié."
    )


DXF_LAYERS: dict[str, tuple[int, str]] = {
    "MURS": (7, "CONTINUOUS"),
    "MURS_ESTIMES": (8, "DASHED"),
    "OUVERTURES": (5, "CONTINUOUS"),
    "PIECES": (3, "CONTINUOUS"),
    "PENTES": (30, "DASHED"),
    "COTES": (1, "CONTINUOUS"),
    "ESCALIERS": (4, "CONTINUOUS"),
}
"""Calques DXF (couleur ACI, type de ligne) : les murs estimés, inférés ou générés sont sur
leur propre calque, en tirets, pour rester distinguables dans tout logiciel de dessin."""


def render_dxf(project: Project, level_id: UUID) -> str:
    """Plan d'un niveau en DXF (R2010, mètres) : murs, ouvertures, pièces, pentes, cotes.

    Même géométrie que le SVG et le PDF ; un calque par nature d'élément, les incertains à
    part. Texte DXF, relu par ezdxf dans les tests.
    """
    from ezdxf.enums import TextEntityAlignment
    from ezdxf.filemanagement import new as new_dxf

    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    doc = new_dxf("R2010", setup=True)
    doc.header["$INSUNITS"] = 6  # mètres
    for name, (color, linetype) in DXF_LAYERS.items():
        doc.layers.add(name, color=color, linetype=linetype)
    msp = doc.modelspace()
    derived = compute_derived(level)
    graph = WallGraph.from_level(level)
    units = project.units_display
    middle = TextEntityAlignment.MIDDLE_CENTER

    def text(
        content: str, x: float, y: float, layer: str, height: float, rotation: float = 0.0
    ) -> None:
        msp.add_text(
            content, height=height, rotation=rotation, dxfattribs={"layer": layer}
        ).set_placement((x, y), align=middle)

    if derived.bounds is None:
        text("Niveau vide", 0.0, 0.0, "PIECES", 0.25)
    for rid, room in derived.rooms.items():
        element = level.elements[rid]
        msp.add_lwpolyline(
            [(p.x, p.y) for p in room.polygon], close=True, dxfattribs={"layer": "PIECES"}
        )
        text(element.name or "Pièce", room.centroid.x, room.centroid.y + 0.12, "PIECES", 0.2)
        text(_dxf_area_label(room), room.centroid.x, room.centroid.y - 0.15, "PIECES", 0.14)
    for roof in derived.roofs.values():
        a, b = roof.eave
        msp.add_line((a.x, a.y), (b.x, b.y), dxfattribs={"layer": "PENTES"})
        d = roof.usable_offset_m
        if d is not None and d > 0.0:
            a2 = (a.x + roof.inward.x * d, a.y + roof.inward.y * d)
            b2 = (b.x + roof.inward.x * d, b.y + roof.inward.y * d)
            msp.add_line(a2, b2, dxfattribs={"layer": "PENTES"})
            text(USABLE_LABEL, (a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2 + 0.1, "PENTES", 0.12)
    for wid, wall in derived.walls.items():
        element = graph.wall(wid)
        layer = "MURS_ESTIMES" if element.source in UNCERTAIN else "MURS"
        msp.add_lwpolyline(
            [(p.x, p.y) for p in wall.polygon], close=True, dxfattribs={"layer": layer}
        )
        gap = element.thickness / 2.0 + 0.16
        label = _dxf_length(wall.length_m, units)
        if element.source in UNCERTAIN:
            label = f"≈ {label}"
        angle = dimension_angle(wall.normal)
        text(
            label,
            wall.midpoint.x + wall.normal.x * gap,
            wall.midpoint.y + wall.normal.y * gap,
            "COTES",
            0.12,
            angle,
        )
    for element in level.elements.values():
        if isinstance(element, Opening) and element.host_wall in derived.walls:
            _opening_dxf(msp, graph, element)
    _stairs_dxf(msp, derived, text)
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue()


def _stairs_dxf(msp: Any, derived: DerivedLevel, text: Callable[..., None]) -> None:
    """Escaliers (S1) sur leur calque : trémie, marches, foulée, flèche, coupe, garde-corps."""
    attrs = {"layer": "ESCALIERS"}
    for s in derived.stairs.values():
        msp.add_lwpolyline([(p.x, p.y) for p in s.outline], close=True, dxfattribs=attrs)
        for tread in s.treads:
            msp.add_lwpolyline([(p.x, p.y) for p in tread], close=True, dxfattribs=attrs)
        for line in (s.walk, s.arrow, s.cut, *s.railings):
            msp.add_lwpolyline([(p.x, p.y) for p in line], dxfattribs=attrs)
        text(stairs_label(s), s.walk[0].x, s.walk[0].y - 0.15, "ESCALIERS", 0.1)


def _opening_dxf(msp: Any, graph: WallGraph, opening: Opening) -> None:
    wall = graph.wall(opening.host_wall)
    a, _ = graph.wall_axis(wall.id)
    ux, uy = graph.wall_direction(wall.id)
    nx, ny = -uy, ux
    half = wall.thickness / 2.0 + 0.01
    p0 = (a.x + ux * opening.offset, a.y + uy * opening.offset)
    p1 = (p0[0] + ux * opening.width, p0[1] + uy * opening.width)
    corners = [
        (p0[0] + nx * half, p0[1] + ny * half),
        (p1[0] + nx * half, p1[1] + ny * half),
        (p1[0] - nx * half, p1[1] - ny * half),
        (p0[0] - nx * half, p0[1] - ny * half),
    ]
    msp.add_lwpolyline(corners, close=True, dxfattribs={"layer": "OUVERTURES"})
    sym = _symbol(graph, opening)
    for line in sym.lines:
        attrs = {"layer": "OUVERTURES"}
        if line.style == "hidden":
            attrs["linetype"] = "DASHED"
        if len(line.points) == 2:
            msp.add_line(line.points[0], line.points[1], dxfattribs=attrs)
        else:
            msp.add_lwpolyline(list(line.points), dxfattribs=attrs)
    for arc in sym.arcs:
        start, extent = arc.start_deg, arc.extent_deg
        lo, hi = (start, start + extent) if extent >= 0 else (start + extent, start)
        msp.add_arc(arc.center, arc.radius, lo, hi, dxfattribs={"layer": "OUVERTURES"})


def export_dxf(project: Project, level_id: UUID, out: Path) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dxf(project, level_id), encoding="utf-8")
    return Artifact(name=out.name, path=out, media_type="application/dxf", description="Plan DXF")


def export_pdf(project: Project, level_id: UUID, out: Path) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render_pdf(project, level_id))
    return Artifact(name=out.name, path=out, media_type="application/pdf", description="Plan 2D")


def export_svg(project: Project, level_id: UUID, out: Path) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_svg(project, level_id), encoding="utf-8")
    return Artifact(name=out.name, path=out, media_type="image/svg+xml", description="Plan 2D")


def shareable(project: Project) -> Project:
    """Copie destinée au partage : sans historique (chaque geste avec acteur, horodatage et
    patch) ni chemin d'image (les pixels ne sont jamais dans le JSON ; la référence garde id,
    empreinte, dimensions, focale et qualité). Niveaux, mesures, calibrations, contraintes et
    réglages de confidentialité sont intacts. Le fichier reste un projet valide, mais ses images
    ne sont plus localisables : c'est voulu."""
    images = {iid: ref.model_copy(update={"path": ""}) for iid, ref in project.images.items()}
    return project.model_copy(update={"images": images, "history": [], "history_cursor": 0})


def export_json(project: Project, out: Path, *, share: bool = False) -> Artifact:
    """`share=True` écrit la copie de `shareable` (sans historique ni chemins)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    data = shareable(project) if share else project
    out.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
    description = "Projet à partager (sans historique ni chemins)" if share else "Projet complet"
    return Artifact(name=out.name, path=out, media_type="application/json", description=description)
