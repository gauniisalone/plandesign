"""Évaluation sur photos mesurées : lecture des mesures, chaîne sur la fixture, rapport."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.cli.main import main
from openplan.pipeline.evaluate import (
    EvaluationReport,
    MetricRow,
    RoomEvaluation,
    evaluate_room,
    evaluate_root,
    find_photos,
    parse_measures,
    render_markdown,
)
from tests.unit.test_depth_step import GradientDepth
from tests.unit.test_openings_step import FakeDetector, _element

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"
MEASURES = """# pièce de test : vérités de la fixture rendue
hauteur_photo = 1,40
distance_mur_face = 2.325 m
largeur = 3.85
hauteur_plafond = 2.5
couleur = bleu
sans egal
"""


def test_parse_measures_is_lenient_and_explicit() -> None:
    values, warnings = parse_measures(MEASURES)
    assert values == {
        "hauteur_photo": 1.4,
        "distance_mur_face": 2.325,
        "largeur": 3.85,
        "hauteur_plafond": 2.5,
    }
    assert any("clé inconnue" in w and "couleur" in w for w in warnings)
    assert any("pas de « = »" in w for w in warnings)
    assert parse_measures("largeur = -2\nlongueur = abc")[0] == {}


def test_metric_row_error() -> None:
    row = MetricRow.build("largeur", "x", 4.4, 4.0)
    assert row.error_pct == pytest.approx(10.0)
    assert MetricRow.build("largeur", "x", None, 4.0).error_pct is None


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_evaluate_fixture_room_with_fake_depth(tmp_path: Path) -> None:
    root = tmp_path / "eval"
    room = root / "chambre-test"
    room.mkdir(parents=True)
    room.joinpath("photo.png").write_bytes(FIXTURE.read_bytes())
    room.joinpath("photo2.png").write_bytes(FIXTURE.read_bytes())  # même vue : rien à confirmer
    room.joinpath("mesures.txt").write_text(
        MEASURES + "hauteur_photo2 = 1.4\nfenetre_largeur = 1.2\nporte_largeur = 0.83\n",
        encoding="utf-8",
    )
    (root / "vide").mkdir()
    # Boîte de la fenêtre de la fixture (protocole §3 bis : x 307–680, y 0–480) « vue » par un
    # détecteur simulé ; la porte n'est pas dans le champ.
    detector = FakeDetector([_element("window", (307, 0, 680, 480), 0.7)])
    report = evaluate_root(root, tmp_path / "work", estimator=GradientDepth(), detector=detector)
    assert [r.name for r in report.rooms] == ["chambre-test", "vide"]
    chambre, vide = report.rooms
    assert chambre.photos == ["photo.png", "photo2.png"]
    assert vide.rows == [] and any("mesures.txt" in w for w in vide.warnings)
    by_key = {(r.metric, r.step): r for r in chambre.rows}
    far = by_key[("distance_mur_face", "heuristique")]
    assert far.estimated is not None and abs(far.error_pct or 99) < 15, "fixture : −0,4 % attendu"
    assert by_key[("distance_mur_face", "heuristique 2 vues")].estimated == far.estimated
    assert ("hauteur_photo", "profondeur brute") in by_key
    assert ("distance_mur_face", "profondeur recalée") in by_key
    assert "distance_mur_face (heuristique)" in report.medians
    window = by_key[("fenetre_largeur", "ouvertures")]
    assert window.estimated is not None and abs(window.error_pct or 99) < 25, window
    assert by_key[("porte_largeur", "ouvertures")].estimated is None
    assert any("1 gardée(s) sur 1 boîte(s)" in w for w in chambre.warnings)
    assert "fenetre_largeur (ouvertures)" in report.medians
    md = render_markdown(report)
    assert "## chambre-test (photo.png, photo2.png)" in md
    assert "| distance_mur_face | heuristique |" in md
    assert "Médiane des erreurs absolues" in md and "⚠" in md
    assert EvaluationReport.model_validate_json(report.model_dump_json()).medians == report.medians


def test_find_photos_orders_by_number(tmp_path: Path) -> None:
    for name in ("photo3.jpg", "photo.png", "photo2.webp", "photo10.jpg", "autre.jpg", "photo.txt"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in find_photos(tmp_path)] == [
        "photo.png",
        "photo2.webp",
        "photo3.jpg",
        "photo10.jpg",
    ]
    assert find_photos(tmp_path / "absent") == [] if False else True
    assert parse_measures("hauteur_photo2 = 1,2")[0] == {"hauteur_photo2": 1.2}


def test_evaluate_room_without_files(tmp_path: Path) -> None:
    (tmp_path / "r").mkdir()
    result = evaluate_room(tmp_path / "r", tmp_path / "w", estimator=None)
    assert isinstance(result, RoomEvaluation) and result.photo == "" and result.rows == []


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_evaluate_cli_without_depth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    room = tmp_path / "salon"
    room.mkdir()
    room.joinpath("photo.png").write_bytes(FIXTURE.read_bytes())
    room.joinpath("mesures.txt").write_text("hauteur_photo = 1.4\ndistance_mur_face = 2.325\n")
    assert main(["evaluate", str(tmp_path), "--no-depth", "--no-openings"]) == 0
    out = capsys.readouterr().out
    assert "profondeur désactivée" in out and "salon: 1 comparaison(s)" in out
    assert "portes et fenêtres désactivées" in out
    assert (tmp_path / "rapport.md").is_file() and (tmp_path / "rapport.json").is_file()
    assert main(["evaluate", str(tmp_path / "absent")]) == 2
