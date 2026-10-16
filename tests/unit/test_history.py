"""Historique : patch et inverse, annuler/rétablir, politique de priorité des sources."""

from __future__ import annotations

import pytest

from openplan.scene import history
from openplan.scene.history import USER_ACTOR, HistoryError, PatchRejectedError
from openplan.schemas import Project, Source, Wall
from tests.factories import by_name, only_level

PIPELINE = "pipeline:test"


def _with_thickness(project: Project, wall_name: str, thickness: float) -> Project:
    """Copie du projet où le mur nommé a une autre épaisseur (édition typique)."""
    level = only_level(project)
    wall = by_name(level, wall_name, Wall)
    new_wall = wall.model_copy(update={"thickness": thickness})
    new_level = level.model_copy(update={"elements": {**level.elements, wall.id: new_wall}})
    return project.model_copy(update={"levels": {level.id: new_level}})


def _with_wall_update(project: Project, wall_name: str, **update: object) -> Project:
    level = only_level(project)
    wall = by_name(level, wall_name, Wall)
    new_wall = wall.model_copy(update=update)
    new_level = level.model_copy(update={"elements": {**level.elements, wall.id: new_wall}})
    return project.model_copy(update={"levels": {level.id: new_level}})


def test_diff_then_apply_reproduces_target(rect_project: Project) -> None:
    after = _with_thickness(rect_project, "W2", 0.30)
    patch = history.diff(rect_project, after)
    assert patch, "un changement réel doit produire un patch non vide"
    assert history.content(history.apply(rect_project, patch)) == history.content(after)


def test_patch_then_inverse_is_identity(rect_project: Project) -> None:
    after = _with_thickness(rect_project, "W2", 0.30)
    forward = history.diff(rect_project, after)
    backward = history.diff(after, rect_project)
    round_trip = history.apply(history.apply(rect_project, forward), backward)
    assert history.content(round_trip) == history.content(rect_project)


def test_commit_records_entry_and_advances_cursor(rect_project: Project) -> None:
    after = _with_thickness(rect_project, "W2", 0.30)
    committed = history.commit(rect_project, after, actor=USER_ACTOR, label="Épaisseur W2")
    assert len(committed.history) == 1
    assert committed.history_cursor == 1
    assert committed.history[0].label == "Épaisseur W2"
    assert committed.updated_at >= rect_project.updated_at
    assert by_name(only_level(committed), "W2", Wall).thickness == 0.30


def test_empty_change_creates_no_entry(rect_project: Project) -> None:
    same = rect_project.model_copy()
    assert history.commit(rect_project, same, actor=USER_ACTOR, label="rien") is rect_project


def test_undo_and_redo(rect_project: Project) -> None:
    step1 = history.commit(
        rect_project, _with_thickness(rect_project, "W2", 0.30), actor=USER_ACTOR, label="a"
    )
    step2 = history.commit(step1, _with_thickness(step1, "W3", 0.40), actor=USER_ACTOR, label="b")
    assert history.can_undo(step2) and not history.can_redo(step2)

    undone = history.undo(step2)
    assert undone.history_cursor == 1
    assert by_name(only_level(undone), "W3", Wall).thickness == 0.15
    assert by_name(only_level(undone), "W2", Wall).thickness == 0.30
    assert len(undone.history) == 2, "annuler ne supprime pas l'entrée, elle reste à rétablir"

    redone = history.redo(undone)
    assert redone.history_cursor == 2
    assert history.content(redone) == history.content(step2)


def test_new_commit_after_undo_drops_redo_branch(rect_project: Project) -> None:
    step1 = history.commit(
        rect_project, _with_thickness(rect_project, "W2", 0.30), actor=USER_ACTOR, label="a"
    )
    undone = history.undo(step1)
    step1b = history.commit(
        undone, _with_thickness(undone, "W2", 0.20), actor=USER_ACTOR, label="c"
    )
    assert [e.label for e in step1b.history] == ["c"]
    assert not history.can_redo(step1b)


def test_undo_without_history_raises(rect_project: Project) -> None:
    with pytest.raises(HistoryError):
        history.undo(rect_project)
    with pytest.raises(HistoryError):
        history.redo(rect_project)


def test_pipeline_may_edit_estimated_elements(rect_project: Project) -> None:
    after = _with_thickness(rect_project, "W2", 0.30)  # W2 est estimated
    committed = history.commit(rect_project, after, actor=PIPELINE, label="ok")
    assert committed.history[0].actor == PIPELINE


def test_pipeline_cannot_lower_a_measured_source(rect_project: Project) -> None:
    after = _with_wall_update(rect_project, "W1", source=Source.ESTIMATED, confidence=0.5)
    with pytest.raises(PatchRejectedError, match="priorité abaissée"):
        history.commit(rect_project, after, actor=PIPELINE, label="non")


def test_pipeline_cannot_touch_locked_elements(rect_project: Project) -> None:
    locked = _with_wall_update(rect_project, "W2", locked=True)
    after = _with_thickness(locked, "W2", 0.30)
    with pytest.raises(PatchRejectedError, match="verrouillé"):
        history.commit(locked, after, actor=PIPELINE, label="non")
    # L'utilisateur, lui, peut.
    history.commit(locked, after, actor=USER_ACTOR, label="oui")


def test_pipeline_cannot_delete_measured_elements(rect_project: Project) -> None:
    level = only_level(rect_project)
    w1 = by_name(level, "W1", Wall)
    elements = {k: v for k, v in level.elements.items() if k != w1.id}
    after = rect_project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    with pytest.raises(PatchRejectedError, match="supprimer"):
        history.commit(rect_project, after, actor=PIPELINE, label="non")


def test_pipeline_cannot_create_measured_elements(rect_project: Project) -> None:
    level = only_level(rect_project)
    w2 = by_name(level, "W2", Wall)
    fake = Wall(
        start_vertex=w2.start_vertex,
        end_vertex=w2.end_vertex,
        source=Source.MEASURED,
        confidence=1.0,
    )
    elements = {**level.elements, fake.id: fake}
    after = rect_project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    with pytest.raises(PatchRejectedError, match="créer"):
        history.commit(rect_project, after, actor=PIPELINE, label="non")


def test_pipeline_cannot_modify_measurements(rect_project: Project) -> None:
    after = rect_project.model_copy(update={"measurements": {}})
    with pytest.raises(PatchRejectedError, match="mesures"):
        history.commit(rect_project, after, actor=PIPELINE, label="non")


def test_user_can_lower_a_source(rect_project: Project) -> None:
    after = _with_wall_update(rect_project, "W1", source=Source.ESTIMATED, confidence=0.5)
    committed = history.commit(rect_project, after, actor=USER_ACTOR, label="oui")
    assert by_name(only_level(committed), "W1", Wall).source is Source.ESTIMATED
