"""KAN-102 : le module navigateur « Ajouter à PlanDesign ». Jetons créés, listés et révoqués
depuis la section Compte (session obligatoire), gardés en empreinte seulement ; le jeton n'ouvre
QUE les deux routes du module (401 ou 403 partout ailleurs, plusieurs routes essayées), jamais
la session ne les ouvre ; l'image passe par la récupération de KAN-98 L3 (garde anti-SSRF) et
entre dans le node Moodboard choisi d'un projet où le compte est éditeur, avec sa source.
Réseau simulé, sauf pour la garde (adresses refusées sans connexion)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select

from openplan.adapters.remote import link_fetch
from openplan.api.accounts import extension as ext
from openplan.api.accounts.db import current_revision, downgrade, make_engine, upgrade
from openplan.api.accounts.forget import describe, plan_forget
from openplan.api.accounts.models import ExtensionToken
from openplan.api.accounts.quota import LINK_RESERVE, MB
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.guards import EXTENSION_ROLES, GLOBAL_ROUTES, PROJECT_ROUTES, Role
from tests.accounts.helpers import ORIGIN, sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan12_quota import _setup
from tests.accounts.test_kan114_forget import LEA, OTHERS, world  # noqa: F401 - fixture
from tests.unit.test_kan113_image_formats import png

EXT_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
TOKENS = "/api/account/extension-tokens"
TARGETS = "/api/extension/targets"
IMAGE_URL = "https://images.exemple.fr/salon%20bleu.png"
PAGE_URL = "https://www.pinterest.fr/pin/123/"


def _images(pid: str) -> str:
    return f"/api/extension/projects/{pid}/images"


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def fake(raw: str) -> link_fetch.FetchedImage:
        asked.append(raw)
        return link_fetch.FetchedImage(png(), "image/png", raw, None)

    monkeypatch.setattr(link_fetch, "fetch_link_image", fake)
    return asked


def _module(app: object, token: str | None) -> TestClient:
    """Le module : son origine chrome-extension://, aucun cookie, le jeton en Bearer."""
    headers = {"Origin": EXT_ORIGIN}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return TestClient(app, base_url=ORIGIN, headers=headers)  # type: ignore[arg-type]


def _token(client: TestClient, name: str = "Chrome du bureau") -> str:
    r = client.post(TOKENS, json={"name": name})
    assert r.status_code == 201, r.text
    secret: str = r.json()["secret"]
    return secret


def _moodboard(client: TestClient, pid: str) -> str:
    board = client.get(f"/api/projects/{pid}/board").json()
    node: str = next(i["id"] for i in board["items"] if i.get("role") == "inspirations")
    return node


def _photos(client: TestClient, pid: str) -> str:
    board = client.get(f"/api/projects/{pid}/board").json()
    node: str = next(i["id"] for i in board["items"] if i.get("role") == "photos")
    return node


def _add(module: TestClient, pid: str, node: str, **extra: object) -> object:
    body = {"node_id": node, "image_url": IMAGE_URL, "page_url": PAGE_URL, **extra}
    return module.post(_images(pid), json=body)


# --- migration -----------------------------------------------------------------------------


def _shape(engine: Engine) -> tuple[object, ...]:
    """La table de KAN-101 (colonnes, clés étrangères, index), comparable d'un état à l'autre."""
    seen = inspect(engine)
    columns = [(c["name"], str(c["type"]), c["nullable"]) for c in seen.get_columns("share_links")]
    keys = sorted(
        (k["referred_table"], tuple(k["constrained_columns"]))
        for k in seen.get_foreign_keys("share_links")
    )
    indexes = sorted(
        (str(i["name"]), tuple(map(str, i["column_names"])))
        for i in seen.get_indexes("share_links")
    )
    return (columns, keys, indexes)


def test_migration_kan102_goes_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine, "kan102_0011")  # la tête est plus loin (KAN-115)
    assert current_revision(engine) == "kan102_0011"
    columns = {c["name"] for c in inspect(engine).get_columns("extension_tokens")}
    assert columns == {"id", "user_id", "name", "token_hash", "created_at", "last_used_at"}
    assert "token" not in columns, "jamais le jeton en clair"
    indexes = {i["name"] for i in inspect(engine).get_indexes("extension_tokens")}
    assert "ix_extension_tokens_user_id" in indexes
    before = set(inspect(engine).get_table_names())
    shares = _shape(engine)
    downgrade(engine, "kan101_0010")
    assert current_revision(engine) == "kan101_0010"
    after = set(inspect(engine).get_table_names())
    assert after == before - {"extension_tokens"}, "rien d'autre ne part"
    assert _shape(engine) == shares, "KAN-101 intact"
    upgrade(engine, "kan102_0011")
    assert current_revision(engine) == "kan102_0011"
    assert set(inspect(engine).get_table_names()) == before
    engine.dispose()


# --- gestion des jetons (section Compte) ---------------------------------------------------


def test_tokens_are_created_listed_and_revoked_with_a_session(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, guest, _pid = _setup(tmp_path, migrated_url, quota_mb=0)
    created = owner.post(TOKENS, json={"name": "  Chrome   du bureau "})
    assert created.status_code == 201
    secret = created.json()["secret"]
    token_id = created.json()["token"]["id"]
    assert secret.startswith("pde_") and UUID(token_id).hex in secret
    listed = owner.get(TOKENS).json()
    assert [t["name"] for t in listed] == ["Chrome du bureau"]
    assert "secret" not in listed[0] and secret not in json.dumps(listed)
    # La base ne garde que l'empreinte SHA-256.
    repo: AccountsRepo = owner.app.state.accounts  # type: ignore[attr-defined]
    with repo.session() as db:
        row = db.scalar(select(ExtensionToken))
        assert row is not None
        assert row.token_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in repr(vars(row))
    assert guest.get(TOKENS).json() == [], "chacun ne voit que les siens"
    assert guest.delete(f"{TOKENS}/{token_id}").status_code == 404, "pas celui d'un autre"
    assert owner.delete(f"{TOKENS}/{token_id}").status_code == 204
    assert owner.delete(f"{TOKENS}/{token_id}").status_code == 404
    assert owner.get(TOKENS).json() == []
    anonymous = _client(owner.app)
    assert anonymous.get(TOKENS).status_code == 401
    assert anonymous.post(TOKENS, json={}).status_code == 401
    assert anonymous.delete(f"{TOKENS}/{token_id}").status_code == 401


def test_ten_tokens_at_most(tmp_path: Path, migrated_url: str) -> None:
    owner, _guest, _pid = _setup(tmp_path, migrated_url, quota_mb=0)
    for n in range(ext.MAX_TOKENS):
        _token(owner, f"jeton {n}")
    refused = owner.post(TOKENS, json={"name": "un de trop"})
    # KAN-105 L2 : un quota du compte (403 `token_cap`), plus un conflit (409).
    assert refused.status_code == 403 and "révoquez" in refused.json()["detail"]
    assert refused.json()["reason"] == "token_cap"


def test_the_token_is_compared_in_constant_time(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _guest, _pid = _setup(tmp_path, migrated_url, quota_mb=0)
    secret = _token(owner)
    tokens: ext.ExtensionTokens = owner.app.state.extension_tokens  # type: ignore[attr-defined]
    compared: list[tuple[str, str]] = []
    real = ext.hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ext.hmac, "compare_digest", spy)
    assert tokens.resolve(secret) is not None
    assert tokens.resolve(secret[:-2] + "xx") is None, "même ligne, secret faux"
    assert tokens.resolve(f"pde_{uuid4().hex}_inconnu") is None, "ligne inconnue"
    assert tokens.resolve("n'importe quoi") is None
    assert len(compared) == 4, "chaque essai compare une empreinte, ligne trouvée ou non"


# --- le jeton n'ouvre que le module --------------------------------------------------------


def test_the_token_opens_nothing_else(tmp_path: Path, migrated_url: str) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    secret = _token(owner)
    module = _module(owner.app, secret)
    assert module.get(TARGETS).status_code == 200
    node = _moodboard(owner, pid)
    tries = [
        ("GET", "/api/projects", None),
        ("POST", "/api/projects", {"name": "Volé"}),
        ("GET", f"/api/projects/{pid}", None),
        ("GET", f"/api/projects/{pid}/board", None),
        ("GET", f"/api/projects/{pid}/media", None),
        ("POST", f"/api/projects/{pid}/media/link", {"url": IMAGE_URL}),
        ("PATCH", f"/api/projects/{pid}/board/items", {"set": [], "delete": [node]}),
        ("DELETE", f"/api/projects/{pid}", None),
        ("GET", "/api/auth/me", None),
        ("GET", "/api/auth/usage", None),
        ("GET", TOKENS, None),
        ("POST", TOKENS, {"name": "un autre"}),
        ("GET", "/api/admin/usage", None),
        ("PUT", "/api/home/settings", {}),
        ("POST", "/api/worker/claim", {}),
        ("GET", "/api/notifications", None),
    ]
    for method, path, body in tries:
        r = module.request(method, path, json=body)
        assert r.status_code in (401, 403), f"{method} {path} : {r.status_code}"
    # Même sans l'origine du module (un script qui aurait volé le jeton) : rien de plus.
    bare = TestClient(owner.app, base_url=ORIGIN, headers={"Authorization": f"Bearer {secret}"})
    for method, path, body in tries[:4]:
        r = bare.request(method, path, json=body)
        assert r.status_code in (401, 403), f"{method} {path} : {r.status_code}"
    assert len(owner.get("/api/projects").json()) == 1, "aucun projet créé par le jeton"


def test_the_session_never_opens_the_module_routes(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    assert owner.get(TARGETS).status_code == 401, "le cookie de session ne suffit pas"
    body = {"node_id": node, "image_url": IMAGE_URL}
    assert owner.post(_images(pid), json=body).status_code == 401
    assert _module(owner.app, None).get(TARGETS).status_code == 401
    assert _module(owner.app, "pde_faux").get(TARGETS).status_code == 401
    assert seen == []


def test_a_revoked_token_stops_at_once(tmp_path: Path, migrated_url: str) -> None:
    owner, _guest, _pid = _setup(tmp_path, migrated_url, quota_mb=0)
    secret = _token(owner)
    module = _module(owner.app, secret)
    assert module.get(TARGETS).status_code == 200
    token_id = owner.get(TOKENS).json()[0]["id"]
    assert owner.get(TOKENS).json()[0]["last_used_at"] is not None
    assert owner.delete(f"{TOKENS}/{token_id}").status_code == 204
    assert module.get(TARGETS).status_code == 401


def test_repeated_bad_tokens_and_big_bodies_are_cut(tmp_path: Path, migrated_url: str) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    secret = _token(owner)
    module = _module(owner.app, secret)
    big = {"node_id": str(uuid4()), "image_url": "https://a.fr/" + "x" * 17_000}
    assert module.post(_images(pid), json=big).status_code == 413
    bad = _module(owner.app, "pde_" + "0" * 32 + "_faux")
    codes = [bad.get(TARGETS).status_code for _ in range(ext.FAILED_AUTH_LIMIT + 1)]
    assert codes[:-1] == [401] * ext.FAILED_AUTH_LIMIT and codes[-1] == 429


# --- les projets et l'ajout d'une image ----------------------------------------------------


def test_targets_are_the_projects_where_the_account_edits(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    # Un projet de Léa où l'administrateur n'est que lecteur : absent de la liste.
    theirs = guest.post("/api/projects", json={"name": "Chez Léa"}).json()["id"]
    invite = guest.post(f"/api/projects/{theirs}/invitations", json={"role": "viewer"})
    assert owner.post(
        "/api/auth/invitation/accept", json={"token": invite.json()["token"]}
    ).is_success
    mine = owner.post("/api/projects", json={"name": "Salon"}).json()["id"]
    got = _module(owner.app, _token(owner)).get(TARGETS).json()
    ids = [p["id"] for p in got["projects"]]
    assert set(ids) == {pid, mine} and theirs not in ids
    grenier = next(p for p in got["projects"] if p["id"] == pid)
    assert grenier["name"] == "Grenier"
    assert grenier["moodboards"] == [{"id": node, "title": "Moodboard"}]
    assert set(grenier) == {"id", "name", "moodboards"}, "rien d'autre du projet"
    # L'invitée éditrice voit le projet partagé.
    shared = _module(guest.app, _token(guest)).get(TARGETS).json()
    assert pid in [p["id"] for p in shared["projects"]]


def test_the_image_lands_in_the_moodboard_with_its_source(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    added = _add(_module(owner.app, _token(owner)), pid, node)
    assert added.status_code == 201, added.text  # type: ignore[attr-defined]
    out = added.json()  # type: ignore[attr-defined]
    assert out["node_id"] == node and out["name"] == "salon-bleu.png"
    assert seen == [IMAGE_URL], "la récupération de KAN-98 L3, par le serveur"
    board = owner.get(f"/api/projects/{pid}/board").json()
    item = next(i for i in board["items"] if i["id"] == out["item_id"])
    assert item["kind"] == "file" and item["frame"] == node and item["reference"] is True
    assert item["ref"] == out["media_id"] and item["url"] == IMAGE_URL
    media = owner.get(f"/api/projects/{pid}/media").json()
    ref = next(m for m in media if m["id"] == out["media_id"])
    assert ref["source_url"] == PAGE_URL and ref["kind"] == "image" and ref["placed"] is True
    file = owner.get(f"/api/projects/{pid}/media/{out['media_id']}/file")
    assert file.status_code == 200 and file.content == png()
    # Une seconde image prend la case suivante ; l'éditrice invitée ajoute aussi.
    again = _add(_module(guest.app, _token(guest)), pid, node, page_url=None)
    assert again.status_code == 201  # type: ignore[attr-defined]
    media = owner.get(f"/api/projects/{pid}/media").json()
    second = next(m for m in media if m["id"] == again.json()["media_id"])  # type: ignore[attr-defined]
    assert second["source_url"] == IMAGE_URL, "sans page : l'adresse de l'image"
    board = owner.get(f"/api/projects/{pid}/board").json()
    inside = [i for i in board["items"] if i["frame"] == node]
    assert len({(i["x"], i["y"]) for i in inside}) == len(inside) == 2
    activity = owner.get(f"/api/projects/{pid}/activity")
    assert activity.status_code == 200 and activity.json(), "le geste entre au journal"


def test_only_an_editor_and_only_a_moodboard(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    invite = owner.post(f"/api/projects/{pid}/invitations", json={"role": "commenter"}).json()
    viewer = _client(owner.app)
    mailer = owner.app.state.accounts.mailer  # type: ignore[attr-defined]
    sign_in(viewer, mailer, "bruno@example.org")
    assert viewer.post("/api/auth/invitation/accept", json={"token": invite["token"]}).is_success
    bruno = _module(owner.app, _token(viewer))
    assert _add(bruno, pid, node).status_code == 403  # type: ignore[attr-defined]
    assert pid not in [p["id"] for p in bruno.get(TARGETS).json()["projects"]]
    stranger = _client(owner.app)
    sign_in(stranger, mailer, "zoe@example.org")
    zoe = _module(owner.app, _token(stranger))
    assert _add(zoe, pid, node).status_code == 404  # type: ignore[attr-defined]
    mine = _module(owner.app, _token(owner))
    assert _add(mine, pid, _photos(owner, pid)).status_code == 422  # type: ignore[attr-defined]
    assert _add(mine, pid, str(uuid4())).status_code == 404  # type: ignore[attr-defined]
    assert _add(mine, str(uuid4()), node).status_code == 404  # type: ignore[attr-defined]
    assert seen == [], "aucun refus n'a demandé le site"
    assert _add(mine, pid, node).status_code == 201  # type: ignore[attr-defined]


def test_the_same_ssrf_guard_as_a_pasted_link(tmp_path: Path, migrated_url: str) -> None:
    """Sans réseau simulé : la vraie garde de L3 refuse ces adresses AVANT toute connexion
    (schéma, port, adresse interne)."""
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    module = _module(owner.app, _token(owner))
    for url in (
        "http://127.0.0.1/a.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:10.0.0.1]/a.png",
        "https://exemple.fr:8443/a.png",
        "file:///etc/passwd",
        "data:image/png;base64,iVBORw0KGgo=",
    ):
        r = _add(module, pid, node, image_url=url)
        assert r.status_code == 400, f"{url} : {r.status_code}"  # type: ignore[attr-defined]
    assert owner.get(f"/api/projects/{pid}/media").json() == []


def test_errors_of_the_fetch_are_those_of_l3(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    node = _moodboard(owner, pid)
    module = _module(owner.app, _token(owner))

    def too_big(_raw: str) -> link_fetch.FetchedImage:
        raise link_fetch.LinkTooBigError("Image trop lourde : 10 Mo au plus.")

    monkeypatch.setattr(link_fetch, "fetch_link_image", too_big)
    assert _add(module, pid, node).status_code == 413  # type: ignore[attr-defined]

    def gif(raw: str) -> link_fetch.FetchedImage:
        return link_fetch.FetchedImage(b"GIF89a" + b"\0" * 64, "image/gif", raw, None)

    monkeypatch.setattr(link_fetch, "fetch_link_image", gif)
    refused = _add(module, pid, node)
    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert owner.get(f"/api/projects/{pid}/media").json() == []


def test_the_space_of_the_owner_is_checked_before_the_site(
    tmp_path: Path, migrated_url: str, seen: list[str]
) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=30)
    node = _moodboard(owner, pid)
    runs = owner.app.state.store.project_dir(pid) / "runs"  # type: ignore[attr-defined]
    runs.mkdir(exist_ok=True)
    (runs / "presque.bin").write_bytes(b"x" * (30 * MB - LINK_RESERVE + MB))
    refused = _add(_module(owner.app, _token(owner)), pid, node)
    assert refused.status_code == 403 and seen == []  # type: ignore[attr-defined]
    assert refused.json()["reason"] == "space_full"  # type: ignore[attr-defined]


def test_route_tables() -> None:
    module = {k for k, v in GLOBAL_ROUTES.items() if v == "extension"}
    assert module == {
        ("GET", "/api/extension/targets"),
        ("POST", "/api/extension/projects/{project_id}/images"),
    }
    assert set(EXTENSION_ROLES) <= module and set(EXTENSION_ROLES.values()) == {Role.EDITOR}
    assert not any(path.startswith("/api/extension") for _m, path in PROJECT_ROUTES)
    tokens = {k: v for k, v in GLOBAL_ROUTES.items() if "extension-tokens" in k[1]}
    assert set(tokens.values()) == {"user"} and len(tokens) == 3


# --- KAN-114 : la simulation d'effacement compte les jetons --------------------------------


def test_forget_counts_the_extension_tokens(world: dict[str, object]) -> None:  # noqa: F811
    repo: AccountsRepo = world["repo"]  # type: ignore[assignment]
    tokens = ext.ExtensionTokens(repo)
    users: dict[str, UUID] = world["users"]  # type: ignore[assignment]
    tokens.create(users[LEA], "Chrome")
    tokens.create(users[LEA], "Firefox")
    tokens.create(users[OTHERS[0]], "celui de Bruno")
    plan = plan_forget(
        repo,
        LEA,
        projects_dir=world["projects"],  # type: ignore[arg-type]
        data_dir=world["data"],  # type: ignore[arg-type]
    )
    assert plan.extension_tokens == 2
    lines: list[str] = []
    describe(plan, lines.append)
    assert "jetons de l'extension navigateur supprimés : 2" in "\n".join(lines)
    with repo.session() as db:
        assert len(list(db.scalars(select(ExtensionToken)))) == 3, "rien n'est écrit"
