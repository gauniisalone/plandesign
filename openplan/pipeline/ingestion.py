"""Import d'une image dans un projet : copie locale, hachage, EXIF, contrôle qualité.

Règles (brief §3) : la photo reste sur la machine, dans le dossier du projet ; les
métadonnées EXIF sont retirées par défaut (`privacy.strip_exif`) ; seule la focale
équivalente 35 mm est conservée, si `privacy.keep_exif_focal` l'autorise. Le nom de
fichier d'origine n'entre jamais dans le chemin stocké (UUID + extension canonique).
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np

from openplan.adapters.protocols import ImageArray
from openplan.pipeline.quality import assess_quality, quality_warnings
from openplan.schemas.common import DECODED_IMAGE_FORMATS, now_utc
from openplan.schemas.scene import ImageRef, Project

if TYPE_CHECKING:
    from PIL import Image

IMAGES_DIR = "images"
THUMBNAIL_FILE = "thumbnail.jpg"
"""Même nom que `scene.store.THUMBNAIL_FILE` : vignette du projet à la racine de son dossier."""
THUMBNAIL_PX = 240
MAX_BYTES = 50 * 1024 * 1024
FORMATS: dict[str, str] = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
"""Formats acceptés (nom PIL) et extension canonique."""
_EXIF_FOCAL_35MM = 0xA405
_EXIF_IFD = 0x8769


class IngestionError(ValueError):
    """Fichier refusé : trop gros, format inconnu, illisible."""


class ImageNotFoundError(IngestionError):
    """Identifiant d'image inconnu ou fichier absent du dossier du projet."""


def import_image(
    project: Project, project_dir: Path, data: bytes, *, original_name: str = ""
) -> tuple[Project, ImageRef, list[str]]:
    """Enregistre `data` dans <projet>/images/ et renvoie le projet enrichi.

    L'ajout d'une image n'est pas une entrée d'historique : annuler une édition ne doit
    pas faire disparaître un fichier. La suppression est une action explicite.
    """
    if len(data) > MAX_BYTES:
        msg = f"fichier trop volumineux ({len(data) / 1e6:.0f} Mo > {MAX_BYTES / 1e6:.0f} Mo)"
        raise IngestionError(msg)
    if not data:
        msg = "fichier vide"
        raise IngestionError(msg)
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        # KAN-113 : seuls les décodeurs JPEG, PNG et WebP sont essayés, AVANT tout autre
        # contrôle ; un autre format, même renommé en .jpg, n'atteint jamais son décodeur.
        with Image.open(io.BytesIO(data), formats=DECODED_IMAGE_FORMATS) as raw:
            fmt = raw.format or ""
            if fmt not in FORMATS:
                msg = f"format non pris en charge : {fmt or 'inconnu'} ({original_name or '?'})"
                raise IngestionError(msg)
            focal = _read_focal_35mm(raw) if project.privacy.keep_exif_focal else None
            oriented = ImageOps.exif_transpose(raw) or raw
            rgb = oriented.convert("RGB")
            stored = _reencode(rgb, fmt) if project.privacy.strip_exif else data
    except UnidentifiedImageError as exc:
        # Pillow ne dit plus quel autre format c'était : son décodeur n'a pas été essayé.
        msg = (
            f"image illisible ou format non pris en charge ({original_name or '?'}) : "
            "JPEG, PNG ou WebP seulement"
        )
        raise IngestionError(msg) from exc
    except (OSError, Image.DecompressionBombError) as exc:
        msg = f"image illisible ({original_name or '?'}) : {exc}"
        raise IngestionError(msg) from exc

    pixels = np.asarray(rgb, dtype=np.uint8)
    quality = assess_quality(pixels)
    ref = ImageRef(
        path="",
        sha256=hashlib.sha256(stored).hexdigest(),
        width_px=rgb.width,
        height_px=rgb.height,
        exif_stripped=project.privacy.strip_exif,
        focal_35mm=focal,
        quality=quality,
    )
    relative = f"{IMAGES_DIR}/{ref.id}{FORMATS[fmt]}"
    ref = ref.model_copy(update={"path": relative})
    target = project_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(stored)
    if not project.images or not (project_dir / THUMBNAIL_FILE).is_file():
        write_thumbnail(project_dir, rgb)
    after = project.model_copy(
        update={"images": {**project.images, ref.id: ref}, "updated_at": now_utc()}
    )
    return after, ref, quality_warnings(quality)


def write_thumbnail(project_dir: Path, image: Image.Image) -> Path:
    """Vignette du projet (première photo, au plus `THUMBNAIL_PX` de côté, JPEG sans
    métadonnées) pour la liste des projets ; jamais l'image d'origine."""
    from PIL import ImageOps

    small = ImageOps.contain(image.convert("RGB"), (THUMBNAIL_PX, THUMBNAIL_PX))
    path = project_dir / THUMBNAIL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    small.save(path, format="JPEG", quality=80)
    return path


def refresh_thumbnail(project: Project, project_dir: Path) -> None:
    """Après une suppression : vignette depuis la première photo restante, ou plus de vignette."""
    from PIL import Image, ImageOps

    path = project_dir / THUMBNAIL_FILE
    for ref in project.images.values():
        source = project_dir / ref.path
        if source.is_file():
            with Image.open(source, formats=DECODED_IMAGE_FORMATS) as raw:
                write_thumbnail(project_dir, ImageOps.exif_transpose(raw) or raw)
            return
    if path.is_file():
        path.unlink()


def load_image(project_dir: Path, ref: ImageRef) -> ImageArray:
    """Relit une image stockée, orientation EXIF appliquée, en RGB uint8."""
    from PIL import Image, ImageOps

    path = project_dir / ref.path
    if not path.is_file():
        msg = f"fichier image absent : {ref.path}"
        raise ImageNotFoundError(msg)
    with Image.open(path, formats=DECODED_IMAGE_FORMATS) as raw:
        oriented = ImageOps.exif_transpose(raw) or raw
        pixels = np.asarray(oriented.convert("RGB"), dtype=np.uint8)
    return ImageArray(image_id=ref.id, pixels=pixels, focal_35mm=ref.focal_35mm)


def remove_image(project: Project, project_dir: Path, image_id: UUID) -> Project:
    """Supprime le fichier et la référence ; détache l'image des niveaux qui l'utilisent."""
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    path = project_dir / ref.path
    if path.is_file():
        path.unlink()
    # KAN-66 : la profondeur gardée pour la mesure de référence part avec la photo.
    for suffix in (".depth.npy", ".depth.json"):
        (project_dir / IMAGES_DIR / f"{image_id}{suffix}").unlink(missing_ok=True)
    levels = dict(project.levels)
    for lid, level in project.levels.items():
        if level.background_image == image_id:
            levels[lid] = level.model_copy(
                update={"background_image": None, "background_transform": {}}
            )
    images = {k: v for k, v in project.images.items() if k != image_id}
    after = project.model_copy(update={"images": images, "levels": levels, "updated_at": now_utc()})
    refresh_thumbnail(after, project_dir)
    return after


def _read_focal_35mm(image: Image.Image) -> float | None:
    exif = image.getexif()
    value = exif.get_ifd(_EXIF_IFD).get(_EXIF_FOCAL_35MM)
    if value is None:
        return None
    try:
        focal = float(value)
    except (TypeError, ValueError):
        return None
    return focal if focal > 0 else None


def _reencode(rgb: Image.Image, fmt: str) -> bytes:
    """Réécrit l'image sans aucune métadonnée (PIL n'en copie pas par défaut)."""
    buffer = io.BytesIO()
    if fmt == "JPEG":
        rgb.save(buffer, format="JPEG", quality=92, subsampling=0)
    elif fmt == "WEBP":
        rgb.save(buffer, format="WEBP", quality=92)
    else:
        rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
