"""Opérations d'édition d'un niveau : commandes typées → nouveau Project historisé.

Le front et la CLI n'envoient jamais de patch JSON brut : ils envoient une commande
(`Op`), le serveur calcule la géométrie et l'historique. Ce module ne vérifie pas les
invariants (couche `validation`, au-dessus) : voir `openplan.pipeline.editing`.

Règle de provenance : quand l'acteur est l'utilisateur, un sommet qu'il déplace devient
`user_defined` (confiance 1.0) et un mur `measured` dont un sommet a bougé redevient
`user_defined`, puisque la longueur mesurée ne tient plus.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from openplan.geom.roof import slope_length
from openplan.geom.wallgraph import Anchor, WallGraph
from openplan.scene import history
from openplan.scene.catalog import CatalogError, asset, furniture_from_asset
from openplan.scene.history import USER_ACTOR, is_user
from openplan.schemas.common import (
    Confidence,
    Meters,
    Point2,
    Point3,
    Source,
    StrictModel,
    Uncertainty,
)
from openplan.schemas.elements import (
    Camera,
    Ceiling,
    Element,
    Floor,
    Furniture,
    Opening,
    OpeningKind,
    Radiator,
    RoofPlane,
    RoofWindow,
    Room,
    StairKind,
    Stairs,
    StairTurn,
    Swing,
    Vertex,
    Wall,
)
from openplan.schemas.intent import DesignIntent, merge_intent
from openplan.schemas.scene import Level, Project

_SNAP = 1e-9
_ELEMENT_ADAPTER: TypeAdapter[Element] = TypeAdapter(Element)


class OpError(ValueError):
    """Commande impossible : référence inconnue, champ interdit, valeur invalide."""


class MoveVertex(StrictModel):
    op: Literal["move_vertex"] = "move_vertex"
    vertex_id: UUID
    to: Point2


class SetWallLength(StrictModel):
    op: Literal["set_wall_length"] = "set_wall_length"
    wall_id: UUID
    length: float = Field(gt=0.0)
    anchor: Anchor = "start"


class AddWall(StrictModel):
    """Un point existant (UUID) ou un nouveau point (Point2, réutilisé s'il coïncide)."""

    op: Literal["add_wall"] = "add_wall"
    start: UUID | Point2
    end: UUID | Point2
    thickness: Meters = 0.15
    source: Source = Source.USER_DEFINED
    confidence: Confidence = 1.0


class AddRoofWindow(StrictModel):
    """Pose un velux dans une pente (D1) ; refusé s'il dépasse de la pente."""

    op: Literal["add_roof_window"] = "add_roof_window"
    roof: UUID
    offset: Meters
    distance: Meters
    width: float = Field(default=0.78, gt=0.0, le=3.0)
    length: float = Field(default=0.98, gt=0.0, le=3.0)
    name: str | None = None
    source: Source = Source.USER_DEFINED
    confidence: Confidence = 1.0


class AddStairs(StrictModel):
    """Pose une cage d'escalier (S1) : trémie dans ce plancher, escalier qui descend au niveau
    d'en dessous. Sans `rise`, la hauteur d'étage vient du niveau d'en dessous (2,70 m sinon)."""

    op: Literal["add_stairs"] = "add_stairs"
    kind: StairKind = "straight"
    position: Point2
    yaw: float = 0.0
    width: float = Field(default=0.90, ge=0.70, le=1.20)
    turn: StairTurn = "left"
    railing: bool = True
    rise: float | None = Field(default=None, gt=0.5, le=6.0)
    name: str | None = None
    source: Source = Source.USER_DEFINED
    confidence: Confidence = 1.0


DEFAULT_RISE_M = 2.70
"""Hauteur d'étage supposée quand le niveau d'en dessous n'existe pas dans le projet."""


def stair_rise(project: Project, level_id: UUID) -> float:
    """Hauteur à franchir pour arriver à ce niveau : écart avec le niveau d'en dessous le plus
    proche, s'il est plausible (1,5 à 6 m) ; sinon 2,70 m. Les variantes (T7e) ne sont pas des
    niveaux d'en dessous."""
    host = project.levels[level_id]
    below = [
        lv.elevation
        for lv in project.levels.values()
        if lv.variant_of is None and lv.elevation < host.elevation - 0.5
    ]
    if below:
        gap = host.elevation - max(below)
        if 1.5 <= gap <= 6.0:
            return round(gap, 3)
    return DEFAULT_RISE_M


class AddOpening(StrictModel):
    op: Literal["add_opening"] = "add_opening"
    host_wall: UUID
    kind: OpeningKind
    offset: Meters
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    sill_height: Meters = 0.0
    swing: Swing | None = None
    leaves: int = Field(default=1, ge=1, le=2)
    split: float = Field(default=0.5, ge=0.25, le=0.75)
    source: Source = Source.USER_DEFINED
    confidence: Confidence = 1.0


class AddFurniture(StrictModel):
    """Pose un meuble du catalogue (`assets/catalog.yaml`) au sol, centré en `position`.
    Les dimensions absentes sont celles du catalogue ; une dimension donnée doit être dans la
    plage de l'asset."""

    op: Literal["add_furniture"] = "add_furniture"
    asset_id: str
    position: Point2
    yaw: float = 0.0
    width: float | None = Field(default=None, gt=0.0)
    depth: float | None = Field(default=None, gt=0.0)
    height: float | None = Field(default=None, gt=0.0)
    name: str | None = None
    source: Source = Source.USER_DEFINED
    confidence: Confidence = 1.0


class ReplaceFurniture(StrictModel):
    """Remplace un meuble par un autre modèle du catalogue (P4, « Remplacer » sur un rendu) :
    même place, même lacet, même identifiant ; dimensions et nom du nouveau modèle."""

    op: Literal["replace_furniture"] = "replace_furniture"
    element_id: UUID
    asset_id: str


class AddCamera(StrictModel):
    """Point de vue libre pour l'écran Rendre : posé dans le plan, visée `yaw`, à hauteur d'œil."""

    op: Literal["add_camera"] = "add_camera"
    position: Point2
    yaw: float = 0.0
    height: Meters = 1.5
    name: str | None = None
    pitch: float = Field(default=0.0, ge=-1.2, le=1.2)
    """Tangage (radians, positif vers le haut) : la vue d'architecte regarde un peu la toiture."""
    focal_px: float | None = Field(default=None, gt=0.0)
    """Focale pour 1280 px de large ; None = focale par défaut du rendu."""


class UpdateElement(StrictModel):
    """Modifie des champs d'un élément ; `id` et `type` sont interdits."""

    op: Literal["update_element"] = "update_element"
    element_id: UUID
    fields: dict[str, JsonValue]


class SetIntent(StrictModel):
    """Modifie l'intention de design d'une pièce (V1). Un utilisateur marque `user_defined`
    les champs qu'il touche ; une étape automatique ne réécrit jamais ces champs-là."""

    op: Literal["set_intent"] = "set_intent"
    room_id: UUID
    fields: dict[str, JsonValue]


class UpdateVertex(StrictModel):
    op: Literal["update_vertex"] = "update_vertex"
    vertex_id: UUID
    fields: dict[str, JsonValue]


class RemoveElement(StrictModel):
    """Supprimer un mur entraîne ses ouvertures, radiateurs et les pièces qui l'utilisent."""

    op: Literal["remove_element"] = "remove_element"
    element_id: UUID


class RemoveVertex(StrictModel):
    """Supprimer un sommet entraîne les murs qui s'y attachent."""

    op: Literal["remove_vertex"] = "remove_vertex"
    vertex_id: UUID


class DetectRooms(StrictModel):
    """Crée une pièce (avec sol et plafond dérivés) pour chaque boucle fermée sans pièce."""

    op: Literal["detect_rooms"] = "detect_rooms"


class UpdateLevel(StrictModel):
    """Modifie nom, hauteur, source de hauteur, altitude ou fond de photo du niveau."""

    op: Literal["update_level"] = "update_level"
    fields: dict[str, JsonValue]


Op = Annotated[
    MoveVertex
    | SetWallLength
    | AddWall
    | AddOpening
    | AddRoofWindow
    | AddStairs
    | AddFurniture
    | ReplaceFurniture
    | AddCamera
    | UpdateElement
    | SetIntent
    | UpdateVertex
    | RemoveElement
    | RemoveVertex
    | DetectRooms
    | UpdateLevel,
    Field(discriminator="op"),
]

OP_ADAPTER: TypeAdapter[Op] = TypeAdapter(Op)


def apply_op(project: Project, level_id: UUID, op: Op, *, actor: str = USER_ACTOR) -> Project:
    """Applique une commande et renvoie le projet historisé (sans vérifier les invariants)."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    if isinstance(op, AddStairs) and op.rise is None:
        op = op.model_copy(update={"rise": stair_rise(project, level_id)})
    new_level, label = _dispatch(level, op, actor)
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    return history.commit(project, after, actor=actor, label=label)


def apply_ops(
    project: Project,
    level_id: UUID,
    ops: list[Op],
    *,
    actor: str,
    label: str,
    finish: Callable[[Level], Level] | None = None,
) -> Project:
    """Plusieurs commandes en un seul pas d'historique (un plan de départ entier, L1b) ;
    `finish` retouche le niveau obtenu avant l'entrée d'historique."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    for op in ops:
        level, _ = _dispatch(level, op, actor)
    if finish is not None:
        level = finish(level)
    after = project.model_copy(update={"levels": {**project.levels, level_id: level}})
    return history.commit(project, after, actor=actor, label=label)


def apply_to_level(level: Level, ops: list[Op], *, actor: str) -> Level:
    """Les commandes appliquées au niveau seul, sans entrée d'historique : pour un calcul en
    plusieurs temps (plan généré, L3) qui finit par un seul pas d'historique."""
    for op in ops:
        level, _ = _dispatch(level, op, actor)
    return level


def _dispatch(level: Level, op: Op, actor: str) -> tuple[Level, str]:  # noqa: PLR0911, PLR0912
    match op:
        case MoveVertex():
            graph = WallGraph.from_level(level)
            graph.vertex(op.vertex_id)
            moved = graph.move_vertex(op.vertex_id, op.to)
            return _mark_moved(moved, [op.vertex_id], actor), "Déplacer un sommet"
        case SetWallLength():
            graph = WallGraph.from_level(level)
            wall = graph.wall(op.wall_id)
            moved = graph.set_wall_length(op.wall_id, op.length, op.anchor)
            touched = {
                "start": [wall.end_vertex],
                "end": [wall.start_vertex],
                "center": [wall.start_vertex, wall.end_vertex],
            }[op.anchor]
            return _mark_moved(moved, touched, actor), "Régler la longueur d'un mur"
        case AddWall():
            return _add_wall(level, op), "Ajouter un mur"
        case AddOpening():
            return _add_opening(level, op), "Ajouter une ouverture"
        case AddRoofWindow():
            return _add_roof_window(level, op), "Ajouter un velux"
        case AddStairs():
            return _add_stairs(level, op), "Ajouter un escalier"
        case AddFurniture():
            return _add_furniture(level, op), "Ajouter un meuble"
        case ReplaceFurniture():
            return _replace_furniture(level, op), "Remplacer un meuble"
        case AddCamera():
            return _add_camera(level, op), "Ajouter un point de vue"
        case UpdateElement():
            return _update_element(level, op, actor), "Modifier un élément"
        case SetIntent():
            return _set_intent(level, op, actor), "Modifier l'ambiance"
        case UpdateVertex():
            return _update_vertex(level, op), "Modifier un sommet"
        case RemoveElement():
            return _remove_element(level, op.element_id), "Supprimer un élément"
        case RemoveVertex():
            return _remove_vertex(level, op.vertex_id), "Supprimer un sommet"
        case DetectRooms():
            return detect_rooms(level), "Détecter les pièces"
        case UpdateLevel():
            return _update_level(level, op), "Modifier le niveau"


# ---------------------------------------------------------------------- helpers
def _set_intent(level: Level, op: SetIntent, actor: str) -> Level:
    room = level.elements.get(op.room_id)
    if not isinstance(room, Room):
        msg = f"pièce inconnue : {op.room_id}"
        raise OpError(msg)
    current = room.intent or DesignIntent(room_type=room.room_type)
    try:
        merged, _ = merge_intent(current, dict(op.fields), by_user=is_user(actor))
    except (ValueError, ValidationError) as exc:
        raise OpError(str(exc)) from exc
    updated = room.model_copy(update={"intent": merged})
    return level.model_copy(update={"elements": {**level.elements, room.id: updated}})


def _mark_moved(level: Level, vertex_ids: list[UUID], actor: str) -> Level:
    if not is_user(actor):
        return level
    vertices = dict(level.vertices)
    for vid in vertex_ids:
        vertices[vid] = vertices[vid].model_copy(
            update={
                "source": Source.USER_DEFINED,
                "confidence": 1.0,
                "uncertainty": Uncertainty(),
            }
        )
    elements = dict(level.elements)
    for eid, element in level.elements.items():
        if (
            isinstance(element, Wall)
            and element.source is Source.MEASURED
            and (element.start_vertex in vertex_ids or element.end_vertex in vertex_ids)
        ):
            elements[eid] = element.model_copy(update={"source": Source.USER_DEFINED})
    return level.model_copy(update={"vertices": vertices, "elements": elements})


def _resolve_vertex(
    level: Level, ref: UUID | Point2, new_vertices: dict[UUID, Vertex], op: AddWall
) -> UUID:
    if isinstance(ref, UUID):
        if ref in level.vertices or ref in new_vertices:
            return ref
        msg = f"sommet inconnu : {ref}"
        raise OpError(msg)
    for vid, vertex in [*level.vertices.items(), *new_vertices.items()]:
        if math.hypot(vertex.position.x - ref.x, vertex.position.y - ref.y) <= _SNAP:
            return vid
    vertex = Vertex(position=ref, source=op.source, confidence=op.confidence)
    new_vertices[vertex.id] = vertex
    return vertex.id


def _add_wall(level: Level, op: AddWall) -> Level:
    new_vertices: dict[UUID, Vertex] = {}
    start = _resolve_vertex(level, op.start, new_vertices, op)
    end = _resolve_vertex(level, op.end, new_vertices, op)
    if start == end:
        msg = "un mur doit relier deux sommets distincts"
        raise OpError(msg)
    wall = Wall(
        start_vertex=start,
        end_vertex=end,
        thickness=op.thickness,
        source=op.source,
        confidence=op.confidence,
    )
    return level.model_copy(
        update={
            "vertices": {**level.vertices, **new_vertices},
            "elements": {**level.elements, wall.id: wall},
        }
    )


def _add_roof_window(level: Level, op: AddRoofWindow) -> Level:
    roof = level.elements.get(op.roof)
    if not isinstance(roof, RoofPlane):
        msg = f"pente inconnue : {op.roof}"
        raise OpError(msg)
    eave = math.hypot(roof.eave_end.x - roof.eave_start.x, roof.eave_end.y - roof.eave_start.y)
    slope = slope_length(roof.eave_height, roof.pitch, level.height)
    if slope <= 0.0:
        msg = "cette pente n'atteint pas la hauteur du niveau : pas de place pour un velux"
        raise OpError(msg)
    if op.offset + op.width > eave + 1e-6 or op.distance + op.length > slope + 1e-6:
        msg = (
            f"le velux ({op.width:.2f} × {op.length:.2f} m) dépasse de la pente "
            f"({eave:.2f} m le long de l'égout, {slope:.2f} m le long de la pente)"
        )
        raise OpError(msg)
    count = sum(1 for e in level.elements.values() if isinstance(e, RoofWindow))
    window = RoofWindow(
        roof=op.roof,
        offset=op.offset,
        distance=op.distance,
        width=op.width,
        length=op.length,
        name=op.name or f"Velux {count + 1}",
        source=op.source,
        confidence=op.confidence,
    )
    return level.model_copy(update={"elements": {**level.elements, window.id: window}})


def _add_stairs(level: Level, op: AddStairs) -> Level:
    count = sum(1 for e in level.elements.values() if isinstance(e, Stairs))
    stairs = Stairs(
        kind=op.kind,
        position=op.position,
        yaw=op.yaw,
        width=op.width,
        rise=op.rise if op.rise is not None else DEFAULT_RISE_M,
        turn=op.turn,
        railing=op.railing,
        name=op.name or ("Escalier" if count == 0 else f"Escalier {count + 1}"),
        source=op.source,
        confidence=op.confidence,
    )
    return level.model_copy(update={"elements": {**level.elements, stairs.id: stairs}})


def _add_opening(level: Level, op: AddOpening) -> Level:
    if not isinstance(level.elements.get(op.host_wall), Wall):
        msg = f"mur hôte inconnu : {op.host_wall}"
        raise OpError(msg)
    try:
        opening = Opening(
            kind=op.kind,
            host_wall=op.host_wall,
            offset=op.offset,
            width=op.width,
            height=op.height,
            sill_height=op.sill_height,
            swing=op.swing,
            leaves=op.leaves,
            split=op.split,
            source=op.source,
            confidence=op.confidence,
        )
    except ValidationError as exc:
        raise OpError(str(exc)) from exc
    return level.model_copy(update={"elements": {**level.elements, opening.id: opening}})


def _add_furniture(level: Level, op: AddFurniture) -> Level:
    try:
        furniture = furniture_from_asset(
            asset(op.asset_id),
            op.position,
            op.yaw,
            width=op.width,
            depth=op.depth,
            height=op.height,
            name=op.name,
            source=op.source,
            confidence=op.confidence,
        )
    except CatalogError as exc:
        raise OpError(str(exc)) from exc
    _check_built_in_bed(level, furniture)
    return level.model_copy(update={"elements": {**level.elements, furniture.id: furniture}})


def _replace_furniture(level: Level, op: ReplaceFurniture) -> Level:
    old = level.elements.get(op.element_id)
    if not isinstance(old, Furniture):
        msg = f"meuble inconnu : {op.element_id}"
        raise OpError(msg)
    t = old.transform
    try:
        chosen = asset(op.asset_id)
    except CatalogError as exc:
        raise OpError(str(exc)) from exc
    # Le dos du meuble reste où il était (contre le mur s'il y était) : le centre avance ou
    # recule de la moitié de l'écart de profondeur, le long de sa face avant.
    old_depth = old.bbox.depth * t.scale
    shift = (chosen.depth.default - old_depth) / 2
    front = (-math.sin(t.yaw), math.cos(t.yaw))
    at = Point2(x=t.position.x + front[0] * shift, y=t.position.y + front[1] * shift)
    try:
        new = furniture_from_asset(chosen, at, t.yaw, source=Source.USER_DEFINED, confidence=1.0)
    except CatalogError as exc:
        raise OpError(str(exc)) from exc
    position = new.transform.position.model_copy(update={"z": t.position.z})
    new = new.model_copy(
        update={
            "id": old.id,
            "transform": new.transform.model_copy(update={"position": position}),
        }
    )
    _check_built_in_bed(level, new)
    return level.model_copy(update={"elements": {**level.elements, old.id: new}})


def _check_built_in_bed(level: Level, furniture: Furniture) -> None:
    """Lit encastré sous pente (H1) : refusé s'il ne laisse pas 0,90 m au-dessus du matelas,
    à l'oreiller."""
    from openplan.geom.built_in_bed import BUILT_IN_BED, MIN_HEADROOM_M, headroom_at
    from openplan.geom.roof import derived_roofs

    if furniture.category != BUILT_IN_BED:
        return
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    if not roofs:
        return
    t = furniture.transform
    free = headroom_at(roofs, level.height, t.position.x, t.position.y, t.yaw, furniture.bbox.depth)
    if free + 1e-6 < MIN_HEADROOM_M:
        msg = (
            f"lit encastré refusé : {free:.2f} m libres au-dessus du matelas à l'oreiller, il en "
            f"faut {MIN_HEADROOM_M:.2f} m (éloignez-le de la jambette ou tournez la tête vers "
            "le faîtage)"
        )
        raise OpError(msg)


def _add_camera(level: Level, op: AddCamera) -> Level:
    count = sum(1 for e in level.elements.values() if isinstance(e, Camera) and e.image_id is None)
    camera = Camera(
        name=op.name or f"Point de vue {count + 1}",
        position=Point3(x=op.position.x, y=op.position.y, z=op.height),
        yaw=op.yaw,
        pitch=op.pitch,
        focal_px=op.focal_px,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    return level.model_copy(update={"elements": {**level.elements, camera.id: camera}})


_CAMERA_POSE = frozenset({"position", "yaw", "pitch", "roll", "focal_px"})


def _update_element(level: Level, op: UpdateElement, actor: str = USER_ACTOR) -> Level:
    element = level.elements.get(op.element_id)
    if element is None:
        msg = f"élément inconnu : {op.element_id}"
        raise OpError(msg)
    if forbidden := {"id", "type"} & op.fields.keys():
        msg = f"champs non modifiables : {sorted(forbidden)}"
        raise OpError(msg)
    data = element.model_dump(mode="json")
    data.update(op.fields)
    try:
        updated = _ELEMENT_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise OpError(str(exc)) from exc
    if isinstance(updated, Furniture) and "bbox" in op.fields:
        _check_catalog_size(updated)
    if (
        isinstance(updated, Camera)
        and "auto_view" in updated.metadata
        and is_user(actor)
        and _CAMERA_POSE & op.fields.keys()
    ):
        # Vue automatique déplacée ou visée à la main (V1) : elle devient celle de
        # l'utilisateur, et « Vues automatiques » ne la remplacera plus.
        updated = updated.model_copy(update={"source": Source.USER_DEFINED, "confidence": 1.0})
    return level.model_copy(update={"elements": {**level.elements, op.element_id: updated}})


def _check_catalog_size(furniture: Furniture) -> None:
    """Redimensionner un meuble (E2) reste dans la plage du catalogue (largeur, profondeur)."""
    try:
        item = asset(furniture.asset_id)
    except CatalogError:
        return
    scale = furniture.transform.scale
    for label, value, rng in (
        ("largeur", furniture.bbox.width * scale, item.width),
        ("profondeur", furniture.bbox.depth * scale, item.depth),
    ):
        if not rng.min - 1e-6 <= value <= rng.max + 1e-6:
            msg = (
                f"{label} de {value:.2f} m hors de la plage du catalogue pour « {item.label} » "
                f"({rng.min:.2f} à {rng.max:.2f} m)"
            )
            raise OpError(msg)


def _update_vertex(level: Level, op: UpdateVertex) -> Level:
    vertex = level.vertices.get(op.vertex_id)
    if vertex is None:
        msg = f"sommet inconnu : {op.vertex_id}"
        raise OpError(msg)
    if "id" in op.fields:
        msg = "champs non modifiables : ['id']"
        raise OpError(msg)
    data = vertex.model_dump(mode="json")
    data.update(op.fields)
    try:
        updated = Vertex.model_validate(data)
    except ValidationError as exc:
        raise OpError(str(exc)) from exc
    return level.model_copy(update={"vertices": {**level.vertices, op.vertex_id: updated}})


def _remove_element(level: Level, element_id: UUID) -> Level:
    element = level.elements.get(element_id)
    if element is None:
        msg = f"élément inconnu : {element_id}"
        raise OpError(msg)
    doomed = {element_id}
    if isinstance(element, Wall):
        for eid, e in level.elements.items():
            if (isinstance(e, Opening | Radiator) and e.host_wall == element_id) or (
                isinstance(e, Room) and element_id in e.boundary_walls
            ):
                doomed.add(eid)
    if isinstance(element, RoofPlane):
        doomed |= {
            eid
            for eid, e in level.elements.items()
            if isinstance(e, RoofWindow) and e.roof == element_id
        }
    rooms_gone = {eid for eid in doomed if isinstance(level.elements[eid], Room)}
    for eid, e in level.elements.items():
        if isinstance(e, Floor | Ceiling) and e.room in rooms_gone:
            doomed.add(eid)
    elements = {eid: e for eid, e in level.elements.items() if eid not in doomed}
    return level.model_copy(update={"elements": elements})


def _remove_vertex(level: Level, vertex_id: UUID) -> Level:
    if vertex_id not in level.vertices:
        msg = f"sommet inconnu : {vertex_id}"
        raise OpError(msg)
    for wid in WallGraph.from_level(level).walls_at(vertex_id):
        level = _remove_element(level, wid)
    vertices = {vid: v for vid, v in level.vertices.items() if vid != vertex_id}
    return level.model_copy(update={"vertices": vertices})


def detect_rooms(level: Level) -> Level:
    """Une pièce (sol et plafond dérivés, `inferred`) par boucle fermée sans pièce."""
    graph = WallGraph.from_level(level)
    existing = {frozenset(e.boundary_walls) for e in level.elements.values() if isinstance(e, Room)}
    count = len(existing)
    new_elements: dict[UUID, Room | Floor | Ceiling] = {}
    for loop in graph.closed_loops():
        if frozenset(loop) in existing:
            continue
        count += 1
        room = Room(
            name=f"Pièce {count}",
            boundary_walls=loop,
            source=Source.INFERRED,
            confidence=0.9,
        )
        floor = Floor(room=room.id, source=Source.INFERRED, confidence=0.9)
        ceiling = Ceiling(room=room.id, source=Source.INFERRED, confidence=0.9)
        new_elements.update({room.id: room, floor.id: floor, ceiling.id: ceiling})
    if not new_elements:
        return level
    return level.model_copy(update={"elements": {**level.elements, **new_elements}})


def _update_level(level: Level, op: UpdateLevel) -> Level:
    if forbidden := {"id", "vertices", "elements"} & op.fields.keys():
        msg = f"champs non modifiables : {sorted(forbidden)}"
        raise OpError(msg)
    data = level.model_dump(mode="json")
    data.update(op.fields)
    try:
        return Level.model_validate(data)
    except ValidationError as exc:
        raise OpError(str(exc)) from exc
