"""KAN-52, lot 1 : la cloche côté serveur, avec les comptes et en local. Publication (arrivée
d'un membre, commentaires, modifications du tableau et du plan, rendu terminé), filtrage par
membre, jamais ses propres gestes, regroupement, état « lu » une par une puis « tout lu »,
départ d'un projet."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from openplan.api.accounts.live import CLOSE_BAD_ORIGIN, CLOSE_NO_SESSION
from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.accounts.helpers import ORIGIN, sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan51_comments import _team, _thread


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _bell(client: TestClient, tab: str = "all") -> dict[str, Any]:
    response = client.get("/api/notifications", params={"tab": tab})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _types(client: TestClient) -> list[str]:
    return [line["type"] for line in _bell(client)["items"]]


def test_joins_comments_and_changes_reach_the_right_people(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    # Trois arrivées : le propriétaire les voit toutes ; chacun voit celles des autres arrivés
    # après lui, jamais la sienne.
    assert _types(owner) == ["member.joined"] * 3
    assert _types(m["editor"]) == []
    assert _types(m["viewer"]) == ["member.joined"] * 2
    # L'éditeur modifie l'espace de travail : les autres le voient, pas lui.
    view = {"view": {"x": 1, "y": 2, "zoom": 1}}
    items = owner.get(f"/api/projects/{pid}/board").json()["items"]
    moved = {**items[0], "x": items[0]["x"] + 50}
    assert (
        m["editor"].patch(f"/api/projects/{pid}/board/items", json={"set": [moved]}).status_code
        == 200
    )
    assert m["editor"].patch(f"/api/projects/{pid}/board/items", json=view).status_code == 200
    line = _bell(owner)["items"][0]
    assert line["type"] == "board.changed" and line["actor_name"] == "editor"
    assert line["project_name"] == "Salon" and line["mine"] is False
    assert "board.changed" not in _types(m["editor"])
    # Le commentateur ouvre un fil : le propriétaire le reçoit ; l'éditeur, qui n'y a rien
    # écrit, non. L'éditeur répond : le commentateur (qui y a écrit) et le propriétaire.
    tid = _thread(m["commenter"], pid).json()["id"]
    assert _types(owner)[0] == "comment.created"
    assert "comment.created" not in _types(m["editor"])
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Oui"})
    assert _types(m["commenter"])[0] == "comment.replied"
    assert _types(owner)[0] == "comment.replied"
    assert "comment.replied" not in _types(m["editor"])
    first = _bell(owner)["items"][0]
    assert first["thread_id"] == tid and "Oui" not in str(first)  # jamais le texte


def test_read_one_line_then_all_and_the_count(tmp_path: Path, migrated_url: str) -> None:
    owner, _, _, _ = _team(tmp_path, migrated_url)
    lines = _bell(owner, "unread")["items"]
    assert len(lines) == 3 and all(line["unread"] for line in lines)
    assert owner.get("/api/notifications/count").json() == {"unread": 3}
    # Lire la ligne du milieu ne touche pas les plus anciennes (GitHub, Linear).
    middle = lines[1]["id"]
    assert owner.post("/api/notifications/read", json={"event_id": middle}).status_code == 204
    after = _bell(owner)["items"]
    assert [line["unread"] for line in after] == [True, False, True]
    assert owner.get("/api/notifications/count").json() == {"unread": 2}
    assert [line["id"] for line in _bell(owner, "unread")["items"]] == [
        lines[0]["id"],
        lines[2]["id"],
    ]
    # Une ligne qui n'est pas à moi : 404.
    unknown = owner.post("/api/notifications/read", json={"event_id": str(uuid4())})
    assert unknown.status_code == 404
    assert owner.post("/api/notifications/read-all").status_code == 204
    assert owner.get("/api/notifications/count").json() == {"unread": 0}
    assert _bell(owner, "unread")["items"] == [] and len(_bell(owner)["items"]) == 3


def test_only_member_projects_and_leaving_removes_them(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, service = _team(tmp_path, migrated_url)
    stranger = _client(owner.app)
    sign_in(stranger, owner.app.state.accounts.mailer, "x@example.org")  # type: ignore[attr-defined]
    assert _bell(stranger)["items"] == [] and stranger.get("/api/notifications/count").json() == {
        "unread": 0
    }
    owner.post(
        f"/api/projects/{pid}/comments", json={"target": {"at": {"x": 1, "y": 1}}, "text": "a"}
    )
    assert len(_bell(m["viewer"])["items"]) == 2  # deux arrivées après la sienne
    gone = service.user_by_email("viewer@example.org")
    assert owner.delete(f"/api/projects/{pid}/members/{gone.id}").status_code == 200
    assert _bell(m["viewer"])["items"] == []
    assert m["viewer"].get("/api/notifications/count").json() == {"unread": 0}


def _me_ws(client: TestClient, origin: str = ORIGIN) -> Any:
    """`/ws/me` ; le cookie `Secure` passé à la main (le client de test ne l'envoie pas sur ws)."""
    headers = {"origin": origin}
    raw = client.cookies.get("pd_session")
    if raw:
        headers["cookie"] = f"pd_session={raw}"
    return client.websocket_connect("/ws/me", headers=headers)


def test_my_channel_refuses_without_session_or_from_another_site(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, _, _, _ = _team(tmp_path, migrated_url)
    for client, origin, code in (
        (_client(owner.app), ORIGIN, CLOSE_NO_SESSION),  # non connecté → refus
        (owner, "https://ailleurs.example.org", CLOSE_BAD_ORIGIN),
    ):
        with pytest.raises(WebSocketDisconnect) as closed, _me_ws(client, origin) as ws:
            ws.receive_text()
        assert closed.value.code == code


def test_my_channel_rings_the_recipients_not_the_author(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    items = owner.get(f"/api/projects/{pid}/board").json()["items"]
    with _me_ws(owner) as mine, _me_ws(m["editor"]) as his:
        assert mine.receive_json()["type"] == "hello"
        assert his.receive_json()["type"] == "hello"
        moved = {**items[0], "x": items[0]["x"] + 5}
        m["editor"].patch(f"/api/projects/{pid}/board/items", json={"set": [moved]})
        assert mine.receive_json() == {"type": "bell"}  # le propriétaire : sa cloche a changé
        # L'auteur n'est pas sonné ; ses autres appareils le sont quand il lit sa cloche.
        his.send_text('{"type":"ping"}')
        assert his.receive_json() == {"type": "pong"}
        m["editor"].post("/api/notifications/read-all")
        assert his.receive_json() == {"type": "bell"}
        # Un commentaire : les membres sont sonnés.
        _thread(m["commenter"], pid)
        assert mine.receive_json() == {"type": "bell"}
        assert his.receive_json() == {"type": "bell"}


def test_signed_out_is_refused(tmp_path: Path, migrated_url: str) -> None:
    owner, _, _, _ = _team(tmp_path, migrated_url)
    anonymous = _client(owner.app)
    assert anonymous.get("/api/notifications").status_code == 401
    assert anonymous.get("/api/notifications/count").status_code == 401
    assert anonymous.post("/api/notifications/read-all").status_code in (401, 403)


class _Done(BaseModel):
    run_id: UUID


def test_local_mode_render_done_is_mine_too(tmp_path: Path) -> None:
    """En local, une seule personne : ses gestes ne lui reviennent pas, sauf « Votre rendu est
    prêt ». Mêmes routes, état « lu » dans un fichier."""
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    items = local.get(f"/api/projects/{pid}/board").json()["items"]
    local.patch(f"/api/projects/{pid}/board/items", json={"set": [{**items[0], "x": 9}]})
    local.post(
        f"/api/projects/{pid}/comments", json={"target": {"at": {"x": 1, "y": 1}}, "text": "a"}
    )
    assert _bell(local)["items"] == []
    run = uuid4()
    local.app.state.jobs.on_done("render", "user", UUID(pid), _Done(run_id=run))  # type: ignore[attr-defined]
    lines = _bell(local)["items"]
    assert [(x["type"], x["mine"], x["run_id"]) for x in lines] == [("render.done", True, str(run))]
    assert local.get("/api/notifications/count").json() == {"unread": 1}
    local.post("/api/notifications/read", json={"event_id": lines[0]["id"]})
    assert local.get("/api/notifications/count").json() == {"unread": 0}
    assert (tmp_path / "local" / "notifications.json").is_file()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


def test_a_photo_drop_makes_one_line_then_groups(tmp_path: Path, migrated_url: str) -> None:
    """Le dépôt d'une photo touche le plan et le tableau : une seule ligne, au nom de celui qui
    dépose ; ce que la tâche de fond construit ensuite ne publie rien ; un second dépôt dans
    les 10 min rejoint la même ligne."""
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    owner.post("/api/notifications/read-all")
    for name in ("a.png", "b.png"):
        sent = m["editor"].post(
            f"/api/projects/{pid}/images", files={"file": (name, _png(), "image/png")}
        )
        assert sent.status_code == 201, sent.text
    lines = _bell(owner, "unread")["items"]
    assert [(x["type"], x["count"], x["actor_name"]) for x in lines] == [
        ("plan.changed", 2, "editor")
    ]


def test_plan_changes_are_published_with_their_level(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    level = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    op = {"op": "add_wall", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 0}}
    response = m["editor"].post(f"/api/projects/{pid}/levels/{level}/ops", json=op)
    assert response.status_code == 200, response.text
    line = _bell(owner)["items"][0]
    assert line["type"] == "plan.changed" and line["level_id"] == level
    # Renommer le projet n'est pas une modification du plan.
    owner_count = len(_bell(m["editor"])["items"])
    owner.patch(f"/api/projects/{pid}", json={"name": "Salon bleu"})
    assert len(_bell(m["editor"])["items"]) == owner_count
