"""`openplan doctor` (4.10a) : chaque manque a sa commande ; rien n'est modifié ni téléchargé."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.adapters.registry import AiConfig
from openplan.api.app import create_app
from openplan.api.doctor import diagnose, render_text
from openplan.api.settings import Settings
from openplan.cli.main import main


def _by_key(report, key: str):  # type: ignore[no-untyped-def]
    return next(c for c in report.checks if c.key == key)


def test_doctor_names_each_missing_piece_with_its_command(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "weights",
        blender_bin="/nonexistent/blender",
        port=8765,
    )
    report = diagnose(settings, web_dir=tmp_path / "web", probe_ai=False)
    assert report.ok, "Python et dossier des projets suffisent pour démarrer"
    assert _by_key(report, "python").status == "ok" and _by_key(report, "python").required
    assert _by_key(report, "data").status == "ok" and (tmp_path / "data").is_dir()
    web = _by_key(report, "web")
    assert web.status == "missing" and "npm run build" in (web.fix or "")
    blender = _by_key(report, "blender")
    assert blender.status == "missing" and "blender.org" in (blender.fix or "")
    weights = [c for c in report.checks if c.key.startswith("weights:")]
    assert weights and all(c.status == "missing" for c in weights)
    # KAN-79 : le modèle du navigateur se convertit (export-web-depth), il ne se télécharge pas.
    commands = ("openplan models pull", "openplan models export-web-depth")
    assert all(any(cmd in (c.fix or "") for cmd in commands) for c in weights)
    assert any("--accept-license" in (c.fix or "") for c in weights), "OpenRAIL à accepter"
    ai = _by_key(report, "ai")
    assert ai.status == "info" and "Ollama" in (ai.fix or "")
    access = _by_key(report, "access")
    assert access.status == "ok" and "sans jeton" in access.detail
    text = render_text(report)
    assert "[!!] Blender" in text and "→ installer Blender" in text and "PlanDesign démarre" in text


def test_doctor_sees_a_busy_port_an_unreachable_model_and_missing_tokens(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        settings = Settings(
            data_dir=tmp_path / "data",
            models_dir=tmp_path / "weights",
            port=port,
            host="0.0.0.0",
            ai=AiConfig(base_url=f"http://127.0.0.1:{port + 1}/v1", model="qwen2.5vl:3b"),
        )
        report = diagnose(settings, web_dir=tmp_path / "web", probe_ai=True)
    assert _by_key(report, "web").status == "ok"
    port_check = _by_key(report, "port")
    assert port_check.status == "info" and "déjà utilisé" in port_check.detail
    ai = _by_key(report, "ai")
    assert ai.status == "missing" and "injoignable" in ai.detail
    assert "ollama serve" in (ai.fix or "")
    access = _by_key(report, "access")
    assert access.status == "missing" and access.required and "openplan token" in (access.fix or "")
    assert not report.ok and "ne peut pas démarrer" in render_text(report)
    # Un dossier des projets impossible (un fichier à sa place) : requis, dit.
    (tmp_path / "file").write_text("x", encoding="utf-8")
    broken = diagnose(Settings(data_dir=tmp_path / "file" / "sub"), web_dir=None, probe_ai=False)
    data = _by_key(broken, "data")
    assert data.status == "missing" and "OPENPLAN_DATA_DIR" in (data.fix or "") and not broken.ok


def test_doctor_command_and_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENPLAN_DOTENV", "")
    monkeypatch.setenv("OPENPLAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENPLAN_MODELS_DIR", str(tmp_path / "weights"))
    monkeypatch.setenv("OPENPLAN_PORT", "8766")
    assert main(["doctor", "--json", "--no-probe"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and any(c["key"] == "blender" for c in report["checks"])
    assert main(["doctor", "--no-probe"]) == 0
    assert "bilan de l'installation" in capsys.readouterr().out
    with TestClient(create_app(settings=Settings(data_dir=tmp_path / "d2", port=8767))) as c:
        r = c.get("/api/doctor")
        assert r.status_code == 200
        keys = [c["key"] for c in r.json()["checks"]]
        assert keys[:3] == ["python", "data", "web"] and "access" in keys
