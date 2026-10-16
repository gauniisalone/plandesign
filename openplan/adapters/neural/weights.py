"""Poids de modèles : emplacement local, présence, licence acceptée, téléchargement explicite.

Règle du brief : aucun téléchargement implicite. `pull` installe des poids avec le
téléchargeur qu'on lui passe (`adapters.neural.download`), et n'est appelé que par
`openplan models pull <id>`. Certains poids (OpenRAIL-M) exigent que l'utilisateur accepte
la licence : `pull` refuse sans `accept_license`, et écrit alors un marqueur horodaté que
l'adaptateur vérifie avant de charger les poids. Ce module n'importe aucun client réseau.
Le dossier des poids (`OPENPLAN_MODELS_DIR`, défaut `models/weights`) est ignoré par git.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from openplan.adapters.protocols import AdapterUnavailableError

DEFAULT_WEIGHTS_ROOT = Path("models") / "weights"
SENTINEL = "model.safetensors"
"""Fichier dont la présence signe une installation complète (par défaut ; un modèle peut en
désigner un autre, ex. `unet/diffusion_pytorch_model.fp16.safetensors`)."""
LICENSE_MARKER = "LICENSE_ACCEPTED"


class LicenseNotAcceptedError(AdapterUnavailableError):
    """Les poids exigent l'acceptation explicite de leur licence par l'utilisateur."""


def weights_dir(model_id: str, root: Path = DEFAULT_WEIGHTS_ROOT) -> Path:
    """Un dossier par modèle, nommé d'après son identifiant de registre."""
    return root / model_id.replace("@", "_").replace("/", "_")


def is_installed(
    model_id: str, root: Path = DEFAULT_WEIGHTS_ROOT, sentinel: str = SENTINEL
) -> bool:
    return (weights_dir(model_id, root) / sentinel).is_file()


def license_accepted(model_id: str, root: Path = DEFAULT_WEIGHTS_ROOT) -> bool:
    return (weights_dir(model_id, root) / LICENSE_MARKER).is_file()


def require_installed(
    model_id: str,
    root: Path = DEFAULT_WEIGHTS_ROOT,
    sentinel: str = SENTINEL,
    *,
    license_required: bool = False,
) -> Path:
    path = weights_dir(model_id, root)
    if not (path / sentinel).is_file():
        msg = (
            f"poids absents pour {model_id} ({path}). "
            f"Téléchargement explicite : uv run openplan models pull {model_id}"
        )
        raise AdapterUnavailableError(msg)
    if license_required and not (path / LICENSE_MARKER).is_file():
        msg = (
            f"licence non acceptée pour {model_id} : relancer "
            f"uv run openplan models pull {model_id} --accept-license"
        )
        raise LicenseNotAcceptedError(msg)
    return path


Downloader = Callable[[str, Path, list[str] | None], None]


def pull(
    model_id: str,
    repo_id: str,
    root: Path,
    *,
    downloader: Downloader,
    sentinel: str = SENTINEL,
    allow_patterns: list[str] | None = None,
    license_name: str | None = None,
    accept_license: bool = False,
) -> Path:
    """Installe les poids d'un modèle du registre dans son dossier via `downloader`
    (`adapters.neural.download.hf_download` en pratique). Action explicite, jamais appelée
    par le pipeline. Si `license_name` est donné, rien n'est téléchargé sans
    `accept_license` ; l'acceptation est consignée dans le dossier. Renvoie le dossier."""
    if license_name and not accept_license:
        msg = (
            f"{model_id} est distribué sous {license_name} : lire la licence et relancer avec "
            "--accept-license pour télécharger"
        )
        raise LicenseNotAcceptedError(msg)
    target = weights_dir(model_id, root)
    target.mkdir(parents=True, exist_ok=True)
    downloader(repo_id, target, allow_patterns)
    if not (target / sentinel).is_file():
        msg = f"téléchargement incomplet : {sentinel} absent dans {target}"
        raise AdapterUnavailableError(msg)
    if license_name:
        (target / LICENSE_MARKER).write_text(
            f"{license_name}\naccepted_at: {datetime.now(UTC).isoformat()}\nrepo: {repo_id}\n",
            encoding="utf-8",
        )
    return target
