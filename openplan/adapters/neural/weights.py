"""Poids de modèles : emplacement local, présence, licence acceptée, téléchargement explicite.

Règle du brief : aucun téléchargement implicite. `pull` installe des poids avec le
téléchargeur qu'on lui passe (`adapters.neural.download`), et n'est appelé que par
`openplan models pull <id>`. Certains poids (OpenRAIL-M) exigent que l'utilisateur accepte
la licence : `pull` refuse sans `accept_license`, et écrit alors un marqueur horodaté que
l'adaptateur vérifie avant de charger les poids. Ce module n'importe aucun client réseau.
Le dossier des poids (`OPENPLAN_MODELS_DIR`, défaut `models/weights`) est ignoré par git.
"""

from __future__ import annotations

import hashlib
import os
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
    files: dict[str, str] | None = None,
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
    if files:
        bad = mismatched(target, files)
        if bad:
            msg = (
                f"empreinte sha256 fausse ou fichier absent après téléchargement : {', '.join(bad)}"
            )
            raise AdapterUnavailableError(msg)
    if license_name:
        (target / LICENSE_MARKER).write_text(
            f"{license_name}\naccepted_at: {datetime.now(UTC).isoformat()}\nrepo: {repo_id}\n",
            encoding="utf-8",
        )
    return target


def sha256_of(path: Path, chunk: int = 1 << 24) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def mismatched(folder: Path, files: dict[str, str]) -> list[str]:
    """Fichiers attendus absents de `folder` ou dont l'empreinte sha256 diffère."""
    return [
        name
        for name, digest in files.items()
        if not (folder / name).is_file() or sha256_of(folder / name) != digest
    ]


def link_from(model_id: str, root: Path, source: Path, files: dict[str, str]) -> Path:
    """KAN-88 : reprend des poids déjà présents sur la machine au lieu de les retélécharger.
    Toutes les empreintes sha256 de `source` sont vérifiées d'abord (refus sinon, rien n'est
    lié) ; chaque fichier est ensuite lié en dur (même disque : aucune place en plus), sinon
    par lien symbolique. Renvoie le dossier du modèle."""
    bad = mismatched(source, files)
    if bad:
        msg = f"{source} : empreinte sha256 fausse ou fichier absent : {', '.join(bad)}"
        raise AdapterUnavailableError(msg)
    target = weights_dir(model_id, root)
    for name in files:
        src, dst = source / name, target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            if dst.resolve() == src.resolve() or (
                dst.is_file() and dst.stat().st_ino == src.stat().st_ino
            ):
                continue
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            dst.symlink_to(src.resolve())
    return target
