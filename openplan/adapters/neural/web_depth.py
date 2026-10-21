"""KAN-79 : le modèle de profondeur du serveur (Depth Anything V2 Metric Indoor Small,
Apache-2.0) converti en ONNX fp16 pour le navigateur.

`openplan models export-web-depth` : conversion sur le processeur, depuis les poids déjà
installés (aucun téléchargement), dans `<poids>/depth_anything_v2_metric_indoor_small_web_1.0/`
(`model_fp16.onnx`, son `.sha256`, `config.json`, `preprocessor_config.json`, `LICENSE.txt`).
Rien n'entre dans le dépôt. La conversion demande `torch`, `transformers` (extra `neural`),
`onnx` et `onnxscript`, qui ne sont PAS des dépendances du serveur : voir `INSTALL`. Les
poids sont passés en fp16 dans PyTorch avant l'export (un convertisseur après coup laissait un
graphe incohérent).

Entrées et sorties du graphe, en float32 (calcul en fp16 à l'intérieur) :
`pixel_values` [1, 3, H, W] (H et W multiples de 14, normalisés ImageNet) →
`predicted_depth` [1, H, W], en mètres (déjà bornée à 20 m par le modèle).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from openplan.adapters.neural.depth_anything import MODEL_ID as SERVER_MODEL_ID
from openplan.adapters.neural.weights import require_installed, weights_dir
from openplan.adapters.protocols import AdapterUnavailableError

WEB_MODEL_ID = "depth_anything_v2_metric_indoor_small_web@1.0"
ONNX_NAME = "model_fp16.onnx"
SHA_SUFFIX = ".sha256"
INSTALL = "uv run --extra neural --with onnx --with onnxscript openplan models export-web-depth"
OPSET = 17
TRACE_SIZE = (518, 686)
"""Taille de traçage (photo 4:3 couchée) ; H et W restent libres dans le graphe."""
LICENSE_TEXT = """Depth Anything V2 Metric Indoor Small : Apache License 2.0.
Poids d'origine : https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf
Licence : https://www.apache.org/licenses/LICENSE-2.0
Converti en ONNX fp16 par PlanDesign (openplan models export-web-depth), sans autre changement.
"""


def read_sha256(onnx_path: Path) -> str:
    """Empreinte écrite à la conversion ; recalculée (une fois) si le fichier manque."""
    side = onnx_path.with_name(onnx_path.name + SHA_SUFFIX)
    try:
        text = side.read_text(encoding="utf-8").split()[0]
        if len(text) == 64:
            return text
    except (OSError, IndexError):
        pass
    return file_sha256(onnx_path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _imports() -> tuple[Any, Any, Any]:
    try:
        import onnx  # type: ignore[import-not-found,unused-ignore]
        import torch
        from transformers import AutoModelForDepthEstimation
    except ImportError as exc:
        msg = f"conversion impossible : {INSTALL} ({exc})"
        raise AdapterUnavailableError(msg) from exc
    return onnx, torch, AutoModelForDepthEstimation


def _free_head_size(model: Any, torch: Any) -> None:
    """La tête de transformers appelle `int()` sur la taille de sortie, ce qui la fige à la
    taille d'export (une carte de 518 × 686 pour toute photo). Même calcul, sans `int()`, pour
    que la taille suive l'entrée dans le graphe."""
    head = model.head

    def forward(hidden_states: Any, patch_height: Any, patch_width: Any) -> Any:
        x = head.conv1(hidden_states[head.head_in_index])
        size = (patch_height * head.patch_size, patch_width * head.patch_size)
        x = torch.nn.functional.interpolate(x, size, mode="bilinear", align_corners=True)
        x = head.conv3(head.activation1(head.conv2(x)))
        return (head.activation2(x) * head.max_depth).squeeze(dim=1)

    head.forward = forward


def export_web_depth(models_dir: Path) -> Path:
    """Convertit les poids installés du serveur ; renvoie le fichier ONNX écrit."""
    source = require_installed(SERVER_MODEL_ID, models_dir)
    onnx, torch, auto_model = _imports()
    target = weights_dir(WEB_MODEL_ID, models_dir)
    target.mkdir(parents=True, exist_ok=True)
    # Poids en fp16 dès PyTorch (calcul fp16 dans le graphe), entrées et sorties en float32 :
    # le graphe est cohérent sans convertisseur après coup.
    model = auto_model.from_pretrained(source, local_files_only=True).eval().half()
    _free_head_size(model, torch)

    class Head(torch.nn.Module):  # type: ignore[misc,name-defined,unused-ignore]
        def __init__(self, inner: Any) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, pixel_values: Any) -> Any:
            half = pixel_values.to(torch.float16)
            return self.inner(pixel_values=half).predicted_depth.to(torch.float32)

    out = target / ONNX_NAME
    tmp = target / (ONNX_NAME + ".tmp")
    dummy = torch.zeros(1, 3, *TRACE_SIZE)
    # Export par torch.export (dynamo) : l'export par traçage figeait les tailles de la tête
    # (int() sur des formes) et rendait une carte de 518 × 686 pour toute photo. Hauteur et
    # largeur restent libres, multiples de 14 (patchs de DINOv2).
    dim = torch.export.Dim
    dynamic = {"pixel_values": {2: 14 * dim("ph", min=4, max=80), 3: 14 * dim("pw", min=4, max=80)}}
    with torch.no_grad():
        program = torch.onnx.export(
            Head(model),
            (dummy,),
            input_names=["pixel_values"],
            output_names=["predicted_depth"],
            dynamic_shapes=dynamic,
            opset_version=OPSET,
            dynamo=True,
        )
    program.save(str(tmp))
    onnx.checker.check_model(str(tmp))
    for name in ("config.json", "preprocessor_config.json"):
        shutil.copyfile(source / name, target / name)
    (target / "LICENSE.txt").write_text(LICENSE_TEXT, encoding="utf-8")
    digest = file_sha256(tmp)
    (target / (ONNX_NAME + SHA_SUFFIX)).write_text(f"{digest}  {ONNX_NAME}\n", encoding="utf-8")
    tmp.replace(out)  # en dernier : sa présence signe une installation complète
    return out
