"""KAN-139 : dans le bake « realistic », le pignon des combles ne sort jamais en pierre d'office.
Il prend la matière des murs du projet (style ou intention, sinon les autres murs, sinon un
enduit neutre) ; la pierre ne reste que demandée (matière choisie sur le mur, ou dite dans la
consigne de l'utilisateur). Scène du bake seulement : aucun rendu."""

from __future__ import annotations

import pytest

from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import scene_words
from openplan.pipeline.style import STYLE_SURFACES
from openplan.scene.ops import SetIntent, UpdateElement, apply_op
from openplan.schemas.elements import Room, Wall
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level

STONES = {"stone", "stone_rubble"}


def _room(project: Project) -> Room:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Room))


def _gables(project: Project) -> list[Wall]:
    level = only_level(project)
    return [e for e in level.elements.values() if isinstance(e, Wall) and e.name == "Pignon"]


def _gable_finishes(project: Project) -> set[str]:
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    ids = {g.id for g in _gables(project)}
    return {p.finish for p in [*bake.prisms, *bake.polyhedra] if p.element_id in ids}


def _intent(project: Project, *, actor: str = "user", **fields: object) -> Project:
    return apply_op(
        project,
        only_level(project).id,
        SetIntent(room_id=_room(project).id, fields=fields),
        actor=actor,
    )


@pytest.mark.parametrize("style", sorted(STYLE_SURFACES))
def test_every_style_gives_the_gables_the_walls_finish(style: str) -> None:
    project = _intent(attic_project(), style=style)
    finishes = _gable_finishes(project)
    assert finishes == {STYLE_SURFACES[style]["walls"]}, (style, finishes)
    assert not finishes & STONES


def test_without_intent_an_old_stone_gable_takes_the_other_walls() -> None:
    """Projet d'avant KAN-139 : la pierre posée d'office (`metadata.finish`) sur ses pignons ne
    compte plus ; ils prennent l'enduit de ses jambettes."""
    project = attic_project()
    level = only_level(project)
    elements = {
        e.id: e.model_copy(update={"metadata": {"finish": "stone"}}) if e in _gables(project) else e
        for e in level.elements.values()
    }
    old = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    assert _gable_finishes(old) == {"plaster"}


def test_no_other_wall_to_follow_gives_a_neutral_plaster() -> None:
    project = attic_project()
    level = only_level(project)
    elements = {
        e.id: e.model_copy(update={"metadata": {}}) if isinstance(e, Wall) else e
        for e in level.elements.values()
    }
    bare = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    assert _gable_finishes(bare) == {"plaster"}


def test_the_walls_wanted_by_the_intent_go_on_the_gables() -> None:
    project = _intent(attic_project(), style="naturel", materials={"walls": "limewash"})
    assert _gable_finishes(project) == {"limewash"}


def test_stone_stays_when_the_user_asks_for_it() -> None:
    # « pierre apparente » dans la consigne : `materials` choisi par l'utilisateur.
    asked = _intent(attic_project(), style="naturel", materials={"gables": "stone"})
    assert _gable_finishes(asked) == {"stone"}
    # La même pierre proposée par une étape automatique n'est pas reprise.
    guessed = _intent(
        attic_project(), actor="pipeline:autopilot", style="naturel", materials={"gables": "stone"}
    )
    assert _gable_finishes(guessed) == {"plaster"}
    # Matière choisie à la main sur le mur : elle reste, quel que soit le style.
    project = _intent(attic_project(), style="industriel")
    gable = _gables(project)[0]
    chosen = apply_op(
        project,
        only_level(project).id,
        UpdateElement(
            element_id=gable.id,
            fields={"metadata": {"finish": "stone_rubble", "finish_source": "user_defined"}},
        ),
    )
    bake = bake_level(chosen, only_level(chosen).id, style="realistic")
    assert {p.finish for p in [*bake.prisms, *bake.polyhedra] if p.element_id == gable.id} == {
        "stone_rubble"
    }


def test_a_new_attic_has_no_stone_on_its_gables() -> None:
    project = attic_project()
    assert all("finish" not in g.metadata for g in _gables(project))


def test_the_render_prompt_of_a_natural_attic_no_longer_says_stone() -> None:
    project = _intent(attic_project(), style="naturel")
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    words = scene_words(bake, project, level.id)
    assert not any("stone" in s for s in words.surfaces), words.surfaces
