"""e2e-online (KAN-89) : crée le compte autorisé aux rendus réalistes avec un identifiant connu
d'avance (OPENPLAN_RENDER_ACCOUNTS ne nomme que des uuid). Base jetable des e2e seulement.

Usage : python seed_render_account.py <url de la base> <uuid> <adresse>
"""

from __future__ import annotations

import sys
from uuid import UUID

from openplan.api.accounts.db import make_engine, utcnow
from openplan.api.accounts.models import User
from openplan.api.accounts.repo import AccountsRepo


def main(url: str, user_id: str, email: str) -> int:
    repo = AccountsRepo(make_engine(url))
    with repo.session() as db, db.begin():
        db.add(User(id=UUID(user_id), email=email, name="", is_admin=False, created_at=utcnow()))
    repo.engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
