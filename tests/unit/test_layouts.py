"""Générateur de dispositions (3.4) : variantes valides, distinctes, scorées, appliquées."""

from __future__ import annotations

import time

import pytest

from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.layouts import (
    STEP,
    LayoutBody,
    apply_layout,
    build_context,
    expand_brief,
    generate_layouts,
)
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import AddFurniture, UpdateElement, apply_op
from openplan.schemas import Furniture, Source
from openplan.schemas.common import Point2
from openplan.schemas.layout import BriefItem, ItemDimensions, LayoutBrief
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import check_layout
from tests.factories import attic_project, only_level, rect_room_project

BEDROOM = LayoutBrief(
    room_use="bedroom_office",
    items=[
        BriefItem(category="bed_double", near=["wall"], away_from=["door_swing"]),
        BriefItem(category="desk", near=["window"]),
        BriefItem(category="wardrobe", priority="nice_to_have"),
        BriefItem(category="nightstand", count=2, priority="nice_to_have"),
    ],
)


def _walls_distance(project, furniture: Furniture) -> float:  # type: ignore[no-untyped-def]
    from openplan.geom.wallgraph import WallGraph
    from openplan.validation.layout import pieces_of

    level = only_level(project)
    piece = next(p for p in pieces_of(level) if p.element.id == furniture.id)
    return float(piece.polygon.distance(WallGraph.from_level(level).walls_union()))


def test_three_valid_distinct_variants_in_under_five_seconds() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    started = time.perf_counter()
    run = generate_layouts(project, level_id, LayoutBody(brief=BEDROOM, variants=3, seed=1))
    elapsed = time.perf_counter() - started
    assert run.result.step == STEP and elapsed < 5.0, elapsed
    assert len(run.proposals) == 3, run.result.warnings
    for proposal in run.proposals:
        assert proposal.valid and proposal.summary.startswith(("Très bien", "Correct", "À revoir"))
        assert "meubles placés" in proposal.summary
        categories = [f.category for f in proposal.furniture]
        assert "bed_double" in categories and "desk" in categories, proposal.unplaced
        assert all(f.source is Source.GENERATED and f.confidence == 0.6 for f in proposal.furniture)
        assert all(f.provenance.step == STEP for f in proposal.furniture)
        # Propriété : aucune violation dure une fois les meubles dans la scène.
        trial = _apply(project, level_id, proposal)
        assert not [v for v in check_layout(trial) if v.severity == "high"]
        bed = next(f for f in proposal.furniture if f.category == "bed_double")
        assert _walls_distance(trial, bed) < 0.05, "le lit est dos à un mur"
    scores = [p.score for p in run.proposals]
    assert scores == sorted(scores, reverse=True)
    # Distinctes : un meuble indispensable bouge d'au moins 0,5 m entre deux variantes.
    a, b = run.proposals[0], run.proposals[1]
    pa = {f.category: f.transform.position for f in a.furniture}
    pb = {f.category: f.transform.position for f in b.furniture}
    assert any(
        abs(pa[c].x - pb[c].x) + abs(pa[c].y - pb[c].y) >= 0.5 for c in ("bed_double", "desk")
    )


def _apply(project, level_id, proposal):  # type: ignore[no-untyped-def]
    return apply_layout(project, level_id, proposal)


def test_generation_is_deterministic_for_a_seed() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    first = generate_layouts(project, level_id, LayoutBody(brief=BEDROOM, variants=2, seed=7))
    second = generate_layouts(project, level_id, LayoutBody(brief=BEDROOM, variants=2, seed=7))
    positions = lambda run: [  # noqa: E731
        (f.category, round(f.transform.position.x, 3), round(f.transform.position.y, 3))
        for p in run.proposals
        for f in p.furniture
    ]
    assert positions(first) == positions(second)


def test_low_storage_goes_against_the_eave_wall_of_the_attic() -> None:
    project = attic_project()
    level_id = only_level(project).id
    brief = LayoutBrief(
        items=[
            BriefItem(category="low_storage", near=["low_wall"]),
            BriefItem(category="bed_single"),
            BriefItem(category="desk", near=["ridge"]),
        ]
    )
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=2, seed=3))
    assert run.proposals, run.result.warnings
    best = run.proposals[0]
    low = next(f for f in best.furniture if f.category == "low_storage")
    # Les murs d'égout des combles de test sont à y = 0 et y = 5 : le rangement est contre l'un.
    assert min(low.transform.position.y, 5.0 - low.transform.position.y) < 0.5
    assert not [v for v in check_layout(_apply(project, level_id, best)) if v.severity == "high"]
    ctx = build_context(project, level_id, None)
    assert any(f.eave for f in ctx.faces) and ctx.landmarks.low_zone is not None


def test_brief_dimensions_are_kept_or_brought_back_to_the_catalog() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    ctx = build_context(project, level_id, None)
    brief = LayoutBrief(
        items=[
            BriefItem(category="bed_double", dimensions_m=ItemDimensions(width=1.8)),
            BriefItem(category="desk", dimensions_m=ItemDimensions(width=3.0)),
            BriefItem(category="piano"),
        ]
    )
    wants, unplaced = expand_brief(brief, ctx)
    assert [w.width for w in wants] == [1.8, 1.4]
    assert any("hors de la plage" in n for n in wants[1].notes)
    assert unplaced and unplaced[0].category == "piano" and "catalogue" in unplaced[0].reason
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=1))
    assert any("hors de la plage" in w for w in run.result.warnings)
    assert run.proposals and any(u.category == "piano" for u in run.proposals[0].unplaced)
    assert "non placé" in run.proposals[0].summary


def test_too_small_room_names_what_did_not_fit() -> None:
    project = rect_room_project(width=2.0, depth=1.5)
    level_id = only_level(project).id
    brief = LayoutBrief(
        items=[
            BriefItem(category="bed_double"),
            BriefItem(category="wardrobe", count=3),
            BriefItem(category="sofa"),
        ]
    )
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=2, seed=0))
    if run.proposals:
        assert run.proposals[0].unplaced, "tout ne peut pas tenir dans 2 × 1,5 m"
        assert "aucune position" in run.proposals[0].unplaced[0].reason
    else:
        assert any("trop petite" in w for w in run.result.warnings)


def test_existing_furniture_is_respected_and_apply_is_a_user_gesture() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        # Dos au mur de gauche (x = 0,075), hors du débattement de la porte (y ≤ 0,9).
        AddFurniture(asset_id="wardrobe_150", position=Point2(x=0.4, y=2.1), yaw=-1.5708),
    )
    wardrobe = next(e for e in only_level(project).elements.values() if isinstance(e, Furniture))
    project = apply_op(
        project, level_id, UpdateElement(element_id=wardrobe.id, fields={"preserve": True})
    )
    run = generate_layouts(project, level_id, LayoutBody(brief=BEDROOM, variants=1, seed=2))
    assert run.proposals
    proposal = run.proposals[0]
    after = apply_layout(project, level_id, proposal)
    assert (
        after.history[-1].actor == USER_ACTOR
        and "Appliquer la variante 1" in after.history[-1].label
    )
    assert wardrobe.id in only_level(after).elements, "le meuble à conserver est toujours là"
    assert validate_invariants(after).valid
    generated = [
        e
        for e in only_level(after).elements.values()
        if isinstance(e, Furniture) and e.source is Source.GENERATED
    ]
    assert len(generated) == len(proposal.furniture)
    # Appliquer deux fois la même variante crée des meubles neufs (identifiants distincts).
    twice = apply_layout(after, level_id, proposal)
    assert len(only_level(twice).elements) == len(only_level(after).elements) + len(
        proposal.furniture
    )


def test_errors_are_explicit() -> None:
    from openplan.scene.factory import new_project

    empty = new_project("vide")
    with pytest.raises(DetectionError, match="aucune pièce fermée"):
        generate_layouts(empty, only_level(empty).id, LayoutBody(brief=BEDROOM))
    project = rect_room_project()
    with pytest.raises(DetectionError, match="pièce inconnue"):
        generate_layouts(
            project, only_level(project).id, LayoutBody(brief=BEDROOM, room_id=empty.id)
        )
