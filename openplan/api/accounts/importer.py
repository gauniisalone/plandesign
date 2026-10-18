"""`openplan accounts import --owner <email> <dossier>` (note 17, § 9 ; B1e) : les projets de la
machine d'augus copiés dans le stockage du serveur à comptes, augus propriétaire.

- Le propriétaire doit déjà avoir un compte (première connexion faite), sinon refus.
- La liste des projets trouvés est montrée (nom, date, photos, rendus, poids copié) ; rien
  n'est copié sans confirmation (projet par projet, « tous », ou `--yes` / `--only`).
- Copié : `project.json`, `images/`, `cover/`, la vignette, le tableau, et dans `runs/` les
  rendus et métadonnées seulement. Laissé sur la machine : scènes Blender (`*.blend`), cartes de
  profondeur (`*.npy`, `*.npz`) et la corbeille des rendus (`runs/.trash`).
- Un projet déjà présent (même id) est sauté et signalé. Rien n'est supprimé de la machine.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from openplan.api.accounts.repo import AccountsRepo

PROJECT_FILE = "project.json"
HEAVY_SUFFIXES = frozenset({".blend", ".blend1", ".npy", ".npz"})
TRASH = ".trash"
RENDER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ImportRefusedError(RuntimeError):
    """Propriétaire inconnu ou dossier sans projet."""


@dataclass(frozen=True)
class Candidate:
    id: UUID
    name: str
    path: Path
    updated: str
    photos: int
    renders: int
    copy_bytes: int
    left_bytes: int


def _kept(rel: Path) -> bool:
    """Le fichier fait-il partie de la copie ? (chemin relatif au dossier du projet)"""
    parts = rel.parts
    if parts and parts[0] == "runs":
        if TRASH in parts:
            return False
        return rel.suffix.lower() not in HEAVY_SUFFIXES
    return True


def files_to_copy(project_dir: Path) -> tuple[list[Path], int]:
    """Les fichiers copiés (relatifs) et le poids laissé sur la machine."""
    kept: list[Path] = []
    left = 0
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(project_dir)
        if _kept(rel):
            kept.append(rel)
        else:
            left += path.stat().st_size
    return kept, left


def find_projects(source: Path) -> list[Candidate]:
    """Les dossiers `<id>/project.json` de `source`, du plus récent au plus ancien."""
    found: list[Candidate] = []
    for directory in sorted(p for p in source.iterdir() if p.is_dir()):
        file = directory / PROJECT_FILE
        if not file.is_file():
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            project_id = UUID(str(data["id"]))
        except (ValueError, KeyError, TypeError):
            continue
        if directory.name != str(project_id):
            continue
        kept, left = files_to_copy(directory)
        renders = sum(
            1 for rel in kept if rel.parts[0] == "runs" and rel.suffix.lower() in RENDER_SUFFIXES
        )
        found.append(
            Candidate(
                id=project_id,
                name=str(data.get("name") or directory.name),
                path=directory,
                updated=str(data.get("updated_at") or "")[:10],
                photos=len(data.get("images") or {}),
                renders=renders,
                copy_bytes=sum((directory / rel).stat().st_size for rel in kept),
                left_bytes=left,
            )
        )
    found.sort(key=lambda c: c.updated, reverse=True)
    return found


def copy_project(candidate: Candidate, destination: Path) -> int:
    """Copie le projet dans `destination/<id>` ; renvoie le nombre de fichiers copiés."""
    target = destination / str(candidate.id)
    kept, _ = files_to_copy(candidate.path)
    for rel in kept:
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.path / rel, out)
    (target / "runs").mkdir(exist_ok=True)
    return len(kept)


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} Mo"


def run_import(
    repo: AccountsRepo,
    owner_email: str,
    source: Path,
    destination: Path,
    *,
    only: Iterable[str] = (),
    yes: bool = False,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> list[UUID]:
    """Importe les projets choisis ; renvoie les ids importés."""
    owner = repo.user_by_email(owner_email)
    if owner is None:
        msg = (
            f"aucun compte pour {owner_email} : se connecter une première fois sur le serveur, "
            "puis relancer l'import"
        )
        raise ImportRefusedError(msg)
    if not source.is_dir():
        msg = f"dossier introuvable : {source}"
        raise ImportRefusedError(msg)
    candidates = find_projects(source)
    wanted = {o.strip() for o in only if o.strip()}
    if wanted:
        candidates = [c for c in candidates if str(c.id) in wanted or str(c.id)[:8] in wanted]
    if not candidates:
        msg = f"aucun projet à importer dans {source}"
        raise ImportRefusedError(msg)
    say(f"{len(candidates)} projet(s) trouvé(s) :")
    for c in candidates:
        say(
            f"  {str(c.id)[:8]}  {c.name}  · modifié le {c.updated or '?'} · {c.photos} photo(s) · "
            f"{c.renders} rendu(s) · {_size(c.copy_bytes)} copiés, {_size(c.left_bytes)} laissés"
        )
    imported: list[UUID] = []
    all_yes = yes
    for c in candidates:
        if repo.project_exists(c.id) or (destination / str(c.id)).exists():
            say(f"  sauté : « {c.name} » est déjà sur le serveur")
            continue
        if not all_yes:
            answer = ask(f"Importer « {c.name} » ? [o]ui / [n]on / [t]ous / [q]uitter : ")
            answer = answer.strip().lower()[:1]
            if answer == "q":
                break
            if answer == "t":
                all_yes = True
            elif answer != "o":
                continue
        count = copy_project(c, destination)
        repo.add_project(c.id, owner.id, c.name)
        imported.append(c.id)
        say(f"  importé : « {c.name} » ({count} fichiers)")
    say(f"{len(imported)} projet(s) importé(s), propriétaire {owner.email}.")
    return imported
