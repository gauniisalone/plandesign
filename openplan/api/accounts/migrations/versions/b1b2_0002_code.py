"""B1b2 : la connexion se fait par code à 6 chiffres ; nombre d'essais faux par code.

Révision : b1b2_0002 ; précédente : b1a_0001.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1b2_0002"
down_revision: str | None = "b1a_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("login_tokens") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("login_tokens") as batch:
        batch.drop_column("attempts")
