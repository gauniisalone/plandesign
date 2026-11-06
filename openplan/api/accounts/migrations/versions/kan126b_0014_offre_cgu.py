"""Lot schéma du 6 nov. (KAN-126b, KAN-133) : l'offre du compte et la date d'acceptation des CGU.

- `users.offer` : `essentiel` ou `pro` ; `pro` par défaut, côté serveur aussi, pour les comptes
  existants comme pour les nouveaux (décision du PO : personne ne perd rien tant qu'augus n'a
  rien décidé). Posée par l'administrateur (PATCH /api/admin/users/{id}/offer). Pas de
  contrainte CHECK : sous SQLite, l'ajouter recréerait la table `users` (voir la descente de
  kan104_0012) ; la valeur est bornée par l'application (`policy.offers.OFFERS`).
- `users.terms_accepted_at` : la date de la DERNIÈRE acceptation des conditions, par l'une des
  trois voies de KAN-133 (inscription par mot de passe, connexion par code, nouvelle
  acceptation). DISTINCTE de `accepted_terms_at` (kan104_0012), qui reste le repère des comptes
  inscrits par mot de passe et de leurs quotas. Remplie pour les comptes existants avec
  `accepted_terms_at` quand elle existe ; sinon nulle (la date d'une acceptation passée par code
  n'a pas été gardée).

KAN-145 (« owner.changed ») n'a pas de colonne : le journal du projet est un fichier.

Révision : kan126b_0014 ; précédente : kan104_0012. Une migration publiée n'est jamais modifiée
(note 17, § 8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "kan126b_0014"
down_revision: str | None = "kan104_0012"
branch_labels = None
depends_on = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("offer", sa.String(16), nullable=False, server_default="pro"))
        batch.add_column(sa.Column("terms_accepted_at", _UTC, nullable=True))
    op.execute(
        "UPDATE users SET terms_accepted_at = accepted_terms_at WHERE accepted_terms_at IS NOT NULL"
    )


def downgrade() -> None:
    # Sans batch, comme kan104_0012 : ALTER TABLE ... DROP COLUMN ne touche à aucune ligne.
    for column in ("terms_accepted_at", "offer"):
        op.drop_column("users", column)
