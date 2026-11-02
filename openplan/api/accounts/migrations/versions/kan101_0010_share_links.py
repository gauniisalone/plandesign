"""KAN-101 : liens publics vers un rendu réaliste (`share_links`), en lecture seule et
révocables ; le jeton n'est gardé qu'en empreinte SHA-256.

Révision : kan101_0010 ; précédente : kan109b_0009.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan101_0010"
down_revision: str | None = "kan109b_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("show_project_name", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_share_links_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["render_requests.id"],
            name="fk_share_links_request_id_render_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_share_links_created_by_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_share_links"),
        sa.UniqueConstraint("token_hash", name="uq_share_links_token_hash"),
    )
    op.create_index("ix_share_links_project", "share_links", ["project_id"])
    op.create_index("ix_share_links_request", "share_links", ["request_id"])
    op.create_index("ix_share_links_created_by", "share_links", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_share_links_created_by", table_name="share_links")
    op.drop_index("ix_share_links_request", table_name="share_links")
    op.drop_index("ix_share_links_project", table_name="share_links")
    op.drop_table("share_links")
