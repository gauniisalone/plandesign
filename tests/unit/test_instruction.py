"""V1 (note 10, point 5) : consigne libre → intention de design. Quinze consignes de
référence en phrases simples (hors ligne, obligatoire) ; le modèle est testé sur sa réponse
(validation champ par champ, réessai, complément par les phrases) et, s'il est présent, en
vrai ; le prompt de finition est dérivé de l'intention."""

from __future__ import annotations

import os
from typing import Any

import pytest

from openplan.adapters.protocols import LlmRequest, LlmResponse
from openplan.pipeline.finish import MAX_PROMPT_WORDS, finish_prompt
from openplan.pipeline.instruction import (
    InstructionParseError,
    intent_fields,
    parse_translation,
    translate_phrases,
    translate_with_model,
)
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import SetIntent, apply_op
from openplan.schemas.elements import Room
from openplan.schemas.intent import DesignIntent
from tests.factories import attic_project, only_level

CATEGORIES = load_catalog().categories

REFERENCE: list[tuple[str, dict[str, Any]]] = [
    (
        "grenier chaleureux, bois et lin, deux lits",
        {
            "room_type": "attic",
            "mood": ["chaleureux"],
            "palette": ["lin", "sable", "chene clair"],
            "furniture": [{"category": "bed_single", "count": 2}],
        },
    ),
    (
        "chambre d'ado industrielle",
        {"room_type": "bedroom", "usage": "chambre d'ado", "style": "industriel"},
    ),
    (
        "bureau scandinave lumineux sous les toits",
        {"room_type": "office", "style": "scandinave", "mood": ["lumineux"]},
    ),
    ("salon chalet avec cheminée", {"room_type": "living_room", "style": "chalet"}),
    (
        "chambre d'amis épurée, murs blancs à la chaux, parquet clair",
        {
            "room_type": "bedroom",
            "usage": "chambre d'amis",
            "mood": ["epure"],
            "materials": {"walls": "limewash", "floor": "wood_floor"},
        },
    ),
    (
        "suite parentale cosy avec coin lecture, lumière chaude du soir",
        {
            "room_type": "bedroom",
            "usage": "suite parentale",
            "mood": ["cosy"],
            "zones": ["couchage", "lecture"],
            "light": {"warmth": "chaude", "time": "soir"},
        },
    ),
    (
        "loft industriel brut, béton ciré et poutres sombres, sans tapis",
        {
            "style": "industriel",
            "mood": ["brut"],
            "materials": {"floor": "concrete_floor", "timber": "oak_dark"},
            "avoid": ["rug", "rug_round"],
        },
    ),
    (
        "séjour contemporain, palette gris anthracite, blanc cassé et noyer, format portrait",
        {
            "room_type": "living_room",
            "style": "contemporain",
            "palette": ["gris anthracite", "blanc casse", "noyer"],
            "views": {"aspect": "4:5"},
        },
    ),
    (
        "chambre d'enfant douillette en vert sauge et bois clair",
        {
            "room_type": "bedroom",
            "usage": "chambre d'enfant",
            "mood": ["cosy"],
            "palette": ["vert sauge", "chene clair", "lin"],
        },
    ),
    (
        "combles scandinaves avec poutres blanches et lambris blanc, un bureau sous la fenêtre",
        {
            "room_type": "attic",
            "style": "scandinave",
            "materials": {"timber": "painted_wood", "slopes": "painted_wood"},
            "furniture": [{"category": "desk", "count": 1}],
        },
    ),
    (
        "chambre sobre et lumineuse, heure dorée",
        {
            "room_type": "bedroom",
            "mood": ["lumineux"],
            "decor_density": "sobre",
            "light": {"time": "heure_doree"},
        },
    ),
    (
        "salon bohème riche en déco, tons terracotta et ocre",
        {
            "room_type": "living_room",
            "style": "naturel",
            "decor_density": "riche",
            "palette": ["terracotta", "ocre", "lin"],
        },
    ),
    (
        "chambre avec deux chevets et une commode, pas de canapé",
        {
            "room_type": "bedroom",
            "furniture": [
                {"category": "nightstand", "count": 2},
                {"category": "chest", "count": 1},
            ],
            "avoid": ["sofa"],
        },
    ),
    (
        "pièce chaleureuse au style chalet, pierre apparente et parquet ancien",
        {
            "style": "chalet",
            "mood": ["chaleureux"],
            "materials": {"gables": "stone", "floor": "wood_dark"},
        },
    ),
    (
        "bureau contemporain avec espace de travail et beaucoup de rangements, "
        "lumière froide du matin",
        {
            "room_type": "office",
            "style": "contemporain",
            "zones": ["bureau", "rangement"],
            "light": {"warmth": "froide", "time": "matin"},
        },
    ),
]


@pytest.mark.parametrize(("text", "expected"), REFERENCE, ids=[t for t, _ in REFERENCE])
def test_reference_instruction_in_phrases(text: str, expected: dict[str, Any]) -> None:
    out = translate_phrases(text, CATEGORIES)
    assert out.fields == expected, out.understood
    assert out.by == "phrases"
    # Chaque traduction est une intention valide.
    current = DesignIntent()
    DesignIntent.model_validate({**current.model_dump(), **intent_fields(current, out.fields)})


def test_reference_set_has_fifteen_varied_instructions() -> None:
    assert len(REFERENCE) == 15
    assert len({e.get("style") for _, e in REFERENCE}) >= 5


def test_unknown_things_are_noted_not_invented() -> None:
    out = translate_phrases("salon chalet avec cheminée", CATEGORIES)
    assert any("cheminée" in n for n in out.notes)
    assert "furniture" not in out.fields


def test_one_or_two_colours_are_completed_by_the_style_and_it_is_said() -> None:
    out = translate_phrases("chambre scandinave en vert sauge", CATEGORIES)
    assert out.fields["palette"] == ["vert sauge", "blanc casse", "lin"]
    assert any("complétée" in n for n in out.notes)


# ------------------------------------------------------------------ modèle
def test_model_answer_is_validated_field_by_field() -> None:
    raw = (
        '```json\n{"fields": {"style": "scandinave", "mood": ["lumineux", "joyeux"], '
        '"palette": ["lin", "blanc casse", "chene clair"], "furniture": '
        '[{"category": "desk", "count": 1}, {"category": "piano", "count": 1}], '
        '"position": {"x": 1}}, "notes": ["pas de piano au catalogue"]}\n```'
    )
    out = parse_translation(raw, CATEGORIES)
    assert out.fields["style"] == "scandinave"
    assert "mood" not in out.fields, "humeur inconnue : le champ est écarté"
    assert out.fields["furniture"] == [{"category": "desk", "count": 1}]
    assert any("piano" in d for d in out.dropped)
    assert any("position" in d for d in out.dropped)
    assert out.notes == ["pas de piano au catalogue"]
    with pytest.raises(InstructionParseError):
        parse_translation("je ne sais pas", CATEGORIES)


class _FakeModel:
    model_id = "fake-local"
    remote = False
    supports_images = False
    destination = "localhost"

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(text=self.answers.pop(0), model_id=self.model_id)


class _Gateway:
    def bind(self, model: Any, project: Any) -> Any:
        return model.complete


def test_model_retries_once_and_phrases_fill_what_it_forgot() -> None:
    model = _FakeModel(["voilà ma réponse", '{"fields": {"style": "chalet"}}'])
    out = translate_with_model(
        "salon chalet chaleureux, format portrait",
        CATEGORIES,
        model=model,
        gateway=_Gateway(),  # type: ignore[arg-type]
        project=attic_project(),
    )
    assert len(model.requests) == 2, "un réessai"
    assert out.by == "llm:fake-local"
    assert out.fields["style"] == "chalet"
    assert out.fields["mood"] == ["chaleureux"], "oublié par le modèle, lu par les phrases"
    assert out.fields["views"] == {"aspect": "4:5"}
    # Texte seul : aucune image ne part.
    assert all(not r.images for r in model.requests)
    assert "salon chalet chaleureux" in model.requests[0].messages[1].content


@pytest.mark.skipif(
    not os.environ.get("OPENPLAN_TEST_LLM"),
    reason="modèle local absent (OPENPLAN_TEST_LLM=1 avec un modèle configuré pour l'essayer)",
)
@pytest.mark.parametrize(("text", "expected"), REFERENCE[:5], ids=[t for t, _ in REFERENCE[:5]])
def test_reference_instruction_with_the_local_model(text: str, expected: dict[str, Any]) -> None:
    from openplan.adapters.gateway import ModelGateway
    from openplan.adapters.registry import build_language_model
    from openplan.api.settings import Settings

    model = build_language_model(Settings.from_env().ai)
    if model is None or model.remote:
        pytest.skip("pas de modèle local configuré")
    out = translate_with_model(
        text,
        CATEGORIES,
        model=model,
        gateway=ModelGateway(None),
        project=attic_project(),
    )
    for name in ("room_type", "style"):
        if name in expected:
            assert out.fields.get(name) == expected[name]


# ------------------------------------------------------------------ application et prompt
def test_partial_objects_keep_what_was_not_said() -> None:
    current = DesignIntent.model_validate({"light": {"time": "matin", "warmth": "froide"}})
    fields = intent_fields(current, {"light": {"warmth": "chaude"}})
    assert fields["light"] == {"time": "matin", "warmth": "chaude"}


def test_instruction_is_applied_as_user_choice_and_kept() -> None:
    project = attic_project()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    text = "combles scandinaves cosy, poutres blanches"
    out = translate_phrases(text, CATEGORIES)
    fields = intent_fields(room.intent, out.fields)
    fields.update({"instruction": text, "translated_by": out.by})
    after = apply_op(project, level.id, SetIntent(room_id=room.id, fields=fields))
    intent = only_level(after).elements[room.id].intent  # type: ignore[union-attr]
    assert intent is not None
    assert intent.style == "scandinave" and intent.chosen_by_user("style")
    assert intent.materials.timber == "painted_wood"
    assert intent.instruction == text and intent.translated_by == "phrases"
    assert "instruction" not in intent.sources


def test_finish_prompt_is_derived_from_the_intent() -> None:
    project = attic_project()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    before = finish_prompt(project, None)
    assert "natural materials" in before
    project = apply_op(
        project,
        level.id,
        SetIntent(
            room_id=room.id,
            fields={
                "style": "industriel",
                "mood": ["brut"],
                "palette": ["gris anthracite", "noyer", "terracotta"],
                "materials": {"floor": "concrete_floor"},
                "light": {"time": "heure_doree", "warmth": "chaude"},
            },
        ),
    )
    prompt = finish_prompt(project, None)
    for words in (
        "industrial loft",
        "raw textures",
        "polished concrete floor",
        "charcoal grey",
        "golden hour light",
        "warm tones",
        "converted attic",
    ):
        assert words in prompt, (words, prompt)
    assert len(prompt.split()) <= MAX_PROMPT_WORDS
    assert "scandinavian" in finish_prompt(project, "scandinave"), "le style forcé l'emporte"
