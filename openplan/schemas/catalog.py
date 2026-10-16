"""Catalogue de meubles (roadmap 3.1, bibliothèque élargie le 3 octobre 2026).

Un asset décrit un meuble type par ses dimensions réelles (plage min / défaut / max), sa
façon de se poser (libre, contre un mur, contre le mur d'égout d'un comble), le dégagement
qu'il exige, sa pièce (filtre de la bibliothèque) et sa licence. Les règles d'aménagement
raisonnent toujours sur la boîte (`BBox3`) ; le modèle 3D (`model`, GLB) et la vignette
(`preview`) ne servent qu'à l'affichage, mis à l'échelle de la boîte. Un modèle tiers n'est
accepté qu'en CC0 et s'il figure dans le registre des sources
(`assets/furniture/SOURCES.yaml`) ; une licence inconnue ou restreinte est refusée au
chargement (brief §16).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from openplan.schemas.common import BBox3, Meters, StrictModel

AssetLicense = Literal["open_source", "cc0", "cc_by", "commercial_restricted", "unknown"]
"""Même vocabulaire que `Material.license` (format de scène)."""
ACCEPTED_LICENSES: frozenset[str] = frozenset({"open_source", "cc0", "cc_by"})
AssetAnchor = Literal["free", "wall", "eave_wall"]
"""Où le meuble se pose : n'importe où, dos à un mur, dos au mur d'égout (zone basse d'un
comble : rangement bas, tête de lit)."""
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
Room = Literal["sejour", "chambre", "bureau", "cuisine", "salle_de_bain"]
ROOM_LABELS: dict[str, str] = {
    "sejour": "Séjour",
    "chambre": "Chambre",
    "bureau": "Bureau",
    "cuisine": "Cuisine",
    "salle_de_bain": "Salle de bain",
}
_FILE = re.compile(r"^(models|previews)/[A-Za-z0-9_.-]{1,60}\.(glb|png)$")


class DimensionRange(StrictModel):
    """Plage admise pour une dimension : le catalogue donne un défaut, l'utilisateur ou le
    brief peuvent la régler dans [min, max]."""

    min: Meters = Field(gt=0.0)
    default: Meters = Field(gt=0.0)
    max: Meters = Field(gt=0.0)

    @model_validator(mode="after")
    def _ordered(self) -> DimensionRange:
        if not self.min <= self.default <= self.max:
            msg = f"plage incohérente : min {self.min} ≤ défaut {self.default} ≤ max {self.max}"
            raise ValueError(msg)
        return self

    def pick(self, wanted: float | None, what: str) -> float:
        if wanted is None:
            return self.default
        if not self.min <= wanted <= self.max:
            msg = f"{what} {wanted:.2f} m hors de la plage du catalogue [{self.min}, {self.max}]"
            raise ValueError(msg)
        return wanted


class Clearance(StrictModel):
    """Dégagement à laisser libre autour du meuble (règles d'aménagement, 3.2) : devant
    (ouverture des portes d'une armoire, recul d'une chaise), sur les côtés d'accès, derrière."""

    front: Meters = 0.0
    sides: Meters = 0.0
    back: Meters = 0.0


class CatalogAsset(StrictModel):
    id: str
    category: str
    """Catégorie fermée utilisée par le brief d'aménagement (ex. "bed_double", "desk")."""
    label: str
    """Nom affiché (français)."""
    width: DimensionRange
    depth: DimensionRange
    height: DimensionRange
    anchor: AssetAnchor = "free"
    clearance: Clearance = Field(default_factory=Clearance)
    tags: list[str] = Field(default_factory=list)
    walkable: bool = False
    """Traversable (tapis) : on marche dessus et on pose des meubles dessus ; les règles de
    chevauchement, de dégagement et de débattement l'ignorent."""
    room: Room = "sejour"
    model: str | None = None
    """Modèle 3D GLB, relatif à `assets/furniture/` (ex. "models/bedDouble.glb")."""
    preview: str | None = None
    """Vignette PNG, relative à `assets/furniture/`."""
    procedural: Literal["built_in_bed"] | None = None
    """Meuble paramétrique (H1) : géométrie calculée par OpenPlan Studio (`geom/built_in_bed.py`),
    sans modèle ni vignette tiers, donc sans licence externe à vérifier."""
    license: AssetLicense
    author: str
    notes: str = ""

    @model_validator(mode="after")
    def _accepted(self) -> CatalogAsset:
        if not _ID.match(self.id):
            msg = f"identifiant d'asset invalide : {self.id!r}"
            raise ValueError(msg)
        if self.license not in ACCEPTED_LICENSES:
            msg = (
                f"asset {self.id} : licence {self.license!r} refusée (acceptées : "
                f"{', '.join(sorted(ACCEPTED_LICENSES))})"
            )
            raise ValueError(msg)
        for kind, path in (("modèle", self.model), ("vignette", self.preview)):
            if path is not None and not _FILE.match(path):
                msg = f"asset {self.id} : chemin de {kind} invalide {path!r}"
                raise ValueError(msg)
        return self

    def bbox(
        self, width: float | None = None, depth: float | None = None, height: float | None = None
    ) -> BBox3:
        """Boîte du meuble : défauts du catalogue, ou valeurs demandées si elles sont dans la
        plage ; sinon ValueError explicite."""
        return BBox3(
            width=self.width.pick(width, "largeur"),
            depth=self.depth.pick(depth, "profondeur"),
            height=self.height.pick(height, "hauteur"),
        )


class Catalog(StrictModel):
    version: int = 1
    license_note: str = ""
    assets: list[CatalogAsset] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Catalog:
        seen: set[str] = set()
        for asset in self.assets:
            if asset.id in seen:
                msg = f"identifiant d'asset en double : {asset.id}"
                raise ValueError(msg)
            seen.add(asset.id)
        return self

    def get(self, asset_id: str) -> CatalogAsset | None:
        return next((a for a in self.assets if a.id == asset_id), None)

    @property
    def rooms(self) -> list[str]:
        return [r for r in ROOM_LABELS if any(a.room == r for a in self.assets)]

    @property
    def categories(self) -> list[str]:
        out: list[str] = []
        for asset in self.assets:
            if asset.category not in out:
                out.append(asset.category)
        return out


class ModelSource(StrictModel):
    """Une source de modèles 3D tiers, vérifiée à la main au moment du téléchargement."""

    id: str
    name: str
    url: str
    license: Literal["cc0"]
    """Seule licence acceptée pour un modèle tiers : CC0, sans obligation pour l'utilisateur."""
    license_url: str
    license_evidence: list[str] = Field(min_length=1)
    """Où et comment la licence a été lue (page officielle, fichier de licence du paquet)."""
    verified_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    archive: str
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[str] = Field(min_length=1)
    """Fichiers du dépôt qui viennent de cette source (relatifs à `assets/furniture/`)."""


class FurnitureSources(StrictModel):
    version: int = 1
    sources: list[ModelSource] = Field(min_length=1)

    def source_of(self, path: str) -> ModelSource | None:
        return next((s for s in self.sources if path in s.files), None)


def check_assets(catalog: Catalog, sources: FurnitureSources, root: Path) -> list[str]:
    """Problèmes de licence ou de fichier du catalogue : chaque modèle et chaque vignette doit
    exister sous `root` et figurer dans une source CC0 du registre ; chaque fichier du
    dossier doit être déclaré. Liste vide = conforme."""
    problems: list[str] = []
    declared: set[str] = set()
    own: set[str] = set()
    for asset in catalog.assets:
        for path in (asset.model, asset.preview):
            if path is None:
                continue
            declared.add(path)
            if not (root / path).is_file():
                problems.append(f"{asset.id} : fichier absent {path}")
            if asset.procedural is not None and path == asset.preview:
                # Vignette d'un meuble paramétrique (H1b) : rendue par OpenPlan Studio depuis sa
                # propre géométrie, sans source tierce.
                own.add(path)
                continue
            if sources.source_of(path) is None:
                problems.append(f"{asset.id} : {path} n'a pas de source dans le registre")
    for kind in ("models", "previews"):
        folder = root / kind
        if not folder.is_dir():
            continue
        for file in sorted(folder.iterdir()):
            rel = f"{kind}/{file.name}"
            if rel not in own and sources.source_of(rel) is None:
                problems.append(f"fichier sans source : {rel}")
    return problems
