"""KAN-79 : profondeur calculée dans le navigateur.

- `GET /api/web-models/depth` sert le modèle ONNX (fp16) au navigateur, depuis le dossier des
  poids (hors du dépôt), en streaming, avec ETag = sha256 et Cache-Control immutable ; 304 si
  `If-None-Match` correspond. Aucune requête vers Hugging Face.
- `PUT /api/projects/{project_id}/levels/{level_id}/images/{image_id}/depth` reçoit la carte
  (float16, grille de 640 px au plus) et la taille de la photo ; la range comme la profondeur
  calculée par le serveur. Droits d'édition, bail du plan (comme les autres écritures du plan),
  place disque ; 422 avec la raison pour une carte incohérente.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

from openplan.adapters.neural.web_depth import ONNX_NAME, WEB_MODEL_ID, read_sha256
from openplan.adapters.neural.weights import is_installed, weights_dir
from openplan.api.body_encoding import decoded_body
from openplan.api.deps import ProjectDep, SettingsDep, StoreDep
from openplan.pipeline.client_depth import (
    MAX_BYTES,
    ClientDepthRefusedError,
    parse_client_depth,
    store_client_depth,
)
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["profondeur du navigateur"])

IMMUTABLE = "private, max-age=31536000, immutable"


class ClientDepthResult(StrictModel):
    image_id: UUID
    depth_size: tuple[int, int]
    """(largeur, hauteur) de la grille gardée."""
    model_id: str


@lru_cache(maxsize=4)
def _etag(path: Path, mtime_ns: int) -> str:
    del mtime_ns  # clé du cache seulement : un fichier remplacé est relu
    return f'"{read_sha256(path)}"'


@router.get("/web-models/depth")
def web_depth_model(settings: SettingsDep, request: Request) -> Response:
    """Le modèle de profondeur du navigateur (ONNX fp16, ~50 Mo). 404 s'il n'est pas installé
    (`openplan models export-web-depth`)."""
    if not is_installed(WEB_MODEL_ID, settings.models_dir, ONNX_NAME):
        raise HTTPException(
            status_code=404, detail="modèle de profondeur du navigateur non installé"
        )
    path = weights_dir(WEB_MODEL_ID, settings.models_dir) / ONNX_NAME
    etag = _etag(path, path.stat().st_mtime_ns)
    headers = {"ETag": etag, "Cache-Control": IMMUTABLE}
    asked = request.headers.get("if-none-match", "")
    if etag in (tag.strip() for tag in asked.split(",")):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="application/octet-stream", headers=headers)


@router.put("/projects/{project_id}/levels/{level_id}/images/{image_id}/depth")
def put_client_depth(
    level_id: UUID,
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    body: Annotated[bytes, Body(media_type="application/octet-stream")],
    image_width: int,
    image_height: int,
    content_encoding: Annotated[str | None, Header()] = None,
) -> ClientDepthResult:
    """Range la profondeur calculée par le navigateur pour cette photo du projet ; ne modifie
    pas le plan (l'estimation, lancée ensuite, la relit sans modèle). La carte peut arriver
    compressée (gzip ou deflate, connexion lente), décompressée avec un plafond."""
    if level_id not in project.levels:
        raise HTTPException(status_code=404, detail=f"niveau inconnu : {level_id}")
    if image_id not in project.images:
        raise HTTPException(status_code=404, detail=f"image inconnue : {image_id}")
    body = decoded_body(body, content_encoding, MAX_BYTES)
    if len(body) > MAX_BYTES:
        raise HTTPException(status_code=422, detail="profondeur trop grande (640 px au plus)")
    try:
        grid = parse_client_depth(body, project, image_id, image_width, image_height)
    except ClientDepthRefusedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    size = store_client_depth(project, store.project_dir(project.id), image_id, grid)
    return ClientDepthResult(image_id=image_id, depth_size=size, model_id=WEB_MODEL_ID)
