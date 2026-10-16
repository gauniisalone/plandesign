"""Exports : JSON du projet, SVG du plan d'un niveau."""

from __future__ import annotations

import unicodedata
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from openplan.api.deps import ProjectDep
from openplan.pipeline.export import render_dxf, render_pdf, render_svg, shareable
from openplan.pipeline.section import render_section_pdf

router = APIRouter(tags=["exports"])


def _safe_filename(name: str) -> str:
    """Nom de fichier ASCII (les en-têtes HTTP n'acceptent pas les accents) sans caractère
    interdit par les systèmes de fichiers."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_name if ch not in '"\\/:*?<>|\r\n').strip() or "plan"


@router.get("/projects/{project_id}/exports/json")
def export_json(project: ProjectDep, share: bool = False) -> Response:
    """Le projet complet (sauvegarde : historique et chemins d'images inclus) ou, avec
    `share=true`, la copie à partager sans historique ni chemins (`pipeline.export.shareable`)."""
    suffix = ".partage.openplan.json" if share else ".openplan.json"
    filename = (project.name + suffix).replace('"', "")
    data = shareable(project) if share else project
    return Response(
        content=data.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/exports/svg")
def export_svg(
    level_id: UUID, project: ProjectDep, theme: Literal["light", "dark"] = "light"
) -> Response:
    """Plan du niveau en SVG ; `theme=dark` (C1) pour la vignette du tableau en thème sombre."""
    return Response(content=render_svg(project, level_id, theme), media_type="image/svg+xml")


@router.get("/projects/{project_id}/exports/dxf")
def export_dxf(level_id: UUID, project: ProjectDep) -> Response:
    """Plan en DXF R2010 (mètres), un calque par nature d'élément, incertains à part."""
    level = project.levels.get(level_id)
    level_name = level.name if level else "niveau"
    filename = _safe_filename(f"{project.name} - {level_name}") + ".dxf"
    return Response(
        content=render_dxf(project, level_id),
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/exports/section-pdf")
def export_section_pdf(
    level_id: UUID,
    project: ProjectDep,
    axis: Literal["x", "y"],
    value: float = Query(ge=-100.0, le=100.0),
    page: str = "A4",
) -> Response:
    """Coupe verticale cotée (E3) par le plan x = valeur ou y = valeur, en PDF vectoriel."""
    level = project.levels.get(level_id)
    level_name = level.name if level else "niveau"
    filename = _safe_filename(f"{project.name} - {level_name} - coupe {axis}") + ".pdf"
    return Response(
        content=render_section_pdf(project, level_id, axis, value, page_format=page),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/exports/pdf")
def export_pdf(level_id: UUID, project: ProjectDep, page: str = "A4") -> Response:
    """Plan coté en PDF vectoriel (A4 ou A3 paysage, échelle normalisée, cartouche)."""
    level = project.levels.get(level_id)
    level_name = level.name if level else "niveau"
    filename = _safe_filename(f"{project.name} - {level_name}") + ".pdf"
    return Response(
        content=render_pdf(project, level_id, page_format=page),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
