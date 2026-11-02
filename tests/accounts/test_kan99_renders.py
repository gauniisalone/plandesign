"""KAN-99 : comparer les rendus. Côté serveur : les rendus d'un projet ouverts à tous ses
membres (décision d'augus, 2 nov.), l'étoile « Favoris » commune au projet (`favorites.json`,
sans auteur, hors du format du projet), et le plan d'effacement d'un compte (KAN-114) qui
compte ses rendus montrés dans des projets qui restent.

- Voir (liste, image) : tout membre, lecteur compris, hors liste blanche ; un non-membre
  reçoit 404 (la garde ne révèle pas le projet) ; rendu désactivé : 403 pour tous.
- Lancer, variante, couverture, réactions : liste blanche et lanceur, comme avant.
- L'étoile : tout éditeur ; ni demande créée (quota du jour intact) ni refus d'espace plein.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openplan.api.accounts.db import make_engine, utcnow
from openplan.api.accounts.forget import describe, plan_forget
from openplan.api.accounts.models import RenderRequest
from openplan.api.accounts.render_favorites import FAVORITES, load_favorites, save_favorites
from openplan.api.accounts.render_queue import RenderQueue
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.guards import PROJECT_ROUTES, Role
from openplan.schemas.render_queue import MAX_FAVORITES
from tests.accounts.test_kan89_realistic import World
from tests.accounts.test_kan91_renders import base, listing, render_one

P = "/api/projects/{project_id}"
ROUTES = {
    ("GET", P + "/realistic"): Role.VIEWER,
    ("GET", P + "/realistic/{request_id}/image"): Role.VIEWER,
    ("PUT", P + "/realistic/{request_id}/favorite"): Role.EDITOR,
    ("DELETE", P + "/realistic/{request_id}/favorite"): Role.EDITOR,
}


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def _member(world: World, email: str, role: str | None) -> tuple[object, UUID]:
    client = world.person(email)
    user = world.service.user_by_email(email)
    assert user is not None
    if role is not None:
        world.service.set_member(world.project_id, user.id, role, added_by=world.augus_id)
    return client, user.id


def _allow(world: World, *users: UUID) -> None:
    """Ajoute des comptes à la liste blanche du poste (OPENPLAN_RENDER_ACCOUNTS)."""
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    queue.config = replace(queue.config, accounts=queue.config.accounts | frozenset(users))


def test_routes_and_rules(world: World) -> None:
    assert {k: PROJECT_ROUTES[k] for k in ROUTES} == ROUTES
    world.close()


def test_members_see_every_render_of_the_project(world: World) -> None:
    world.ask()
    rid = str(render_one(world)["request_id"])
    mine = listing(world)["requests"][0]
    assert mine["id"] == rid and mine["mine"] is True
    url = base(world)

    # Lectrice hors liste blanche : la liste du projet et l'image, pas « mine ».
    kim, _ = _member(world, "kim@example.org", "viewer")
    seen = kim.get(f"{url}/realistic")  # type: ignore[attr-defined]
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert [r["id"] for r in body["requests"]] == [rid]
    assert body["requests"][0]["mine"] is False and body["requests"][0]["image"] is True
    assert body["status"]["allowed"] is False  # elle ne lance toujours rien
    image = kim.get(f"{url}/realistic/{rid}/image")  # type: ignore[attr-defined]
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    # Ni lancer, ni étoile (éditeur), ni variante, couverture, réaction.
    asked = kim.post(  # type: ignore[attr-defined]
        f"{url}/levels/{world.level_id}/realistic", json={"camera_id": str(world.camera_id)}
    )
    assert asked.status_code == 403
    assert kim.put(f"{url}/realistic/{rid}/favorite").status_code == 403  # type: ignore[attr-defined]
    assert kim.post(f"{url}/realistic/{rid}/variant").status_code == 403  # type: ignore[attr-defined]
    assert kim.put(f"{url}/realistic/{rid}/cover").status_code == 403  # type: ignore[attr-defined]
    assert kim.put(f"{url}/realistic/{rid}/reactions/up").status_code == 403  # type: ignore[attr-defined]

    # Non-membre : 404 partout (on ne révèle pas que le projet existe).
    bob, _ = _member(world, "bob@example.org", None)
    for method, path in (
        ("GET", f"{url}/realistic"),
        ("GET", f"{url}/realistic/{rid}/image"),
        ("PUT", f"{url}/realistic/{rid}/favorite"),
        ("DELETE", f"{url}/realistic/{rid}/favorite"),
    ):
        assert bob.request(method, path).status_code == 404, path  # type: ignore[attr-defined]

    # Un autre compte autorisé, éditeur : il voit le rendu d'augus, l'étoile, mais variante,
    # couverture et réaction restent au lanceur (404 : pas son rendu).
    zoe, zoe_id = _member(world, "zoe@example.org", "editor")
    _allow(world, zoe_id)
    row = zoe.get(f"{url}/realistic").json()["requests"][0]  # type: ignore[attr-defined]
    assert row["id"] == rid and row["mine"] is False
    assert zoe.post(f"{url}/realistic/{rid}/variant").status_code == 404  # type: ignore[attr-defined]
    assert zoe.put(f"{url}/realistic/{rid}/cover").status_code == 404  # type: ignore[attr-defined]
    assert zoe.put(f"{url}/realistic/{rid}/reactions/up").status_code == 404  # type: ignore[attr-defined]
    starred = zoe.put(f"{url}/realistic/{rid}/favorite")  # type: ignore[attr-defined]
    assert starred.status_code == 200 and starred.json() == {"favorite_ids": [rid]}
    # Sa propre demande est « mine » pour elle, pas pour augus ; elle part sur son quota.
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    world.ask(zoe)  # type: ignore[arg-type]
    assert queue.used_today(zoe_id) == 1
    rows = {r["id"]: r["mine"] for r in listing(world)["requests"]}
    assert sorted(rows.values()) == [False, True]
    assert sorted(r["mine"] for r in zoe.get(f"{url}/realistic").json()["requests"]) == [  # type: ignore[attr-defined]
        False,
        True,
    ]
    # L'étoile d'un éditeur est vue de tous, lectrice comprise.
    assert kim.get(f"{url}/realistic").json()["favorite_ids"] == [rid]  # type: ignore[attr-defined]
    world.close()


def test_favorites_are_shared_free_and_never_refused_for_space(world: World) -> None:
    world.ask()
    first = str(render_one(world)["request_id"])
    url = base(world)
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    used = queue.used_today(world.augus_id)
    put = world.client.put(f"{url}/realistic/{first}/favorite")
    assert put.status_code == 200 and put.json() == {"favorite_ids": [first]}
    # Deux fois : rien de plus ; la liste les donne ; le quota du jour ne bouge pas.
    assert world.client.put(f"{url}/realistic/{first}/favorite").json() == put.json()
    assert listing(world)["favorite_ids"] == [first]
    assert queue.used_today(world.augus_id) == used
    # Le fichier, à côté de la couverture, sans auteur ni date.
    store = world.client.app.state.store  # type: ignore[attr-defined]
    project_dir = store.project_dir(world.project_id)
    text = (project_dir / FAVORITES).read_text(encoding="utf-8")
    assert first in text and str(world.augus_id) not in text and "at" not in text.split('"')
    # Une éditrice hors liste blanche l'enlève (l'étoile n'est à personne).
    marie, _ = _member(world, "marie@example.org", "editor")
    gone = marie.delete(f"{url}/realistic/{first}/favorite")  # type: ignore[attr-defined]
    assert gone.status_code == 200 and gone.json() == {"favorite_ids": []}
    assert marie.delete(f"{url}/realistic/{first}/favorite").json() == {"favorite_ids": []}  # type: ignore[attr-defined]
    # Rendu inconnu, ou pas fait, ou d'un autre projet : 404.
    assert world.client.put(f"{url}/realistic/{uuid4()}/favorite").status_code == 404
    assert world.client.delete(f"{url}/realistic/{uuid4()}/favorite").status_code == 404
    pending = str(world.ask()["id"])
    assert world.client.put(f"{url}/realistic/{pending}/favorite").status_code == 404
    pid, lid, cid = world.new_project(world.client)
    other = world.client.post(
        f"/api/projects/{pid}/levels/{lid}/realistic", json={"camera_id": str(cid)}
    )
    assert other.status_code == 201
    assert world.client.put(f"{url}/realistic/{other.json()['id']}/favorite").status_code == 404
    assert world.client.delete(f"{url}/realistic/{other.json()['id']}/favorite").status_code == 404
    # Espace plein (KAN-12) : lancer est refusé (413), l'étoile passe.
    settings = world.client.app.state.settings  # type: ignore[attr-defined]
    world.client.app.state.settings = replace(settings, quota_mb=1)  # type: ignore[attr-defined]
    (project_dir / "remplissage.bin").write_bytes(b"x" * 2_000_000)
    pid3, lid3, cid3 = world.new_project(world.client)
    refused = world.client.post(
        f"/api/projects/{pid3}/levels/{lid3}/realistic", json={"camera_id": str(cid3)}
    )
    assert refused.status_code == 413
    assert world.client.put(f"{url}/realistic/{first}/favorite").status_code == 200
    world.close()


def test_an_unknown_render_in_the_file_is_not_listed(world: World) -> None:
    world.ask()
    rid = UUID(str(render_one(world)["request_id"]))
    store = world.client.app.state.store  # type: ignore[attr-defined]
    project_dir = store.project_dir(world.project_id)
    save_favorites(project_dir, [uuid4(), rid])
    assert listing(world)["favorite_ids"] == [str(rid)]
    world.close()


def test_favorites_file_is_tolerant_and_capped(tmp_path: Path) -> None:
    assert load_favorites(tmp_path) == []
    (tmp_path / FAVORITES).parent.mkdir(parents=True)
    (tmp_path / FAVORITES).write_text("pas du json", encoding="utf-8")
    assert load_favorites(tmp_path) == []
    ids = [uuid4() for _ in range(MAX_FAVORITES + 5)]
    kept = save_favorites(tmp_path, [ids[0], *ids, ids[1]])
    assert len(kept) == MAX_FAVORITES and kept[-1] == ids[-1]
    assert load_favorites(tmp_path) == kept


def test_rendering_disabled_refuses_everyone(world: World) -> None:
    world.ask()
    rid = str(render_one(world)["request_id"])
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    queue.config = replace(queue.config, accounts=frozenset())
    url = base(world)
    for method, path in (
        ("GET", f"{url}/realistic"),
        ("GET", f"{url}/realistic/{rid}/image"),
        ("PUT", f"{url}/realistic/{rid}/favorite"),
    ):
        assert world.client.request(method, path).status_code == 403, path
    world.close()


def test_forget_plan_counts_renders_shown_in_projects_that_stay(
    tmp_path: Path, migrated_url: str
) -> None:
    """KAN-114 : la clé étrangère `render_requests.user_id` est en cascade. Les rendus du compte
    effacé disparaîtraient aussi des projets partagés qui restent (montrés à tous depuis
    KAN-99) : le plan le dit, chiffré, sans rien écrire."""
    engine = make_engine(migrated_url)
    repo = AccountsRepo(engine)
    lea = repo.create_user("lea@example.org").id
    bruno = repo.create_user("bruno@example.org").id
    solo, theirs = uuid4(), uuid4()
    repo.add_project(solo, lea, "Grenier")
    repo.add_project(theirs, bruno, "Atelier")
    repo.set_member(theirs, lea, "editor", added_by=bruno)
    now = utcnow()
    with repo.session() as db, db.begin():
        for pid in (solo, theirs, theirs):
            db.add(
                RenderRequest(
                    id=uuid4(),
                    user_id=lea,
                    project_id=pid,
                    level_id=uuid4(),
                    camera_id=uuid4(),
                    state="done",
                    created_at=now,
                    expires_at=now,
                    attempts=1,
                )
            )
    data = tmp_path / "data"
    plan = plan_forget(repo, "lea@example.org", projects_dir=data / "projects", data_dir=data)
    assert (plan.renders, plan.renders_kept_projects) == (3, 2)
    lines: list[str] = []
    describe(plan, lines.append)
    assert any("rendus réalistes qu'il a lancés, supprimés : 3, dont 2" in x for x in lines)
    assert not any("bruno" in x for x in lines)
    with repo.session() as db:
        assert db.query(RenderRequest).count() == 3  # rien d'écrit
    engine.dispose()
