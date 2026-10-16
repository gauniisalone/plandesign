"""Désigner un élément dans un rendu (P4) : la carte des éléments écrite par Blender à côté du
rendu (`idmap.npz` + `idmap.json`, voir blender/scripts/idmap.py) dit, pour chaque pixel,
l'élément du projet qui s'y voit. Le décor qui habille un meuble (linge, lampe de chevet,
livres) y porte déjà l'identifiant de ce meuble ; le décor sans porteur (plante, cadre, tapis,
rideau, suspension) garde « decor-n ».

Un rendu plus ancien, sans carte, la reçoit à la demande depuis son `scene.blend` (une passe
d'environ 1 à 2 s). Les coordonnées sont relatives (0 à 1) : la même carte sert pour l'image
Blender, sa finition (A) et l'image d'inspiration (B), qui gardent le cadrage.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal
from uuid import UUID

import numpy as np

from openplan.pipeline.blender_bridge import SCRIPT, BlenderError, find_blender
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import Project

IDMAP_NPZ = "idmap.npz"
IDMAP_JSON = "idmap.json"
IDMAP_SCRIPT = SCRIPT.parent / "idmap.py"
PickKind = Literal["element", "decor", "none"]


class PickResult(StrictModel):
    """Ce qu'un clic sur un rendu désigne."""

    kind: PickKind
    """« element » : un élément du projet ; « decor » : décor sans porteur ; « none » : rien."""
    element_id: UUID | None = None
    element_type: str | None = None
    """« furniture », « wall », « room », « opening », « roof_plane »…"""
    category: str | None = None
    """Meuble : sa catégorie du catalogue (pour « Remplacer »)."""
    name: str
    decor_kind: str | None = None
    """Décor sans porteur : « plant », « frame », « jute_rug »…"""
    on_demand: bool = False
    """La carte vient d'être faite pour ce clic (rendu plus ancien que P4)."""


class PickError(ValueError):
    """Rendu sans carte et sans scène Blender pour la faire."""


def has_idmap(render_dir: Path) -> bool:
    return (render_dir / IDMAP_NPZ).is_file() and (render_dir / IDMAP_JSON).is_file()


def make_idmap(render_dir: Path, blender_bin: str | None, timeout_s: float = 120.0) -> None:
    """Carte d'un rendu plus ancien, depuis son `scene.blend`."""
    blend = render_dir / "scene.blend"
    if not blend.is_file():
        msg = "ce rendu n'a pas gardé sa scène Blender : refaites-le pour y désigner un meuble"
        raise PickError(msg)
    binary = find_blender(blender_bin)
    if binary is None:
        msg = "Blender introuvable : la carte des éléments de ce rendu ne peut pas être faite"
        raise BlenderError(msg)
    cmd = [str(binary), "-b", "--python", str(IDMAP_SCRIPT), "--", str(blend), str(render_dir)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    if done.returncode != 0 or not has_idmap(render_dir):
        tail = "\n".join((done.stdout + done.stderr).splitlines()[-8:])
        msg = f"carte des éléments non faite :\n{tail}"
        raise BlenderError(msg)


def _decor_kind(render_dir: Path, key: str) -> str | None:
    bake = render_dir / "bake.json"
    try:
        index = int(key.split("-", 1)[1].split("_", 1)[0].split(".", 1)[0])
        decor = json.loads(bake.read_text(encoding="utf-8")).get("decor", [])
        kind = decor[index].get("kind")
    except (ValueError, IndexError, OSError, AttributeError):
        return None
    return kind if isinstance(kind, str) else None


DECOR_LABELS = {
    "plant": "plante",
    "floor_plant": "grande plante",
    "frame": "cadre",
    "jute_rug": "tapis",
    "curtain": "rideau",
    "pendant": "suspension",
    "table_lamp": "lampe",
    "pillow": "coussin",
    "throw": "plaid",
    "duvet": "couette",
    "books": "livres",
}


OUTLINE_RGB = (255, 196, 0)
"""Contour de l'élément désigné (P4b) : jaune vif, lisible sur un rendu clair comme sombre."""


def _value_at(render_dir: Path, x: float, y: float) -> tuple[np.ndarray, int]:
    index = np.load(render_dir / IDMAP_NPZ)["index"]
    h, w = index.shape
    col = min(w - 1, max(0, int(x * w)))
    row = min(h - 1, max(0, int(y * h)))
    return index, int(index[row, col])


def _grow(mask: np.ndarray, steps: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def outline_png(render_dir: Path, x: float, y: float, *, width_px: int = 4) -> bytes:
    """Contour (et léger voile) de ce qui se voit en (x, y), en PNG transparent à la taille de
    la carte : posé sur l'image du rendu, il confirme le choix avant l'action. Vide sur le fond
    ou sans carte."""
    from io import BytesIO

    from PIL import Image

    if not has_idmap(render_dir):
        msg = "ce rendu n'a pas encore de carte des éléments"
        raise PickError(msg)
    index, value = _value_at(render_dir, x, y)
    h, w = index.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    if value:
        mask = index == value
        inner = ~_grow(~mask, 1)
        edge = _grow(mask & ~inner, width_px // 2)
        rgba[mask] = (*OUTLINE_RGB, 46)
        rgba[edge] = (*OUTLINE_RGB, 255)
    buffer = BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def pick(
    project: Project,
    render_dir: Path,
    x: float,
    y: float,
    *,
    blender_bin: str | None = None,
) -> PickResult:
    """L'élément vu en (x, y), coordonnées relatives à l'image (0 en haut à gauche)."""
    on_demand = False
    if not has_idmap(render_dir):
        make_idmap(render_dir, blender_bin)
        on_demand = True
    _, value = _value_at(render_dir, x, y)
    ids: list[str] = json.loads((render_dir / IDMAP_JSON).read_text(encoding="utf-8"))["ids"]
    if value == 0 or value > len(ids):
        return PickResult(kind="none", name="rien", on_demand=on_demand)
    key = ids[value - 1]
    try:
        element_id = UUID(key)
    except ValueError:
        kind = _decor_kind(render_dir, key) if key.startswith("decor-") else None
        label = DECOR_LABELS.get(kind or "", "élément de décor")
        return PickResult(kind="decor", name=label, decor_kind=kind, on_demand=on_demand)
    for level in project.levels.values():
        element = level.elements.get(element_id)
        if element is None:
            continue
        name = getattr(element, "name", None) or getattr(element, "category", None)
        return PickResult(
            kind="element",
            element_id=element_id,
            element_type=element.type,
            category=getattr(element, "category", None),
            name=str(name or element.type),
            on_demand=on_demand,
        )
    # L'élément vu au moment du rendu n'existe plus (retiré depuis).
    return PickResult(kind="none", name="élément retiré depuis ce rendu", on_demand=on_demand)
