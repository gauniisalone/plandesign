"""Plan généré depuis un programme (accueil L3, note 12 § 4, essai du 8 octobre).

Découpage récursif d'une emprise rectangulaire :

- la zone jour (séjour, avec la cuisine si elle est ouverte) à gauche, sur toute la profondeur ;
- la zone nuit à droite, coupée par un dégagement de 1 m qui dessert chaque pièce ; les pièces
  y forment deux bandes, côté rue et côté jardin, chacune en façade ;
- la profondeur est cherchée pour un rapport 1,3–1,6 et des petits côtés tenus (chambre
  2,40 m, salle de bain 1,60 m, WC 1 m) ; trois passes visent les surfaces nettes ;
- un WC ou un cellier tenu à sa largeur minimale sur toute la profondeur de sa bande garde sa
  surface, le reste devient un placard (cloison), ouvert sur la pièce voisine.

Murs `generated` (confiance 0,3) : ce n'est pas un relevé, le bandeau le dit. Portes de
83 cm (73 cm pour les pièces d'eau), porte d'entrée de 90 cm au bout du dégagement, une
fenêtre par pièce en façade, porte-fenêtre au sud et fenêtre à l'ouest pour le séjour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import pairwise
from uuid import UUID

from pydantic import JsonValue

from openplan.pipeline.derived import compute_derived
from openplan.pipeline.mainroom import GENERATED_KEY, ROOM_KEY, ZONES_KEY
from openplan.pipeline.programme import SIMPLIFIED_ABOVE_M2, Programme
from openplan.scene import history
from openplan.scene.ops import AddOpening, AddWall, DetectRooms, OpError, apply_to_level
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import OpeningKind, Room, Wall
from openplan.schemas.scene import Level, Project

LABEL = "Plan généré depuis votre programme"
ACTOR = "pipeline:programme"
EXT, PART = 0.20, 0.07
CORRIDOR = 1.0 + PART
NET = EXT / 2 + PART / 2
DOOR, WET_DOOR, ENTRY = 0.83, 0.73, 0.90
CONFIDENCE = 0.3
MIN_PLACARD_M = 0.6
"""En dessous, le surplus d'un WC n'est pas un placard utile : il reste au WC (bandeau)."""


@dataclass
class Space:
    key: str
    label: str
    room_type: str
    target: float
    min_side: float = 0.9
    rect: tuple[float, float, float, float] | None = None
    service: bool = False
    """WC, cellier, dressing : un peu grand, il devient placard plutôt que pièce vide."""


@dataclass
class Layout:
    sx: float
    sy: float
    xd: float
    spaces: list[Space]
    corridor: tuple[float, float, float, float]
    placards: list[tuple[Space, Space]]
    """(placard, pièce voisine qui l'ouvre)."""


# ------------------------------------------------------------------ programme → pièces
def spaces_of(p: Programme) -> list[Space]:
    out: list[Space] = []
    small = p.area_m2 < 40
    for i in range(p.bedrooms):
        out.append(
            Space(f"ch{i + 1}", f"Chambre {i + 1}", "bedroom", 12.0 if i == 0 else 10.0, 2.4)
        )
    for i in range(p.bathrooms):
        label = "Salle de bain" if p.bathrooms == 1 else f"Salle de bain {i + 1}"
        out.append(Space(f"sdb{i + 1}", label, "bathroom", 4.0 if small else 5.0, 1.6))
    for i in range(p.shower_rooms):
        out.append(Space(f"se{i + 1}", "Salle d'eau", "bathroom", 3.5, 1.4))
    if p.wc:
        out.append(Space("wc", "WC", "other", 1.5, 1.0, service=True))
    if p.kitchen == "fermee":
        out.append(Space("cuisine", "Cuisine", "kitchen", 8.0, 1.8))
    if p.office == "piece":
        out.append(Space("bureau", "Bureau", "office", 7.0, 2.1))
    for extra in p.extras:
        out.append(Space(extra, extra.capitalize(), "other", 3.0, 1.2, service=True))
    if p.kind == "studio":
        living = "Pièce de vie"
    else:
        living = "Séjour" if p.kitchen == "fermee" else "Séjour et cuisine"
    out.insert(0, Space("sejour", living, "living_room", 0.0, 3.0))
    return out


def size_targets(p: Programme) -> list[Space]:
    """Séjour = le reste ; s'il passe sous 20 m² (14 pour un studio), tout est réduit."""
    spaces = spaces_of(p)
    rest = sum(s.target for s in spaces[1:])
    corridor_guess = 0.10 * p.area_m2 if len(spaces) > 1 else 0.0
    living = p.area_m2 - rest - corridor_guess
    floor = 14.0 if p.kind == "studio" else 20.0
    if living < floor and rest:
        k = max(0.5, (p.area_m2 - corridor_guess - floor) / rest)
        for s in spaces[1:]:
            s.target = max(9.0, s.target * k) if s.room_type == "bedroom" else s.target * k
        living = max(floor, p.area_m2 - corridor_guess - sum(s.target for s in spaces[1:]))
    spaces[0].target = living
    return spaces


# ------------------------------------------------------------------ découpage
def _strips(night: list[Space]) -> tuple[list[Space], list[Space]]:
    """Deux bandes : chambres alternées, autres pièces dans la bande la moins chargée ;
    cuisine fermée et bureau au plus près du séjour, chambres au fond."""
    top: list[Space] = []
    bottom: list[Space] = []

    def load(strip: list[Space]) -> float:
        return sum(x.target for x in strip)

    beds = [s for s in night if s.room_type == "bedroom"]
    others = sorted((s for s in night if s.room_type != "bedroom"), key=lambda s: -s.target)
    for i, s in enumerate(beds):
        (bottom if i % 2 == 0 else top).append(s)
    for s in others:
        (top if load(top) <= load(bottom) else bottom).append(s)
    order = {"kitchen": 0, "office": 1, "bathroom": 2, "other": 3, "bedroom": 4}
    top.sort(key=lambda s: order[s.room_type])
    bottom.sort(key=lambda s: order[s.room_type])
    return top, bottom


def _widths(strip: list[Space], width: float) -> list[float]:
    """Largeurs proportionnelles aux surfaces, jamais sous le petit côté minimal (net)."""
    total = sum(s.target for s in strip)
    widths = [s.target / total * width for s in strip]
    need = [s.min_side + PART for s in strip]
    for _ in range(6):
        fixed = [i for i in range(len(strip)) if widths[i] < need[i] - 1e-9]
        if not fixed:
            break
        for i in fixed:
            widths[i] = need[i]
        free = [i for i in range(len(strip)) if widths[i] > need[i] + 1e-9]
        rest = width - sum(widths[i] for i in range(len(strip)) if i not in free)
        share = sum(strip[i].target for i in free)
        for i in free:
            widths[i] = strip[i].target / share * rest
    return widths


def _night(top: list[Space], bottom: list[Space], sy: float) -> tuple[float, float, float]:
    """Largeur de la zone nuit et profondeurs des deux bandes, avec les surfaces réellement
    prises (une pièce tenue à sa largeur minimale prend plus que sa cible)."""
    depth = sy - CORRIDOR

    def eff(strip: list[Space], d: float) -> float:
        return sum(max(s.target, (s.min_side + PART) * d) for s in strip)

    at, ab = sum(s.target for s in top), sum(s.target for s in bottom)
    wn = dt = db = 0.0
    for _ in range(8):
        wn = (at + ab) / depth
        dt = depth * at / (at + ab) if top else 0.0
        db = depth - dt
        at, ab = eff(top, dt), eff(bottom, db)
    if top and not bottom:
        # Une seule bande (studio) : elle prend la profondeur d'une pièce carrée, l'entrée
        # prend le reste.
        total = sum(s.target for s in top)
        dt = min(depth, max(top[0].min_side + NET, math.sqrt(total) + NET))
        wn = total / dt
    wn = max(wn, *(sum(s.min_side + PART for s in strip) for strip in (top, bottom)))
    return wn, dt, db


def slice_plan(spaces: list[Space], total: float) -> Layout:
    """Profondeur cherchée de 4,5 à 14,5 m : rapport proche de 1,45, petits côtés tenus,
    surfaces subies (pièces tenues à leur minimum) et pièces trop allongées pénalisées."""
    living, night = spaces[0], spaces[1:]
    top, bottom = _strips(night) if night else ([], [])
    best: tuple[float, float] | None = None
    for step in range(101):
        sy = 4.5 + step * 0.1
        wn, dt, db = _night(top, bottom, sy) if night else (0.0, 0.0, 0.0)
        living_area = total - wn * sy
        if living_area < 12:
            continue
        xd = living_area / sy
        sx = xd + wn
        score = abs(max(sx, sy) / min(sx, sy) - 1.45) * 3
        sides = [(xd - NET, living.min_side), (sy - EXT, living.min_side)]
        for strip, d in ((top, dt), (bottom, db)):
            if not strip:
                continue
            for s, w in zip(strip, _widths(strip, wn), strict=True):
                sides += [(w - PART, s.min_side), (d - NET, s.min_side)]
                score += abs(w * d - s.target) / s.target * (0.3 if s.service else 1.5)
                if not s.service:
                    score += max(0.0, max(w, d) / min(w, d) - 2.0)
        score += 10 * sum(max(0.0, need - got) for got, need in sides)
        if best is None or score < best[0]:
            best = (score, sy)
    if best is None:
        msg = "programme impossible à découper : surface trop petite pour les pièces demandées"
        raise OpError(msg)
    sy = best[1]
    wn, dt, db = _night(top, bottom, sy) if night else (0.0, 0.0, 0.0)
    xd = (total - wn * sy) / sy
    sx = xd + wn
    living = replace(living, rect=(0.0, 0.0, xd, sy))
    out = [living]
    corridor = (0.0, 0.0, 0.0, 0.0)
    placards: list[tuple[Space, Space]] = []
    if night:
        yb = db if bottom else 0.0
        corridor = (xd, yb, sx, sy - dt)
        for strip, y0, y1, low in ((bottom, 0.0, yb, True), (top, sy - dt, sy, False)):
            x = xd
            placed: list[Space] = []
            for s, w in zip(strip, _widths(strip, sx - xd), strict=True):
                placed.append(replace(s, rect=(x, y0, min(sx, x + w), y1)))
                x += w
            if placed:
                last = placed[-1]
                assert last.rect is not None
                placed[-1] = replace(last, rect=(last.rect[0], y0, sx, y1))
            out += _with_placards(placed, low, placards)
    return Layout(sx, sy, xd, out, corridor, placards)


def _with_placards(
    strip: list[Space], low: bool, placards: list[tuple[Space, Space]]
) -> list[Space]:
    """Un WC ou un cellier bien plus grand que voulu garde sa surface côté dégagement ; le
    fond, côté façade, devient un placard ouvert sur la pièce voisine (une chambre de
    préférence)."""
    out: list[Space] = []
    for i, s in enumerate(strip):
        assert s.rect is not None
        x0, y0, x1, y1 = s.rect
        width = x1 - x0 - PART
        depth = s.target / width + NET if width > 0 else 0.0
        spare = (y1 - y0) - max(depth, s.min_side + NET)
        if not s.service or spare < MIN_PLACARD_M:
            out.append(s)
            continue
        cut = y1 - (y1 - y0 - spare) if low else y0 + (y1 - y0 - spare)
        kept = replace(s, rect=(x0, cut, x1, y1) if low else (x0, y0, x1, cut))
        closet = Space(
            f"placard-{s.key}",
            "Placard",
            "other",
            round(width * (spare - PART), 1),
            0.6,
            rect=(x0, y0, x1, cut) if low else (x0, cut, x1, y1),
        )
        neighbours = [strip[j] for j in (i + 1, i - 1) if 0 <= j < len(strip)]
        opener = next((n for n in neighbours if n.room_type == "bedroom"), None) or (
            neighbours[0] if neighbours else kept
        )
        placards.append((closet, opener))
        out += [kept, closet]
    return out


# ------------------------------------------------------------------ murs, pièces, ouvertures
def _segments(layout: Layout) -> list[tuple[Point2, Point2, float]]:
    """Chaque bord de rectangle, coupé à chaque jonction en T et dédoublonné ; murs extérieurs
    sur le pourtour, cloisons ailleurs."""
    sx, sy = layout.sx, layout.sy
    rects = [s.rect for s in layout.spaces if s.rect is not None]
    if len(layout.spaces) > 1:
        rects.append(layout.corridor)
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for x0, y0, x1, y1 in rects:
        edges += [
            ((x0, y0), (x1, y0)),
            ((x1, y0), (x1, y1)),
            ((x0, y1), (x1, y1)),
            ((x0, y0), (x0, y1)),
        ]
    points = {_key(p) for e in edges for p in e}
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    out: list[tuple[Point2, Point2, float]] = []
    for a, b in edges:
        horizontal = abs(a[1] - b[1]) < 1e-6
        cuts = sorted(
            {_key(a), _key(b), *(q for q in points if _on_segment(q, a, b))},
            key=lambda c: c[0] if horizontal else c[1],
        )
        for c0, c1 in pairwise(cuts):
            k = (c0, c1) if c0 <= c1 else (c1, c0)
            if k in seen or math.dist(c0, c1) < 1e-3:
                continue
            seen.add(k)
            outer = (horizontal and (abs(c0[1]) < 1e-3 or abs(c0[1] - sy) < 1e-3)) or (
                not horizontal and (abs(c0[0]) < 1e-3 or abs(c0[0] - sx) < 1e-3)
            )
            out.append((Point2(x=c0[0], y=c0[1]), Point2(x=c1[0], y=c1[1]), EXT if outer else PART))
    return out


def _key(p: tuple[float, float]) -> tuple[float, float]:
    return (round(p[0], 4), round(p[1], 4))


def _on_segment(q: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> bool:
    (qx, qy), (ax, ay), (bx, by) = q, a, b
    length = math.dist(a, b)
    if length < 1e-6 or abs((bx - ax) * (qy - ay) - (by - ay) * (qx - ax)) / length > 1e-3:
        return False
    return (min(ax, bx) + 1e-3 < qx < max(ax, bx) - 1e-3) or (
        min(ay, by) + 1e-3 < qy < max(ay, by) - 1e-3
    )


def _wall_on(level: Level, a: tuple[float, float], b: tuple[float, float]) -> Wall | None:
    def near(p: Point2, q: tuple[float, float]) -> bool:
        return abs(p.x - q[0]) < 2e-3 and abs(p.y - q[1]) < 2e-3

    for e in level.elements.values():
        if isinstance(e, Wall):
            p0 = level.vertices[e.start_vertex].position
            p1 = level.vertices[e.end_vertex].position
            if (near(p0, a) and near(p1, b)) or (near(p0, b) and near(p1, a)):
                return e
    return None


def _opening(
    level: Level,
    wall: Wall,
    at: tuple[float, float],
    kind: OpeningKind,
    width: float,
    height: float,
    sill: float,
) -> AddOpening:
    """Ouverture dont le bord commence en `at`, gardée dans le mur (10 cm de chaque côté)."""
    p0 = level.vertices[wall.start_vertex].position
    p1 = level.vertices[wall.end_vertex].position
    length = math.dist((p0.x, p0.y), (p1.x, p1.y))
    width = min(width, length - 0.2)
    offset = min(max(0.1, math.dist((p0.x, p0.y), at)), length - width - 0.1)
    # mesurée depuis le bord de départ : si `at` est le bord de fin, l'ouverture recule
    return AddOpening(
        host_wall=wall.id,
        kind=kind,
        offset=round(offset, 3),
        width=round(width, 3),
        height=height,
        sill_height=sill,
        source=Source.GENERATED,
        confidence=CONFIDENCE,
    )


def _openings(level: Level, layout: Layout) -> list[AddOpening]:  # noqa: PLR0912, PLR0915 - une ouverture par cas
    ops: list[AddOpening] = []
    sx, sy, xd = layout.sx, layout.sy, layout.xd
    placard_keys = {c.key for c, _ in layout.placards}
    if len(layout.spaces) > 1:
        _, cy0, _, cy1 = layout.corridor
        for s in layout.spaces[1:]:
            if s.key in placard_keys:
                continue
            assert s.rect is not None
            rx0, ry0, rx1, ry1 = s.rect
            y = cy0 if abs(ry1 - cy0) < 1e-3 else cy1
            wall = _wall_on(level, (rx0, y), (rx1, y))
            if wall is not None:
                width = WET_DOOR if s.room_type in ("bathroom", "other") else DOOR
                ops.append(
                    _opening(level, wall, (rx0 + 0.15, y), OpeningKind.DOOR, width, 2.04, 0.0)
                )
            fy = 0.0 if ry0 < 1e-3 else sy if abs(ry1 - sy) < 1e-3 else None
            if fy is None or s.room_type == "other":
                continue
            wall = _wall_on(level, (rx0, fy), (rx1, fy))
            if wall is not None:
                wet = s.room_type == "bathroom"
                width = 0.6 if wet else min(1.2, rx1 - rx0 - 0.6)
                at = ((rx0 + rx1) / 2 - width / 2, fy)
                ops.append(
                    _opening(
                        level,
                        wall,
                        at,
                        OpeningKind.WINDOW,
                        width,
                        0.6 if wet else 1.25,
                        1.4 if wet else 0.9,
                    )
                )
        wall = _wall_on(level, (xd, cy0), (xd, cy1))
        if wall is not None:
            ops.append(_opening(level, wall, (xd, cy0 + 0.08), OpeningKind.DOOR, DOOR, 2.04, 0.0))
        wall = _wall_on(level, (sx, cy0), (sx, cy1))
        if wall is not None:
            ops.append(_opening(level, wall, (sx, cy0 + 0.05), OpeningKind.DOOR, ENTRY, 2.04, 0.0))
        for closet, opener in layout.placards:
            assert closet.rect is not None and opener.rect is not None
            x0, y0, x1, y1 = closet.rect
            x = (
                x1
                if abs(opener.rect[0] - x1) < 1e-3
                else x0
                if abs(opener.rect[2] - x0) < 1e-3
                else None
            )
            if x is None:  # pas de voisin : le placard ouvre sur sa propre pièce
                y = y1 if abs(opener.rect[1] - y1) < 1e-3 else y0
                wall = _wall_on(level, (x0, y), (x1, y))
                at = (x0 + 0.1, y)
            else:
                wall = _wall_on(level, (x, y0), (x, y1))
                at = (x, y0 + 0.1)
            if wall is not None:
                ops.append(_opening(level, wall, at, OpeningKind.FOLDING_DOOR, 0.8, 2.04, 0.0))
    else:
        wall = _wall_on(level, (sx, 0), (sx, sy))
        if wall is not None:
            ops.append(_opening(level, wall, (sx, 0.4), OpeningKind.DOOR, ENTRY, 2.04, 0.0))
    south = _wall_on(level, (0, 0), (xd, 0))
    if south is not None:
        width = min(2.0, xd - 1.0)
        at = (xd / 2 - width / 2, 0.0)
        ops.append(_opening(level, south, at, OpeningKind.FRENCH_WINDOW, width, 2.15, 0.0))
    west = _wall_on(level, (0, sy), (0, 0))
    if west is not None:
        ops.append(_opening(level, west, (0, sy / 2 + 0.6), OpeningKind.WINDOW, 1.2, 1.25, 0.9))
    return ops


def _zones(p: Programme, layout: Layout) -> dict[str, list[str]]:
    """Zones voulues par pièce : cuisine ouverte et repas au séjour, coin nuit au studio, coin
    bureau au séjour s'il a la place (22 m²), sinon à la chambre principale."""
    living = layout.spaces[0]
    zones: dict[str, list[str]] = {}
    base = ["detente"]
    if p.kind == "studio":
        base = ["couchage", "detente"]
    if p.kitchen == "ouverte":
        base += ["cuisine", "repas"]
    elif p.kind != "studio":
        base += ["repas"]
    zones[living.key] = base
    if p.kitchen == "fermee":
        zones["cuisine"] = ["cuisine"]
    if p.office == "coin":
        if living.target >= 22.0 + (8.0 if p.kitchen == "ouverte" else 0.0) or p.bedrooms == 0:
            zones[living.key] = [*zones[living.key], "bureau"]
        else:
            zones["ch1"] = ["couchage", "bureau"]
    return zones


def _build_level(level: Level, layout: Layout, p: Programme, notes: list[str]) -> Level:
    walls = [
        AddWall(start=a, end=b, thickness=t, source=Source.GENERATED, confidence=CONFIDENCE)
        for a, b, t in _segments(layout)
    ]
    level = apply_to_level(level, [*walls, DetectRooms()], actor=ACTOR)
    derived = compute_derived(level)
    rects = [(s.rect, s) for s in layout.spaces if s.rect is not None]
    hallway = Space("degagement", "Entrée et dégagement", "hallway", 0.0, 0.9, rect=layout.corridor)
    if len(layout.spaces) > 1:
        rects.append((layout.corridor, hallway))
    zones = _zones(p, layout)
    elements = dict(level.elements)
    for rid, d in derived.rooms.items():
        cx = sum(q.x for q in d.polygon) / len(d.polygon)
        cy = sum(q.y for q in d.polygon) / len(d.polygon)
        for rect, s in rects:
            if rect[0] < cx < rect[2] and rect[1] < cy < rect[3]:
                room = elements[rid]
                assert isinstance(room, Room)
                metadata: dict[str, JsonValue] = {**room.metadata, ROOM_KEY: s.key}
                if s.key in zones:
                    metadata[ZONES_KEY] = list(zones[s.key])
                if s.key == "sejour":
                    metadata[GENERATED_KEY] = {
                        "programme": p.model_dump(mode="json", exclude={"notes"}),
                        "notes": list(notes),
                        "simplified": p.area_m2 > SIMPLIFIED_ABOVE_M2,
                    }
                elements[rid] = room.model_copy(
                    update={"name": s.label, "room_type": s.room_type, "metadata": metadata}
                )
                break
    level = level.model_copy(update={"elements": elements})
    return apply_to_level(level, list(_openings(level, layout)), actor=ACTOR)


def _net_areas(level: Level) -> dict[str, float]:
    derived = compute_derived(level)
    return {
        str(e.metadata.get(ROOM_KEY)): derived.rooms[e.id].area_m2
        for e in level.elements.values()
        if isinstance(e, Room) and e.id in derived.rooms
    }


def generate_plan(
    project: Project, level_id: UUID, programme: Programme, *, actor: str = ACTOR, rounds: int = 3
) -> tuple[Project, list[str]]:
    """Le plan du programme sur un niveau vide, en un pas d'historique ; les notes (surfaces
    supposées, plan simplifié, WC agrandi) vont au bandeau."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = "ce niveau a déjà des murs : le plan généré ne s'ajoute qu'à un plan vide"
        raise OpError(msg)
    spaces = size_targets(programme)
    wanted = {s.key: s.target for s in spaces}
    axis = dict(wanted)
    total = programme.area_m2
    layout = slice_plan(spaces, total)
    built = _build_level(level, layout, programme, [])
    # Les murs mangent la surface : chaque passe gonfle chaque pièce de nuit de ce que ses murs
    # lui ont pris, et l'emprise du manque du séjour (une pièce tenue à son minimum n'est pas
    # rétrécie).
    for _ in range(rounds):
        got = _net_areas(built)
        total += wanted["sejour"] - got.get("sejour", wanted["sejour"])
        for s in spaces[1:]:
            if got.get(s.key):
                axis[s.key] *= min(1.3, max(0.97, wanted[s.key] / got[s.key]))
        layout = slice_plan([replace(s, target=axis[s.key]) for s in spaces], total)
        built = _build_level(level, layout, programme, [])
    notes = list(programme.notes)
    if programme.area_m2 > SIMPLIFIED_ABOVE_M2:
        notes.append("plan simplifié, une seule circulation")
    got = _net_areas(built)
    for s in spaces:
        if s.service and s.key in got and got[s.key] > wanted[s.key] * 1.4:
            notes.append(f"{s.label} de {got[s.key]:.1f} m² : trop étroit pour y loger un placard")
    closets = {c.key for c, _ in layout.placards}
    hosts = [s.label for s in spaces if f"placard-{s.key}" in closets]
    if hosts:
        where = " et ".join(f"du {h}" if h == "WC" else f"du {h.lower()}" for h in hosts)
        notes.append(f"placard au fond {where}, plutôt qu'une pièce trop grande")
    built = _build_level(level, layout, programme, notes)
    after = project.model_copy(update={"levels": {**project.levels, level_id: built}})
    return history.commit(project, after, actor=actor, label=LABEL), notes
