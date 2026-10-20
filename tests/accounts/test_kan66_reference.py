"""KAN-66 : la mesure de référence est une écriture d'éditeur (garde E) ; le lecteur et le
commentateur sont refusés avant tout calcul ; elle respecte le bail du plan (KAN-11)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts.lease import IMAGE_LEASED
from openplan.api.guards import PROJECT_ROUTES
from openplan.pipeline.reference import camera_basis, with_basis
from openplan.schemas.scene import ImageRef
from tests.accounts.helpers import ADMIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client
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


def test_a_measurement_respects_the_plan_lease(tmp_path: Path, migrated_url: str) -> None:
    """Une mesure change l'échelle de tout le plan (KAN-11) : Fanny tient le plan estimé
    depuis la photo, Gilles pose une mesure, la retire ou réestime la photo : 409, même
    message que les autres écritures du plan ; le plan libéré, Gilles passe la garde."""
    owner, _service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Grenier à trois"}).json()
    pid, level = project["id"], next(iter(project["levels"]))
    people: dict[str, TestClient] = {}
    for who in ("fanny.kan66", "gilles.kan66"):
        token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()
        client = _client(owner.app)
        sign_in(client, mailer, f"{who}@example.org")
        accepted = client.post("/api/auth/invitation/accept", json={"token": token["token"]})
        assert accepted.status_code == 200
        people[who] = client
    fanny, gilles = people["fanny.kan66"], people["gilles.kan66"]
    # Le plan a été estimé depuis une photo (profondeur non gardée : peu importe ici).
    store = owner.app.state.store  # type: ignore[attr-defined]
    saved = store.load(UUID(pid))
    ref = ImageRef(
        path="images/photo.jpg", sha256="0" * 64, width_px=320, height_px=240, exif_stripped=True
    )
    saved = saved.model_copy(update={"images": {ref.id: ref}})
    saved = with_basis(saved, UUID(level), camera_basis(ref.id, 0.5, 1.5, entered=False))
    store.save(saved)
    url = f"/api/projects/{pid}/images/{ref.id}/reference"
    body = {"p1": [10, 10], "p2": [100, 10], "length_m": 2.04}
    # Fanny prend la main sur ce plan.
    assert fanny.post(f"/api/projects/{pid}/levels/{level}/lease").status_code == 200
    holder = owner.app.state.leases.holder(UUID(pid), level)  # type: ignore[attr-defined]
    expected = f"{holder.name} modifie ce plan"
    refused = gilles.put(url, json=body)
    assert refused.status_code == 409 and refused.json()["detail"] == expected
    gone = gilles.delete(f"{url}/{uuid4()}")
    assert gone.status_code == 409 and gone.json()["detail"] == expected
    reest = gilles.post(f"/api/projects/{pid}/levels/{level}/images/{ref.id}/reestimate")
    assert reest.status_code == 409 and reest.json()["detail"] == expected
    assert store.load(UUID(pid)).images[ref.id].reference_segments == []
    # Fanny rend la main : Gilles passe la garde (la profondeur manque : 422, pas 409).
    assert fanny.delete(f"/api/projects/{pid}/levels/{level}/lease").status_code == 204
    passed = gilles.put(url, json=body)
    assert passed.status_code == 422 and "réestimez la photo" in passed.json()["detail"]
    assert set(PROJECT_ROUTES) >= IMAGE_LEASED
