"""KAN-31 : confiance de l'estimation en trois niveaux, dérivée de facteurs réglés sur le banc
KAN-33. Sans GPU : la partie « contrôle » du banc est recalculée sur la profondeur enregistrée
de la scène KAN-27 (tests/fixtures/control_attic_depth.npz)."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from openplan.pipeline import autoplan, confidence
from openplan.pipeline.autoplan import run_autoplan
from openplan.pipeline.bench import (
    CONTROL_DEPTH,
    CONTROL_RIDGE,
    bench_control,
    render_markdown,
    ruler_length,
    run_bench,
)
from openplan.pipeline.confidence import (
    CHECK_MAX,
    EAVE_UNSEEN_ERROR,
    FLOOR_UNSEEN_ERROR,
    RELIABLE_MAX,
    SCALE_ERROR,
    ScaleSource,
    assess,
    assess_plan_image,
)
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from tests.factories import only_level
from tests.unit.test_autoplan import AtticDepth, _photo, _plan, needs_fixture

SOURCES: tuple[ScaleSource, ...] = ("reference", "entered", "assumed")


@pytest.fixture(scope="module")
def control() -> dict[str, float]:
    _rows, medians = bench_control(CONTROL_DEPTH)
    return medians


def test_control_truth_is_the_kan27_scene() -> None:
    from tests import control_attic

    assert pytest.approx(control_attic.RIDGE, abs=1e-9) == CONTROL_RIDGE
    assert round(CONTROL_RIDGE, 2) == 3.54


def test_weights_are_the_bench_numbers(control: dict[str, float]) -> None:
    """Must have 2 : les poids sont les erreurs médianes du banc (à 3 points près ; « supposée »
    est la moyenne du contrôle et de la règle, celle-ci n'étant pas versionnée)."""
    assert SCALE_ERROR["reference"] == pytest.approx(
        control["contrôle · mesure de référence"], abs=0.03
    )
    assert SCALE_ERROR["entered"] == pytest.approx(
        control["contrôle · hauteur saisie 1,45 m"], abs=0.03
    )
    assert SCALE_ERROR["assumed"] == pytest.approx(
        control["contrôle · hauteur supposée 1,50 m"], abs=0.05
    )
    assert (
        pytest.approx(control["contrôle · sol hors champ (hauteur saisie 1,45 m)"], abs=0.03)
        == FLOOR_UNSEEN_ERROR
    )
    assert control["contrôle · égout"] >= EAVE_UNSEEN_ERROR
    assert SCALE_ERROR["reference"] < SCALE_ERROR["entered"] < SCALE_ERROR["assumed"]


def test_fiable_only_when_the_bench_is_under_ten_percent(control: dict[str, float]) -> None:
    """Must have 4 : « fiable » n'est atteignable que si l'erreur médiane du banc est sous 10 %.
    Au 20 oct. 2026 aucune configuration ne l'est : aucune combinaison n'est « fiable »."""
    best = min(control.values())
    flags = (True, False)
    for source, vps, floor, eave in itertools.product(SOURCES, flags, flags, flags):
        c = assess(source, focal_from_vps=vps, floor_out_of_view=floor, eave_unseen=eave)
        if c.level == "fiable":
            assert c.expected_error < RELIABLE_MAX
            assert best < RELIABLE_MAX, "« fiable » sans configuration du banc sous 10 %"
    if best >= RELIABLE_MAX:
        assert all(assess(s, focal_from_vps=True).level != "fiable" for s in SOURCES), (
            "le banc ne justifie aucun « fiable »"
        )


def test_three_levels_and_main_reason() -> None:
    """Must have 3 et 5 : trois niveaux, la raison principale, textes KAN-62 / KAN-66."""
    assumed = assess("assumed")
    assert assumed.level == "approximatif" and assumed.label == "approximatif"
    assert assumed.reason == "échelle supposée : ajoutez une mesure de référence"
    assert assumed.factors[0].label == "hauteur supposée 1,50 m (par défaut)"
    entered = assess("entered", camera_height_m=1.45)
    assert entered.factors[0].label == "hauteur de prise de vue saisie 1,45 m"
    reference = assess("reference")
    assert reference.level == "a_verifier" and reference.label == "à vérifier"
    assert reference.factors[0].label == "échelle calée sur votre mesure"
    assert reference.expected_error < CHECK_MAX
    # Le facteur le plus lourd donne la raison.
    attic = assess("reference", eave_unseen=True)
    assert attic.level == "approximatif" and attic.reason.startswith("égout non vu")
    focal = assess("reference", focal_from_vps=False)
    assert [f.key for f in focal.factors] == ["scale", "focal"]
    assert focal.expected_error == pytest.approx(
        float(np.hypot(SCALE_ERROR["reference"], confidence.FOCAL_FALLBACK_ERROR)), abs=1e-3
    )
    for c in (assumed, entered, reference, attic):
        assert "%" not in c.label + c.reason
        assert c.label in {"fiable", "à vérifier", "approximatif"}


def test_plan_image_levels() -> None:
    assert assess_plan_image(True).level == "a_verifier"
    guessed = assess_plan_image(False)
    assert guessed.level == "approximatif"
    assert guessed.reason == "échelle devinée sur le dessin : ajoutez une mesure de référence"


def test_ruler_length_on_an_exact_tilted_plane() -> None:
    """Règle de 1,00 m posée sur un plan incliné vu en perspective : longueur 3D retrouvée."""
    h, w, f = 240, 320, 300.0
    cx, cy = w / 2, h / 2
    vs, _us = np.mgrid[0:h, 0:w].astype(np.float64)
    # Plan z = 2 + 0,5 y (repère caméra) : rayon (x', y', 1) · t → t = 2 / (1 − 0,5 y').
    yp = (vs - cy) / f
    depth = 2.0 / (1.0 - 0.5 * yp)
    a3 = np.array([-0.4, 0.1, 2.05])
    b3 = np.array([0.3, 0.5, 2.25])
    true = float(np.linalg.norm(b3 - a3))  # deux points du plan, ≈ 0,83 m

    def px(p: np.ndarray) -> tuple[float, float]:
        return (p[0] / p[2] * f + cx, p[1] / p[2] * f + cy)

    got = ruler_length(depth.astype(np.float32), f, (cx, cy), px(a3), px(b3))
    assert got == pytest.approx(true, rel=0.02)
    # Une extrémité sur un fond lointain ne fausse pas l'ajustement.
    spoiled = depth.copy()
    au, av = px(a3)
    spoiled[int(av) - 1 : int(av) + 2, int(au) - 1 : int(au) + 2] = 9.0
    assert ruler_length(spoiled.astype(np.float32), f, (cx, cy), px(a3), px(b3)) == pytest.approx(
        true, rel=0.03
    )


def test_bench_on_a_root_without_photos(tmp_path: Path) -> None:
    """Le banc tourne sans photo ni GPU : la scène de contrôle seule, rapport en chiffres."""
    (tmp_path / "piece").mkdir()
    report = run_bench(tmp_path)
    assert "contrôle · hauteur saisie 1,45 m" in report.medians
    text = render_markdown(report)
    assert "Banc KAN-33" in text and "faîtage" in text


@needs_fixture
def test_autoplan_gives_the_level_and_its_reason(tmp_path: Path) -> None:
    """La chaîne d'import rend le niveau et la raison ; photo levée sous les combles : sol hors
    champ et égout non vu parmi les facteurs."""
    project, level = _photo(tmp_path)
    image_id = next(iter(project.images))
    ref = project.images[image_id]
    run = run_autoplan(
        project,
        tmp_path,
        level.id,
        image_id,
        tmp_path / "runs",
        depth=AtticDepth(focal_px=0.66 * ref.width_px, pitch=0.35),
    )
    assert run.method == "attic"
    trust = run.estimate_confidence
    assert trust is not None and trust.level == "approximatif"
    keys = {f.key for f in trust.factors}
    assert {"scale", "floor", "eave"} <= keys
    assert trust.factors[0].label == "hauteur supposée 1,50 m (par défaut)"
    entered = run_autoplan(
        project,
        tmp_path,
        level.id,
        image_id,
        tmp_path / "runs",
        depth=AtticDepth(focal_px=0.66 * ref.width_px),
        camera_height_m=1.45,
    ).estimate_confidence
    assert entered is not None
    assert entered.factors[0].label == "hauteur de prise de vue saisie 1,45 m"


def test_autoplan_plan_image_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [])
    monkeypatch.setattr(autoplan.shutil, "which", lambda _name: None)
    project, ref, _ = import_image(new_project("scan"), tmp_path, _plan(), original_name="p.png")
    run = run_autoplan(project, tmp_path, only_level(project).id, ref.id, tmp_path / "runs")
    assert run.estimate_confidence is not None
    assert run.estimate_confidence.level == "approximatif"
    again = run_autoplan(run.project, tmp_path, only_level(project).id, ref.id, tmp_path / "runs")
    assert again.method == "none" and again.estimate_confidence is None
