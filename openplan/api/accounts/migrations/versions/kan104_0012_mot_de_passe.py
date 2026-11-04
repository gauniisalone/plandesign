"""KAN-104 : connexion par mot de passe et inscription (désactivées par défaut).

- `users` : empreinte argon2id du mot de passe (null : pas de mot de passe), date de pose,
  échecs et délai progressif ; adresse prouvée (remplie avec `created_at` pour les comptes
  existants, qui l'ont tous prouvée par un code) ; IP tronquée et CGU acceptées (inscription).
- `login_tokens.purpose` : `login` (défaut, les codes existants), `signup`, `reset`, `reauth` ;
  un code ne sert qu'à son usage.
- `sessions` : méthode de connexion (`code` par défaut) et dernière preuve récente.

Révision : kan104_0012 ; précédente : kan115_0013 (rechaînée pour la MEP 37, après KAN-101,
KAN-102 et KAN-115). Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan104_0012"
down_revision: str | None = "kan115_0013"
branch_labels = None
depends_on = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(255), nullable=True))
        batch.add_column(sa.Column("password_set_at", _UTC, nullable=True))
        batch.add_column(
            sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("locked_until", _UTC, nullable=True))
        batch.add_column(sa.Column("email_verified_at", _UTC, nullable=True))
        batch.add_column(sa.Column("signup_ip", sa.String(64), nullable=True))
        batch.add_column(sa.Column("accepted_terms_at", _UTC, nullable=True))
        batch.add_column(sa.Column("terms_version", sa.String(16), nullable=True))
    op.execute("UPDATE users SET email_verified_at = created_at")
    with op.batch_alter_table("login_tokens") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="login")
        )
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(
            sa.Column("auth_method", sa.String(16), nullable=False, server_default="code")
        )
        batch.add_column(sa.Column("reauth_at", _UTC, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("reauth_at")
        batch.drop_column("auth_method")
    with op.batch_alter_table("login_tokens") as batch:
        batch.drop_column("purpose")
    # `users` sans batch : sous SQLite, batch recrée la table (copie, DROP, renommage) et, avec
    # `PRAGMA foreign_keys=ON`, le DROP passe par un DELETE implicite que refusent les tables qui
    # la visent (membres, rendus...). ALTER TABLE ... DROP COLUMN (SQLite ≥ 3.35, Postgres) ne
    # touche à aucune ligne.
    for column in (
        "terms_version",
        "accepted_terms_at",
        "signup_ip",
        "email_verified_at",
        "locked_until",
        "failed_logins",
        "password_set_at",
        "password_hash",
    ):
        op.drop_column("users", column)
