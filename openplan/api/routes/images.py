"""Images d'un projet : import local (multipart), fichier, qualité, suppression."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, UploadFile

from openplan.api.board_rules import place_new_photo
from openplan.api.deps import ProjectDep, StoreDep
from openplan.api.files import served_file
from openplan.pipeline.ingestion import ImageNotFoundError, import_image, remove_image
from openplan.pipeline.quality import quality_warnings
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import ImageRef, Project

router = APIRouter(tags=["images"])


class ImageImportResult(StrictModel):
    image: ImageRef
    warnings: list[str]
    project: Project


class ImageQuality(StrictModel):
    quality: dict[str, float]
    warnings: list[str]


@router.post("/projects/{project_id}/images", status_code=201)
async def upload_image(
    file: UploadFile, project: ProjectDep, store: StoreDep, level_id: UUID | None = None
) -> ImageImportResult:
    """Copie l'image dans le projet (jamais ailleurs), EXIF retiré selon `privacy`. KAN-91 :
    `level_id`, le plan ouvert : la photo entre dans la carte Photos de ce plan."""
    data = await file.read()
    after, ref, warnings = import_image(
        project, store.project_dir(project.id), data, original_name=file.filename or ""
    )
    store.save(after)
    if level_id is not None:
        place_new_photo(after, store, level_id, ref.id)
    return ImageImportResult(image=ref, warnings=warnings, project=after)


@router.get("/projects/{project_id}/images/{image_id}/file")
def image_file(image_id: UUID, project: ProjectDep, store: StoreDep) -> Response:
    ref = _ref(project, image_id)
    path = store.project_dir(project.id) / ref.path
    if not path.is_file():
        msg = f"fichier image absent : {ref.path}"
        raise ImageNotFoundError(msg)
    return served_file(store, path)


@router.get("/projects/{project_id}/images/{image_id}/quality")
def image_quality(image_id: UUID, project: ProjectDep) -> ImageQuality:
    ref = _ref(project, image_id)
    return ImageQuality(quality=ref.quality, warnings=quality_warnings(ref.quality))


@router.delete("/projects/{project_id}/images/{image_id}")
def delete_image(image_id: UUID, project: ProjectDep, store: StoreDep) -> Project:
    after = remove_image(project, store.project_dir(project.id), image_id)
    store.save(after)
    return after


def _ref(project: Project, image_id: UUID) -> ImageRef:
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    return ref
