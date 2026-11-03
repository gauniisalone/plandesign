"""KAN-120 avec les comptes : « Régénérer le plan » (replace) écrit une seule fois, à la fin de
la tâche, et l'annonce aux autres éditeurs À CE MOMENT-LÀ (« changed »), pas seulement à
l'envoi de la demande ; l'onglet qui l'a demandé (X-Live-Conn) n'en reçoit aucun."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts import live
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"
CHANGED = {"type": "changed", "scope": "project"}

pytestmark = pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")


def _ws(client: TestClient, pid: str) -> Any:
    # Comme test_b2a_live : le cookie `Secure` passé à la main sur ws://.
    headers = {"origin": ORIGIN}
    raw = client.cookies.get("pd_session")
    if raw:
        headers["cookie"] = f"pd_session={raw}"
    return client.websocket_connect(f"/ws/projects/{pid}", headers=headers)


def _done(client: TestClient, pid: str, job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(1200):
        job = client.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    pytest.fail("tâche jamais terminée")


def _cursor(client: TestClient, pid: str) -> int:
    return int(client.get(f"/api/projects/{pid}").json()["history_cursor"])


def test_regenerated_plan_reaches_the_other_editor_after_the_final_write(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Régénérer à deux"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()["token"]
    guest = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(guest, mailer, "marie.durand@example.org")
    assert guest.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    upload = owner.post(
        f"/api/projects/{pid}/images?level_id={lid}",
        files={"file": ("p.png", FIXTURE.read_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    autoplan = f"/api/projects/{pid}/levels/{lid}/images/{upload.json()['image']['id']}/autoplan"
    first = owner.post(autoplan)
    assert first.status_code == 202, first.text
    assert _done(owner, pid, first.json())["status"] == "done"

    # L'annonce de fin de tâche relève le curseur du serveur au moment où elle part.
    seen: list[int] = []
    original = live.announce_changed_from_thread

    def spy(app: Any, project_id: Any, exclude: str | None = None) -> None:
        # Lu sur le disque, depuis le fil de la tâche (pas de requête de test croisée).
        seen.append(app.state.store.load(project_id).history_cursor)
        original(app, project_id, exclude=exclude)

    monkeypatch.setattr(live, "announce_changed_from_thread", spy)

    before = _cursor(owner, pid)
    with _ws(owner, pid) as mine, _ws(guest, pid) as hers:
        conn = mine.receive_json()["conn"]
        hers.receive_json()
        assert mine.receive_json()["type"] == "join"  # arrivée de l'invitée
        again = owner.post(f"{autoplan}?replace=true", headers={"X-Live-Conn": conn})
        assert again.status_code == 202, again.text
        assert _done(owner, pid, again.json())["status"] == "done"
        after = _cursor(owner, pid)
        assert after == before + 1  # une seule étape d'annulation
        # Annoncé une fois l'écriture faite : le serveur avait déjà le nouveau curseur.
        assert seen == [after]
        # Marie : l'avis de la demande, puis celui de la fin de la tâche.
        assert hers.receive_json() == CHANGED
        assert hers.receive_json() == CHANGED
        hers.send_text('{"type":"ping"}')
        assert hers.receive_json() == {"type": "pong"}  # rien d'autre
        # L'onglet demandeur n'a reçu aucun des deux avis.
        mine.send_text('{"type":"ping"}')
        assert mine.receive_json() == {"type": "pong"}
