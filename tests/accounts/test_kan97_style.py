"""KAN-97 : les cartes Inspirations jusqu'au rendu réaliste, par les vraies routes (sans poids).

Un poste qui annonce « style » reçoit les noms des images de la carte Inspirations reliée à la
carte Rendus, lit chacune sous son bail (JPEG réduit), et renvoie ses mots : la carte Rendus dit
« Guidé par : scandinave, lumineux ». Un ancien poste ne reçoit rien de nouveau et la carte
nomme alors les images, « non lues ». Sans inspiration : « Sans inspiration » (liste vide)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from openplan.schemas.render_queue import CAPABILITIES_HEADER, STYLE_IMAGE_SIDE, WorkerResult
from tests.accounts.test_kan89_realistic import WORKER, World, png

STYLED = {"state": "idle", "version": "heartbeat+style"}
OLD = {"state": "idle", "version": "heartbeat"}


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> Iterator[World]:
    w = World(tmp_path, migrated_url)
    yield w
    w.close()


def _inspire(world: World, name: str = "salon-clair.png") -> None:
    """Une image jointe posée dans la carte Inspirations (reliée d'office à la carte Rendus)."""
    pid = world.project_id
    big = io.BytesIO()
    Image.new("RGB", (1600, 1000), (230, 220, 200)).save(big, format="PNG")
    sent = world.client.post(
        f"/api/projects/{pid}/media", files={"file": (name, big.getvalue(), "image/png")}
    )
    assert sent.status_code == 201, sent.text
    board = world.client.get(f"/api/projects/{pid}/board").json()
    insp = next(i for i in board["items"] if i["kind"] == "frame" and i["role"] == "inspirations")
    board["items"].append(
        {
            "id": "00000000-0000-4000-8000-00000000a097",
            "kind": "file",
            "x": insp["x"] + 24,
            "y": insp["y"] + 24,
            "w": 280,
            "h": 190,
            "ref": sent.json()["id"],
            "text": name,
            "frame": insp["id"],
            "reference": True,
        }
    )
    saved = world.client.put(f"/api/projects/{pid}/board", json=board)
    assert saved.status_code == 200, saved.text


def _claim(world: World, body: dict[str, str]) -> tuple[dict[str, Any], str]:
    taken = world.worker.post("/api/worker/claim", json=body, headers=WORKER)
    assert taken.status_code == 200, taken.text
    return taken.json(), taken.headers.get(CAPABILITIES_HEADER, "")


def _deposit(world: World, job: dict[str, Any], words: list[str] | None) -> None:
    meta = WorkerResult(
        seconds=90.0, vram_peak_mib=0, width=64, height=48, engine="fake", style_words=words
    )
    done = world.worker.post(
        f"/api/worker/jobs/{job['request_id']}/result",
        headers={**WORKER, "X-Render-Lease": job["lease"]},
        data={"meta": meta.model_dump_json(exclude_none=True)},
        files={"image": ("r.png", png(), "image/png")},
    )
    assert done.status_code == 200, done.text


def _row(world: World) -> dict[str, Any]:
    rows = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"]
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    return row


def test_a_new_worker_reads_the_inspirations_and_the_card_says_its_words(world: World) -> None:
    _inspire(world)
    asked = world.ask()
    assert asked["guided_by"] == ["salon-clair.png"] and asked["guided_read"] is False
    job, served = _claim(world, STYLED)
    assert served == "heartbeat+style"
    assert job["style"] == ["salon-clair.png"]
    lease = {**WORKER, "X-Render-Lease": job["lease"]}
    got = world.worker.get(f"/api/worker/jobs/{job['request_id']}/style/0", headers=lease)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(got.content)) as img:
        assert max(img.size) == STYLE_IMAGE_SIDE, "réduite pour le poste"
    missing = world.worker.get(f"/api/worker/jobs/{job['request_id']}/style/1", headers=lease)
    assert missing.status_code == 410
    no_lease = world.worker.get(f"/api/worker/jobs/{job['request_id']}/style/0", headers=WORKER)
    assert no_lease.status_code == 404
    out_of_range = world.worker.get(f"/api/worker/jobs/{job['request_id']}/style/9", headers=lease)
    assert out_of_range.status_code == 401, "hors des chemins du poste"
    _deposit(world, job, ["scandinavian", "bright"])
    row = _row(world)
    assert row["guided_by"] == ["scandinave", "lumineux"] and row["guided_read"] is True


def test_an_old_worker_gets_nothing_new_and_the_card_names_the_images(world: World) -> None:
    _inspire(world)
    world.ask()
    job, served = _claim(world, OLD)
    assert served == "heartbeat" and "style" not in job, "un ancien poste refuserait le champ"
    _deposit(world, job, None)
    row = _row(world)
    assert row["guided_by"] == ["salon-clair.png"] and row["guided_read"] is False


def test_without_inspiration_the_card_says_so(world: World) -> None:
    asked = world.ask()
    assert asked["guided_by"] == []
    job, _ = _claim(world, STYLED)
    assert "style" not in job
    _deposit(world, job, None)
    assert _row(world)["guided_by"] == []


def test_the_real_worker_fetches_the_images_and_sends_its_words(
    world: World, tmp_path: Path
) -> None:
    """Le vrai poste (client, boucle, calcul isolé, moteur factice) : il annonce « style »,
    télécharge l'image dans le dossier du calcul, et ses mots (ceux du moteur factice) arrivent
    sur la carte Rendus."""
    from tests.accounts.test_kan89_worker_flow import _worker

    _inspire(world)
    world.ask()
    worker, said = _worker(world, tmp_path)
    worker.cycle()
    assert worker.rendered == 1, said
    assert "style" in worker.client.served
    row = _row(world)
    assert row["state"] == "done"
    assert row["guided_by"] == ["scandinave", "lumineux"] and row["guided_read"] is True


def test_an_unknown_word_from_a_newer_worker_is_ignored(world: World) -> None:
    _inspire(world)
    world.ask()
    job, _ = _claim(world, STYLED)
    _deposit(world, job, ["scandinavian", "neo brutalist"])
    assert _row(world)["guided_by"] == ["scandinave"]
