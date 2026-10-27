"""Tables des comptes (note 17, § 8) : utilisateurs, liens de connexion, sessions, projets
(index et droits seulement : le projet reste un fichier du stockage) et membres.

Les jetons (lien de connexion, session) ne sont gardés qu'en empreinte SHA-256 : une fuite de
la base ne donne ni lien ni session utilisable. Les dates sont en UTC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("viewer", "commenter", "editor", "owner")
INVITE_ROLES = ("viewer", "commenter", "editor")
"""Rôles qu'un lien d'invitation peut donner (B1e) : jamais `owner`."""

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _utc() -> DateTime:
    return DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    """Adresse normalisée (minuscules, sans espaces) : la clé du compte."""
    name: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(_utc())
    last_seen_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)


class LoginToken(Base):
    """Code de connexion envoyé par e-mail (B1b2 ; un lien avant) : 6 chiffres, usage unique,
    15 min, 5 essais au plus ; gardé en empreinte salée par l'identifiant de la ligne et
    l'adresse (`token_hash`)."""

    __tablename__ = "login_tokens"
    __table_args__ = (Index("ix_login_tokens_email_created", "email", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    next: Mapped[str] = mapped_column(String(512), default="/")
    created_at: Mapped[datetime] = mapped_column(_utc())
    expires_at: Mapped[datetime] = mapped_column(_utc())
    used_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Adresse IP tronquée (/24 en IPv4, /48 en IPv6) : assez pour comprendre un abus."""
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """B1b2 : essais de code faux ; au 5e, le code est révoqué."""


class AuthSession(Base):
    """Session d'un navigateur (cookie `pd_session`) : 30 jours glissants, révocable."""

    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(_utc())
    last_used_at: Mapped[datetime] = mapped_column(_utc())
    expires_at: Mapped[datetime] = mapped_column(_utc())
    revoked_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class ProjectRow(Base):
    """Index et droits d'un projet ; `project.json` et ses fichiers restent dans le stockage."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(_utc())
    storage_key: Mapped[str] = mapped_column(String(200))


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'commenter', 'editor', 'owner')", name="role_known"),
    )

    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    added_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(_utc())


class Invitation(Base):
    """Lien d'invitation à un projet (B1e) : un rôle, une expiration, usage unique, révocable.
    Le jeton (32 octets) n'est gardé qu'en empreinte ; il voyage dans l'ancre du lien
    (`#/invitation/<jeton>`), jamais dans une URL envoyée au serveur, jamais par e-mail."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'commenter', 'editor')", name="role_invitable"),
        Index("ix_invitations_email", "email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(_utc())
    expires_at: Mapped[datetime] = mapped_column(_utc())
    used_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    """Révoquée par le propriétaire, ou refusée par l'invité (KAN-55)."""
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    """KAN-55 : l'adresse invitée (normalisée), pour une invitation nominative ; None pour un
    lien. Montrée au seul propriétaire, jamais journalisée."""


class NotificationRead(Base):
    """KAN-52 : « Tout marquer comme lu » d'une personne (un repère, tous projets confondus)."""

    __tablename__ = "notification_reads"

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    read_until: Mapped[datetime] = mapped_column(_utc())


class NotificationReadItem(Base):
    """KAN-52 : une ligne de la cloche lue une par une (l'id de son geste le plus récent)."""

    __tablename__ = "notification_read_items"
    __table_args__ = (Index("ix_notification_read_items_user_project", "user_id", "project_id"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    event_at: Mapped[datetime] = mapped_column(_utc())


RENDER_STATES = ("pending", "taken", "done", "failed", "expired")


class RenderRequest(Base):
    """KAN-89 : une demande de rendu réaliste d'une vue, calculée par le poste de rendu.
    Stockée sans calcul ; le poste la prend (`taken`, avec un bail), puis la dépose (`done`)
    ou signale un échec (`failed`) ; non prise à temps, elle expire (`expired`)."""

    __tablename__ = "render_requests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'taken', 'done', 'failed', 'expired')", name="state_known"
        ),
        Index("ix_render_requests_state_created", "state", "created_at"),
        Index("ix_render_requests_user_created", "user_id", "created_at"),
        Index("ix_render_requests_project", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    level_id: Mapped[UUID] = mapped_column(Uuid)
    camera_id: Mapped[UUID] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(_utc())
    expires_at: Mapped[datetime] = mapped_column(_utc())
    """Au-delà, une demande encore en attente expire."""
    taken_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    lease_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    """Bail de la prise en cours : exigé pour télécharger, déposer ou signaler un échec."""
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    finished_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """Code d'échec (`FAILURE_TEXT`) ; jamais un texte libre."""
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    vram_peak_mib: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    beat_at: Mapped[datetime | None] = mapped_column(_utc(), nullable=True)
    """KAN-96 : dernier battement du poste pour cette prise ; None pour un poste qui n'a pas
    annoncé savoir battre (seul le délai de reprise `TAKE_TTL` vaut alors)."""


class RenderWorker(Base):
    """KAN-89 : dernier battement de cœur du poste de rendu (une ligne par poste)."""

    __tablename__ = "render_workers"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(_utc())
    state: Mapped[str] = mapped_column(String(16))


# --- KAN-109 : audience du site public (agrégats par jour, aucune donnée personnelle) -------


class SiteDay(Base):
    """Une ligne par jour (UTC) : vues de pages de plandesign.fr, visiteurs uniques (figés au
    jour clos), arrivées par www (301), robots, erreurs, appareils."""

    __tablename__ = "site_days"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    """AAAA-MM-JJ (UTC)."""
    views: Mapped[int] = mapped_column(Integer, default=0)
    visitors: Mapped[int] = mapped_column(Integer, default=0)
    www: Mapped[int] = mapped_column(Integer, default=0)
    bots: Mapped[int] = mapped_column(Integer, default=0)
    errors_4xx: Mapped[int] = mapped_column(Integer, default=0)
    errors_5xx: Mapped[int] = mapped_column(Integer, default=0)
    mobile: Mapped[int] = mapped_column(Integer, default=0)
    tablet: Mapped[int] = mapped_column(Integer, default=0)
    desktop: Mapped[int] = mapped_column(Integer, default=0)


class SiteDayPage(Base):
    """Vues par page (chemin sans la requête) et par jour."""

    __tablename__ = "site_day_pages"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    path: Mapped[str] = mapped_column(String(300), primary_key=True)
    views: Mapped[int] = mapped_column(Integer, default=0)


class SiteDaySource(Base):
    """Vues par source et par jour : le DOMAINE du référent (jamais son chemin) ou
    `utm:<utm_source>`."""

    __tablename__ = "site_day_sources"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    source: Mapped[str] = mapped_column(String(200), primary_key=True)
    views: Mapped[int] = mapped_column(Integer, default=0)


class SiteVisitor(Base):
    """Empreintes du jour (sel du jour, IP déjà tronquée par Caddy, User-Agent) : servent
    seulement à compter les visiteurs uniques ; effacées avec le sel sous 48 h."""

    __tablename__ = "site_visitors"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)


class SiteSalt(Base):
    """Sel aléatoire d'un jour ; effacé avec les empreintes de ce jour (48 h au plus)."""

    __tablename__ = "site_salts"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    salt: Mapped[str] = mapped_column(String(64))


class AccessLogCursor(Base):
    """Où en est la lecture d'un fichier du journal : le courant par inode et position, un
    fichier tourné (.gz) une fois lu (`done`)."""

    __tablename__ = "access_log_cursors"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    inode: Mapped[int] = mapped_column(BigInteger, default=0)
    position: Mapped[int] = mapped_column(BigInteger, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    first_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    """Premier jour lu dans ce fichier (« depuis le JJ/MM » du back-office)."""
