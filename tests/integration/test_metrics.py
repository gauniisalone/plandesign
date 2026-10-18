"""P0 : mesure du parcours. Gestes d'interface au journal local (vocabulaire fermé, jamais
envoyé), indicateurs calculés sur la machine (temps jusqu'au premier rendu, gestes avant et
après, onglets, « Affiner », rendus demandés, vues périmées)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.cli.main import main
from openplan.pipeline import journal
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.metrics import project_metrics
from openplan.pipeline.render import RENDER_SIDECAR, RenderEntry
from openplan.scene.ops import AddCamera, apply_op
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Point2
from openplan.schemas.elements import Camera
from openplan.schemas.scene import HistoryEntry, Project
from tests.factories import attic_project, only_level, rect_room_project

T0 = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)


def _step(actor: str, seconds: float, label: str = "geste") -> HistoryEntry:
    # Un pas qui touche ce qu'on voit hors d'un plan précis (une calibration) : il périme
    # toutes les vues (P2 ; T7e : un pas sur un seul plan ne périme que les vues de ce plan).
    touch = [{"op": "replace", "path": "/calibrations/0", "value": 2.5}]
    return HistoryEntry(
        actor=actor,
        label=label,
        patch=touch,
        inverse_patch=touch,
        timestamp=T0 + timedelta(seconds=seconds),
    )


def _render(
    store: ProjectStore, project: Project, camera: UUID, seconds: float, variant: int | None = None
) -> None:
    folder = store.runs_dir(project.id) / str(uuid4()) / "blender"
    folder.mkdir(parents=True)
    (folder / "preview.png").write_bytes(b"png")
    entry = RenderEntry(
        run_id=uuid4(),
        camera_id=camera,
        camera_name="Vue",
        samples=8,
        device="cpu",
        render_s=5.0,
        width_px=64,
        height_px=48,
        created_at=T0 + timedelta(seconds=seconds),
        variant=variant,
    )
    (folder / RENDER_SIDECAR).write_text(entry.model_dump_json())


@pytest.fixture
def scene(tmp_path: Path) -> tuple[ProjectStore, Project, TestClient]:
    store = ProjectStore(tmp_path / "data")
    history = [
        _step("user", 2, "Modifier les réglages"),  # l'accueil, au dépôt : pas un geste
        _step("pipeline:autopilot", 3),
        _step("user", 60),  # avant le premier rendu
        _step("user", 200),  # après : les deux vues deviennent périmées
    ]
    base = attic_project()
    base = apply_op(base, only_level(base).id, AddCamera(position=Point2(x=1.0, y=1.0)))
    project = base.model_copy(
        update={"created_at": T0, "history": history, "history_cursor": len(history)}
    )
    store.save(project)
    # Deux vues réelles : seules les caméras du projet comptent (P2).
    a, b = (e.id for e in only_level(project).elements.values() if isinstance(e, Camera))
    _render(store, project, a, 50, variant=1)  # vignette de variante : ne compte pas
    _render(store, project, a, 100)
    _render(store, project, b, 110)
    _render(store, project, a, 300)  # vue A rendue de nouveau : seule B reste périmée
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    return store, project, TestClient(app)


def test_ui_events_and_metrics(scene: tuple[ProjectStore, Project, TestClient]) -> None:
    store, project, client = scene
    pid = str(project.id)
    for event, detail in (
        ("tab_changed", "edit>render"),
        ("tab_changed", "vue:2d>split"),
        ("refine_clicked", "arrivee"),
        ("render_requested", "one"),
    ):
        r = client.post(f"/api/projects/{pid}/journal/ui", json={"event": event, "detail": detail})
        assert r.status_code == 200 and r.json() == {"recorded": True}
    journal.record(
        store.project_dir(project.id),
        project,
        JournalEvent(project_id=project.id, kind="undo", note="Déplacer"),
    )
    m = client.get(f"/api/projects/{pid}/metrics").json()
    assert m["ttfr_s"] == 100.0
    assert m["gestures_before_first_render"] == 1
    assert m["gestures_after_first_render"] == 2  # le geste à 200 s et l'annulation
    assert (m["tab_changes"], m["refine_clicks"], m["render_requests"]) == (2, 1, 1)
    assert (m["renders"], m["rendered_views"], m["stale_views"]) == (3, 2, 1)
    assert m["journal_enabled"] is True


def test_generate_click_is_one_gesture_before_the_first_render(tmp_path: Path) -> None:
    """T4 : sans la bascule « générer au dépôt d'une photo », « Générer maintenant » est le
    geste du chemin ; avec elle, rien n'est compté (0 geste)."""
    store = ProjectStore(tmp_path / "data")
    project = rect_room_project(4.0, 3.0)
    store.save(project)
    client = TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))
    pid = str(project.id)
    m = client.get(f"/api/projects/{pid}/metrics").json()
    assert m["gestures_before_first_render"] == 0 and m["generate_clicks"] == 0
    r = client.post(
        f"/api/projects/{pid}/journal/ui", json={"event": "generate_clicked", "detail": "now"}
    )
    assert r.status_code == 200 and r.json() == {"recorded": True}
    m = client.get(f"/api/projects/{pid}/metrics").json()
    assert m["gestures_before_first_render"] == 1 and m["generate_clicks"] == 1
    # La bascule est un réglage du projet, activé par défaut.
    assert client.get(f"/api/projects/{pid}").json()["render"]["auto_on_drop"] is True


def test_b3_space_moves_are_split(scene: tuple[ProjectStore, Project, TestClient]) -> None:
    """B3 (plus d'onglets) : allers-retours résultats ↔ éditeur, entrées dans le plan et
    changements de vue du plan sont comptés à part ; le total reste `tab_changes`."""
    _, project, client = scene
    pid = str(project.id)
    for detail in (
        "renders>board",
        "board>plan",
        "vue:2d>3d",
        "3d>board",
        "board>renders",
        "renders>plan",
    ):
        client.post(
            f"/api/projects/{pid}/journal/ui", json={"event": "tab_changed", "detail": detail}
        )
    m = client.get(f"/api/projects/{pid}/metrics").json()
    assert m["tab_changes"] == 6
    assert (m["results_editor_trips"], m["plan_entries"], m["view_changes"]) == (2, 2, 1)


def test_t7d2_viewer_is_counted_apart(scene: tuple[ProjectStore, Project, TestClient]) -> None:
    """T7d2 : ouvrir la visionneuse des rendus n'est ni un geste ni un passage d'espace ; la
    mesure le compte à part (`viewer_opens`)."""
    _, project, client = scene
    pid = str(project.id)
    before = client.get(f"/api/projects/{pid}/metrics").json()
    for detail in ("premiere", "3f2a6b1c-0d4e-4f5a-8b6c-7d8e9f0a1b2c"):
        r = client.post(
            f"/api/projects/{pid}/journal/ui", json={"event": "viewer_opened", "detail": detail}
        )
        assert r.status_code == 200 and r.json() == {"recorded": True}
    m = client.get(f"/api/projects/{pid}/metrics").json()
    assert m["viewer_opens"] == 2
    for key in ("gestures_before_first_render", "tab_changes", "results_editor_trips"):
        assert m[key] == before[key], key


@pytest.mark.parametrize(
    "body",
    [
        {"event": "tab_changed", "detail": "/home/user/photo.jpg"},
        {"event": "tab_changed", "detail": "Texte libre"},
        {"event": "tab_changed", "detail": "x" * 41},
        {"event": "clic_quelconque"},
        {"event": "tab_changed", "detail": "a", "x": 1.0},
    ],
)
def test_ui_event_vocabulary_is_closed(
    scene: tuple[ProjectStore, Project, TestClient], body: dict[str, object]
) -> None:
    _, project, client = scene
    r = client.post(f"/api/projects/{project.id}/journal/ui", json=body)
    assert r.status_code == 422


def test_ui_events_respect_the_journal_switch(
    scene: tuple[ProjectStore, Project, TestClient],
) -> None:
    store, project, client = scene
    off = project.model_copy(
        update={"privacy": project.privacy.model_copy(update={"design_journal": False})}
    )
    store.save(off)
    r = client.post(f"/api/projects/{project.id}/journal/ui", json={"event": "tab_changed"})
    assert r.json() == {"recorded": False}
    assert journal.read(store.project_dir(project.id)) == []
    m = client.get(f"/api/projects/{project.id}/metrics").json()
    assert m["journal_enabled"] is False and m["tab_changes"] == 0


def test_the_deposit_lasts_until_the_automatic_path(tmp_path: Path) -> None:
    """Photo déposée : le plan construit (au nom de l'utilisateur, après 15 s d'analyse) fait
    partie du dépôt ; un geste pendant les rendus, lui, compte."""
    store = ProjectStore(tmp_path / "data")
    history = [
        _step("user", 1, "Modifier les réglages"),
        _step("user", 15, "Créer les combles estimés"),
        _step("pipeline:autopilot", 16, "Vues automatiques (3)"),
        _step("user", 25, "Déplacer un meuble"),
    ]
    project = attic_project().model_copy(
        update={"created_at": T0, "history": history, "history_cursor": len(history)}
    )
    store.save(project)
    _render(store, project, uuid4(), 40)
    m = project_metrics(project, store.project_dir(project.id), store.runs_dir(project.id))
    assert m.gestures_before_first_render == 1 and m.gestures_after_first_render == 0
    zero = project.model_copy(update={"history": history[:3], "history_cursor": 3})
    m = project_metrics(zero, store.project_dir(project.id), store.runs_dir(project.id))
    assert m.gestures_before_first_render == 0


def test_metrics_without_render(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = attic_project()
    store.save(project)
    client = TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))
    m = client.get(f"/api/projects/{project.id}/metrics").json()
    assert m["ttfr_s"] is None and m["first_render_at"] is None
    assert m["renders"] == 0 and m["stale_views"] == 0


def test_cli_metrics_all(
    scene: tuple[ProjectStore, Project, TestClient], capsys: pytest.CaptureFixture[str]
) -> None:
    store, project, _ = scene
    (store.root / "pas-un-projet").mkdir()
    assert main(["journal", "metrics", "--all", "--data-dir", str(store.root)]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["project_id"] for line in lines] == [str(project.id)]
    assert lines[0]["ttfr_s"] == 100.0


def test_rerender_route_refuses_unknown_or_empty(
    scene: tuple[ProjectStore, Project, TestClient],
) -> None:
    """P2 : « Re-rendre les n vues » : un point de vue inconnu → 409, liste vide → 422 ; rien
    n'est lancé."""
    _, project, client = scene
    lid = only_level(project).id
    url = f"/api/projects/{project.id}/levels/{lid}/render/views"
    r = client.post(url, json={"camera_ids": [str(uuid4())]})
    assert r.status_code == 409 and "point de vue inconnu" in r.json()["detail"]
    assert client.post(url, json={"camera_ids": []}).status_code == 422


def test_picks_are_journaled_and_counted(scene: tuple[ProjectStore, Project, TestClient]) -> None:
    """P4c : chaque clic sur un rendu est noté (sorte trouvée, jamais la position) ; /metrics
    en tire le taux de résolution et les clics sur du décor sans porteur."""
    import numpy as np

    from openplan.pipeline.pick import IDMAP_JSON, IDMAP_NPZ

    store, project, client = scene
    room = next(e for e in only_level(project).elements.values() if e.type == "room")
    run_id = uuid4()
    folder = store.runs_dir(project.id) / str(run_id) / "blender"
    folder.mkdir(parents=True)
    (folder / RENDER_SIDECAR).write_text("{}")
    index = np.zeros((2, 4), dtype=np.uint16)
    index[:, :2] = 1
    index[0, 2:] = 2
    np.savez_compressed(folder / IDMAP_NPZ, index=index)
    (folder / IDMAP_JSON).write_text(json.dumps({"ids": [str(room.id), "decor-0"]}))
    (folder / "bake.json").write_text(json.dumps({"decor": [{"kind": "floor_plant"}]}))
    url = f"/api/projects/{project.id}/renders/{run_id}/pick"
    for x, y in ((0.1, 0.1), (0.2, 0.9), (0.9, 0.1), (0.9, 0.9)):
        assert client.get(url, params={"x": x, "y": y}).status_code == 200
    events = [e for e in journal.read(store.project_dir(project.id)) if e.kind == "render_picked"]
    assert [e.note for e in events] == [
        "element:room",
        "element:room",
        "decor:floor_plant",
        "none:rien",
    ]
    assert events[0].element_ids == [room.id] and events[0].run_ids == [run_id]
    m = client.get(f"/api/projects/{project.id}/metrics").json()
    assert (m["picks"], m["picks_resolved"], m["picks_decor"], m["picks_none"]) == (4, 2, 1, 1)
    assert m["pick_resolution"] == 0.5
