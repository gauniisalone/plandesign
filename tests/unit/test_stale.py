"""P2 : une vue n'est périmée que si ce qu'on voit a changé depuis son dernier rendu ; un pas
qui ne touche que des caméras ne périme que leurs vues ; un réglage du projet, aucune. Puis
« Re-rendre » refait chaque vue comme son dernier rendu."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from openplan.pipeline.finish import FinishEntry
from openplan.pipeline.inspire import InspirationEntry
from openplan.pipeline.render import RenderEntry
from openplan.pipeline.rerender import DEFAULT_SAMPLES, plan_views
from openplan.pipeline.stale import stale_cameras, step_scope
from openplan.scene import history
from openplan.scene.ops import AddCamera, OpError, RemoveElement, SetIntent, UpdateElement, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Camera, Room
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level

EARLIER = datetime.now(UTC) - timedelta(hours=1)


def _render(
    camera: UUID,
    *,
    at: datetime = EARLIER,
    finished: bool = False,
    inspired: bool = False,
) -> RenderEntry:
    run_id = uuid4()
    return RenderEntry(
        run_id=run_id,
        camera_id=camera,
        camera_name="Vue",
        samples=16,
        device="cpu",
        render_s=4.0,
        width_px=64,
        height_px=48,
        created_at=at,
        daylight="soir",
        finished=FinishEntry(
            run_id=run_id,
            strength=0.4,
            style="naturel",
            prompt="p",
            seed=7,
            model_ids=["sd"],
            duration_s=1.0,
            guided_by=[],
            created_at=EARLIER,
        )
        if finished
        else None,
        inspiration=InspirationEntry(
            run_id=run_id,
            mode="img2img",
            strength=0.8,
            control_scale=0.9,
            prompt="p",
            seed=3,
            model_ids=["sd"],
            duration_s=1.0,
            created_at=EARLIER,
        )
        if inspired
        else None,
    )


def _two_views() -> tuple[Project, UUID, UUID, list[RenderEntry]]:
    project = attic_project()
    level = only_level(project)
    project = apply_op(project, level.id, AddCamera(position=Point2(x=1.0, y=1.0), name="B"))
    cams = [e.id for e in only_level(project).elements.values() if isinstance(e, Camera)]
    assert len(cams) == 2
    a, b = cams
    # Rendus faits juste après la fabrique (son dernier pas) : à jour au départ.
    at = project.history[project.history_cursor - 1].timestamp
    return project, a, b, [_render(a, at=at), _render(b, at=at)]


def test_moving_a_camera_only_stales_its_own_view() -> None:
    project, a, _b, renders = _two_views()
    moved = apply_op(
        project, only_level(project).id, UpdateElement(element_id=a, fields={"yaw": 0.7})
    )
    scope = step_scope(moved.history[-1], moved)
    assert not scope.visible and scope.cameras == {str(a)}
    assert stale_cameras(moved, renders) == {a}


def test_adding_or_removing_a_viewpoint_stales_nothing_else() -> None:
    project, _a, b, renders = _two_views()
    lid = only_level(project).id
    added = apply_op(project, lid, AddCamera(position=Point2(x=2.0, y=1.0), name="C"))
    assert stale_cameras(added, renders) == set()
    removed = apply_op(added, lid, RemoveElement(element_id=b))
    assert step_scope(removed.history[-1], removed).visible is False
    assert stale_cameras(removed, renders) == set()


def test_ambiance_or_geometry_stales_every_view() -> None:
    project, a, b, renders = _two_views()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    moody = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "chalet"}))
    assert step_scope(moody.history[-1], moody).visible
    assert stale_cameras(moody, renders) == {a, b}


def test_project_settings_stale_nothing() -> None:
    project, _a, _b, renders = _two_views()
    renamed = history.commit(
        project,
        project.model_copy(
            update={
                "name": "Autre nom",
                "render": project.render.model_copy(update={"auto_finish": False}),
            }
        ),
        actor="user",
        label="Modifier les réglages",
    )
    assert stale_cameras(renamed, renders) == set()


def test_a_render_after_the_change_is_up_to_date() -> None:
    project, a, b, renders = _two_views()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    moody = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "chalet"}))
    fresh = _render(a).model_copy(update={"created_at": datetime.now(UTC) + timedelta(seconds=1)})
    assert stale_cameras(moody, [*renders, fresh]) == {b}


def test_rerender_plan_repeats_the_last_render_and_follows_the_ambiance() -> None:
    project, a, b, _ = _two_views()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    project = apply_op(
        project, level.id, SetIntent(room_id=room.id, fields={"light": {"time": "heure_doree"}})
    )
    # A : vue automatique ; B : point de vue posé à la main.
    project = apply_op(
        project,
        level.id,
        UpdateElement(element_id=a, fields={"metadata": {"auto_view": "overall"}}),
    )
    renders = [_render(a, finished=True, inspired=True), _render(b)]
    plans = {
        p.camera.id: p
        for p in plan_views(
            project, level.id, [a, b, a], renders, auto_finish=True, auto_inspiration=True
        )
    }
    assert len(plans) == 2
    pa, pb = plans[a], plans[b]
    assert (pa.samples, pa.gpu) == (16, False)
    assert pa.daylight == "heure_doree", "vue automatique : la lumière de l'ambiance du moment"
    assert pb.daylight == "soir", "point de vue posé à la main : la lumière de son dernier rendu"
    assert pa.finish is not None and pa.finish.strength == 0.4 and pa.finish.style is None
    assert pa.inspiration is not None and pa.inspiration.seed == 3
    assert pb.finish is None and pb.inspiration is None


def test_rerender_plan_without_a_previous_render_and_errors() -> None:
    project, a, _b, _ = _two_views()
    lid = only_level(project).id
    (plan,) = plan_views(project, lid, [a], [], auto_finish=True, auto_inspiration=False)
    assert plan.samples == DEFAULT_SAMPLES and plan.finish is not None
    assert plan.inspiration is None
    with pytest.raises(OpError, match="point de vue inconnu"):
        plan_views(project, lid, [uuid4()], [], auto_finish=False, auto_inspiration=False)
    with pytest.raises(OpError, match="niveau inconnu"):
        plan_views(project, uuid4(), [a], [], auto_finish=False, auto_inspiration=False)
