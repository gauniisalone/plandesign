"""KAN-52 : état « lu » de la cloche (repère par personne, lignes lues une par une).

Révision : kan52_0004 ; précédente : b1e_0003.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan52_0004"
down_revision: str | None = "b1e_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_reads",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("read_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notification_reads_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_notification_reads"),
    )
    op.create_table(
        "notification_read_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notification_read_items_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_notification_read_items_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "event_id", name="pk_notification_read_items"),
    )
    op.create_index(
        "ix_notification_read_items_user_project",
        "notification_read_items",
        ["user_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_read_items_user_project", table_name="notification_read_items")
    op.drop_table("notification_read_items")
    op.drop_table("notification_reads")
