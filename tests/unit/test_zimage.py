"""KAN-88 : Z-Image-Turbo en local, sans carte graphique.

Choix du moteur, taille de génération, registre des poids (révisions figées, sha256), reprise
d'une copie déjà présente par lien, garde-fou VRAM (arrêt propre, sans gel), réseau coupé dans
le processus de génération, prompt long de KAN-86 passé à Z-Image, et vue automatique dans
l'axe du faîtage. Le calcul lui-même est dans tests/gpu (créneau du PO)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.neural import weights, zimage
from openplan.adapters.neural.zimage import (
    PARTS,
    ZImageFinisher,
    ZImageStoppedError,
    choose_engine,
    edge_control,
    watch,
    z_size,
)
from openplan.adapters.protocols import AdapterUnavailableError, FinishRequest, GeneratedImage
from openplan.adapters.registry import finish_engine, load_registry
from openplan.pipeline.finish import ENGINE_WORDS, FinishOptions, finish_prompt, finish_render
from openplan.pipeline.render import camera_view
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import load_scene_words
from openplan.pipeline.views import (
    AXIS_KEY,
    RIDGE,
    RIDGE_EYE_M,
    apply_auto_views,
    plan_views,
    ridge_line,
)
from openplan.schemas.elements import Camera
from tests.factories import attic_project, only_level, rect_room_project

Z_IDS = [model_id for model_id, _ in PARTS]


# ------------------------------------------------------------------ choix du moteur
def test_zimage_is_the_default_when_weights_and_gpu_are_there() -> None:
    assert choose_engine(None, installed=True, device="cuda") == "zimage"
    assert choose_engine("auto", installed=True, device="cuda") == "zimage"


def test_the_current_chain_stays_without_weights_or_gpu() -> None:
    assert choose_engine("auto", installed=False, device="cuda") == "sd15"
    assert choose_engine("auto", installed=True, device="cpu") == "sd15"
    assert choose_engine("zimage", installed=False, device="cuda") == "sd15"
    assert choose_engine("sd15", installed=True, device="cuda") == "sd15", "choix forcé"


def _fake_z_weights(root: Path) -> None:
    for model_id, sentinel in PARTS:
        path = weights.weights_dir(model_id, root) / sentinel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def test_engine_follows_the_installed_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openplan.adapters import registry

    assert finish_engine(tmp_path) == "sd15", "poids absents : la chaîne d'avant"
    _fake_z_weights(tmp_path)
    assert zimage.installed(tmp_path)
    monkeypatch.setattr(registry, "resolve_device", lambda _d: ("cuda", "cuda (test)"))
    assert finish_engine(tmp_path) == "zimage"
    assert finish_engine(tmp_path, engine="sd15") == "sd15"
    monkeypatch.setattr(registry, "resolve_device", lambda _d: ("cpu", "cpu (test)"))
    assert finish_engine(tmp_path) == "sd15", "sans carte CUDA, pas de Z-Image"


def test_generation_size_keeps_the_render_format() -> None:
    assert z_size(1280, 960) == (1024, 768)
    assert z_size(1024, 1280) == (816, 1024), "portrait 4:5 de la vue du faîtage"
    assert z_size(960, 1280) == (768, 1024)
    for w, h in [(1280, 960), (1024, 1280), (777, 333)]:
        zw, zh = z_size(w, h)
        assert zw % 16 == 0 and zh % 16 == 0 and max(zw, zh) == 1024


# ------------------------------------------------------------------ registre et poids
def test_the_four_weights_are_pinned_and_open() -> None:
    entries = {e.id: e for e in load_registry()}
    for model_id, sentinel in PARTS:
        e = entries[model_id]
        assert e.license == "Apache-2.0" and e.commercial_ok and not e.remote
        assert e.revision is not None and re.fullmatch(r"[0-9a-f]{40}", e.revision)
        assert e.files and sentinel in e.files
        assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in e.files.values())
        assert e.module == "openplan.adapters.neural.zimage"


def _source(tmp: Path, content: dict[str, bytes]) -> tuple[Path, dict[str, str]]:
    src = tmp / "deja-la"
    for name, data in content.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    return src, {n: hashlib.sha256(d).hexdigest() for n, d in content.items()}


def test_weights_already_here_are_linked_not_downloaded(tmp_path: Path) -> None:
    src, files = _source(tmp_path, {"model.gguf": b"poids", "sub/config.json": b"{}"})
    target = weights.link_from("essai@1.0", tmp_path / "weights", src, files)
    for name in files:
        assert (target / name).read_bytes() == (src / name).read_bytes()
        assert (target / name).stat().st_ino == (src / name).stat().st_ino, "lien, pas copie"
    again = weights.link_from("essai@1.0", tmp_path / "weights", src, files)
    assert again == target, "relancer ne casse rien"


def test_a_wrong_checksum_refuses_the_whole_copy(tmp_path: Path) -> None:
    src, files = _source(tmp_path, {"model.gguf": b"poids", "README.md": b"lisez-moi"})
    files["model.gguf"] = "0" * 64
    with pytest.raises(AdapterUnavailableError, match=re.escape("model.gguf")):
        weights.link_from("essai@1.0", tmp_path / "weights", src, files)
    assert not (tmp_path / "weights").exists(), "rien de lié avant vérification complète"


def test_download_checks_the_checksums(tmp_path: Path) -> None:
    def downloader(_repo: str, target: Path, _patterns: list[str] | None) -> None:
        (target / "model.gguf").write_bytes(b"autre chose")

    with pytest.raises(AdapterUnavailableError, match="sha256"):
        weights.pull(
            "essai@1.0",
            "org/essai",
            tmp_path,
            downloader=downloader,
            sentinel="model.gguf",
            files={"model.gguf": hashlib.sha256(b"poids").hexdigest()},
        )


def test_models_pull_from_a_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.adapters import registry
    from openplan.cli.main import main

    src, files = _source(tmp_path, {"z.gguf": b"poids"})
    entry = registry.registry_entry("z_image_turbo_gguf_q4km@1.0").model_copy(
        update={"files": files, "sentinel": "z.gguf"}
    )
    monkeypatch.setattr(registry, "registry_entry", lambda _id: entry)

    def no_download(*_a: object, **_k: object) -> None:
        raise AssertionError("aucun téléchargement avec --from")

    monkeypatch.setattr(weights, "pull", no_download)
    root = tmp_path / "weights"
    code = main(["models", "pull", entry.id, "--from", str(src), "--models-dir", str(root)])
    assert code == 0
    assert weights.is_installed(entry.id, root, "z.gguf")


# ------------------------------------------------------------------ garde-fou VRAM
class _Proc:
    def __init__(self, polls: int) -> None:
        self.polls = polls

    def poll(self) -> int | None:
        self.polls -= 1
        return None if self.polls >= 0 else 0


def test_vram_over_5000_mib_stops_the_generation() -> None:
    killed: list[bool] = []
    readings = iter([3800, 4600, 5120, 5200])
    reason, peak = watch(
        _Proc(10),
        kill=lambda: killed.append(True),
        read_vram=lambda: next(readings),
        read_ram=lambda: 8000,
        sleep=lambda _s: None,
    )
    assert killed == [True]
    assert reason is not None and "5000 Mio" in reason and "5120" in reason
    assert peak == 5120


def test_unreadable_gpu_low_ram_and_timeout_stop_too() -> None:
    reason, _ = watch(_Proc(5), kill=lambda: None, read_vram=lambda: None, sleep=lambda _s: None)
    assert reason is not None and "nvidia-smi" in reason
    reason, _ = watch(
        _Proc(5),
        kill=lambda: None,
        read_vram=lambda: 3000,
        read_ram=lambda: 900,
        sleep=lambda _s: None,
    )
    assert reason is not None and "900 Mio" in reason
    ticks = iter(range(0, 10_000, 400))
    reason, _ = watch(
        _Proc(100),
        kill=lambda: None,
        read_vram=lambda: 3000,
        read_ram=lambda: 8000,
        clock=lambda: float(next(ticks)),
        sleep=lambda _s: None,
    )
    assert reason is not None and "min de calcul" in reason


def test_a_normal_end_is_not_stopped() -> None:
    reason, peak = watch(
        _Proc(3),
        kill=lambda: pytest.fail("rien à tuer"),
        read_vram=lambda: 4700,
        read_ram=lambda: 8000,
        sleep=lambda _s: None,
    )
    assert reason is None and peak == 4700


def test_the_stop_is_immediate_and_frees_the_process() -> None:
    """Arrêt propre, pas de gel : le processus (et son groupe) est tué tout de suite."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )

    def kill() -> None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)

    started = time.monotonic()
    reason, _ = watch(proc, kill=kill, read_vram=lambda: 6000, read_ram=lambda: 8000)
    assert reason is not None and proc.returncode is not None
    assert time.monotonic() - started < 5


def _finisher(tmp_path: Path) -> ZImageFinisher:
    _fake_z_weights(tmp_path)
    return ZImageFinisher(tmp_path)


def _request() -> FinishRequest:
    return FinishRequest(
        prompt="a bedroom",
        image=np.zeros((96, 128, 3), np.uint8),
        edges=np.zeros((96, 128), np.uint8),
    )


def test_nothing_starts_without_a_readable_or_free_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finisher = _finisher(tmp_path)
    monkeypatch.setattr(zimage, "gpu_used_mib", lambda: None)
    with pytest.raises(ZImageStoppedError, match="nvidia-smi illisible"):
        finisher.finish(_request())
    monkeypatch.setattr(zimage, "gpu_used_mib", lambda: 2600)
    with pytest.raises(ZImageStoppedError, match="sert déjà"):
        finisher.finish(_request())


def test_finisher_without_weights_says_what_to_pull(tmp_path: Path) -> None:
    with pytest.raises(AdapterUnavailableError, match=re.escape("models pull z_image_turbo@1.0")):
        ZImageFinisher(tmp_path)


def test_inspiration_stays_on_the_current_chain(tmp_path: Path) -> None:
    calls: list[str] = []

    class Sd:
        def inspire(self, _request: object) -> str:
            calls.append("sd15")
            return "image"

    _fake_z_weights(tmp_path)
    finisher = ZImageFinisher(tmp_path, fallback=Sd)
    assert finisher.inspire(object()) == "image"  # type: ignore[arg-type]
    assert calls == ["sd15"]


def test_thin_edges_survive_the_resize() -> None:
    edges = np.zeros((960, 1280), np.uint8)
    edges[480, :] = 255
    edges[:, 640] = 255
    control = np.asarray(edge_control(edges, (1024, 768)))
    assert control.shape == (768, 1024, 3)
    assert control[:, :, 0].max() == 255 and (control[:, :, 0] == 255).mean() < 0.01
    assert (control[380:388, :, 0] == 255).any(axis=0).mean() > 0.95, "le trait reste continu"


# ------------------------------------------------------------------ aucun réseau
def test_the_generation_process_blocks_the_network() -> None:
    code = (
        "from openplan.adapters.neural import zimage_worker as w\n"
        "w.block_network()\n"
        "import socket, os\n"
        "s = socket.socket()\n"
        "try:\n"
        "    s.connect(('192.0.2.1', 80))\n"
        "except OSError as e:\n"
        "    print('refus', len(w.ATTEMPTS), os.environ['HF_HUB_OFFLINE'])\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True
    ).stdout
    assert out.strip() == "refus 1 1"


# ------------------------------------------------------------------ prompt KAN-86 → Z-Image
class ZFake:
    model_ids: ClassVar[list[str]] = Z_IDS
    prompt_engine = "zimage"

    def __init__(self) -> None:
        self.request: FinishRequest | None = None

    def finish(self, request: FinishRequest) -> GeneratedImage:
        self.request = request
        return GeneratedImage(
            pixels=request.image, seed=request.seed, model_ids=self.model_ids, duration_s=60.0
        )


def test_zimage_gets_the_long_scene_prompt(tmp_path: Path) -> None:
    project = attic_project()
    level = only_level(project)
    folder = tmp_path / "blender"
    folder.mkdir()
    Image.fromarray(np.full((96, 128, 3), 90, np.uint8)).save(folder / "preview.png")
    (folder / "render.json").write_text(json.dumps({"level_id": str(level.id)}), encoding="utf-8")
    bake = bake_level(project, level.id, style="realistic")
    (folder / "bake.json").write_text(bake.model_dump_json(), encoding="utf-8")
    fake = ZFake()
    entry = finish_render(project, folder, uuid4(), FinishOptions(), finisher=fake)
    scene = load_scene_words(project, folder, level.id)
    assert entry.prompt == finish_prompt(project, None, level.id, scene=scene, engine="zimage")
    assert len(entry.prompt.split()) <= ENGINE_WORDS["zimage"]
    assert entry.guided_by == ["arêtes"] and entry.model_ids == Z_IDS
    assert fake.request is not None and fake.request.edges is not None


# ------------------------------------------------------------------ vue du faîtage
def test_attic_ridge_is_found() -> None:
    from openplan.geom.roof import derived_roofs
    from openplan.geom.wallgraph import WallGraph

    level = only_level(attic_project())
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    found = ridge_line(roofs)
    assert found is not None
    _, (dx, dy) = found
    assert math.isclose(math.hypot(dx, dy), 1.0)


def test_ridge_view_is_added_for_attics_only_when_asked() -> None:
    project = attic_project()
    level = only_level(project)
    before, _ = plan_views(project, level.id)
    views, _ = plan_views(project, level.id, ridge=True)
    ridge = [v for v in views if v.axis == RIDGE]
    assert len(ridge) == 1 and not any(v.axis for v in before)
    v = ridge[0]
    assert v.role == "overall" and v.aspect == "4:5" and v.pitch == 0.0
    assert math.isclose(v.z, RIDGE_EYE_M), "œil à 1,60 m sous le faîtage"
    from openplan.geom.roof import derived_roofs
    from openplan.geom.wallgraph import WallGraph

    found = ridge_line(list(derived_roofs(level, WallGraph.from_level(level)).values()))
    assert found is not None
    (px, py), (dx, dy) = found
    # Sur l'axe du faîtage, et le regard le long de cet axe.
    assert abs((v.x - px) * dy - (v.y - py) * dx) < 1e-6
    assert abs(math.cos(v.yaw) * dy - math.sin(v.yaw) * dx) < 1e-6
    # Les autres vues restent ; seule une vue d'ensemble jumelle (même axe) céderait la place.
    assert all(w in before for w in views if w.axis is None)
    assert all(w in views for w in before if w.role != "overall")


def test_no_ridge_view_without_a_roof() -> None:
    project = rect_room_project()
    level = only_level(project)
    assert plan_views(project, level.id, ridge=True) == plan_views(project, level.id)


def test_ridge_camera_renders_in_portrait() -> None:
    project = attic_project()
    level = only_level(project)
    after, report = apply_auto_views(project, level.id, actor="user:test", ridge=True)
    cams = [
        e
        for e in after.levels[level.id].elements.values()
        if isinstance(e, Camera) and e.metadata.get(AXIS_KEY) == RIDGE
    ]
    assert len(cams) == 1
    _, rc = camera_view(after, level.id, cams[0].id)
    assert (rc.width_px, rc.height_px) == (1024, 1280)
    assert any("faîtage" in n for n in report.notes)
    again, _ = apply_auto_views(after, level.id, actor="user:test", ridge=False)
    assert not any(
        isinstance(e, Camera) and e.metadata.get(AXIS_KEY) == RIDGE
        for e in again.levels[level.id].elements.values()
    ), "sans Z-Image, la vue du faîtage s'en va avec les autres vues automatiques"
