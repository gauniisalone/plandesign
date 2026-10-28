"""KAN-109 L2 : événements d'usage pseudonymisés et indicateurs du back-office. Aucune donnée
de contenu ; comptes administrateurs exclus ; seuil de 5 ; effacement ; 13 mois."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select

from openplan.api.accounts.db import current_revision, downgrade, make_engine, upgrade
from openplan.api.accounts.events import CAUSES, EventLog, cause_code, load_secret
from openplan.api.accounts.models import Event
from tests.accounts.helpers import ADMIN, accounts_client, sign_in

MONDAY = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def test_migration_kan109b_goes_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine)
    assert current_revision(engine) == "kan109b_0009"
    assert "events" in inspect(engine).get_table_names()
    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert columns == {"id", "at", "name", "actor", "project", "value"}, "aucun contenu"
    downgrade(engine, "kan109_0008")
    assert "events" not in inspect(engine).get_table_names()
    upgrade(engine)
    assert "events" in inspect(engine).get_table_names()
    engine.dispose()


def _log(url: str, tmp_path: Path, now: list[datetime]) -> EventLog:
    return EventLog(make_engine(url), load_secret(tmp_path), clock=lambda: now[0])


def test_pseudonyms_closed_list_active_once_forget_and_purge(
    tmp_path: Path, migrated_url: str
) -> None:
    now = [MONDAY]
    log = _log(migrated_url, tmp_path, now)
    secret = tmp_path / "events.secret"
    assert secret.stat().st_mode & 0o777 == 0o600
    marie, project = uuid4(), uuid4()
    with pytest.raises(ValueError, match="inconnu"):
        log.emit("photo_uploaded", marie)
    log.emit("project_created", marie, project)
    log.active(marie)
    log.active(marie)  # le même jour : une seule ligne
    with log.session() as db:
        rows = list(db.scalars(select(Event)))
    assert [r.name for r in rows] == ["project_created", "active"]
    assert all(str(marie) not in r.actor and len(r.actor) == 32 for r in rows)
    assert rows[0].project is not None and str(project) not in rows[0].project
    assert rows[0].actor == log.pseudonym(marie), "le même compte, le même pseudonyme"
    # Effacement avec le compte.
    assert log.forget_account(marie) == 2
    # 13 mois : purgés.
    log.emit("signed_in", uuid4())
    now[0] = MONDAY + timedelta(days=400)
    log.purge()
    with log.session() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == 0


def test_an_unwritable_event_never_breaks_the_request(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _log(migrated_url, tmp_path, [MONDAY])

    def broken() -> object:
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(log, "session", broken)
    log.emit("signed_in", uuid4())  # ne lève pas


def test_kpis_two_step_activation_causes_threshold_and_admins_out(
    tmp_path: Path, migrated_url: str
) -> None:
    now = [MONDAY]
    log = _log(migrated_url, tmp_path, now)
    people = [uuid4() for _ in range(6)]
    admin = uuid4()

    def at(days: float) -> None:
        now[0] = MONDAY + timedelta(days=days)

    for p in [*people, admin]:
        at(0)
        log.emit("account_created", p)
    # 4 comptes sur 6 ont un plan sous 7 jours ; 2 d'entre eux un rendu.
    for p in people[:4]:
        at(1)
        log.emit("plan_built", p, uuid4(), 1)
    for p in people[:2]:
        at(2)
        log.emit("render_done", p, uuid4(), 90)
    at(2)
    log.emit("render_requested", people[0], uuid4(), 1)
    log.emit("render_requested", people[1], uuid4(), 0)
    log.emit("render_failed", people[2], uuid4(), cause_code("vram"))
    log.emit("render_failed", people[3], uuid4(), cause_code("lost"))
    log.emit("render_failed", people[3], uuid4(), cause_code("engine_error"))
    # L'administrateur rend beaucoup : il ne compte pas.
    for _ in range(10):
        log.emit("render_done", admin, uuid4(), 1)
    at(8)
    usage = log.usage(2, admins=[admin])
    week = usage["weeks"][0]  # type: ignore[index]
    assert week["signups"] == 6
    assert week["activation"] == {"cohort": 6, "plan": round(4 / 6, 3), "render": round(2 / 6, 3)}
    assert week["first_render_hours"] is None, "moins de 5 premiers rendus : rien"
    renders = week["renders"]
    assert renders["done"] == 2 and renders["failed"] == 3
    assert renders["causes"] == {"lost": 1, "vram": 1, "refused": 0, "other": 1}
    assert renders["success"] == round(2 / 5, 3)
    assert week["usage"]["guided"] is None, "2 demandes : sous le seuil"
    assert week["retention"]["d30"] is None, "la cohorte n'a pas encore 37 jours"
    assert list(renders["causes"]) == list(CAUSES)


def test_admin_route_shows_aggregates_only(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(client, mailer, "marie@example.org")
    made = client.post("/api/projects", json={"name": "Grenier"})
    assert made.status_code == 201
    assert client.get("/api/admin/usage").status_code == 403
    client.cookies.clear()
    sign_in(client, mailer, ADMIN)
    usage = client.get("/api/admin/usage", params={"weeks": 4})
    assert usage.status_code == 200
    body = usage.json()
    assert len(body["weeks"]) == 4 and body["weeks"][-1]["signups"] == 1
    text = json.dumps(body)
    assert not UUID_TEXT.search(text), "aucun identifiant, même pseudonymisé"
    assert "marie" not in text and "Grenier" not in text
    with client.app.state.events.session() as db:  # type: ignore[attr-defined]
        names = sorted(db.scalars(select(Event.name)))
    assert {"account_created", "signed_in", "project_created", "active"} <= set(names)
    del service
