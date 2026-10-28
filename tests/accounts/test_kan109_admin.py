"""KAN-109 L1 : back-office d'augus. Audience du site tirée du journal de Caddy (IP déjà
tronquée), sans donnée personnelle gardée ; santé technique ; routes « admin » seulement ;
uvicorn sans journal d'accès."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import func, select

from openplan.api.accounts.admin_routes import ApiMetrics
from openplan.api.accounts.audience import (
    SiteAudience,
    device_of,
    kind_of,
    parse,
    source_of,
)
from openplan.api.accounts.db import current_revision, downgrade, make_engine, upgrade
from openplan.api.accounts.models import SiteDay, SiteSalt, SiteVisitor
from openplan.api.settings import Settings
from openplan.cli import main as cli
from tests.accounts.helpers import ADMIN, accounts_client, sign_in

FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari/604.1"
IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def line(
    uri: str = "/",
    *,
    host: str = "plandesign.fr",
    status: int = 200,
    agent: str = FIREFOX,
    referer: str = "",
    ip: str = "203.0.113.0",
    method: str = "GET",
    ts: str = "2026-10-28T10:00:00Z",
) -> str:
    """Une ligne comme celles de Caddy, l'IP déjà tronquée (adresses de documentation)."""
    headers = {"User-Agent": [agent]} if agent else {}
    if referer:
        headers["Referer"] = [referer]
    return json.dumps(
        {
            "level": "info",
            "ts": ts,
            "msg": "handled request",
            "request": {
                "remote_ip": ip,
                "client_ip": ip,
                "method": method,
                "host": host,
                "uri": uri,
                "headers": headers,
            },
            "status": status,
            "duration": 0.002,
        }
    )


AUDIENCE_TABLES = {
    "site_days",
    "site_day_pages",
    "site_day_sources",
    "site_visitors",
    "site_salts",
    "access_log_cursors",
}


def test_migration_kan109_goes_up_and_down(db_url: str) -> None:
    """kan109_0008 ↔ kan96_0007 : les 6 tables de l'audience arrivent et repartent seules ;
    les tables d'avant ne bougent pas."""
    from sqlalchemy import inspect

    engine = make_engine(db_url)
    upgrade(engine, "kan109_0008")
    assert current_revision(engine) == "kan109_0008"
    before = set(inspect(engine).get_table_names())
    assert before >= AUDIENCE_TABLES
    downgrade(engine, "kan96_0007")
    assert current_revision(engine) == "kan96_0007"
    after = set(inspect(engine).get_table_names())
    assert not after & AUDIENCE_TABLES
    assert after == before - AUDIENCE_TABLES, "rien d'autre ne part"
    upgrade(engine, "kan109_0008")
    assert current_revision(engine) == "kan109_0008"
    assert set(inspect(engine).get_table_names()) == before
    engine.dispose()


def test_what_counts_as_a_view() -> None:
    def kind(raw: str) -> str:
        hit = parse(raw)
        assert hit is not None
        return kind_of(hit)

    assert kind(line("/")) == "view"
    assert kind(line("/tarifs?utm_source=x")) == "view"
    assert kind(line("/", host="www.plandesign.fr", status=301)) == "www"
    assert kind(line("/", agent=GOOGLEBOT)) == "bot"
    assert kind(line("/", agent="")) == "bot", "sans User-Agent : un robot"
    assert kind(line("/", agent="Gatus/1.0")) == "bot"
    assert kind(line("/_astro/index.abc.css")) == "skip"
    assert kind(line("/favicon.ico")) == "skip"
    assert kind(line("/", method="HEAD")) == "skip"
    assert kind(line("/", host="app.plandesign.fr")) == "skip"
    assert kind(line("/absent", status=404)) == "4xx"
    assert kind(line("/", status=502)) == "5xx"
    assert parse("pas du json") is None and parse("{}") is None


def test_device_and_source_keep_no_path_and_no_address() -> None:
    assert device_of(IPHONE) == "mobile"
    assert device_of(IPAD) == "tablet"
    assert device_of(FIREFOX) == "desktop"
    hit = parse(line("/", referer="https://www.example.org/un/chemin?q=secret"))
    assert hit is not None and source_of(hit) == "example.org"
    hit = parse(line("/?utm_source=Newsletter", referer="https://example.org/"))
    assert hit is not None and source_of(hit) == "utm:newsletter"
    hit = parse(line("/", referer="https://plandesign.fr/autre"))
    assert hit is not None and source_of(hit) is None, "le site lui-même n'est pas une source"


def _audience(url: str, log_dir: Path | None, now: list[datetime]) -> SiteAudience:
    return SiteAudience(make_engine(url), log_dir, clock=lambda: now[0])


def test_reads_once_follows_rotation_and_keeps_no_address(
    tmp_path: Path, migrated_url: str
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = [NOW]
    audience = _audience(migrated_url, logs, now)
    # Dossier vide (Caddy n'a pas encore écrit) : aucune donnée, pas d'erreur.
    assert audience.ingest() == 0
    assert audience.summary(7)["days"] == []
    current = logs / "site.log"
    first = [
        line("/"),
        line("/", agent=IPHONE, ip="198.51.100.0"),
        line("/tarifs", referer="https://example.org/x"),
        line("/", agent=GOOGLEBOT),
        line("/", host="www.plandesign.fr", status=301),
    ]
    current.write_text("\n".join(first) + "\n" + line("/")[:20], encoding="utf-8")
    assert audience.ingest() == 5, "la ligne en cours d'écriture attend le tour suivant"
    day = audience.summary(7)["days"][0]  # type: ignore[index]
    assert day == {
        "day": "2026-10-28",
        "views": 3,
        "visitors": 2,
        "www": 1,
        "bots": 1,
        "errors_4xx": 0,
        "errors_5xx": 0,
    }
    assert audience.ingest() == 0, "relire ne compte rien deux fois"
    # Rotation : un fichier tourné (.gz, lu une seule fois) et un nouveau courant, plus court
    # que la position retenue (son inode peut être réutilisé) : relu depuis le début.
    with gzip.open(logs / "site-2026-10-28T11-00-00.000-size.log.gz", "wt") as gz:
        gz.write(line("/archive", ts="2026-10-27T09:00:00Z") + "\n")
    current.unlink()
    current.write_text(line("/nouveau") + "\n", encoding="utf-8")
    assert audience.ingest() == 2
    summary = audience.summary(7)
    assert {p["path"] for p in summary["pages"]} >= {"/", "/tarifs", "/nouveau", "/archive"}  # type: ignore[index]
    assert summary["since"] == "2026-10-27"
    assert {"source": "example.org", "views": 1} in summary["sources"]  # type: ignore[operator]
    assert audience.ingest() == 0, "le .gz n'est lu qu'une fois"
    # Rien d'une adresse en base : ni l'IP tronquée, ni le User-Agent.
    with audience.session() as db:
        digests = list(db.scalars(select(SiteVisitor.digest)))
    assert digests and all("203.0.113" not in d and "Firefox" not in d for d in digests)


def test_digests_and_salts_leave_after_48_hours_counts_stay(
    tmp_path: Path, migrated_url: str
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = [NOW]
    audience = _audience(migrated_url, logs, now)
    (logs / "site.log").write_text(line("/") + "\n", encoding="utf-8")
    audience.ingest()
    with audience.session() as db:
        assert db.scalar(select(func.count()).select_from(SiteSalt)) == 1
    now[0] = NOW + timedelta(hours=49)
    audience.ingest()
    with audience.session() as db:
        assert db.scalar(select(func.count()).select_from(SiteVisitor)) == 0
        assert db.scalar(select(func.count()).select_from(SiteSalt)) == 0
        kept = db.get(SiteDay, "2026-10-28")
        assert kept is not None and kept.visitors == 1, "le compte du jour reste, figé"
    # 25 mois plus tard, les agrégats partent aussi.
    now[0] = NOW + timedelta(days=761)
    audience.ingest()
    with audience.session() as db:
        assert db.get(SiteDay, "2026-10-28") is None


def test_routes_are_for_the_admin_only(tmp_path: Path, migrated_url: str) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "site.log").write_text(line("/") + "\n", encoding="utf-8")
    client, service, mailer = accounts_client(tmp_path, migrated_url, access_log_dir=logs)
    assert client.get("/api/admin/audience").status_code == 401
    assert client.get("/api/admin/health").status_code == 401
    sign_in(client, mailer, ADMIN)
    client.app.state.audience.ingest()  # type: ignore[attr-defined]
    audience = client.get("/api/admin/audience", params={"days": 7}).json()
    assert audience["connected"] is True and audience["days"][0]["views"] == 1
    assert client.get("/api/admin/audience", params={"days": 12}).status_code == 422
    health = client.get("/api/admin/health").json()
    assert set(health) == {"version", "api", "worker", "disk"}
    assert health["api"]["requests"] >= 1 and health["disk"]["total"] > 0
    del service


def test_a_member_who_is_not_admin_is_refused(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(client, mailer, "marie@example.org")
    assert client.get("/api/admin/audience").status_code == 403
    assert client.get("/api/admin/health").status_code == 403
    del service


def test_api_metrics_count_5xx_and_latency_over_24_hours() -> None:
    clock = [1_000_000.0]
    metrics = ApiMetrics(clock=lambda: clock[0])
    for ms in (10, 20, 30, 40, 1000):
        metrics.record(200, ms / 1000)
    metrics.record(503, 0.05)
    snap = metrics.snapshot()
    assert snap["requests"] == 6 and snap["errors_5xx"] == 1
    assert snap["p50_ms"] == 40.0 and snap["p95_ms"] == 1000.0
    clock[0] += 25 * 3600
    assert metrics.snapshot()["requests"] == 0, "au-delà de 24 h, plus rien"


@pytest.mark.parametrize("reload", [False, True])
@pytest.mark.parametrize(("value", "logged"), [(None, False), ("0", False), ("1", True)])
def test_uvicorn_runs_without_access_log_unless_asked(
    monkeypatch: pytest.MonkeyPatch, reload: bool, value: str | None, logged: bool
) -> None:
    """RGPD (Infra) : uvicorn n'écrit plus les IP des clients dans les journaux Docker ;
    seuls les serveurs jetables des e2e le rallument (garde des 500 des passages)."""
    if value is None:
        monkeypatch.delenv(cli.ACCESS_LOG_VAR, raising=False)
    else:
        monkeypatch.setenv(cli.ACCESS_LOG_VAR, value)
    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    cli._run_server(
        argparse.Namespace(reload=reload),
        Settings(),
        lambda **_: None,  # type: ignore[arg-type,return-value]
    )
    assert seen["access_log"] is logged
