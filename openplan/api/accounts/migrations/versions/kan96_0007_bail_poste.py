"""KAN-96 : battement du poste pour une prise (`render_requests.beat_at`), qui tient le bail.

Révision : kan96_0007 ; précédente : kan89_0006.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan96_0007"
down_revision: str | None = "kan89_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("render_requests") as batch:
        batch.add_column(sa.Column("beat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("render_requests") as batch:
        batch.drop_column("beat_at")
