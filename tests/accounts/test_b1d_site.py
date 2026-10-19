"""B1d-a : origine du site (CORS limité à la connexion), rôle exposé, /nouveau, point de test
du mailer fake et ses garde-fous, galerie limitée aux projets du membre."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts.setup import FakeMailerExposedError
from openplan.api.app import create_app
from openplan.api.settings import Settings, parse_origin
from tests.accounts.helpers import (
    ADMIN,
    ORIGIN,
    accounts_client,
    accounts_settings,
    code_of,
    sign_in,
)

SITE = "https://site.example.org"


def test_site_origin_setting() -> None:
    assert parse_origin(None) is None and parse_origin("  ") is None
    assert parse_origin("https://PlanDesign.fr/") == "https://plandesign.fr"
    assert parse_origin("http://127.0.0.1:4321") == "http://127.0.0.1:4321"
    for bad in ("plandesign.fr", "https://plandesign.fr/page", "*", "https://a.fr, https://b.fr"):
        with pytest.raises(ValueError, match="OPENPLAN_SITE_ORIGIN"):
            parse_origin(bad)
    env = {"OPENPLAN_SITE_ORIGIN": "https://plandesign.fr"}
    assert Settings.from_env(env, dotenv=None).site_origin == "https://plandesign.fr"
    assert Settings.from_env({}, dotenv=None).site_origin is None


def test_without_site_origin_no_outside_origin_is_accepted(
    tmp_path: Path, migrated_url: str
) -> None:
    client, _, _ = accounts_client(tmp_path, migrated_url)
    site = {"Origin": SITE}
    start = client.post("/api/auth/email/start", json={"email": ADMIN}, headers=site)
    assert start.status_code == 403 and "access-control-allow-origin" not in start.headers
    pre = client.options(
        "/api/auth/email/start",
        headers={**site, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in pre.headers
    # Les origines de développement (Vite) ne passent pas non plus avec les comptes.
    dev = {"Origin": "http://127.0.0.1:5173"}
    assert "access-control-allow-origin" not in client.get("/api/health", headers=dev).headers


def test_site_window_can_sign_in_with_cookies(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    site = {"Origin": SITE}
    pre = client.options(
        "/api/auth/email/verify",
        headers={**site, "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == SITE
    assert pre.headers["access-control-allow-credentials"] == "true"
    assert "POST" in pre.headers["access-control-allow-methods"]
    assert pre.headers["access-control-allow-headers"] == "content-type"
    start = client.post("/api/auth/email/start", json={"email": ADMIN}, headers=site)
    assert start.status_code == 202
    assert start.headers["access-control-allow-origin"] == SITE
    assert start.headers["vary"] == "Origin"
    verify = client.post(
        "/api/auth/email/verify",
        json={"email": ADMIN, "code": code_of(mailer, ADMIN)},
        headers=site,
    )
    assert verify.status_code == 200 and verify.headers["access-control-allow-origin"] == SITE
    cookie = verify.headers["set-cookie"].lower()
    assert "pd_session=" in cookie and "samesite=lax" in cookie and "secure" in cookie
    me = client.get("/api/auth/me", headers=site)
    assert me.status_code == 200 and me.headers["access-control-allow-credentials"] == "true"


def test_site_origin_is_refused_everywhere_else(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    sign_in(client, mailer, ADMIN)
    site = {"Origin": SITE}
    made = client.post("/api/projects", json={"name": "Grenier"}, headers=site)
    assert made.status_code == 403 and "access-control-allow-origin" not in made.headers
    assert client.post("/api/auth/logout-all", headers=site).status_code == 403
    assert client.post("/api/auth/login/verify", data={}, headers=site).status_code == 403
    listed = client.get("/api/projects", headers=site)
    assert "access-control-allow-origin" not in listed.headers  # illisible depuis le site
    pre = client.options("/api/projects", headers={**site, "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers
    # Une autre origine sur la connexion : refusée, sans en-tête CORS.
    other = {"Origin": "https://ailleurs.example.org"}
    refused = client.post("/api/auth/email/start", json={"email": ADMIN}, headers=other)
    assert refused.status_code == 403 and "access-control-allow-origin" not in refused.headers


def test_health_says_only_whether_accounts_are_on(tmp_path: Path, migrated_url: str) -> None:
    client, _, _ = accounts_client(tmp_path, migrated_url)
    health = client.get("/api/health").json()
    assert health["accounts"] is True
    # Bêta sur invitation (19 oct.) : le mode d'inscription, et rien d'autre sur les comptes.
    assert health["signup"] == "invite"
    assert not any(k in health for k in ("mailer", "admin", "users", "site_origin"))
    opened, _, _ = accounts_client(tmp_path / "o", migrated_url, signup="open")
    assert opened.get("/api/health").json()["signup"] == "open"
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    assert local.get("/api/health").json()["accounts"] is False
    assert "signup" not in local.get("/api/health").json()


def test_site_window_reads_health_without_cookies(tmp_path: Path, migrated_url: str) -> None:
    client, _, _ = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    health = client.get("/api/health", headers={"Origin": SITE})
    assert health.status_code == 200 and health.json()["signup"] == "invite"
    assert health.headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-credentials" not in health.headers
    other = client.get("/api/health", headers={"Origin": "https://ailleurs.example.org"})
    assert "access-control-allow-origin" not in other.headers


def test_role_is_exposed_and_gallery_is_the_members_own(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, "marie@example.org")
    pid = owner.post("/api/projects", json={"name": "Grenier de Marie"}).json()["id"]
    assert owner.get(f"/api/projects/{pid}/role").json() == {"role": "owner"}
    paul = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(paul, mailer, "paul@example.org")
    assert paul.get("/api/projects").json() == []
    assert paul.get(f"/api/projects/{pid}/role").status_code == 404
    marie = service.user_by_email("marie@example.org")
    paul_user = service.user_by_email("paul@example.org")
    assert marie is not None and paul_user is not None
    service.set_member(UUID(pid), paul_user.id, "viewer", added_by=marie.id)
    assert [p["id"] for p in paul.get("/api/projects").json()] == [pid]
    assert paul.get(f"/api/projects/{pid}/role").json() == {"role": "viewer"}
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    made = local.post("/api/projects", json={"name": "Local"}).json()["id"]
    assert local.get(f"/api/projects/{made}/role").json() == {"role": "owner"}


def test_nouveau_redirects_to_the_front_route(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=Settings(data_dir=tmp_path / "d")))
    query = "v=1&source=site&piece=attic&surface=35.5&description=Chambre+d%27amis"
    done = client.get(f"/nouveau?{query}", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == f"/#/nouveau?{query}"
    bare = client.get("/nouveau", follow_redirects=False)
    assert bare.headers["location"] == "/#/nouveau"


def test_fake_mailer_test_route_and_its_guards(tmp_path: Path, migrated_url: str) -> None:
    client, _, _ = accounts_client(tmp_path, migrated_url)
    client.post("/api/auth/email/start", json={"email": ADMIN})
    code = client.get("/api/test/mail/last-code", params={"email": " Admin@Example.org "})
    assert code.status_code == 200 and len(code.json()["code"]) == 6
    missing = client.get("/api/test/mail/last-code", params={"email": "x@example.org"})
    assert missing.status_code == 404
    # Route absente avec un autre mailer.
    console = create_app(settings=accounts_settings(tmp_path / "c", migrated_url, mailer="console"))
    paths = {getattr(r, "path", "") for r in console.routes}
    assert "/api/test/mail/last-code" not in paths
    assert TestClient(console, base_url=ORIGIN).get(
        "/api/test/mail/last-code", params={"email": ADMIN}
    ).status_code in (401, 404)
    # Refus de démarrer en mailer fake hors boucle locale.
    with pytest.raises(FakeMailerExposedError, match="boucle locale"):
        create_app(settings=accounts_settings(tmp_path / "e", migrated_url, host="0.0.0.0"))
