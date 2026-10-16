"""Vignette d'un projet pour la galerie de tableaux (T5, choix du PO du 16 octobre), par ordre
de préférence : le dernier rendu retenu (favori) ou, à défaut, le plus récent, réduit en WebP
de 480 px de large et mis en cache ; sinon le plan en SVG léger ; sinon la première photo
réduite ; sinon une carte « Exigences » en SVG. Rien n'est demandé à l'extérieur."""

from __future__ import annotations

import html
import textwrap
from dataclasses import dataclass
from pathlib import Path

from openplan.pipeline.export import SvgTheme, render_svg
from openplan.pipeline.gallery import GalleryEntry, gallery
from openplan.schemas.elements import Wall
from openplan.schemas.scene import Project

COVER_DIR = "cover"
COVER_WIDTH = 480
COVER_QUALITY = 80
_ORDER = ("finished", "blender", "inspiration", "catalogue")


@dataclass(frozen=True)
class Cover:
    kind: str
    """« render », « plan », « photo » ou « requirements »."""
    media_type: str
    path: Path | None = None
    content: bytes | None = None


def _image(entry: GalleryEntry, variant: str) -> str | None:
    if variant == "blender":
        return entry.image
    found = getattr(entry, variant, None)
    return found.image if found is not None else None


def _pick_render(project: Project, runs_dir: Path) -> tuple[GalleryEntry, str] | None:
    """Le dernier rendu d'une vue avec une image favorite, sinon le plus récent (sa finition
    s'il en a une)."""
    entries = [e for e in gallery(project, runs_dir) if e.variant is None]
    for entry in entries:
        for favorite in entry.favorites:
            if _image(entry, favorite):
                return entry, str(favorite)
    for entry in entries:
        for variant in _ORDER:
            if _image(entry, variant):
                return entry, variant
    return None


def _render_cover(project_dir: Path, runs_dir: Path, entry: GalleryEntry, variant: str) -> Path:
    """La réduction WebP du rendu choisi, refaite seulement si l'image est plus récente."""
    from PIL import Image

    name = _image(entry, variant) or entry.image
    source = runs_dir / str(entry.run_id) / "blender" / name
    out_dir = project_dir / COVER_DIR
    target = out_dir / f"{entry.run_id}-{variant}.webp"
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webp"):
        old.unlink(missing_ok=True)
    with Image.open(source) as img:
        rgb = img.convert("RGB")
        height = max(1, round(rgb.height * COVER_WIDTH / rgb.width))
        rgb.resize((COVER_WIDTH, height), Image.Resampling.LANCZOS).save(
            target, format="WEBP", quality=COVER_QUALITY
        )
    return target


def _requirements_svg(project: Project, theme: SvgTheme) -> bytes:
    dark = theme == "dark"
    bg, card, ink, muted = (
        ("#17151f", "#2f2850", "#ece8f5", "#a79fbf")
        if dark
        else ("#f4f3f8", "#e4defa", "#211c35", "#6b6585")
    )
    text = (project.pending_instruction or "").strip()
    lines = textwrap.wrap(text, width=34, max_lines=4, placeholder=" …") or [
        "Ajoutez une photo, vos exigences,",
        "ou tracez le plan.",
    ]
    rows = "".join(
        f'<text x="48" y="{132 + 30 * i}" font-size="20" fill="{ink if text else muted}">'
        f"{html.escape(line)}</text>"
        for i, line in enumerate(lines)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 360" width="480" height="360">'
        f'<rect width="480" height="360" fill="{bg}"/>'
        f'<rect x="24" y="24" width="432" height="312" rx="14" fill="{card}"/>'
        f'<text x="48" y="80" font-size="22" font-weight="600" fill="{ink}" '
        'font-family="system-ui, sans-serif">Exigences</text>'
        f'<g font-family="system-ui, sans-serif">{rows}</g></svg>'
    )
    return svg.encode("utf-8")


def project_cover(
    project: Project,
    project_dir: Path,
    runs_dir: Path,
    photo_thumbnail: Path,
    theme: SvgTheme = "light",
) -> Cover:
    """La vignette du projet, dans l'ordre de préférence du PO."""
    picked = _pick_render(project, runs_dir)
    if picked is not None:
        path = _render_cover(project_dir, runs_dir, *picked)
        return Cover(kind="render", media_type="image/webp", path=path)
    for level_id, level in project.levels.items():
        if any(isinstance(e, Wall) for e in level.elements.values()):
            svg = render_svg(project, level_id, theme)
            return Cover(kind="plan", media_type="image/svg+xml", content=svg.encode("utf-8"))
    if photo_thumbnail.is_file():
        return Cover(kind="photo", media_type="image/jpeg", path=photo_thumbnail)
    return Cover(
        kind="requirements",
        media_type="image/svg+xml",
        content=_requirements_svg(project, theme),
    )
