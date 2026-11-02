"""KAN-99 : les rendus étoilés d'un projet (« Favoris » du node Rendus).

Un fichier à côté de la couverture, `realistic/favorites.json` : la liste des demandes étoilées,
sans auteur ni date (rien de personnel : l'effacement d'un compte, KAN-114, n'a rien à y
réécrire). Hors du format du projet (`project.json`, `board.json`) : ni version de schéma ni
migration. Lu et écrit sous le verrou du projet par l'appelant ; écriture atomique. Le fichier
est dans le dossier du projet : il compte dans la place du propriétaire (quelques octets), mais
une étoile ne crée aucune demande et ne touche donc pas au quota des rendus du jour.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from openplan.schemas.render_queue import MAX_FAVORITES, RealisticFavorites

FAVORITES = Path("realistic") / "favorites.json"


def load_favorites(project_dir: Path) -> list[UUID]:
    """Les rendus étoilés, dans l'ordre où ils l'ont été ; un fichier absent ou illisible : []."""
    path = project_dir / FAVORITES
    if not path.is_file():
        return []
    try:
        data = RealisticFavorites.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return []
    return list(dict.fromkeys(data.favorite_ids))


def save_favorites(project_dir: Path, ids: Iterable[UUID]) -> list[UUID]:
    """Écrit la liste (sans doublon, plafonnée aux plus récentes) ; renvoie ce qui est gardé."""
    kept = list(dict.fromkeys(ids))[-MAX_FAVORITES:]
    path = project_dir / FAVORITES
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    tmp.write_text(
        RealisticFavorites(favorite_ids=kept).model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    return kept
