"""Téléchargement de poids depuis Hugging Face : seul module du projet à importer
`huggingface_hub` (contrat import-linter). Appelé uniquement par `openplan models pull`."""

from __future__ import annotations

from pathlib import Path

from openplan.adapters.protocols import AdapterUnavailableError

DEFAULT_PATTERNS = ["*.json", "*.safetensors", "README.md", "LICENSE*"]


def hf_download(repo_id: str, target: Path, allow_patterns: list[str] | None = None) -> None:
    """`allow_patterns` restreint les fichiers (ex. variante fp16 seulement) ; défaut : les
    configurations JSON et tous les safetensors du dépôt."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        msg = "huggingface-hub absent : make setup-neural"
        raise AdapterUnavailableError(msg) from exc
    snapshot_download(
        repo_id, local_dir=str(target), allow_patterns=allow_patterns or DEFAULT_PATTERNS
    )
