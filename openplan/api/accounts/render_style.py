"""KAN-97 : les cartes Inspirations jusqu'au rendu réaliste, côté serveur.

À la demande, les images de style de la carte Rendus (C1, `style_references`) sont notées
(`realistic-style/<id>.style.json`) : la demande garde celles du moment du clic. Un poste qui
annonce `STYLE` reçoit leurs noms dans `WorkerJob.style` et lit chaque image par
`/api/worker/jobs/{id}/style/{k}`, réduite (STYLE_IMAGE_SIDE, JPEG). Il renvoie les mots
retenus, gardés dans `realistic-style/<id>.words.json` : la carte Rendus dit « Guidé par ».
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from openplan.api.board_rules import style_references
from openplan.pipeline.contextual import ContextualError, ContextualReference, reference_path
from openplan.pipeline.style_words import french
from openplan.scene.store import ProjectStore
from openplan.schemas.common import DECODED_IMAGE_FORMATS
from openplan.schemas.render_queue import (
    MAX_STYLE_IMAGE_BYTES,
    MAX_STYLE_IMAGES,
    STYLE_IMAGE_SIDE,
    RealisticRequest,
)
from openplan.schemas.scene import Project

NOTES_DIR = "realistic-style"
"""À côté du dossier des rendus (`realistic/`), qui ne garde que les images."""
JPEG_QUALITY = 85


def _sidecar(store: ProjectStore, project_id: UUID, request_id: UUID, kind: str) -> Path:
    return store.project_dir(project_id) / NOTES_DIR / f"{request_id}.{kind}.json"


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(f".{uuid4().hex}.part")
    partial.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


def _read(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def note_references(
    store: ProjectStore, project: Project, level_id: UUID, request_id: UUID
) -> None:
    """Les images de style de la carte Rendus au moment de la demande (au plus
    MAX_STYLE_IMAGES) ; une liste vide dit « sans inspiration »."""
    refs = style_references(project, store, level_id, MAX_STYLE_IMAGES)
    _write(
        _sidecar(store, project.id, request_id, "style"),
        [{"kind": k, "ref": r, "label": label} for k, r, label in refs],
    )


def references(
    store: ProjectStore, project_id: UUID, request_id: UUID
) -> list[ContextualReference] | None:
    """Les images notées à la demande ; None pour une demande d'avant KAN-97."""
    data = _read(_sidecar(store, project_id, request_id, "style"))
    if not isinstance(data, list):
        return None
    out: list[ContextualReference] = []
    for item in data[:MAX_STYLE_IMAGES]:
        with contextlib.suppress(ValueError, TypeError):
            out.append(ContextualReference.model_validate(item))
    return out


def style_names(store: ProjectStore, project_id: UUID, request_id: UUID) -> list[str]:
    """Les noms envoyés au poste (`WorkerJob.style`), dans l'ordre de `/style/{k}`."""
    return [r.label or "Image" for r in references(store, project_id, request_id) or []]


def style_image(
    store: ProjectStore, project: Project, request_id: UUID, index: int
) -> bytes | None:
    """L'image `index`, réduite à STYLE_IMAGE_SIDE en JPEG ; None si elle n'existe plus."""
    from PIL import Image, UnidentifiedImageError

    refs = references(store, project.id, request_id) or []
    if not 0 <= index < len(refs):
        return None
    project_dir = store.project_dir(project.id)
    try:
        path = reference_path(project, project_dir, store.runs_dir(project.id), refs[index])
        with Image.open(path, formats=DECODED_IMAGE_FORMATS) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((STYLE_IMAGE_SIDE, STYLE_IMAGE_SIDE))
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=JPEG_QUALITY)
    except (ContextualError, OSError, UnidentifiedImageError, ValueError):
        return None
    data = out.getvalue()
    return data if len(data) <= MAX_STYLE_IMAGE_BYTES else None


def note_words(store: ProjectStore, project_id: UUID, request_id: UUID, words: list[str]) -> None:
    """Les mots retenus par le poste (clés du vocabulaire)."""
    _write(_sidecar(store, project_id, request_id, "words"), list(words))


def with_guidance(
    store: ProjectStore, project_id: UUID, public: RealisticRequest
) -> RealisticRequest:
    """« Guidé par » : les mots lus par le poste, en français (`words`) ; images lues sans mot
    retenu : leurs noms (`no_style`) ; pas lues (ancien poste, SigLIP2 absent, rendu pas encore
    fait) : leurs noms (`unread`) ; [] sans inspiration ; rien pour une demande d'avant KAN-97."""
    refs = references(store, project_id, public.id)
    words = _read(_sidecar(store, project_id, public.id, "words"))
    shown = french(str(w) for w in words) if isinstance(words, list) else []
    if shown:
        return public.model_copy(update={"guided_by": shown, "guided_kind": "words"})
    if refs is None:
        return public
    names = [r.label or "Image" for r in refs]
    if not names:
        return public.model_copy(update={"guided_by": []})
    kind = "no_style" if isinstance(words, list) else "unread"
    return public.model_copy(update={"guided_by": names, "guided_kind": kind})
