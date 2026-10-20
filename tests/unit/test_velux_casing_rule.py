"""KAN-67 : les règles d'aménagement et le générateur connaissent le caisson des velux (KAN-63).
Le volume vient du bake (une seule géométrie) ; le générateur n'y pose aucun meuble, et un
meuble posé à la main qui y entre donne une règle souple `velux_casing`, visible au glisser,
dans la validation et dans la Critique (avec « Déplacer … loin d'un velux »)."""

from __future__ import annotations

import numpy as np
import pytest

from openplan.geom.velux import (
    CHEVETRE_DEPTH_M,
)
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.critique import CritiqueBody, rule_remarks
from openplan.pipeline.layouts import (
    FurnitureMoveBody,
    LayoutBody,
    ReviseBody,
    apply_layout,
    build_context,
    check_furniture_move,
    generate_layouts,
    revise_proposal,
)
from openplan.pipeline.scene3d import (
    bake_level,
)
from openplan.scene.ops import AddFurniture, AddRoofWindow, apply_op
from openplan.schemas import Furniture, RoofPlane, RoofWindow
from openplan.schemas.common import Point2
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import CASING_TOLERANCE_M, check_layout, velux_casings
from tests.factories import attic_project, only_level, rect_room_project
from tests.unit.test_timber_reveal import _attic, _with_windows
from tests.unit.test_velux_chevetre import BUILT_IN, DESK_UNDER_VELUX


def _baked_boards(project: Project) -> list[np.ndarray]:
    level = only_level(project)
    return [
        np.array([[v.x, v.y, v.z] for v in p.vertices])
        for p in bake_level(project, level.id, style="realistic").polyhedra
        if p.kind == "reveal"
    ]


def _key(points: np.ndarray) -> tuple[float, ...]:
    return tuple(np.round(np.sort(points, axis=0), 6).ravel())


@pytest.mark.parametrize("pitch_deg", [30, 45, 60])
def test_rules_use_the_baked_casing(pitch_deg: float) -> None:
    """Must have 1 : le volume des règles est exactement celui que le bake bâtit (sans meuble
    dessous), planche par planche."""
    project = _with_windows(_attic(pitch_deg))
    level = only_level(project)
    casings = velux_casings(level, WallGraph(level))
    assert len(casings) == 4
    ruled = sorted(_key(b) for c in casings for b in c.boards)
    assert ruled == sorted(_key(b) for b in _baked_boards(project))


def _sill_bottoms(project: Project) -> list[float]:
    level = only_level(project)
    return sorted(
        round(min(v.z for v in p.vertices), 6)
        for p in bake_level(project, level.id, style="realistic").polyhedra
        if p.kind == "reveal" and p.part == 11
    )


@pytest.mark.parametrize("pitch_deg", [45, 60])
@pytest.mark.parametrize("brief", [DESK_UNDER_VELUX, BUILT_IN], ids=["bureau", "lit_encastre"])
def test_generated_layouts_leave_the_casing_whole(pitch_deg: float, brief: LayoutBrief) -> None:
    """Must have 2 : les 4 cas de KAN-63. Aucune variante ne pose un meuble dans le caisson :
    il garde sa pleine profondeur au rendu (appuis identiques à la pièce vide), aucune règle
    `velux_casing`, et aucune variante perdue (3 variantes, meubles requis tous posés).
    Budget de 30 s (décision du PO) : le test juge la géométrie, pas la charge de la machine.
    Surcoût mesuré du caisson sur le générateur : voir le rapport de KAN-67 (60° bureau et
    lit encastré, avant et après l'optimisation)."""
    project = _with_windows(_attic(pitch_deg))
    level_id = only_level(project).id
    empty = _sill_bottoms(project)
    run = generate_layouts(
        project, level_id, LayoutBody(brief=brief, variants=3, seed=4, budget_s=30)
    )
    assert len(run.proposals) == 3, run.result.warnings
    required = {i.category for i in brief.items if i.priority == "required"}
    for proposal in run.proposals:
        assert required <= {f.category for f in proposal.furniture}, proposal.unplaced
        trial = apply_layout(project, level_id, proposal)
        assert not [v for v in check_layout(trial) if v.type == "velux_casing"]
        assert _sill_bottoms(trial) == empty, "caisson en pleine profondeur"
        assert not [v for v in check_layout(trial) if v.severity == "high"]


def _chest_under_velux() -> tuple[Project, Furniture, RoofWindow]:
    project = _attic(60)
    level = only_level(project)
    roof = min(
        (e for e in level.elements.values() if isinstance(e, RoofPlane)),
        key=lambda r: min(r.eave_start.y, r.eave_end.y),
    )
    project = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=1.8, distance=0.6))
    project = apply_op(
        project,
        level.id,
        AddFurniture(asset_id="chest_of_drawers", position=Point2(x=2.2, y=0.35), yaw=0.0),
    )
    level = only_level(project)
    chest = next(e for e in level.elements.values() if isinstance(e, Furniture))
    velux = next(e for e in level.elements.values() if isinstance(e, RoofWindow))
    return project, chest, velux


def test_a_hand_placed_chest_gets_a_soft_rule_everywhere() -> None:
    """Must have 3 : une commode posée à la main sous le velux bas (60°) : règle souple
    `velux_casing` qui cite le meuble, le velux (son nom à l'écran) et les deux hauteurs ; dans
    la validation du plan et pendant le glisser ; jamais une règle dure (la pose reste permise)."""
    project, chest, velux = _chest_under_velux()
    level = only_level(project)
    rule = [v for v in check_layout(project) if v.type == "velux_casing"]
    assert len(rule) == 1 and rule[0].severity == "medium"
    assert set(rule[0].element_ids) == {chest.id, velux.id}
    label = chest.name or chest.category
    assert f"« {label} » passe sous le caisson du « {velux.name} »" in rule[0].message
    # Cotes à la française, comme le reste de l'application (« 0,72 m »).
    assert "bas du caisson à 0,72 m" in rule[0].message and "meuble à 0,85 m" in rule[0].message
    report = validate_invariants(project)
    assert report.valid, "règle souple : rien n'est bloqué"
    assert any(v.type == "velux_casing" for v in report.violations)
    dragged = check_furniture_move(
        project, level.id, chest.id, FurnitureMoveBody(position=Point2(x=2.2, y=0.35))
    )
    assert dragged.valid and any(v.type == "velux_casing" for v in dragged.violations)
    away = check_furniture_move(
        project, level.id, chest.id, FurnitureMoveBody(position=Point2(x=4.5, y=2.5))
    )
    assert not any(v.type == "velux_casing" for v in away.violations)


def test_the_critique_offers_to_move_it_away_from_the_velux() -> None:
    """Must have 3 : dans une variante, la Critique cite la règle et propose « Déplacer …
    loin d'un velux » ; l'action recalcule une variante sans le meuble sous le caisson."""
    project, chest, _velux = _chest_under_velux()
    level_id = only_level(project).id
    run = generate_layouts(
        project, level_id, LayoutBody(brief=LayoutBrief(items=[BriefItem(category="desk")]))
    )
    assert run.proposals
    proposal = run.proposals[0]
    # La commode posée à la main entre dans la variante (comme un meuble à revoir).
    under = proposal.model_copy(update={"furniture": [*proposal.furniture, chest]})
    body = CritiqueBody(proposal=under, use_model=False)
    remarks = [r for r in rule_remarks(project, level_id, body) if r.rule == "velux_casing"]
    assert len(remarks) == 1 and remarks[0].furniture_id == chest.id
    assert remarks[0].rule_label == "sous le caisson d'un velux"
    action = remarks[0].action
    assert action is not None and action.kind == "move_away" and action.target == "skylight"
    assert action.label.endswith("loin d'un velux")
    revised = revise_proposal(project, level_id, ReviseBody(proposal=under, action=action))
    trial = apply_layout(
        project.model_copy(
            update={
                "levels": {
                    **project.levels,
                    level_id: only_level(project).model_copy(
                        update={
                            "elements": {
                                k: v
                                for k, v in only_level(project).elements.items()
                                if k != chest.id
                            }
                        }
                    ),
                }
            }
        ),
        level_id,
        revised,
    )
    assert not [v for v in check_layout(trial) if v.type == "velux_casing"]


@pytest.mark.parametrize("factory", [attic_project, rect_room_project], ids=["combles", "piece"])
def test_without_velux_nothing_changes(factory) -> None:  # type: ignore[no-untyped-def]
    """Must have 4 : sans velux, aucun caisson dans les règles ni le générateur (les
    aménagements générés sont ceux d'avant : la non-régression est vérifiée aussi par les
    tests d'aménagement existants et par la comparaison avant/après du rapport)."""
    project = factory()
    level = only_level(project)
    assert velux_casings(level, WallGraph(level)) == []
    ctx = build_context(project, level.id, None)
    assert ctx.casings == [] and ctx.landmarks.skylights == []
    assert CASING_TOLERANCE_M == 0.005 and pytest.approx(0.40) == CHEVETRE_DEPTH_M
