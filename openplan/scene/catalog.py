"""Chargement du catalogue de meubles (`assets/catalog.yaml`) et création d'un `Furniture`.

Le catalogue est lu une fois par chemin ; une licence inconnue, un identifiant en double ou
une plage incohérente font échouer le chargement avec un message clair (`CatalogError`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from openplan.resources import resource
from openplan.schemas.catalog import Catalog, CatalogAsset, FurnitureSources
from openplan.schemas.common import Confidence, Point2, Point3, Provenance, Source, Transform3
from openplan.schemas.elements import Furniture

CATALOG_PATH = resource("assets", "catalog.yaml")
FURNITURE_DIR = resource("assets", "furniture")
SOURCES_PATH = FURNITURE_DIR / "SOURCES.yaml"


class CatalogError(ValueError):
    """Catalogue illisible ou meuble inconnu / hors plage."""


@lru_cache(maxsize=8)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Catalog.model_validate(data)
    except FileNotFoundError as exc:
        msg = f"catalogue introuvable : {path}"
        raise CatalogError(msg) from exc
    except (ValidationError, yaml.YAMLError) as exc:
        msg = f"catalogue invalide ({path.name}) : {exc}"
        raise CatalogError(msg) from exc


@lru_cache(maxsize=4)
def load_sources(path: Path = SOURCES_PATH) -> FurnitureSources:
    try:
        return FurnitureSources.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        msg = f"registre des sources introuvable : {path}"
        raise CatalogError(msg) from exc
    except (ValidationError, yaml.YAMLError) as exc:
        msg = f"registre des sources invalide ({path.name}) : {exc}"
        raise CatalogError(msg) from exc


def asset_file(relative: str) -> Path:
    """Chemin d'un modèle ou d'une vignette du catalogue (relatif à `assets/furniture/`)."""
    return FURNITURE_DIR / relative


def asset(asset_id: str, path: Path = CATALOG_PATH) -> CatalogAsset:
    found = load_catalog(path).get(asset_id)
    if found is None:
        known = ", ".join(a.id for a in load_catalog(path).assets)
        msg = f"meuble inconnu au catalogue : {asset_id} (connus : {known})"
        raise CatalogError(msg)
    return found


def furniture_from_asset(
    item: CatalogAsset,
    position: Point2,
    yaw: float = 0.0,
    *,
    width: float | None = None,
    depth: float | None = None,
    height: float | None = None,
    name: str | None = None,
    source: Source = Source.USER_DEFINED,
    confidence: Confidence = 1.0,
    provenance: Provenance | None = None,
) -> Furniture:
    """Un meuble posé au sol en (x, y), tourné de `yaw`, aux dimensions du catalogue ou à
    celles demandées si elles sont dans la plage."""
    try:
        bbox = item.bbox(width, depth, height)
    except ValueError as exc:
        msg = f"{item.label} : {exc}"
        raise CatalogError(msg) from exc
    furniture = Furniture(
        name=name or item.label,
        asset_id=item.id,
        category=item.category,
        transform=Transform3(position=Point3(x=position.x, y=position.y, z=0.0), yaw=yaw),
        bbox=bbox,
        source=source,
        confidence=confidence,
    )
    if provenance is not None:
        furniture = furniture.model_copy(update={"provenance": provenance})
    return furniture
