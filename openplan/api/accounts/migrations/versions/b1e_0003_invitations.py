"""B1e : liens d'invitation à un projet (rôle, expiration, usage unique, révocation).

Révision : b1e_0003 ; précédente : b1b2_0002.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "b1e_0003"
down_revision: str | None = "b1b2_0002"
branch_labels = None
depends_on = None


def _ts(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        _ts("created_at"),
        _ts("expires_at"),
        sa.Column("used_by", sa.Uuid(), nullable=True),
        _ts("used_at", nullable=True),
        _ts("revoked_at", nullable=True),
        sa.CheckConstraint(
            "role IN ('viewer', 'commenter', 'editor')", name="ck_invitations_role_invitable"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_invitations_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_invitations_created_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["used_by"], ["users.id"], name="fk_invitations_used_by_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_project_id", "invitations", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_invitations_project_id", table_name="invitations")
    op.drop_table("invitations")
