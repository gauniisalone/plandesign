"""B1a : utilisateurs, liens de connexion, sessions, projets et membres.

Révision : b1a_0001 ; précédente : aucune.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "b1a_0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def _ts(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        _ts("created_at"),
        _ts("last_seen_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "login_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("next", sa.String(512), nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        _ts("used_at", nullable=True),
        _ts("revoked_at", nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_login_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_login_tokens_token_hash"),
    )
    op.create_index("ix_login_tokens_email_created", "login_tokens", ["email", "created_at"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _ts("created_at"),
        _ts("last_used_at"),
        _ts("expires_at"),
        _ts("revoked_at", nullable=True),
        sa.Column("user_agent", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        _ts("created_at"),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_projects_owner_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table(
        "members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        _ts("added_at"),
        sa.CheckConstraint(
            "role IN ('viewer', 'commenter', 'editor', 'owner')", name="ck_members_role_known"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_members_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_members_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name="fk_members_added_by_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("project_id", "user_id", name="pk_members"),
    )
    op.create_index("ix_members_user_id", "members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_members_user_id", table_name="members")
    op.drop_table("members")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_login_tokens_email_created", table_name="login_tokens")
    op.drop_table("login_tokens")
    op.drop_table("users")
