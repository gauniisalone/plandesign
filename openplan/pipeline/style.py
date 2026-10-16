"""Ambiance → matières (V1, note 10, point 3) : le style de l'intention choisit la matière de
chaque surface (sol, murs, pignons, pentes, charpente) et la teinte des meubles.

Ordre de priorité, du plus fort au plus faible :
1. la matière choisie par l'utilisateur sur l'élément (`metadata["finish"]` avec
   `metadata["finish_source"] == "user_defined"`) ;
2. la matière voulue par l'intention pour cette surface (`DesignIntent.materials`) ;
3. la matière du style ;
4. la matière posée par une étape automatique sur l'élément (combles : pignon en pierre…) ;
5. la matière par défaut du type.
Les meubles du catalogue gardent leurs modèles ; leurs matières nommées (`wood`, `woodDark`,
`carpet`, `metal`) prennent la teinte du style, le tissu celle de la palette si elle est dite.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.bake import RGB, Finish
from openplan.schemas.elements import Room
from openplan.schemas.intent import COLORS, DesignIntent
from openplan.schemas.scene import Level

SURFACES = ("floor", "walls", "gables", "slopes", "timber")

STYLE_SURFACES: dict[str, dict[str, Finish]] = {
    "naturel": {
        "floor": "wood_floor",
        "walls": "plaster",
        "gables": "stone",
        "slopes": "wood_boards",
        "timber": "timber",
    },
    "chalet": {
        "floor": "wood_dark",
        "walls": "wood_boards",
        "gables": "stone_rubble",
        "slopes": "wood_boards",
        "timber": "timber",
    },
    "scandinave": {
        "floor": "wood_floor",
        "walls": "limewash",
        "gables": "limewash",
        "slopes": "painted_wood",
        "timber": "painted_wood",
    },
    "industriel": {
        "floor": "concrete_floor",
        "walls": "plaster",
        "gables": "stone_rubble",
        "slopes": "oak_dark",
        "timber": "oak_dark",
    },
    "contemporain": {
        "floor": "oak_dark",
        "walls": "limewash",
        "gables": "limewash",
        "slopes": "limewash",
        "timber": "painted_wood",
    },
}
"""Matière de chaque surface selon le style (bois brut ou blanchi, chaux, béton…)."""


def _srgb(r: int, g: int, b: int) -> RGB:
    """Couleur sRGB 0–255 → linéaire 0–1 (Blender)."""
    return ((r / 255) ** 2.2, (g / 255) ** 2.2, (b / 255) ** 2.2)


STYLE_WOODS: dict[str, dict[str, Finish]] = {
    "naturel": {"wood": "wood_boards", "woodDark": "wood_dark"},
    "chalet": {"wood": "wood_dark", "woodDark": "wood_dark"},
    "scandinave": {"wood": "painted_wood", "woodDark": "wood_floor"},
    "industriel": {"wood": "oak_dark", "woodDark": "oak_dark"},
    "contemporain": {"wood": "painted_wood", "woodDark": "oak_dark"},
}
"""Bois des meubles selon le style : chêne clair, noyer, bois peint blanc, chêne foncé."""

STYLE_RAILING: dict[str, Finish] = {
    "naturel": "timber",
    "chalet": "wood_dark",
    "scandinave": "painted_wood",
    "industriel": "metal",
    "contemporain": "metal",
}
"""Garde-corps selon le style : bois brut, noyer, bois peint blanc, métal noir."""

STYLE_DOORS: dict[str, Finish] = {
    "naturel": "wood_boards",
    "chalet": "wood_dark",
    "scandinave": "painted_wood",
    "industriel": "oak_dark",
    "contemporain": "painted_wood",
}
"""Vantaux des portes selon le style : chêne clair, noyer, bois peint blanc, chêne foncé."""


def door_leaf_finish(default: Finish) -> Finish:
    """Bois des vantaux pour le bake en cours : celui du style si la pièce a une ambiance,
    sinon `default` (projet d'avant V1)."""
    styled = CURRENT.get()
    if styled is None or not styled.by_surface:
        return default
    return STYLE_DOORS.get(styled.style, default)


def floor_finish(default: Finish) -> Finish:
    """Sol du bake en cours (seuils des portes) : celui du style si la pièce a une ambiance."""
    styled = CURRENT.get()
    if styled is None or not styled.by_surface:
        return default
    return styled.by_surface.get("floor", default)


STYLE_TINTS: dict[str, dict[str, RGB]] = {
    "naturel": {
        "wood": _srgb(196, 160, 115),
        "woodDark": _srgb(150, 112, 75),
        "carpet": _srgb(214, 200, 176),
    },
    "chalet": {
        "wood": _srgb(110, 75, 50),
        "woodDark": _srgb(80, 52, 34),
        "carpet": _srgb(150, 70, 50),
    },
    "scandinave": {
        "wood": _srgb(232, 228, 220),
        "woodDark": _srgb(196, 160, 115),
        "carpet": _srgb(190, 192, 192),
    },
    "industriel": {
        "wood": _srgb(80, 55, 38),
        "woodDark": _srgb(60, 42, 30),
        "carpet": _srgb(140, 80, 40),
        "metal": _srgb(30, 30, 32),
    },
    "contemporain": {
        "wood": _srgb(236, 234, 230),
        "woodDark": _srgb(60, 62, 66),
        "carpet": _srgb(64, 66, 72),
        "metal": _srgb(25, 25, 27),
    },
}
"""Teinte des matières nommées des modèles Kenney selon le style (chêne clair, noyer, blanc,
bois foncé et noir…)."""


@dataclass
class StyleFinishes:
    """Ce que l'ambiance du niveau impose, résolu une fois par bake."""

    style: str
    by_surface: dict[str, Finish]
    gables: set[UUID] = field(default_factory=set)
    tints: dict[str, RGB] = field(default_factory=dict)
    woods: dict[str, Finish] = field(default_factory=dict)


CURRENT: ContextVar[StyleFinishes | None] = ContextVar("openplan_style_finishes", default=None)
"""Matières du style du niveau en cours de bake (posé par `bake_level`)."""


def level_intent(level: Level) -> DesignIntent | None:
    for e in level.elements.values():
        if isinstance(e, Room) and e.intent is not None:
            return e.intent
    return None


def gable_walls(level: Level, graph: WallGraph) -> set[UUID]:
    """Murs de pignon : sous une toiture, les murs qui bordent une pièce sans porter d'égout
    (les cloisons intérieures restent des murs)."""
    roofs = list(derived_roofs(level, graph).values())
    if not roofs:
        return set()
    boundary = {w for e in level.elements.values() if isinstance(e, Room) for w in e.boundary_walls}
    out: set[UUID] = set()
    for wid in boundary:
        if wid not in graph.walls:
            continue
        try:
            ux, uy = graph.wall_direction(wid)
        except GeometryError:
            continue
        a, _ = graph.wall_axis(wid)
        nx, ny = -uy, ux
        eave = any(
            abs((r.eave[0].x - a.x) * nx + (r.eave[0].y - a.y) * ny) < 0.3
            and abs((r.eave[1].x - a.x) * nx + (r.eave[1].y - a.y) * ny) < 0.3
            for r in roofs
        )
        if not eave:
            out.add(wid)
    return out


def style_finishes(level: Level, graph: WallGraph) -> StyleFinishes:
    from openplan.pipeline.decor import ambiance

    style = ambiance(level)
    intent = level_intent(level)
    if intent is None:
        # Pièce sans intention (projet d'avant V1) : ses matières restent celles d'avant ;
        # le style ne s'impose qu'une fois l'ambiance posée (fiche, ou chemin automatique).
        return StyleFinishes(style, {}, set(), {})
    by_surface: dict[str, Finish] = dict(STYLE_SURFACES.get(style, STYLE_SURFACES["naturel"]))
    if intent is not None:
        for surface in SURFACES:
            wanted = getattr(intent.materials, surface, None)
            if isinstance(wanted, str) and wanted in FINISH_NAMES:
                by_surface[surface] = wanted  # type: ignore[assignment]
    tints = dict(STYLE_TINTS.get(style, STYLE_TINTS["naturel"]))
    if intent is not None and intent.palette:
        tints["carpet"] = _srgb(*COLORS[intent.palette[0]])
    tints = {k: v for k, v in tints.items() if k in ("carpet", "metal")}
    woods = dict(STYLE_WOODS.get(style, STYLE_WOODS["naturel"]))
    return StyleFinishes(style, by_surface, gable_walls(level, graph), tints, woods)


FINISH_NAMES: frozenset[str] = frozenset(
    {
        "plaster",
        "wood_floor",
        "wood_dark",
        "wood_boards",
        "stone",
        "stone_rubble",
        "concrete",
        "glass",
        "timber",
        "metal",
        "limewash",
        "concrete_floor",
        "oak_dark",
        "painted_wood",
    }
)


def surface_of(kind: str, element_id: UUID, styled: StyleFinishes) -> str | None:
    if kind == "wall":
        return "gables" if element_id in styled.gables else "walls"
    return {"floor": "floor", "slab": "floor", "roof": "slopes", "timber": "timber"}.get(kind)
