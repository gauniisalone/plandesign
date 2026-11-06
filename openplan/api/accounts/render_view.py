"""KAN-154 (étape 1) : la vue d'une demande de rendu réaliste, FIGÉE au moment du clic.

Le bake (style réaliste) et la caméra de la vue sont écrits à la création de la demande, à
côté des images de style du même clic (KAN-97, `realistic-style/<id>.bake.json` et
`<id>.camera.json`, dans le dossier du projet). Le poste les lit tels quels (`files/bake`,
`files/camera`) au lieu de les recalculer depuis l'état courant du projet ; ils restent avec la
demande après le dépôt du rendu : le contrôle de fidélité (étape 2) compare l'image à CETTE
géométrie-là. Aucune colonne ni table : des fichiers du projet, donc effacés avec lui (projet
supprimé, compte effacé), et pris dans l'archive d'un projet (export) comme les autres.

Repli : une demande d'avant KAN-154 (aucun fichier) est encore servie en recalculant la vue,
comme avant.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError

from openplan.api.accounts.render_style import NOTES_DIR
from openplan.pipeline.render import camera_view
from openplan.pipeline.scene3d import bake_level
from openplan.scene.store import ProjectStore
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.fidelity import FidelityVerdict
from openplan.schemas.scene import Project

FrozenName = Literal["bake", "camera"]
FROZEN: tuple[FrozenName, ...] = ("bake", "camera")


def frozen_path(store: ProjectStore, project_id: UUID, request_id: UUID, name: FrozenName) -> Path:
    return store.project_dir(project_id) / NOTES_DIR / f"{request_id}.{name}.json"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(f".{uuid4().hex}.part")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def freeze_view(
    store: ProjectStore, project: Project, level_id: UUID, camera_id: UUID, request_id: UUID
) -> None:
    """Le bake et la caméra de la vue, tels qu'au clic. NoCameraError si la vue n'existe pas
    (l'appelant l'a déjà vérifiée) ; OSError si le disque refuse (l'appelant le dit)."""
    bake = bake_level(project, level_id, "realistic")
    camera = camera_view(project, level_id, camera_id)[1]
    _write(frozen_path(store, project.id, request_id, "bake"), bake.model_dump_json())
    _write(frozen_path(store, project.id, request_id, "camera"), camera.model_dump_json())


def frozen_text(
    store: ProjectStore, project_id: UUID, request_id: UUID, name: FrozenName
) -> str | None:
    """Le fichier figé, tel quel ; None pour une demande d'avant KAN-154."""
    try:
        return frozen_path(store, project_id, request_id, name).read_text(encoding="utf-8")
    except OSError:
        return None


def fidelity_path(store: ProjectStore, project_id: UUID, request_id: UUID) -> Path:
    """KAN-154 (étape 2) : le verdict de fidélité du rendu, rangé avec la demande."""
    return store.project_dir(project_id) / NOTES_DIR / f"{request_id}.fidelity.json"


def note_fidelity(
    store: ProjectStore, project_id: UUID, request_id: UUID, verdict: FidelityVerdict
) -> None:
    _write(fidelity_path(store, project_id, request_id), verdict.model_dump_json())


def fidelity_of(store: ProjectStore, project_id: UUID, request_id: UUID) -> FidelityVerdict | None:
    try:
        raw = fidelity_path(store, project_id, request_id).read_text(encoding="utf-8")
        return FidelityVerdict.model_validate_json(raw)
    except (OSError, ValidationError):
        return None


def frozen_view(
    store: ProjectStore, project_id: UUID, request_id: UUID
) -> tuple[Bake, RenderCamera] | None:
    """Le bake et la caméra figés d'une demande (contrôle de fidélité, étape 2) ; None s'il en
    manque un ou s'il ne se lit pas."""
    bake = frozen_text(store, project_id, request_id, "bake")
    camera = frozen_text(store, project_id, request_id, "camera")
    if bake is None or camera is None:
        return None
    try:
        return Bake.model_validate_json(bake), RenderCamera.model_validate_json(camera)
    except ValidationError:
        return None
