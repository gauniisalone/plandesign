"""KAN-115 : les rendus d'un compte effacé restent dans leur projet, avec l'auteur « Ancien
membre ». Migration kan115_0013 (`render_requests.user_id` : CASCADE → SET NULL, nullable),
montée ET descente ; galerie, image, favoris et réactions gardés ; personne n'en est plus le
lanceur (variante et couverture refusées)."""

from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select

from openplan.api.accounts.db import (
    check_schema,
    current_revision,
    downgrade,
    head_revision,
    make_engine,
    upgrade,
    utcnow,
)
from openplan.api.accounts.forget import cancel_pending_renders, describe, plan_forget
from openplan.api.accounts.models import RenderRequest, ShareLink, User
from openplan.api.accounts.render_queue import RenderQueue
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.accounts.share_links import ShareLinks
from tests.accounts.helpers import ORIGIN
from tests.accounts.test_kan89_realistic import World
from tests.accounts.test_kan91_renders import base, listing, render_one

FK = "fk_render_requests_user_id_users"
PREVIOUS = importlib.import_module(
    "openplan.api.accounts.migrations.versions.kan115_0013_rendus_ancien_membre"
).down_revision
"""La révision d'avant KAN-115, lue dans la migration (rechaînée à l'intégration)."""


def _user_fk(engine: object) -> tuple[bool, str | None]:
    """(colonne nullable, règle ON DELETE) de `render_requests.user_id`."""
    info = inspect(engine)
    column = next(c for c in info.get_columns("render_requests") if c["name"] == "user_id")
    fk = next(f for f in info.get_foreign_keys("render_requests") if f["referred_table"] == "users")
    rule = (fk.get("options") or {}).get("ondelete")
    return bool(column["nullable"]), None if rule is None else str(rule).upper()


def _row(project: UUID, user: UUID | None, state: str = "done") -> RenderRequest:
    now = utcnow()
    return RenderRequest(
        id=uuid4(),
        user_id=user,
        project_id=project,
        level_id=uuid4(),
        camera_id=uuid4(),
        state=state,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        attempts=1,
    )


def _link(project: UUID, request: UUID, by: UUID) -> ShareLink:
    now = utcnow()
    return ShareLink(
        token_hash=uuid4().hex * 2,
        project_id=project,
        request_id=request,
        created_by=by,
        created_at=now,
        expires_at=now + timedelta(days=1),
        show_project_name=True,
    )


def _link_ids(repo: AccountsRepo) -> set[UUID]:
    with repo.session() as db:
        return set(db.scalars(select(ShareLink.id)))


def test_migration_kan115_up_and_down(db_url: str) -> None:
    """Depuis une base neuve : montée jusqu'à la précédente, puis kan115_0013 ; descente
    documentée (demandes sans lanceur supprimées, avec leurs liens publics) ; remontée. Les
    liens publics (KAN-101) survivent à la recréation de la table sous SQLite."""
    engine = make_engine(db_url)
    upgrade(engine, PREVIOUS)
    assert current_revision(engine) == PREVIOUS
    assert _user_fk(engine) == (False, "CASCADE")
    repo = AccountsRepo(engine)
    lea = repo.create_user("lea@example.org").id
    bruno = repo.create_user("bruno@example.org").id
    pid = uuid4()
    repo.add_project(pid, bruno, "Atelier")
    kept, brunos = _row(pid, lea), _row(pid, bruno)
    with repo.session() as db, db.begin():
        db.add_all([kept, brunos])
    leas_link, kept_link, brunos_link = (
        _link(pid, kept.id, lea),
        _link(pid, kept.id, bruno),
        _link(pid, brunos.id, bruno),
    )
    with repo.session() as db, db.begin():
        db.add_all([leas_link, kept_link, brunos_link])
    upgrade(engine)
    assert current_revision(engine) == "kan115_0013" == head_revision()
    check_schema(engine)
    assert _user_fk(engine) == (True, "SET NULL")
    assert _link_ids(repo) == {leas_link.id, kept_link.id, brunos_link.id}, "rien ne part"
    orphan = _row(pid, None)
    with repo.session() as db, db.begin():
        db.add(orphan)
    # Le compte effacé : sa demande reste, sans lanceur ; le lien qu'il avait créé part.
    with repo.session() as db, db.begin():
        db.execute(delete(User).where(User.id == lea))
    with repo.session() as db:
        rows = db.execute(select(RenderRequest.id, RenderRequest.user_id)).all()
    assert {rid: uid for rid, uid in rows} == {kept.id: None, orphan.id: None, brunos.id: bruno}
    assert _link_ids(repo) == {kept_link.id, brunos_link.id}
    # Descente : obligatoire et en cascade ; les demandes sans lanceur partent d'abord, avec
    # les liens publics qui les visaient.
    downgrade(engine, PREVIOUS)
    assert current_revision(engine) == PREVIOUS
    assert _user_fk(engine) == (False, "CASCADE")
    with repo.session() as db:
        assert db.scalars(select(RenderRequest.id)).all() == [brunos.id]
    assert _link_ids(repo) == {brunos_link.id}
    # Remontée : la demande de Bruno et son lien sont intacts.
    upgrade(engine)
    assert current_revision(engine) == head_revision()
    check_schema(engine)
    assert _user_fk(engine) == (True, "SET NULL")
    with repo.session() as db:
        assert db.scalars(select(RenderRequest.user_id)).all() == [bruno]
    assert _link_ids(repo) == {brunos_link.id}
    engine.dispose()


def test_erasure_cancels_pending_requests_only(tmp_path: Path, migrated_url: str) -> None:
    """Décision du PO (3 nov.) : à l'effacement, les demandes de Léa EN ATTENTE sont annulées
    (sorties de la file, `expired`), dans la même transaction que la suppression du compte ;
    celle déjà prise par le poste suit son sort habituel, la faite reste « Ancien membre ».
    `forget --dry-run` les compte sans rien écrire."""
    engine = make_engine(migrated_url)
    repo = AccountsRepo(engine)
    lea = repo.create_user("lea@example.org").id
    bruno = repo.create_user("bruno@example.org").id
    pid = uuid4()
    repo.add_project(pid, bruno, "Atelier")
    waiting, taken, done = (_row(pid, lea, s) for s in ("pending", "taken", "done"))
    brunos = _row(pid, bruno, "pending")
    with repo.session() as db, db.begin():
        db.add_all([waiting, taken, done, brunos])

    plan = plan_forget(repo, "lea@example.org", projects_dir=tmp_path / "p", data_dir=tmp_path)
    assert (plan.renders, plan.renders_kept_projects, plan.renders_pending) == (3, 3, 1)
    lines: list[str] = []
    describe(plan, lines.append)
    assert "  demandes en attente annulées : 1" in lines
    with repo.session() as db:
        assert db.get(RenderRequest, waiting.id).state == "pending"  # type: ignore[union-attr]

    # Le chemin de l'effacement : annuler, puis supprimer le compte, en une transaction.
    with repo.session() as db, db.begin():
        assert cancel_pending_renders(db, lea, utcnow()) == 1
        db.execute(delete(User).where(User.id == lea))
    with repo.session() as db:
        states = {
            rid: (state, uid, finished is not None)
            for rid, state, uid, finished in db.execute(
                select(
                    RenderRequest.id,
                    RenderRequest.state,
                    RenderRequest.user_id,
                    RenderRequest.finished_at,
                )
            )
        }
    assert states == {
        waiting.id: ("expired", None, True),
        taken.id: ("taken", None, False),
        done.id: ("done", None, False),
        brunos.id: ("pending", bruno, False),
    }
    engine.dispose()


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def test_renders_of_an_erased_account_stay_as_former_member(world: World) -> None:
    """Zoé (autorisée, éditrice) rend et son rendu est étoilé ; son compte est effacé : le rendu
    reste dans la galerie de tous (« Ancien membre »), avec son image, son étoile et ses
    réactions ; personne n'en est le lanceur."""
    url = base(world)
    zoe = world.person("zoe@example.org")
    user = world.service.user_by_email("zoe@example.org")
    assert user is not None
    world.service.set_member(world.project_id, user.id, "editor", added_by=world.augus_id)
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    queue.config = replace(queue.config, accounts=queue.config.accounts | {user.id})
    world.ask(zoe)
    rid = str(render_one(world)["request_id"])
    assert world.client.put(f"{url}/realistic/{rid}/favorite").status_code == 200
    assert world.client.put(f"{url}/realistic/{rid}/reactions/heart").status_code == 200
    seen = {r["id"]: r for r in listing(world)["requests"]}
    assert seen[rid]["author"] != "Ancien membre" and seen[rid]["mine"] is False

    # Effacement du compte (ce que fera `accounts forget --apply`) : la ligne `users` part.
    with queue.session() as db, db.begin():
        db.execute(delete(User).where(User.id == user.id))
    assert queue.get(UUID(rid)).user_id is None  # type: ignore[union-attr]

    body = listing(world)
    row = next(r for r in body["requests"] if r["id"] == rid)
    assert row["author"] == "Ancien membre" and row["mine"] is False and row["image"] is True
    assert body["favorite_ids"] == [rid]
    assert [(p["item_id"], p["count"]) for p in body["reactions"]] == [(rid, 1)]
    image = world.client.get(f"{url}/realistic/{rid}/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    # Personne n'en est le lanceur : variante et couverture refusées (404, pas son rendu).
    assert world.client.post(f"{url}/realistic/{rid}/variant").status_code == 404
    assert world.client.put(f"{url}/realistic/{rid}/cover").status_code == 404
    # L'étoile et les réactions restent possibles (elles ne sont à personne).
    assert world.client.delete(f"{url}/realistic/{rid}/favorite").status_code == 200
    assert world.client.put(f"{url}/realistic/{rid}/reactions/up").status_code == 200
    # Ses propres rendus pour augus : « Vous ».
    world.ask()
    mine = next(r for r in listing(world)["requests"] if r["mine"])
    assert mine["author"] == "Vous"
    world.close()


def test_public_link_of_an_erased_author(world: World) -> None:
    """KAN-101 × KAN-114 × KAN-115 : Zoé partage son rendu par un lien public, puis son compte
    est effacé. Le plan de `forget` compte le lien (supprimé) et le rendu (gardé) ; le lien
    tombe avec le compte (`share_links.created_by` en cascade) : la page publique répond 404,
    le rendu reste « Ancien membre », et personne ne peut le repartager. Un lien créé par un
    autre (hors des routes) reste ouvert, sans rien dire de l'auteur effacé."""
    app = world.client.app
    app.state.settings = replace(app.state.settings, share_links=True)  # type: ignore[attr-defined]
    url = base(world)
    zoe = world.person("zoe@example.org")
    user = world.service.user_by_email("zoe@example.org")
    assert user is not None
    world.service.set_member(world.project_id, user.id, "editor", added_by=world.augus_id)
    queue: RenderQueue = app.state.render_queue  # type: ignore[attr-defined]
    queue.config = replace(queue.config, accounts=queue.config.accounts | {user.id})
    world.ask(zoe)
    rid = str(render_one(world)["request_id"])
    made = zoe.post(f"{url}/realistic/{rid}/share-links", json={})
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    visitor = TestClient(app, base_url=ORIGIN, client=("203.0.113.58", 50000))
    assert visitor.get(f"/api/share/{token}").status_code == 200

    # `accounts forget --dry-run` : le lien est compté supprimé, le rendu gardé.
    data_dir = app.state.settings.data_dir  # type: ignore[attr-defined]
    repo = AccountsRepo(world.service.engine)
    plan = plan_forget(
        repo, "zoe@example.org", projects_dir=data_dir / "projects", data_dir=data_dir
    )
    assert (plan.share_links, plan.renders, plan.renders_kept_projects) == (1, 1, 1)
    lines: list[str] = []
    describe(plan, lines.append)
    assert any("liens publics de rendus supprimés : 1" in x for x in lines)

    with queue.session() as db, db.begin():
        db.execute(delete(User).where(User.id == user.id))
    links: ShareLinks = app.state.share_links  # type: ignore[attr-defined]
    with links.session() as db:
        assert db.scalars(select(ShareLink)).all() == []
    assert visitor.get(f"/api/share/{token}").status_code == 404
    assert visitor.get(f"/api/share/{token}/image").status_code == 404
    row = next(r for r in listing(world)["requests"] if r["id"] == rid)
    assert row["author"] == "Ancien membre" and row["image"] is True
    # Personne n'en est le lanceur : pas de nouveau lien, même pour la propriétaire.
    assert world.client.post(f"{url}/realistic/{rid}/share-links", json={}).status_code == 404

    # Un lien vers ce rendu créé par un autre compte (hors des routes) reste ouvert ; la page
    # ne dit rien de l'auteur effacé.
    _row, other = links.create(world.project_id, UUID(rid), world.augus_id)
    page = visitor.get(f"/api/share/{other}")
    assert page.status_code == 200
    assert "zoe" not in page.text and "Ancien membre" not in page.text
    assert visitor.get(f"/api/share/{other}/image").status_code == 200
    world.close()
