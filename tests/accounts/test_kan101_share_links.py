"""KAN-101 : partager UN rendu réaliste par un lien public, en lecture seule et révocable.

Réglage éteint (défaut) : toutes les routes répondent 404, `share_links` est faux dans
`/api/health`. Allumé : l'éditeur crée (le lecteur et le commentateur 403, le non-membre 404),
la page s'ouvre sans compte (image sans métadonnées, noindex, CSP stricte, aucune donnée
personnelle), la révocation et l'expiration donnent 404. Jeton jamais gardé en clair ; débit
limité, journal sans adresse entière ; liens tombés avec le rendu, le projet, le compte."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, inspect, select

from openplan.api.accounts.db import current_revision, downgrade, make_engine, upgrade, utcnow
from openplan.api.accounts.models import RenderRequest, ShareLink, User
from openplan.api.accounts.share_links import ShareLinks, guided_text
from openplan.api.app import create_app
from openplan.api.guards import GLOBAL_ROUTES, PROJECT_ROUTES, Role
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.settings import Settings
from openplan.api.share_page import SHARE_CSP, logged_form
from openplan.schemas.render_queue import RealisticRequest, WorkerResult
from tests.accounts.helpers import ORIGIN, accounts_client
from tests.accounts.test_kan89_realistic import AUGUS, WORKER, World

P = "/api/projects/{project_id}"
NEW_ROUTES = {
    ("POST", P + "/realistic/{request_id}/share-links"),
    ("GET", P + "/share-links"),
    ("DELETE", P + "/share-links/{link_id}"),
}
PUBLIC_ROUTES = {("GET", "/api/share/{token}"), ("GET", "/api/share/{token}/image")}


def _exif_png(width: int = 64, height: int = 48) -> bytes:
    """Un PNG du poste qui porte des métadonnées : EXIF (appareil, GPS) et texte PNG."""
    image = Image.new("RGB", (width, height), (120, 140, 160))
    exif = Image.Exif()
    exif[0x010F] = "Fabricant secret"  # Make
    exif[0x0110] = "Modele secret"  # Model
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "auteur : lea@example.org")
    out = io.BytesIO()
    image.save(out, format="PNG", exif=exif.tobytes(), pnginfo=info)
    return out.getvalue()


def _done_render(world: World, data: bytes | None = None) -> str:
    """Une demande prise et déposée par le poste ; son identifiant."""
    world.ask()
    job = world.worker.post("/api/worker/claim", json={"state": "idle"}, headers=WORKER)
    assert job.status_code == 200, job.text
    rid, lease = job.json()["request_id"], job.json()["lease"]
    meta = WorkerResult(seconds=12.0, vram_peak_mib=1000, width=64, height=48, engine="fake")
    done = world.worker.post(
        f"/api/worker/jobs/{rid}/result",
        headers={**WORKER, "X-Render-Lease": lease},
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", data or _exif_png(), "image/png")},
    )
    assert done.status_code == 200, done.text
    return str(rid)


def _switch(world: World, on: bool) -> None:
    app = world.client.app
    app.state.settings = replace(app.state.settings, share_links=on)  # type: ignore[attr-defined]


def _anonymous(world: World, ip: str = "203.0.113.57") -> TestClient:
    """Un visiteur sans compte : ni cookie, ni origine."""
    return TestClient(world.client.app, base_url=ORIGIN, client=(ip, 50000))


def _links(world: World) -> ShareLinks:
    links: ShareLinks = world.client.app.state.share_links  # type: ignore[attr-defined]
    return links


@pytest.fixture
def shared(tmp_path: Path, migrated_url: str) -> tuple[World, str]:
    world = World(tmp_path, migrated_url)
    _switch(world, True)
    return world, _done_render(world)


def test_migration_kan101_goes_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine, "kan101_0010")
    assert current_revision(engine) == "kan101_0010"
    before = set(inspect(engine).get_table_names())
    assert "share_links" in before
    columns = {c["name"] for c in inspect(engine).get_columns("share_links")}
    assert columns == {
        "id",
        "token_hash",
        "project_id",
        "request_id",
        "created_by",
        "created_at",
        "expires_at",
        "revoked_at",
        "show_project_name",
    }
    assert "token" not in columns, "jamais le jeton en clair"
    targets = {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("share_links")}
    assert targets == {"projects", "render_requests", "users"}
    downgrade(engine, "kan109b_0009")
    assert current_revision(engine) == "kan109b_0009"
    after = set(inspect(engine).get_table_names())
    assert after == before - {"share_links"}, "rien d'autre ne part"
    upgrade(engine, "kan101_0010")  # la tête est plus loin (KAN-102, KAN-115)
    assert current_revision(engine) == "kan101_0010"
    assert set(inspect(engine).get_table_names()) == before
    engine.dispose()


def test_rules_are_declared_in_the_guards() -> None:
    for key in NEW_ROUTES:
        assert PROJECT_ROUTES[key] == Role.EDITOR, key
    for key in PUBLIC_ROUTES:
        assert GLOBAL_ROUTES[key] == "public", key


def test_switch_reads_the_environment() -> None:
    assert Settings.from_env({}, dotenv=None).share_links is False
    for on in ("1", "true", "oui", " ON "):
        assert Settings.from_env({"OPENPLAN_SHARE_LINKS": on}, dotenv=None).share_links
    for off in ("0", "non", "", "false"):
        assert not Settings.from_env({"OPENPLAN_SHARE_LINKS": off}, dotenv=None).share_links


def test_disabled_by_default_every_route_is_404(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    rid = _done_render(world)
    base = f"/api/projects/{world.project_id}"
    assert world.client.get("/api/health").json()["share_links"] is False
    assert world.client.post(f"{base}/realistic/{rid}/share-links", json={}).status_code == 404
    assert world.client.get(f"{base}/share-links").status_code == 404
    # Un lien valide (créé hors des routes) : la page publique et l'image répondent 404.
    _row, token = _links(world).create(world.project_id, UUID(rid), world.augus_id)
    link = _links(world).active(world.project_id)[0]
    assert world.client.delete(f"{base}/share-links/{link.id}").status_code == 404
    visitor = _anonymous(world)
    page = visitor.get(f"/api/share/{token}")
    assert page.status_code == 404 and "<img" not in page.text
    assert visitor.get(f"/api/share/{token}/image").status_code == 404
    # Allumé, le même lien s'ouvre (preuve que la 404 venait du réglage).
    _switch(world, True)
    assert visitor.get(f"/api/share/{token}").status_code == 200
    world.close()


def test_health_tells_the_front_whether_to_offer_sharing(tmp_path: Path, migrated_url: str) -> None:
    on, service, _ = accounts_client(tmp_path / "on", migrated_url, share_links=True)
    assert on.get("/api/health").json()["share_links"] is True
    off, _, _ = accounts_client(tmp_path / "off", migrated_url)
    assert off.get("/api/health").json()["share_links"] is False
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    assert "share_links" not in local.get("/api/health").json(), "mode local : rien à dire"
    service.engine.dispose()


def test_editor_creates_others_are_refused(shared: tuple[World, str]) -> None:
    world, rid = shared
    base = f"/api/projects/{world.project_id}"
    owner = world.service.user_by_email(AUGUS)
    assert owner is not None
    people = {}
    for role in ("viewer", "commenter", "editor"):
        client = world.person(f"{role}@example.org")
        user = world.service.user_by_email(f"{role}@example.org")
        assert user is not None
        world.service.set_member(world.project_id, user.id, role, added_by=owner.id)
        people[role] = client
    people["stranger"] = world.person("stranger@example.org")
    create = f"{base}/realistic/{rid}/share-links"
    for who, expected in (("viewer", 403), ("commenter", 403), ("stranger", 404)):
        assert people[who].post(create, json={}).status_code == expected, who
        assert people[who].get(f"{base}/share-links").status_code == expected, who
    # Un éditeur ne partage pas le rendu d'un autre (rendu personnel, comme la variante).
    assert people["editor"].post(create, json={}).status_code == 403
    made = world.client.post(create, json={"show_project_name": False})
    assert made.status_code == 201, made.text
    body = made.json()
    token = body["token"]
    assert len(token) == 43 and body["show_project_name"] is False
    assert body["expires_at"] > body["created_at"]
    # La base ne garde que l'empreinte ; aucune colonne ne contient le jeton.
    with world.service.session() as db:
        row = db.get(ShareLink, UUID(body["id"]))
        assert row is not None and row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        columns = [c.key for c in ShareLink.__table__.columns]
        assert token not in str([getattr(row, c) for c in columns])
        days = (row.expires_at - row.created_at).days
    assert days == 30, "30 jours par défaut"
    # L'éditeur voit la liste du projet, sans jeton, et peut révoquer ; le lecteur non.
    listed = people["editor"].get(f"{base}/share-links")
    assert listed.status_code == 200 and [x["id"] for x in listed.json()] == [body["id"]]
    assert "token" not in listed.json()[0]
    assert people["viewer"].delete(f"{base}/share-links/{body['id']}").status_code == 403
    assert people["editor"].delete(f"{base}/share-links/{body['id']}").status_code == 204
    assert world.client.get(f"{base}/share-links").json() == []
    world.close()


def test_public_page_shows_only_the_render(shared: tuple[World, str]) -> None:
    world, rid = shared
    base = f"/api/projects/{world.project_id}"
    hidden = world.client.post(
        f"{base}/realistic/{rid}/share-links", json={"show_project_name": False}
    ).json()["token"]
    named = world.client.post(f"{base}/realistic/{rid}/share-links", json={}).json()["token"]
    visitor = _anonymous(world)
    page = visitor.get(f"/api/share/{hidden}")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html = page.text
    assert f'src="/api/share/{hidden}/image"' in html and "PlanDesign" in html
    assert '<meta name="robots" content="noindex' in html and "<script" not in html
    for secret in (AUGUS, "Chambre", str(world.project_id), rid, str(world.augus_id)):
        assert secret not in html, secret
    assert "Chambre" in visitor.get(f"/api/share/{named}").text, "nom du projet laissé coché"
    # En-têtes de sécurité de la page publique.
    h = page.headers
    assert h["content-security-policy"] == SHARE_CSP
    assert "default-src 'none'" in SHARE_CSP and "script-src" not in SHARE_CSP
    assert h["x-robots-tag"].startswith("noindex")
    assert h["cache-control"] == "no-store" and h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in h
    # L'image : recodée, sans EXIF ni texte PNG.
    image = visitor.get(f"/api/share/{hidden}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["x-robots-tag"].startswith("noindex")
    served = Image.open(io.BytesIO(image.content))
    assert served.size == (64, 48)
    assert "exif" not in served.info and "Comment" not in served.info
    assert len(served.getexif()) == 0
    assert b"secret" not in image.content and b"lea@example.org" not in image.content
    # L'original du poste, lui, les avait (le test prouve bien le retrait).
    original = world.client.get(f"{base}/realistic/{rid}/image").content
    assert b"Fabricant secret" in original
    world.close()


def test_revoked_expired_unknown_all_answer_404(shared: tuple[World, str]) -> None:
    world, rid = shared
    base = f"/api/projects/{world.project_id}"
    made = world.client.post(f"{base}/realistic/{rid}/share-links", json={}).json()
    other = world.client.post(f"{base}/realistic/{rid}/share-links", json={"days": 7}).json()
    visitor = _anonymous(world)
    assert visitor.get(f"/api/share/{made['token']}").status_code == 200
    assert world.client.delete(f"{base}/share-links/{made['id']}").status_code == 204
    gone = visitor.get(f"/api/share/{made['token']}")
    assert gone.status_code == 404, "révoqué : 404, pas 410"
    assert visitor.get(f"/api/share/{made['token']}/image").status_code == 404
    assert world.client.delete(f"{base}/share-links/{made['id']}").status_code == 404
    unknown = visitor.get("/api/share/" + "A" * 43)
    malformed = visitor.get("/api/share/court")
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.text == gone.text == malformed.text, "rien ne distingue les cas"
    # Expiré (7 jours) : 404, et il quitte la liste des liens actifs.
    links = _links(world)
    links.clock = lambda: utcnow() + timedelta(days=8)
    assert visitor.get(f"/api/share/{other['token']}").status_code == 404
    assert world.client.get(f"{base}/share-links").json() == []
    world.close()


def test_guided_by_never_names_moodboard_images() -> None:
    def render(by: list[str] | None, kind: str | None) -> RealisticRequest:
        return RealisticRequest.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "level_id": "00000000-0000-0000-0000-000000000002",
                "camera_id": "00000000-0000-0000-0000-000000000003",
                "state": "done",
                "created_at": utcnow(),
                "finished_at": None,
                "expires_at": utcnow(),
                "seconds": None,
                "width": None,
                "height": None,
                "error": None,
                "image": True,
                "guided_by": by,
                "guided_kind": kind,
            }
        )

    assert guided_text(render(["scandinave", "lumineux"], "words")) == (
        "Guidé par : scandinave, lumineux"
    )
    named = guided_text(render(["vacances-chez-lea.jpg", "salon-dupont.png"], "unread"))
    assert named == "Guidé par : 2 images d'inspiration"
    assert guided_text(render(["photo-perso.jpg"], "no_style")) == (
        "Guidé par : 1 image d'inspiration"
    )
    assert guided_text(render([], None)) == "Sans inspiration"
    assert guided_text(render(None, None)) is None


def test_rate_limit_and_the_log_never_keeps_the_full_address(
    shared: tuple[World, str], caplog: pytest.LogCaptureFixture
) -> None:
    world, rid = shared
    base = f"/api/projects/{world.project_id}"
    token = world.client.post(f"{base}/realistic/{rid}/share-links", json={}).json()["token"]
    _links(world).limiter = SlidingWindowLimiter(2, 60.0)
    visitor = _anonymous(world, "203.0.113.57")
    with caplog.at_level(logging.WARNING, logger="openplan.share"):
        assert visitor.get(f"/api/share/{token}").status_code == 200
        assert visitor.get(f"/api/share/{token}/image").status_code == 200
        slow = visitor.get(f"/api/share/{token}")
    assert slow.status_code == 429 and slow.headers["retry-after"] == "60"
    assert slow.headers["x-robots-tag"].startswith("noindex")
    assert "203.0.113.0/24" in caplog.text and "203.0.113.57" not in caplog.text
    assert token not in caplog.text
    # Une autre adresse passe.
    assert _anonymous(world, "198.51.100.9").get(f"/api/share/{token}").status_code == 200
    world.close()


def test_the_access_log_never_keeps_the_token(tmp_path: Path, migrated_url: str) -> None:
    assert logged_form("/api/share/abcDEF_123-x") == "/api/share/…"
    assert logged_form("/api/share/abcDEF_123-x/image") == "/api/share/…/image"
    assert logged_form("/api/projects/x/share-links") == "/api/projects/x/share-links"


def test_links_fall_with_their_render_project_and_account(shared: tuple[World, str]) -> None:
    world, rid = shared
    base = f"/api/projects/{world.project_id}"
    world.client.post(f"{base}/realistic/{rid}/share-links", json={})
    links = _links(world)

    def count() -> int:
        with links.session() as db:
            return len(list(db.scalars(select(ShareLink))))

    assert count() == 1
    # Le rendu supprimé : son lien part.
    with world.service.session() as db, db.begin():
        db.execute(delete(RenderRequest).where(RenderRequest.id == UUID(rid)))
    assert count() == 0
    # Le compte supprimé : ses liens partent (un éditeur qui n'est pas propriétaire).
    rid2 = _done_render(world)
    owner = world.service.user_by_email(AUGUS)
    assert owner is not None
    world.person("editrice@example.org")
    editor = world.service.user_by_email("editrice@example.org")
    assert editor is not None
    world.service.set_member(world.project_id, editor.id, "editor", added_by=owner.id)
    links.create(world.project_id, UUID(rid2), editor.id)
    assert count() == 1
    with world.service.session() as db, db.begin():
        db.execute(delete(User).where(User.id == editor.id))
    assert count() == 0
    # Le projet supprimé : ses liens partent.
    world.client.post(f"{base}/realistic/{rid2}/share-links", json={})
    assert count() == 1
    assert world.client.delete(base).status_code == 204
    assert count() == 0
    world.close()
