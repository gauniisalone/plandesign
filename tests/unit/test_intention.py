"""Rôle Intention (3.3) : validation tolérante du brief, description de la pièce, chaîne
avec un modèle simulé (texte seul, consentement, réessai, artefacts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openplan.adapters.gateway import ConsentRequiredError, ModelGateway, read_network_log
from openplan.adapters.protocols import LlmRequest, LlmResponse
from openplan.adapters.remote.intention import (
    IntentionAgent,
    IntentionParseError,
    numbers_in,
    parse_brief,
)
from openplan.pipeline.intention import (
    STEP,
    IntentionBody,
    describe_room,
    preview_intention,
    run_intention,
)
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, UpdateElement, apply_op
from openplan.schemas import Furniture
from openplan.schemas.common import Point2
from tests.factories import attic_project, only_level, rect_room_project

CATEGORIES = load_catalog().categories
USER_TEXT = "Chambre d'amis avec un coin bureau, le lit fait 160 × 200, rien devant le radiateur"
GOOD = {
    "room_use": "guest_room_office",
    "items": [
        {
            "category": "bed_double",
            "count": 1,
            "priority": "required",
            "dimensions_m": {"width": 1.6, "depth": 2.0},
            "near": ["wall"],
            "away_from": ["window", "door_swing"],
        },
        {"category": "desk", "count": 1, "priority": "required", "near": ["window"]},
        {"category": "nightstand", "count": 2, "priority": "nice_to_have"},
    ],
    "avoid": ["rien devant le radiateur"],
    "notes": "budget non précisé",
}


def test_numbers_written_by_the_user_in_metres_and_centimetres() -> None:
    found = numbers_in("le lit fait 160 × 200 et le bureau 1,40 m")
    assert {160.0, 1.6, 200.0, 2.0, 1.4} <= found
    assert any(abs(v - 0.014) < 1e-9 for v in found)


def test_parse_brief_keeps_valid_items_and_user_dimensions() -> None:
    brief = parse_brief("```json\n" + json.dumps(GOOD) + "\n```", CATEGORIES, USER_TEXT)
    assert [i.category for i in brief.items] == ["bed_double", "desk", "nightstand"]
    bed = brief.items[0]
    assert bed.dimensions_m is not None and bed.dimensions_m.width == 1.6
    assert bed.dimensions_m.depth == 2.0 and bed.dimensions_m.height is None
    assert bed.near == ["wall"] and bed.away_from == ["window", "door_swing"]
    assert brief.items[2].count == 2 and brief.items[2].priority == "nice_to_have"
    assert brief.avoid == ["rien devant le radiateur"] and brief.notes == "budget non précisé"
    assert brief.dropped == [] and len(brief.required) == 2
    assert brief.room_use == "guest_room_office"


def test_parse_brief_drops_and_explains() -> None:
    """Catégorie hors catalogue, position proposée, dimension non écrite par l'utilisateur,
    dimensions de pièce, repère inconnu : écartés, chacun avec son motif ; rien n'est inventé."""
    sneaky = {
        "room_width": 8.0,
        "items": [
            {"category": "piano", "count": 1},
            {
                "category": "desk",
                "position": {"x": 1.0, "y": 2.0},
                "dimensions_m": {"width": 1.8},
                "near": ["fireplace", "window"],
            },
            {"category": "wardrobe", "count": 0},
            "pas un objet",
        ],
        "avoid": "texte au lieu d'une liste",
    }
    brief = parse_brief(json.dumps(sneaky), CATEGORIES, "un bureau et une armoire")
    assert [i.category for i in brief.items] == ["desk", "wardrobe"]
    desk = brief.items[0]
    assert desk.dimensions_m is None and desk.near == ["window"]
    assert brief.items[1].count == 1, "quantité 0 ramenée à 1"
    reasons = "\n".join(brief.dropped)
    assert "dimensions ou une forme de pièce" in reasons
    assert "piano" in reasons and "hors catalogue" in reasons
    assert "position proposée" in reasons and "moteur géométrique" in reasons
    assert "width 1,8 m" in reasons and "non écrit par vous" in reasons
    assert "élément 4 : pas un objet" in reasons
    assert brief.avoid == []


def test_parse_brief_errors_only_without_json() -> None:
    with pytest.raises(IntentionParseError, match="sans objet JSON"):
        parse_brief("Je ne peux pas répondre.", CATEGORIES, "x")
    with pytest.raises(IntentionParseError, match="illisible"):
        parse_brief('{"items": [}', CATEGORIES, "x")
    with pytest.raises(IntentionParseError, match="sans objet JSON"):
        parse_brief("[1, 2]", CATEGORIES, "x")
    empty = parse_brief("{}", CATEGORIES, "x")
    assert empty.items == [] and empty.dropped == []


def test_describe_room_says_the_plan_and_nothing_else() -> None:
    project = attic_project()
    level_id = only_level(project).id
    with_furniture = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=2.5), name="Bureau"),
    )
    desk = next(e for e in only_level(with_furniture).elements.values() if isinstance(e, Furniture))
    with_furniture = apply_op(
        with_furniture, level_id, UpdateElement(element_id=desk.id, fields={"preserve": True})
    )
    text = describe_room(with_furniture, level_id)
    assert "m² au sol" in text and "à plus de 1,80 m" in text
    assert "Hauteur sous plafond" in text and "Murs :" in text
    assert "Pentes de toit : 2" in text and "égout à" in text
    assert "Bureau 1,40 × 0,70 × 0,75 m (à conserver)" in text
    assert "ne se modifient pas" in text
    assert "photo" not in text.lower() and "image" not in text.lower()
    rect = rect_room_project()
    plain = describe_room(rect, only_level(rect).id)
    assert "porte de 0,83 m" in plain and "fenêtre de 1,20 m" in plain and "allège 0,90 m" in plain
    assert "Aucun meuble" in plain and "Pentes" not in plain


class ScriptedModel:
    """Modèle local simulé : rend les réponses dans l'ordre, vérifie qu'aucune image ne part."""

    model_id = "fake:intention"
    remote = False
    supports_images = False
    destination = "localhost:11434"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        assert request.purpose == "intention" and not request.images
        self.requests.append(request)
        return LlmResponse(text=self.answers.pop(0), model_id=self.model_id, input_tokens=400)


def test_preview_shows_what_would_leave_without_calling_the_model() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    model = ScriptedModel()
    preview = preview_intention(project, level_id, IntentionBody(text=USER_TEXT), model)
    assert preview.text == USER_TEXT and "porte de 0,83 m" in preview.room_description
    assert preview.destination == "localhost:11434" and preview.remote is False
    assert preview.characters > len(USER_TEXT) + len(preview.room_description)
    assert set(CATEGORIES) == set(preview.categories) and model.requests == []
    without = preview_intention(project, level_id, IntentionBody(text=USER_TEXT), None)
    assert without.model_id is None and without.destination is None


def test_run_intention_text_only_with_retry_and_artifacts(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    model = ScriptedModel("Désolé, voici une idée : un lit et un bureau.", json.dumps(GOOD))
    gateway = ModelGateway(tmp_path / "log.jsonl")
    run = run_intention(
        project,
        level_id,
        IntentionBody(text=USER_TEXT),
        tmp_path / "runs",
        model=model,
        gateway=gateway,
    )
    assert run.result.step == STEP and run.result.confidence == 0.6
    assert [i.category for i in run.brief.items] == ["bed_double", "desk", "nightstand"]
    assert run.brief.keep == [] and run.result.model_ids == ["llm:fake:intention"]
    assert any("second essai réussi" in w for w in run.result.warnings)
    assert len(model.requests) == 2
    retry = model.requests[1]
    assert retry.messages[-1].role == "user" and "uniquement" in retry.messages[-1].content
    assert "porte de 0,83 m" in model.requests[0].messages[1].content
    assert USER_TEXT in model.requests[0].messages[1].content
    names = {a.name for a in run.result.artifacts}
    assert names == {"intention_raw.txt", "brief.json"}
    raw = (tmp_path / "runs" / str(run.run_id) / "intention" / "intention_raw.txt").read_text()
    assert "second essai" in raw
    entries = read_network_log(tmp_path / "log.jsonl")
    assert len(entries) == 2 and all(e.purpose == "intention" and e.images == 0 for e in entries)
    assert all(e.ok and not e.remote for e in entries)


def test_run_intention_gives_up_after_two_bad_answers(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    model = ScriptedModel("non", "toujours non")
    with pytest.raises(IntentionParseError, match="second essai"):
        run_intention(
            project,
            level_id,
            IntentionBody(text=USER_TEXT),
            tmp_path / "runs",
            model=model,
            gateway=ModelGateway(None),
        )
    assert len(model.requests) == 2


def test_remote_model_requires_consent_before_any_text_leaves(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    model = ScriptedModel(json.dumps(GOOD))
    model.remote = True  # type: ignore[misc]
    with pytest.raises(ConsentRequiredError):
        run_intention(
            project,
            level_id,
            IntentionBody(text=USER_TEXT),
            tmp_path / "runs",
            model=model,
            gateway=ModelGateway(tmp_path / "log.jsonl"),
        )
    assert model.requests == [] and read_network_log(tmp_path / "log.jsonl") == []


def test_agent_prompt_lists_categories_and_forbids_positions() -> None:
    prompt = IntentionAgent.user_prompt("un lit", "Pièce test", CATEGORIES)
    assert "Pièce test" in prompt and "bed_double" in prompt and "low_wall" in prompt
    assert "Pas de position" in prompt and "« un lit »" in prompt
