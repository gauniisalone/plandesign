"""Catalogue de meubles (3.1) : lecture seule, servi au front pour « Ajouter un meuble »."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from openplan.geom.furniture_symbols import SymbolSet, symbol_set
from openplan.scene.catalog import CatalogError, asset, asset_file, load_catalog
from openplan.schemas.catalog import Catalog

router = APIRouter(tags=["catalog"])


@router.get("/catalog")
def catalog() -> Catalog:
    """Les meubles du catalogue de démonstration : boîtes cotées CC0, plages de dimensions,
    ancrage et dégagements. Un asset à licence inconnue ne passe pas le chargement."""
    return load_catalog()


@router.get("/catalog/symbols")
def catalog_symbols() -> SymbolSet:
    """Symboles 2D par famille de meubles (F4), en coordonnées unité de la boîte : le plan les
    met à la taille et au lacet de chaque meuble, comme l'export SVG et le PDF."""
    return symbol_set()


def _file(asset_id: str, kind: str) -> FileResponse:
    item = asset(asset_id)
    relative = item.preview if kind == "preview" else item.model
    if relative is None:
        msg = f"{item.label} : pas de {'vignette' if kind == 'preview' else 'modèle 3D'}"
        raise CatalogError(msg)
    path = asset_file(relative)
    media = "image/png" if kind == "preview" else "model/gltf-binary"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})


@router.get("/catalog/{asset_id}/preview")
def catalog_preview(asset_id: str) -> FileResponse:
    """Vignette d'un meuble (PNG, rendue depuis son modèle 3D)."""
    return _file(asset_id, "preview")


@router.get("/catalog/{asset_id}/model")
def catalog_model(asset_id: str) -> FileResponse:
    """Modèle 3D d'un meuble (GLB, échelle non métrique : mis à l'échelle par la scène)."""
    return _file(asset_id, "model")
