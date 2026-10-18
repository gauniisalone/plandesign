"""Couche de stockage des fichiers (note 17, § 1 et § 8) : une seule porte pour les fichiers
des projets, sur le disque aujourd'hui, sur un stockage objet (S3) plus tard.

Une **clé** est un chemin relatif POSIX (`projects/<id>/images/photo.jpg`), jamais un chemin
du système : ni absolu, ni `..`, ni barre oblique inverse, ni segment vide. Le disque la
résout sous sa racine et refuse tout ce qui en sortirait (lien symbolique compris).

`local_path` dit où lire le fichier quand il est sur le disque (pour un envoi efficace par
l'API ou pour Blender) ; un stockage objet renverra None et l'API passera par `open_read`.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable
from uuid import uuid4

_SEGMENT = re.compile(r"^[^/\\\x00-\x1f]+$")
MAX_KEY_LENGTH = 1024


class StorageKeyError(ValueError):
    """Clé invalide (absolue, `..`, caractère de contrôle) ou chemin hors du stockage."""


class StoredFileNotFoundError(FileNotFoundError):
    """Aucun fichier sous cette clé."""


def check_key(key: str) -> str:
    """La clé telle quelle si elle est sûre, sinon StorageKeyError."""
    if not key or len(key) > MAX_KEY_LENGTH or key.startswith("/"):
        msg = "clé de stockage invalide"
        raise StorageKeyError(msg)
    for segment in key.split("/"):
        if segment in ("", ".", "..") or not _SEGMENT.match(segment):
            msg = "clé de stockage invalide"
            raise StorageKeyError(msg)
    return key


@runtime_checkable
class Storage(Protocol):
    """Ce que l'application attend d'un stockage de fichiers."""

    def exists(self, key: str) -> bool: ...

    def read_bytes(self, key: str) -> bytes: ...

    def open_read(self, key: str) -> BinaryIO: ...

    def write_bytes(self, key: str, data: bytes) -> None: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def list(self, prefix: str) -> list[str]: ...

    def size(self, key: str) -> int: ...

    def local_path(self, key: str) -> Path | None: ...

    def key_of(self, path: Path) -> str: ...


class DiskStorage:
    """Fichiers sous un dossier racine (le dossier des données)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._resolved = self.root.resolve()

    def _path(self, key: str) -> Path:
        path = self._resolved.joinpath(*check_key(key).split("/"))
        # Un lien symbolique ne doit pas faire sortir de la racine.
        if not path.resolve().is_relative_to(self._resolved):
            msg = "chemin hors du stockage"
            raise StorageKeyError(msg)
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StoredFileNotFoundError(key)
        return path.read_bytes()

    def open_read(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.is_file():
            raise StoredFileNotFoundError(key)
        return path.open("rb")

    def write_bytes(self, key: str, data: bytes) -> None:
        """Écriture atomique : fichier temporaire voisin puis remplacement."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if not path.is_file():
            raise StoredFileNotFoundError(key)
        path.unlink()

    def delete_prefix(self, prefix: str) -> int:
        """Supprime tout ce qui est sous `prefix` (un dossier) ; renvoie le nombre de fichiers."""
        path = self._path(prefix.rstrip("/"))
        if not path.is_dir():
            return 0
        count = sum(1 for p in path.rglob("*") if p.is_file())
        shutil.rmtree(path)
        return count

    def list(self, prefix: str) -> list[str]:
        """Clés des fichiers sous `prefix`, triées."""
        path = self._path(prefix.rstrip("/"))
        if not path.is_dir():
            return []
        return sorted(self._key(p) for p in self._walk(path))

    def size(self, key: str) -> int:
        path = self._path(key)
        if not path.is_file():
            raise StoredFileNotFoundError(key)
        return path.stat().st_size

    def local_path(self, key: str) -> Path | None:
        return self._path(key)

    def key_of(self, path: Path) -> str:
        """Clé d'un chemin du disque situé sous la racine, sinon StorageKeyError."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self._resolved) or resolved == self._resolved:
            msg = "chemin hors du stockage"
            raise StorageKeyError(msg)
        return check_key(self._key(resolved))

    def _key(self, path: Path) -> str:
        return path.resolve().relative_to(self._resolved).as_posix()

    @staticmethod
    def _walk(path: Path) -> Iterator[Path]:
        for p in path.rglob("*"):
            if p.is_file():
                yield p
