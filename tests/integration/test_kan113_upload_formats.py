"""KAN-113 : par l'API, une photo d'un autre format que JPEG, PNG ou WebP (SPIDER, GIF,
TIFF, EPS), même renommée en .jpg, reçoit un 4xx clair, jamais un 500, et rien n'est gardé."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.unit.test_kan113_image_formats import OTHERS, other_format, png


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(settings=Settings(data_dir=tmp_path / "data")), raise_server_exceptions=False
    )


@pytest.mark.parametrize("fmt", OTHERS)
def test_renamed_upload_gets_a_clean_4xx(client: TestClient, fmt: str) -> None:
    pid = client.post("/api/projects", json={"name": "Atelier"}).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/images",
        files={"file": ("photo.jpg", other_format(fmt), "image/jpeg")},
    )
    assert 400 <= r.status_code < 500, r.text
    assert "JPEG, PNG ou WebP seulement" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}").json()["images"] == {}


def test_a_png_still_uploads(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Atelier"}).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/images", files={"file": ("salon.png", png(), "image/png")}
    )
    assert r.status_code == 201, r.text
