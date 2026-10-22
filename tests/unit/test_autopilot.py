"""V1 (note 10, point 6) : chemin « zéro geste » après le plan : ambiance déduite,
aménagement par zones, trois vues, rendus, finition enchaînée (coupable), sans jamais
réécrire ce que l'utilisateur a choisi, et sans perdre ses gestes faits pendant le calcul."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pytest

from openplan.adapters.protocols import AdapterUnavailableError
from openplan.pipeline import autopilot as ap
from openplan.pipeline.autopilot import Workspace, run_autopilot
from openplan.pipeline.timings import Countdown, Timings
from openplan.pipeline.views import AUTO_KEY
from openplan.scene.ops import AddCamera, AddFurniture, SetIntent, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Camera, Furniture, Room
from openplan.schemas.scene import Project, RenderSettings
from tests.factories import attic_project, only_level


class _Store:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.saves = 0

    def load(self) -> Project:
        return self.project

    def save(self, project: Project) -> None:
        self.project = project
        self.saves += 1


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """Rendu Blender remplacé : écrit une image, garde la liste des caméras rendues."""
    rendered: list[UUID] = []

    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")
        rendered.append(camera_id)

    monkeypatch.setattr(ap, "render_camera_view", render)
    return rendered


def _run(
    store: _Store,
    tmp_path: Path,
    finisher: Any = None,
    stage: Any = None,
    timings: Timings | None = None,
) -> ap.AutopilotRun:
    level_id = only_level(store.project).id
    return run_autopilot(
        Workspace(store.load, store.save),
        level_id,
        tmp_path,
        stage=stage or (lambda *_, **__: None),
        blender_bin=None,
        device="cuda",
        finisher=finisher,
        timings=timings,
    )


def test_zero_gesture_path_fills_an_empty_attic(tmp_path: Path, fake_render: list[UUID]) -> None:
    store = _Store(attic_project(camera=False))
    stages: list[str] = []
    left: list[float] = []

    def stage(text: str, remaining: Any = None) -> None:
        stages.append(text)
        assert remaining is not None, "chaque étape annonce son temps restant"
        left.append(remaining())

    def unavailable() -> Any:
        raise AdapterUnavailableError("poids absents : openplan models pull sd15")

    timings = Timings(tmp_path / "timings.json")
    run = _run(store, tmp_path, finisher=unavailable, stage=stage, timings=timings)
    level = only_level(store.project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    assert room.intent is not None and room.intent.style == "naturel"
    assert not room.intent.chosen_by_user("style"), "déduite : l'utilisateur peut la changer"
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture)]
    assert len(furniture) >= 5, "aménagement par zones appliqué"
    cams = [e for e in level.elements.values() if isinstance(e, Camera) and AUTO_KEY in e.metadata]
    assert {c.metadata[AUTO_KEY] for c in cams} >= {"overall", "zone", "detail"}
    assert len(fake_render) == len(run.run_ids) == 3
    by_name = {s.name: s for s in run.steps}
    assert by_name["intent"].status == by_name["layout"].status == "done"
    assert by_name["finish"].status == "skipped" and "poids absents" in by_name["finish"].note
    # Progression : une étape à la fois, avec un temps restant jamais nul.
    assert stages[0].startswith("Étape 1/")
    assert any("rendu" in s for s in stages)
    assert all(t >= 3.0 for t in left)
    # Les durées réelles des rendus sont rangées pour les prochaines estimations.
    assert (tmp_path / "timings.json").is_file()
    assert Timings(tmp_path / "timings.json").estimate("render", 999.0) < 999.0
    # Chaque modification est un pas d'historique annulable, attribué au chemin automatique.
    labels = [h.label for h in store.project.history]
    assert any("variante" in label.lower() for label in labels)
    assert all(h.actor == ap.ACTOR for h in store.project.history[-3:])


def test_user_choices_are_never_rewritten(tmp_path: Path, fake_render: list[UUID]) -> None:
    project = attic_project(camera=False)
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    project = apply_op(
        project, level.id, SetIntent(room_id=room.id, fields={"style": "industriel"})
    )
    polygon_center = Point2(x=3.0, y=2.5)
    project = apply_op(
        project, level.id, AddFurniture(asset_id="armchair", position=polygon_center)
    )
    store = _Store(project)
    run = _run(store, tmp_path)
    level = only_level(store.project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    assert room.intent is not None and room.intent.style == "industriel"
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture)]
    assert len(furniture) == 1, "un meuble posé à la main : l'aménagement est sauté"
    steps = {s.name: s for s in run.steps}
    assert steps["intent"].status == "skipped" and steps["layout"].status == "skipped"


def test_user_gestures_during_the_run_are_kept(tmp_path: Path, fake_render: list[UUID]) -> None:
    store = _Store(attic_project(camera=False))
    level_id = only_level(store.project).id

    def stage(text: str, **_: Any) -> None:
        # Pendant le calcul, l'utilisateur pose un point de vue dans l'éditeur.
        if "trois vues" in text:
            store.project = apply_op(
                store.project, level_id, AddCamera(position=Point2(x=2.0, y=2.0), name="Mienne")
            )

    _run(store, tmp_path, stage=stage)
    names = [e.name for e in only_level(store.project).elements.values() if isinstance(e, Camera)]
    assert "Mienne" in names
    mine = next(
        e
        for e in only_level(store.project).elements.values()
        if isinstance(e, Camera) and e.name == "Mienne"
    )
    assert mine.source is Source.USER_DEFINED


def test_chained_finish_keeps_the_blender_image_and_can_be_switched_off(
    tmp_path: Path, fake_render: list[UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    finished: list[Path] = []

    def finish(
        project: Project, render_dir: Path, run_id: UUID, options: Any, *, finisher: Any
    ) -> None:
        assert options.style is None, "la finition suit l'ambiance"
        (render_dir / "finished.png").write_bytes(b"ia")
        finished.append(render_dir)

    monkeypatch.setattr(ap, "finish_render", finish)
    store = _Store(attic_project(camera=False))
    run = _run(store, tmp_path, finisher=object)
    assert len(run.finished) == 3
    for folder in finished:
        assert (folder / "preview.png").is_file() and (folder / "finished.png").is_file()

    off = attic_project(camera=False)
    off = off.model_copy(update={"render": RenderSettings(auto_finish=False)})
    store = _Store(off)
    finished.clear()
    run = _run(store, tmp_path / "off", finisher=object)
    assert not finished and not run.finished
    assert any(s.name == "finish" and "réglages" in s.note for s in run.steps)


class _FakeModel:
    """Finition et inspiration simulées (mêmes poids dans la vraie vie)."""

    def __init__(self) -> None:
        self.model_ids = ["fake"]
        self.inspired: list[Any] = []

    def inspire(self, request: Any) -> Any:
        from openplan.adapters.protocols import GeneratedImage

        self.inspired.append(request)
        return GeneratedImage(
            pixels=np.full(request.image.shape, 200, np.uint8),
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=0.5,
        )


def test_inspiration_b_is_chained_marked_and_keeps_a_and_blender(
    tmp_path: Path, fake_render: list[UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from openplan.pipeline.inspire import INSPIRATION, LABEL, read_inspiration

    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), (90, 60, 40)).save(out_dir / "preview.png")

    def finish(
        project: Project, render_dir: Path, run_id: UUID, options: Any, *, finisher: Any
    ) -> None:
        assert options.strength == pytest.approx(0.35), "A : force bloquée à 0,35"
        (render_dir / "finished.png").write_bytes(b"ia")

    monkeypatch.setattr(ap, "render_camera_view", render)
    monkeypatch.setattr(ap, "finish_render", finish)
    model = _FakeModel()
    store = _Store(attic_project(camera=False))
    run = _run(store, tmp_path, finisher=lambda: model)
    assert len(run.inspired) == 3 and len(model.inspired) == 3
    first = model.inspired[0]
    assert (first.mode, first.strength, first.control_scale) == ("img2img", 0.8, 1.2)
    for rid in run.inspired:
        folder = next(tmp_path.glob(f"{rid}/blender"))
        entry = read_inspiration(folder)
        assert entry is not None and entry.source.value == "generated" and entry.label == LABEL
        assert (folder / INSPIRATION).is_file()
        assert (folder / "preview.png").is_file() and (folder / "finished.png").is_file()
        stamped = np.asarray(Image.open(folder / INSPIRATION))
        assert (stamped != 200).any(), "le libellé est inscrit sur l'image"

    off = attic_project(camera=False)
    off = off.model_copy(update={"render": RenderSettings(auto_inspiration=False)})
    model = _FakeModel()
    run = _run(_Store(off), tmp_path / "off", finisher=lambda: model)
    assert not model.inspired and not run.inspired
    assert any(s.name == "inspiration" and "réglages" in s.note for s in run.steps)


def test_timings_keep_a_rolling_average_of_real_durations(tmp_path: Path) -> None:
    path = tmp_path / "local" / "timings.json"
    timings = Timings(path)
    assert timings.estimate("finish", 25.0) == 25.0, "sans mesure : la valeur du protocole"
    for seconds in (40.0, 30.0, 36.0, 36.0, 36.0, 36.0):
        timings.record("finish", seconds)
    # Les 5 dernières : 30, 36, 36, 36, 36 (le 40 du début est sorti de la fenêtre).
    assert timings.estimate("finish", 25.0) == pytest.approx(34.8)
    assert Timings(path).estimate("finish", 25.0) == pytest.approx(34.8), "relues"
    estimates = ap.step_estimates("cuda", finish=True, timings=timings)
    assert estimates[-1] == pytest.approx(34.8)


def test_countdown_never_shows_zero_while_a_step_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("openplan.pipeline.timings.time.perf_counter", lambda: clock[0])
    countdown = Countdown([10.0, 20.0])
    countdown.start(1)
    assert countdown.remaining() == pytest.approx(20.0)
    clock[0] += 18.0
    assert countdown.remaining() == pytest.approx(4.5), "presque fini : 3 s, ou un quart"
    clock[0] += 1.0
    assert countdown.remaining() == pytest.approx(4.75), "estimation tenue : jamais 0"
    clock[0] += 41.0
    assert countdown.remaining() == pytest.approx(15.0), "dépassement : un quart du temps pris"


def test_job_status_reports_the_live_remaining_time() -> None:
    import threading

    from pydantic import BaseModel

    from openplan.api.jobs import JobRegistry

    gate = threading.Event()

    class Done(BaseModel):
        ok: bool = True

    def work(stage: Any) -> Done:
        stage("Étape 1/1 : finition", remaining=lambda: 12.4)
        gate.wait(5)
        return Done()

    jobs = JobRegistry()
    job = jobs.submit_staged("autopilot", work, device="cuda", expected_s=30, message="…")
    for _ in range(50):
        status = jobs.get(job.id)
        if status.stage:
            break
        threading.Event().wait(0.02)
    assert status.remaining_s == 12.0
    gate.set()
    jobs.shutdown()


def test_settings_default_on_and_old_projects_load() -> None:
    from openplan.scene.migrate import load_project
    from openplan.schemas.common import SCHEMA_VERSION

    data = attic_project().model_dump(mode="json")
    data["schema_version"] = "0.5.0"
    data.pop("render")
    loaded = load_project(data)
    assert loaded.schema_version == SCHEMA_VERSION == "0.18.0"
    assert loaded.render.auto_path and loaded.render.auto_finish
    assert not math.isnan(ap.plan_estimate(loaded, only_level(loaded).id, "cuda", finish=True))
