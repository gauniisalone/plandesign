"""La pièce principale d'un niveau, et les pièces d'un plan généré (L3).

Une pièce seule (photo, combles, plan de départ) : c'est elle. Un logement généré : le séjour,
puis la chambre principale, et les pièces habitables qui reçoivent une ambiance et des meubles.
"""

from __future__ import annotations

from openplan.pipeline.derived import compute_derived
from openplan.schemas.elements import Room
from openplan.schemas.scene import Level

GENERATED_KEY = "generated_plan"
ROOM_KEY = "programme_room"
ZONES_KEY = "programme_zones"
HABITABLE = ("living_room", "bedroom", "office", "kitchen")
"""Pièces qui reçoivent une ambiance et des meubles ; ni salle de bain, ni WC, ni dégagement."""


def rooms_of(level: Level) -> list[Room]:
    return [e for e in level.elements.values() if isinstance(e, Room)]


def main_room(level: Level) -> Room | None:
    """La pièce qui a une ambiance (le séjour d'abord s'il y en a plusieurs) ; sinon le séjour,
    sinon la plus grande ; une seule pièce : elle."""
    rooms = rooms_of(level)
    if len(rooms) <= 1:
        return rooms[0] if rooms else None
    pool = [r for r in rooms if r.intent is not None] or rooms
    living = [r for r in pool if r.room_type == "living_room"]
    if len(living) == 1:
        return living[0]
    pool = living or pool
    areas = compute_derived(level).rooms
    return max(pool, key=lambda r: areas[r.id].area_m2 if r.id in areas else 0.0)


def is_generated(level: Level) -> bool:
    """Le niveau porte un plan généré depuis un programme (L3)."""
    return any(GENERATED_KEY in r.metadata for r in rooms_of(level))


def principal_bedroom(level: Level) -> Room | None:
    """La chambre principale : « Chambre 1 » du programme, sinon la plus grande chambre."""
    beds = [r for r in rooms_of(level) if r.room_type == "bedroom"]
    if not beds:
        return None
    first = next((r for r in beds if r.metadata.get(ROOM_KEY) == "ch1"), None)
    if first is not None:
        return first
    areas = compute_derived(level).rooms
    return max(beds, key=lambda r: areas[r.id].area_m2 if r.id in areas else 0.0)


def habitable_rooms(level: Level) -> list[Room]:
    """Pièce principale d'abord, puis les autres pièces habitables d'un plan généré."""
    main = main_room(level)
    if main is None:
        return []
    if not is_generated(level):
        return [main]
    others = [r for r in rooms_of(level) if r.id != main.id and r.room_type in HABITABLE]
    others.sort(key=lambda r: (r.room_type != "bedroom", str(r.metadata.get(ROOM_KEY, r.name))))
    return [main, *others]
