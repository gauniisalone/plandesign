"""KAN-98 L3 : la route POST /media/link, réseau simulé (la garde anti-SSRF elle-même est testée
dans tests/unit/test_kan98_link_fetch.py). Image gardée avec son lien, refus de la garde rendus
avec leur code sans rien garder, contenu contrôlé par ses octets (KAN-113 : JPEG, PNG ou WebP),
10 liens par minute."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.adapters.remote import link_fetch
from openplan.api.app import create_app
from openplan.api.link_images import LINK_LIMIT
from openplan.api.settings import Settings
from tests.unit.test_kan113_image_formats import OTHERS, other_format, png

Fetch = Callable[[str], link_fetch.FetchedImage]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(settings=Settings(data_dir=tmp_path / "data")), raise_server_exceptions=False
    )


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Les liens que la route a demandés ; par défaut, une page dont l'aperçu est un PNG."""
    asked: list[str] = []

    def fake(raw: str) -> link_fetch.FetchedImage:
        asked.append(raw)
        return link_fetch.FetchedImage(
            png(), "image/png", "https://cdn.exemple.fr/i/salon%20bleu.png", raw
        )

    monkeypatch.setattr(link_fetch, "fetch_link_image", fake)
    return asked


def _answer(monkeypatch: pytest.MonkeyPatch, fake: Fetch) -> None:
    monkeypatch.setattr(link_fetch, "fetch_link_image", fake)


def _project(client: TestClient) -> str:
    pid: str = client.post("/api/projects", json={"name": "Moodboard"}).json()["id"]
    return pid


def test_a_page_link_keeps_its_preview_image_and_the_page(
    client: TestClient, seen: list[str]
) -> None:
    pid = _project(client)
    page = "https://exemple.fr/salon"
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": page})
    assert r.status_code == 201, r.text
    ref = r.json()
    assert seen == [page]
    assert ref["source_url"] == page, "la page, pas l'adresse de son image"
    assert ref["media_type"] == "image/png" and ref["kind"] == "image"
    assert ref["name"] == "salon-bleu.png" and ref["placed"] is False
    listed = client.get(f"/api/projects/{pid}/media").json()
    assert [m["source_url"] for m in listed] == [page]
    got = client.get(f"/api/projects/{pid}/media/{ref['id']}/file")
    assert got.status_code == 200 and got.content == png()


def test_an_image_link_keeps_the_image_address_and_placed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = "https://exemple.fr/photos/canape.jpg"
    _answer(monkeypatch, lambda raw: link_fetch.FetchedImage(png(), "image/png", image, None))
    pid = _project(client)
    r = client.post(
        f"/api/projects/{pid}/media/link", params={"placed": "true"}, json={"url": image}
    )
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["source_url"] == image and ref["placed"] is True
    assert ref["name"] == "canape.png", "l'extension du type réel, pas celle de l'adresse"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (link_fetch.LinkRefusedError, 400),
        (link_fetch.LinkTooBigError, 413),
        (link_fetch.LinkNoImageError, 422),
        (link_fetch.LinkUnreachableError, 502),
        (link_fetch.LinkTooSlowError, 504),
    ],
)
def test_a_refusal_of_the_guard_keeps_its_code_and_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error: type[Exception], status: int
) -> None:
    def refuse(raw: str) -> link_fetch.FetchedImage:
        raise error("Ce lien est refusé.")

    _answer(monkeypatch, refuse)
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/x"})
    assert r.status_code == status, r.text
    assert r.json()["detail"] == "Ce lien est refusé."
    assert client.get(f"/api/projects/{pid}/media").json() == []


@pytest.mark.parametrize("fmt", OTHERS)
def test_bytes_that_are_not_jpeg_png_or_webp_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fmt: str
) -> None:
    """Un site qui dit « image/png » mais sert autre chose (SPIDER, GIF, TIFF, EPS) : 422,
    rien de gardé ; aucun autre décodeur que JPEG, PNG et WebP n'est essayé (KAN-113)."""
    data = other_format(fmt)
    _answer(
        monkeypatch,
        lambda raw: link_fetch.FetchedImage(data, "image/png", "https://exemple.fr/x.png", None),
    )
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/x.png"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("Image refusée")
    assert client.get(f"/api/projects/{pid}/media").json() == []


def test_a_truncated_png_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = png()[:40]
    _answer(
        monkeypatch,
        lambda raw: link_fetch.FetchedImage(broken, "image/png", "https://exemple.fr/x.png", None),
    )
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/x.png"})
    assert r.status_code == 422 and "illisible" in r.json()["detail"], r.text


@pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": "https://a.fr/" + "a" * 5000}])
def test_a_malformed_body_is_a_422_without_any_fetch(
    client: TestClient, seen: list[str], body: dict[str, str]
) -> None:
    pid = _project(client)
    assert client.post(f"/api/projects/{pid}/media/link", json=body).status_code == 422
    assert seen == []


def test_ten_links_a_minute_then_429(client: TestClient, seen: list[str]) -> None:
    pid = _project(client)
    for n in range(LINK_LIMIT):
        url = f"https://exemple.fr/{n}"
        assert client.post(f"/api/projects/{pid}/media/link", json={"url": url}).status_code == 201
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/de-trop"})
    assert r.status_code == 429 and "une minute" in r.json()["detail"]
    assert len(seen) == LINK_LIMIT, "le lien de trop n'est pas demandé au réseau"
