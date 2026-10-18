"""Étape « rendering.photo » : rendu Blender avec matières depuis la caméra estimée de la photo.

La scène (murs, pentes, sol) est rendue par Cycles au point de vue de la caméra de la photo,
avec des matières procédurales (bois, pierre, enduit : aucune texture externe). Le résultat
est une image de synthèse de la géométrie **estimée**, pas une retouche de la photo ; elle
hérite des incertitudes de la scène. Sans Blender, l'erreur est explicite et le viewer GLB
« matières » reste le repli.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from shapely.geometry import Point, Polygon

from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.blender_bridge import BlenderError, run_blender
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.finish import FinishEntry, read_finish
from openplan.pipeline.inspire import InspirationEntry, read_inspiration
from openplan.pipeline.layouts import LayoutProposal
from openplan.scene.ops import AddCamera
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point2, Point3, StrictModel
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import (
    DOOR_KINDS,
    GLAZED_KINDS,
    Camera,
    Furniture,
    Opening,
    RoofWindow,
    Room,
)
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import clearance_at

STEP = "rendering.photo"
STEP_VIEW = "rendering.view"
DEFAULT_SIZE = (1280, 960)
PORTRAIT_SIZE = (1024, 1280)
"""Format 4:5 (V1) d'un point de vue marqué `metadata["aspect"] = "4:5"`."""
DEFAULT_FOCAL_RATIO = 0.8
"""Focale par défaut en fraction de la largeur, comme l'estimateur EXIF."""
MAX_RENDER_PX = 1600
"""Grand côté maximal du rendu : au-delà, la photo est réduite (temps de rendu)."""


class NoCameraError(ValueError):
    """Aucune caméra dans le niveau : rien à rendre « depuis la photo »."""


def photo_camera(project: Project, level_id: UUID) -> tuple[Camera, RenderCamera]:
    """Caméra du niveau (de préférence celle liée à une photo) et sa version pour le rendu."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    cameras = [e for e in level.elements.values() if isinstance(e, Camera) and e.visible]
    if not cameras:
        msg = (
            "aucune caméra dans le niveau : crée d'abord les combles ou une proposition "
            "depuis la photo"
        )
        raise NoCameraError(msg)
    camera = next((c for c in cameras if c.image_id is not None), cameras[0])
    width, height = DEFAULT_SIZE
    if camera.image_id is not None and camera.image_id in project.images:
        ref = project.images[camera.image_id]
        width, height = ref.width_px, ref.height_px
    scale = min(1.0, MAX_RENDER_PX / max(width, height))
    focal = camera.focal_px if camera.focal_px is not None else DEFAULT_FOCAL_RATIO * width
    return camera, RenderCamera(
        position=camera.position,
        yaw=camera.yaw,
        pitch=camera.pitch,
        roll=camera.roll,
        focal_px=focal * scale,
        width_px=max(16, round(width * scale)),
        height_px=max(16, round(height * scale)),
        checker_floor=False,
    )


def camera_view(project: Project, level_id: UUID, camera_id: UUID) -> tuple[Camera, RenderCamera]:
    """Une caméra précise du niveau (photo ou point de vue libre) et sa version pour le rendu :
    une caméra libre rend en 1280 × 960 avec la focale par défaut."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    camera = level.elements.get(camera_id)
    if not isinstance(camera, Camera):
        msg = f"point de vue inconnu : {camera_id}"
        raise NoCameraError(msg)
    width, height = PORTRAIT_SIZE if camera.metadata.get("aspect") == "4:5" else DEFAULT_SIZE
    if camera.image_id is not None and camera.image_id in project.images:
        ref = project.images[camera.image_id]
        width, height = ref.width_px, ref.height_px
    scale = min(1.0, MAX_RENDER_PX / max(width, height))
    # La focale d'un point de vue libre est donnée pour 1280 px de grand côté, en paysage
    # comme en portrait.
    focal = camera.focal_px if camera.focal_px is not None else DEFAULT_FOCAL_RATIO * width
    return camera, RenderCamera(
        position=camera.position,
        yaw=camera.yaw,
        pitch=camera.pitch,
        roll=camera.roll,
        focal_px=focal * scale,
        width_px=max(16, round(width * scale)),
        height_px=max(16, round(height * scale)),
        checker_floor=False,
    )


class RenderEntry(StrictModel):
    """Un rendu réalisé (galerie de l'écran Rendre) : lu dans `render.json` à côté de l'image."""

    run_id: UUID
    camera_id: UUID | None
    camera_name: str
    samples: int
    device: str
    render_s: float | None
    width_px: int
    height_px: int
    created_at: datetime
    image: str = "preview.png"
    daylight: str | None = None
    """Préréglage d'éclairage (matin, midi, soir, nuit) ; None = éclairage d'atelier neutre."""
    north_deg: float = 90.0
    variant: int | None = None
    """Vignette d'une variante d'aménagement (3.7) : son numéro ; None = rendu de la scène."""
    finished: FinishEntry | None = None
    """Finition IA (D5) faite sur ce rendu, s'il y en a une (image `generated` à côté)."""
    section: str | None = None
    """Rendu en coupe (E3) : « horizontale à 1,20 m », « verticale x = 3,00 m » ; None sinon."""
    inspiration: InspirationEntry | None = None
    """Sortie B (V1) : l'image d'inspiration, `generated`, « cotes non garanties »."""
    catalogue: InspirationEntry | None = None
    """« Version catalogue » demandée (V1.7b) : txt2img, « matières libres »."""
    level_id: UUID | None = None
    """T7e : le plan (niveau) rendu ; absent des rendus d'avant, retrouvé alors par la caméra."""


RENDER_SIDECAR = "render.json"


def section_label(section: tuple[str, float] | None) -> str | None:
    """« horizontale à 1,20 m », « verticale x = 3,00 m » (galerie)."""
    if section is None:
        return None
    axis, value = section
    shown = f"{value:.2f}".replace(".", ",")
    return f"horizontale à {shown} m" if axis == "z" else f"verticale {axis} = {shown} m"


def section_keeps_above(section: tuple[str, float] | None, camera: RenderCamera) -> bool:
    """Coupe verticale au rendu : on retire la moitié où se trouve la caméra, sinon elle
    regarderait le vide. La coupe horizontale garde toujours le dessous."""
    if section is None or section[0] == "z":
        return False
    position = camera.position.x if section[0] == "x" else camera.position.y
    return position < section[1]


def render_expectation(device: str, samples: int, pixels: int = 1280 * 960) -> tuple[float, str]:
    """Durée attendue d'un rendu Cycles, d'après le protocole (RTX PRO 500 : 64 éch. en 8–10 s à
    1280 × 960 ; processeur : 43 s), mise à l'échelle des échantillons et des pixels."""
    factor = (samples / 64.0) * (pixels / (1280.0 * 960.0))
    if device == "cuda":
        expected = 4.0 + 6.0 * factor
        return expected, f"Rendu sur GPU : environ {expected:.0f} s attendues."
    expected = 8.0 + 36.0 * factor
    return expected, (
        f"Rendu sur processeur (aucun GPU vu) : environ {expected:.0f} s. "
        "La page reste utilisable pendant ce temps."
    )


# ------------------------------------------------------------------ éclairage (heure du jour)
Daylight = Literal["matin", "midi", "heure_doree", "soir", "nuit"]
DAYLIGHT_LABELS: dict[str, str] = {
    "matin": "matin (soleil bas à l'est)",
    "midi": "midi (soleil haut au sud)",
    "heure_doree": "heure dorée (soleil bas et chaud, fin de journée)",
    "soir": "soir (soleil bas à l'ouest)",
    "nuit": "nuit (lampes intérieures)",
}


@dataclass(frozen=True)
class _Preset:
    """Cap boussole du soleil (0 = nord, 90 = est ; None = pas de soleil), hauteur, température,
    énergie ; ciel ; lampes intérieures (le soleil n'entre que par les baies)."""

    compass: float | None
    elevation: float
    kelvin: float
    energy: float
    sky: tuple[float, float, float]
    sky_strength: float
    key: float
    fill: float
    tint_sky: bool = False
    """Teinter le ciel HDRI installé par `sky` (heure dorée, H4) ; sinon le ciel garde ses
    couleurs."""
    sun_angle: float = 1.5
    """Diamètre apparent du soleil (degrés) : plus grand, les ombres s'adoucissent."""
    fill_kelvin: float | None = None
    """Température de l'appoint intérieur ; None = blanc."""


_PRESETS: dict[str, _Preset] = {
    "matin": _Preset(100.0, 22.0, 4200.0, 3.0, (0.75, 0.80, 0.92), 0.9, 100.0, 25.0),
    "midi": _Preset(180.0, 58.0, 5800.0, 4.5, (0.62, 0.74, 0.92), 1.0, 60.0, 20.0),
    # H4 : fin de journée douce, plus haute et moins orangée que le soir, ciel chaud et clair.
    "heure_doree": _Preset(
        245.0, 16.0, 3000.0, 5.0, (1.0, 0.70, 0.46), 0.9, 110.0, 45.0, True, 4.0, 2700.0
    ),
    "soir": _Preset(260.0, 10.0, 2900.0, 2.2, (0.85, 0.62, 0.45), 0.6, 150.0, 40.0),
    "nuit": _Preset(None, 0.0, 0.0, 0.0, (0.02, 0.03, 0.07), 0.25, 450.0, 80.0),
}


def _unit(v: float) -> float:
    return max(0.0, min(255.0, v)) / 255.0


def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Couleur (0–1) d'une lumière de température donnée (approximation de Tanner Helland)."""
    t = max(1000.0, min(40000.0, kelvin)) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    if t <= 66:
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        g = 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return _unit(r), _unit(g), _unit(b)


def describe_north(north_deg: float) -> str:
    """« nord vers le haut du plan » (90°), la droite (0°), le bas (270°), la gauche (180°)."""
    names = {
        90: "le haut du plan",
        0: "la droite du plan",
        270: "le bas du plan",
        180: "la gauche du plan",
    }
    rounded = round(north_deg) % 360
    return f"nord vers {names[rounded]}" if rounded in names else f"nord à {north_deg:.0f}°"


def daylight_spec(preset: Daylight, north_deg: float) -> dict[str, Any]:
    """Éclairage prêt pour Blender : soleil (direction dans le repère du plan, énergie,
    couleur), ciel, lampes intérieures. L'azimut boussole devient un angle du plan grâce au
    nord du niveau (0° = +X, antihoraire ; 90° = nord en haut)."""
    p = _PRESETS[preset]
    sun: dict[str, Any] | None = None
    if p.compass is not None:
        theta = math.radians(north_deg - p.compass)
        el = math.radians(p.elevation)
        sun = {
            "direction": [
                math.cos(theta) * math.cos(el),
                math.sin(theta) * math.cos(el),
                math.sin(el),
            ],
            "elevation_deg": p.elevation,
            "compass_deg": p.compass,
            "energy": p.energy,
            "color": list(kelvin_to_rgb(p.kelvin)),
            "angle_deg": p.sun_angle,
        }
    return {
        "preset": preset,
        "label": DAYLIGHT_LABELS[preset],
        "north_deg": north_deg,
        "north": describe_north(north_deg),
        "sun": sun,
        "sky_color": list(p.sky),
        "sky_strength": p.sky_strength,
        "interior_key": p.key,
        "interior_fill": p.fill,
        "sky_tint": p.tint_sky,
        "fill_color": list(kelvin_to_rgb(p.fill_kelvin)) if p.fill_kelvin else None,
    }


def render_camera_view(
    project: Project,
    level_id: UUID,
    camera_id: UUID,
    out_dir: Path,
    *,
    blender_bin: str | None = None,
    samples: int = 64,
    gpu: bool = True,
    run_id: UUID | None = None,
    daylight: Daylight | None = None,
    section: tuple[str, float] | None = None,
) -> StepResult:
    """Rendu Cycles avec matières depuis un point de vue donné ; `daylight` place le soleil
    d'après le nord du niveau ; `section` (axe z, x ou y, position) le rend en coupe (E3) ;
    écrit `render.json` à côté de l'image pour la galerie."""
    camera, render_camera = camera_view(project, level_id, camera_id)
    north = project.levels[level_id].north_deg
    light = default_light(project.levels[level_id], daylight, north)
    keep_above = section_keeps_above(section, render_camera)
    if light is not None:
        light = auto_exposed(
            project,
            level_id,
            render_camera,
            light,
            out_dir,
            blender_bin=blender_bin,
            gpu=gpu,
            section=section,
            section_keep_above=keep_above,
        )
    result = run_blender(
        project,
        level_id,
        out_dir,
        blender_bin=blender_bin,
        preview=True,
        run_id=run_id,
        camera=render_camera,
        samples=samples,
        denoise=True,
        style="realistic",
        gpu=gpu,
        light=light,
        depth=True,  # la finition IA (D5) se guide par la profondeur du rendu
        section=section,
        section_keep_above=keep_above,
        backdrop=section is not None and section[0] != "z",
        idmap=True,  # P4 : désigner un meuble dans le rendu
    )
    preview = next((a for a in result.artifacts if a.name == "preview.png"), None)
    if preview is None:
        msg = "Blender n'a pas produit d'image"
        raise BlenderError(msg)
    device = next((m.split("@", 1)[1] for m in result.model_ids if m.startswith("cycles@")), "?")
    render_s = _render_seconds(preview.description)
    entry = RenderEntry(
        run_id=run_id or uuid4(),
        camera_id=camera.id,
        level_id=level_id,
        camera_name=camera.name or ("Photo" if camera.image_id else "Point de vue"),
        samples=samples,
        device=device,
        render_s=render_s,
        width_px=render_camera.width_px,
        height_px=render_camera.height_px,
        created_at=datetime.now(UTC),
        daylight=daylight,
        north_deg=north,
        section=section_label(section),
    )
    (out_dir / RENDER_SIDECAR).write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _note_gallery(out_dir, project, level_id, camera, None)
    assumptions = [
        Assumption(
            text="Image de synthèse de la géométrie estimée, pas une photo : dimensions et point "
            "de vue héritent des incertitudes de la scène.",
            impact="high",
        ),
        Assumption(
            text=f"Point de vue « {entry.camera_name} » ({camera.source.value}), "
            f"{render_camera.width_px} × {render_camera.height_px} px, {samples} échantillons.",
            impact="medium",
        ),
    ]
    return result.model_copy(update={"step": STEP_VIEW, "assumptions": assumptions})


# ------------------------------------------------------------------ vignette d'une variante (3.7)
STEP_VARIANT = "rendering.variant"
VARIANT_SIZE = (640, 480)
VARIANT_SAMPLES = 32
_CORNER_INSET_M = 0.45
_EYE_MAX_M = 2.2
_LOOK_AT_Z = 0.8
_OVERVIEW_MIN_HEADROOM_M = 1.8
"""Hauteur libre minimale sous la pente là où se tient la caméra de vignette."""


def _room_shape(level: Level) -> tuple[Polygon | None, tuple[float, float, float, float] | None]:
    """Polygone de la plus grande pièce fermée (s'il y en a une) et l'emprise des sommets."""
    graph = WallGraph.from_level(level)
    best: Polygon | None = None
    for room in (e for e in level.elements.values() if isinstance(e, Room)):
        try:
            polygon = graph.room_polygon(room.id)
        except GeometryError:
            continue
        if not polygon.is_empty and (best is None or polygon.area > best.area):
            best = polygon
    if best is not None:
        return best, best.bounds
    if level.vertices:
        xs = [v.position.x for v in level.vertices.values()]
        ys = [v.position.y for v in level.vertices.values()]
        return None, (min(xs), min(ys), max(xs), max(ys))
    return None, None


# ------------------------------------------------------------------ D3 : lumière du jour équilibrée
SKYLIGHT_GAIN = 1.5
"""Un velux éclaire environ une fois et demie plus qu'une fenêtre de même surface."""
TARGET_GLAZING_RATIO = 0.30
"""Surface vitrée / surface au sol qui se passe d'exposition : en dessous, l'image est
éclaircie comme le fait un photographe d'intérieur (1 EV par moitié de vitrage en moins)."""
MAX_EXPOSURE_EV = 2.5


def daylight_apertures(level: Level) -> float:
    """Surface par laquelle le jour entre (m²) : fenêtres, et velux pondérés."""
    total = 0.0
    for e in level.elements.values():
        if not e.visible:
            continue
        if isinstance(e, Opening) and e.kind in GLAZED_KINDS:
            total += e.width * e.height
        elif isinstance(e, RoofWindow):
            total += SKYLIGHT_GAIN * e.width * e.length
    return total


def _floor_area(level: Level) -> float:
    derived = compute_derived(level)
    area = sum(r.area_m2 for r in derived.rooms.values())
    if area <= 0.0 and derived.bounds is not None:
        b = derived.bounds
        area = (b.max.x - b.min.x) * (b.max.y - b.min.y)
    return area


def balanced_light(spec: dict[str, Any], level: Level) -> dict[str, Any]:
    """Le jour d'abord (D3) : quand la pièce a des fenêtres ou des velux, les lampes d'appoint
    ne font plus qu'un faible remplissage et l'exposition compense une pièce peu vitrée (comme un
    photographe d'intérieur) ; la nuit et les pièces aveugles gardent leurs lampes."""
    area = daylight_apertures(level)
    if spec.get("sun") is None or area <= 0.0:
        return {**spec, "exposure": 0.0, "balanced": False}
    floor = max(_floor_area(level), 1.0)
    ratio = area / floor
    exposure = min(MAX_EXPOSURE_EV, max(0.0, math.log2(TARGET_GLAZING_RATIO / ratio)))
    return {
        **spec,
        # Pas de lampe au plafond (elle fait une tache vive) ; un faible appoint près de l'œil.
        "interior_key": 0.0,
        "interior_fill": spec["interior_fill"] * 0.5,
        "exposure": round(exposure, 2),
        "balanced": True,
        "glazing_ratio": round(ratio, 3),
    }


PROBE_WIDTH_PX = 200
PROBE_SAMPLES = 8
TARGET_MEAN = 110.0
"""Luminosité moyenne visée (0–255, sRGB) : une photo d'intérieur claire, sans brûler le ciel."""
EXPOSURE_RANGE = (-1.0, 5.0)


def exposure_correction(mean: float) -> float:
    """EV à ajouter pour amener une moyenne sRGB mesurée vers TARGET_MEAN (la courbe sRGB
    double la valeur affichée pour ≈ 2,2 EV de lumière)."""
    return max(-2.0, min(4.0, 2.2 * math.log2(TARGET_MEAN / max(mean, 1.0))))


def auto_exposed(
    project: Project,
    level_id: UUID,
    camera: RenderCamera,
    light: dict[str, Any],
    work_dir: Path,
    *,
    blender_bin: str | None = None,
    gpu: bool = True,
    section: tuple[str, float] | None = None,
    section_keep_above: bool = False,
) -> dict[str, Any]:
    """Exposition automatique (D3) : un rendu d'essai minuscule mesure la luminosité, puis
    l'exposition est corrigée comme le ferait un appareil photo. Lumière non équilibrée
    (nuit, pièce aveugle) : inchangée."""
    if not light.get("balanced"):
        return light
    from PIL import Image

    ratio = PROBE_WIDTH_PX / camera.width_px
    probe = camera.model_copy(
        update={
            "width_px": PROBE_WIDTH_PX,
            "height_px": max(16, round(camera.height_px * ratio)),
            "focal_px": camera.focal_px * ratio,
        }
    )
    out = work_dir / "exposure_probe"
    run_blender(
        project,
        level_id,
        out,
        blender_bin=blender_bin,
        preview=True,
        camera=probe,
        samples=PROBE_SAMPLES,
        denoise=True,
        style="realistic",
        gpu=gpu,
        light=light,
        section=section,
        section_keep_above=section_keep_above,
    )
    with Image.open(out / "preview.png") as img:
        gray = img.convert("L")
        mean = sum(i * n for i, n in enumerate(gray.histogram())) / max(1, gray.width * gray.height)
    exposure = float(light.get("exposure", 0.0)) + exposure_correction(mean)
    exposure = max(EXPOSURE_RANGE[0], min(EXPOSURE_RANGE[1], exposure))
    return {**light, "exposure": round(exposure, 2), "probe_mean": round(mean, 1)}


def default_light(
    level: Level, daylight: Daylight | None, north_deg: float
) -> dict[str, Any] | None:
    """La lumière d'un rendu : l'heure demandée, équilibrée ; sans heure demandée, le jour de
    midi si la pièce a des ouvertures, sinon l'éclairage d'atelier d'avant (None)."""
    if daylight is None and daylight_apertures(level) <= 0.0:
        return None
    spec = daylight_spec(daylight or "midi", north_deg)
    return balanced_light(through_skylights(spec, level), level)


SKYLIGHT_TURN_DEG = 60.0
"""Le soleil peut tourner jusqu'à 60° de sa direction pour entrer par un velux (H2)."""


def through_skylights(spec: dict[str, Any], level: Level) -> dict[str, Any]:
    """Velux (H2) : le soleil se tourne vers la pente qui porte le plus de velux, sans s'écarter
    de plus de 60° de l'heure choisie, pour qu'une flaque de jour tombe dans la pièce comme
    dans les références ; sa hauteur et sa couleur ne changent pas."""
    sun = spec.get("sun")
    if sun is None:
        return spec
    areas: dict[Any, float] = {}
    for e in level.elements.values():
        if isinstance(e, RoofWindow) and e.visible:
            areas[e.roof] = areas.get(e.roof, 0.0) + e.width * e.length
    if not areas:
        return spec
    roofs = derived_roofs(level, WallGraph.from_level(level))
    roof_id = max(areas, key=lambda r: areas[r])
    roof = roofs.get(roof_id)
    if roof is None:
        return spec
    dx, dy, dz = sun["direction"]
    current = math.atan2(dy, dx)
    outward = math.atan2(-roof.inward.y, -roof.inward.x)
    delta = math.atan2(math.sin(outward - current), math.cos(outward - current))
    limit = math.radians(SKYLIGHT_TURN_DEG)
    turned = current + max(-limit, min(limit, delta))
    flat = math.hypot(dx, dy)
    direction = [math.cos(turned) * flat, math.sin(turned) * flat, dz]
    return {
        **spec,
        "sun": {
            **sun,
            "direction": direction,
            "skylight_turn_deg": round(math.degrees(turned - current), 1),
        },
    }


# ------------------------------------------------------------------ D3 : vue d'architecte
ARCHITECT_NAME = "Vue d'architecte"
ARCHITECT_FOCAL_PX = 1280 / 2.0 / math.tan(math.radians(40.0))
"""Champ horizontal de 80° pour 1280 px : grand angle d'intérieur, sans déformation forte."""
_ARCHITECT_INSET_M = 0.45
_ARCHITECT_EYE_M = 1.5


def _architect_axis(
    level: Level, polygon: Polygon, roofs: list[DerivedRoof]
) -> tuple[float, float, float, float, float]:
    """(ux, uy, cx, cy, longueur) : l'axe de visée et le milieu d'où on le parcourt. Sous une
    toiture, l'axe suit le faîtage (d'un pignon vers l'autre, le cadrage des photos de grenier)
    et le milieu est sous la plus grande hauteur libre ; sinon, la longueur de la pièce."""
    if roofs:
        a, b = roofs[0].eave
        theta = math.atan2(b.y - a.y, b.x - a.x)
    else:
        coords = list(polygon.minimum_rotated_rectangle.exterior.coords)
        (x0, y0), (x1, y1), (x2, y2) = coords[0], coords[1], coords[2]
        if math.hypot(x1 - x0, y1 - y0) >= math.hypot(x2 - x1, y2 - y1):
            theta = math.atan2(y1 - y0, x1 - x0)
        else:
            theta = math.atan2(y2 - y1, x2 - x1)
    ux, uy = math.cos(theta), math.sin(theta)
    pts = list(polygon.exterior.coords)
    along = [x * ux + y * uy for x, y in pts]
    across = [-x * uy + y * ux for x, y in pts]
    mid_u, mid_v = (max(along) + min(along)) / 2.0, (max(across) + min(across)) / 2.0
    if roofs:
        lo, hi = min(across), max(across)
        mid_v = max(
            (clearance_at((mid_u * ux - v * uy, mid_u * uy + v * ux), roofs, level), v)
            for v in [lo + k * (hi - lo) / 40 for k in range(41)]
        )[1]
    return ux, uy, mid_u * ux - mid_v * uy, mid_u * uy + mid_v * ux, max(along) - min(along)


def _door_side(level: Level, graph: WallGraph, cx: float, cy: float, ux: float, uy: float) -> float:
    """Côté de l'axe où se trouve la porte (on entre par là) ; −1 sans porte."""
    for e in level.elements.values():
        if isinstance(e, Opening) and e.kind in DOOR_KINDS and e.host_wall in graph.walls:
            a, b = graph.wall_axis(e.host_wall)
            t = (e.offset + e.width / 2.0) / max(graph.wall_length(e.host_wall), 1e-9)
            dx, dy = a.x + (b.x - a.x) * t - cx, a.y + (b.y - a.y) * t - cy
            return 1.0 if dx * ux + dy * uy > 0 else -1.0
    return -1.0


def architect_view(project: Project, level_id: UUID) -> AddCamera:
    """Point de vue frontal et centré (D3) : au bout de l'axe de la pièce (le faîtage sous une
    toiture), à hauteur d'œil, grand angle, un peu relevé sous une toiture. On entre par le côté
    de la porte quand il y en a une. Renvoie l'opération à appliquer (geste annulable)."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    polygon, _ = _room_shape(level)
    if polygon is None:
        msg = "tracez d'abord les murs et détectez la pièce : la vue d'architecte la cadre"
        raise NoCameraError(msg)
    graph = WallGraph.from_level(level)
    roofs = list(derived_roofs(level, graph).values())
    ux, uy, cx, cy, length = _architect_axis(level, polygon, roofs)
    side = _door_side(level, graph, cx, cy, ux, uy)
    # Pièce non rectangulaire ou toiture basse : on revient vers le centre jusqu'à être dans la
    # pièce, à 30 cm au moins des murs, avec la hauteur d'œil sous la pente.
    inner = polygon.buffer(-0.3)
    if inner.is_empty:
        inner = polygon
    if not inner.contains(Point(cx, cy)):
        p = inner.representative_point()
        cx, cy = float(p.x), float(p.y)

    def fits(px: float, py: float) -> bool:
        if not inner.contains(Point(px, py)):
            return False
        return not roofs or clearance_at((px, py), roofs, level) >= _ARCHITECT_EYE_M + 0.3

    back = max(length / 2.0 - _ARCHITECT_INSET_M, 0.0)
    x, y = cx + side * ux * back, cy + side * uy * back
    while back > 0.0 and not fits(x, y):
        back = max(0.0, back - 0.1)
        x, y = cx + side * ux * back, cy + side * uy * back
    if not inner.contains(Point(x, y)):
        x, y = cx, cy
    clearance = clearance_at((x, y), roofs, level) if roofs else level.height
    return AddCamera(
        position=Point2(x=round(x, 3), y=round(y, 3)),
        yaw=round(math.atan2(-side * uy, -side * ux), 4),
        height=round(max(1.0, min(_ARCHITECT_EYE_M, clearance - 0.25)), 3),
        name=ARCHITECT_NAME,
        pitch=math.radians(6.0) if roofs else 0.0,
        focal_px=round(ARCHITECT_FOCAL_PX, 1),
    )


SECTION_VIEW_FOCAL_PX = 1100.0
"""Focale de la vue de coupe pour 1280 px de large (environ 60° d'angle horizontal)."""
SECTION_VIEW_MARGIN_M = 0.6


def section_view_name(axis: str, value: float) -> str:
    return f"Coupe {axis} = {value:.2f} m".replace(".", ",")


def section_view(project: Project, level_id: UUID, axis: str, value: float) -> AddCamera:
    """Perspective de coupe d'architecte (E3b) : caméra perpendiculaire au plan de coupe
    vertical, face à la tranche, reculée du côté retiré juste assez pour cadrer toute la coupe
    (largeur et hauteur, marge comprise), à mi-hauteur. Renvoie l'opération à appliquer."""
    from openplan.pipeline.section import section_profile

    if axis not in ("x", "y"):
        msg = "la vue de coupe se fait sur une coupe verticale (axe x ou y)"
        raise NoCameraError(msg)
    profile = section_profile(project, level_id, axis, value)  # type: ignore[arg-type]
    bounds = profile.bounds
    if bounds is None:
        msg = "le plan de coupe ne traverse rien : déplacez-le dans la pièce"
        raise NoCameraError(msg)
    umin, vmin, umax, vmax = bounds
    half_w = (umax - umin) / 2.0 + SECTION_VIEW_MARGIN_M
    half_h = (vmax - vmin) / 2.0 + SECTION_VIEW_MARGIN_M
    f = SECTION_VIEW_FOCAL_PX
    distance = max(half_w * f / 640.0, half_h * f / 480.0)
    mid_u, mid_v = (umin + umax) / 2.0, (vmin + vmax) / 2.0
    # Côté gardé : en deçà du plan ; la caméra se tient au-delà et regarde vers les valeurs
    # décroissantes de l'axe.
    if axis == "x":
        x, y, yaw = value + distance, mid_u, math.pi
    else:
        x, y, yaw = mid_u, value + distance, -math.pi / 2
    return AddCamera(
        position=Point2(x=round(x, 3), y=round(y, 3)),
        yaw=round(yaw, 4),
        height=round(max(0.5, mid_v), 3),
        name=section_view_name(axis, value),
        pitch=0.0,
        focal_px=SECTION_VIEW_FOCAL_PX,
    )


def overview_camera(
    level: Level, furniture: list[Furniture], size: tuple[int, int] = VARIANT_SIZE
) -> RenderCamera:
    """Point de vue de vignette : dans la pièce, dans le coin le plus éloigné des meubles, sous
    le plafond, grand angle, regardant les meubles à hauteur de meuble (le centre de la pièce
    s'il n'y en a pas). Sans pièce, l'emprise des murs sert de cadre."""
    polygon, bounds = _room_shape(level)
    if bounds is None:
        if not furniture:
            msg = "rien à cadrer : ni mur ni meuble"
            raise NoCameraError(msg)
        xs = [f.transform.position.x for f in furniture]
        ys = [f.transform.position.y for f in furniture]
        bounds = (min(xs) - 1.5, min(ys) - 1.5, max(xs) + 1.5, max(ys) + 1.5)
    minx, miny, maxx, maxy = bounds
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    if furniture:
        # La vignette regarde les meubles, depuis le coin qui en est le plus loin.
        cx = sum(f.transform.position.x for f in furniture) / len(furniture)
        cy = sum(f.transform.position.y for f in furniture) / len(furniture)
    corners = [
        (minx + _CORNER_INSET_M, miny + _CORNER_INSET_M),
        (maxx - _CORNER_INSET_M, miny + _CORNER_INSET_M),
        (minx + _CORNER_INSET_M, maxy - _CORNER_INSET_M),
        (maxx - _CORNER_INSET_M, maxy - _CORNER_INSET_M),
    ]
    graph = WallGraph.from_level(level)
    roofs = list(derived_roofs(level, graph).values()) if graph.walls else []

    def headroom(px: float, py: float) -> float:
        return clearance_at((px, py), roofs, level) if roofs else level.height

    # Sous une toiture dont l'égout descend au sol, les coins sont trop bas : on part du coin
    # le plus éloigné des meubles et on revient vers eux jusqu'à tenir sous la pente.
    inside = [c for c in corners if polygon is None or polygon.contains(Point(*c))]
    if inside:
        x, y = max(inside, key=lambda c: math.hypot(c[0] - cx, c[1] - cy))
    elif polygon is not None:
        p = polygon.representative_point()
        x, y = float(p.x), float(p.y)
    else:
        x, y = corners[0]
    x0, y0 = x, y
    for k in range(21):
        t = k / 20.0
        px, py = x0 + (cx - x0) * t, y0 + (cy - y0) * t
        if headroom(px, py) >= _OVERVIEW_MIN_HEADROOM_M:
            x, y = px, py
            break
    z = min(level.height - 0.3, _EYE_MAX_M, headroom(x, y) - 0.3)
    horizontal = max(math.hypot(cx - x, cy - y), 0.5)
    width, height = size
    return RenderCamera(
        position=Point3(x=x, y=y, z=max(z, 1.0)),
        yaw=math.atan2(cy - y, cx - x),
        pitch=-math.atan2(max(z, 1.0) - _LOOK_AT_Z, horizontal),
        focal_px=width / 2.0,  # champ horizontal de 90°
        width_px=width,
        height_px=height,
        checker_floor=False,
    )


def render_proposal(
    project: Project,
    level_id: UUID,
    proposal: LayoutProposal,
    out_dir: Path,
    *,
    blender_bin: str | None = None,
    samples: int = VARIANT_SAMPLES,
    gpu: bool = True,
    run_id: UUID | None = None,
    daylight: Daylight | None = None,
) -> StepResult:
    """Vignette Blender d'une variante **sans l'appliquer** : la scène d'essai reçoit les
    meubles de la variante, le rendu part d'un point de vue de vignette ; l'entrée de galerie
    porte le numéro de la variante. La géométrie rendue est celle validée par le moteur."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    if not proposal.furniture:
        msg = "cette variante ne contient aucun meuble"
        raise NoCameraError(msg)
    trial_level = level.model_copy(
        update={"elements": {**level.elements, **{f.id: f for f in proposal.furniture}}}
    )
    trial = project.model_copy(update={"levels": {**project.levels, level.id: trial_level}})
    camera = overview_camera(level, proposal.furniture)
    light = daylight_spec(daylight, level.north_deg) if daylight else None
    result = run_blender(
        trial,
        level_id,
        out_dir,
        blender_bin=blender_bin,
        preview=True,
        run_id=run_id,
        camera=camera,
        samples=samples,
        denoise=True,
        style="realistic",
        gpu=gpu,
        light=light,
    )
    preview = next((a for a in result.artifacts if a.name == "preview.png"), None)
    if preview is None:
        msg = "Blender n'a pas produit d'image"
        raise BlenderError(msg)
    device = next((m.split("@", 1)[1] for m in result.model_ids if m.startswith("cycles@")), "?")
    entry = RenderEntry(
        run_id=run_id or uuid4(),
        camera_id=None,
        level_id=level_id,
        camera_name=f"Variante {proposal.index}",
        samples=samples,
        device=device,
        render_s=_render_seconds(preview.description),
        width_px=camera.width_px,
        height_px=camera.height_px,
        created_at=datetime.now(UTC),
        daylight=daylight,
        north_deg=level.north_deg,
        variant=proposal.index,
    )
    (out_dir / RENDER_SIDECAR).write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _note_gallery(out_dir, project, level_id, None, proposal.index)
    assumptions = [
        Assumption(
            text=f"Vignette de la variante {proposal.index} : ses meubles sont posés dans une "
            "copie de la scène le temps du rendu ; rien n'est appliqué au projet.",
            impact="medium",
        ),
        Assumption(
            text="Image de synthèse de la géométrie estimée (boîtes cotées pour les meubles), "
            "pas une photo.",
            impact="high",
        ),
    ]
    return result.model_copy(update={"step": STEP_VARIANT, "assumptions": assumptions})


def _note_gallery(
    out_dir: Path, project: Project, level_id: UUID, camera: Camera | None, variant: int | None
) -> None:
    """US2 : vue et ambiance du moment, rangées dans `gallery.json` à côté de l'image."""
    from openplan.pipeline.gallery import note_render

    note_render(out_dir, project, level_id, camera, variant)


def _render_seconds(description: str) -> float | None:
    match = re.search(r", (\d+) s$", description)
    return float(match.group(1)) if match else None


def list_renders(runs_dir: Path) -> list[RenderEntry]:
    """Les rendus du projet, du plus récent au plus ancien (fichiers `render.json` des runs)."""
    entries: list[RenderEntry] = []
    if not runs_dir.is_dir():
        return entries
    for sidecar in runs_dir.glob(f"*/blender/{RENDER_SIDECAR}"):
        try:
            entry = RenderEntry.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if (sidecar.parent / entry.image).is_file():
            entries.append(
                entry.model_copy(
                    update={
                        "finished": read_finish(sidecar.parent),
                        "inspiration": read_inspiration(sidecar.parent),
                        "catalogue": read_inspiration(sidecar.parent, catalogue=True),
                    }
                )
            )
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries


def render_photo_view(
    project: Project,
    level_id: UUID,
    out_dir: Path,
    *,
    blender_bin: str | None = None,
    samples: int = 64,
    gpu: bool = True,
    run_id: UUID | None = None,
) -> StepResult:
    """Rendu Cycles avec matières au point de vue de la caméra de la photo."""
    camera, render_camera = photo_camera(project, level_id)
    result = run_blender(
        project,
        level_id,
        out_dir,
        blender_bin=blender_bin,
        preview=True,
        run_id=run_id,
        camera=render_camera,
        samples=samples,
        denoise=True,
        style="realistic",
        gpu=gpu,
    )
    if not any(a.name == "preview.png" for a in result.artifacts):
        msg = "Blender n'a pas produit d'image"
        raise BlenderError(msg)
    assumptions = [
        Assumption(
            text="Image de synthèse de la géométrie estimée, pas une photo retouchée : les "
            "dimensions et le point de vue héritent des incertitudes de la scène.",
            impact="high",
        ),
        Assumption(
            text="Matières procédurales par défaut (bois, pierre, enduit) : aspect indicatif, "
            "modifiable élément par élément.",
            impact="low",
        ),
        Assumption(
            text=(
                f"Point de vue : caméra « {camera.name or 'Photo'} » ({camera.source.value}), "
                f"focale {render_camera.focal_px:.0f} px pour {render_camera.width_px} px."
            ),
            impact="medium",
        ),
    ]
    return result.model_copy(update={"step": STEP, "assumptions": assumptions})
