"""Règles d'aménagement 3.2 : collisions, murs, débattement de porte, fenêtre, radiateur,
dégagements du catalogue, passage de 0,80 m. Cas vrai / faux pour chaque règle."""

from __future__ import annotations

import math
from uuid import UUID

import pytest

from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas import (
    Constraint,
    ConstraintKind,
    Fixture,
    Opening,
    Point3,
    Project,
    Radiator,
    Source,
    Swing,
    Transform3,
)
from openplan.schemas.common import BBox3, Point2
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import PASSAGE_WIDTH_M, check_layout
from tests.factories import by_name, only_level, rect_room_project

# Chambre de la fabrique : 4 × 3 m, murs de 0,15 m centrés sur (0,0)-(4,0)-(4,3)-(0,3), donc
# intérieur x ∈ [0,075 ; 3,925], y ∈ [0,075 ; 2,925]. Porte sur W1 (y = 0) de x 0,5 à 1,33,
# gond à gauche (x = 0,5), s'ouvre vers l'intérieur (+y). Fenêtre sur W3 (y = 3) de x 2,8 à
# 1,6, allège 0,9.


def _add(
    project: Project, asset: str, x: float, y: float, yaw: float = 0.0, **kw: object
) -> Project:
    level_id = only_level(project).id
    return apply_op(
        project,
        level_id,
        AddFurniture(asset_id=asset, position=Point2(x=x, y=y), yaw=yaw, **kw),  # type: ignore[arg-type]
    )


def _types(project: Project) -> list[str]:
    return sorted(v.type for v in check_layout(project))


def test_clean_layout_has_no_violation() -> None:
    project = rect_room_project()
    # Lit tourné de 90° : sa profondeur (2,0 m) suit x, tête contre W2 (face intérieure 3,925).
    project = _add(project, "bed_double_160", 2.925, 1.5, math.pi / 2)
    # Bureau dos au mur haut, face vers la pièce, loin de la fenêtre (x 1,6 à 2,8).
    project = _add(project, "desk_140", 0.85, 2.55, math.pi)
    assert check_layout(project) == []
    assert validate_invariants(project).valid


def test_overlap_and_wall_penetration_are_hard() -> None:
    project = rect_room_project()
    project = _add(project, "desk_140", 2.0, 1.5)
    project = _add(project, "chest_of_drawers", 2.3, 1.6)  # sur le bureau
    violations = check_layout(project)
    overlap = [v for v in violations if v.type == "furniture_overlap"]
    assert len(overlap) == 1 and overlap[0].severity == "high"
    assert "chevauche" in overlap[0].message and "cm²" in overlap[0].message
    assert not validate_invariants(project).valid
    in_wall = _add(rect_room_project(), "bookshelf_80", 3.95, 1.5)  # dans W2
    (v,) = [v for v in check_layout(in_wall) if v.type == "furniture_in_wall"]
    assert v.severity == "high" and "entre dans W2" in v.message


def test_soft_constraint_downgrades_and_off_disables() -> None:
    project = rect_room_project()
    project = _add(project, "desk_140", 2.0, 1.5)
    project = _add(project, "chest_of_drawers", 2.3, 1.6)
    ids = next(v.element_ids for v in check_layout(project) if v.type == "furniture_overlap")
    soft = Constraint(kind=ConstraintKind.NO_OVERLAP, element_ids=[ids[0]], hard=False)
    softened = project.model_copy(update={"constraints": {soft.id: soft}})
    (v,) = [v for v in check_layout(softened) if v.type == "furniture_overlap"]
    assert v.severity == "medium" and validate_invariants(softened).valid
    off = soft.model_copy(update={"enabled": False})
    assert "furniture_overlap" not in _types(
        project.model_copy(update={"constraints": {off.id: off}})
    )


def test_door_swing_is_hard_and_depends_on_the_swing() -> None:
    project = rect_room_project()
    blocked = _add(project, "chest_of_drawers", 0.9, 0.5)  # dans le quart de disque de la porte
    (v,) = [v for v in check_layout(blocked) if v.type == "door_swing_blocked"]
    assert v.severity == "high" and "ne s'ouvre plus" in v.message and "W1" in v.message
    # La même commode contre le mur de droite de la porte, hors du débattement : rien.
    beside = _add(project, "chest_of_drawers", 2.2, 0.35)
    assert "door_swing_blocked" not in _types(beside)
    # Porte qui s'ouvre vers l'extérieur : le débattement est dehors, la commode passe.
    level = only_level(project)
    door = by_name(level, "door", Opening)
    outward = door.model_copy(update={"swing": Swing(hinge="left", direction="outward")})
    project_out = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(
                    update={"elements": {**level.elements, door.id: outward}}
                )
            }
        }
    )
    assert "door_swing_blocked" not in _types(_add(project_out, "chest_of_drawers", 0.9, 0.5))
    # Sans sens connu : rectangle de part et d'autre, la commode est signalée.
    unknown = door.model_copy(update={"swing": None})
    project_unknown = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(
                    update={"elements": {**level.elements, door.id: unknown}}
                )
            }
        }
    )
    assert "door_swing_blocked" in _types(_add(project_unknown, "chest_of_drawers", 0.9, 0.5))


def test_window_blocked_only_by_something_taller_than_the_sill() -> None:
    project = rect_room_project()
    tall = _add(project, "wardrobe_150", 2.2, 2.6, math.pi)  # 2,00 m devant la fenêtre
    (v,) = [v for v in check_layout(tall) if v.type == "window_blocked"]
    assert v.severity == "medium" and "masque la fenêtre" in v.message and "0.90" in v.message
    assert validate_invariants(tall).valid
    low = _add(project, "low_storage_eave", 2.2, 2.6, math.pi)  # 0,70 m < allège 0,90
    assert "window_blocked" not in _types(low)


def _with_radiator(project: Project) -> tuple[Project, UUID]:
    level = only_level(project)
    wall = by_name(
        level,
        "W3",
        type(by_name(level, "W1", __import__("openplan.schemas", fromlist=["Wall"]).Wall)),
    )
    radiator = Radiator(
        name="radiateur",
        host_wall=wall.id,
        offset=0.3,
        width=0.8,
        height=0.6,
        depth=0.1,
        bottom_height=0.15,
        clearance=0.3,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    return (
        project.model_copy(
            update={
                "levels": {
                    level.id: level.model_copy(
                        update={"elements": {**level.elements, radiator.id: radiator}}
                    )
                }
            }
        ),
        radiator.id,
    )


def test_radiator_and_fixture_clearances() -> None:
    project, radiator_id = _with_radiator(rect_room_project())
    # W3 va de (4,3) à (0,3) : offset 0,3 → x de 3,7 à 2,9 ; bande de 0,4 m sous y = 2,925.
    # Commode dos au mur haut (face vers la pièce), dans la bande du radiateur.
    close = _add(project, "chest_of_drawers", 3.3, 2.6, math.pi)
    (v,) = [v for v in check_layout(close) if v.type == "clearance_blocked"]
    assert v.severity == "medium" and "radiateur" in v.message and radiator_id in v.element_ids
    far = _add(project, "chest_of_drawers", 3.3, 2.0, math.pi)
    assert "clearance_blocked" not in _types(far)
    # Élément fixe avec keep_clear : un meuble dans sa zone est signalé.
    level = only_level(project)
    sink = Fixture(
        name="évier",
        category="sink",
        transform=Transform3(position=Point3(x=1.0, y=1.5, z=0.0)),
        bbox=BBox3(width=0.6, depth=0.5, height=0.9),
        keep_clear=0.6,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    with_sink = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(update={"elements": {**level.elements, sink.id: sink}})
            }
        }
    )
    near_sink = _add(with_sink, "office_chair", 1.0, 2.2)
    assert any(
        v.type == "clearance_blocked" and sink.id in v.element_ids for v in check_layout(near_sink)
    )


def test_catalog_clearance_of_the_furniture_itself() -> None:
    """Une armoire face au mur ne s'ouvre pas ; une chaise devant un bureau est dans son recul."""
    project = rect_room_project()
    facing_wall = _add(project, "wardrobe_150", 2.0, 0.4)  # face avant (+y local)… vers +y ? non :
    # yaw 0 : face avant vers +y, dos vers le mur bas (y = 0) : dégagement libre → rien.
    assert "clearance_blocked" not in _types(facing_wall)
    turned = _add(project, "wardrobe_150", 2.0, 0.4, math.pi)  # face vers le mur bas
    (v,) = [v for v in check_layout(turned) if v.type == "clearance_blocked"]
    assert "bute sur un mur" in v.message
    desk = _add(project, "desk_140", 2.0, 1.5)  # recul de chaise 0,80 m vers +y
    armchair_in_front = _add(desk, "armchair", 2.0, 2.4)
    assert any(
        v.type == "clearance_blocked" and "que demande « Bureau" in v.message
        for v in check_layout(armchair_in_front)
    )
    # Sa chaise, elle, est à sa place devant le bureau (meubles compagnons, V1).
    chair_in_front = _add(desk, "office_chair", 2.0, 2.2)
    assert "clearance_blocked" not in _types(chair_in_front)
    chair_aside = _add(desk, "office_chair", 3.4, 1.5)
    assert "clearance_blocked" not in _types(chair_aside)


def test_passage_from_the_door() -> None:
    """Un bureau au fond, atteignable ; le même bureau muré derrière une rangée d'armoires qui
    barre la pièce : injoignable avec 0,80 m de passage."""
    project = rect_room_project()
    reachable = _add(project, "desk_140", 3.0, 2.5, math.pi)
    assert "furniture_unreachable" not in _types(reachable)
    # Barrière : trois armoires de 1,50 m bout à bout à y = 1,5 sur x ∈ [0,075 ; 3,925]
    # laissent moins de 0,80 m ; le bureau derrière n'est plus atteignable.
    barred = reachable
    for x in (0.7, 2.0, 3.3):  # trois armoires de 1,25 m, 5 cm entre elles
        barred = _add(barred, "wardrobe_150", x, 1.5, width=1.25)
    violations = check_layout(barred)
    unreachable = [v for v in violations if v.type == "furniture_unreachable"]
    assert [v.element_ids[0] for v in unreachable] and all(
        v.severity == "medium" for v in unreachable
    )
    desk_id = next(
        e.id
        for e in only_level(barred).elements.values()
        if getattr(e, "asset_id", None) == "desk_140"
    )
    assert desk_id in [v.element_ids[0] for v in unreachable]
    assert f"{PASSAGE_WIDTH_M:.2f} m" in unreachable[0].message
    assert validate_invariants(barred).valid, "règle souple par défaut"
    hard = Constraint(kind=ConstraintKind.MIN_PASSAGE, element_ids=[desk_id], hard=True)
    strict = barred.model_copy(update={"constraints": {hard.id: hard}})
    assert not validate_invariants(strict).valid


def test_layout_rules_stay_quiet_without_furniture() -> None:
    assert check_layout(rect_room_project()) == []
    assert validate_invariants(rect_room_project()).valid


@pytest.mark.parametrize("asset", ["bed_double_160", "sofa_2", "table_2"])
def test_every_catalog_asset_fits_somewhere_in_the_room(asset: str) -> None:
    project = _add(rect_room_project(), asset, 2.0, 1.6)
    assert not any(v.severity == "high" for v in check_layout(project)), _types(project)
