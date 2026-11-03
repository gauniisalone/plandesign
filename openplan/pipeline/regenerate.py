"""KAN-120 : « Régénérer le plan ». Le plan d'un niveau est refait à partir de ses photos, puis
remplacé d'un seul coup : une seule entrée d'historique, Ctrl+Z remet l'ancien plan en entier.

Décision d'augus (3 nov.) : les meubles qui restent dans les nouveaux murs sont gardés, les
autres retirés et comptés. Les vues de rendu (caméras sans photo) et les lumières suivent la
même règle ; ce qui s'accroche à un ancien mur (radiateur, élément fixe posé sur un mur) part
avec lui. Rien ici ne lit ni n'écrit le disque.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from shapely.geometry import MultiPoint, Point, Polygon
from shapely.ops import unary_union

from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.elements import Camera, Fixture, Furniture, Light, Room
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import footprint_corners

TOLERANCE_M = 0.05
"""Un meuble posé contre un mur déborde de quelques centimètres sur son épaisseur."""

Kept = Furniture | Fixture | Light | Camera


@dataclass(frozen=True)
class Regenerated:
    project: Project
    removed_furniture: int
    """Meubles hors des nouveaux murs, retirés (dits à l'utilisateur)."""


def _movable(level: Level) -> dict[UUID, Kept]:
    """Ce qui peut survivre au nouveau plan : meubles, éléments fixes non accrochés à un mur,
    lumières et vues de rendu."""
    out: dict[UUID, Kept] = {}
    for eid, e in level.elements.items():
        if not isinstance(e, Furniture | Fixture | Light | Camera):
            continue
        if isinstance(e, Fixture) and e.host_wall is not None:
            continue  # accroché à un ancien mur
        if isinstance(e, Camera) and e.image_id is not None:
            continue  # caméra estimée d'une photo : refaite par la nouvelle estimation
        out[eid] = e
    return out


def cleared(project: Project, level_id: UUID) -> Project:
    """Le niveau sans rien de ce que l'estimation refait : ni murs ni sommets, ni combles, ni
    échelle tirée d'une photo. Le reste du projet est intact. Jamais enregistré."""
    level = project.levels[level_id]
    blank = level.model_copy(
        update={"vertices": {}, "elements": {}, "kind": None, "scale_basis": None}
    )
    return project.model_copy(update={"levels": {**project.levels, level_id: blank}})


def _inside(level: Level) -> Polygon | None:
    graph = WallGraph.from_level(level)
    parts: list[Polygon] = []
    for e in level.elements.values():
        if isinstance(e, Room):
            try:
                parts.append(graph.room_polygon(e.id))
            except GeometryError:
                continue
    if not parts and level.vertices:
        hull = MultiPoint(
            [(v.position.x, v.position.y) for v in level.vertices.values()]
        ).convex_hull
        parts = [hull] if isinstance(hull, Polygon) else []
    if not parts:
        return None
    area = unary_union(parts).buffer(TOLERANCE_M)
    return area if isinstance(area, Polygon) else Polygon(area.convex_hull)


def _shape(e: Kept) -> Polygon | Point:
    if isinstance(e, Furniture | Fixture):
        t, b = e.transform, e.bbox
        return Polygon(
            footprint_corners(
                t.position.x, t.position.y, t.yaw, b.width * t.scale, b.depth * t.scale
            )
        )
    return Point(e.position.x, e.position.y)


def regenerated(before: Project, rebuilt: Project, level_id: UUID) -> Regenerated:
    """Le nouveau plan (`rebuilt`, construit sur `cleared(before)`), avec ce qui y tient de
    l'ancien niveau. L'historique est celui de `before` : l'appelant le committe en une fois."""
    kept = _movable(before.levels[level_id])
    level = rebuilt.levels[level_id]
    area = _inside(level)
    elements = dict(level.elements)
    removed = 0
    for eid, e in kept.items():
        if area is not None and area.contains(_shape(e)):
            elements[eid] = e
        elif isinstance(e, Furniture):
            removed += 1
    level = level.model_copy(update={"elements": elements})
    project = rebuilt.model_copy(
        update={
            "levels": {**rebuilt.levels, level_id: level},
            "history": before.history,
            "history_cursor": before.history_cursor,
        }
    )
    return Regenerated(project=project, removed_furniture=removed)
