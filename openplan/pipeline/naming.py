"""Nom automatique d'un projet (accueil L1a, décision d'augus du 6 octobre) : « Grenier ·
6 oct. », d'après le type de pièce (ou de niveau) et la date de création, au lieu de
« WhatsApp Image 2026 08 25 at 13.36.24 ». Un nom donné à la main n'est jamais remplacé ;
les anciens projets ne sont renommés que si leur nom est encore celui d'un fichier."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo

from openplan.scene import history
from openplan.schemas.elements import Room
from openplan.schemas.scene import Project

PARIS = ZoneInfo("Europe/Paris")
MONTHS = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)
ROOM_NAMES: dict[str, str] = {
    "attic": "Grenier",
    "bedroom": "Chambre",
    "living_room": "Séjour",
    "kitchen": "Cuisine",
    "bathroom": "Salle de bain",
    "office": "Bureau",
    "hallway": "Entrée",
}
LEVEL_NAMES: dict[str, str] = {"attic": "Grenier", "basement": "Sous-sol"}
FALLBACK = "Projet"
LABEL = "Nommer le projet"

# Noms d'appareils et de messageries, ou date-heure collée : le nom vient d'un fichier.
_FILE_NAME = re.compile(
    r"^(whatsapp image|img|dsc|dscn|dcim|pxl|photo|image|screenshot|capture d.écran|"
    r"capture|scan|signal)[ _-]*\d|\d{4}[ _.-]\d{2}[ _.-]\d{2}[ _]+(at[ _]+)?\d{2}[ _.-]\d{2}|"
    r"\d{8}[ _-]\d{6}",
    re.IGNORECASE,
)


def looks_like_file_name(name: str) -> bool:
    """Le nom est-il encore celui d'un fichier photo (« IMG 2041 », « WhatsApp Image… ») ?"""
    return bool(_FILE_NAME.search(name.strip()))


def _programme(rooms: list[Room]) -> dict[str, Any] | None:
    for room in rooms:
        meta = room.metadata.get("generated_plan")
        if isinstance(meta, dict) and isinstance(meta.get("programme"), dict):
            return meta["programme"]  # type: ignore[return-value]
    return None


def kind_label(project: Project) -> str | None:
    """Ce que montre le projet : type de la pièce principale, sinon sorte du niveau."""
    for level in project.levels.values():
        rooms = [e for e in level.elements.values() if isinstance(e, Room)]
        programme = _programme(rooms)
        if programme is not None:
            # Plan généré (L3) : « Appartement T3 », « Studio », « Maison ».
            kind = programme.get("kind")
            if kind == "studio":
                return "Studio"
            if kind == "maison":
                return "Maison"
            bedrooms = programme.get("bedrooms")
            return f"Appartement T{(bedrooms if isinstance(bedrooms, int) else 0) + 1}"
        main = next((r for r in rooms if r.intent is not None), rooms[0] if rooms else None)
        room_type = None
        if main is not None:
            room_type = (main.intent.room_type if main.intent else None) or main.room_type
        if room_type in ROOM_NAMES:
            return ROOM_NAMES[room_type]
        if level.kind in LEVEL_NAMES:
            return LEVEL_NAMES[level.kind]
    return None


def auto_name(project: Project, taken: Iterable[str] = ()) -> str:
    """« Grenier · 6 oct. » ; « (2) », « (3) »… si un autre projet porte déjà ce nom."""
    created = project.created_at.astimezone(PARIS)
    base = f"{kind_label(project) or FALLBACK} · {created.day} {MONTHS[created.month - 1]}"
    others = set(taken)
    name, n = base, 2
    while name in others:
        name, n = f"{base} ({n})", n + 1
    return name


def listed_name(project: Project, taken: Iterable[str]) -> Project | None:
    """À l'affichage de la liste (décision PO du 6 octobre, 23 h 40) : un projet d'avant
    0.9.0 dont le nom est encore celui d'un fichier reçoit son nom automatique, enregistré
    sans pas d'historique ; un nom donné à la main (ou déjà automatique) n'est pas touché."""
    if project.name_auto is not None or not looks_like_file_name(project.name):
        return None
    name = auto_name(project, (t for t in taken if t != project.name))
    return project.model_copy(update={"name": name, "name_auto": True})


def rename(project: Project, taken: Iterable[str], *, actor: str) -> Project | None:
    """Nouveau projet si le nom automatique change : nom déjà automatique, ou nom d'un
    projet d'avant 0.9.0 encore tiré d'un fichier ; None sinon (nom donné à la main, ou rien
    à changer). Le pas d'historique est attribué à `actor` (le chemin)."""
    legacy = project.name_auto is None and looks_like_file_name(project.name)
    if not (project.name_auto or legacy):
        return None
    name = auto_name(project, (t for t in taken if t != project.name))
    if name == project.name and project.name_auto:
        return None
    after = project.model_copy(update={"name": name, "name_auto": True})
    return history.commit(project, after, actor=actor, label=LABEL)
