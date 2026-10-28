"""KAN-109 L2 : événements d'usage de l'application (pseudonymisés, sans contenu), pour les
indicateurs du back-office ; effacés avec le compte et après 13 mois.

Révision : kan109b_0009 ; précédente : kan109_0008.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan109b_0009"
down_revision: str | None = "kan109_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("project", sa.String(32), nullable=True),
        sa.Column("value", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )
    op.create_index("ix_events_name_at", "events", ["name", "at"])
    op.create_index("ix_events_actor_at", "events", ["actor", "at"])


def downgrade() -> None:
    op.drop_index("ix_events_actor_at", table_name="events")
    op.drop_index("ix_events_name_at", table_name="events")
    op.drop_table("events")
