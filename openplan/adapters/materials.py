"""Matières de rendu à la demande (D2) : lecture du registre, état d'installation, téléchargement
**explicite** (`openplan materials pull`) avec vérification SHA-256 ; rien n'est téléchargé
automatiquement. Les fichiers vivent dans `models/materials/<id>/`, hors du dépôt."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import yaml

from openplan.resources import resource
from openplan.schemas.materials import MaterialEntry, MaterialRegistry

REGISTRY_PATH = resource("assets", "materials", "registry.yaml")
DEFAULT_MATERIALS_ROOT = Path("models") / "materials"
MARKER = "INSTALLED.json"
USER_AGENT = "openplan-studio (materials pull)"


class MaterialError(RuntimeError):
    """Téléchargement refusé ou incomplet (empreinte fausse, réseau)."""


def load_materials(path: Path = REGISTRY_PATH) -> MaterialRegistry:
    return MaterialRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def material_dir(entry: MaterialEntry, root: Path = DEFAULT_MATERIALS_ROOT) -> Path:
    return root / entry.id


def file_name(entry: MaterialEntry, channel: str) -> str:
    return f"{channel}.hdr" if channel == "hdri" else f"{channel}.jpg"


def is_installed(entry: MaterialEntry, root: Path = DEFAULT_MATERIALS_ROOT) -> bool:
    folder = material_dir(entry, root)
    return (folder / MARKER).is_file() and all(
        (folder / file_name(entry, c)).is_file() for c in entry.files
    )


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data: bytes = response.read()
    return data


def pull(entry: MaterialEntry, root: Path = DEFAULT_MATERIALS_ROOT) -> Path:
    """Télécharge les fichiers d'une matière et vérifie chaque empreinte ; un fichier faux est
    refusé et rien n'est marqué installé."""
    folder = material_dir(entry, root)
    folder.mkdir(parents=True, exist_ok=True)
    for channel, spec in entry.files.items():
        data = _fetch(spec.url)
        digest = hashlib.sha256(data).hexdigest()
        if digest != spec.sha256:
            msg = f"{entry.id}/{channel} : empreinte {digest[:12]}… au lieu de {spec.sha256[:12]}…"
            raise MaterialError(msg)
        (folder / file_name(entry, channel)).write_bytes(data)
    (folder / MARKER).write_text(
        json.dumps(
            {
                "id": entry.id,
                "license": entry.license,
                "license_evidence": entry.license_evidence,
                "page": entry.page,
                "verified_on": entry.verified_on,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return folder


def installed_map(root: Path = DEFAULT_MATERIALS_ROOT) -> dict[str, object]:
    """Ce que Blender reçoit : par finition, les trois images et la taille d'une répétition ;
    le ciel HDRI s'il est installé. Vide sans aucune matière (matières procédurales)."""
    out: dict[str, object] = {"surfaces": {}, "sky": None}
    surfaces: dict[str, object] = {}
    for entry in load_materials().materials:
        if not is_installed(entry, root):
            continue
        folder = material_dir(entry, root).resolve()
        if entry.kind == "surface" and entry.finish is not None:
            surfaces[entry.finish] = {
                **{c: str(folder / file_name(entry, c)) for c in entry.files},
                "tile_m": entry.tile_m,
                "brightness": entry.brightness,
                "saturation": entry.saturation,
                "planks": entry.planks,
            }
        else:
            out["sky"] = str(folder / file_name(entry, "hdri"))
    out["surfaces"] = surfaces
    return out
