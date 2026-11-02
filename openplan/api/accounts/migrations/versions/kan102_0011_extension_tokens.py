"""KAN-102 : jetons du module navigateur « Ajouter à PlanDesign » (empreinte seulement).

Révision : kan102_0011 ; précédente : kan101_0010 (liens publics, KAN-101), rechaînée à
l'intégration (numéro réservé par le PO).
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan102_0011"
down_revision: str | None = "kan101_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extension_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_extension_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extension_tokens"),
    )
    op.create_index("ix_extension_tokens_user_id", "extension_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_extension_tokens_user_id", table_name="extension_tokens")
    op.drop_table("extension_tokens")
