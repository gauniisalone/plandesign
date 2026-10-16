"""Rôle Critique (3.5) : remarques des règles (meuble + règle, tri, action), variante révisée
par une action (jamais appliquée), validation tolérante de la réponse du modèle, chaîne avec
un modèle simulé (texte seul, consentement, réessai, artefacts)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

import pytest
from shapely.geometry import Polygon

from openplan.adapters.gateway import ConsentRequiredError, ModelGateway, read_network_log
from openplan.adapters.protocols import LlmRequest, LlmResponse
from openplan.adapters.remote.critique import CritiqueAgent, CritiqueParseError, parse_critique
from openplan.pipeline.critique import (
    STEP,
    CritiqueBody,
    furniture_labels,
    preview_critique,
    rule_remarks,
    run_critique,
)
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.layouts import (
    LayoutBody,
    LayoutProposal,
    ReviseBody,
    build_context,
    generate_layouts,
    revise_proposal,
)
from openplan.scene.catalog import asset, furniture_from_asset
from openplan.schemas import Source
from openplan.schemas.common import Point2
from openplan.schemas.layout import IMPORTANCE_RANK, BriefItem, CritiqueAction, LayoutBrief
from openplan.schemas.scene import Project
from openplan.validation.layout import footprint_corners
from tests.factories import only_level, rect_room_project
from tests.unit.test_layouts import BEDROOM

CONTRARY = LayoutBrief(
    items=[
        BriefItem(category="desk", away_from=["window"]),
        BriefItem(category="bed_double", near=["wall"]),
        BriefItem(category="nightstand", priority="nice_to_have"),
    ]
)


def _hand_made() -> tuple[Project, UUID, LayoutProposal]:
    """Bureau collé sous la fenêtre (mur W3, y = 3) et lit dos au mur W1 : variante à critiquer."""
    project = rect_room_project()
    level_id = only_level(project).id
    desk = furniture_from_asset(
        asset("desk_140"), Point2(x=1.8, y=2.555), -math.pi, source=Source.GENERATED
    )
    bed = furniture_from_asset(
        asset("bed_double_160"), Point2(x=2.5, y=1.095), 0.0, source=Source.GENERATED
    )
    proposal = LayoutProposal(
        index=2, seed=5, score=0.0, valid=True, summary="", furniture=[desk, bed]
    )
    return project, level_id, proposal


def _polygon(furniture) -> Polygon:  # type: ignore[no-untyped-def]
    t, b = furniture.transform, furniture.bbox
    return Polygon(footprint_corners(t.position.x, t.position.y, t.yaw, b.width, b.depth))


def test_rule_remarks_cite_furniture_and_rule_sorted_with_actions() -> None:
    project, level_id, proposal = _hand_made()
    remarks = rule_remarks(project, level_id, CritiqueBody(proposal=proposal, brief=CONTRARY))
    desk = proposal.furniture[0]
    away = next(r for r in remarks if r.rule == "preference_away")
    assert away.furniture_id == desk.id and away.furniture_label == "Bureau 140 × 70"
    assert away.rule_label == "souhait « loin de » non tenu" and "fenêtre" in away.message
    assert away.action is not None and away.action.kind == "move_away"
    assert away.action.target == "window" and away.action.furniture_id == desk.id
    assert away.action.label == "Déplacer Bureau 140 × 70 loin d'une fenêtre"
    assert all(r.furniture_id is not None and r.rule and r.origin == "rules" for r in remarks)
    ranks = [IMPORTANCE_RANK[r.importance] for r in remarks]
    assert ranks == sorted(ranks)
    # Le lit dos au mur tient son souhait : aucune remarque « près d'un mur » pour lui.
    assert not any(r.rule == "preference_near" for r in remarks)


def test_revise_moves_only_the_desk_away_from_the_window() -> None:
    project, level_id, proposal = _hand_made()
    remarks = rule_remarks(project, level_id, CritiqueBody(proposal=proposal, brief=CONTRARY))
    action = next(r for r in remarks if r.rule == "preference_away").action
    assert action is not None
    out = revise_proposal(
        project, level_id, ReviseBody(proposal=proposal, action=action, brief=CONTRARY)
    )
    ctx = build_context(project, level_id, None)
    desk = next(f for f in out.furniture if f.category == "desk")
    assert desk.id == proposal.furniture[0].id and desk.source is Source.GENERATED
    assert ctx.landmarks.far("window", _polygon(desk)), "le bureau est loin de la fenêtre"
    bed = next(f for f in out.furniture if f.category == "bed_double")
    assert bed == proposal.furniture[1], "les autres meubles ne bougent pas"
    assert out.index == 2 and out.seed == 5 and out.valid
    assert (
        out.summary.startswith(("Très bien", "Correct", "À revoir"))
        and "souhaits tenus" in out.summary
    )
    after = rule_remarks(project, level_id, CritiqueBody(proposal=out, brief=CONTRARY))
    assert not any(r.rule == "preference_away" for r in after)


def test_revise_remove_and_explicit_errors() -> None:
    project, level_id, proposal = _hand_made()
    desk, bed = proposal.furniture
    removed = revise_proposal(
        project,
        level_id,
        ReviseBody(
            proposal=proposal,
            action=CritiqueAction(kind="remove", furniture_id=desk.id),
            brief=CONTRARY,
        ),
    )
    assert [f.id for f in removed.furniture] == [bed.id] and "1 meuble placé" in removed.summary
    remove_desk = CritiqueAction(kind="remove", furniture_id=desk.id)
    remove_bed = CritiqueAction(kind="remove", furniture_id=bed.id)
    with pytest.raises(DetectionError, match="pas dans la variante"):
        revise_proposal(project, level_id, ReviseBody(proposal=removed, action=remove_desk))
    with pytest.raises(DetectionError, match="serait vide"):
        revise_proposal(project, level_id, ReviseBody(proposal=removed, action=remove_bed))
    # Pas de radiateur dans cette pièce : « près d'un radiateur » est impossible, dit, et la
    # variante d'origine reste.
    with pytest.raises(DetectionError, match=r"aucune position .* près d'un radiateur"):
        revise_proposal(
            project,
            level_id,
            ReviseBody(
                proposal=proposal,
                action=CritiqueAction(kind="move_near", furniture_id=desk.id, target="radiator"),
            ),
        )


def test_rule_remarks_on_a_generated_variant_are_consistent_with_its_score() -> None:
    """Une variante du générateur : aucune remarque dure ; ses violations souples et ses
    souhaits manqués se retrouvent en remarques, une par meuble et règle."""
    project = rect_room_project()
    level_id = only_level(project).id
    run = generate_layouts(project, level_id, LayoutBody(brief=BEDROOM, variants=1, seed=1))
    proposal = run.proposals[0]
    remarks = rule_remarks(project, level_id, CritiqueBody(proposal=proposal, brief=BEDROOM))
    assert not any(r.importance == "high" for r in remarks)
    assert len([r for r in remarks if r.rule.startswith("preference_")]) == len(proposal.missed)
    assert len([r for r in remarks if not r.rule.startswith("preference_")]) == len(
        proposal.violations
    )
    labels = dict(furniture_labels(proposal.furniture))
    assert len(set(labels.values())) == len(labels), "libellés uniques (chevets numérotés)"


GOOD_CRITIQUE = {
    "verdict": "Variante fonctionnelle, le bureau gagnerait à voir la fenêtre.",
    "strengths": ["Le lit est dos au mur, loin de la porte."],
    "remarks": [
        {
            "furniture": 1,
            "rule": "light",
            "importance": "high",
            "message": "Le bureau tourne le dos à la lumière du jour.",
            "action": {"kind": "move_near", "target": "window"},
        },
        {
            "furniture": "Lit double 160 × 200",
            "rule": "circulation",
            "importance": "low",
            "message": "On passe d'un seul côté du lit.",
            "action": {"kind": "teleport", "x": 1.0, "y": 2.0},
        },
        {
            "furniture": 1,
            "rule": "preference_away",
            "importance": "medium",
            "message": "Répète la règle déjà connue : fondue.",
        },
        {"furniture": 9, "rule": "style", "message": "Meuble inconnu : écartée."},
        {
            "furniture": 2,
            "rule": "furniture_overlap",
            "importance": "high",
            "message": "Le lit chevauche le bureau (faux : contredit par le calcul).",
        },
        {"furniture": 2, "rule": "feng_shui", "message": "Règle hors liste → autre."},
        {"furniture": 2, "rule": "ergonomics", "message": "   "},
        "pas un objet",
    ],
}


def test_parse_critique_is_tolerant_and_says_what_it_drops() -> None:
    _, _, proposal = _hand_made()
    refs = furniture_labels(proposal.furniture)
    critique = parse_critique("```json\n" + json.dumps(GOOD_CRITIQUE) + "\n```", refs)
    assert critique.verdict.startswith("Variante fonctionnelle") and len(critique.strengths) == 1
    rules = [r.rule for r in critique.remarks]
    # Tri par importance (high, medium sans importance dite = medium, low) puis par meuble.
    assert rules == ["light", "furniture_overlap", "preference_away", "other", "circulation"]
    light = critique.remarks[0]
    assert light.furniture_id == proposal.furniture[0].id and light.origin == "model"
    assert light.action is not None and light.action.kind == "move_near"
    assert light.action.label == "Déplacer Bureau 140 × 70 près d'une fenêtre"
    bed = next(r for r in critique.remarks if r.rule == "circulation")
    assert bed.furniture_id == proposal.furniture[1].id and bed.action is None
    reasons = "\n".join(critique.dropped)
    assert "position proposée" in reasons and "meuble « 9 » absent" in reasons
    assert "feng_shui" in reasons and "sans texte" in reasons and "pas un objet" in reasons
    with pytest.raises(CritiqueParseError, match="sans objet JSON"):
        parse_critique("Je passe.", refs)
    with pytest.raises(CritiqueParseError, match="illisible"):
        parse_critique('{"remarks": [}', refs)
    assert parse_critique("{}", refs).remarks == []


class ScriptedCritic:
    """Modèle local simulé : réponses dans l'ordre ; vérifie que seul du texte part."""

    model_id = "fake:critic"
    remote = False
    supports_images = False
    destination = "localhost:11434"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        assert request.purpose == "critique" and not request.images
        self.requests.append(request)
        return LlmResponse(text=self.answers.pop(0), model_id=self.model_id, input_tokens=600)


def test_run_critique_merges_rules_then_model_text_only(tmp_path: Path) -> None:
    project, level_id, proposal = _hand_made()
    model = ScriptedCritic("Voici mon avis : le bureau est mal placé.", json.dumps(GOOD_CRITIQUE))
    gateway = ModelGateway(tmp_path / "log.jsonl")
    run = run_critique(
        project,
        level_id,
        CritiqueBody(proposal=proposal, brief=CONTRARY),
        tmp_path / "runs",
        model=model,
        gateway=gateway,
    )
    assert run.result.step == STEP and run.result.confidence == 0.6
    assert run.proposal_index == 2 and run.proposal_seed == 5
    assert run.rules_count >= 1 and run.model_count == 3, [r.rule for r in run.critique.remarks]
    ranks = [IMPORTANCE_RANK[r.importance] for r in run.critique.remarks]
    assert ranks == sorted(ranks)
    # La remarque du modèle qui répète une règle déjà dite est fondue.
    away = [r for r in run.critique.remarks if r.rule == "preference_away"]
    assert len(away) == 1 and away[0].origin == "rules"
    light = next(r for r in run.critique.remarks if r.rule == "light")
    assert light.origin == "model" and light.importance == "medium", "plafond du modèle"
    # La collision inventée par le modèle n'est pas confirmée par le calcul : écartée et dite.
    assert not any(r.rule == "furniture_overlap" for r in run.critique.remarks)
    assert any("contredit par la géométrie" in d for d in run.critique.dropped)
    assert run.result.model_ids == ["llm:fake:critic"] and len(model.requests) == 2
    assert any("second essai réussi" in w for w in run.result.warnings)
    prompt = model.requests[0].messages[1].content
    assert "1. Bureau 140 × 70" in prompt and "de la fenêtre" in prompt
    assert "Remarques déjà connues" in prompt and "loin d'une fenêtre" in prompt
    assert "photo" not in prompt.lower()
    names = {a.name for a in run.result.artifacts}
    assert names == {"critique_raw.txt", "critique.json"}
    saved_path = tmp_path / "runs" / str(run.run_id) / "layout" / "critique.json"
    assert json.loads(saved_path.read_text())["remarks"][0]["furniture_label"]
    entries = read_network_log(tmp_path / "log.jsonl")
    assert len(entries) == 2 and all(e.purpose == "critique" and e.images == 0 for e in entries)


def test_run_critique_without_model_is_rules_only_and_preview_sends_nothing(
    tmp_path: Path,
) -> None:
    project, level_id, proposal = _hand_made()
    body = CritiqueBody(proposal=proposal, brief=CONTRARY)
    run = run_critique(project, level_id, body, tmp_path / "runs", model=None, gateway=None)
    assert run.model_count == 0 and run.result.model_ids == []
    assert any("règles seules" in w and "aucun modèle" in w for w in run.result.warnings)
    assert all(r.origin == "rules" for r in run.critique.remarks)
    model = ScriptedCritic()
    preview = preview_critique(project, level_id, body, model)
    assert preview.destination == "localhost:11434" and preview.remote is False
    assert preview.characters == len(preview.text) and model.requests == []
    # `use_model: false` : configuré mais non sollicité.
    quiet = run_critique(
        project,
        level_id,
        CritiqueBody(proposal=proposal, brief=CONTRARY, use_model=False),
        tmp_path / "runs",
        model=model,
        gateway=ModelGateway(None),
    )
    assert model.requests == [] and any("non sollicité" in w for w in quiet.result.warnings)


def test_remote_critic_requires_consent_and_bad_answers_give_up(tmp_path: Path) -> None:
    project, level_id, proposal = _hand_made()
    remote = ScriptedCritic(json.dumps(GOOD_CRITIQUE))
    remote.remote = True
    with pytest.raises(ConsentRequiredError):
        run_critique(
            project,
            level_id,
            CritiqueBody(proposal=proposal),
            tmp_path / "runs",
            model=remote,
            gateway=ModelGateway(tmp_path / "log.jsonl"),
        )
    assert remote.requests == [] and read_network_log(tmp_path / "log.jsonl") == []
    stubborn = ScriptedCritic("non", "toujours non")
    with pytest.raises(CritiqueParseError, match="second essai"):
        run_critique(
            project,
            level_id,
            CritiqueBody(proposal=proposal),
            tmp_path / "runs",
            model=stubborn,
            gateway=ModelGateway(None),
        )
    with pytest.raises(DetectionError, match="aucun meuble"):
        run_critique(
            project,
            level_id,
            CritiqueBody(proposal=proposal.model_copy(update={"furniture": []})),
            tmp_path / "runs",
        )


def test_agent_prompt_lists_rules_places_and_forbids_positions() -> None:
    prompt = CritiqueAgent.user_prompt("Pièce test", "desk × 1", "1. Bureau", "")
    assert "Pièce test" in prompt and "desk × 1" in prompt and "1. Bureau" in prompt
    assert "aucune" in prompt and "circulation" in prompt and "door_swing" in prompt
    assert "window_blocked" not in prompt, "la géométrie n'est pas proposée au modèle"
    assert "Pas de position" in prompt and "move_away" in prompt


def test_furniture_labels_say_where() -> None:
    from openplan.scene.catalog import furniture_from_asset

    def at(x: float, y: float):  # type: ignore[no-untyped-def]
        return furniture_from_asset(asset("nightstand"), Point2(x=x, y=y), 0.0)

    pair = [at(3.0, 1.0), at(1.0, 1.0)]
    assert [label for _, label in furniture_labels(pair)] == [
        "Table de chevet (à droite)",
        "Table de chevet (à gauche)",
    ]
    stacked = [at(1.0, 2.0), at(1.0, 0.5)]
    assert next(label for _, label in furniture_labels(stacked)).endswith("(en haut)")
    three = [at(2.0, 1.0), at(1.0, 1.0), at(3.0, 1.0)]
    assert [label for _, label in furniture_labels(three)] == [
        "Table de chevet (n° 2)",
        "Table de chevet (n° 1)",
        "Table de chevet (n° 3)",
    ]
