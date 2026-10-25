"""KAN-95 (lot F) : un comble mesuré vu par plusieurs photos entre au banc KAN-33 ; sans ses
cotes, il est sauté sans bruit. Jeu synthétique (aucune photo réelle)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from openplan.pipeline.bench import MULTI_WORK, run_bench
from tests.attic_scenes import AXIAL_A, AXIAL_B, HEIGHT_PX, OBLIQUE, WIDTH_PX, Attic, camera
from tests.synthetic_depth import attic_faces, render_depth

ATTIC = Attic()


def _room(root: Path, name: str, measures: str | None) -> Path:
    from PIL import Image

    room = root / name
    room.mkdir(parents=True)
    faces = attic_faces(
        length=ATTIC.length, width=ATTIC.width, ridge_height=ATTIC.ridge, eave_height=0.0
    )
    for i, spec in enumerate((AXIAL_A, AXIAL_B, OBLIQUE), start=1):
        rng = np.random.default_rng(i)
        pixels = rng.integers(90, 160, size=(HEIGHT_PX, WIDTH_PX, 3), dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="PNG")
        (room / f"photo{i}.png").write_bytes(buffer.getvalue())
        np.save(room / f"photo{i}.depth.npy", render_depth(faces, camera(*spec)))
    if measures is not None:
        (room / "mesures.txt").write_text(measures, encoding="utf-8")
    return room


def test_a_measured_attic_seen_by_several_photos_is_benched(tmp_path: Path) -> None:
    room = _room(
        tmp_path,
        "combles",
        "hauteur_faitage = 2,50  # au faîtage\nlargeur = 6.00\nlongueur = 8.00\n",
    )
    report = run_bench(tmp_path, control_depth=tmp_path / "absent.npz")
    fused = [r for r in report.rows if "recoupement 3 photos · hauteur supposée" in r.case]
    metrics = {r.metric: r for r in fused}
    assert set(metrics) == {"faîtage", "largeur au sol", "longueur"}
    assert metrics["faîtage"].error is not None and abs(metrics["faîtage"].error) < 0.05
    assert metrics["largeur au sol"].error is not None
    assert abs(metrics["largeur au sol"].error) < 0.08
    assert "combles · recoupement" in report.medians
    assert "combles · recoupement, faîtage connu" in report.medians
    assert any("concordent" in n for n in report.notes)
    assert not (room / MULTI_WORK).exists(), "le dossier de travail est effacé"


def test_without_its_measures_the_scene_is_skipped_silently(tmp_path: Path) -> None:
    _room(tmp_path, "sans-cotes", None)
    _room(tmp_path, "chambre", "surface = 9\n")
    report = run_bench(tmp_path, control_depth=tmp_path / "absent.npz")
    assert not [r for r in report.rows if "recoupement" in r.case]
    assert not [n for n in report.notes if "recoupement" in n or "hauteur_faitage" in n]
