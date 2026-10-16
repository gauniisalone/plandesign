"""Re-rendre des vues périmées (P2) : chaque vue est refaite comme son dernier rendu (qualité,
périphérique, finition et image d'inspiration s'il en avait), pour que l'image change avec le
plan et pas avec les réglages. La lumière d'une vue automatique suit l'ambiance du moment
(comme le chemin automatique) ; celle d'un point de vue posé à la main reste celle de son
dernier rendu. Sans rendu précédent, la vue est faite comme par le chemin automatique."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args
from uuid import UUID

from openplan.adapters.protocols import AdapterUnavailableError
from openplan.pipeline.autopilot import AUTO_FINISH_STRENGTH, LIGHT_TO_DAYLIGHT
from openplan.pipeline.blender_bridge import new_run_dir
from openplan.pipeline.finish import FinishOptions, finish_render
from openplan.pipeline.inspire import InspirationOptions, inspire_render
from openplan.pipeline.mainroom import main_room
from openplan.pipeline.render import Daylight, RenderEntry, render_camera_view
from openplan.pipeline.stale import latest_view_renders
from openplan.pipeline.views import AUTO_KEY
from openplan.scene.ops import OpError
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Camera
from openplan.schemas.scene import Project

DEFAULT_SAMPLES = 64
MAX_VIEWS = 12


@dataclass(frozen=True)
class ViewPlan:
    camera: Camera
    samples: int
    gpu: bool
    daylight: Daylight | None
    finish: FinishOptions | None
    inspiration: InspirationOptions | None


def _as_daylight(value: str | None) -> Daylight | None:
    """L'éclairage écrit dans `render.json` (None = éclairage d'atelier neutre)."""
    for known in get_args(Daylight):
        if value == known:
            return cast(Daylight, known)
    return None


def _ambiance_daylight(project: Project, level_id: UUID) -> Daylight | None:
    room = main_room(project.levels[level_id])
    light = room.intent.light.time if room is not None and room.intent is not None else "midi"
    return LIGHT_TO_DAYLIGHT.get(light)


def plan_views(
    project: Project,
    level_id: UUID,
    camera_ids: Iterable[UUID],
    renders: Iterable[RenderEntry],
    *,
    auto_finish: bool,
    auto_inspiration: bool,
) -> list[ViewPlan]:
    """Ce qui sera refait pour chaque vue demandée, sans rien lancer (testable sans Blender)."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    latest = latest_view_renders(renders)
    plans: list[ViewPlan] = []
    for camera_id in dict.fromkeys(camera_ids):
        camera = level.elements.get(camera_id)
        if not isinstance(camera, Camera):
            msg = f"point de vue inconnu : {camera_id}"
            raise OpError(msg)
        last = latest.get(camera_id)
        auto = camera.metadata.get(AUTO_KEY) is not None
        if last is None:
            plans.append(
                ViewPlan(
                    camera=camera,
                    samples=DEFAULT_SAMPLES,
                    gpu=True,
                    daylight=_ambiance_daylight(project, level_id),
                    finish=FinishOptions(strength=AUTO_FINISH_STRENGTH) if auto_finish else None,
                    inspiration=InspirationOptions() if auto_inspiration else None,
                )
            )
            continue
        daylight = _ambiance_daylight(project, level_id) if auto else _as_daylight(last.daylight)
        plans.append(
            ViewPlan(
                camera=camera,
                samples=last.samples,
                gpu=last.device != "cpu",
                daylight=daylight,
                # Le style de la finition suit l'ambiance du moment (style=None).
                finish=FinishOptions(strength=last.finished.strength, seed=last.finished.seed)
                if last.finished is not None
                else None,
                inspiration=InspirationOptions(
                    mode=last.inspiration.mode,
                    control_scale=last.inspiration.control_scale,
                    seed=last.inspiration.seed,
                    **(
                        {"strength": last.inspiration.strength}
                        if last.inspiration.strength is not None
                        else {}
                    ),
                )
                if last.inspiration is not None
                else None,
            )
        )
    if not plans:
        msg = "aucune vue à re-rendre"
        raise OpError(msg)
    if len(plans) > MAX_VIEWS:
        msg = f"au plus {MAX_VIEWS} vues à la fois"
        raise OpError(msg)
    return plans


class RerenderRun(StrictModel):
    run_ids: list[UUID]
    finished: list[UUID]
    inspired: list[UUID]
    failed: list[str]
    """« Finition Vue d'ensemble : … » : ce qui n'a pas pu être refait (le rendu, lui, reste)."""
    seconds: float


def rerender(
    project: Project,
    level_id: UUID,
    plans: list[ViewPlan],
    runs_dir: Path,
    *,
    blender_bin: str | None,
    stage: Callable[[str], None],
    finisher: Callable[[], object] | None,
) -> RerenderRun:
    """Rendus Blender d'abord (chacun rejoint la galerie dès qu'il est fini), puis finitions et
    images d'inspiration avec le modèle d'images chargé une seule fois."""
    t0 = time.perf_counter()
    done: list[tuple[ViewPlan, UUID, Path]] = []
    failed: list[str] = []
    for i, plan in enumerate(plans, start=1):
        name = plan.camera.name or "point de vue"
        stage(f"Vue {i}/{len(plans)} : {name}")
        run_id, out_dir = new_run_dir(runs_dir)
        render_camera_view(
            project,
            level_id,
            plan.camera.id,
            out_dir,
            blender_bin=blender_bin,
            samples=plan.samples,
            gpu=plan.gpu,
            run_id=run_id,
            daylight=plan.daylight,
        )
        done.append((plan, run_id, out_dir))
    finished: list[UUID] = []
    inspired: list[UUID] = []
    wanted = [d for d in done if d[0].finish is not None or d[0].inspiration is not None]
    model: object | None = None
    if wanted and finisher is not None:
        try:
            model = finisher()
        except AdapterUnavailableError as exc:
            failed.append(f"Finition et inspiration : modèle d'images absent ({exc})"[:200])
    if model is not None:
        for plan, run_id, out_dir in wanted:
            name = plan.camera.name or "point de vue"
            if plan.finish is not None:
                stage(f"Finition : {name}")
                try:
                    finish_render(project, out_dir, run_id, plan.finish, finisher=model)  # type: ignore[arg-type]
                    finished.append(run_id)
                except Exception as exc:
                    failed.append(f"Finition {name} : {exc}"[:200])
            if plan.inspiration is not None and hasattr(model, "inspire"):
                stage(f"Inspiration : {name}")
                try:
                    inspire_render(project, out_dir, run_id, plan.inspiration, inspirer=model)  # type: ignore[arg-type]
                    inspired.append(run_id)
                except Exception as exc:
                    failed.append(f"Inspiration {name} : {exc}"[:200])
    return RerenderRun(
        run_ids=[run_id for _, run_id, _ in done],
        finished=finished,
        inspired=inspired,
        failed=failed,
        seconds=round(time.perf_counter() - t0, 1),
    )
