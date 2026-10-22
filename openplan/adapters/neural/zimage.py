"""Finition locale des rendus par Z-Image-Turbo (KAN-88), moteur par défaut quand ses poids
sont installés et qu'une carte CUDA est là ; sinon la chaîne SD 1.5 (`finish.ControlledRefiner`)
reste celle d'avant, sans changement.

Chaîne (essai KAN-84b, 4/5 pour augus contre 2/5 à SD 1.5) : transformer Z-Image-Turbo en GGUF
Q4_K_M, ControlNet Union 2.1 complet guidé par les arêtes du rendu Blender (0,7), 8 pas sans
CFG, 1 024 px de grand côté ; encodeur de texte Qwen3-4B (GGUF) sur le processeur, donc la
description entière de KAN-86 (`engine="zimage"`).

La génération tourne dans un processus à part (`zimage_worker`) :
- **garde-fou VRAM** : la mémoire de la carte est lue toutes les 0,5 s ; au-delà de 5 000 Mio,
  le processus est tué et la finition échoue avec un message clair, sans geler la machine
  (le pic mesuré est de 4,9 Gio sur une carte de 6 Go). Même chose sous 1 500 Mio de RAM
  libre, ou au bout de 15 min ;
- **aucun réseau** : poids lus sur le disque seulement (`local_files_only`, variables
  `HF_*_OFFLINE`), connexions refusées dans le processus, et espace de noms réseau vide
  (`unshare -rn`) quand la machine le permet ;
- toute la mémoire (8 Go d'encodeur, 5 Go de blocs épinglés) est rendue à la fin du calcul.

La sortie reste `generated` : une finition sur le rendu des cotes, jamais une mesure.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from openplan.adapters.neural.weights import (
    DEFAULT_WEIGHTS_ROOT,
    is_installed,
    require_installed,
)
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    FinishRequest,
    GeneratedImage,
    InspirationRequest,
)

BASE_ID = "z_image_turbo@1.0"
BASE_SENTINEL = "vae/diffusion_pytorch_model.safetensors"
TRANSFORMER_ID = "z_image_turbo_gguf_q4km@1.0"
TRANSFORMER_FILE = "z-image-turbo-Q4_K_M.gguf"
CONTROLNET_ID = "z_image_controlnet_union@2.1"
CONTROLNET_FILE = "Z-Image-Turbo-Fun-Controlnet-Union-2.1-2602-8steps.safetensors"
ENCODER_ID = "qwen3_4b_gguf_q4km@1.0"
ENCODER_FILE = "Qwen3-4B-Q4_K_M.gguf"
PARTS: tuple[tuple[str, str], ...] = (
    (BASE_ID, BASE_SENTINEL),
    (TRANSFORMER_ID, TRANSFORMER_FILE),
    (CONTROLNET_ID, CONTROLNET_FILE),
    (ENCODER_ID, ENCODER_FILE),
)
Z_MODEL_IDS: tuple[str, ...] = tuple(model_id for model_id, _ in PARTS)

STEPS = 8
EDGE_SCALE = 0.7
"""0,9 tient mieux le plan mais lisse les grandes surfaces (pierre rendue en enduit)."""
LONG_SIDE_PX = 1024
VRAM_LIMIT_MIB = 5000
BUSY_GPU_MIB = 500
"""Au-delà, la carte sert déjà à autre chose : le calcul ne part pas (il dépasserait 5 Go)."""
RAM_MIN_MIB = 1500
TIMEOUT_S = 900.0
POLL_S = 0.5

Engine = Literal["zimage", "sd15"]
ENGINE_ENV = "OPENPLAN_FINISH_ENGINE"


class ZImageStoppedError(AdapterUnavailableError):
    """Le garde-fou a arrêté la génération (VRAM, RAM, durée) : rien n'a gelé."""


def installed(root: Path = DEFAULT_WEIGHTS_ROOT) -> bool:
    """Les quatre poids sont là (simple présence des fichiers, sans rien charger)."""
    return all(is_installed(model_id, root, sentinel) for model_id, sentinel in PARTS)


def choose_engine(requested: str | None, *, installed: bool, device: str) -> Engine:
    """Moteur de finition : Z-Image quand ses poids sont installés et qu'une carte CUDA est
    là (`auto`, défaut), sinon SD 1.5 ; `sd15` force la chaîne d'avant."""
    wanted = (requested or "auto").strip().lower()
    if wanted == "sd15":
        return "sd15"
    return "zimage" if installed and device == "cuda" else "sd15"


def z_size(width: int, height: int, long_side: int = LONG_SIDE_PX) -> tuple[int, int]:
    """Taille de génération : même format que le rendu, grand côté 1 024, multiples de 16
    (1 280 × 960 → 1 024 × 768 ; portrait 1 024 × 1 280 → 816 × 1 024)."""
    scale = long_side / max(width, height)
    return (
        max(16, round(width * scale / 16) * 16),
        max(16, round(height * scale / 16) * 16),
    )


def gpu_used_mib() -> int | None:
    """Mémoire occupée sur la carte (nvidia-smi), None si illisible."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def ram_available_mib() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class _Running(Protocol):
    def poll(self) -> int | None: ...


def watch(
    proc: _Running,
    *,
    kill: Callable[[], None],
    read_vram: Callable[[], int | None] = gpu_used_mib,
    read_ram: Callable[[], int | None] = ram_available_mib,
    limit_mib: int = VRAM_LIMIT_MIB,
    ram_min_mib: int = RAM_MIN_MIB,
    timeout_s: float = TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str | None, int]:
    """Surveille la génération jusqu'à sa fin : (raison de l'arrêt, ou None si elle a fini
    seule ; pic de VRAM lu). Au premier dépassement, `kill` est appelé aussitôt."""
    start = clock()
    peak = 0
    while proc.poll() is None:
        used = read_vram()
        free = read_ram()
        reason: str | None = None
        if used is None:
            reason = "nvidia-smi est devenu illisible, la mémoire de la carte n'est plus surveillée"
        elif used > limit_mib:
            reason = f"la carte graphique a dépassé {limit_mib} Mio ({used} Mio)"
        elif free is not None and free < ram_min_mib:
            reason = f"il ne restait que {free} Mio de mémoire vive (minimum {ram_min_mib})"
        elif clock() - start > timeout_s:
            reason = f"plus de {round(timeout_s / 60)} min de calcul"
        if used is not None:
            peak = max(peak, used)
        if reason is not None:
            kill()
            return reason, peak
        sleep(POLL_S)
    return None, peak


def edge_control(edges: np.ndarray, size: tuple[int, int]) -> Any:
    """Carte d'arêtes du rendu à la taille de génération, en blanc sur noir : réduite par
    moyenne de zone puis re-seuillée, pour ne pas perdre les traits d'un pixel."""
    from PIL import Image

    small = Image.fromarray(edges.astype(np.uint8)).convert("L").resize(size, Image.Resampling.BOX)
    return small.point(lambda p: 255 if p > 40 else 0).convert("RGB")


def _no_network_prefix() -> list[str]:
    """`unshare -rn` (espace de noms réseau vide) quand la machine le permet."""
    if shutil.which("unshare") is None:
        return []
    try:
        ok = subprocess.run(["unshare", "-rn", "true"], timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return ["unshare", "-rn"] if ok else []


class ZImageFinisher:
    """Implémente ImageFinisher (et `inspire`, laissé à la chaîne SD 1.5 si elle est
    installée : l'inspiration ne change pas avec KAN-88)."""

    prompt_engine: Engine = "zimage"

    def __init__(
        self,
        weights_root: Path = DEFAULT_WEIGHTS_ROOT,
        *,
        fallback: Callable[[], Any] | None = None,
    ) -> None:
        self.dirs = {
            "base": require_installed(BASE_ID, weights_root, BASE_SENTINEL),
            "transformer": require_installed(TRANSFORMER_ID, weights_root, TRANSFORMER_FILE),
            "controlnet": require_installed(CONTROLNET_ID, weights_root, CONTROLNET_FILE),
            "encoder": require_installed(ENCODER_ID, weights_root, ENCODER_FILE),
        }
        self.model_ids = [BASE_ID, TRANSFORMER_ID, CONTROLNET_ID, ENCODER_ID]
        self._make_fallback = fallback
        self._fallback: Any = None
        self._lock = threading.Lock()
        self._prefix: list[str] | None = None

    def _sd(self) -> Any:
        if self._fallback is None:
            if self._make_fallback is None:
                msg = "inspiration : la chaîne SD 1.5 n'est pas installée"
                raise AdapterUnavailableError(msg)
            self._fallback = self._make_fallback()
        return self._fallback

    def inspire(self, request: InspirationRequest) -> GeneratedImage:
        result: GeneratedImage = self._sd().inspire(request)
        return result

    def _release_fallback(self) -> None:
        """Avant Z-Image, la chaîne SD 1.5 rend la carte (elle se recharge à son prochain
        calcul) : les deux ne tiennent pas ensemble sous 5 Go."""
        if self._fallback is not None and getattr(self._fallback, "_pipe", None) is not None:
            from openplan.adapters.neural.idle import free_gpu_cache

            self._fallback._unload()
            free_gpu_cache()

    def finish(self, request: FinishRequest) -> GeneratedImage:
        with self._lock:
            return self._finish(request)

    def _finish(self, request: FinishRequest) -> GeneratedImage:
        if request.edges is None:
            msg = "Z-Image se guide par les arêtes du rendu : aucune carte d'arêtes fournie"
            raise AdapterUnavailableError(msg)
        self._release_fallback()
        used = gpu_used_mib()
        if used is None:
            msg = (
                "rendu Z-Image non lancé : nvidia-smi illisible, "
                "la mémoire de la carte ne serait pas surveillée"
            )
            raise ZImageStoppedError(msg)
        if used > BUSY_GPU_MIB:
            msg = f"rendu Z-Image non lancé : la carte graphique sert déjà ({used} Mio occupés)"
            raise ZImageStoppedError(msg)
        if self._prefix is None:
            self._prefix = _no_network_prefix()
        from PIL import Image

        started = time.perf_counter()
        h, w = request.image.shape[:2]
        size = z_size(w, h)
        work = Path(tempfile.mkdtemp(prefix="openplan-zimage-"))
        try:
            edge_control(request.edges, size).save(work / "control.png")
            job = {
                "dirs": {k: str(v.resolve()) for k, v in self.dirs.items()},
                "transformer_file": TRANSFORMER_FILE,
                "controlnet_file": CONTROLNET_FILE,
                "encoder_file": ENCODER_FILE,
                "prompt": request.prompt,
                "control": str(work / "control.png"),
                "width": size[0],
                "height": size[1],
                "seed": request.seed,
                "steps": STEPS,
                "scale": EDGE_SCALE,
                "out": str(work / "out.png"),
                "result": str(work / "result.json"),
            }
            (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
            env = {
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "DIFFUSERS_OFFLINE": "1",
            }
            cmd = [
                *self._prefix,
                sys.executable,
                "-m",
                "openplan.adapters.neural.zimage_worker",
                str(work / "job.json"),
            ]
            with (work / "worker.log").open("wb") as log:
                proc = subprocess.Popen(
                    cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
                )

                def kill() -> None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=30)

                reason, peak = watch(proc, kill=kill)
            if reason is not None:
                msg = f"rendu Z-Image arrêté par le garde-fou : {reason}. Le rendu Blender reste."
                raise ZImageStoppedError(msg)
            if proc.returncode != 0 or not (work / "out.png").is_file():
                tail = (work / "worker.log").read_text(encoding="utf-8", errors="replace")
                last = " | ".join(tail.strip().splitlines()[-2:])[-300:]
                msg = f"rendu Z-Image en échec (code {proc.returncode}) : {last}"
                raise AdapterUnavailableError(msg)
            result = json.loads((work / "result.json").read_text(encoding="utf-8"))
            if result.get("network_attempts"):
                msg = "rendu Z-Image refusé : une connexion réseau a été tentée pendant le calcul"
                raise AdapterUnavailableError(msg)
            with Image.open(work / "out.png") as img:
                out = img.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
            return GeneratedImage(
                pixels=np.asarray(out, dtype=np.uint8),
                seed=request.seed,
                model_ids=list(self.model_ids),
                duration_s=time.perf_counter() - started,
                peak_vram_mb=round(peak * 1.048576),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
