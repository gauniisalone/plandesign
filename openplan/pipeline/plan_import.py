"""Import d'un plan 2D existant (U1, décision d'augus du 27 sept. 2026) : DXF → murs.

Première version **sans modèle appris** : les tracés vectoriels d'un DXF (LINE, LWPOLYLINE,
POLYLINE, blocs insérés) deviennent des segments en mètres. Les unités sont lues dans l'en-tête
(`$INSUNITS`) ou devinées à l'étendue du dessin, et dites. Les murs dessinés en **double
trait** (deux lignes parallèles proches) sont fusionnés en un mur dont l'épaisseur est leur
écart. Deux temps : `preview_dxf` (segments, calques, avertissements ; rien n'entre dans la
scène) puis `apply_segments` (les segments retenus entrent dans un niveau vide, source
`observed` : relevés d'un document, pas mesurés par nous ; un seul commit, annulable).
PDF vectoriel et SVG suivront avec la même structure `PlanSegment`.
"""

from __future__ import annotations

import io
import itertools
import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from openplan.pipeline.floorplan import DetectionError
from openplan.scene import history
from openplan.scene.ops import detect_rooms
from openplan.schemas.common import Point2, Provenance, Source, StrictModel, Uncertainty
from openplan.schemas.derived import Bounds
from openplan.schemas.elements import Vertex, Wall
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import Project

ACTOR = "pipeline:plan_import"
STEP = "plan_import.dxf"
OBSERVED_CONFIDENCE = 0.8
OBSERVED_SIGMA_M = 0.02
"""Un tracé vectoriel est précis ; l'incertitude vient du document lui-même."""
ESTIMATED_CONFIDENCE = 0.5
ESTIMATED_SIGMA_M = 0.1
"""Trait vectorisé dans une image : la position dépend du seuillage et de l'échelle du fond."""
MIN_SEGMENT_M = 0.05
SNAP_M = 0.03
"""Deux extrémités à moins de cette distance partagent le même sommet."""
DOUBLE_LINE_MIN_M = 0.04
DOUBLE_LINE_MAX_M = 0.60
"""Écart entre deux traits parallèles lu comme l'épaisseur d'un mur."""
PARALLEL_DEG = 3.0
DEFAULT_THICKNESS_M = 0.15
WALL_LAYER_HINTS = ("mur", "wall", "cloison", "partition")
INSUNITS_TO_M: dict[int, tuple[float, str]] = {
    1: (0.0254, "pouces (en-tête du fichier)"),
    2: (0.3048, "pieds (en-tête du fichier)"),
    4: (0.001, "millimètres (en-tête du fichier)"),
    5: (0.01, "centimètres (en-tête du fichier)"),
    6: (1.0, "mètres (en-tête du fichier)"),
}


class PlanSegment(StrictModel):
    start: Point2
    end: Point2
    layer: str = "0"
    thickness: float | None = None
    """Épaisseur déduite d'un double trait ; None = épaisseur par défaut."""

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)


class PlanLayer(StrictModel):
    name: str
    segments: int
    selected: bool
    """Coché par défaut : calque dont le nom évoque un mur, ou tous s'il n'y en a aucun."""


PlanKind = Literal["dxf", "pdf", "svg", "image"]
KIND_LABELS: dict[str, str] = {
    "dxf": "DXF",
    "pdf": "PDF vectoriel",
    "svg": "SVG",
    "image": "image de plan",
}


class PlanImportPreview(StrictModel):
    kind: PlanKind
    units: str
    """D'où vient l'échelle : en-tête du fichier, échelle papier lue, ou devinée."""
    segments: list[PlanSegment] = Field(default_factory=list)
    layers: list[PlanLayer] = Field(default_factory=list)
    bounds: Bounds | None = None
    entity_count: int = 0
    merged_pairs: int = 0
    """Doubles traits fusionnés en un mur épais."""
    paper_scale: int | None = None
    """Plan papier (PDF, SVG en mm) : dénominateur de l'échelle 1:n utilisée pour les mètres ;
    l'utilisateur peut en choisir une autre (les segments se remettent à l'échelle)."""
    warnings: list[str] = Field(default_factory=list)


class PlanApplyBody(StrictModel):
    segments: list[PlanSegment] = Field(min_length=1)
    scale: float = Field(default=1.0, gt=0.0)
    """Facteur appliqué aux segments (déjà en mètres) si l'utilisateur corrige l'échelle."""
    kind: PlanKind = "dxf"
    source: Literal["observed", "estimated"] = "observed"
    """Tracés d'un document vectoriel : observés ; traits vectorisés d'une image : estimés."""


# ------------------------------------------------------------------ lecture DXF
def _units(doc: object, extent: float) -> tuple[float, str, list[str]]:
    header = getattr(doc, "header", {})
    try:
        code = int(header.get("$INSUNITS", 0))
    except (TypeError, ValueError):
        code = 0
    if code in INSUNITS_TO_M:
        factor, label = INSUNITS_TO_M[code]
        return factor, label, []
    return _guess_units(extent)


def _guess_units(extent: float) -> tuple[float, str, list[str]]:
    """Sans unité déclarée : une pièce fait quelques mètres, pas quelques milliers."""
    if extent > 500:
        return (
            0.001,
            "millimètres (devinés à l'étendue du dessin)",
            [
                "Le fichier ne déclare pas d'unité : millimètres supposés d'après l'étendue du "
                "dessin. Corrigez l'échelle si le plan paraît trop grand ou trop petit."
            ],
        )
    if extent > 50:
        return (
            0.01,
            "centimètres (devinés à l'étendue du dessin)",
            ["Le fichier ne déclare pas d'unité : centimètres supposés d'après l'étendue."],
        )
    return (
        1.0,
        "mètres (supposés : aucune unité déclarée)",
        ["Le fichier ne déclare pas d'unité : mètres supposés."],
    )


RawSegment = tuple[tuple[float, float], tuple[float, float], str]


def _raw_segments(msp: object) -> list[RawSegment]:
    """Segments bruts (unités du fichier) des lignes et polylignes, blocs insérés compris."""
    out: list[RawSegment] = []

    def add_poly(points: list[tuple[float, float]], closed: bool, layer: str) -> None:
        for a, b in itertools.pairwise(points):
            out.append((a, b, layer))
        if closed and len(points) > 2:
            out.append((points[-1], points[0], layer))

    def handle(entity: object) -> None:
        kind = entity.dxftype()  # type: ignore[attr-defined]
        layer = str(entity.dxf.layer)  # type: ignore[attr-defined]
        if kind == "LINE":
            s, e = entity.dxf.start, entity.dxf.end  # type: ignore[attr-defined]
            out.append(((float(s.x), float(s.y)), (float(e.x), float(e.y)), layer))
        elif kind == "LWPOLYLINE":
            pts = [(float(x), float(y)) for x, y in entity.get_points("xy")]  # type: ignore[attr-defined]
            add_poly(pts, bool(entity.closed), layer)  # type: ignore[attr-defined]
        elif kind == "POLYLINE":
            pts = [(float(p.x), float(p.y)) for p in entity.points()]  # type: ignore[attr-defined]
            add_poly(pts, bool(entity.is_closed), layer)  # type: ignore[attr-defined]
        elif kind == "INSERT":
            for virtual in entity.virtual_entities():  # type: ignore[attr-defined]
                handle(virtual)

    for entity in msp:  # type: ignore[attr-defined]
        handle(entity)
    return out


def preview_dxf(data: bytes) -> PlanImportPreview:
    """Lit un DXF (texte ou binaire) et renvoie ses segments en mètres, sans toucher la scène."""
    from ezdxf import recover

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.dxf"
        path.write_bytes(data)
        try:
            doc, _auditor = recover.readfile(str(path))
        except Exception as exc:
            msg = f"fichier DXF illisible : {exc}"
            raise DetectionError(msg) from exc
        raw = _raw_segments(doc.modelspace())
    if not raw:
        msg = "aucun tracé (ligne ou polyligne) dans ce DXF"
        raise DetectionError(msg)
    factor, units, warnings = _units(doc, _extent(raw))
    return _preview_from_raw("dxf", raw, factor, units, warnings)


def _extent(raw: list[RawSegment]) -> float:
    xs = [p[0] for a, b, _ in raw for p in (a, b)]
    ys = [p[1] for a, b, _ in raw for p in (a, b)]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _preview_from_raw(
    kind: PlanKind,
    raw: list[RawSegment],
    factor: float,
    units: str,
    warnings: list[str],
    *,
    thicknesses: dict[int, float] | None = None,
    paper_scale: int | None = None,
    selected_layers: set[str] | None = None,
) -> PlanImportPreview:
    """Segments bruts (unités du document) → aperçu en mètres, origine au coin bas gauche,
    doubles traits fusionnés. `thicknesses` : épaisseur connue (unités du document) par index
    de segment brut (rectangles pleins d'un PDF)."""
    xs = [p[0] for a, b, _ in raw for p in (a, b)]
    ys = [p[1] for a, b, _ in raw for p in (a, b)]
    origin = (min(xs), min(ys))
    segments = [
        PlanSegment(
            start=Point2(x=(a[0] - origin[0]) * factor, y=(a[1] - origin[1]) * factor),
            end=Point2(x=(b[0] - origin[0]) * factor, y=(b[1] - origin[1]) * factor),
            layer=layer,
            thickness=(
                round(thicknesses[i] * factor, 3) if thicknesses and i in thicknesses else None
            ),
        )
        for i, (a, b, layer) in enumerate(raw)
    ]
    segments = [s for s in segments if s.length >= MIN_SEGMENT_M]
    if not segments:
        msg = "tous les tracés font moins de 5 cm une fois à l'échelle : unité douteuse"
        raise DetectionError(msg)
    segments, merged = merge_double_lines(segments)
    layers = _layers(segments)
    if selected_layers is not None:
        layers = [
            layer.model_copy(update={"selected": layer.name in selected_layers}) for layer in layers
        ]
    return PlanImportPreview(
        kind=kind,
        units=units,
        segments=segments,
        layers=layers,
        bounds=_bounds(segments),
        entity_count=len(raw),
        merged_pairs=merged,
        paper_scale=paper_scale,
        warnings=warnings,
    )


# ------------------------------------------------------------------ PDF vectoriel
PT_TO_M = 0.0254 / 72.0
PAPER_SCALES = (50, 100, 20, 200, 25, 500)
SCALE_TEXT = re.compile(r"1\s*[:/]\s*(\d{1,4})\b")
THIN_RECT_PT = 2.5
"""Un rectangle plus fin que cela est un trait dessiné en rectangle."""
WALL_RECT_MAX_PT = 40.0
"""Un rectangle plein plus étroit que cela (≈ 0,7 m à 1:50) est un mur dessiné plein."""


def paper_scale_from_text(text: str) -> int | None:
    """« 1:50 », « 1/100 », « Échelle 1 : 50 » → 50, 100, 50."""
    match = SCALE_TEXT.search(text or "")
    return int(match.group(1)) if match else None


def guess_paper_scale(extent_m_at_1: float) -> tuple[int, list[str]]:
    """Choisit l'échelle papier 1:n qui donne une pièce plausible (2,5 à 25 m)."""
    for scale in PAPER_SCALES:
        if 2.5 <= extent_m_at_1 * scale <= 25.0:
            return scale, [
                f"Aucune échelle écrite sur le plan : 1:{scale} supposé d'après la taille du "
                "dessin. Choisissez l'échelle réelle si le plan paraît trop grand ou trop petit."
            ]
    return 50, [
        "Aucune échelle écrite et taille de dessin inhabituelle : 1:50 supposé, à corriger."
    ]


def _pdf_raw(page: object) -> tuple[list[RawSegment], dict[int, float], dict[str, float]]:
    """Segments (points, Y vers le haut) des lignes et rectangles de la page, calque par
    épaisseur de trait ; épaisseur des rectangles pleins étroits (murs pleins)."""
    raw: list[RawSegment] = []
    thick: dict[int, float] = {}
    widths: dict[str, float] = {}
    height = float(page.height)  # type: ignore[attr-defined]

    def layer_of(lw: float) -> str:
        name = f"trait {lw:.1f} pt"
        widths[name] = lw
        return name

    for line in page.lines:  # type: ignore[attr-defined]
        pts = line.get("pts") or []
        if len(pts) < 2:
            continue
        lw = float(line.get("linewidth") or 1.0)
        layer = layer_of(lw)
        for (x0, t0), (x1, t1) in itertools.pairwise(pts):
            raw.append(((float(x0), height - float(t0)), (float(x1), height - float(t1)), layer))
    for rect in page.rects:  # type: ignore[attr-defined]
        x0, x1 = float(rect["x0"]), float(rect["x1"])
        y0, y1 = float(rect["y0"]), float(rect["y1"])
        w, h = x1 - x0, y1 - y0
        lw = float(rect.get("linewidth") or 1.0)
        if min(w, h) <= THIN_RECT_PT:
            # Trait dessiné comme un rectangle fin : segment sur son grand axe.
            layer = layer_of(round(min(w, h), 1))
            if w >= h:
                raw.append(((x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2), layer))
            else:
                raw.append((((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), layer))
        elif rect.get("fill") and min(w, h) <= WALL_RECT_MAX_PT:
            # Mur dessiné plein : axe du grand côté, épaisseur = petit côté.
            layer = "murs pleins"
            widths[layer] = 99.0
            if w >= h:
                raw.append(((x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2), layer))
            else:
                raw.append((((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), layer))
            thick[len(raw) - 1] = min(w, h)
        elif rect.get("stroke", True):
            layer = layer_of(lw)
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            for a, b in itertools.pairwise([*corners, corners[0]]):
                raw.append((a, b, layer))
    return raw, thick, widths


def preview_pdf(data: bytes, *, paper_scale: int | None = None) -> PlanImportPreview:
    """Première page d'un PDF vectoriel : lignes et rectangles → segments en mètres, d'après
    l'échelle écrite sur le plan (« 1:50 ») ou devinée ; calques = épaisseurs de trait, les
    plus épais cochés d'office (les murs sont dessinés plus gras que les cotes)."""
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages:
                msg = "PDF sans page"
                raise DetectionError(msg)
            page = pdf.pages[0]
            raw, thick, widths = _pdf_raw(page)
            text = page.extract_text() or ""
    except DetectionError:
        raise
    except Exception as exc:
        msg = f"PDF illisible : {exc}"
        raise DetectionError(msg) from exc
    if not raw:
        msg = (
            "aucun tracé vectoriel dans ce PDF : s'il s'agit d'un scan, importez-le comme image "
            "et calez-le avec l'outil Cote"
        )
        raise DetectionError(msg)
    warnings: list[str] = []
    scale = paper_scale or paper_scale_from_text(text)
    if scale is None:
        scale, warnings = guess_paper_scale(_extent(raw) * PT_TO_M)
        units = f"points PDF, échelle 1:{scale} supposée"
    else:
        units = f"points PDF, échelle 1:{scale}" + (
            " (choisie)" if paper_scale else " (lue sur le plan)"
        )
    factor = PT_TO_M * scale
    return _preview_from_raw(
        "pdf",
        raw,
        factor,
        units,
        warnings,
        thicknesses=thick,
        paper_scale=scale,
        selected_layers=_thick_layers(widths),
    )


def _thick_layers(widths: dict[str, float]) -> set[str] | None:
    """Les calques « épais » (≥ 60 % du trait le plus gras) : ce sont les murs, pas les cotes."""
    if len(widths) <= 1:
        return None
    top = max(widths.values())
    return {name for name, lw in widths.items() if lw >= 0.6 * top}


# ------------------------------------------------------------------ SVG
SVG_NS = "{http://www.w3.org/2000/svg}"
INKSCAPE_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"
SVG_UNITS_TO_M = {"mm": 0.001, "cm": 0.01, "in": 0.0254, "pt": PT_TO_M, "pc": 12 * PT_TO_M}
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate)\s*\(([^)]*)\)")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _mul(m: Matrix, n: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def _parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    for kind, args in _TRANSFORM.findall(text or ""):
        v = [float(x) for x in _NUMBER.findall(args)]
        if kind == "matrix" and len(v) == 6:
            n: Matrix = (v[0], v[1], v[2], v[3], v[4], v[5])
        elif kind == "translate" and v:
            n = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif kind == "scale" and v:
            n = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif kind == "rotate" and v:
            r = math.radians(v[0])
            n = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                n = _mul(_mul((1, 0, 0, 1, cx, cy), n), (1, 0, 0, 1, -cx, -cy))
        else:
            continue
        m = _mul(m, n)
    return m


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _advance(cmd: str, x: float, y: float, tokens: list[str], i: int) -> tuple[float, float, int]:
    """Point suivant d'un chemin SVG pour M/L/H/V (absolu ou relatif) et index consommé."""
    if cmd in "Hh":
        nx = float(tokens[i])
        return (x + nx if cmd == "h" else nx), y, i + 1
    if cmd in "Vv":
        ny = float(tokens[i])
        return x, (y + ny if cmd == "v" else ny), i + 1
    nx, ny = float(tokens[i]), float(tokens[i + 1])
    if cmd.islower():
        nx, ny = x + nx, y + ny
    return nx, ny, i + 2


def _path_points(d: str) -> list[list[tuple[float, float]]]:
    """Sous-chemins d'un `d` SVG : M/L/H/V/Z absolus et relatifs ; une courbe coupe le trait."""
    tokens = re.findall(r"[MmLlHhVvZzCcSsQqTtAa]|" + _NUMBER.pattern, d)
    subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cmd = ""
    x = y = 0.0
    start = (0.0, 0.0)
    i = 0

    def close() -> None:
        nonlocal current
        if current:
            subpaths.append(current)
        current = []

    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            cmd = token
            i += 1
            if cmd in "Zz":
                if current:
                    current.append(start)
                close()
                x, y = start
            continue
        if cmd not in "MmLlHhVv" or i + (1 if cmd in "HhVv" else 2) > len(tokens):
            # Courbes et arcs : hors périmètre ; leurs nombres sont sautés, le trait coupé.
            i += 1
            close()
            continue
        x, y, i = _advance(cmd, x, y, tokens, i)
        if cmd in "Mm":
            close()
            start = (x, y)
            cmd = "L" if cmd == "M" else "l"
        current.append((x, y))
    close()
    return [p for p in subpaths if len(p) >= 2]


def _svg_length(value: str | None) -> tuple[float | None, str]:
    """Longueur SVG « 210mm » → (210.0, "mm") ; sans unité → (v, "")."""
    if not value:
        return None, ""
    match = re.match(r"\s*([-+]?\d*\.?\d+)\s*([a-z%]*)", value)
    if not match:
        return None, ""
    return float(match.group(1)), match.group(2)


def _svg_raw(root: Any) -> list[RawSegment]:
    raw: list[RawSegment] = []

    def walk(node: Any, matrix: Matrix, layer: str) -> None:
        tag = node.tag.replace(SVG_NS, "")
        m = _mul(matrix, _parse_transform(node.get("transform")))
        if tag == "g":
            name = node.get(INKSCAPE_LABEL) or node.get("id") or layer
            for child in node:
                walk(child, m, name)
            return
        polylines: list[list[tuple[float, float]]] = []
        if tag == "line":
            polylines = [
                [
                    (float(node.get("x1", 0)), float(node.get("y1", 0))),
                    (float(node.get("x2", 0)), float(node.get("y2", 0))),
                ]
            ]
        elif tag in ("polyline", "polygon"):
            nums = [float(v) for v in _NUMBER.findall(node.get("points", ""))]
            pts = list(zip(nums[0::2], nums[1::2], strict=False))
            if tag == "polygon" and pts:
                pts.append(pts[0])
            polylines = [pts]
        elif tag == "rect":
            x, y = float(node.get("x", 0)), float(node.get("y", 0))
            w, h = float(node.get("width", 0)), float(node.get("height", 0))
            polylines = [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
        elif tag == "path":
            polylines = _path_points(node.get("d", ""))
        for pts in polylines:
            world = [_apply(m, px, py) for px, py in pts]
            for a, b in itertools.pairwise(world):
                raw.append((a, b, layer))
        for child in node:
            walk(child, m, layer)

    walk(root, IDENTITY, "svg")
    return raw


def preview_svg(data: bytes, *, paper_scale: int | None = None) -> PlanImportPreview:
    """SVG : lignes, polylignes, rectangles, chemins droits (transformations comprises) →
    segments en mètres. Unité physique (mm, cm, in) + échelle papier lue ou devinée ; sans
    unité : unités devinées à l'étendue comme un DXF. Calques = groupes nommés."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        msg = f"SVG illisible : {exc}"
        raise DetectionError(msg) from exc
    raw = _svg_raw(root)
    if not raw:
        msg = "aucun tracé (ligne, polyligne, rectangle, chemin droit) dans ce SVG"
        raise DetectionError(msg)
    # Y du SVG vers le bas : on retourne autour du haut du dessin.
    top = max(p[1] for a, b, _ in raw for p in (a, b))
    raw = [((a[0], top - a[1]), (b[0], top - b[1]), layer) for a, b, layer in raw]
    width, unit = _svg_length(root.get("width"))
    view_box = [float(v) for v in _NUMBER.findall(root.get("viewBox", ""))]
    warnings: list[str] = []
    scale: int | None = None
    if width and unit in SVG_UNITS_TO_M and len(view_box) == 4 and view_box[2] > 0:
        paper_per_unit = width * SVG_UNITS_TO_M[unit] / view_box[2]
        text = " ".join(t.text or "" for t in root.iter(f"{SVG_NS}text"))
        scale = paper_scale or paper_scale_from_text(text)
        if scale is None:
            scale, warnings = guess_paper_scale(_extent(raw) * paper_per_unit)
            units = f"{unit} sur papier, échelle 1:{scale} supposée"
        else:
            units = f"{unit} sur papier, échelle 1:{scale}" + (
                " (choisie)" if paper_scale else " (lue sur le plan)"
            )
        factor = paper_per_unit * scale
    else:
        factor, units, warnings = _guess_units(_extent(raw))
    return _preview_from_raw("svg", raw, factor, units, warnings, paper_scale=scale)


# ------------------------------------------------------------------ image de plan (heuristique)
def preview_image(
    project: Project, project_dir: Path, level_id: UUID, image_id: UUID
) -> PlanImportPreview:
    """Traits épais d'une image de plan mise en fond (seuillage, ouverture morphologique,
    Hough) → segments en mètres via l'échelle du fond (outil Cote). Heuristique sans modèle :
    murs `estimated`, à corriger."""
    import cv2
    import numpy as np

    from openplan.pipeline.ingestion import load_image

    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise DetectionError(msg)
    if level.background_image != image_id:
        msg = "mettez d'abord cette image en fond du plan, puis calez-la avec l'outil Cote"
        raise DetectionError(msg)
    transform = level.background_transform
    warnings: list[str] = []
    if "scale" not in transform:
        warnings.append(
            "Échelle du fond non calée (largeur supposée 4 m) : utilisez l'outil Cote avant "
            "de créer les murs, ou corrigez ensuite par calibration."
        )
    m_per_px = float(transform.get("scale", 4.0 / ref.width_px))
    tx, ty = float(transform.get("tx", 0.0)), float(transform.get("ty", 0.0))
    rot = float(transform.get("rotation", 0.0))
    rgb = load_image(project_dir, ref).pixels
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    ink = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    k = max(3, round(min(h, w) / 220))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    # Les bords du trait épais (pas son remplissage) : deux droites par mur, appariées ensuite
    # en un mur dont l'épaisseur est leur écart.
    edges = cv2.Canny(thick, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(40, int(min(h, w) / 12)),
        minLineLength=max(20, int(min(h, w) / 20)),
        maxLineGap=k * 3,
    )
    if lines is None or len(lines) == 0:
        msg = "aucun trait épais trouvé dans l'image : le plan est trop clair ou trop fin"
        raise DetectionError(msg)
    cos, sin = math.cos(rot), math.sin(rot)

    def world(px: float, py: float) -> tuple[float, float]:
        lx, ly = px * m_per_px, (h - py) * m_per_px
        return tx + cos * lx - sin * ly, ty + sin * lx + cos * ly

    raw: list[RawSegment] = [
        (world(float(x0), float(y0)), world(float(x1), float(y1)), "traits épais")
        for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4)
    ]
    raw = _merge_collinear(raw, tol=max(2.0 * m_per_px, 0.01), gap=6 * k * m_per_px)
    warnings.append(
        f"{len(raw)} trait(s) épais relevés par une heuristique (pas un modèle) : les murs créés "
        "seront « estimés », à vérifier et corriger."
    )
    # Déjà en mètres et dans le repère du plan : facteur 1, origine conservée.
    xs = [p[0] for a, b, _ in raw for p in (a, b)]
    ys = [p[1] for a, b, _ in raw for p in (a, b)]
    shifted = [
        ((a[0] - min(xs), a[1] - min(ys)), (b[0] - min(xs), b[1] - min(ys)), layer)
        for a, b, layer in raw
    ]
    preview = _preview_from_raw("image", shifted, 1.0, "mètres (échelle du fond)", warnings)
    # Remettre l'origine du fond : les murs doivent tomber sur le dessin.
    dx, dy = min(xs), min(ys)
    preview.segments = [
        s.model_copy(
            update={
                "start": Point2(x=s.start.x + dx, y=s.start.y + dy),
                "end": Point2(x=s.end.x + dx, y=s.end.y + dy),
            }
        )
        for s in preview.segments
    ]
    preview.bounds = _bounds(preview.segments)
    return preview


def _merge_collinear(raw: list[RawSegment], *, tol: float, gap: float) -> list[RawSegment]:
    """Fusionne les segments colinéaires (même direction à 3°, écart latéral ≤ tol) qui se
    touchent ou se recouvrent (trou ≤ gap) : Hough en rend plusieurs par trait."""
    out: list[RawSegment] = []
    used = [False] * len(raw)
    for i, (a, b, layer) in enumerate(raw):
        if used[i]:
            continue
        used[i] = True
        ux, uy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(ux, uy)
        if n < 1e-9:
            continue
        ux, uy = ux / n, uy / n
        lo, hi = 0.0, n
        changed = True
        while changed:
            changed = False
            for j, (c, d, _l) in enumerate(raw):
                if used[j]:
                    continue
                vx, vy = d[0] - c[0], d[1] - c[1]
                vn = math.hypot(vx, vy)
                if vn < 1e-9 or abs(ux * vx + uy * vy) / vn < math.cos(math.radians(3)):
                    continue
                off = [abs(-uy * (p[0] - a[0]) + ux * (p[1] - a[1])) for p in (c, d)]
                if max(off) > tol:
                    continue
                t = sorted(ux * (p[0] - a[0]) + uy * (p[1] - a[1]) for p in (c, d))
                if t[0] > hi + gap or t[1] < lo - gap:
                    continue
                lo, hi = min(lo, t[0]), max(hi, t[1])
                used[j] = True
                changed = True
        out.append(((a[0] + ux * lo, a[1] + uy * lo), (a[0] + ux * hi, a[1] + uy * hi), layer))
    return out


def _layers(segments: list[PlanSegment]) -> list[PlanLayer]:
    counts: dict[str, int] = {}
    for s in segments:
        counts[s.layer] = counts.get(s.layer, 0) + 1
    hinted = {n for n in counts if any(h in n.lower() for h in WALL_LAYER_HINTS)}
    return [
        PlanLayer(name=n, segments=c, selected=(n in hinted) if hinted else True)
        for n, c in sorted(counts.items())
    ]


def _bounds(segments: list[PlanSegment]) -> Bounds:
    pts = [p for s in segments for p in (s.start, s.end)]
    return Bounds(
        min=Point2(x=min(p.x for p in pts), y=min(p.y for p in pts)),
        max=Point2(x=max(p.x for p in pts), y=max(p.y for p in pts)),
    )


# ------------------------------------------------------------------ double trait
def _frame(s: PlanSegment) -> tuple[float, float, float]:
    """Direction unitaire (ux, uy) et longueur."""
    n = s.length
    return (s.end.x - s.start.x) / n, (s.end.y - s.start.y) / n, n


def _pair(a: PlanSegment, b: PlanSegment) -> PlanSegment | None:
    """Fusionne deux traits parallèles proches en un mur épais (axe médian), sinon None."""
    ux, uy, la = _frame(a)
    vx, vy, _ = _frame(b)
    if abs(ux * vx + uy * vy) < math.cos(math.radians(PARALLEL_DEG)):
        return None
    # Distance perpendiculaire de b à la droite de a.
    nx, ny = -uy, ux
    d1 = (b.start.x - a.start.x) * nx + (b.start.y - a.start.y) * ny
    d2 = (b.end.x - a.start.x) * nx + (b.end.y - a.start.y) * ny
    gap = (d1 + d2) / 2.0
    if abs(d1 - d2) > 0.02 or not (DOUBLE_LINE_MIN_M <= abs(gap) <= DOUBLE_LINE_MAX_M):
        return None
    # Recouvrement le long de a.
    t1 = (b.start.x - a.start.x) * ux + (b.start.y - a.start.y) * uy
    t2 = (b.end.x - a.start.x) * ux + (b.end.y - a.start.y) * uy
    lo, hi = min(t1, t2), max(t1, t2)
    overlap = min(hi, la) - max(lo, 0.0)
    if overlap < 0.5 * min(la, hi - lo):
        return None
    # Axe médian sur l'étendue réunie : aux angles, le trait extérieur dépasse de l'épaisseur,
    # l'axe garde la plus grande étendue pour que les murs se rejoignent.
    start_t, end_t = min(lo, 0.0), max(hi, la)
    half = gap / 2.0
    return PlanSegment(
        start=Point2(
            x=a.start.x + ux * start_t + nx * half, y=a.start.y + uy * start_t + ny * half
        ),
        end=Point2(x=a.start.x + ux * end_t + nx * half, y=a.start.y + uy * end_t + ny * half),
        layer=a.layer,
        thickness=round(abs(gap), 3),
    )


def merge_double_lines(segments: list[PlanSegment]) -> tuple[list[PlanSegment], int]:
    """Apparie les traits parallèles proches d'un même calque ; renvoie (segments, paires)."""
    used = [False] * len(segments)
    out: list[PlanSegment] = []
    merged = 0
    for i, a in enumerate(segments):
        if used[i]:
            continue
        match = None
        for j in range(i + 1, len(segments)):
            if used[j] or segments[j].layer != a.layer:
                continue
            match = _pair(a, segments[j])
            if match is not None:
                used[j] = True
                merged += 1
                break
        used[i] = True
        out.append(match if match is not None else a)
    return _trim_to_corners(out), merged


def _trim_to_corners(segments: list[PlanSegment]) -> list[PlanSegment]:
    """Après fusion, deux axes qui se croisent près de leurs bouts sont raccordés au
    croisement (les dépassements d'angle disparaissent)."""
    pts: list[list[Point2]] = [[s.start, s.end] for s in segments]
    for i, a in enumerate(segments):
        for j, b in enumerate(segments):
            if j <= i:
                continue
            x = _intersection(a, b)
            if x is None:
                continue
            for k, s in ((i, a), (j, b)):
                reach = max((s.thickness or DEFAULT_THICKNESS_M) * 1.5, SNAP_M)
                for end in (0, 1):
                    p = pts[k][end]
                    if math.hypot(p.x - x.x, p.y - x.y) <= reach:
                        pts[k][end] = x
    return [
        PlanSegment(start=p[0], end=p[1], layer=s.layer, thickness=s.thickness)
        for s, p in zip(segments, pts, strict=True)
        if math.hypot(p[1].x - p[0].x, p[1].y - p[0].y) >= MIN_SEGMENT_M
    ]


def _intersection(a: PlanSegment, b: PlanSegment) -> Point2 | None:
    ux, uy, _ = _frame(a)
    vx, vy, _ = _frame(b)
    den = ux * vy - uy * vx
    if abs(den) < 1e-6:
        return None
    dx, dy = b.start.x - a.start.x, b.start.y - a.start.y
    t = (dx * vy - dy * vx) / den
    return Point2(x=a.start.x + ux * t, y=a.start.y + uy * t)


# ------------------------------------------------------------------ application
def apply_segments(
    project: Project, level_id: UUID, body: PlanApplyBody, *, run_id: UUID | None = None
) -> tuple[Project, StepResult]:
    """Les segments retenus deviennent des murs `observed` d'un niveau vide (un commit)."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = (
            "le niveau contient déjà des murs : l'import de plan ne s'applique qu'à un niveau vide"
        )
        raise DetectionError(msg)
    rid = run_id or uuid4()
    step = f"plan_import.{body.kind}"
    source = Source.OBSERVED if body.source == "observed" else Source.ESTIMATED
    confidence = OBSERVED_CONFIDENCE if source is Source.OBSERVED else ESTIMATED_CONFIDENCE
    sigma = OBSERVED_SIGMA_M if source is Source.OBSERVED else ESTIMATED_SIGMA_M
    provenance = Provenance(step=step, run_id=rid)
    vertices: list[Vertex] = []

    def vertex_at(p: Point2) -> UUID:
        for v in vertices:
            if math.hypot(v.position.x - p.x, v.position.y - p.y) <= SNAP_M:
                return v.id
        v = Vertex(
            position=Point2(x=round(p.x, 3), y=round(p.y, 3)),
            source=source,
            confidence=confidence,
            uncertainty=Uncertainty(sigma=sigma),
        )
        vertices.append(v)
        return v.id

    walls: list[Wall] = []
    skipped = 0
    for index, s in enumerate(body.segments, start=1):
        start = Point2(x=s.start.x * body.scale, y=s.start.y * body.scale)
        end = Point2(x=s.end.x * body.scale, y=s.end.y * body.scale)
        a, b = vertex_at(start), vertex_at(end)
        if a == b:
            skipped += 1
            continue
        walls.append(
            Wall(
                name=f"Mur {index}",
                start_vertex=a,
                end_vertex=b,
                thickness=s.thickness or DEFAULT_THICKNESS_M,
                source=source,
                confidence=confidence,
                uncertainty=Uncertainty(sigma=sigma),
                provenance=provenance,
            )
        )
    if not walls:
        msg = "aucun mur exploitable dans les segments retenus"
        raise DetectionError(msg)
    new_level = level.model_copy(
        update={
            "vertices": {**level.vertices, **{v.id: v for v in vertices}},
            "elements": {**level.elements, **{w.id: w for w in walls}},
        }
    )
    new_level = detect_rooms(new_level)
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    label = f"Importer un plan ({KIND_LABELS.get(body.kind, body.kind)})"
    after = history.commit(project, after, actor=ACTOR, label=label, run_id=rid)
    warnings = [f"{skipped} segment(s) trop court(s) ignoré(s)."] if skipped else []
    text = (
        "Murs relevés sur le document importé (observés) : l'échelle vient du fichier, "
        "vérifiez une longueur connue et calibrez si besoin."
        if source is Source.OBSERVED
        else "Murs devinés dans l'image par une heuristique (estimés) : vérifiez chaque mur, "
        "déplacez, supprimez, calibrez."
    )
    return after, StepResult(
        step=step,
        patch=after.history[-1].patch,
        confidence=confidence,
        assumptions=[Assumption(text=text, impact="medium")],
        warnings=warnings,
        duration_s=time.perf_counter() - started,
    )
