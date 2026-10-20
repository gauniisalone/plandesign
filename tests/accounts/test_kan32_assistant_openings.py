"""KAN-32 : l'assistant qui modifie une porte ou une fenêtre passe par les droits du plan.
Lecteur refusé (403) ; le bail du plan (KAN-11) vaut pour l'assistant comme pour les autres
écritures : une autre personne qui tient le plan → 409, rien ne change ; le geste fait entre
au journal d'activité (KAN-58) avec son libellé, et « Annuler » aussi."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts.lease import LEASED_ROUTES
from openplan.api.guards import L
from tests.accounts.helpers import ADMIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trois personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _trio(tmp_path: Path, url: str) -> tuple[TestClient, TestClient, TestClient, str, str]:
    """Propriétaire, éditeur, lecteur ; un plan rectangulaire avec une fenêtre estimée."""
    owner, _service, mailer = accounts_client(tmp_path, url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Plan à trois"}).json()
    pid = project["id"]
    level = next(iter(project["levels"]))
    others = []
    for role, email in (("editor", "gilles@example.org"), ("viewer", "fanny@example.org")):
        token = owner.post(f"/api/projects/{pid}/invitations", json={"role": role}).json()["token"]
        client = _client(owner.app)
        sign_in(client, mailer, email)
        assert client.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
        others.append(client)
    ops = f"/api/projects/{pid}/levels/{level}/ops"
    corners = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1], strict=True):
        r = owner.post(
            ops, json={"op": "add_wall", "start": {"x": ax, "y": ay}, "end": {"x": bx, "y": by}}
        )
        assert r.status_code == 200, r.text
    r = owner.post(ops, json={"op": "detect_rooms"})
    assert r.status_code == 200, r.text
    plan = owner.get(f"/api/projects/{pid}").json()["levels"][level]
    south = next(
        e["id"]
        for e in plan["elements"].values()
        if e["type"] == "wall"
        and plan["vertices"][e["start_vertex"]]["position"]["y"] == 0.0
        and plan["vertices"][e["end_vertex"]]["position"]["y"] == 0.0
    )
    r = owner.post(
        ops,
        json={
            "op": "add_opening",
            "host_wall": south,
            "kind": "window",
            "offset": 1.5,
            "width": 1.0,
            "height": 1.25,
            "sill_height": 0.9,
            "source": "estimated",
            "confidence": 0.6,
        },
    )
    assert r.status_code == 200, r.text
    # Le propriétaire rend la main : le plan est libre.
    assert owner.delete(f"/api/projects/{pid}/levels/{level}/lease").status_code == 204
    return owner, others[0], others[1], pid, level


def _windows(client: TestClient, pid: str, level: str) -> list[dict[str, Any]]:
    plan = client.get(f"/api/projects/{pid}").json()["levels"][level]
    return [e for e in plan["elements"].values() if e["type"] == "opening"]


def test_the_assistant_route_is_under_the_plan_lease() -> None:
    assert ("POST", L + "/assistant/say") in LEASED_ROUTES
    assert ("POST", L + "/assistant/act") in LEASED_ROUTES


def test_viewer_refused_lease_applies_and_the_gesture_is_journaled(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, editor, viewer, pid, level = _trio(tmp_path, migrated_url)
    say = f"/api/projects/{pid}/levels/{level}/assistant/say"
    text = {"text": "élargis la fenêtre à 1,20 m"}

    # Lecteur : refusé, rien ne change.
    assert viewer.post(say, json=text).status_code == 403
    assert _windows(owner, pid, level)[0]["width"] == 1.0

    # L'éditeur parle à l'assistant : il prend le bail, le geste est fait.
    done = editor.post(say, json=text)
    assert done.status_code == 200, done.text
    parts = done.json()["turn"]["parts"]
    assert [p["status"] for p in parts] == ["done"], parts
    assert _windows(owner, pid, level)[0]["width"] == 1.2

    # Le propriétaire, pendant que l'éditeur tient le plan : 409, rien ne change.
    refused = owner.post(say, json={"text": "supprime la fenêtre"})
    assert refused.status_code == 409 and refused.json()["detail"] == "gilles modifie ce plan"
    assert len(_windows(owner, pid, level)) == 1

    # Journal d'activité : le geste de l'assistant, au nom de l'éditeur, avec son libellé.
    events = [json.loads(line) for line in _events(tmp_path).read_text("utf-8").splitlines()]
    said = [
        e
        for e in events
        if e["type"] == "plan.changed" and (e.get("label") or "").startswith("Assistant")
    ]
    assert [e["label"] for e in said] == ["Assistant : largeur de la fenêtre sud à 1,20 m"]
    lines = owner.get(f"/api/projects/{pid}/activity", params={"kind": "plan"}).json()["items"]
    assert [line["label"] for line in lines if line["actor_name"] == "gilles"] == [
        "Assistant : largeur de la fenêtre sud à 1,20 m"
    ], lines

    # Annuler (par l'éditeur, qui tient le plan) : la fenêtre revient, « Annulé : … » au journal.
    assert editor.post(f"/api/projects/{pid}/undo").status_code == 200
    assert _windows(owner, pid, level)[0]["width"] == 1.0
    raw = _events(tmp_path).read_text("utf-8")
    assert "Annulé : Assistant : largeur de la fenêtre sud à 1,20 m" in raw


def _events(tmp_path: Path) -> Path:
    return next(tmp_path.rglob("events.jsonl"))
