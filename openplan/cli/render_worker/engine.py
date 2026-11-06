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
from dataclasses import dataclass, replace
from pathlib import Path

from openplan.adapters.protocols import RoomElementDetector
from openplan.schemas.bake import RenderCamera
from openplan.schemas.fidelity import FidelityVerdict
from openplan.schemas.render_queue import MAX_IMAGE_SIDE, FailureReason

ZIMAGE_MODULE = "openplan.pipeline.zimage_render"
FAKE_LONG_SIDE = 1024
FAKE_STYLE_WORDS = ("scandinavian", "bright")
"""KAN-97 : ce que le moteur factice « lit » dans des images de style (tests, e2e)."""


@dataclass(frozen=True)
class RenderOutcome:
    seconds: float
    vram_peak_mib: int
    width: int
    height: int
    engine: str
    style_words: list[str] | None = None
    """KAN-97 : mots de style retenus ; None sans image de style (ou moteur sans eux)."""
    fidelity: dict[str, object] | None = None
    """KAN-154 : le verdict de fidélité (`FidelityVerdict` en JSON) ; None drapeau éteint."""


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
    # Sonar S8707, faux positifs (deux lignes) : chemins fixes du dossier de la tâche, choisis par
    # le poste (sandbox.py), jamais par le serveur.
    started = time.perf_counter()
    raw = camera_path.read_text(encoding="utf-8")  # NOSONAR
    camera = RenderCamera.model_validate_json(raw)
    scale = min(1.0, FAKE_LONG_SIDE / max(camera.width_px, camera.height_px))
    width = max(16, min(MAX_IMAGE_SIDE, round(camera.width_px * scale)))
    height = max(16, min(MAX_IMAGE_SIDE, round(camera.height_px * scale)))
    out_png.write_bytes(png_bytes(width, height, (196, 170, 140)))  # NOSONAR
    return RenderOutcome(
        seconds=time.perf_counter() - started,
        vram_peak_mib=0,
        width=width,
        height=height,
        engine="fake",
    )


def zimage_render(
    project: Path,
    bake: Path,
    camera: Path,
    out_png: Path,
    seed: int | None = None,
    style_dir: Path | None = None,
    style_ignored: tuple[str, ...] = (),
) -> RenderOutcome:
    try:
        module = importlib.import_module(ZIMAGE_MODULE)
    except ImportError as exc:
        raise EngineFailedError("unavailable") from exc
    unavailable = getattr(module, "ZImageUnavailable", ())
    vram = getattr(module, "ZImageVramGuard", ())
    # KAN-91 : une variante passe son tirage ; sinon, celui par défaut du moteur (KAN-88).
    extra: dict[str, object] = {} if seed is None else {"seed": seed}
    if style_dir is not None:  # KAN-97 : images d'inspiration déposées par le poste
        extra["style_dir"] = style_dir
        if style_ignored:  # KAN-130 : sans mot désactivé, l'appel reste celui de KAN-97
            extra["style_ignored"] = style_ignored

    def once(seed_now: int | None) -> object:
        args = dict(extra) if seed_now is None else {**extra, "seed": seed_now}
        try:
            return module.render_scene(bake, camera, out_png, project_path=project, **args)
        except vram as exc:  # avant « unavailable » : ce pourrait en être une sous-classe
            raise EngineFailedError("vram") from exc
        except unavailable as exc:
            raise EngineFailedError("unavailable") from exc
        except TimeoutError as exc:
            raise EngineFailedError("timeout") from exc
        except Exception as exc:
            raise EngineFailedError("engine_error") from exc

    from openplan.pipeline import fidelity

    verdict: dict[str, object] | None = None
    if fidelity.enabled():
        # KAN-154 : contrôle, puis relance avec une autre graine (deux fois au plus) ; le
        # dernier tirage reste, marqué non conforme s'il l'est encore.
        held: list[object] = []
        start = seed if seed is not None else int(getattr(module, "DEFAULT_SEED", 1234))
        checked = fidelity.render_checked(
            lambda s: held.append(once(s)), lambda n: check_render(out_png, n), start
        )
        result = held[-1]
        verdict = checked.verdict.model_dump(mode="json")
    else:
        result = once(seed)
    return RenderOutcome(
        seconds=float(result.seconds),  # type: ignore[attr-defined]
        vram_peak_mib=int(result.vram_peak_mib),  # type: ignore[attr-defined]
        width=int(result.width),  # type: ignore[attr-defined]
        height=int(result.height),  # type: ignore[attr-defined]
        engine=str(getattr(result, "engine", "zimage")),
        style_words=_words(getattr(result, "style_words", None)),
        fidelity=verdict,
    )


def check_render(out_png: Path, attempt: int) -> FidelityVerdict:
    """KAN-154 : le rendu écrit et ses guides (`<rendu>.guides.npz`, écrits par render_scene),
    passés aux mesures ; le détecteur d'ouvertures sur le processeur s'il est installé."""
    import numpy as np
    from PIL import Image

    from openplan.pipeline import fidelity
    from openplan.pipeline.guides import load_guides
    from openplan.pipeline.zimage_render import guides_path, models_root

    with Image.open(out_png) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    guides = load_guides(guides_path(out_png))
    return fidelity.check_fidelity(
        pixels, guides, fidelity.default_measures(_detector(models_root())), attempts=attempt
    )


_DETECTORS: dict[Path, RoomElementDetector | None] = {}


def _detector(root: Path) -> RoomElementDetector | None:
    """OWLv2 sur le processeur (la carte reste à Z-Image), chargé une fois ; absent : None,
    et la mesure des ouvertures n'est pas faite (elle ne fait pas échouer le verdict)."""
    if root not in _DETECTORS:
        from openplan.adapters.protocols import AdapterUnavailableError
        from openplan.adapters.registry import build_opening_detector

        try:
            _DETECTORS[root] = build_opening_detector(root, "cpu")
        except AdapterUnavailableError:
            _DETECTORS[root] = None
    return _DETECTORS[root]


def _words(value: object) -> list[str] | None:
    if value is None or not isinstance(value, list | tuple):
        return None
    return [str(w) for w in value]


def render(
    engine: str,
    project: Path,
    bake: Path,
    camera: Path,
    out_png: Path,
    *,
    seed: int | None = None,
    style_dir: Path | None = None,
    style_ignored: tuple[str, ...] = (),
) -> RenderOutcome:
    if engine == "fake":
        outcome = fake_render(camera, out_png)
        # Le moteur factice des tests lit les images de style sans modèle : des mots fixes,
        # moins ceux désactivés (KAN-130).
        if style_dir is not None and any(style_dir.iterdir()):
            words = [w for w in FAKE_STYLE_WORDS if w not in style_ignored]
            return replace(outcome, style_words=words)
        return outcome
    if engine == "zimage":
        return zimage_render(project, bake, camera, out_png, seed, style_dir, style_ignored)
    raise EngineFailedError("unavailable")
