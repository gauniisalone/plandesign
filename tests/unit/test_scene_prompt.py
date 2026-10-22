"""KAN-86 : la description du rendu suit les vraies matières de la scène.

Scène de test : les combles de contrôle (`attic_project`), pignons en pierre, deux lits simples,
une bibliothèque basse et un chevet, habillage « realistic ». Le prompt dit la pierre (jamais
« plastered walls » par défaut du style), nomme et compte les meubles vus, et sa longueur suit
le moteur."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openplan.pipeline.finish import (
    ENGINE_WORDS,
    PromptEngine,
    _room_word,
    finish_prompt,
    prompt_words,
)
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import (
    FINISH_SURFACE,
    SceneWords,
    load_scene_words,
    scene_words,
)
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import Point2, Point3
from openplan.schemas.elements import Wall
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


def _asset(category: str) -> str:
    return next(a.id for a in load_catalog().assets if a.category == category)


def _stone_attic() -> Project:
    project = attic_project()
    level = only_level(project)
    elements = {
        eid: e.model_copy(update={"metadata": {**e.metadata, "finish": "stone"}})
        if isinstance(e, Wall)
        else e
        for eid, e in level.elements.items()
    }
    project = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    for category, x, y in (
        ("bed_single", 1.5, 1.2),
        ("bed_single", 1.5, 3.8),
        ("low_storage", 4.0, 2.5),
        ("nightstand", 2.6, 0.5),
    ):
        project = apply_op(
            project,
            level.id,
            AddFurniture(asset_id=_asset(category), position=Point2(x=x, y=y), yaw=0.0),
        )
    return project


def _scene(project: Project, camera: RenderCamera | None = None) -> tuple[Bake, str]:
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    words = scene_words(bake, project, level.id, camera)
    return bake, finish_prompt(project, None, level.id, scene=words, engine="zimage")


def test_the_stone_of_the_scene_replaces_the_style_default() -> None:
    project = _stone_attic()
    level = only_level(project)
    before = finish_prompt(project, None, level.id)
    _, after = _scene(project)
    assert "stone" in after, after
    assert "plastered walls" not in after, "plus de matière par défaut contraire à la scène"
    # Combles : les murs de pierre sont les pignons.
    assert "exposed stone gable walls" in after
    assert "stone" not in before, "avant KAN-86, l'ambiance seule ne savait rien de la pierre"


def test_furniture_seen_is_named_and_counted() -> None:
    _, prompt = _scene(_stone_attic())
    assert "two single beds" in prompt, prompt
    assert "a low open bookcase" in prompt, "nommée, elle ne devient pas une cheminée"
    assert "a nightstand" in prompt
    assert "bed singles" not in prompt and "low storage" not in prompt


def test_nothing_is_said_that_the_scene_does_not_have() -> None:
    """Chaque matière dite vient d'un solide de la scène ; chaque objet d'habillage dit existe."""
    bake, prompt = _scene(_stone_attic())
    finishes = {p.finish for p in bake.prisms} | {p.finish for p in bake.polyhedra}
    for table in FINISH_SURFACE.values():
        for finish, phrase in table.items():
            if phrase in prompt:
                assert finish in finishes, f"« {phrase} » sans {finish} dans la scène"
    kinds = {d.kind for d in bake.decor}
    if "pendant lamp" in prompt:
        assert "pendant" in kinds
    if "jute rug" in prompt:
        assert "jute_rug" in kinds
    if "curtains" in prompt:
        assert "curtain" in kinds
    assert "fireplace" not in prompt and "window" not in prompt


def test_length_follows_the_engine() -> None:
    project = _stone_attic()
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    words = scene_words(bake, project, level.id)
    # Une scène plus fournie (huit sortes de meubles en plus) dépasse les 60 mots de SD 1.5.
    rich = SceneWords(
        surfaces=words.surfaces,
        furniture=[*words.furniture, *(f"a {w} chair" for w in "abcdefgh")],
        textiles=words.textiles,
    )
    sd = finish_prompt(project, None, level.id, scene=rich, engine="sd15")
    z = finish_prompt(project, None, level.id, scene=rich, engine="zimage")
    assert len(sd.split()) <= ENGINE_WORDS["sd15"] == 60
    assert len(z.split()) > 60, "Z-Image lit la description entière"
    assert len(z.split()) <= ENGINE_WORDS["zimage"]
    for part in [*rich.surfaces, *rich.furniture, *rich.textiles]:
        assert part in z, f"Z-Image : « {part} » coupé"
    assert z.endswith("realistic textures") and "stone" in sd


def test_only_the_furniture_in_view_is_named() -> None:
    project = _stone_attic()
    level = only_level(project)
    xs = [v.position.x for v in level.vertices.values()]
    ys = [v.position.y for v in level.vertices.values()]
    # Caméra hors de la pièce, le dos tourné : aucun meuble dans l'image.
    away = RenderCamera(
        position=Point3(x=max(xs) + 5.0, y=(min(ys) + max(ys)) / 2, z=1.6),
        yaw=0.0,
        pitch=0.0,
        focal_px=760.0,
        width_px=768,
        height_px=1024,
    )
    _, prompt = _scene(project, away)
    assert "single bed" not in prompt and "bookcase" not in prompt, prompt
    # Caméra dans la pièce, tournée vers les lits (vers les x décroissants).
    inside = away.model_copy(
        update={
            "position": Point3(x=max(xs) - 0.3, y=(min(ys) + max(ys)) / 2, z=1.6),
            "yaw": math.pi,
        }
    )
    _, seen = _scene(project, inside)
    assert "single bed" in seen, seen


def test_without_a_scene_the_prompt_is_unchanged() -> None:
    """Rendu d'avant (sans bake.json) : la finition garde le prompt de l'ambiance, mot pour mot."""
    project = _stone_attic()
    level = only_level(project)
    room, intent = _room_word(project, level.id)
    assert finish_prompt(project, None, level.id) == prompt_words(room, intent, None)


def test_scene_is_read_next_to_the_render(tmp_path: Path) -> None:
    project = _stone_attic()
    level = only_level(project)
    assert load_scene_words(project, tmp_path, level.id) is None, "pas de bake.json : rien"
    bake = bake_level(project, level.id, style="realistic")
    (tmp_path / "bake.json").write_text(bake.model_dump_json(), encoding="utf-8")
    words = load_scene_words(project, tmp_path, level.id)
    assert words is not None and any("stone" in s for s in words.surfaces)
    (tmp_path / "bake.json").write_text("{abîmé", encoding="utf-8")
    assert load_scene_words(project, tmp_path, level.id) is None


@pytest.mark.parametrize("engine", ["sd15", "zimage"])
def test_prompt_is_ascii_english_without_doubled_commas(engine: PromptEngine) -> None:
    project = _stone_attic()
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    prompt = finish_prompt(
        project, None, level.id, scene=scene_words(bake, project, level.id), engine=engine
    )
    assert ",," not in prompt and ", ," not in prompt and prompt.isascii()
