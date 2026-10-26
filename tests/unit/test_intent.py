"""V1 (note 10, point 1) : intention de design dans la pièce, migration 0.5.0, priorité aux
choix de l'utilisateur, couloir neutre derrière un passage."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openplan.pipeline.decor import ambiance
from openplan.pipeline.scene3d import bake_level
from openplan.scene.migrate import load_project
from openplan.scene.ops import AddOpening, OpError, SetIntent, apply_op
from openplan.schemas.common import SCHEMA_VERSION, Source
from openplan.schemas.elements import OpeningKind, Room, Wall
from openplan.schemas.intent import DesignIntent, merge_intent
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project

PIPELINE = "pipeline:design"


def _room(project: Project) -> Room:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Room))


def test_schema_closes_the_vocabulary() -> None:
    intent = DesignIntent(style="chalet", mood=["chaleureux"], palette=["lin", "noyer", "ocre"])
    assert intent.source_of("style") is Source.INFERRED, "sans provenance : déduit"
    with pytest.raises(ValidationError, match="style inconnu"):
        DesignIntent(style="baroque")
    with pytest.raises(ValidationError, match="3 à 5"):
        DesignIntent(palette=["lin", "noyer"])
    with pytest.raises(ValidationError):
        DesignIntent(mood=["chaleureux", "lumineux", "brut"])
    with pytest.raises(ValidationError, match="couleurs inconnues"):
        DesignIntent(palette=["lin", "noyer", "fuchsia"])


def test_an_automatic_step_never_overwrites_a_user_choice() -> None:
    chosen, _ = merge_intent(DesignIntent(), {"style": "industriel"}, by_user=True)
    assert chosen.chosen_by_user("style")
    auto, kept = merge_intent(chosen, {"style": "chalet", "decor_density": "riche"}, by_user=False)
    assert auto.style == "industriel" and kept == ["style"]
    assert auto.decor_density == "riche" and auto.source_of("decor_density") is Source.INFERRED
    # L'utilisateur, lui, peut toujours changer d'avis.
    again, kept = merge_intent(auto, {"style": "scandinave"}, by_user=True)
    assert again.style == "scandinave" and kept == []


def test_set_intent_op_marks_user_fields_and_respects_them() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    room = _room(project)
    assert room.intent is None
    project = apply_op(project, level_id, SetIntent(room_id=room.id, fields={"style": "chalet"}))
    intent = _room(project).intent
    assert intent is not None and intent.style == "chalet" and intent.chosen_by_user("style")
    assert intent.room_type == room.room_type, "type repris de la pièce"
    project = apply_op(
        project,
        level_id,
        SetIntent(room_id=room.id, fields={"style": "industriel", "mood": ["lumineux"]}),
        actor=PIPELINE,
    )
    intent = _room(project).intent
    assert intent is not None and intent.style == "chalet" and intent.mood == ["lumineux"]
    with pytest.raises(OpError, match="style inconnu"):
        apply_op(project, level_id, SetIntent(room_id=room.id, fields={"style": "baroque"}))
    with pytest.raises(OpError, match="inconnus"):
        apply_op(project, level_id, SetIntent(room_id=room.id, fields={"couleur": "rouge"}))


def test_decor_follows_the_intent_style() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    assert ambiance(only_level(project)) == "naturel"
    project = apply_op(
        project, level_id, SetIntent(room_id=_room(project).id, fields={"style": "scandinave"})
    )
    assert ambiance(only_level(project)) == "scandinave"


def test_migration_moves_the_c3_ambiance_into_the_intent_without_loss() -> None:
    project = rect_room_project()
    data = project.model_dump(mode="json")
    data["schema_version"] = "0.4.0"
    level = next(iter(data["levels"].values()))
    room = next(e for e in level["elements"].values() if e["type"] == "room")
    room.pop("intent", None)
    room["metadata"] = {"ambiance": "chalet", "note": "à garder"}
    loaded = load_project(data)
    assert loaded.schema_version == SCHEMA_VERSION == "0.20.0"
    migrated = _room(loaded)
    assert migrated.intent is not None and migrated.intent.style == "chalet"
    assert migrated.intent.chosen_by_user("style"), "l'ambiance avait été choisie"
    assert migrated.metadata == {"note": "à garder"}
    assert ambiance(only_level(loaded)) == "chalet"
    # Une valeur inconnue n'est pas perdue : elle reste dans les métadonnées.
    room["metadata"] = {"ambiance": "baroque"}
    odd = _room(load_project(data))
    assert odd.intent is None and odd.metadata == {"ambiance": "baroque"}


def test_a_passage_gets_a_neutral_corridor_on_the_far_side() -> None:
    project = rect_room_project()
    level = only_level(project)
    wall = next(e for e in level.elements.values() if isinstance(e, Wall) and e.name == "W2")
    project = apply_op(
        project,
        level.id,
        AddOpening(host_wall=wall.id, kind=OpeningKind.PASSAGE, offset=1.0, width=0.9, height=2.1),
    )
    bake = bake_level(project, level.id, style="realistic")
    passage = next(
        e.id
        for e in only_level(project).elements.values()
        if e.type == "opening" and e.kind is OpeningKind.PASSAGE  # type: ignore[union-attr]
    )
    parts = [p for p in bake.prisms if p.kind == "joinery" and p.element_id == passage]
    walls = [p for p in parts if p.z1 > level.elevation + 1e-6]
    assert len(walls) == 4, "deux joues, un fond, un plafond"
    assert len(parts) == 6, "plus un seuil dans le mur et un sol de couloir (V1.4b)"
    # W2 est le mur x = 4 de la pièce 4 × 3 : le couloir est au-delà (x > 4).
    assert min(q.x for p in walls for q in p.exterior) >= 4.0 - 0.01
