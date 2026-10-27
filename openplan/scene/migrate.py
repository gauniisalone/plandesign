"""Migrations du format de scène entre versions de `schema_version`.

Chaque migration transforme le dictionnaire JSON d'une version vers la suivante.
Le chargement d'un projet passe toujours par `load_project`.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import Any

from openplan.brand import APP_NAME
from openplan.schemas.common import SCHEMA_VERSION
from openplan.schemas.scene import Project

MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]

_MIGRATIONS: dict[str, tuple[str, MigrationFn]] = {}
"""version de départ → (version d'arrivée, fonction)."""


class UnsupportedSchemaVersionError(ValueError):
    """Le projet est dans une version que ce logiciel ne sait pas lire."""


def register(from_version: str, to_version: str) -> Callable[[MigrationFn], MigrationFn]:
    """Déclare une migration. Une seule migration par version de départ."""

    def decorator(fn: MigrationFn) -> MigrationFn:
        if from_version in _MIGRATIONS:
            msg = f"migration déjà enregistrée depuis {from_version}"
            raise ValueError(msg)
        _MIGRATIONS[from_version] = (to_version, fn)
        return fn

    return decorator


def _parse(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        msg = f"schema_version invalide : {version!r}"
        raise UnsupportedSchemaVersionError(msg)
    major, minor, patch = (int(p) for p in parts)
    return major, minor, patch


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Renvoie une copie de `data` portée à SCHEMA_VERSION.

    Lève UnsupportedSchemaVersionError si la version est absente, invalide, inconnue ou
    plus récente que ce logiciel.
    """
    data = copy.deepcopy(data)
    version = data.get("schema_version")
    if not isinstance(version, str):
        msg = "schema_version manquante"
        raise UnsupportedSchemaVersionError(msg)
    if _parse(version) > _parse(SCHEMA_VERSION):
        msg = (
            f"le projet est en {version}, plus récent que {SCHEMA_VERSION} : "
            f"mettre à jour {APP_NAME}"
        )
        raise UnsupportedSchemaVersionError(msg)
    while version != SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            msg = f"aucune migration connue depuis {version}"
            raise UnsupportedSchemaVersionError(msg)
        to_version, fn = step
        data = fn(data)
        data["schema_version"] = to_version
        version = to_version
    return data


def load_project(data: dict[str, Any]) -> Project:
    """Migre puis valide un projet lu sur disque."""
    return Project.model_validate(migrate(data))


@register("0.20.0", "0.21.0")
def _explicit_first_plan(data: dict[str, Any]) -> dict[str, Any]:
    """0.21.0 (KAN-93) : les cartes Photos et Rendus du premier plan portent la `ref` de leur
    plan dans `board.json` (migration faite à la lecture de l'espace de travail,
    `board.explicit_first`) ; `project.json` ne change pas. Version montée pour qu'un retour
    arrière vers 0.20.0 passe par la descente (qui leur retire cette `ref`)."""
    return data


@register("0.19.0", "0.20.0")
def _posts_trusses(data: dict[str, Any]) -> dict[str, Any]:
    """0.20.0 (KAN-94) : poteaux (`post`) et fermes intermédiaires (`truss`) ; un projet 0.19.0
    n'en a pas. Version montée pour qu'un retour arrière vers 0.19.0 passe par la descente."""
    return data


@register("0.18.0", "0.19.0")
def _floor_zones(data: dict[str, Any]) -> dict[str, Any]:
    """0.19.0 (KAN-92) : zones de sol surélevées (`floor_zone`) ; un projet 0.18.0 n'en a pas.
    Version montée pour qu'un retour arrière vers 0.18.0 passe par la descente (qui les retire)."""
    return data


@register("0.17.0", "0.18.0")
def _board_links(data: dict[str, Any]) -> dict[str, Any]:
    """0.18.0 (KAN-91) : l'espace de travail en cartes reliées, dans `board.json` (liens, paires
    coupées, étiquettes ignorées) ; `project.json` ne change pas. Version montée pour qu'un retour
    arrière vers 0.17.0 passe par la descente (qui retire ces champs de `board.json`)."""
    return data


@register("0.16.0", "0.17.0")
def _scale_confidence(data: dict[str, Any]) -> dict[str, Any]:
    """0.17.0 (KAN-66 + KAN-31) : `ScaleBasis.confidence` et `ScaleBasis.confidence_inputs`,
    la confiance de l'estimation tenue à jour avec l'échelle ; un projet 0.16.0 (en production
    depuis la MEP 5) n'en a pas, sa confiance revient à la prochaine estimation ou mesure. Version
    montée pour qu'un retour arrière vers 0.16.0 refuse proprement (409) au lieu de casser."""
    return data


@register("0.15.0", "0.16.0")
def _reference_segments(data: dict[str, Any]) -> dict[str, Any]:
    """0.16.0 (KAN-66) : `ImageRef.reference_segments` (mesure de référence sur une photo) et
    `Level.scale_basis` (origine de l'échelle) ; un projet 0.15.0 n'a ni l'un ni l'autre, ses
    photos sont à réestimer avant de pouvoir y mesurer."""
    return data


@register("0.14.0", "0.15.0")
def _variant_of(data: dict[str, Any]) -> dict[str, Any]:
    """0.15.0 (plusieurs plans, T7e) : `Level.variant_of`, l'étage dont un plan est une
    variante ; tout niveau d'un projet 0.14.0 est un étage (None), sans perte."""
    return data


@register("0.13.0", "0.14.0")
def _auto_on_drop(data: dict[str, Any]) -> dict[str, Any]:
    """0.14.0 (tableau T4) : `render.auto_on_drop`, une photo déposée sur le tableau lance la
    construction du plan ; un projet 0.13.0 la prend activée (défaut)."""
    return data


@register("0.12.0", "0.13.0")
def _pending_answers(data: dict[str, Any]) -> dict[str, Any]:
    """0.13.0 (tableau T1) : `pending_answers`, les réponses guidées de la note Exigences du
    tableau, gardées avec la consigne jusqu'au chemin automatique ; un projet 0.12.0 n'en a
    pas."""
    return data


@register("0.11.0", "0.12.0")
def _programme_zones(data: dict[str, Any]) -> dict[str, Any]:
    """0.12.0 (accueil L3) : zones « cuisine » et « repas » dans l'intention d'une pièce (séjour
    avec cuisine ouverte d'un plan généré) ; un projet 0.11.0 n'en a pas."""
    return data


@register("0.10.0", "0.11.0")
def _remote_text_only(data: dict[str, Any]) -> dict[str, Any]:
    """0.11.0 (accueil L2) : `privacy.remote_text_only`, l'accord donné depuis l'accueil vaut
    pour le texte seul (jamais une image) ; un projet 0.10.0 n'en a pas."""
    return data


@register("0.9.0", "0.10.0")
def _pending_instruction(data: dict[str, Any]) -> dict[str, Any]:
    """0.10.0 (accueil L1b) : `pending_instruction`, la consigne écrite sur l'accueil sans
    fichier ni surface, gardée jusqu'à ce qu'une pièce existe ; un projet 0.9.0 n'en a pas."""
    return data


@register("0.8.0", "0.9.0")
def _auto_name(data: dict[str, Any]) -> dict[str, Any]:
    """0.9.0 (accueil L1a) : `name_auto`, vrai quand le nom du projet est choisi par
    OpenPlan (« Grenier · 6 oct. ») et peut encore être remplacé ; un projet 0.8.0 garde son
    nom (inconnu : seuls les noms encore tirés d'un fichier seront renommés)."""
    return data


@register("0.7.0", "0.8.0")
def _inspiration_setting(data: dict[str, Any]) -> dict[str, Any]:
    """0.8.0 (V1, sortie B) : réglage de l'image d'inspiration du chemin automatique
    (`render.auto_inspiration`, actif par défaut) ; un projet 0.7.0 se lit tel quel."""
    return data


@register("0.6.0", "0.7.0")
def _design_journal(data: dict[str, Any]) -> dict[str, Any]:
    """0.7.0 (V2) : réglage du journal local des demandes (`privacy.design_journal`, actif
    par défaut) ; un projet 0.6.0 se lit tel quel."""
    return data


@register("0.5.0", "0.6.0")
def _render_settings(data: dict[str, Any]) -> dict[str, Any]:
    """0.6.0 (V1, point 6) : réglages du chemin automatique (`Project.render`), aux valeurs
    par défaut ; un projet 0.5.0 se lit tel quel."""
    return data


@register("0.4.0", "0.5.0")
def _design_intent(data: dict[str, Any]) -> dict[str, Any]:
    """0.5.0 (V1, 5 octobre 2026) : intention de design dans la pièce (`Room.intent`).
    L'ambiance C3 (`metadata["ambiance"]`), choisie par l'utilisateur, devient son style
    (`user_defined`) ; une valeur inconnue reste dans les métadonnées, sans perte."""
    from openplan.schemas.intent import STYLES

    for level in data.get("levels", {}).values():
        for element in level.get("elements", {}).values():
            if element.get("type") != "room" or element.get("intent") is not None:
                continue
            metadata = element.get("metadata") or {}
            ambiance = metadata.get("ambiance")
            if isinstance(ambiance, str) and ambiance in STYLES:
                element["intent"] = {
                    "style": ambiance,
                    "sources": {"style": {"source": "user_defined", "confidence": 1.0}},
                }
                element["metadata"] = {k: v for k, v in metadata.items() if k != "ambiance"}
    return data


@register("0.3.0", "0.4.0")
def _opening_library(data: dict[str, Any]) -> dict[str, Any]:
    """0.4.0 (E5, 5 octobre 2026) : nouvelles sortes d'ouvertures (double porte, coulissante,
    galandage, pliante, porte-fenêtre, baie coulissante) et champs `leaves`, `split` aux
    valeurs par défaut ; un projet 0.3.0 se lit tel quel."""
    return data


@register("0.2.0", "0.3.0")
def _parametric_stairs(data: dict[str, Any]) -> dict[str, Any]:
    """0.3.0 (S1) : l'escalier réservé (emprise libre) devient paramétrique. Une emprise
    ancienne donne un escalier droit centré sur elle, monté le long de son grand côté, de la
    largeur de son petit côté (bornée à 0,70–1,20 m) ; le niveau d'arrivée est gardé."""
    for level in data.get("levels", {}).values():
        for element in level.get("elements", {}).values():
            if element.get("type") != "stairs" or "footprint" not in element:
                continue
            pts = [(float(p["x"]), float(p["y"])) for p in element.pop("footprint")]
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            edges = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
            (ax, ay), (bx, by) = max(edges, key=lambda e: math.dist(*e))
            yaw = math.atan2(by - ay, bx - ax)
            ux, uy = -math.sin(yaw), math.cos(yaw)
            across = [x * ux + y * uy for x, y in pts]
            width = max(across) - min(across)
            element.update(
                {
                    "kind": "straight",
                    "position": {"x": cx, "y": cy},
                    "yaw": yaw,
                    "width": min(1.2, max(0.7, width)),
                    "rise": 2.70,
                    "turn": "left",
                    "railing": True,
                }
            )
    return data


@register("0.1.0", "0.2.0")
def _add_roof_windows(data: dict[str, Any]) -> dict[str, Any]:
    """0.2.0 (D1, 4 octobre 2026) : nouvel élément `roof_window` (velux) et sorte de niveau
    facultative ; un projet 0.1.0 se lit tel quel."""
    return data
