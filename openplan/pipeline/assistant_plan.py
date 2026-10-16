"""L'assistant agit sur le plan lui-même (B4) : longueurs de murs et dimensions de la pièce,
portes et fenêtres, nom et usage de la pièce.

Toujours par les commandes d'édition existantes (`set_wall_length`, `add_opening`,
`update_element`, `update_level`), jamais par une coordonnée du modèle. Un mur se désigne par
son nom, sa position (« du fond », « de gauche », « nord »), ce qu'il porte (« celui de la
fenêtre ») ou sa taille (« le plus long ») ; « du fond » se lit face à la photo quand il y a une
vue, sinon vers le nord. Plusieurs murs possibles → question.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from openplan.geom.roof import derived_roofs
from openplan.geom.stairs import stair_layout
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.scene import history
from openplan.scene.ops import (
    AddOpening,
    AddStairs,
    SetWallLength,
    UpdateElement,
    UpdateLevel,
    apply_op,
    stair_rise,
)
from openplan.schemas.assistant import ASSISTANT_ACTOR, ROOM_TYPES, AssistantAction, AssistantIntent
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import (
    OPENING_LABELS,
    Camera,
    Opening,
    OpeningKind,
    Room,
    Stairs,
    Swing,
    Wall,
)
from openplan.schemas.scene import Level, Project
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import _inside_sign, _room_polygons

DOOR = {"width": 0.83, "height": 2.04, "sill": 0.0}
WINDOW = {"width": 1.20, "height": 1.25, "sill": 0.90}
OPENING_SPECS: dict[str, dict[str, float]] = {
    "door": DOOR,
    "window": WINDOW,
    "double_door": {"width": 1.40, "height": 2.04, "sill": 0.0},
    "sliding_door": {"width": 0.83, "height": 2.04, "sill": 0.0},
    "pocket_door": {"width": 0.83, "height": 2.04, "sill": 0.0},
    "folding_door": {"width": 0.90, "height": 2.04, "sill": 0.0},
    "french_window": {"width": 0.95, "height": 2.15, "sill": 0.0},
    "sliding_window": {"width": 2.40, "height": 2.15, "sill": 0.0},
}
"""Largeurs typiques par défaut (E5) : 0,83 / 1,40 / 2,40 m ; porte-fenêtre double 1,40."""
FRENCH_PAIR_WIDTH_M = 1.40
OPENING_STEP_M = 0.10
OPENING_MARGIN_M = 0.10
ROOM_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "bedroom": ("chambre",),
    "living_room": ("salon", "sejour", "salle a manger", "piece a vivre"),
    "kitchen": ("cuisine",),
    "bathroom": ("salle de bain", "salle d'eau", "sdb", "toilettes", "wc"),
    "office": ("bureau",),
    "attic": ("combles", "grenier", "sous les toits"),
    "hallway": ("couloir", "entree", "degagement", "palier"),
    "other": (),
}


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    ).replace("'", " ")


@dataclass
class WallPick:
    walls: list[Wall]
    """Un seul : trouvé ; plusieurs : ambigu ; aucun : introuvable."""
    label: str


def _wall_label(level: Level, wall: Wall, graph: WallGraph) -> str:
    return wall.name or f"mur de {graph.wall_length(wall.id):.2f} m"


def _midpoints(graph: WallGraph, walls: list[Wall]) -> dict[UUID, tuple[float, float]]:
    out = {}
    for w in walls:
        a, b = graph.wall_axis(w.id)
        out[w.id] = ((a.x + b.x) / 2, (a.y + b.y) / 2)
    return out


def _facing(level: Level) -> tuple[float, float]:
    """Direction « vers le fond » : la visée de la vue d'une photo quand il y en a une, sinon
    le nord du plan (90° = haut)."""
    cameras = [
        e for e in level.elements.values() if isinstance(e, Camera) and e.image_id is not None
    ]
    if cameras:
        cam = cameras[0]
        return math.cos(cam.yaw), math.sin(cam.yaw)
    north = math.radians(level.north_deg if level.north_deg is not None else 90.0)
    return math.cos(north), math.sin(north)


def resolve_wall(  # noqa: PLR0911
    project: Project, level_id: UUID, target: str | None
) -> WallPick:
    level = project.levels[level_id]
    graph = WallGraph.from_level(level)
    walls = [w for w in graph.walls.values() if w.visible]
    if not walls:
        return WallPick([], target or "le mur")
    if len(walls) == 1:
        return WallPick(walls, target or "le mur")
    if not target:
        return WallPick(walls, "le mur")
    wanted = _fold(target)
    named = [w for w in walls if w.name and _fold(w.name) in wanted]
    if len(named) == 1:
        return WallPick(named, target)
    mids = _midpoints(graph, walls)
    fx, fy = _facing(level)
    cx = sum(m[0] for m in mids.values()) / len(mids)
    cy = sum(m[1] for m in mids.values()) / len(mids)

    def along(w: Wall, ux: float, uy: float) -> float:
        mx, my = mids[w.id]
        return (mx - cx) * ux + (my - cy) * uy

    rules: list[tuple[tuple[str, ...], tuple[float, float]]] = [
        (("fond", "face", "en face", "devant moi", "nord"), (fx, fy)),
        (("derriere", "dos", "sud", "devant", "bas"), (-fx, -fy)),
        (("gauche", "ouest"), (-fy, fx)),
        (("droite", "est "), (fy, -fx)),
    ]
    for words, (ux, uy) in rules:
        if any(w in wanted for w in words):
            best = max(walls, key=lambda w: along(w, ux, uy))
            return WallPick([best], target)
    if "long" in wanted or "grand" in wanted:
        return WallPick([max(walls, key=lambda w: graph.wall_length(w.id))], target)
    if "court" in wanted or "petit" in wanted:
        return WallPick([min(walls, key=lambda w: graph.wall_length(w.id))], target)
    hosts: list[Wall] = []
    for word, kind in (("fenetre", OpeningKind.WINDOW), ("porte", OpeningKind.DOOR)):
        if word in wanted:
            ids = {
                o.host_wall
                for o in level.elements.values()
                if isinstance(o, Opening) and o.kind is kind
            }
            hosts = [w for w in walls if w.id in ids]
            if hosts:
                return WallPick(hosts, target)
    return WallPick([], target)


def _ambiguous_wall(intent: AssistantIntent, pick: WallPick, level: Level) -> AssistantAction:
    graph = WallGraph.from_level(level)
    what = "Régler" if intent.kind == "set_dimension" else "Poser sur"
    if not pick.walls:
        return AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{what} {pick.label}",
            reason=f"je ne sais pas quel mur est « {pick.label} » ; dites « du fond », « de "
            "gauche », « le plus long », ou cliquez le mur dans le plan",
        )
    names = ", ".join(_wall_label(level, w, graph) for w in pick.walls[:6])
    return AssistantAction(
        kind=intent.kind,
        status="question",
        summary=f"{what} {pick.label}",
        reason=f"Quel mur : {names} ?",
        element_ids=[w.id for w in pick.walls],
    )


def _commit(project: Project, after: Project, label: str, rid: UUID) -> Project:
    return history.commit(project, after, actor=ASSISTANT_ACTOR, label=label, run_id=rid)


def _pair_by_size(graph: WallGraph, walls: list[Wall], longest: bool) -> list[Wall]:
    """Les murs parallèles au plus long (ou au plus court) : la « longueur » ou la « largeur »
    de la pièce."""
    ref = (max if longest else min)(walls, key=lambda w: graph.wall_length(w.id))
    ux, uy = graph.wall_direction(ref.id)
    out = []
    for w in walls:
        try:
            vx, vy = graph.wall_direction(w.id)
        except GeometryError:
            continue
        if abs(ux * vx + uy * vy) > 0.98:
            out.append(w)
    return out


def _set_length(
    project: Project, level_id: UUID, walls: list[Wall], value: float, rid: UUID
) -> Project:
    """Impose la longueur à des murs parallèles en déplaçant leurs extrémités du même côté :
    une pièce rectangulaire le reste."""
    level = project.levels[level_id]
    graph = WallGraph.from_level(level)
    ux, uy = graph.wall_direction(walls[0].id)
    current = project
    for w in walls:
        a, b = graph.wall_axis(w.id)
        forward = (b.x - a.x) * ux + (b.y - a.y) * uy > 0
        current = apply_op(
            current,
            level_id,
            SetWallLength(wall_id=w.id, length=value, anchor="start" if forward else "end"),
            actor=ASSISTANT_ACTOR,
        )
    return current


def set_dimension(  # noqa: PLR0911
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    level = project.levels[level_id]
    value = intent.value_m
    if value is None:
        return project, AssistantAction(
            kind=intent.kind,
            status="question",
            summary="Régler une dimension",
            reason="Quelle valeur, en mètres ?",
        )
    if intent.dimension == "height":
        if (
            level.height_source in (Source.MEASURED, Source.USER_DEFINED)
            and abs(level.height - value) > 1e-6
        ):
            pass
        try:
            after = apply_op(
                project,
                level_id,
                UpdateLevel(fields={"height": round(value, 3), "height_source": "measured"}),
                actor=ASSISTANT_ACTOR,
            )
        except history.PatchRejectedError:
            return project, AssistantAction(
                kind=intent.kind,
                status="refused",
                summary="Hauteur sous plafond",
                reason="vous l'avez déjà fixée vous-même : changez-la dans la fiche Niveau",
            )
        return after, AssistantAction(
            kind=intent.kind, status="done", summary=f"Hauteur sous plafond réglée à {value:.2f} m"
        )
    graph = WallGraph.from_level(level)
    if intent.dimension in ("length", "width"):
        walls = [w for w in graph.walls.values() if w.visible]
        if len(walls) < 2:
            return project, AssistantAction(
                kind=intent.kind,
                status="refused",
                summary="Dimension de la pièce",
                reason="il faut d'abord des murs",
            )
        pair = _pair_by_size(graph, walls, longest=intent.dimension == "length")
        what = "longueur" if intent.dimension == "length" else "largeur"
    else:
        pick = resolve_wall(project, level_id, intent.target)
        if len(pick.walls) != 1:
            return project, _ambiguous_wall(intent, pick, level)
        pair = pick.walls
        what = _wall_label(level, pair[0], graph)
    try:
        after = _set_length(project, level_id, pair, value, rid)
    except (GeometryError, ValueError) as exc:
        return project, AssistantAction(
            kind=intent.kind, status="refused", summary=f"Régler {what}", reason=str(exc)[:160]
        )
    if not validate_invariants(after).valid:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Régler {what}",
            reason="le plan deviendrait incohérent (murs qui se croisent) ; déplacez les "
            "sommets à la main",
        )
    return after, AssistantAction(
        kind=intent.kind,
        status="done",
        summary=f"{what} : {value:.2f} m"
        if intent.dimension == "wall"
        else f"{what.capitalize()} de la pièce : {value:.2f} m",
        element_ids=[w.id for w in pair],
    )


def _free_offset(level: Level, graph: WallGraph, wall: Wall, width: float) -> float | None:
    """Position le long du mur : au milieu, sinon décalée par pas jusqu'à une place sans autre
    ouverture, avec une marge aux extrémités."""
    length = graph.wall_length(wall.id)
    if length < width + 2 * OPENING_MARGIN_M:
        return None
    taken = [
        (o.offset, o.offset + o.width)
        for o in level.elements.values()
        if isinstance(o, Opening) and o.host_wall == wall.id
    ]
    center = (length - width) / 2
    steps = int(length / OPENING_STEP_M) + 1
    for k in range(steps):
        for sign in (1, -1):
            offset = center + sign * k * OPENING_STEP_M
            if offset < OPENING_MARGIN_M or offset + width > length - OPENING_MARGIN_M:
                continue
            if all(offset + width <= a or offset >= b for a, b in taken):
                return round(offset, 3)
            if k == 0:
                break
    return None


def add_opening(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    level = project.levels[level_id]
    kind = intent.opening or "door"
    spec = OPENING_SPECS[kind]
    opening_kind = OpeningKind(kind)
    label = OPENING_LABELS[opening_kind]
    word = label[0].upper() + label[1:]
    leaves = (intent.leaves or 1) if opening_kind is OpeningKind.FRENCH_WINDOW else 1
    pick = resolve_wall(project, level_id, intent.target)
    if len(pick.walls) != 1:
        if not intent.target and len(pick.walls) > 1:
            return project, AssistantAction(
                kind=intent.kind,
                status="question",
                summary=f"Poser une {word.lower()}",
                reason="Sur quel mur ? (du fond, de gauche, de droite, le plus long…)",
                element_ids=[w.id for w in pick.walls],
            )
        return project, _ambiguous_wall(intent, pick, level)
    wall = pick.walls[0]
    graph = WallGraph.from_level(level)
    default_width = FRENCH_PAIR_WIDTH_M if leaves == 2 else spec["width"]
    width = intent.value_m if intent.value_m and 0.4 <= intent.value_m <= 4.0 else default_width
    height = min(spec["height"], level.height - 0.05)
    offset = _free_offset(level, graph, wall, width)
    if offset is None:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{word} sur {_wall_label(level, wall, graph)}",
            reason="pas de place sur ce mur (trop court ou déjà occupé) ; choisissez un autre mur",
            element_ids=[wall.id],
        )
    after = apply_op(
        project,
        level_id,
        AddOpening(
            host_wall=wall.id,
            kind=opening_kind,
            offset=offset,
            width=width,
            height=height,
            sill_height=spec["sill"],
            swing=_room_swing(level, graph, wall, offset + width / 2, opening_kind),
            leaves=leaves,
            source=Source.ESTIMATED,
            confidence=0.6,
        ),
        actor=ASSISTANT_ACTOR,
    )
    new_ids = set(after.levels[level_id].elements) - set(level.elements)
    if not validate_invariants(after).valid:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{word} sur {_wall_label(level, wall, graph)}",
            reason="l'ouverture ne tient pas dans ce mur ; posez-la à la main avec la palette",
            element_ids=[wall.id],
        )
    return after, AssistantAction(
        kind=intent.kind,
        status="done",
        summary=f"{word} de {width:.2f} m posée sur {_wall_label(level, wall, graph)}, au "
        "milieu de la place libre",
        element_ids=list(new_ids),
    )


def _room_swing(
    level: Level, graph: WallGraph, wall: Wall, along: float, kind: OpeningKind
) -> Swing | None:
    """Gonds à gauche, ouverture vers la pièce (côté de son sol) ; rien pour une fenêtre."""
    if kind in (OpeningKind.WINDOW, OpeningKind.PASSAGE):
        return None
    sign = _inside_sign(graph, wall.id, along, _room_polygons(level, graph))
    return Swing(hinge="left", direction="inward" if sign > 0 else "outward")


STAIR_LABELS = {
    "straight": "droit",
    "quarter_turn": "quart tournant",
    "half_turn": "demi-tournant",
    "spiral": "hélicoïdal",
}


def _stairs_place(
    level: Level, graph: WallGraph, intent: AssistantIntent, across: float
) -> tuple[Point2, float, str] | None:
    """Centre et lacet de l'escalier : au centre de la pièce, la montée le long du faîtage (ou
    du plus long mur) ; contre un mur, la montée le long de ce mur."""
    rooms = []
    for e in level.elements.values():
        if isinstance(e, Room):
            try:
                rooms.append(graph.room_polygon(e.id))
            except GeometryError:
                continue
    room = max(rooms, key=lambda p: p.area) if rooms else None
    if intent.target is None or intent.target == "centre":
        if room is None:
            return None
        c = room.centroid
        roofs = list(derived_roofs(level, graph).values())
        if roofs:
            a, b = roofs[0].eave
            yaw = math.atan2(b.y - a.y, b.x - a.x)
        else:
            longest = max(graph.walls, key=graph.wall_length)
            yaw = math.atan2(*reversed(graph.wall_direction(longest)))
        return Point2(x=float(c.x), y=float(c.y)), yaw, "au centre de la pièce"
    return None


def add_stairs(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    """Pose une cage d'escalier (S1) au centre de la pièce ou contre le mur nommé."""
    level = project.levels[level_id]
    graph = WallGraph.from_level(level)
    kind = intent.stairs or "straight"
    word = f"Escalier {STAIR_LABELS[kind]}"
    rise = stair_rise(project, level_id)
    probe = Stairs(
        kind=kind,
        position=Point2(x=0.0, y=0.0),
        rise=rise,
        source=Source.ESTIMATED,
        confidence=0.6,
    )
    layout = stair_layout(probe)
    _along, across = layout.extent
    placed = _stairs_place(level, graph, intent, across)
    where = ""
    if placed is None and intent.target not in (None, "centre"):
        pick = resolve_wall(project, level_id, intent.target)
        if len(pick.walls) != 1:
            return project, _ambiguous_wall(intent, pick, level)
        wall = pick.walls[0]
        a, b = graph.wall_axis(wall.id)
        ux, uy = graph.wall_direction(wall.id)
        nx, ny = -uy, ux
        mx, my = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
        rooms = [graph.room_polygon(e.id) for e in level.elements.values() if isinstance(e, Room)]
        if rooms:
            c = max(rooms, key=lambda p: p.area).centroid
            if (c.x - mx) * nx + (c.y - my) * ny < 0:
                nx, ny = -nx, -ny
        back = wall.thickness / 2.0 + across / 2.0 + 0.02
        placed = (
            Point2(x=mx + nx * back, y=my + ny * back),
            math.atan2(uy, ux),
            f"contre {_wall_label(level, wall, graph)}",
        )
    if placed is None:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=word,
            reason="aucune pièce fermée où le poser ; tracez la pièce d'abord",
        )
    position, yaw, where = placed
    after = apply_op(
        project,
        level_id,
        AddStairs(
            kind=kind,
            position=position,
            yaw=yaw,
            rise=rise,
            source=Source.ESTIMATED,
            confidence=0.6,
        ),
        actor=ASSISTANT_ACTOR,
    )
    new_ids = list(set(after.levels[level_id].elements) - set(level.elements))
    if not validate_invariants(after).valid:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{word} {where}",
            reason="un meuble occupe la place de la trémie ; déplacez-le ou nommez un autre mur",
        )
    steps = layout.steps
    return after, AssistantAction(
        kind=intent.kind,
        status="done",
        summary=f"{word} posé {where} : {steps.risers} marches de {steps.riser_m * 100:.0f} cm, "
        "trémie avec garde-corps",
        element_ids=new_ids,
    )


def _room_type_from_text(text: str | None) -> str | None:
    if not text:
        return None
    folded = _fold(text)
    for room_type, words in ROOM_TYPE_WORDS.items():
        if any(w in folded for w in words):
            return room_type
    return None


def name_room(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    level = project.levels[level_id]
    rooms = [r for r in level.elements.values() if isinstance(r, Room)]
    if not rooms:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary="Nommer la pièce",
            reason="aucune pièce fermée dans le plan (« Détecter les pièces »)",
        )
    room = rooms[0]
    if len(rooms) > 1:
        wanted = _fold(intent.target or "")
        hits = [r for r in rooms if r.name and _fold(r.name) in wanted]
        if len(hits) == 1:
            room = hits[0]
        else:
            return project, AssistantAction(
                kind=intent.kind,
                status="question",
                summary="Nommer la pièce",
                reason="Laquelle : " + ", ".join(r.name or "sans nom" for r in rooms) + " ?",
                element_ids=[r.id for r in rooms],
            )
    fields: dict[str, object] = {}
    if intent.name:
        fields["name"] = intent.name[:60]
    room_type = (
        intent.room_type if intent.room_type in ROOM_TYPES else _room_type_from_text(intent.name)
    )
    if room_type:
        fields["room_type"] = room_type
    if not fields:
        return project, AssistantAction(
            kind=intent.kind,
            status="question",
            summary="Nommer la pièce",
            reason="Quel nom lui donner ?",
        )
    after = apply_op(
        project,
        level_id,
        UpdateElement(element_id=room.id, fields=fields),  # type: ignore[arg-type]
        actor=ASSISTANT_ACTOR,
    )
    said = " et ".join(
        filter(
            None,
            [
                f"nommée « {fields['name']} »" if "name" in fields else "",
                f"usage : {room_type}" if room_type else "",
            ],
        )
    )
    return after, AssistantAction(
        kind=intent.kind, status="done", summary=f"Pièce {said}", element_ids=[room.id]
    )


__all__ = ["add_opening", "name_room", "resolve_wall", "set_dimension"]
