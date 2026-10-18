"""Invariants structurels du format de scène (docs/architecture/01 §7).

Ces règles portent sur la cohérence des références et des sources, pas sur la qualité
géométrique (aires, intersections), qui relève de `openplan.geom` et de l'incrément 2.
"""

from __future__ import annotations

import itertools
import math
from uuid import UUID

from openplan.schemas.common import USER_AUTHORITATIVE
from openplan.schemas.elements import (
    Ceiling,
    Floor,
    Opening,
    Radiator,
    RoofPlane,
    RoofWindow,
    Room,
    Vertex,
    Wall,
)
from openplan.schemas.results import ValidationReport, Violation
from openplan.schemas.scene import Level, Project
from openplan.validation.consistency import check_views
from openplan.validation.layout import check_layout


def check_invariants(project: Project) -> list[Violation]:
    violations: list[Violation] = []
    for level in project.levels.values():
        violations.extend(_check_level(level))
        if level.background_image is not None and level.background_image not in project.images:
            violations.append(
                Violation(
                    type="orphan_background_image",
                    severity="medium",
                    element_ids=[level.id, level.background_image],
                    message="Le fond de photo du niveau référence une image qui n'existe plus.",
                )
            )
        if level.variant_of is not None:
            storey = project.levels.get(level.variant_of)
            if storey is None or storey.variant_of is not None or storey.id == level.id:
                violations.append(
                    Violation(
                        type="orphan_variant",
                        severity="high",
                        element_ids=[level.id, level.variant_of],
                        message="Ce plan est la variante d'un étage qui n'existe pas (T7e).",
                    )
                )
    violations.extend(_check_measurements(project))
    violations.extend(check_layout(project))
    violations.extend(check_views(project))
    return violations


def validate_invariants(project: Project) -> ValidationReport:
    return ValidationReport.from_violations(check_invariants(project))


def _check_level(level: Level) -> list[Violation]:
    out: list[Violation] = []
    walls = {eid: e for eid, e in level.elements.items() if isinstance(e, Wall)}
    for vertex in level.vertices.values():
        out.extend(_check_confidence(vertex.id, vertex.source, vertex.confidence, "sommet"))
    for element in level.elements.values():
        out.extend(_check_confidence(element.id, element.source, element.confidence, "élément"))
        if isinstance(element, Wall):
            out.extend(_check_wall(element, level))
        elif isinstance(element, Opening | Radiator):
            out.extend(_check_hosted(element, walls, level))
        elif isinstance(element, Room):
            out.extend(_check_room(element, walls))
        elif isinstance(element, Floor | Ceiling):
            out.extend(_check_surface(element, level))
        elif isinstance(element, RoofWindow) and not isinstance(
            level.elements.get(element.roof), RoofPlane
        ):
            out.append(
                Violation(
                    type="orphan_roof_window",
                    severity="high",
                    element_ids=[element.id],
                    message=f"velux « {element.name or element.id} » sans pente",
                )
            )
    out.extend(_check_openings_overlap(level))
    return out


def _check_openings_overlap(level: Level) -> list[Violation]:
    """Deux ouvertures d'un même mur qui se chevauchent : la 3D absorbe le recouvrement, le
    plan le signale (sévérité moyenne, l'édition reste possible)."""
    by_wall: dict[UUID, list[Opening]] = {}
    for element in level.elements.values():
        if isinstance(element, Opening):
            by_wall.setdefault(element.host_wall, []).append(element)
    out: list[Violation] = []
    for openings in by_wall.values():
        ordered = sorted(openings, key=lambda o: o.offset)
        for previous, current in itertools.pairwise(ordered):
            if current.offset < previous.offset + previous.width - 1e-9:
                out.append(
                    Violation(
                        type="openings_overlap",
                        severity="medium",
                        element_ids=[previous.id, current.id],
                        message=(
                            f"Deux ouvertures se chevauchent sur le même mur "
                            f"({previous.kind.value} à {previous.offset:.2f} m, "
                            f"{current.kind.value} à {current.offset:.2f} m)."
                        ),
                    )
                )
    return out


def _check_confidence(
    item_id: UUID, source: object, confidence: float, label: str
) -> list[Violation]:
    if confidence >= 1.0 and source not in USER_AUTHORITATIVE:
        return [
            Violation(
                type="confidence_without_authority",
                severity="medium",
                element_ids=[item_id],
                message=f"Le {label} a une confiance de 1.0 alors que sa source est « {source} ».",
            )
        ]
    return []


def _check_wall(wall: Wall, level: Level) -> list[Violation]:
    missing = [v for v in (wall.start_vertex, wall.end_vertex) if v not in level.vertices]
    if missing:
        return [
            Violation(
                type="orphan_wall_vertex",
                severity="high",
                element_ids=[wall.id, *missing],
                message="Le mur référence un sommet qui n'existe pas dans ce niveau.",
            )
        ]
    return []


def _check_hosted(
    element: Opening | Radiator, walls: dict[UUID, Wall], level: Level
) -> list[Violation]:
    wall = walls.get(element.host_wall)
    if wall is None:
        return [
            Violation(
                type="orphan_host_wall",
                severity="high",
                element_ids=[element.id, element.host_wall],
                message="L'élément est rattaché à un mur qui n'existe pas.",
            )
        ]
    length = wall_length(wall, level.vertices)
    if length is not None and element.offset + element.width > length + 1e-9:
        return [
            Violation(
                type="hosted_element_exceeds_wall",
                severity="high",
                element_ids=[element.id, wall.id],
                message=(
                    f"L'élément dépasse le mur : décalage {element.offset:.2f} m + largeur "
                    f"{element.width:.2f} m > longueur {length:.2f} m."
                ),
            )
        ]
    return []


def _check_room(room: Room, walls: dict[UUID, Wall]) -> list[Violation]:
    missing = [w for w in room.boundary_walls if w not in walls]
    if missing:
        return [
            Violation(
                type="orphan_room_wall",
                severity="high",
                element_ids=[room.id, *missing],
                message="La pièce référence un mur qui n'existe pas.",
            )
        ]
    loop = [walls[w] for w in room.boundary_walls]
    for current, following in zip(loop, loop[1:] + loop[:1], strict=True):
        shared = {current.start_vertex, current.end_vertex} & {
            following.start_vertex,
            following.end_vertex,
        }
        if not shared:
            return [
                Violation(
                    type="room_not_closed",
                    severity="high",
                    element_ids=[room.id, current.id, following.id],
                    message="Le contour de la pièce n'est pas fermé : deux murs consécutifs "
                    "ne partagent aucun sommet.",
                )
            ]
    return []


def _check_surface(surface: Floor | Ceiling, level: Level) -> list[Violation]:
    out: list[Violation] = []
    room = level.elements.get(surface.room)
    if not isinstance(room, Room):
        out.append(
            Violation(
                type="orphan_surface_room",
                severity="high",
                element_ids=[surface.id, surface.room],
                message="La surface référence une pièce qui n'existe pas.",
            )
        )
    if surface.derived == (surface.polygon is not None):
        out.append(
            Violation(
                type="surface_derived_mismatch",
                severity="medium",
                element_ids=[surface.id],
                message="Une surface dérivée ne porte pas de polygone ; une surface détachée "
                "doit en porter un.",
            )
        )
    return out


def _check_measurements(project: Project) -> list[Violation]:
    element_ids: set[UUID] = set()
    vertex_ids: set[UUID] = set()
    for level in project.levels.values():
        element_ids.update(level.elements)
        vertex_ids.update(level.vertices)
    out: list[Violation] = []
    for m in project.measurements.values():
        unknown: list[UUID] = []
        if m.element_id is not None and m.element_id not in element_ids:
            unknown.append(m.element_id)
        if m.vertex_ids is not None:
            unknown.extend(v for v in m.vertex_ids if v not in vertex_ids)
        if m.image_points is not None and m.image_points[0] not in project.images:
            unknown.append(m.image_points[0])
        if unknown:
            out.append(
                Violation(
                    type="measurement_target_missing",
                    severity="high",
                    element_ids=[m.id, *unknown],
                    message="La mesure cible un élément, un sommet ou une image inexistants.",
                )
            )
    return out


def wall_length(wall: Wall, vertices: dict[UUID, Vertex]) -> float | None:
    """Longueur d'axe d'un mur, ou None si un sommet manque."""
    start = vertices.get(wall.start_vertex)
    end = vertices.get(wall.end_vertex)
    if start is None or end is None:
        return None
    return math.hypot(end.position.x - start.position.x, end.position.y - start.position.y)
