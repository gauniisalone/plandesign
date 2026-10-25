"""KAN-95 : le plan recoupé sur plusieurs photos (`attic-fusion`, `photo-fusion`) est un geste
d'éditeur, comme autoplan : lecteur et commentateur reçoivent 403 ; un non-membre reçoit la même
404 qu'un projet inexistant (l'existence du projet ne se devine pas, règle de B1c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.api.guards import PROJECT_ROUTES, Role
from tests.accounts.helpers import sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan51_comments import _team

L = "/api/projects/{project_id}/levels/{level_id}"
ROUTES = ("attic-fusion", "photo-fusion")


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def test_the_two_fusion_routes_are_editor_gestures() -> None:
    for name in ROUTES:
        assert PROJECT_ROUTES[("POST", f"{L}/{name}")] == Role.EDITOR, name


def test_readers_commenters_get_403_and_a_stranger_404(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, service = _team(tmp_path, migrated_url)
    level = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    stranger = _client(owner.app)
    sign_in(stranger, service.mailer, "inconnu@example.org")
    for name in ROUTES:
        url = f"/api/projects/{pid}/levels/{level}/{name}"
        for role in ("viewer", "commenter"):
            assert m[role].post(url, json={}).status_code == 403, (name, role)
        assert stranger.post(url, json={}).status_code == 404, name
