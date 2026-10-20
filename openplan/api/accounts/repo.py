"""Lectures et écritures des comptes, projets et membres (sans HTTP)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.models import ROLES, Invitation, Member, ProjectRow, User

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
            db.execute(delete(Invitation).where(Invitation.project_id == project_id))
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

    def roles_of(self, user_id: UUID) -> dict[UUID, str]:
        """Rôle de l'utilisateur sur chacun de ses projets (galerie, B1e)."""
        with self.session() as db:
            rows = db.execute(
                select(Member.project_id, Member.role).where(Member.user_id == user_id)
            )
            return {project_id: role for project_id, role in rows}

    def display_of(self, user_id: UUID) -> str | None:
        """KAN-51 : le nom montré aux autres (celui du compte, sinon le début de l'adresse avant
        le @, jamais l'adresse entière) ; None si le compte n'existe plus."""
        user = self.user(user_id)
        if user is None:
            return None
        name = (user.name or "").strip()
        return name[:80] if name else user.email.split("@", 1)[0][:40]

    def payer_of(self, project_id: UUID) -> UUID | None:
        """KAN-12 : le compte dont la place compte pour ce projet, son premier propriétaire."""
        with self.session() as db:
            first: UUID | None = db.scalar(
                select(Member.user_id)
                .where(Member.project_id == project_id, Member.role == "owner")
                .order_by(Member.added_at)
                .limit(1)
            )
            return first

    def paid_project_ids(self, user_id: UUID) -> set[UUID]:
        """KAN-12 : les projets dont ce compte est le premier propriétaire."""
        owned = [pid for pid, role in self.roles_of(user_id).items() if role == "owner"]
        return {pid for pid in owned if self.payer_of(pid) == user_id}

    def project_exists(self, project_id: UUID) -> bool:
        with self.session() as db:
            return db.get(ProjectRow, project_id) is not None

    def project_ids_of(self, user_id: UUID) -> set[UUID]:
        with self.session() as db:
            return set(db.scalars(select(Member.project_id).where(Member.user_id == user_id)))
