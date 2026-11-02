"""e2e-accounts (KAN-101) : pose un rendu réaliste FAIT dans un projet, comme l'aurait déposé le
poste de rendu (ligne `done` en base, image PNG synthétique dans `realistic/`). La place d'un
Mo des e2e-accounts refuserait une vraie demande. Base et dossier jetables des e2e seulement.

Usage : python seed_done_render.py <url de la base> <dossier des données> <projet> <adresse>
Écrit l'identifiant du rendu sur la sortie standard.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageDraw
from sqlalchemy import select

from openplan.api.accounts.db import make_engine, utcnow
from openplan.api.accounts.models import RenderRequest, User
from openplan.api.accounts.repo import AccountsRepo

WIDTH, HEIGHT = 320, 240


def main(url: str, data_dir: str, project_id: str, email: str) -> int:
    folder = Path(data_dir) / "projects" / project_id
    project = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    level_id, level = next(iter(project["levels"].items()))
    cameras = [k for k, e in level["elements"].items() if e.get("type") == "camera"]
    repo = AccountsRepo(make_engine(url))
    now = utcnow()
    rid = uuid4()
    with repo.session() as db, db.begin():
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None, "compte inconnu"
        db.add(
            RenderRequest(
                id=rid,
                user_id=user.id,
                project_id=UUID(project_id),
                level_id=UUID(level_id),
                camera_id=UUID(cameras[0]) if cameras else uuid4(),
                state="done",
                created_at=now - timedelta(minutes=2),
                expires_at=now + timedelta(hours=1),
                taken_at=now - timedelta(minutes=1),
                finished_at=now,
                attempts=1,
                seconds=41.0,
                width=WIDTH,
                height=HEIGHT,
                engine="fake",
            )
        )
    repo.engine.dispose()
    image = Image.new("RGB", (WIDTH, HEIGHT), (214, 200, 180))
    ImageDraw.Draw(image).rectangle((40, 60, 280, 200), fill=(150, 120, 90))
    (folder / "realistic").mkdir(parents=True, exist_ok=True)
    image.save(folder / "realistic" / f"{rid}.png", format="PNG")
    sys.stdout.write(f"{rid}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
