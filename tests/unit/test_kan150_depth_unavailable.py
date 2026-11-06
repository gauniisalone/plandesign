"""KAN-150 : la profondeur indisponible n'est plus avalée. `read_photos` la journalise en
WARNING (logger openplan) et la marque ; le recoupement porte l'état et l'avis « cotes estimées
sans profondeur » (montré dans les avis du plan) ; le banc et l'évaluation sans profondeur
échouent, visiblement. Scènes synthétiques (aucune photo réelle)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.protocols import AdapterUnavailableError, DepthMap, ImageArray
from openplan.cli.main import main
from openplan.pipeline.attic_fusion import fuse_attic
from openplan.pipeline.bench import run_bench
from openplan.pipeline.photo_fusion import (
    DEPTH_ABSENT,
    DEPTH_UNAVAILABLE,
    DEPTH_UNAVAILABLE_NOTICE,
    read_photos,
)
from openplan.pipeline.reference import depth_paths
from openplan.pipeline.room_fusion import fuse_room
from tests.room_scenes import project_with_room_photos, rectangle

ROOM = rectangle(4.0, 3.5)
OPPOSITE = [(0.5, 0.5, 1.5, 40.0, -12.0), (3.5, 3.0, 1.5, 220.0, -12.0)]
LOGGER = "openplan.pipeline.photo_fusion"


class _Broken:
    """Un estimateur dont le modèle manque (torch absent, par exemple)."""

    model_id = "profondeur-de-test"

    def predict(self, image: ImageArray) -> DepthMap:
        msg = "torch absent"
        raise AdapterUnavailableError(msg)


class _FromStore(_Broken):
    """En ligne : la profondeur vient du navigateur, jamais calculée ici."""

    from_store = True


def _without_depth(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, level, ids = project_with_room_photos(tmp_path, ROOM, OPPOSITE)
    for image_id in ids:
        for path in depth_paths(tmp_path, image_id):
            path.unlink(missing_ok=True)
    return project, level, ids


def test_mh1_a_failing_estimator_is_logged_and_marked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project, _, ids = _without_depth(tmp_path)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        readings = read_photos(project, tmp_path, ids, _Broken())  # type: ignore[arg-type]
    assert [r.reason for r in readings] == [DEPTH_UNAVAILABLE, DEPTH_UNAVAILABLE]
    assert all(r.depth_unavailable for r in readings)
    warned = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert len(warned) == 1  # une ligne pour la série, pas une par photo
    assert "2 photo(s) sur 2" in warned[0].getMessage()
    assert "torch absent" in warned[0].getMessage()


def test_mh1_no_estimator_at_all_is_unavailable_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project, _, ids = _without_depth(tmp_path)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        readings = read_photos(project, tmp_path, ids, None)
    assert all(r.reason == DEPTH_UNAVAILABLE for r in readings)
    assert any("aucun estimateur" in r.getMessage() for r in caplog.records)


def test_mh1_online_without_browser_depth_stays_absent_and_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project, _, ids = _without_depth(tmp_path)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        readings = read_photos(project, tmp_path, ids, _FromStore())  # type: ignore[arg-type]
    assert [r.reason for r in readings] == [DEPTH_ABSENT, DEPTH_ABSENT]
    assert not any(r.depth_unavailable for r in readings)
    assert not [r for r in caplog.records if r.name == LOGGER]


def test_mh2_room_fusion_carries_the_state_and_the_notice(tmp_path: Path) -> None:
    project, level, ids = _without_depth(tmp_path)
    run = fuse_room(project, tmp_path, level, ids, estimator=_Broken())  # type: ignore[arg-type]
    assert run.depth_unavailable is True
    assert not run.applied
    assert run.notices[0] == DEPTH_UNAVAILABLE_NOTICE
    assert DEPTH_UNAVAILABLE_NOTICE in run.result.warnings
    assert "cotes estimées sans profondeur" in DEPTH_UNAVAILABLE_NOTICE


def test_mh2_attic_fusion_carries_the_state_and_the_notice(tmp_path: Path) -> None:
    project, level, ids = _without_depth(tmp_path)
    run = fuse_attic(project, tmp_path, level, ids, estimator=_Broken())  # type: ignore[arg-type]
    assert run.depth_unavailable is True
    assert run.notices[0] == DEPTH_UNAVAILABLE_NOTICE
    assert DEPTH_UNAVAILABLE_NOTICE in run.result.warnings


def test_mh2_with_depth_nothing_changes(tmp_path: Path) -> None:
    project, level, ids = project_with_room_photos(tmp_path, ROOM, OPPOSITE)
    run = fuse_room(project, tmp_path, level, ids)
    assert run.depth_unavailable is False
    assert DEPTH_UNAVAILABLE_NOTICE not in run.notices
    assert DEPTH_UNAVAILABLE_NOTICE not in run.result.warnings


def _photo_without_depth(root: Path) -> None:
    from PIL import Image

    room = root / "chambre"
    room.mkdir(parents=True)
    pixels = np.random.default_rng(1).integers(90, 160, size=(48, 64, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    (room / "photo1.png").write_bytes(buffer.getvalue())


def test_mh3_a_bench_without_depth_fails_visibly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _photo_without_depth(tmp_path)
    report = run_bench(tmp_path, control_depth=tmp_path / "absent.npz")
    assert report.missing_depth == ["chambre/photo1.png"]
    assert any("profondeur indisponible" in n for n in report.notes)
    assert main(["bench", str(tmp_path)]) == 3
    assert "banc incomplet : 1 photo(s) sans profondeur" in capsys.readouterr().err


def test_mh3_an_evaluation_without_depth_weights_fails_visibly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "eval"
    root.mkdir()
    code = main(["evaluate", str(root), "--models-dir", str(tmp_path / "vide")])
    assert code == 3
    assert "--no-depth" in capsys.readouterr().err
