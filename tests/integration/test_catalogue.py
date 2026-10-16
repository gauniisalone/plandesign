"""V1.7b : « Version catalogue » d'une vue, à la demande (txt2img 1,2, « matières libres »),
à côté des autres images ; bandeau d'inspiration lisible (au moins 1/25 de la hauteur)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from openplan.adapters.protocols import GeneratedImage
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.inspire import CATALOGUE, CATALOGUE_LABEL, STAMP_SHARE, stamp
from openplan.pipeline.render import RENDER_SIDECAR, RenderEntry
from openplan.scene.store import ProjectStore
from tests.factories import attic_project


class _Model:
    def __init__(self) -> None:
        self.model_ids = ["fake"]
        self.requests: list[Any] = []

    def finish(self, request: Any) -> GeneratedImage:  # pragma: no cover - non appelé
        raise NotImplementedError

    def inspire(self, request: Any) -> GeneratedImage:
        self.requests.append(request)
        return GeneratedImage(
            pixels=np.full(request.image.shape, 210, np.uint8),
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=0.1,
        )


def test_stamp_is_at_least_a_twenty_fifth_of_the_height() -> None:
    plain = np.full((960, 1280, 3), 210, np.uint8)
    marked = stamp(plain)
    rows = np.where((marked != 210).any(axis=(1, 2)))[0]
    assert rows.size and rows.max() - rows.min() + 1 >= 960 * STAMP_SHARE
    band = marked[rows.max() - 2, 40]
    assert band.max() < 120, "fond sombre semi-opaque sous le texte"


def test_catalogue_on_demand_keeps_other_images(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = attic_project()
    store.save(project)
    run_id = uuid4()
    folder = store.runs_dir(project.id) / str(run_id) / "blender"
    folder.mkdir(parents=True)
    Image.new("RGB", (128, 96), (90, 60, 40)).save(folder / "preview.png")
    (folder / "inspiration.png").write_bytes(b"b")
    entry = RenderEntry(
        run_id=run_id,
        camera_id=None,
        camera_name="Vue d'ensemble",
        samples=64,
        device="cuda",
        render_s=1.0,
        width_px=128,
        height_px=96,
        created_at=datetime.now(UTC),
    )
    (folder / RENDER_SIDECAR).write_text(entry.model_dump_json())
    model = _Model()
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), image_finisher=model
    )
    with TestClient(app) as client:
        pid = str(project.id)
        job = client.post(f"/api/projects/{pid}/renders/{run_id}/catalogue")
        assert job.status_code == 202, job.text
        for _ in range(100):
            status = client.get(f"/api/projects/{pid}/jobs/{job.json()['id']}").json()
            if status["status"] != "running":
                break
            time.sleep(0.05)
        assert status["status"] == "done", status
        (render,) = client.get(f"/api/projects/{pid}/renders").json()
    assert render["catalogue"]["label"] == CATALOGUE_LABEL
    assert render["catalogue"]["source"] == "generated" and render["catalogue"]["mode"] == "txt2img"
    assert model.requests[0].mode == "txt2img" and model.requests[0].control_scale == 1.2
    assert (folder / CATALOGUE).is_file()
    assert (folder / "preview.png").is_file() and (folder / "inspiration.png").read_bytes() == b"b"
