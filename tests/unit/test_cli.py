"""CLI : schémas, export, version."""

from pathlib import Path

import pytest

from openplan.cli.main import main
from openplan.schemas import Project
from tests.factories import only_level


def test_schemas_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schemas", str(tmp_path)]) == 0
    assert (tmp_path / "derived_level.schema.json").is_file()
    assert "project.schema.json" in capsys.readouterr().out


def test_export_command(rect_project: Project, tmp_path: Path) -> None:
    src = tmp_path / "p.json"
    src.write_text(rect_project.model_dump_json(), encoding="utf-8")
    assert main(["export", str(src), "--out", str(tmp_path / "plan.svg")]) == 0
    assert "<svg" in (tmp_path / "plan.svg").read_text(encoding="utf-8")
    level = only_level(rect_project)
    assert (
        main(
            [
                "export",
                str(src),
                "--format",
                "json",
                "--level",
                str(level.id),
                "--out",
                str(tmp_path / "copy.json"),
            ]
        )
        == 0
    )
    assert (tmp_path / "copy.json").is_file()
    args = ["export", str(src), "--format", "json", "--share", "--out", str(tmp_path / "s.json")]
    assert main(args) == 0
    assert '"history": []' in (tmp_path / "s.json").read_text(encoding="utf-8")


def test_export_glb_and_bake(rect_project: Project, tmp_path: Path) -> None:
    src = tmp_path / "p.json"
    src.write_text(rect_project.model_dump_json(), encoding="utf-8")
    assert main(["export", str(src), "--format", "glb", "--out", str(tmp_path / "s.glb")]) == 0
    assert (tmp_path / "s.glb").read_bytes()[:4] == b"glTF"
    assert main(["export", str(src), "--format", "bake", "--out", str(tmp_path / "b.json")]) == 0
    assert '"prisms"' in (tmp_path / "b.json").read_text(encoding="utf-8")
    assert main(["export", str(src), "--format", "pdf", "--out", str(tmp_path / "p.pdf")]) == 0
    assert (tmp_path / "p.pdf").read_bytes()[:5] == b"%PDF-"
    assert main(["export", str(src), "--format", "dxf", "--out", str(tmp_path / "p.dxf")]) == 0
    assert "MURS_ESTIMES" in (tmp_path / "p.dxf").read_text(encoding="utf-8")


def test_blender_command_without_binary(rect_project: Project, tmp_path: Path) -> None:
    from openplan.pipeline.blender_bridge import BlenderError

    src = tmp_path / "p.json"
    src.write_text(rect_project.model_dump_json(), encoding="utf-8")
    with pytest.raises(BlenderError):
        main(
            ["blender", str(src), "--out-dir", str(tmp_path / "r"), "--blender-bin", "/nonexistent"]
        )


FIXTURE_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture image.png absente")
def test_run_command_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Photo → proposition → mesure du mur de face → calibration → exports, tout dans --out."""
    out = tmp_path / "run"
    code = main(
        [
            "run",
            str(FIXTURE_IMAGE),
            "--out",
            str(out),
            "--measure",
            "3.85",
            "--camera-height",
            "1.4",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "hypothèse (high) : Hauteur de caméra supposée 1,40 m" in printed
    assert "calibré sur « Mur" in printed
    for name in ("project.json", "plan.svg", "scene.glb", "export.json"):
        assert (out / name).is_file(), name
    assert any((out / "images").iterdir()), "la photo est copiée dans le dossier du projet"
    overlays = list((out / "runs").glob("*/detection/overlay.png"))
    assert len(overlays) == 1
    import json

    from openplan.scene.migrate import load_project

    project = load_project(json.loads((out / "project.json").read_text(encoding="utf-8")))
    level = only_level(project)
    measured = [e for e in level.elements.values() if e.type == "wall" and e.source == "measured"]
    assert len(measured) == 1 and measured[0].name in ("Mur haut", "Mur bas")
    assert "3,85 m" in (out / "plan.svg").read_text(encoding="utf-8")


def test_serve_settings_keep_ai_and_blender(tmp_path: Path) -> None:
    """Régression : `openplan serve` perdait la configuration IA lue dans l'environnement."""
    from openplan.adapters.registry import AiConfig
    from openplan.api.settings import Settings
    from openplan.cli.main import build_parser, serve_settings

    base = Settings(
        blender_bin="/opt/b/blender",
        ai=AiConfig(base_url="http://127.0.0.1:11434/v1", model="moondream"),
    )
    args = build_parser().parse_args(["serve", "--port", "9999", "--data-dir", str(tmp_path)])
    settings = serve_settings(args, base)
    assert settings.port == 9999 and settings.data_dir == tmp_path
    assert settings.blender_bin == "/opt/b/blender"
    assert settings.ai.model == "moondream" and settings.ai.configured
    assert serve_settings(build_parser().parse_args(["serve"]), base).port == base.port
    # Hors boucle locale sans jeton configuré : refus dit (4.6) ; avec un jeton, gardé.
    from openplan.cli.main import ExposedWithoutTokenError

    with pytest.raises(ExposedWithoutTokenError, match="openplan token"):
        serve_settings(build_parser().parse_args(["serve", "--host", "0.0.0.0"]), base)
    kept = serve_settings(
        build_parser().parse_args(["serve", "--host", "192.168.1.10"]),
        Settings(api_tokens={"augus": "mon-jeton-0123456789"}),
    )
    assert kept.tokens == {"augus": "mon-jeton-0123456789"}
    assert serve_settings(build_parser().parse_args(["serve"]), base).api_token is None
    # --web : front construit servi par la même API ; --web-dir choisit le dossier.
    assert serve_settings(build_parser().parse_args(["serve"]), base).web_dir is None
    auto = serve_settings(build_parser().parse_args(["serve", "--web"]), base).web_dir
    assert auto is not None and auto.parts[-3:] == ("apps", "web", "dist")
    chosen = serve_settings(
        build_parser().parse_args(["serve", "--web-dir", str(tmp_path)]), base
    ).web_dir
    assert chosen == tmp_path


def test_serve_web_without_build_explains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["serve", "--web-dir", str(tmp_path / "absent")]) == 2
    out = capsys.readouterr().out
    assert "front non construit" in out and "make start" in out


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
