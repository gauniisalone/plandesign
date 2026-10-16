"""Chaîne automatique à l'import : photo d'une pièce ou image d'un plan → plan construit."""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.protocols import DepthMap, ImageArray
from openplan.pipeline import autoplan
from openplan.pipeline.autoplan import (
    classify_image,
    guess_plan_scale,
    parse_dimension,
    plan_skew_rad,
    run_autoplan,
    scale_from_dimensions,
)
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from openplan.schemas import Camera, Project, RoofPlane, Source, Wall
from openplan.schemas.scene import Level
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level
from tests.synthetic_depth import SyntheticCamera, attic_faces, render_depth
from tests.unit.test_depthplan import BoxDepth

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"
needs_fixture = pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")


def _png(gray: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(gray).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _plan(*, door: bool = False, angle_deg: float = 0.0) -> bytes:
    """Rectangle 600 × 400 px en traits de 10 px ; porte : trou de 40 px dans le mur du bas."""
    import cv2

    img = np.full((600, 800), 255, dtype=np.uint8)
    cv2.rectangle(img, (100, 100), (700, 500), 0, thickness=10)
    if door:
        cv2.rectangle(img, (300, 490), (340, 510), 255, thickness=-1)
    cv2.line(img, (100, 550), (700, 550), 0, 1)
    if angle_deg:
        matrix = cv2.getRotationMatrix2D((400, 300), angle_deg, 1.0)
        img = cv2.warpAffine(img, matrix, (800, 600), borderValue=255)
    return _png(img)


def _photo(tmp_path: Path) -> tuple[Project, Level]:
    project, _, _ = import_image(
        new_project("auto"), tmp_path, FIXTURE.read_bytes(), original_name="p.png"
    )
    return project, only_level(project)


def _walls(project: Project) -> list[Wall]:
    return [e for e in only_level(project).elements.values() if isinstance(e, Wall)]


def test_classify_plan_and_photo() -> None:
    from PIL import Image

    with Image.open(io.BytesIO(_plan())) as img:
        assert classify_image(np.asarray(img.convert("RGB"), dtype=np.uint8)) == "plan"
    rng = np.random.default_rng(0)
    photo = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
    assert classify_image(photo) == "photo"
    warm = np.full((120, 160, 3), (200, 150, 110), dtype=np.uint8)
    assert classify_image(warm) == "photo", "un mur beige n'est pas une feuille de papier"


@needs_fixture
def test_photo_without_models_builds_the_plan_from_lines(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    image_id = next(iter(project.images))
    said: list[str] = []
    run = run_autoplan(
        project,
        tmp_path,
        level.id,
        image_id,
        tmp_path / "runs",
        notices=["poids absents : uv run openplan models pull x"],
        progress=said.append,
    )
    assert run.kind == "photo" and run.method == "lines" and run.backup is None
    assert _walls(run.project) and validate_invariants(run.project).valid
    stages = {s.key: s.status for s in run.stages}
    assert stages["lines"] == "done" and stages["depth"] == "skipped"
    assert stages["openings"] == "skipped"
    assert run.notices == ["poids absents : uv run openplan models pull x"]
    assert said[0] == "Lecture du fichier…" and "Lecture de la photo…" in said
    assert not any("RANSAC" in s or "@" in s for s in said), "pas de jargon ni de nom de modèle"
    assert any("1.50 m" in a.text for a in run.result.assumptions)
    assert (tmp_path / "runs" / str(run.run_id) / "autoplan" / "autoplan.json").is_file()
    assert all(e.actor.startswith("pipeline:") for e in run.project.history)


@needs_fixture
def test_photo_with_depth_keeps_the_best_and_the_other_as_backup(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    image_id = next(iter(project.images))
    calls: list[int] = []

    class Counting(BoxDepth):
        def predict(self, image: ImageArray) -> DepthMap:
            calls.append(1)
            return super().predict(image)

    run = run_autoplan(project, tmp_path, level.id, image_id, tmp_path / "runs", depth=Counting())
    assert len(calls) == 1, "une seule inférence de profondeur par image"
    assert set(run.scores) == {"lines", "depth"}
    best = max(run.scores, key=lambda k: run.scores[k])
    assert run.method == best and run.backup in {"lines", "depth"} - {best}
    backup = tmp_path / "runs" / str(run.run_id) / "autoplan" / f"secours_{run.backup}.json"
    assert _walls(Project.model_validate_json(backup.read_text(encoding="utf-8")))
    assert _walls(run.project) and validate_invariants(run.project).valid
    cameras = [e for e in only_level(run.project).elements.values() if isinstance(e, Camera)]
    assert len(cameras) == 1
    assert {s.key: s.status for s in run.stages}["attic"] == "done"
    assert not [e for e in only_level(run.project).elements.values() if isinstance(e, RoofPlane)]


class AtticDepth:
    """Combles 6 × 5 m vus le long du faîtage, rendus à la taille de l'image."""

    model_id = "fake_attic_depth@0"

    def __init__(self, focal_px: float) -> None:
        self.focal_px = focal_px

    def predict(self, image: ImageArray) -> DepthMap:
        camera = SyntheticCamera(
            position=(1.5, 0.3, 1.5),
            yaw=0.0,
            pitch=-0.14,
            focal_px=self.focal_px,
            width_px=image.width,
            height_px=image.height,
        )
        depth = render_depth(attic_faces(), camera, noise=0.005)
        return DepthMap(depth=depth, metric=True, model_id=self.model_id)


@needs_fixture
def test_two_slopes_seen_build_the_attic(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    image_id = next(iter(project.images))
    ref = project.images[image_id]
    run = run_autoplan(
        project,
        tmp_path,
        level.id,
        image_id,
        tmp_path / "runs",
        depth=AtticDepth(focal_px=0.66 * ref.width_px),
    )
    assert run.method == "attic", [s.model_dump() for s in run.stages]
    elements = only_level(run.project).elements.values()
    assert len([e for e in elements if isinstance(e, RoofPlane)]) == 2
    assert validate_invariants(run.project).valid
    assert run.backup in ("lines", "depth")


@needs_fixture
def test_second_photo_is_confronted_to_the_existing_plan(tmp_path: Path) -> None:
    project, level = _photo(tmp_path)
    image_id = next(iter(project.images))
    first = run_autoplan(project, tmp_path, level.id, image_id, tmp_path / "runs")
    count = len(_walls(first.project))
    second = run_autoplan(first.project, tmp_path, level.id, image_id, tmp_path / "runs")
    assert second.method == "view" and len(_walls(second.project)) == count


def test_plan_image_scale_from_wall_thickness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [])
    monkeypatch.setattr(autoplan.shutil, "which", lambda _name: None)
    project, ref, _ = import_image(new_project("scan"), tmp_path, _plan(), original_name="p.png")
    level = only_level(project)
    run = run_autoplan(project, tmp_path, level.id, ref.id, tmp_path / "runs")
    assert run.kind == "plan" and run.method == "plan_image"
    walls = _walls(run.project)
    assert len(walls) == 4 and all(w.source is Source.ESTIMATED for w in walls)
    after = only_level(run.project)
    assert after.background_image == ref.id
    # Trait de 10 px supposé 20 cm : 2 cm par pixel, pièce de 12 × 8 m.
    assert after.background_transform["scale"] == pytest.approx(0.02, rel=0.15)
    assert any("Échelle devinée" in n and "Recalibrer" in n for n in run.notices)
    assert any("Tesseract" in n and "apt install" in n for n in run.notices)
    assert sum("Tesseract" in n for n in run.notices) == 1
    assert validate_invariants(run.project).valid
    # Une seconde image de plan ne touche pas aux murs existants.
    again = run_autoplan(run.project, tmp_path, level.id, ref.id, tmp_path / "runs")
    assert again.method == "none" and len(_walls(again.project)) == 4


def test_plan_image_scale_from_read_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # « 3,00 m » lu sous le mur du bas (600 px) : 5 mm par pixel.
    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [(3.0, 400.0, 573.0)])
    project, ref, _ = import_image(new_project("scan"), tmp_path, _plan(), original_name="p.png")
    run = run_autoplan(project, tmp_path, only_level(project).id, ref.id, tmp_path / "runs")
    scale = only_level(run.project).background_transform["scale"]
    assert scale == pytest.approx(0.005, rel=0.05)
    assert any("cote" in n for n in run.notices)
    lengths = sorted(
        math.dist(
            (
                only_level(run.project).vertices[w.start_vertex].position.x,
                only_level(run.project).vertices[w.start_vertex].position.y,
            ),
            (
                only_level(run.project).vertices[w.end_vertex].position.x,
                only_level(run.project).vertices[w.end_vertex].position.y,
            ),
        )
        for w in _walls(run.project)
    )
    assert lengths == pytest.approx([2.0, 2.0, 3.0, 3.0], abs=0.15)


def test_plan_image_scale_refined_by_a_standard_door() -> None:
    import cv2
    from PIL import Image

    with Image.open(io.BytesIO(_plan(door=True))) as img:
        gray = cv2.cvtColor(np.asarray(img.convert("RGB"), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    # Mur du bas coupé par un trou de 40 px : à 2 cm par pixel, 0,80 m, une porte.
    segments = [((100.0, 500.0), (300.0, 500.0)), ((340.0, 500.0), (700.0, 500.0))]
    guess = guess_plan_scale(gray, [], segments)
    assert "porte" in guess.reason and guess.m_per_px == pytest.approx(0.02, rel=0.15)
    # Dessin sans trait épais : taille plausible.
    blank = np.full((300, 400), 255, dtype=np.uint8)
    assert "taille plausible" in guess_plan_scale(blank, [], []).reason


def test_skewed_plan_is_straightened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv2
    from PIL import Image

    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [])
    data = _plan(angle_deg=5.0)
    with Image.open(io.BytesIO(data)) as img:
        gray = cv2.cvtColor(np.asarray(img.convert("RGB"), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    assert math.degrees(plan_skew_rad(gray)) == pytest.approx(5.0, abs=0.6)
    project, ref, _ = import_image(new_project("scan"), tmp_path, data, original_name="p.png")
    run = run_autoplan(project, tmp_path, only_level(project).id, ref.id, tmp_path / "runs")
    level = only_level(run.project)
    assert level.background_transform["rotation"] == pytest.approx(math.radians(-5.0), abs=0.011)
    assert len(_walls(run.project)) == 4
    for wall in _walls(run.project):
        a = level.vertices[wall.start_vertex].position
        b = level.vertices[wall.end_vertex].position
        angle = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 90.0
        assert min(angle, 90.0 - angle) < 1.0, "murs droits après redressement"
    assert {s.key: s.detail for s in run.stages}["deskew"].endswith("redressé")


def test_dimension_parsing_and_scale_from_dimensions() -> None:
    assert parse_dimension("3,20") == pytest.approx(3.2)
    assert parse_dimension("3.2 m") == pytest.approx(3.2)
    assert parse_dimension("320") == pytest.approx(3.2)
    assert parse_dimension("12.50m") == pytest.approx(12.5)
    assert parse_dimension("Salon") is None and parse_dimension("12") is None
    assert parse_dimension("9999") is None
    horizontal = ((0.0, 100.0), (400.0, 100.0))
    vertical = ((0.0, 100.0), (0.0, 400.0))
    tokens = [(4.0, 200.0, 120.0), (3.0, 15.0, 250.0)]
    assert scale_from_dimensions(tokens, [horizontal, vertical]) == pytest.approx(0.01)
    assert scale_from_dimensions([(4.0, 2000.0, 2000.0)], [horizontal]) is None
    assert scale_from_dimensions([], [horizontal]) is None


def test_reading_can_be_forced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """« Ce n'est pas un plan » : la même image relue comme une photo, et l'inverse."""
    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [])
    project, ref, _ = import_image(new_project("scan"), tmp_path, _plan(), original_name="p.png")
    level = only_level(project)
    as_photo = run_autoplan(project, tmp_path, level.id, ref.id, tmp_path / "runs", kind="photo")
    assert as_photo.kind == "photo" and as_photo.method != "plan_image"
    assert only_level(as_photo.project).background_image is None
    as_plan = run_autoplan(project, tmp_path, level.id, ref.id, tmp_path / "runs", kind="plan")
    assert as_plan.kind == "plan" and as_plan.method == "plan_image"


def test_door_gap_is_measured_on_the_ink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoplan, "ocr_tokens", lambda _path: [])
    data = _plan(door=True)
    project, ref, _ = import_image(new_project("scan"), tmp_path, data, original_name="p.png")
    run = run_autoplan(project, tmp_path, only_level(project).id, ref.id, tmp_path / "runs")
    assert any("porte" in n for n in run.notices), run.notices
    # Trou de 40 px supposé 0,80 m : 2 cm par pixel.
    scale = only_level(run.project).background_transform["scale"]
    assert scale == pytest.approx(0.02, rel=0.1)
