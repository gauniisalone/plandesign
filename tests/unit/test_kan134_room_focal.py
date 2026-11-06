"""KAN-134 : la focale des « Réglages des rendus », passée à « Proposer / Recalculer les vues ».
Elle ne touche que les vues recalculées (jamais une vue modifiée à la main) ; « Automatique »
(aucune focale) donne exactement le calcul d'avant ; hors de 24 à 35 mm, elle est refusée.
Plans de test seulement."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.pipeline.generated import generate_plan
from openplan.pipeline.programme import read_programme
from openplan.pipeline.room_views import apply_room_views, is_room_view, plan_room_views
from openplan.pipeline.views import focal_px
from openplan.scene.factory import new_project
from openplan.scene.ops import UpdateElement, apply_op
from openplan.schemas.common import Source
from openplan.schemas.elements import Camera
from openplan.schemas.scene import Project
from tests.factories import only_level

T3 = "Un appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau"


def _plan() -> Project:
    reading = read_programme(T3, None)
    assert reading.programme is not None
    project = new_project("Essai")
    project, _ = generate_plan(project, only_level(project).id, reading.programme)
    return project


def _room_views(project: Project) -> list[Camera]:
    return sorted(
        (e for e in only_level(project).elements.values() if is_room_view(e)),
        key=lambda c: c.name or "",
    )  # type: ignore[return-value]


def _mm(cam: Camera) -> float:
    assert cam.focal_px is not None
    return cam.focal_px * 36.0 / 1280.0


def test_automatic_is_exactly_todays_computation() -> None:
    level = only_level(_plan())
    assert plan_room_views(level, None) == plan_room_views(level)


def test_the_wanted_focal_reaches_every_recomputed_view_and_its_note() -> None:
    project = _plan()
    level_id = only_level(project).id
    project, _ = apply_room_views(project, level_id, actor="user", focal_mm=33)
    views = _room_views(project)
    assert views
    for cam in views:
        assert _mm(cam) == pytest.approx(33.0, abs=0.05)
        assert cam.source is Source.GENERATED, "la vue reste calculée"
        note = str(cam.metadata["note"])
        assert "33 mm" in note and "% du sol visible" in note, "la note du calcul reste"


def test_a_view_set_by_hand_keeps_its_own_focal() -> None:
    project = _plan()
    level_id = only_level(project).id
    project, _ = apply_room_views(project, level_id, actor="user")
    mine = _room_views(project)[0]
    project = apply_op(
        project, level_id, UpdateElement(element_id=mine.id, fields={"focal_px": focal_px(30.0)})
    )
    project, report = apply_room_views(project, level_id, actor="user", focal_mm=24)
    after = {c.id: c for c in _room_views(project)}
    assert _mm(after[mine.id]) == pytest.approx(30.0, abs=0.05)
    assert after[mine.id].source is Source.USER_DEFINED
    others = [c for cid, c in after.items() if cid != mine.id]
    assert others and all(_mm(c) == pytest.approx(24.0, abs=0.05) for c in others)
    assert any("modifiée à la main" in k for k in report.kept)


@pytest.mark.parametrize("mm", [23, 36, 0])
def test_a_focal_out_of_bounds_is_refused(mm: int) -> None:
    project = _plan()
    with pytest.raises(ValueError, match="hors bornes"):
        apply_room_views(project, only_level(project).id, actor="user", focal_mm=mm)


def test_the_route_takes_the_focal_and_refuses_out_of_bounds(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openplan.api.app import create_app
    from openplan.api.settings import Settings
    from openplan.scene.store import ProjectStore

    store = ProjectStore(tmp_path / "data")
    project = _plan()
    store.save(project)
    level = only_level(project)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    base = f"/api/projects/{project.id}/levels/{level.id}/views/rooms"
    with TestClient(app) as client:
        for mm in (23, 36):
            refused = client.post(base, json={"focal_mm": mm})
            assert refused.status_code == 422, refused.text
        assert client.post(base, json={}).status_code == 200
        r = client.post(base, json={"focal_mm": 35})
        assert r.status_code == 200, r.text
        cams = [
            e
            for e in r.json()["project"]["levels"][str(level.id)]["elements"].values()
            if e["type"] == "camera" and "room_view" in (e.get("metadata") or {})
        ]
        assert cams and all(c["focal_px"] == pytest.approx(focal_px(35.0), abs=0.1) for c in cams)
