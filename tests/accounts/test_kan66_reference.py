"""KAN-66 : la mesure de référence est une écriture d'éditeur (garde E) ; le lecteur et le
commentateur sont refusés avant tout calcul."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from tests.accounts.test_kan51_comments import _team


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def test_reference_routes_are_for_editors(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    image, segment = uuid4(), uuid4()
    lid = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    url = f"/api/projects/{pid}/images/{image}/reference"
    body = {"p1": [10, 10], "p2": [100, 10], "length_m": 2.04}
    for role in ("viewer", "commenter"):
        assert m[role].put(url, json=body).status_code == 403
        assert m[role].delete(f"{url}/{segment}").status_code == 403
        reest = m[role].post(f"/api/projects/{pid}/levels/{lid}/images/{image}/reestimate")
        assert reest.status_code == 403
    # L'éditeur passe la garde : la photo est inconnue (404), rien d'autre.
    assert m["editor"].put(url, json=body).status_code == 404
    assert m["editor"].delete(f"{url}/{segment}").status_code == 404
