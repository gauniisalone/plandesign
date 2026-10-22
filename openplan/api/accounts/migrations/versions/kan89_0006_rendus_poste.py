"""KAN-89 : file des rendus réalistes calculés par le poste d'augus (`render_requests`) et
battement de cœur du poste (`render_workers`).

Révision : kan89_0006 ; précédente : kan55_0005.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan89_0006"
down_revision: str | None = "kan55_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("level_id", sa.Uuid(), nullable=False),
        sa.Column("camera_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=32), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("vram_peak_mib", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("engine", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'taken', 'done', 'failed', 'expired')",
            name="ck_render_requests_state_known",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_render_requests_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_render_requests_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_render_requests"),
    )
    op.create_index("ix_render_requests_state_created", "render_requests", ["state", "created_at"])
    op.create_index("ix_render_requests_user_created", "render_requests", ["user_id", "created_at"])
    op.create_index("ix_render_requests_project", "render_requests", ["project_id"])
    op.create_table(
        "render_workers",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_render_workers"),
    )


def downgrade() -> None:
    op.drop_table("render_workers")
    op.drop_index("ix_render_requests_project", table_name="render_requests")
    op.drop_index("ix_render_requests_user_created", table_name="render_requests")
    op.drop_index("ix_render_requests_state_created", table_name="render_requests")
    op.drop_table("render_requests")
