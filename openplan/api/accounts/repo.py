"""Lectures et écritures des comptes, projets et membres (sans HTTP)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.models import ROLES, Member, ProjectRow, User

PROJECTS_PREFIX = "projects"
"""Les projets d'un serveur à comptes vivent sous `<data>/projects/<id>/` (clé de stockage)."""


def normalize_email(email: str) -> str:
    """Minuscules, espaces retirés (note 17, § 2) : la clé unique du compte."""
    return "".join(email.split()).lower()


def storage_key(project_id: UUID) -> str:
    return f"{PROJECTS_PREFIX}/{project_id}"


class AccountsRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def session(self) -> Session:
        return self.sessions()

    # --- utilisateurs -----------------------------------------------------------------

    def user_by_email(self, email: str) -> User | None:
        with self.session() as db:
            return db.scalar(select(User).where(User.email == normalize_email(email)))

    def user(self, user_id: UUID) -> User | None:
        with self.session() as db:
            return db.get(User, user_id)

    def create_user(self, email: str, *, name: str = "", is_admin: bool = False) -> User:
        user = User(email=normalize_email(email), name=name, is_admin=is_admin, created_at=utcnow())
        with self.session() as db, db.begin():
            db.add(user)
        return user

    # --- projets et membres -----------------------------------------------------------

    def add_project(self, project_id: UUID, owner_id: UUID, name: str) -> ProjectRow:
        """Ligne du projet et son propriétaire comme premier membre (`owner`)."""
        now = utcnow()
        row = ProjectRow(
            id=project_id,
            owner_id=owner_id,
            name=name[:200],
            created_at=now,
            storage_key=storage_key(project_id),
        )
        with self.session() as db, db.begin():
            db.add(row)
            db.flush()
            db.add(
                Member(
                    project_id=project_id,
                    user_id=owner_id,
                    role="owner",
                    added_by=owner_id,
                    added_at=now,
                )
            )
        return row

    def remove_project(self, project_id: UUID) -> None:
        with self.session() as db, db.begin():
            db.execute(delete(Member).where(Member.project_id == project_id))
            db.execute(delete(ProjectRow).where(ProjectRow.id == project_id))

    def set_member(
        self, project_id: UUID, user_id: UUID, role: str, *, added_by: UUID | None
    ) -> None:
        if role not in ROLES:
            msg = f"rôle inconnu : {role}"
            raise ValueError(msg)
        with self.session() as db, db.begin():
            member = db.get(Member, (project_id, user_id))
            if member is None:
                db.add(
                    Member(
                        project_id=project_id,
                        user_id=user_id,
                        role=role,
                        added_by=added_by,
                        added_at=utcnow(),
                    )
                )
            else:
                member.role = role

    def remove_member(self, project_id: UUID, user_id: UUID) -> None:
        with self.session() as db, db.begin():
            db.execute(
                delete(Member).where(Member.project_id == project_id, Member.user_id == user_id)
            )

    def role_name(self, project_id: UUID, user_id: UUID) -> str | None:
        with self.session() as db:
            member = db.get(Member, (project_id, user_id))
            return None if member is None else member.role

    def project_ids_of(self, user_id: UUID) -> set[UUID]:
        with self.session() as db:
            return set(db.scalars(select(Member.project_id).where(Member.user_id == user_id)))
