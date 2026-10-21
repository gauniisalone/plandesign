"""KAN-55 : invitation nominative (par adresse e-mail) ; une colonne `email` sur `invitations`.

Révision : kan55_0005 ; précédente : kan52_0004.
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan55_0005"
down_revision: str | None = "kan52_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invitations") as batch:
        batch.add_column(sa.Column("email", sa.String(length=320), nullable=True))
    op.create_index("ix_invitations_email", "invitations", ["email"])


def downgrade() -> None:
    op.drop_index("ix_invitations_email", table_name="invitations")
    with op.batch_alter_table("invitations") as batch:
        batch.drop_column("email")
