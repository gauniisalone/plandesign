"""Processus de génération Z-Image (KAN-88), lancé par `zimage.ZImageFinisher` pour une vue :
`python -m openplan.adapters.neural.zimage_worker <job.json>`.

1. Réseau coupé dans le processus avant tout import lourd (toute connexion hors socket Unix
   est refusée et comptée), poids lus sur le disque seulement.
2. Encodage du texte par Qwen3-4B (GGUF) sur le processeur, comme
   `ZImagePipeline._encode_prompt` : gabarit de conversation, avant-dernière couche, jetons utiles.
   L'encodeur (≈ 8 Go en bf16) est rendu avant le chargement du reste.
3. Mémoire de la carte (6 Go) : le ControlNet complet est lu en projection mémoire (mmap),
   ses couches et les 30 blocs du transformer GGUF restent sur le processeur et montent un par
   un sur la carte dans des tampons fixes ; cache vidé après chaque couche de contrôle ; VAE
   en tuiles de 512 px. Pic mesuré à l'essai : 3,8 Go (torch), 4,9 Gio (nvidia-smi).

Le processus parent surveille la carte et tue celui-ci au-delà de 5 000 Mio.
"""

from __future__ import annotations

import gc
import importlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

for _key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "DIFFUSERS_OFFLINE"):
    os.environ[_key] = "1"

ATTEMPTS: list[str] = []
"""Connexions tentées pendant le calcul (attendu : aucune)."""

CONTROLNET_CONFIG: dict[str, Any] = {
    "add_control_noise_refiner": "control_noise_refiner",
    "all_f_patch_size": [1],
    "all_patch_size": [2],
    "control_in_dim": 33,
    "control_layers_places": list(range(0, 30, 2)),
    "control_refiner_layers_places": [0, 1],
    "dim": 3840,
    "n_heads": 30,
    "n_kv_heads": 30,
    "n_refiner_layers": 2,
    "norm_eps": 1e-05,
    "qk_norm": True,
}
"""Configuration du ControlNet Union 2.1 complet (VideoX-Fun `z_image_control_2.1.yaml` : 15
couches de contrôle aux places 0, 2, …, 28) ; le dépôt des poids n'en publie pas."""

MAX_TOKENS = 512


def block_network() -> None:
    """Toute connexion hors socket Unix lève une erreur et est comptée."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded(self: socket.socket, address: Any, *args: Any) -> None:
        if self.family != socket.AF_UNIX:
            ATTEMPTS.append(repr(address))
            msg = "KAN-88 : réseau interdit pendant la génération"
            raise OSError(msg)
        connect(self, address, *args)

    def guarded_ex(self: socket.socket, address: Any, *args: Any) -> int:
        if self.family != socket.AF_UNIX:
            ATTEMPTS.append(repr(address))
            return 101  # ENETUNREACH
        return connect_ex(self, address, *args)

    socket.socket.connect = guarded  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = guarded_ex  # type: ignore[method-assign,assignment]


def _module(name: str) -> Any:
    """Import dynamique (diffusers, transformers) : mypy donne le même résultat avec ou sans
    l'extra `neural` installé."""
    return importlib.import_module(name)


def encode(prompt: str, base: Path, encoder: Path, encoder_file: str) -> Any:
    """Plongements du texte (jetons utiles × 2 560), calculés sur le processeur."""
    import torch

    transformers = _module("transformers")
    torch.set_num_threads(min(12, os.cpu_count() or 4))
    tok = transformers.AutoTokenizer.from_pretrained(base / "tokenizer", local_files_only=True)
    model = transformers.AutoModelForCausalLM.from_pretrained(
        encoder, gguf_file=encoder_file, dtype=torch.bfloat16, local_files_only=True
    ).eval()
    text = tok.apply_chat_template(
        [{"role": "user", "content": prompt}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=True,
    )
    ids = tok(
        [text], padding="max_length", max_length=MAX_TOKENS, truncation=True, return_tensors="pt"
    )
    with torch.inference_mode():
        states = model(
            input_ids=ids.input_ids,
            attention_mask=ids.attention_mask.bool(),
            output_hidden_states=True,
        ).hidden_states[-2]
    emb = states[0][ids.attention_mask[0].bool()].contiguous().clone()
    del model, states
    gc.collect()
    return emb


def stream_blocks(blocks: list[Any], torch: Any, *, nslots: int, pin: bool) -> None:
    """Blocs de même architecture gardés sur le processeur ; sur la carte, `nslots` tampons
    FIXES (l'allocateur multi-flux gonflait sinon la réserve à 5,5 Go). Bloc i → tampon
    i % nslots, copié sur un flux à part, bloc suivant préchargé. Source non épinglée (fichier
    projeté) : un seul tampon hôte épinglé de la taille d'un bloc sert d'étape (sinon le pilote
    WSL projette la mémoire hôte : +2,3 Gio vus par nvidia-smi)."""
    dev = torch.device("cuda")
    copy_stream = torch.cuda.Stream()
    n = len(blocks)
    cpu: list[list[tuple[Any, Any]]] = []
    for b in blocks:
        params = list(b.parameters()) + list(b.buffers())
        cpu.append([(p, p.data.pin_memory() if pin else p.data) for p in params])
        for p, c in cpu[-1]:
            p.data = c
    layout: list[tuple[int, list[tuple[int, int, Any, tuple[int, ...]]]]] = []
    for rec in cpu:
        off, lay = 0, []
        for _, c in rec:
            nb = c.numel() * c.element_size()
            lay.append((off, nb, c.dtype, tuple(c.shape)))
            off += (nb + 255) // 256 * 256
        layout.append((off, lay))
    size = max(o for o, _ in layout)
    ns = min(nslots, n)
    arenas = [torch.empty(size, dtype=torch.uint8, device=dev) for _ in range(ns)]
    views = [
        [arenas[i % ns][o : o + nb].view(dt).view(sh) for o, nb, dt, sh in layout[i][1]]
        for i in range(n)
    ]
    stage = None if pin else torch.empty(size, dtype=torch.uint8).pin_memory()
    sviews = (
        None
        if stage is None
        else [
            [stage[o : o + nb].view(dt).view(sh) for o, nb, dt, sh in layout[i][1]]
            for i in range(n)
        ]
    )
    free_ev: dict[int, Any] = {}
    ready: dict[int, Any] = {}

    def load(i: int) -> None:
        if i >= n or i in ready:
            return
        s = i % ns
        if sviews is not None:
            copy_stream.synchronize()
            for (_, c), hv in zip(cpu[i], sviews[i], strict=True):
                hv.copy_(c)
        with torch.cuda.stream(copy_stream):
            if s in free_ev:
                copy_stream.wait_event(free_ev.pop(s))
            for k, ((_, c), buf) in enumerate(zip(cpu[i], views[i], strict=True)):
                buf.copy_(c if sviews is None else sviews[i][k], non_blocking=True)
            ev = torch.cuda.Event()
            ev.record(copy_stream)
        ready[i] = ev

    def pre(i: int) -> Any:
        def hook(_mod: Any, _args: Any) -> None:
            load(i)
            torch.cuda.current_stream().wait_event(ready.pop(i))
            for (p, _), buf in zip(cpu[i], views[i], strict=True):
                p.data = buf
            if ns > 1:
                load(i + 1)

        return hook

    def post(i: int) -> Any:
        def hook(_mod: Any, _args: Any, out: Any) -> Any:
            ev = torch.cuda.Event()
            ev.record(torch.cuda.current_stream())
            free_ev[i % ns] = ev
            return out

        return hook

    for i, b in enumerate(blocks):
        b.register_forward_pre_hook(pre(i))
        b.register_forward_hook(post(i))


def load_controlnet_mmap(path: Path, cls: Any, torch: Any) -> Any:
    """ControlNet complet sans copie en RAM : ses tenseurs pointent dans le fichier safetensors
    projeté en mémoire (le chargement normal gardait deux copies de 6,7 Go)."""
    import mmap
    import struct
    import warnings

    from accelerate import init_empty_weights
    from accelerate.utils import set_module_tensor_to_device

    with init_empty_weights():
        model = cls.from_config(CONTROLNET_CONFIG)
    fh = path.open("rb")
    mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    n = struct.unpack("<Q", mm[:8])[0]
    header = json.loads(mm[8 : 8 + n])
    header.pop("__metadata__", None)
    dtypes = {"BF16": torch.bfloat16, "F16": torch.float16, "F32": torch.float32}
    names = dict(model.named_parameters()) | dict(model.named_buffers())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # tampon en lecture seule : on ne fait que lire
        for key, meta in header.items():
            if key not in names:
                continue
            a, b = meta["data_offsets"]
            t = torch.frombuffer(mm, dtype=torch.uint8, count=b - a, offset=8 + n + a)
            t = t.view(dtypes[meta["dtype"]]).view(meta["shape"])
            set_module_tensor_to_device(model, key, "cpu", value=t, dtype=t.dtype)
    # Relu après chargement : `names` garde les paramètres vides d'avant (l'essai comptait
    # donc tout comme manquant).
    loaded = [*model.named_parameters(), *model.named_buffers()]
    missing = [k for k, v in loaded if getattr(v, "is_meta", False)]
    if missing:
        msg = f"ControlNet incomplet : {len(missing)} tenseurs manquants"
        raise RuntimeError(msg)
    model._openplan_mmap = (fh, mm)
    return model.eval()


def generate(job: dict[str, Any], emb: Any) -> tuple[Any, dict[str, float]]:
    import torch
    from PIL import Image

    diffusers = _module("diffusers")
    AutoencoderKL = diffusers.AutoencoderKL  # noqa: N806 - classes de diffusers
    FlowMatchEulerDiscreteScheduler = diffusers.FlowMatchEulerDiscreteScheduler  # noqa: N806
    GGUFQuantizationConfig = diffusers.GGUFQuantizationConfig  # noqa: N806
    ZImageControlNetModel = diffusers.ZImageControlNetModel  # noqa: N806
    ZImageControlNetPipeline = diffusers.ZImageControlNetPipeline  # noqa: N806
    ZImageTransformer2DModel = diffusers.ZImageTransformer2DModel  # noqa: N806

    dirs = {k: Path(v) for k, v in job["dirs"].items()}
    dt = torch.bfloat16
    t0 = time.perf_counter()
    cn = load_controlnet_mmap(
        dirs["controlnet"] / job["controlnet_file"], ZImageControlNetModel, torch
    )
    gc.collect()
    layers, refiner = cn.control_layers, cn.control_noise_refiner
    cn.control_layers, cn.control_noise_refiner = torch.nn.ModuleList(), torch.nn.ModuleList()
    cn.to("cuda")
    cn.control_layers, cn.control_noise_refiner = layers, refiner
    # Un seul tampon : chaque couche de contrôle ré-empile les sorties des précédentes.
    stream_blocks([*refiner, *layers], torch, nslots=1, pin=False)
    for blk in layers:
        blk.register_forward_hook(lambda _m, _a, _o: torch.cuda.empty_cache())
    gc.collect()
    tr = ZImageTransformer2DModel.from_single_file(
        str(dirs["transformer"] / job["transformer_file"]),
        quantization_config=GGUFQuantizationConfig(compute_dtype=dt),
        config=str(dirs["base"]),
        subfolder="transformer",
        dtype=dt,
        local_files_only=True,
    )
    vae = AutoencoderKL.from_pretrained(dirs["base"] / "vae", dtype=dt, local_files_only=True)
    sch = FlowMatchEulerDiscreteScheduler.from_pretrained(
        dirs["base"] / "scheduler", local_files_only=True
    )
    blocks = tr.layers
    tr.layers = torch.nn.ModuleList()
    tr.to("cuda")
    tr.layers = blocks
    stream_blocks(list(blocks), torch, nslots=2, pin=True)
    gc.collect()
    vae.to("cuda")
    vae.enable_tiling()
    vae.tile_sample_min_size, vae.tile_latent_min_size = 512, 64
    pipe = ZImageControlNetPipeline(
        scheduler=sch, vae=vae, text_encoder=None, tokenizer=None, transformer=tr, controlnet=cn
    )
    pipe.set_progress_bar_config(disable=True)
    torch.cuda.synchronize()
    load_s = time.perf_counter() - t0
    control = Image.open(job["control"]).convert("RGB")
    torch.cuda.reset_peak_memory_stats()
    t1 = time.perf_counter()
    generator = torch.Generator(device="cuda").manual_seed(int(job["seed"]))
    with torch.inference_mode():
        image = pipe(
            prompt=None,
            prompt_embeds=[emb.to("cuda", dt)],
            control_image=control,
            controlnet_conditioning_scale=float(job["scale"]),
            width=int(job["width"]),
            height=int(job["height"]),
            num_inference_steps=int(job["steps"]),
            guidance_scale=0.0,
            generator=generator,
        ).images[0]
    torch.cuda.synchronize()
    stats = {
        "load_s": round(load_s, 1),
        "denoise_s": round(time.perf_counter() - t1, 1),
        "torch_peak_mb": round(torch.cuda.max_memory_allocated() / 1e6),
    }
    return image, stats


def main(argv: list[str]) -> int:
    block_network()
    # Sonar S8707 et S2083, faux positifs : ce processus enfant lit la tâche écrite par son
    # parent (zimage.py), avec les chemins de son dossier temporaire ; rien ne vient du dehors.
    job = json.loads(Path(argv[1]).read_text(encoding="utf-8"))  # NOSONAR
    dirs = {k: Path(v) for k, v in job["dirs"].items()}
    t0 = time.perf_counter()
    emb = encode(job["prompt"], dirs["base"], dirs["encoder"], job["encoder_file"])
    encode_s = time.perf_counter() - t0
    image, stats = generate(job, emb)
    image.save(job["out"])
    result = {
        **stats,
        "encode_s": round(encode_s, 1),
        "tokens": int(emb.shape[0]),
        "duration_s": round(time.perf_counter() - t0, 1),
        "network_attempts": len(ATTEMPTS),
    }
    Path(job["result"]).write_text(json.dumps(result), encoding="utf-8")  # NOSONAR (voir plus haut)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
