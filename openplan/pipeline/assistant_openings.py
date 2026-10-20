"""L'assistant sur les portes et fenêtres déjà posées (KAN-32) : déplacer le long du mur,
supprimer, changer la largeur ou la hauteur.

Toujours par les commandes d'édition du plan (`UpdateElement`, `RemoveElement`, celles de
l'inspecteur et des poignées E5a), jamais par une géométrie à part ; un pas d'historique
attribué à `assistant` par geste, annulable. Une ouverture se désigne par sa sorte (« la
fenêtre », « la porte »), sa pièce (« de la chambre », « du placard »), son orientation
(« nord ») ou sa place sur le mur (« de gauche ») ; plusieurs candidates → question qui les
liste, jamais un choix au hasard. L'ouverture reste dans son mur, à l'écart des angles et des
autres ouvertures du mur ; sinon refus avec la raison.

« Gauche » et « droite » se lisent depuis la pièce, face au mur : c'est le seul sens qui vaut
pour tous les murs (vu de dessus, un mur vertical n'a ni gauche ni droite).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from shapely.geometry import Point

from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import OpError, RemoveElement, UpdateElement, apply_ops
from openplan.schemas.assistant import ASSISTANT_ACTOR, AssistantAction, AssistantIntent
from openplan.schemas.common import USER_AUTHORITATIVE
from openplan.schemas.elements import OPENING_LABELS, Opening, OpeningKind, Room
from openplan.schemas.numbers import metres
from openplan.schemas.scene import Level, Project
from openplan.validation.invariants import validate_invariants

OPENING_MARGIN_M = 0.10
"""Jambage minimal laissé à un bout de mur (le même que l'assistant qui pose, B4)."""
CORNER_CLEARANCE_M = 0.05
"""Au droit d'un angle : la moitié de l'épaisseur du mur qui arrive, plus ce jeu."""
MIN_SIZE_M = 0.40
MAX_WIDTH_M = 4.0
CEILING_GAP_M = 0.05

_DOOR_KINDS = frozenset(
    {
        OpeningKind.DOOR,
        OpeningKind.DOUBLE_DOOR,
        OpeningKind.SLIDING_DOOR,
        OpeningKind.POCKET_DOOR,
        OpeningKind.FOLDING_DOOR,
        OpeningKind.PASSAGE,
    }
)
_WINDOW_KINDS = frozenset({OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW})
_ROOM_WORDS: dict[str, tuple[str, ...]] = {
    "bedroom": ("chambre",),
    "living_room": ("salon", "sejour", "salle a manger", "piece a vivre"),
    "kitchen": ("cuisine",),
    "bathroom": ("salle de bain", "salle de bains", "salle d eau", "sdb", "toilettes", "wc"),
    "office": ("bureau",),
    "attic": ("combles", "grenier"),
    "hallway": ("couloir", "degagement", "palier"),
}
"""Usages des pièces (« porte d'entrée » n'en est pas un : c'est une porte)."""
_COMPASS = ("nord", "est", "sud", "ouest")


def _fold(text: str) -> str:
    out = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", out.replace("’", "'").replace("'", " ").replace("-", " ")).strip()


def _has(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}s?\b", text) is not None


def _noun(o: Opening) -> str:
    return OPENING_LABELS[o.kind]


def _the(o: Opening) -> str:
    """« la fenêtre », « le passage »."""
    return ("le " if o.kind is OpeningKind.PASSAGE else "la ") + _noun(o)


def _e(o: Opening) -> str:
    """Accord du participe : « supprimée », « supprimé » (passage)."""
    return "" if o.kind is OpeningKind.PASSAGE else "e"


def _kinds(wanted: str, hint: str | None) -> frozenset[OpeningKind] | None:  # noqa: PLR0911
    """Les sortes d'ouvertures que la désignation vise ; None : toutes."""
    if re.search(r"\bportes? fenetres?\b", wanted):
        return frozenset({OpeningKind.FRENCH_WINDOW})
    if _has(wanted, "baie"):
        return frozenset({OpeningKind.SLIDING_WINDOW})
    if _has(wanted, "fenetre"):
        return _WINDOW_KINDS
    if _has(wanted, "porte") or _has(wanted, "passage"):
        return _DOOR_KINDS
    if hint is not None:
        kind = OpeningKind(hint)
        if kind in _WINDOW_KINDS:
            return _WINDOW_KINDS
        if kind in _DOOR_KINDS:
            return _DOOR_KINDS
        return frozenset({kind})
    return None


@dataclass
class Placed:
    """Une ouverture vue depuis sa pièce : orientation et sens « gauche » le long du mur."""

    opening: Opening
    room: Room | None
    compass: str
    left: float
    """+1 : la gauche (face au mur, depuis la pièce) est vers la fin du mur ; −1 sinon."""
    length: float


def _rooms(level: Level) -> list[Room]:
    return [r for r in level.elements.values() if isinstance(r, Room)]


def _side(
    graph: WallGraph, level: Level, o: Opening, room: Room | None
) -> tuple[float, float, float]:
    """(signe de l'intérieur par rapport à la normale gauche du mur, nord·sortant, est·sortant).
    L'intérieur : la pièce donnée, sinon une pièce qui touche le mur, sinon le côté du centre
    du plan."""
    a, _b = graph.wall_axis(o.host_wall)
    ux, uy = graph.wall_direction(o.host_wall)
    nx, ny = -uy, ux
    along = o.offset + o.width / 2
    px, py = a.x + ux * along, a.y + uy * along
    probe = graph.wall(o.host_wall).thickness / 2 + 0.05
    sign: float | None = None
    for r in [room] if room is not None else _rooms(level):
        if o.host_wall not in r.boundary_walls:
            continue
        try:
            polygon = graph.room_axis_polygon(r.id)
        except GeometryError:
            continue
        if polygon.contains(Point(px + nx * probe, py + ny * probe)):
            sign = 1.0
            break
        if polygon.contains(Point(px - nx * probe, py - ny * probe)):
            sign = -1.0
            break
    if sign is None:
        points = [v.position for v in level.vertices.values()]
        cx = sum(p.x for p in points) / max(1, len(points))
        cy = sum(p.y for p in points) / max(1, len(points))
        sign = 1.0 if (cx - px) * nx + (cy - py) * ny >= 0 else -1.0
    out_x, out_y = -sign * nx, -sign * ny
    north = math.radians(level.north_deg)
    north_x, north_y = math.cos(north), math.sin(north)
    east_x, east_y = north_y, -north_x
    return sign, out_x * north_x + out_y * north_y, out_x * east_x + out_y * east_y


def _compass(n: float, e: float) -> str:
    if abs(n) >= abs(e):
        return "nord" if n > 0 else "sud"
    return "est" if e > 0 else "ouest"


def _room_of(level: Level, o: Opening, named: list[Room]) -> Room | None:
    for r in named or _rooms(level):
        if o.host_wall in r.boundary_walls:
            return r
    return None


def _place(graph: WallGraph, level: Level, o: Opening, named: list[Room]) -> Placed:
    room = _room_of(level, o, named)
    sign, n, e = _side(graph, level, o, room)
    return Placed(o, room, _compass(n, e), sign, graph.wall_length(o.host_wall))


def _named_rooms(level: Level, wanted: str) -> list[Room] | None:
    """Les pièces nommées dans la désignation (par leur nom, sinon par leur usage) ; None :
    aucune pièce nommée."""
    rooms = _rooms(level)
    by_name = [r for r in rooms if r.name and _fold(r.name) and _has(wanted, _fold(r.name))]
    if by_name:
        longest = max(len(_fold(r.name or "")) for r in by_name)
        return [r for r in by_name if len(_fold(r.name or "")) == longest]
    for room_type, words in _ROOM_WORDS.items():
        if any(_has(wanted, w) for w in words):
            return [r for r in rooms if r.room_type == room_type or _named_like(r, words)]
    return None


def _named_like(room: Room, words: tuple[str, ...]) -> bool:
    name = _fold(room.name or "")
    return bool(name) and any(_has(name, w) for w in words)


def _compass_word(wanted: str) -> str | None:
    for word in ("nord", "sud", "ouest"):
        if _has(wanted, word):
            return word
    # « est » est aussi un verbe : seulement après l'ouverture, « à l'est », « côté est ».
    if re.search(r"\b(fenetres?|portes?|baies?|passages?|ouvertures?|cote|l|mur)\s+est\b", wanted):
        return "est"
    return None


def _position_word(wanted: str) -> str | None:
    if re.search(r"\b(de|a|sur la|la plus a) gauche\b", wanted):
        return "gauche"
    if re.search(r"\b(de|a|sur la|la plus a) droite\b", wanted):
        return "droite"
    if re.search(r"\bdu (milieu|centre)\b", wanted):
        return "milieu"
    return None


@dataclass
class OpeningPick:
    placed: list[Placed]
    """Une seule : trouvée ; plusieurs : ambiguë ; aucune : introuvable."""
    label: str
    why: str = ""
    """Pourquoi aucune (pièce sans ouverture de cette sorte…)."""


def resolve_opening(
    project: Project, level_id: UUID, target: str | None, hint: str | None = None
) -> OpeningPick:
    """L'ouverture désignée : nom exact, puis sorte, pièce, orientation, place sur le mur."""
    level = project.levels[level_id]
    graph = WallGraph.from_level(level)
    label = (target or "l'ouverture").strip()
    wanted = _fold(target or "")
    openings = [
        o
        for o in level.elements.values()
        if isinstance(o, Opening) and o.visible and o.host_wall in graph.walls
    ]
    named = [o for o in openings if o.name and _fold(o.name) and _fold(o.name) == wanted]
    if len(named) == 1:
        return OpeningPick([_place(graph, level, named[0], [])], label)
    kinds = _kinds(wanted, hint)
    if kinds is not None:
        openings = [o for o in openings if o.kind in kinds]
    rooms = _named_rooms(level, wanted)
    if rooms is not None:
        if not rooms:
            return OpeningPick([], label, "je ne trouve pas cette pièce dans le plan")
        walls = {w for r in rooms for w in r.boundary_walls}
        openings = [o for o in openings if o.host_wall in walls]
    placed = [_place(graph, level, o, rooms or []) for o in openings]
    if not placed:
        return OpeningPick([], label)
    compass = _compass_word(wanted)
    if compass is not None:
        placed = [p for p in placed if p.compass == compass]
        if not placed:
            return OpeningPick([], label, f"aucune ouverture de cette sorte côté {compass}")
    position = _position_word(wanted)
    if position is not None and len(placed) > 1:
        walls_hit = {p.opening.host_wall for p in placed}
        if len(walls_hit) == 1:
            ordered = sorted(placed, key=lambda p: -p.left * _center(p.opening))
            pick = {"gauche": ordered[0], "droite": ordered[-1]}.get(position)
            if position == "milieu" and len(ordered) % 2 == 1:
                pick = ordered[len(ordered) // 2]
            if pick is not None:
                placed = [pick]
    return OpeningPick(placed, label)


def _center(o: Opening) -> float:
    return o.offset + o.width / 2


def describe(placed: list[Placed]) -> list[str]:
    """« la fenêtre nord », « la fenêtre nord de gauche », « la porte est de Chambre 2 » : ce
    qu'il faut redire pour lever l'ambiguïté."""
    rooms = {p.room.id if p.room else None for p in placed}
    out = []
    for p in placed:
        words = [_the(p.opening), p.compass]
        same = [
            q
            for q in placed
            if q.opening.host_wall == p.opening.host_wall and _noun(q.opening) == _noun(p.opening)
        ]
        if len(same) > 1:
            ordered = sorted(same, key=lambda q: -q.left * _center(q.opening))
            rank = ordered.index(p)
            if rank == 0:
                words.append("de gauche")
            elif rank == len(ordered) - 1:
                words.append("de droite")
            else:
                words.append(f"n° {rank + 1} depuis la gauche")
        if len(rooms) > 1 and p.room is not None and p.room.name:
            words.append(f"({p.room.name})")
        out.append(" ".join(words))
    return out


def _or(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ou " + names[-1]


_VERBS = {
    "move_opening": "Déplacer",
    "remove_opening": "Supprimer",
    "resize_opening": "Redimensionner",
}


def _not_one(intent: AssistantIntent, pick: OpeningPick) -> AssistantAction:
    verb = _VERBS[intent.kind]
    if not pick.placed:
        reason = pick.why or f"je ne trouve pas « {pick.label} » dans le plan"
        return AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{verb} {pick.label}",
            reason=reason + " ; cliquez l'ouverture dans le plan pour la modifier à la main",
        )
    names = describe(pick.placed[:6])
    feminine = any(p.opening.kind is not OpeningKind.PASSAGE for p in pick.placed)
    return AssistantAction(
        kind=intent.kind,
        status="question",
        summary=f"{verb} {pick.label}",
        reason=f"{'Laquelle' if feminine else 'Lequel'} : {_or(names)} ?",
        element_ids=[p.opening.id for p in pick.placed],
    )


def _end_margin(graph: WallGraph, o: Opening, at_start: bool) -> float:
    """Jambage à garder au bout du mur : un angle (autre mur au sommet) prend la moitié de
    l'épaisseur du mur qui arrive, plus un jeu."""
    wall = graph.wall(o.host_wall)
    vertex = wall.start_vertex if at_start else wall.end_vertex
    others = [graph.wall(w) for w in graph.walls_at(vertex) if w != o.host_wall]
    corner = max((w.thickness / 2 + CORNER_CLEARANCE_M for w in others), default=0.0)
    return max(OPENING_MARGIN_M, corner)


def _misfit_height(level: Level, o: Opening, height: float) -> str | None:
    if height < MIN_SIZE_M - 1e-9:
        return f"une ouverture fait au moins {metres(MIN_SIZE_M)} de haut"
    if o.sill_height + height > level.height - CEILING_GAP_M + 1e-9:
        return (
            f"elle monterait à {metres(o.sill_height + height)}, au ras ou au-dessus du plafond "
            f"({metres(level.height)})"
        )
    return None


def _misfit(graph: WallGraph, level: Level, o: Opening, offset: float, width: float) -> str | None:
    """Pourquoi la nouvelle place (ou largeur) le long du mur ne va pas, ou None."""
    length = graph.wall_length(o.host_wall)
    if width < MIN_SIZE_M - 1e-9:
        return f"une ouverture fait au moins {metres(MIN_SIZE_M)} de large"
    if width > MAX_WIDTH_M + 1e-9:
        return f"au plus {metres(MAX_WIDTH_M)} de large"
    start, end = _end_margin(graph, o, True), _end_margin(graph, o, False)
    if offset < -1e-9 or offset + width > length + 1e-9:
        return f"elle sortirait de son mur ({metres(length)} de long)"
    if offset < start - 1e-9 or offset + width > length - end + 1e-9:
        return (
            f"elle mordrait sur l'angle du mur (il faut {metres(min(start, end))} au moins entre "
            "l'ouverture et l'angle)"
        )
    for other in level.elements.values():
        if (
            isinstance(other, Opening)
            and other.id != o.id
            and other.host_wall == o.host_wall
            and offset < other.offset + other.width - 1e-9
            and other.offset < offset + width - 1e-9
        ):
            return f"elle chevaucherait la {_noun(other)} du même mur"
    return None


def _low(title: str) -> str:
    return title[0].lower() + title[1:]


def _apply(
    project: Project,
    level_id: UUID,
    intent: AssistantIntent,
    op: UpdateElement | RemoveElement,
    label: str,
    summary: str,
    o: Opening,
    *,
    asked: bool = False,
) -> tuple[Project, AssistantAction]:
    """Le geste par la commande d'édition du plan, un pas d'historique attribué à
    `assistant` ; refusé si l'ouverture est verrouillée, par la politique des sources ou par
    les invariants. `asked` : demande explicite et sans ambiguïté de l'utilisateur (décision PO
    de KAN-32 pour la suppression) ; la politique qui protège ce qu'il a posé lui-même ne
    s'oppose pas à sa propre demande, annulable."""
    if o.locked:
        it = "la" if _e(o) else "le"
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=summary,
            reason=f"{_the(o)} est verrouillé{_e(o)} : déverrouillez-{it} dans sa fiche",
            element_ids=[o.id],
        )
    actor = USER_ACTOR if asked and o.source in USER_AUTHORITATIVE else ASSISTANT_ACTOR
    try:
        after = apply_ops(project, level_id, [op], actor=actor, label=label)
    except history.PatchRejectedError as exc:
        reason = "changement refusé : " + " ; ".join(exc.problems)[:160]
        return project, AssistantAction(
            kind=intent.kind, status="refused", summary=summary, reason=reason, element_ids=[o.id]
        )
    except OpError as exc:
        return project, AssistantAction(
            kind=intent.kind, status="refused", summary=summary, reason=str(exc)[:160]
        )
    if not validate_invariants(after).valid:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=summary,
            reason="le plan deviendrait incohérent ; modifiez l'ouverture à la main",
            element_ids=[o.id],
        )
    if actor != ASSISTANT_ACTOR:
        # Le pas reste celui de l'assistant dans l'historique (il se distingue des gestes à
        # la main) ; seule la vérification des sources a laissé passer la demande.
        last = after.history[-1].model_copy(update={"actor": ASSISTANT_ACTOR})
        after = after.model_copy(update={"history": [*after.history[:-1], last]})
    return after, AssistantAction(
        kind=intent.kind, status="done", summary=summary, element_ids=[o.id]
    )


def _one(
    project: Project, level_id: UUID, intent: AssistantIntent
) -> tuple[Placed, str] | AssistantAction:
    """L'ouverture visée et son nom (« La fenêtre nord »), ou la question / le refus."""
    pick = resolve_opening(project, level_id, intent.target, intent.opening)
    if len(pick.placed) != 1:
        return _not_one(intent, pick)
    placed = pick.placed[0]
    name = describe([placed])[0]
    return placed, name[0].upper() + name[1:]


def move_opening(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    """Le long de son mur, de l'écart écrit (`delta_m`, négatif vers la gauche)."""
    del rid
    found = _one(project, level_id, intent)
    if isinstance(found, AssistantAction):
        return project, found
    placed, title = found
    o = placed.opening
    if not intent.delta_m:
        return project, AssistantAction(
            kind=intent.kind,
            status="question",
            summary=f"Déplacer {_low(title)}",
            reason="De combien, et vers où ? (par exemple « de 50 cm vers la gauche »)",
            element_ids=[o.id],
        )
    shift = abs(intent.delta_m)
    toward = "la gauche" if intent.delta_m < 0 else "la droite"
    offset = round(o.offset - placed.left * intent.delta_m, 3)
    level = project.levels[level_id]
    why = _misfit(WallGraph.from_level(level), level, o, offset, o.width)
    if why is not None:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Déplacer {_low(title)} de {metres(shift)} vers {toward}",
            reason=f"{why} : {_the(o)} reste où elle est"
            if _e(o)
            else f"{why} : {_the(o)} reste où il est",
            element_ids=[o.id],
        )
    return _apply(
        project,
        level_id,
        intent,
        UpdateElement(element_id=o.id, fields={"offset": offset}),
        f"Assistant : déplacer {_low(title)} de {metres(shift)} vers {toward}",
        f"{title} déplacé{_e(o)} de {metres(shift)} vers {toward} (vu de la pièce, face au mur)",
        o,
    )


def remove_opening(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    del rid
    found = _one(project, level_id, intent)
    if isinstance(found, AssistantAction):
        return project, found
    placed, title = found
    o = placed.opening
    return _apply(
        project,
        level_id,
        intent,
        RemoveElement(element_id=o.id),
        f"Assistant : supprimer {_low(title)}",
        f"{title} supprimé{_e(o)}. Annuler pour {'la' if _e(o) else 'le'} remettre.",
        o,
        asked=True,
    )


def resize_opening(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    """Largeur (autour de son milieu) ou hauteur (l'appui ne bouge pas) : la valeur écrite
    (`value_m`) ou l'écart écrit (`delta_m`)."""
    del rid
    found = _one(project, level_id, intent)
    if isinstance(found, AssistantAction):
        return project, found
    placed, title = found
    o = placed.opening
    tall = intent.dimension == "height"
    what = "hauteur" if tall else "largeur"
    current = o.height if tall else o.width
    if intent.value_m is not None:
        value = intent.value_m
    elif intent.delta_m:
        value = current + intent.delta_m
    else:
        return project, AssistantAction(
            kind=intent.kind,
            status="question",
            summary=f"Redimensionner {_low(title)}",
            reason=f"Quelle {what} lui donner ? (par exemple « à 1,20 m »)",
            element_ids=[o.id],
        )
    value = round(value, 3)
    if abs(value - current) < 1e-6:
        return project, AssistantAction(
            kind=intent.kind,
            status="done",
            summary=f"{title} : {what} déjà de {metres(value)}",
            element_ids=[o.id],
        )
    level = project.levels[level_id]
    fields: dict[str, float]
    if tall:
        fields = {"height": value}
        why = _misfit_height(level, o, value)
    else:
        # Élargie ou rétrécie autour de son milieu : elle ne se décale pas.
        offset = round(o.offset - (value - o.width) / 2, 3)
        fields = {"offset": offset, "width": value}
        why = _misfit(WallGraph.from_level(level), level, o, offset, value)
    if why is not None:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"{title} à {metres(value)} de {what}",
            reason=f"{why} : {_the(o)} garde sa taille",
            element_ids=[o.id],
        )
    return _apply(
        project,
        level_id,
        intent,
        UpdateElement(element_id=o.id, fields=dict(fields)),
        f"Assistant : {what} de {_low(title)} à {metres(value)}",
        f"{title} : {what} {metres(current)} → {metres(value)}",
        o,
    )


__all__ = ["describe", "move_opening", "remove_opening", "resize_opening", "resolve_opening"]
