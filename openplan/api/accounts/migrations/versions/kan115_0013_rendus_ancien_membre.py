"""KAN-115 : les rendus d'un compte effacé restent dans leur projet (auteur « Ancien membre »).

`render_requests.user_id` passe de `ON DELETE CASCADE` à `ON DELETE SET NULL` et devient
nullable : effacer un compte (KAN-114) ne supprime plus ses rendus, montrés à tous les membres
du projet depuis KAN-99. Une demande sans lanceur n'a plus personne pour la variante, la
couverture, le partage (KAN-101) ni l'annulation. Les liens publics que le compte avait créés
partent toujours avec lui (`share_links.created_by` reste en cascade, KAN-101).

Descente : la colonne redevient obligatoire et en cascade ; les demandes sans lanceur (rendus
d'anciens membres) sont d'abord SUPPRIMÉES, comme l'aurait fait l'effacement avant KAN-115,
avec les liens publics qui les visaient ; leurs images restent sur le disque du projet.

Révision : kan115_0013. La précédente est `down_revision`, seule ligne à rechaîner à
l'intégration (après KAN-102, kan102_0011).
Une migration publiée n'est jamais modifiée (note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan115_0013"
down_revision: str | None = "kan102_0011"
branch_labels = None
depends_on = None

FK = "fk_render_requests_user_id_users"
KEPT = "_kan115_share_links"


def _user_fk(*, nullable: bool, ondelete: str) -> None:
    """Refait la clé étrangère du lanceur.

    SQLite : `batch_alter_table` recrée `render_requests` (copie, DROP, renommage) ; avec
    `PRAGMA foreign_keys=ON` (accounts.db), le DROP supprimerait en cascade tous les liens
    publics (`share_links.request_id`, KAN-101). Ils sont mis de côté puis remis, dans la même
    transaction. Postgres : simple ALTER, rien à garder."""
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        op.execute(sa.text(f"CREATE TEMP TABLE {KEPT} AS SELECT * FROM share_links"))
    with op.batch_alter_table("render_requests") as batch:
        batch.drop_constraint(FK, type_="foreignkey")
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=nullable)
        batch.create_foreign_key(FK, "users", ["user_id"], ["id"], ondelete=ondelete)
    if sqlite:
        op.execute(sa.text(f"DELETE FROM share_links WHERE id IN (SELECT id FROM {KEPT})"))
        op.execute(sa.text(f"INSERT INTO share_links SELECT * FROM {KEPT}"))
        op.execute(sa.text(f"DROP TABLE {KEPT}"))


def upgrade() -> None:
    _user_fk(nullable=True, ondelete="SET NULL")


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM share_links WHERE request_id IN "
            "(SELECT id FROM render_requests WHERE user_id IS NULL)"
        )
    )
    op.execute(sa.text("DELETE FROM render_requests WHERE user_id IS NULL"))
    _user_fk(nullable=False, ondelete="CASCADE")
