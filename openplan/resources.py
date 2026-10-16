"""Ressources livrées avec le paquet (cadrage 0.4, 4.10b).

En développement, elles sont lues dans le dépôt (`models/registry.yaml`, `assets/catalog.yaml`,
`blender/scripts/build_scene.py`, `apps/web/dist`). Dans le paquet installé (`uv tool install
openplan-studio`), `make build` en a copié une version dans `openplan/_bundled/`, que le wheel
embarque (`[tool.hatch.build] artifacts`). Les poids des modèles, eux, ne sont jamais embarqués.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
BUNDLED = PACKAGE_DIR / "_bundled"


def resource(*relative: str, repo_root: Path = REPO_ROOT, bundled: Path = BUNDLED) -> Path:
    """Chemin d'une ressource : celle du dépôt si elle existe, sinon la copie embarquée
    (même chemin relatif sous `openplan/_bundled/`)."""
    candidate = repo_root.joinpath(*relative)
    if candidate.exists():
        return candidate
    return bundled.joinpath(*relative)


def bundled_web_dir(repo_root: Path = REPO_ROOT, bundled: Path = BUNDLED) -> Path:
    """Front construit : `apps/web/dist` dans un dépôt (même non construit : le message le
    dit), sinon la copie embarquée `openplan/_bundled/web`."""
    checkout = repo_root / "apps" / "web"
    if checkout.is_dir():
        return checkout / "dist"
    return bundled / "web"
