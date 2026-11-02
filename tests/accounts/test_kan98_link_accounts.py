"""KAN-98 L3 avec les comptes : POST /media/link est une route d'éditeur ; la place du
propriétaire est contrôlée avant toute requête vers le site (réserve de 10 Mo, la taille la
plus grande permise pour l'image) ; 10 liens par minute et par compte. Réseau simulé."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.adapters.remote import link_fetch
from openplan.api.accounts.quota import LINK_RESERVE, LINKS, MB
from openplan.api.guards import PROJECT_ROUTES, Role
from openplan.api.link_images import LINK_LIMIT
from tests.accounts.helpers import sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan12_quota import _setup
from tests.unit.test_kan113_image_formats import png


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def fake(raw: str) -> link_fetch.FetchedImage:
        asked.append(raw)
        return link_fetch.FetchedImage(png(), "image/png", raw, None)

    monkeypatch.setattr(link_fetch, "fetch_link_image", fake)
    return asked


def _link(client: TestClient, pid: str, n: int = 0) -> int:
    r = client.post(f"/api/projects/{pid}/media/link", json={"url": f"https://exemple.fr/{n}.png"})
    code: int = r.status_code
    return code


def test_an_editor_pastes_a_viewer_cannot(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    assert _link(owner, pid) == 201
    assert _link(guest, pid, 1) == 201, "l'invitée éditrice"
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer"}).json()["token"]
    viewer = _client(owner.app)
    mailer = owner.app.state.accounts.mailer  # type: ignore[attr-defined]
    sign_in(viewer, mailer, "bruno@example.org")
    assert viewer.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    assert _link(viewer, pid, 2) == 403
    assert _link(_client(owner.app), pid, 3) == 401, "sans session"
    assert len(seen) == 2, "aucun refus n'a demandé le site"


def test_a_full_space_is_refused_before_the_site_is_asked(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=30)
    store = owner.app.state.store  # type: ignore[attr-defined]
    runs = store.project_dir(pid) / "runs"
    runs.mkdir(exist_ok=True)
    # Il reste moins que la réserve d'un lien : refusé, alors que la limite n'est pas atteinte.
    (runs / "presque.bin").write_bytes(b"x" * (30 * MB - LINK_RESERVE + MB))
    refused = owner.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/a"})
    assert refused.status_code == 413 and "Votre espace est plein" in refused.json()["detail"]
    theirs = guest.post(f"/api/projects/{pid}/media/link", json={"url": "https://exemple.fr/b"})
    assert theirs.status_code == 413 and "propriétaire" in theirs.json()["detail"]
    assert seen == [], "le site n'est jamais demandé"
    (runs / "presque.bin").unlink()
    assert _link(owner, pid) == 201


def test_ten_links_a_minute_per_account(tmp_path: Path, migrated_url: str, seen: list[str]) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    for n in range(LINK_LIMIT):
        assert _link(owner, pid, n) == 201
    assert _link(owner, pid, 99) == 429
    assert _link(guest, pid, 100) == 201, "l'autre compte a sa propre limite"


def test_route_tables() -> None:
    assert set(PROJECT_ROUTES) >= LINKS
    assert {PROJECT_ROUTES[k] for k in LINKS} == {Role.EDITOR}
