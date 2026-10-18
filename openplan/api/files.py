"""Envoi des fichiers des projets (note 17, § 4) : toujours par la couche `Storage` du projet,
après la garde de la route ; jamais de chemin statique ni d'URL publique.

Un test vérifie qu'aucune route ne construit de `FileResponse` elle-même (le catalogue de
meubles, ressources du paquet et non fichiers d'utilisateur, est la seule exception).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse

from openplan.scene.storage import StorageKeyError, StoredFileNotFoundError
from openplan.scene.store import ProjectStore


def served_file(
    store: ProjectStore,
    path: Path,
    *,
    media_type: str | None = None,
    filename: str | None = None,
    content_disposition_type: Literal["inline", "attachment"] = "attachment",
    headers: dict[str, str] | None = None,
) -> Response:
    """Le fichier `path` du stockage des projets ; 404 s'il n'y est pas (ou en sort)."""
    storage = store.storage
    try:
        key = storage.key_of(path)
        if not storage.exists(key):
            raise StoredFileNotFoundError(key)
    except (StorageKeyError, StoredFileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="fichier introuvable") from exc
    local = storage.local_path(key)
    if local is not None:
        return FileResponse(
            local,
            media_type=media_type,
            filename=filename,
            content_disposition_type=content_disposition_type,
            headers=headers,
        )
    out = Response(  # pragma: no cover - stockage objet, plus tard
        content=storage.read_bytes(key), media_type=media_type, headers=headers
    )
    return out  # pragma: no cover
