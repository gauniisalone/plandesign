"""Pont Blender : bake JSON → sous-processus `blender -b --python` → .blend, GLB, aperçu.

Le script `blender/scripts/build_scene.py` n'importe que bpy et la bibliothèque standard :
la version de Python embarquée par Blender n'a pas à correspondre à celle du projet.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from openplan.adapters.materials import DEFAULT_MATERIALS_ROOT, installed_map
from openplan.pipeline.scene3d import bake_level
from openplan.resources import resource
from openplan.scene.catalog import FURNITURE_DIR
from openplan.schemas.bake import BakeStyle, RenderCamera
from openplan.schemas.results import Artifact, StepResult
from openplan.schemas.scene import Project

SCRIPT = resource("blender", "scripts", "build_scene.py")
STEP = "rendering.blender"
_CANDIDATES = ("blender", "~/opt/blender/blender", "/opt/blender/blender", "/usr/bin/blender")


class BlenderError(RuntimeError):
    """Blender absent, échec du script ou rapport illisible."""


def find_blender(hint: str | None = None) -> Path | None:
    """Binaire Blender.

    Un chemin explicite (autre que le simple nom « blender ») fait autorité : s'il n'existe
    pas, on ne cherche pas ailleurs, pour ne jamais lancer un Blender que l'utilisateur n'a
    pas désigné. Sans indication, on essaie le PATH puis les emplacements usuels.
    """
    if hint and hint != "blender":
        return _resolve(hint)
    for candidate in _CANDIDATES:
        found = _resolve(candidate)
        if found is not None:
            return found
    return None


def _resolve(candidate: str) -> Path | None:
    expanded = Path(candidate).expanduser()
    if expanded.is_file():
        return expanded
    found = shutil.which(candidate)
    return Path(found) if found else None


def materials_root() -> Path:
    """Dossier des matières installées : OPENPLAN_MATERIALS_DIR, sinon models/materials."""
    value = os.environ.get("OPENPLAN_MATERIALS_DIR")
    return Path(value) if value else DEFAULT_MATERIALS_ROOT


def run_blender(  # noqa: PLR0913, PLR0912, PLR0915 - options de rendu nommées, facultatives
    project: Project,
    level_id: UUID,
    out_dir: Path,
    *,
    blender_bin: str | None = None,
    preview: bool = True,
    timeout_s: float = 600.0,
    run_id: UUID | None = None,
    camera: RenderCamera | None = None,
    samples: int = 8,
    denoise: bool = False,
    style: BakeStyle = "provenance",
    gpu: bool = False,
    depth: bool = False,
    light: dict[str, Any] | None = None,
    section: tuple[str, float] | None = None,
    section_keep_above: bool = False,
    backdrop: bool = False,
    idmap: bool = False,
) -> StepResult:
    """`camera` impose la pose et la résolution du rendu (fixtures synthétiques, rendu depuis
    la photo) ; `style="realistic"` remplace les couleurs de provenance par des matières
    procédurales ; `gpu` demande Cycles sur GPU (CUDA/OptiX), avec repli CPU silencieux ;
    `depth` ajoute la passe de profondeur Cycles (`depth_z.npy`, mètres) à l'aperçu ;
    `light` (voir `render.daylight_spec`) place le soleil, le ciel et les lampes ;
    `section` (axe, position) tranche la scène et garde le côté inférieur, ou supérieur avec
    `section_keep_above` ; `backdrop` remplace le ciel vu par la caméra par un fond gris
    clair uni (rendu en coupe) ; `idmap` écrit, après l'aperçu, la carte des éléments
    (P4 : `idmap.npz` et `idmap.json`, une passe d'environ 1 s)."""
    binary = find_blender(blender_bin)
    if binary is None:
        msg = "Blender introuvable : renseigner OPENPLAN_BLENDER_BIN ou installer Blender 4.2+"
        raise BlenderError(msg)
    if not SCRIPT.is_file():
        msg = f"script Blender absent : {SCRIPT}"
        raise BlenderError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    bake_path = out_dir / "bake.json"
    bake_path.write_text(bake_level(project, level_id, style).model_dump_json(), encoding="utf-8")
    blend, glb, png, report = (
        out_dir / n for n in ("scene.blend", "scene.glb", "preview.png", "blender_report.json")
    )
    cmd = [
        str(binary),
        "-b",
        "--python",
        str(SCRIPT),
        "--",
        "--bake",
        str(bake_path),
        "--out-blend",
        str(blend),
        "--out-glb",
        str(glb),
        "--report",
        str(report),
        "--assets",
        str(FURNITURE_DIR),
    ]
    materials = installed_map(materials_root())
    if materials["surfaces"] or materials["sky"]:
        materials_path = out_dir / "materials.json"
        materials_path.write_text(json.dumps(materials, indent=2), encoding="utf-8")
        cmd += ["--materials", str(materials_path)]
    depth_npy = out_dir / "depth_z.npy"
    if preview:
        cmd += ["--preview", str(png), "--samples", str(samples)]
        if depth:
            cmd += ["--depth", str(depth_npy)]
    if denoise:
        cmd.append("--denoise")
    cmd += ["--style", style]
    if gpu:
        cmd.append("--gpu")
    if camera is not None:
        camera_path = out_dir / "camera.json"
        camera_path.write_text(camera.model_dump_json(indent=2), encoding="utf-8")
        cmd += ["--camera", str(camera_path)]
    if section is not None:
        side = ":+" if section_keep_above else ""
        cmd += ["--section", f"{section[0]}:{section[1]:.4f}{side}"]
    if backdrop:
        cmd.append("--backdrop")
    if idmap and preview:
        cmd += ["--idmap", str(out_dir)]
    if light is not None:
        light_path = out_dir / "light.json"
        light_path.write_text(json.dumps(light, indent=2), encoding="utf-8")
        cmd += ["--light", str(light_path)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"Blender a dépassé {timeout_s:.0f} s"
        raise BlenderError(msg) from exc
    duration = time.perf_counter() - started
    if completed.returncode != 0 or not report.is_file():
        tail = "\n".join(completed.stdout.splitlines()[-15:] + completed.stderr.splitlines()[-15:])
        msg = f"Blender a échoué (code {completed.returncode}) :\n{tail}"
        raise BlenderError(msg)
    data = json.loads(report.read_text(encoding="utf-8"))
    if data.get("error"):
        msg = f"script Blender en erreur : {data['error']}"
        raise BlenderError(msg)
    artifacts = [
        Artifact(
            name="bake.json",
            path=bake_path,
            media_type="application/json",
            description="Prismes 3D",
        )
    ]
    device = str(data.get("device", "CPU"))
    render_s = data.get("render_s")
    preview_desc = f"Rendu Cycles {device}, {samples} échantillons"
    if isinstance(render_s, int | float):
        preview_desc += f", {render_s:.0f} s"
    for path, media, desc in (
        (blend, "application/x-blender", "Scène Blender"),
        (glb, "model/gltf-binary", "GLB exporté par Blender"),
        (png, "image/png", preview_desc),
        (depth_npy, "application/octet-stream", "Passe de profondeur Cycles (m, HxW float32)"),
        (out_dir / "idmap.npz", "application/octet-stream", "Carte des éléments (P4)"),
        (report, "application/json", "Rapport Blender"),
    ):
        if path.is_file():
            artifacts.append(
                Artifact(name=path.name, path=path, media_type=media, description=desc)
            )
    return StepResult(
        step=STEP,
        confidence=1.0,
        warnings=[str(w) for w in data.get("warnings", [])],
        artifacts=artifacts,
        duration_s=duration,
        model_ids=[f"blender@{data.get('blender_version', '?')}", f"cycles@{device}"],
        assumptions=[],
        patch=[],
    )


def new_run_dir(
    runs_root: Path, run_id: UUID | None = None, step: str = "blender"
) -> tuple[UUID, Path]:
    rid = run_id or uuid4()
    return rid, runs_root / str(rid) / step
