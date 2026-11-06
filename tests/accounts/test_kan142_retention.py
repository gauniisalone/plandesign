"""KAN-142 : la purge quotidienne tient les durées promises par la page de confidentialité.
Un test par table, des deux côtés du délai ; une ligne de journal par purge, des nombres
seulement."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from openplan.api.accounts.db import make_engine
from openplan.api.accounts.models import AuthSession, Invitation, LoginToken, ProjectRow, User
from openplan.api.accounts.retention import (
    CODES_KEEP,
    INVITATIONS_KEEP,
    SESSIONS_KEEP,
    Retention,
)

NOW = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
JUST = timedelta(minutes=1)
"""Une minute de part et d'autre du délai."""
EMAIL = "marie@example.org"


def _user(db: Session) -> UUID:
    user = User(email=EMAIL, created_at=NOW - timedelta(days=90))
    db.add(user)
    db.flush()
    return user.id


def _project(db: Session, owner: UUID) -> UUID:
    project = ProjectRow(id=uuid4(), owner_id=owner, created_at=NOW, storage_key="projects/x")
    db.add(project)
    db.flush()
    return project.id


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _token() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def engine(migrated_url: str) -> Engine:
    return make_engine(migrated_url)


def test_codes_are_erased_after_24_hours(engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        for age, purpose in (
            (CODES_KEEP + JUST, "login"),
            (CODES_KEEP + JUST, "reset"),
            (CODES_KEEP - JUST, "login"),
        ):
            created = NOW - age
            db.add(
                LoginToken(
                    email=EMAIL,
                    token_hash=_token(),
                    created_at=created,
                    expires_at=created + timedelta(minutes=15),
                    purpose=purpose,
                )
            )
    done = Retention(engine, clock=lambda: NOW).purge()
    assert done.login_tokens == 2
    assert _count(engine, LoginToken) == 1, "le code de moins de 24 heures reste"


def test_sessions_are_erased_30_days_after_the_last_visit(engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        user = _user(db)
        for last, revoked in (
            (NOW - SESSIONS_KEEP - JUST, None),  # plus de 30 jours : part
            (NOW - SESSIONS_KEEP - JUST, NOW - timedelta(days=40)),  # révoquée, ancienne : part
            (NOW - SESSIONS_KEEP + JUST, NOW - timedelta(days=2)),  # révoquée, récente : reste
            (NOW - SESSIONS_KEEP + JUST, None),  # moins de 30 jours : reste
        ):
            db.add(
                AuthSession(
                    user_id=user,
                    token_hash=_token(),
                    created_at=last - timedelta(days=60),
                    last_used_at=last,
                    expires_at=last + SESSIONS_KEEP,
                    revoked_at=revoked,
                )
            )
    done = Retention(engine, clock=lambda: NOW).purge()
    assert done.sessions == 2
    assert _count(engine, AuthSession) == 2


def test_invitations_are_erased_30_days_after_creation(engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        owner = _user(db)
        project = _project(db, owner)
        for age, email, used in (
            (INVITATIONS_KEEP + JUST, None, None),  # lien expiré : part
            (INVITATIONS_KEEP + JUST, "paul@example.org", NOW - timedelta(days=29)),  # utilisée
            (INVITATIONS_KEEP - JUST, None, None),  # lien de 30 jours encore valable : reste
        ):
            created = NOW - age
            db.add(
                Invitation(
                    project_id=project,
                    role="viewer",
                    token_hash=_token(),
                    created_by=owner,
                    created_at=created,
                    expires_at=created + timedelta(days=30),
                    used_at=used,
                    email=email,
                )
            )
    done = Retention(engine, clock=lambda: NOW).purge()
    assert done.invitations == 2
    assert _count(engine, Invitation) == 1


def test_one_log_line_per_purge_with_counts_only(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    with Session(engine) as db, db.begin():
        created = NOW - CODES_KEEP - JUST
        db.add(
            LoginToken(
                email=EMAIL,
                token_hash=_token(),
                created_at=created,
                expires_at=created + timedelta(minutes=15),
            )
        )
    with caplog.at_level(logging.INFO, logger="openplan.retention"):
        Retention(engine, clock=lambda: NOW).purge()
    lines = [r.getMessage() for r in caplog.records if r.name == "openplan.retention"]
    assert lines == ["purge des durées de conservation : codes 1, sessions 0, invitations 0"]
    assert "@" not in lines[0]


def test_nothing_else_is_touched(engine: Engine) -> None:
    """Ce que la page ne promet pas (demandes de rendu, liens publics) reste : la purge n'efface
    que les trois tables."""
    import inspect

    from openplan.api.accounts import retention

    source = inspect.getsource(retention.Retention.purge)
    assert source.count("delete(") == 3
    for model in ("RenderRequest", "ShareLink", "NotificationRead", "Event"):
        assert model not in source
