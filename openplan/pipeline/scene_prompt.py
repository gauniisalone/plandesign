"""KAN-86 : ce que la scène rendue dit d'elle-même, pour la description du rendu.

`finish_prompt` (V1.5) ne lisait que l'ambiance de la pièce et les matières par défaut de son
style : un grenier aux murs de pierre partait en « plastered walls ». Ici, tout vient de la
scène réellement rendue (`bake.json`, à côté de l'image) : la matière dominante de chaque
surface (murs, sol, plafond, pentes, charpente), les meubles visibles dans la vue, nommés par
leur catégorie du catalogue et comptés, et l'habillage (textiles, tapis, suspension, lampes,
livres, plantes) avec les couleurs de sa palette. Aucun mot ne décrit une chose absente de la
scène : ce qui manque est tu, jamais remplacé par une valeur par défaut.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from openplan.pipeline.guides import VISIBLE_MIN_PX, visible_pixels
from openplan.schemas.bake import Bake, Decor, Polyhedron, Prism, RenderCamera
from openplan.schemas.elements import Furniture
from openplan.schemas.intent import COLORS
from openplan.schemas.scene import Project

FINISH_SURFACE: dict[str, dict[str, str]] = {
    "walls": {
        "plaster": "plastered walls",
        "limewash": "white limewash walls",
        "stone": "exposed stone walls",
        "stone_rubble": "rubble stone walls",
        "concrete": "concrete walls",
        "wood_boards": "wooden board walls",
        "painted_wood": "white painted wood walls",
        "timber": "timber walls",
    },
    "floor": {
        "wood_floor": "light pine floorboards",
        "wood_dark": "old dark parquet",
        "wood_boards": "wide wooden floorboards",
        "oak_dark": "dark oak floor",
        "concrete_floor": "polished concrete floor",
        "concrete": "concrete floor",
        "stone": "stone floor",
        "painted_wood": "white painted floorboards",
    },
    "ceiling": {
        "plaster": "plastered ceiling",
        "limewash": "white limewash ceiling",
        "wood_boards": "wooden board ceiling",
        "painted_wood": "white painted wood ceiling",
        "timber": "timber ceiling",
        "concrete": "concrete ceiling",
    },
    "slopes": {
        "plaster": "plastered sloped ceiling",
        "limewash": "white limewash sloped ceiling",
        "wood_boards": "wooden boards sloped ceiling",
        "painted_wood": "white painted wood sloped ceiling",
        "timber": "timber sloped ceiling",
        "stone": "stone sloped ceiling",
    },
    "timber": {
        "timber": "exposed timber beams",
        "oak_dark": "dark oak beams",
        "painted_wood": "white painted beams",
        "wood_boards": "wooden beams",
    },
}
"""Matière (`Finish` du bake) → mots anglais, par surface : « stone » se dit d'un mur, pas
d'un plafond ; une matière sans entrée pour la surface n'est pas dite."""

GABLE_WALLS = "gable walls"
"""Combles : les murs portant de la matière sont les pignons."""

FURNITURE_WORDS: dict[str, tuple[str, str]] = {
    "armchair": ("armchair", "armchairs"),
    "bathroom_cabinet": ("bathroom cabinet", "bathroom cabinets"),
    "bathtub": ("bathtub", "bathtubs"),
    "bed_builtin": ("built-in bed under the slope", "built-in beds under the slope"),
    "bed_bunk": ("bunk bed", "bunk beds"),
    "bed_double": ("double bed", "double beds"),
    "bed_single": ("single bed", "single beds"),
    "bookshelf": ("bookshelf", "bookshelves"),
    "chair": ("chair", "chairs"),
    "chest": ("wooden chest", "wooden chests"),
    "coffee_table": ("coffee table", "coffee tables"),
    "desk": ("desk", "desks"),
    "desk_corner": ("corner desk", "corner desks"),
    "dining_chair": ("dining chair", "dining chairs"),
    "dryer": ("dryer", "dryers"),
    "floor_lamp": ("floor lamp", "floor lamps"),
    "fridge": ("fridge", "fridges"),
    "fridge_small": ("small fridge", "small fridges"),
    "kitchen_base": ("kitchen base cabinet", "kitchen base cabinets"),
    "kitchen_drawers": ("kitchen drawer unit", "kitchen drawer units"),
    "kitchen_sink": ("kitchen sink", "kitchen sinks"),
    "low_storage": ("low open bookcase", "low open bookcases"),
    "nightstand": ("nightstand", "nightstands"),
    "round_table": ("round table", "round tables"),
    "rug": ("rug", "rugs"),
    "rug_round": ("round rug", "round rugs"),
    "shower": ("shower", "showers"),
    "sofa": ("sofa", "sofas"),
    "sofa_corner": ("corner sofa", "corner sofas"),
    "sofa_lounge": ("lounge sofa", "lounge sofas"),
    "stove": ("stove", "stoves"),
    "table": ("table", "tables"),
    "toilet": ("toilet", "toilets"),
    "tv_unit": ("tv unit", "tv units"),
    "wardrobe": ("wardrobe", "wardrobes"),
    "washbasin": ("washbasin", "washbasins"),
    "washer": ("washing machine", "washing machines"),
}
"""Catégorie du catalogue → (singulier, pluriel). Nommer le meuble évite qu'un modèle d'images
le remplace par un autre objet (KAN-84b : la bibliothèque basse devenait une cheminée)."""

NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}

FURNITURE_WOOD: dict[str, str] = {
    "wood_floor": "light wood",
    "wood_boards": "light wood",
    "wood_dark": "dark wood",
    "oak_dark": "dark oak",
    "painted_wood": "white painted wood",
}
"""Bois des meubles (`Prism.woods["wood"]`) → mots."""

PENDANT_WORDS = {
    "rattan": "rattan pendant lamp",
    "metal_black": "black metal pendant lamp",
    "white": "white pendant lamp",
    "brass": "brass pendant lamp",
}

COLOR_ENGLISH: dict[str, str] = {
    "blanc casse": "off-white",
    "lin": "linen",
    "sable": "sand",
    "terracotta": "terracotta",
    "ocre": "ochre",
    "vert sauge": "sage green",
    "vert foret": "forest green",
    "bleu nuit": "navy blue",
    "bleu gris": "grey blue",
    "gris anthracite": "charcoal grey",
    "noir": "black",
    "chene clair": "light oak",
    "noyer": "walnut",
    "rose poudre": "dusty pink",
}

FURNITURE_MIN_PX = 4.0
"""Un objet d'habillage dont le centre se projette hors de l'image (à cette marge près) n'est
pas « vu » ; les meubles, eux, suivent `VISIBLE_MIN_PX` (KAN-88)."""


@dataclass(frozen=True)
class SceneWords:
    """Les trois groupes du prompt tirés de la scène ; un groupe vide est tu."""

    surfaces: list[str] = field(default_factory=list)
    furniture: list[str] = field(default_factory=list)
    textiles: list[str] = field(default_factory=list)


def _dominant(solids: list[Prism] | list[Polyhedron], kinds: set[str]) -> str | None:
    counts = Counter(s.finish for s in solids if s.kind in kinds)
    return counts.most_common(1)[0][0] if counts else None


def _surfaces(bake: Bake, attic: bool) -> list[str]:
    solids: list[Prism | Polyhedron] = [*bake.prisms, *bake.polyhedra]
    by_kind: dict[str, Counter[str]] = {}
    for s in solids:
        by_kind.setdefault(s.kind, Counter())[s.finish] += 1

    def finish_of(*kinds: str) -> str | None:
        total: Counter[str] = Counter()
        for k in kinds:
            total.update(by_kind.get(k, Counter()))
        return total.most_common(1)[0][0] if total else None

    out: list[str] = []
    floor = FINISH_SURFACE["floor"].get(finish_of("floor") or "")
    if floor:
        out.append(floor)
    walls = FINISH_SURFACE["walls"].get(finish_of("wall") or "")
    if walls:
        out.append(walls.replace("walls", GABLE_WALLS) if attic else walls)
    roof = finish_of("roof")
    if roof:
        word = FINISH_SURFACE["slopes"].get(roof)
        if word:
            out.append(word)
    else:
        word = FINISH_SURFACE["ceiling"].get(finish_of("ceiling") or "")
        if word:
            out.append(word)
    timber = FINISH_SURFACE["timber"].get(finish_of("timber") or "")
    if timber:
        out.append(timber)
    return out


def _camera_basis(camera: RenderCamera) -> tuple[tuple[float, ...], ...]:
    yaw, p = camera.yaw, camera.pitch
    forward = (math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p))
    right = (math.sin(yaw), -math.cos(yaw), 0.0)
    up = (-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p))
    return forward, right, up


def in_view(camera: RenderCamera, x: float, y: float, z: float) -> bool:
    """Le point (mètres, repère du plan) se projette-t-il dans l'image de la caméra ?"""
    forward, right, up = _camera_basis(camera)
    d = (x - camera.position.x, y - camera.position.y, z - camera.position.z)
    depth = sum(a * b for a, b in zip(d, forward, strict=True))
    if depth <= 0.1:
        return False
    u = (
        camera.width_px / 2
        + camera.focal_px * sum(a * b for a, b in zip(d, right, strict=True)) / depth
    )
    v = (
        camera.height_px / 2
        - camera.focal_px * sum(a * b for a, b in zip(d, up, strict=True)) / depth
    )
    m = FURNITURE_MIN_PX
    return -m <= u <= camera.width_px + m and -m <= v <= camera.height_px + m


def _center(prism: Prism) -> tuple[float, float, float]:
    n = len(prism.exterior)
    return (
        sum(p.x for p in prism.exterior) / n,
        sum(p.y for p in prism.exterior) / n,
        (prism.z0 + prism.z1) / 2,
    )


def _category(project: Project, level_id: UUID, element_id: UUID) -> str | None:
    """La catégorie du catalogue du meuble (portée par l'élément du plan)."""
    level = project.levels.get(level_id)
    element = level.elements.get(element_id) if level is not None else None
    return element.category if isinstance(element, Furniture) else None


def _furniture(
    bake: Bake, project: Project, level_id: UUID, camera: RenderCamera | None
) -> list[str]:
    seen: dict[UUID, str] = {}
    woods: Counter[str] = Counter()
    # KAN-88 (décision du PO, 22 oct.) : un meuble est nommé dès qu'au moins 200 px de lui
    # sont visibles (rastérisation de la vue, critère de l'essai), pas seulement si son centre
    # est dans l'image : un chevet en bord de cadre oublié devenait autre chose.
    visible = visible_pixels(bake, camera) if camera is not None else None
    for prism in bake.prisms:
        if prism.kind != "furniture" or prism.element_id in seen:
            continue
        if visible is not None and visible.get(str(prism.element_id), 0) < VISIBLE_MIN_PX:
            continue
        category = _category(project, level_id, prism.element_id)
        if category is None or category not in FURNITURE_WORDS:
            continue
        seen[prism.element_id] = category
        if prism.woods and prism.woods.get("wood"):
            woods[prism.woods["wood"]] += 1
    counts = Counter(seen.values())
    out = []
    for category, n in counts.most_common():
        one, many = FURNITURE_WORDS[category]
        if n == 1:
            out.append(f"{'an' if one[0] in 'aeiou' else 'a'} {one}")
        else:
            out.append(f"{NUMBERS.get(n, str(n))} {many}")
    wood = FURNITURE_WOOD.get(woods.most_common(1)[0][0]) if woods else None
    if out and wood:
        out.append(f"furniture in {wood}")
    return out


def _srgb(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def color_name(rgb_linear: tuple[float, float, float]) -> str:
    """Teinte linéaire de Blender → le nom de la palette de l'app le plus proche, en anglais."""
    rgb = [_srgb(c) * 255 for c in rgb_linear]
    name = min(COLORS, key=lambda k: sum((a - b) ** 2 for a, b in zip(COLORS[k], rgb, strict=True)))
    return COLOR_ENGLISH.get(name, name)


def _textiles(bake: Bake, camera: RenderCamera | None) -> list[str]:
    def seen(d: Decor) -> bool:
        return camera is None or in_view(camera, d.x, d.y, d.z + d.height / 2)

    decor = [d for d in bake.decor if seen(d)]
    kinds = Counter(d.kind for d in decor)
    palette = bake.palette
    out: list[str] = []
    if kinds["pillow"] or kinds["throw"] or kinds["duvet"]:
        tint = f"{color_name(palette.textile)} " if palette is not None else ""
        out.append(f"{tint}bedding with pillows and throws")
    if kinds["jute_rug"]:
        out.append("jute rug" if kinds["jute_rug"] == 1 else "jute rugs")
    pendant = next((d for d in decor if d.kind == "pendant"), None)
    if pendant is not None:
        out.append(PENDANT_WORDS.get(pendant.material or "", "pendant lamp"))
    if kinds["table_lamp"]:
        out.append("bedside table lamp" if kinds["table_lamp"] == 1 else "bedside table lamps")
    if kinds["curtain"]:
        tint = f"{color_name(palette.curtain)} " if palette is not None else ""
        out.append(f"{tint}curtains")
    if kinds["books"]:
        out.append("books")
    if kinds["plant"] or kinds["floor_plant"]:
        out.append("plants")
    if kinds["frame"]:
        out.append("framed prints")
    return out


def scene_words(
    bake: Bake, project: Project, level_id: UUID, camera: RenderCamera | None = None
) -> SceneWords:
    """Matières par surface, meubles vus (nommés, comptés) et habillage, pour la vue de
    `camera` (None : toute la scène)."""
    level = project.levels.get(level_id)
    attic = level is not None and level.kind == "attic"
    return SceneWords(
        surfaces=_surfaces(bake, attic),
        furniture=_furniture(bake, project, level_id, camera),
        textiles=_textiles(bake, camera),
    )


def load_scene_words(
    project: Project, render_dir: Path, level_id: UUID | None
) -> SceneWords | None:
    """La scène d'un rendu (`bake.json` et `camera.json` à côté de l'image) ; None si le rendu
    n'en a pas (rendu d'avant, ou sans Blender) : le prompt garde alors l'ambiance seule."""
    try:
        bake = Bake.model_validate_json((render_dir / "bake.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return None
    camera: RenderCamera | None = None
    try:
        camera = RenderCamera.model_validate(
            json.loads((render_dir / "camera.json").read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, ValidationError):
        camera = None
    return scene_words(bake, project, level_id or bake.level_id, camera)
