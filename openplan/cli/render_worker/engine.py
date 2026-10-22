"""Moteur de rendu du poste, derrière une seule interface.

- `zimage` : `openplan.pipeline.zimage_render.render_scene` (KAN-88, écrit par Features),
  importé à l'appel seulement : absent, la demande échoue en « unavailable », rien ne casse ;
- `fake` : une image unie à la taille de la caméra, sans GPU (tests, e2e, essai d'installation).

Contrat de KAN-88 :
    render_scene(bake_path, camera_path, out_png, *, project_path=None, prompt=None) -> ZImageResult
    ZImageResult(seconds, vram_peak_mib, width, height, engine) ; lève ZImageUnavailable,
    ZImageVramGuard.
"""

from __future__ import annotations

import importlib
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from openplan.schemas.bake import RenderCamera
from openplan.schemas.render_queue import MAX_IMAGE_SIDE, FailureReason

ZIMAGE_MODULE = "openplan.pipeline.zimage_render"
FAKE_LONG_SIDE = 1024


@dataclass(frozen=True)
class RenderOutcome:
    seconds: float
    vram_peak_mib: int
    width: int
    height: int
    engine: str


class EngineFailedError(Exception):
    def __init__(self, reason: FailureReason) -> None:
        super().__init__(reason)
        self.reason: FailureReason = reason


def png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Un PNG uni (sans dépendance)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, 6))
        + chunk(b"IEND", b"")
    )


def fake_render(camera_path: Path, out_png: Path) -> RenderOutcome:
    started = time.perf_counter()
    camera = RenderCamera.model_validate_json(camera_path.read_text(encoding="utf-8"))
    scale = min(1.0, FAKE_LONG_SIDE / max(camera.width_px, camera.height_px))
    width = max(16, min(MAX_IMAGE_SIDE, round(camera.width_px * scale)))
    height = max(16, min(MAX_IMAGE_SIDE, round(camera.height_px * scale)))
    out_png.write_bytes(png_bytes(width, height, (196, 170, 140)))
    return RenderOutcome(
        seconds=time.perf_counter() - started,
        vram_peak_mib=0,
        width=width,
        height=height,
        engine="fake",
    )


def zimage_render(project: Path, bake: Path, camera: Path, out_png: Path) -> RenderOutcome:
    try:
        module = importlib.import_module(ZIMAGE_MODULE)
    except ImportError as exc:
        raise EngineFailedError("unavailable") from exc
    unavailable = getattr(module, "ZImageUnavailable", ())
    vram = getattr(module, "ZImageVramGuard", ())
    try:
        result = module.render_scene(bake, camera, out_png, project_path=project)
    except vram as exc:  # avant « unavailable » : ce pourrait en être une sous-classe
        raise EngineFailedError("vram") from exc
    except unavailable as exc:
        raise EngineFailedError("unavailable") from exc
    except TimeoutError as exc:
        raise EngineFailedError("timeout") from exc
    except Exception as exc:
        raise EngineFailedError("engine_error") from exc
    return RenderOutcome(
        seconds=float(result.seconds),
        vram_peak_mib=int(result.vram_peak_mib),
        width=int(result.width),
        height=int(result.height),
        engine=str(getattr(result, "engine", "zimage")),
    )


def render(engine: str, project: Path, bake: Path, camera: Path, out_png: Path) -> RenderOutcome:
    if engine == "fake":
        return fake_render(camera, out_png)
    if engine == "zimage":
        return zimage_render(project, bake, camera, out_png)
    raise EngineFailedError("unavailable")
