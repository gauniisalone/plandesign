"""Fabriques de projets de test, réutilisées par les tests unitaires et géométriques."""

from __future__ import annotations

from openplan.schemas import (
    Floor,
    Level,
    Measurement,
    MeasurementKind,
    Opening,
    OpeningKind,
    Point2,
    Project,
    Room,
    Source,
    Swing,
    Vertex,
    Wall,
)
from openplan.schemas.common import ElementBase


def rect_room_project(
    width: float = 4.0,
    depth: float = 3.0,
    *,
    height: float = 2.5,
    thickness: float = 0.15,
    name: str = "Chambre test",
) -> Project:
    """Pièce rectangulaire : 4 sommets, 4 murs (W1..W4), 1 pièce, 1 sol, 1 porte, 1 fenêtre.

    W1 (sol y=0) est mesuré ; les autres murs sont estimés. Une mesure de longueur cible W1.
    """
    corners = [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]
    vertices = [
        Vertex(position=Point2(x=x, y=y), source=Source.USER_DEFINED, confidence=1.0)
        for x, y in corners
    ]
    walls: list[Wall] = []
    for i in range(4):
        start, end = vertices[i], vertices[(i + 1) % 4]
        measured = i == 0
        walls.append(
            Wall(
                name=f"W{i + 1}",
                start_vertex=start.id,
                end_vertex=end.id,
                thickness=thickness,
                source=Source.MEASURED if measured else Source.ESTIMATED,
                confidence=1.0 if measured else 0.6,
            )
        )
    room = Room(
        name="R1",
        boundary_walls=[w.id for w in walls],
        room_type="bedroom",
        source=Source.INFERRED,
        confidence=0.6,
    )
    floor = Floor(name="floor", room=room.id, source=Source.INFERRED, confidence=0.6)
    door = Opening(
        name="door",
        kind=OpeningKind.DOOR,
        host_wall=walls[0].id,
        offset=0.5,
        width=0.83,
        height=2.04,
        swing=Swing(hinge="left", direction="inward"),
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    window = Opening(
        name="window",
        kind=OpeningKind.WINDOW,
        host_wall=walls[2].id,
        offset=1.2,
        width=1.2,
        height=1.25,
        sill_height=0.9,
        source=Source.OBSERVED,
        confidence=0.8,
    )
    elements: list[ElementBase] = [*walls, room, floor, door, window]
    level = Level(
        name="RDC",
        height=height,
        vertices={v.id: v for v in vertices},
        elements={e.id: e for e in elements},
    )
    measurement = Measurement(kind=MeasurementKind.WALL_LENGTH, value=width, element_id=walls[0].id)
    return Project(
        name=name,
        levels={level.id: level},
        measurements={measurement.id: measurement},
    )


L_ROOM = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (2.0, 6.0), (2.0, 3.0), (0.0, 3.0)]
"""Pièce en L (axes des murs, m) : [0,4]×[0,3] ∪ [2,6]×[0,6], comme `tests/unit/test_depthplan`."""


def l_room_project(*, height: float = 2.5, thickness: float = 0.15) -> Project:
    """Pièce en L à six murs estimés centrés sur l'axe (faces intérieures en retrait d'une
    demi-épaisseur, comme la fixture rectangulaire), pièce, sol et plafond dérivés."""
    from openplan.scene.ops import detect_rooms

    vertices = [
        Vertex(position=Point2(x=x, y=y), source=Source.ESTIMATED, confidence=0.6)
        for x, y in L_ROOM
    ]
    walls = [
        Wall(
            name=f"L{i + 1}",
            start_vertex=vertices[i].id,
            end_vertex=vertices[(i + 1) % len(vertices)].id,
            thickness=thickness,
            source=Source.ESTIMATED,
            confidence=0.6,
        )
        for i in range(len(vertices))
    ]
    level = Level(
        name="RDC",
        height=height,
        vertices={v.id: v for v in vertices},
        elements={w.id: w for w in walls},
    )
    level = detect_rooms(level)
    return Project(name="Pièce en L", levels={level.id: level})


ATTIC = {"length": 6.0, "width": 5.0, "ridge_height": 3.2, "eave_height": 1.0}


def attic_project(*, user_defined: bool = False, camera: bool = True) -> Project:
    """Combles 6 × 5 m, faîtage 3,2 m, murs latéraux 1,0 m, créés par l'étape attic.apply
    sur un projet neuf (mêmes cotes que `tests.synthetic_depth.attic_faces`)."""
    from openplan.pipeline.attic import Estimate
    from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
    from openplan.scene.factory import new_project

    source = Source.USER_DEFINED if user_defined else Source.ESTIMATED
    sigma = 0.0 if user_defined else 0.3

    def est(value: float) -> Estimate:
        return Estimate(value=value, sigma=sigma, source=source)

    project = new_project("Combles test")
    level = only_level(project)
    geometry = AtticGeometry(
        length=est(ATTIC["length"]),
        width=est(ATTIC["width"]),
        ridge_height=est(ATTIC["ridge_height"]),
        eave_height=est(ATTIC["eave_height"]),
        camera_height=est(1.45) if camera else None,
        gable_distance=est(4.5) if camera else None,
    )
    after, _ = apply_attic(project, level.id, geometry)
    return after


def only_level(project: Project) -> Level:
    (level,) = project.levels.values()
    return level


def by_name[T: ElementBase](level: Level, name: str, kind: type[T]) -> T:
    """Retrouve un élément par son nom et son type ; lève KeyError sinon."""
    for element in level.elements.values():
        if element.name == name and isinstance(element, kind):
            return element
    msg = f"aucun {kind.__name__} nommé {name!r}"
    raise KeyError(msg)
