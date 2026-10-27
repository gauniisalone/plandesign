"""KAN-109 L1 : audience du site public, en agrégats par jour (aucune donnée personnelle) ;
empreintes et sels du jour (effacés sous 48 h) ; curseurs de lecture du journal de Caddy.

Révision : kan109_0008 ; précédente : kan96_0007.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan109_0008"
down_revision: str | None = "kan96_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_days",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("visitors", sa.Integer(), nullable=False),
        sa.Column("www", sa.Integer(), nullable=False),
        sa.Column("bots", sa.Integer(), nullable=False),
        sa.Column("errors_4xx", sa.Integer(), nullable=False),
        sa.Column("errors_5xx", sa.Integer(), nullable=False),
        sa.Column("mobile", sa.Integer(), nullable=False),
        sa.Column("tablet", sa.Integer(), nullable=False),
        sa.Column("desktop", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", name="pk_site_days"),
    )
    op.create_table(
        "site_day_pages",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "path", name="pk_site_day_pages"),
    )
    op.create_table(
        "site_day_sources",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "source", name="pk_site_day_sources"),
    )
    op.create_table(
        "site_visitors",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("day", "digest", name="pk_site_visitors"),
    )
    op.create_table(
        "site_salts",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("day", name="pk_site_salts"),
    )
    op.create_table(
        "access_log_cursors",
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("inode", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("first_day", sa.String(10), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_access_log_cursors"),
    )


def downgrade() -> None:
    for table in (
        "access_log_cursors",
        "site_salts",
        "site_visitors",
        "site_day_sources",
        "site_day_pages",
        "site_days",
    ):
        op.drop_table(table)
