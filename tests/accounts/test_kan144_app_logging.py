"""KAN-144 : la ligne de bilan de la purge KAN-142 sort dans le journal de l'application (la
sortie d'erreur d'`openplan serve`, que lit app.log), au démarrage puis chaque jour.

Aucun de ces tests ne force le niveau (pas de `caplog.at_level`) : le niveau vient de la
configuration posée par `openplan serve`."""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from openplan.api.accounts import retention
from openplan.api.accounts.db import make_engine
from openplan.api.settings import Settings
from openplan.cli import main
from openplan.cli.app_logging import configure_app_logging

LINE = "openplan.retention : purge des durées de conservation : codes 0, sessions 0, invitations 0"
ROOT = Path(__file__).resolve().parents[2] / "openplan"


@pytest.fixture
def engine(migrated_url: str) -> Engine:
    return make_engine(migrated_url)


def _wait_lines(capsys: pytest.CaptureFixture[str], count: int, within_s: float = 10.0) -> str:
    seen = ""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        seen += capsys.readouterr().err
        if seen.count(LINE) >= count:
            break
        time.sleep(0.02)
    return seen


def test_serve_writes_the_startup_purge_line(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`openplan serve` (le CMD de l'image) règle les journaux AVANT de construire
    l'application : la purge que lance create_app au démarrage (`_start_retention`) écrit sa ligne
    sur la sortie d'erreur, à côté de celles d'uvicorn."""
    import uvicorn

    from openplan.api.app import _start_retention

    started: list[Any] = []

    def create_app(**_kw: Any) -> Any:
        started.append(_start_retention(engine))
        return object()

    monkeypatch.setattr(uvicorn, "run", lambda *_a, **_kw: None)
    main._run_server(argparse.Namespace(reload=False), Settings(), create_app)  # type: ignore[arg-type]
    try:
        err = _wait_lines(capsys, 1)
    finally:
        started[0].stop()
    lines = [line for line in err.splitlines() if "openplan.retention" in line]
    assert len(lines) == 1, err
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO " + re.escape(LINE), lines[0])


def test_one_line_per_day_after_startup(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """La boucle de la purge reprend toutes les 24 h (`EVERY`) : une ligne à chaque tour.
    Le jour est ramené à quelques millisecondes."""
    monkeypatch.setattr(retention, "EVERY", timedelta(milliseconds=30))
    configure_app_logging()
    purge = retention.Retention(engine)
    purge.start()
    try:
        err = _wait_lines(capsys, 3)
    finally:
        purge.stop()
    assert err.count(LINE) >= 3, err


def test_configured_twice_writes_once(engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    configure_app_logging()
    configure_app_logging()
    retention.Retention(engine).purge()
    assert capsys.readouterr().err.count(LINE) == 1


def test_only_the_app_logger_changes_level(capsys: pytest.CaptureFixture[str]) -> None:
    """La racine et les bibliothèques (SQLAlchemy, httpx…) gardent leur niveau ; les `log.debug`
    de l'application restent cachés."""
    root = logging.getLogger().level
    libraries = {
        name: logging.getLogger(name).getEffectiveLevel() for name in ("sqlalchemy", "httpx")
    }
    configure_app_logging()
    assert logging.getLogger().level == root
    assert {n: logging.getLogger(n).getEffectiveLevel() for n in libraries} == libraries
    logging.getLogger("openplan.live").debug("connexion déjà fermée")
    assert capsys.readouterr().err == ""


INFO_CALL = re.compile(r"\b\w*(?:log|logger)\w*\.(?:info|log)\(")


def test_inventory_of_info_lines() -> None:
    """Inventaire (critère 3 de KAN-144) : au niveau INFO, une seule ligne de `openplan.*` devient
    visible, le bilan de la purge (des nombres). Un nouveau `log.info` fait échouer ce test : le
    relire (ni secret, ni jeton, ni e-mail, ni IP en clair ; volume compatible avec la rotation
    à 14 jours) avant de l'ajouter ici."""
    found = sorted(
        str(path.relative_to(ROOT.parent))
        for path in ROOT.rglob("*.py")
        if INFO_CALL.search(path.read_text(encoding="utf-8"))
    )
    assert found == ["openplan/api/accounts/retention.py"]
