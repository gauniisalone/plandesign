"""KAN-104 L1 : connexion par mot de passe, oubli, changement, mot de passe posé sur un compte
existant, preuve récente, sessions ; et la migration kan104_0012.

DÉSACTIVÉ par défaut (OPENPLAN_PASSWORD_LOGIN faux) : les deux états sont testés. HIBP est
SIMULÉ (`fetch_range` remplacé, `httpx.get` refusé) : aucun appel réseau. Aucun mot de passe
n'apparaît dans les journaux."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from openplan.api import security
from openplan.api.accounts import passwords
from openplan.api.accounts.db import current_revision, downgrade, make_engine, upgrade
from openplan.api.accounts.mail import FakeMailer
from openplan.api.accounts.models import AuthSession, LoginToken, User
from openplan.api.accounts.password_service import (
    PasswordRefusedError,
    PasswordThrottledError,
    lock_delay,
)
from openplan.api.accounts.passwords import (
    PasswordPolicyError,
    check_policy,
    hash_password,
    verify_password,
)
from openplan.api.accounts.service import AccountService
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

LEA = "lea@example.org"
GOOD = "cerisier en fleur 2026"
OTHER = "lampe de chevet bleue"
PWNED = "motdepasse123456"
WRONG = "Adresse ou mot de passe incorrect."
NEW_ROUTES = (
    ("POST", "/api/auth/password/login", {"email": LEA, "password": GOOD}),
    ("POST", "/api/auth/password/forgot", {"email": LEA}),
    ("POST", "/api/auth/password/reset", {"email": LEA, "code": "123456", "password": GOOD}),
    ("PUT", "/api/auth/password", {"password": GOOD}),
    ("POST", "/api/auth/reauth/start", None),
    ("POST", "/api/auth/reauth/verify", {"code": "123456"}),
    ("GET", "/api/auth/sessions", None),
    ("DELETE", "/api/auth/sessions/00000000-0000-4000-8000-000000000000", None),
)


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest().upper()


@pytest.fixture(autouse=True)
def simulated_hibp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """HIBP simulé : PWNED y figure ; tout vrai appel réseau échoue le test."""
    asked: list[str] = []

    def fake_range(prefix: str) -> str:
        asked.append(prefix)
        digest = _sha1(PWNED)
        lines = ["0" * 35 + ":0"]  # remplissage (Add-Padding)
        if prefix == digest[:5]:
            lines.append(f"{digest[5:]}:42")
        return "\r\n".join(lines)

    def no_network(*_a: object, **_k: object) -> None:
        pytest.fail("appel réseau réel dans un test")

    monkeypatch.setattr(passwords, "fetch_range", fake_range)
    monkeypatch.setattr("httpx.get", no_network)
    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 1000)
    return asked


def _with_accounts(
    made: tuple[TestClient, AccountService, FakeMailer],
) -> tuple[TestClient, AccountService, FakeMailer]:
    """Léa et Bruno ont déjà un compte (inscription sur invitation, le défaut)."""
    for email in (LEA, "bruno@example.org"):
        made[1].create_user(email)
    return made


@pytest.fixture
def on(tmp_path: Path, migrated_url: str) -> tuple[TestClient, AccountService, FakeMailer]:
    # HIBP allumé mais simulé (simulated_hibp) : PWNED y figure.
    return _with_accounts(accounts_client(tmp_path, migrated_url, password_login=True, hibp=True))


@pytest.fixture
def off(tmp_path: Path, migrated_url: str) -> tuple[TestClient, AccountService, FakeMailer]:
    return _with_accounts(accounts_client(tmp_path, migrated_url))


def _with_password(
    client: TestClient, mailer: FakeMailer, email: str = LEA, password: str = GOOD
) -> None:
    """Compte existant (code) qui pose un mot de passe : preuve récente, puis PUT."""
    sign_in(client, mailer, email)
    assert client.post("/api/auth/reauth/start").status_code == 202
    assert (
        client.post("/api/auth/reauth/verify", json={"code": code_of(mailer, email)}).status_code
        == 204
    )
    done = client.put("/api/auth/password", json={"password": password})
    assert done.status_code == 204, done.text


def _hash_directly(service: AccountService, email: str, password: str) -> None:
    """Sans passer par les codes (3 par adresse par 15 min, tous usages confondus)."""
    with service.session() as db, db.begin():
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        user.password_hash = hash_password(password)


def _second(client: TestClient) -> TestClient:
    return TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})


# --- désactivé par défaut -----------------------------------------------------------------


def test_off_by_default_every_new_route_is_absent(
    off: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, _, mailer = off
    assert Settings().password_login is False
    assert Settings.from_env({}, dotenv=None).password_login is False
    assert Settings.from_env({"OPENPLAN_PASSWORD_LOGIN": "1"}, dotenv=None).password_login
    assert "password" not in client.get("/api/auth/login").text
    page = client.post("/api/auth/login/password", data={"email": LEA, "password": GOOD})
    assert page.status_code == 404
    sign_in(client, mailer, LEA)  # le code par e-mail est inchangé
    for method, path, body in NEW_ROUTES:
        assert client.request(method, path, json=body).status_code == 404, path
    assert mailer.last_to(LEA) is not None and "mot de passe" not in mailer.last_to(LEA).subject


def test_on_the_page_offers_a_password_form(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, _, _ = on
    page = client.get("/api/auth/login").text
    assert 'type="password"' in page and "/api/auth/login/password" in page


# --- poser un mot de passe sur un compte existant -----------------------------------------


def test_an_existing_account_sets_a_password_after_a_fresh_code(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, service, mailer = on
    sign_in(client, mailer, LEA)
    elsewhere = _second(client)
    sign_in(elsewhere, mailer, LEA)
    first = client.put("/api/auth/password", json={"password": GOOD})
    assert first.status_code == 403 and "code" in first.json()["detail"]
    assert client.post("/api/auth/reauth/start").status_code == 202
    reauth = mailer.last_code_to(LEA)
    assert reauth is not None and "confirmation" in reauth.subject
    # Un code `reauth` n'ouvre pas de connexion.
    refused = _second(client).post(
        "/api/auth/email/verify", json={"email": LEA, "code": reauth.code}
    )
    assert refused.status_code == 400
    assert client.post("/api/auth/reauth/verify", json={"code": reauth.code}).status_code == 204
    short = client.put("/api/auth/password", json={"password": "court"})
    assert short.status_code == 422 and "12" in short.json()["detail"]
    before = client.cookies.get("pd_session")
    done = client.put("/api/auth/password", json={"password": GOOD})
    assert done.status_code == 204
    assert client.cookies.get("pd_session") != before  # jeton neuf
    assert client.get("/api/auth/me").status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401  # autres sessions fermées
    notice = mailer.last_to(LEA)
    assert notice is not None and "a changé" in notice.subject and not notice.code
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == LEA))
        assert (
            user is not None and user.password_hash and user.password_hash.startswith("$argon2id$")
        )
        assert GOOD not in user.password_hash


def test_change_needs_the_current_password(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, _, mailer = on
    _with_password(client, mailer)
    bad = client.put("/api/auth/password", json={"current": OTHER, "password": OTHER + "!"})
    assert bad.status_code == 403
    ok = client.put("/api/auth/password", json={"current": GOOD, "password": OTHER})
    assert ok.status_code == 204
    fresh = _second(client)
    assert (
        fresh.post("/api/auth/password/login", json={"email": LEA, "password": OTHER}).status_code
        == 200
    )
    assert (
        fresh.post("/api/auth/password/login", json={"email": LEA, "password": GOOD}).status_code
        == 401
    )


# --- connexion ----------------------------------------------------------------------------


def test_login_and_one_uniform_refusal(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, service, mailer = on
    _with_password(client, mailer)
    sign_in(_second(client), mailer, "bruno@example.org")  # compte sans mot de passe
    fresh = _second(client)
    ok = fresh.post(
        "/api/auth/password/login", json={"email": " LEA@example.org ", "password": GOOD}
    )
    assert ok.status_code == 200 and ok.json()["email"] == LEA
    assert fresh.cookies.get("pd_session")
    with service.session() as db:
        methods = set(db.scalars(select(AuthSession.auth_method)))
    assert methods == {"code", "password"}
    answers = [
        _second(client).post("/api/auth/password/login", json={"email": e, "password": p})
        for e, p in ((LEA, OTHER), ("personne@example.org", GOOD), ("bruno@example.org", GOOD))
    ]
    assert {(r.status_code, r.json()["detail"]) for r in answers} == {(401, WRONG)}


def test_the_admin_never_signs_in_with_a_password(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, service, mailer = on
    sign_in(client, mailer, ADMIN)
    _hash_directly(service, ADMIN, GOOD)  # posé à la main : refusé quand même
    refused = _second(client).post(
        "/api/auth/password/login", json={"email": ADMIN, "password": GOOD}
    )
    assert (refused.status_code, refused.json()["detail"]) == (401, WRONG)
    assert (
        client.put("/api/auth/password", json={"current": GOOD, "password": OTHER}).status_code
        == 403
    )
    assert client.post("/api/auth/password/forgot", json={"email": ADMIN}).status_code == 202
    assert mailer.last_code_to(ADMIN) is not None
    assert "mot de passe" not in mailer.last_code_to(ADMIN).subject  # aucun code `reset`


def test_progressive_delay_on_the_password_only(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, service, mailer = on
    _with_password(client, mailer)
    now = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    service.clock = lambda: now
    flows = service.passwords
    for _ in range(5):
        with pytest.raises(PasswordRefusedError):
            flows.login(LEA, OTHER, "", "198.51.100.240")
    with pytest.raises(PasswordRefusedError):  # le bon, pendant le délai : même refus
        flows.login(LEA, GOOD, "", "198.51.100.240")
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == LEA))
        assert user is not None and user.failed_logins == 6 and user.locked_until is not None
    sign_in(_second(client), mailer, LEA)  # le code reste ouvert : pas de blocage du titulaire
    later = now + timedelta(minutes=16)
    service.clock = lambda: later
    user_found, _ = flows.login(LEA, GOOD, "", "198.51.100.240")
    assert user_found.email == LEA
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == LEA))
        assert user is not None and user.failed_logins == 0 and user.locked_until is None
    assert [lock_delay(n) for n in (4, 5, 6, 7)] == [
        None,
        *(timedelta(seconds=s) for s in (1, 2, 4)),
    ]
    assert lock_delay(40) == timedelta(minutes=15)


def test_thirty_failures_per_hour_and_ip(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    _, service, _ = on
    for _ in range(30):
        with pytest.raises(PasswordRefusedError):
            service.passwords.login("personne@example.org", GOOD, "", "198.51.100.241")
    with pytest.raises(PasswordThrottledError):
        service.passwords.login("personne@example.org", GOOD, "", "198.51.100.241")
    with pytest.raises(PasswordRefusedError):  # une autre IP n'est pas touchée
        service.passwords.login("personne@example.org", GOOD, "", "198.51.100.242")


def test_the_page_form(on: tuple[TestClient, AccountService, FakeMailer]) -> None:
    client, _, mailer = on
    _with_password(client, mailer)
    fresh = _second(client)
    bad = fresh.post("/api/auth/login/password", data={"email": LEA, "password": OTHER})
    assert bad.status_code == 401 and WRONG in bad.text and OTHER not in bad.text
    ok = fresh.post(
        "/api/auth/login/password",
        data={"email": LEA, "password": GOOD, "next": "/projets"},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/projets"


# --- oubli --------------------------------------------------------------------------------


def test_forgot_and_reset_with_a_30_minute_code(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, service, mailer = on
    sign_in(client, mailer, LEA)
    _hash_directly(service, LEA, GOOD)
    stale = _second(client)
    assert (
        stale.post("/api/auth/password/login", json={"email": LEA, "password": GOOD}).status_code
        == 200
    )
    anyone = _second(client)
    unknown = anyone.post("/api/auth/password/forgot", json={"email": "personne@example.org"})
    known = anyone.post("/api/auth/password/forgot", json={"email": LEA})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
    assert mailer.last_to("personne@example.org") is None
    reset = mailer.last_code_to(LEA)
    assert reset is not None and "mot de passe" in reset.subject and "30 minutes" in reset.text
    with service.session() as db:
        row = db.scalar(select(LoginToken).where(LoginToken.purpose == "reset"))
        assert row is not None and row.expires_at - row.created_at == timedelta(minutes=30)
    # Un code `reset` n'ouvre pas de connexion ; un code de connexion ne réinitialise pas.
    assert (
        anyone.post("/api/auth/email/verify", json={"email": LEA, "code": reset.code}).status_code
        == 400
    )
    anyone.post("/api/auth/email/start", json={"email": LEA})
    login_code = code_of(mailer, LEA)
    wrong_kind = anyone.post(
        "/api/auth/password/reset", json={"email": LEA, "code": login_code, "password": OTHER}
    )
    assert wrong_kind.status_code == 400
    weak = anyone.post(
        "/api/auth/password/reset", json={"email": LEA, "code": reset.code, "password": PWNED}
    )
    assert weak.status_code == 422 and "fuites" in weak.json()["detail"]
    done = anyone.post(
        "/api/auth/password/reset", json={"email": LEA, "code": reset.code, "password": OTHER}
    )
    assert done.status_code == 200, done.text  # le code restait valable après le refus
    assert anyone.get("/api/auth/me").status_code == 200
    assert (
        stale.get("/api/auth/me").status_code == 401
        and client.get("/api/auth/me").status_code == 401
    )
    again = anyone.post(
        "/api/auth/password/reset", json={"email": LEA, "code": reset.code, "password": GOOD}
    )
    assert again.status_code == 400  # usage unique


# --- sessions -----------------------------------------------------------------------------


def test_my_sessions_only(on: tuple[TestClient, AccountService, FakeMailer]) -> None:
    client, _, mailer = on
    sign_in(client, mailer, LEA)
    phone = _second(client)
    sign_in(phone, mailer, LEA)
    bruno = _second(client)
    sign_in(bruno, mailer, "bruno@example.org")
    mine = client.get("/api/auth/sessions").json()
    assert len(mine) == 2 and sum(s["current"] for s in mine) == 1
    other = next(s for s in mine if not s["current"])
    theirs = bruno.get("/api/auth/sessions").json()[0]["id"]
    assert client.delete(f"/api/auth/sessions/{theirs}").status_code == 404
    assert bruno.get("/api/auth/me").status_code == 200
    assert client.delete(f"/api/auth/sessions/{other['id']}").status_code == 204
    assert phone.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200


# --- politique, HIBP, journaux ------------------------------------------------------------


def test_policy_and_hibp_k_anonymity(simulated_hibp: list[str]) -> None:
    def pwned(value: str) -> bool | None:
        return passwords.is_pwned(value)

    assert check_policy("ｃｅｒｉｓｉｅｒ en fleur", LEA, pwned) == "cerisier en fleur"  # NFKC
    for bad, text in (
        ("onze caract", "12"),
        ("x" * 129, "128"),
        ("PlanDesign", "12"),
        ("plandesign!!", "adresse"),
        ("chez lea@example.org", "adresse"),
        (PWNED, "fuites"),
    ):
        with pytest.raises(PasswordPolicyError, match=text):
            check_policy(bad, LEA, pwned)
    assert simulated_hibp and all(len(p) == 5 for p in simulated_hibp)  # 5 caractères seulement
    assert _sha1(PWNED)[5:] not in "".join(simulated_hibp)


def test_hibp_down_is_accepted_and_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def down(_prefix: str) -> str:
        raise TimeoutError

    monkeypatch.setattr(passwords, "fetch_range", down)
    with caplog.at_level(logging.WARNING, logger="openplan.accounts"):
        assert passwords.is_pwned(GOOD) is None
        assert check_policy(GOOD, LEA, passwords.is_pwned) == GOOD
    assert "HIBP indisponible" in caplog.text and GOOD not in caplog.text


def test_hashes_dummy_and_rehash() -> None:
    stored = hash_password(GOOD)
    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password(stored, GOOD) == (True, False)
    assert verify_password(stored, OTHER) == (False, False)
    assert verify_password(None, GOOD) == (False, False)  # empreinte factice, même calcul
    weak = passwords.PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(GOOD)
    assert verify_password(weak, GOOD) == (True, True)  # paramètres changés : recalculée


@contextmanager
def _all_slots_taken(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Les 2 places de calcul prises par d'autres requêtes ; attente raccourcie pour le test."""
    monkeypatch.setattr(passwords, "SLOT_WAIT_S", 0.05)
    held = [passwords._SLOTS.acquire(timeout=1) for _ in range(passwords.HASH_SLOTS)]
    assert all(held)
    try:
        yield
    finally:
        for _ in held:
            passwords._SLOTS.release()


def _busy(response: httpx.Response) -> bool:
    return (
        response.status_code == 503
        and response.headers["Retry-After"] == "2"
        and response.headers["X-PlanDesign-Limit"] == "hash-busy"
    )


def test_hashing_busy_gives_503_and_keeps_the_code(
    on: tuple[TestClient, AccountService, FakeMailer], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service, mailer = on
    assert passwords.HASH_SLOTS == 2  # les 2 cœurs du serveur
    _hash_directly(service, LEA, GOOD)
    fresh = _second(client)
    with _all_slots_taken(monkeypatch):
        login = fresh.post("/api/auth/password/login", json={"email": LEA, "password": GOOD})
        page = fresh.post("/api/auth/login/password", data={"email": LEA, "password": GOOD})
    assert _busy(login) and "réessayez" in login.json()["detail"]
    assert _busy(page) and "réessayez" in page.text and GOOD not in page.text
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == LEA))
    assert user is not None and user.failed_logins == 0  # rien de compté : rien n'a été vérifié
    # Oubli : le code n'est pas consommé tant que le calcul n'a pas eu de place.
    assert fresh.post("/api/auth/password/forgot", json={"email": LEA}).status_code == 202
    code = code_of(mailer, LEA)
    body = {"email": LEA, "code": code, "password": OTHER}
    with _all_slots_taken(monkeypatch):
        assert _busy(fresh.post("/api/auth/password/reset", json=body))
    assert fresh.post("/api/auth/password/reset", json=body).status_code == 200


def test_a_wrong_code_costs_no_hash(
    on: tuple[TestClient, AccountService, FakeMailer], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, mailer = on
    assert client.post("/api/auth/password/forgot", json={"email": LEA}).status_code == 202
    code_of(mailer, LEA)
    computed: list[str] = []

    class Counting(passwords.PasswordHasher):
        def hash(self, password: str | bytes, *, salt: bytes | None = None) -> str:
            computed.append("calcul")
            return super().hash(password, salt=salt)

    monkeypatch.setattr(passwords, "HASHER", Counting(time_cost=2, memory_cost=19_456))
    wrong = client.post(
        "/api/auth/password/reset", json={"email": LEA, "code": "000000", "password": OTHER}
    )
    assert wrong.status_code == 400 and computed == []


def test_no_password_in_the_logs(
    on: tuple[TestClient, AccountService, FakeMailer], caplog: pytest.LogCaptureFixture
) -> None:
    client, _, mailer = on
    with caplog.at_level(logging.DEBUG):
        _with_password(client, mailer)
        _second(client).post("/api/auth/password/login", json={"email": LEA, "password": OTHER})
        _second(client).post("/api/auth/password/login", json={"email": LEA, "password": GOOD})
    assert GOOD not in caplog.text and OTHER not in caplog.text


# --- interface (lot UI) -------------------------------------------------------------------


def test_me_has_no_password_field_value_while_closed(
    off: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    client, _, mailer = off
    assert client.get("/api/health").json()["password_login"] is False  # le lien est caché
    sign_in(client, mailer, LEA)
    assert client.get("/api/auth/me").json()["has_password"] is None  # mot de passe fermé


def test_hibp_off_makes_no_network_call(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPENPLAN_HIBP : allumé par défaut (production) ; `off` : aucun appel, ni par
    fetch_range ni par httpx, et un mot de passe connu des fuites est accepté."""
    assert Settings().hibp is True
    assert Settings.from_env({}, dotenv=None).hibp is True
    for off in ("off", "0", "false", "non", "no", " OFF "):
        assert Settings.from_env({"OPENPLAN_HIBP": off}, dotenv=None).hibp is False, off
    assert Settings.from_env({"OPENPLAN_HIBP": "on"}, dotenv=None).hibp is True
    calls: list[str] = []

    def spy(*args: object, **_k: object) -> None:
        calls.append(repr(args)[:20])
        raise AssertionError("appel à HIBP alors qu'il est éteint")

    monkeypatch.setattr(passwords, "fetch_range", spy)
    monkeypatch.setattr("httpx.get", spy)
    client, _, mailer = _with_accounts(
        accounts_client(tmp_path, migrated_url, password_login=True, hibp=False)
    )
    sign_in(client, mailer, LEA)
    assert client.post("/api/auth/reauth/start").status_code == 202
    code = code_of(mailer, LEA)
    assert client.post("/api/auth/reauth/verify", json={"code": code}).status_code == 204
    assert client.put("/api/auth/password", json={"password": PWNED}).status_code == 204
    assert calls == []


def test_health_says_the_password_is_open(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    """Ajout au contrat de GET /api/health (public) : `password_login`, comme `signup` et
    `share_links` ; la page de connexion et la fenêtre du site n'offrent le lien qu'ouvert."""
    client, _, _ = on
    assert client.get("/api/health").json()["password_login"] is True


def test_me_says_whether_the_account_has_a_password(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    """Ajout au contrat de GET /auth/me : `has_password`, pour « Définir » ou « Changer »."""
    client, service, mailer = on
    sign_in(client, mailer, LEA)
    assert client.get("/api/auth/me").json()["has_password"] is False
    _with_password(client, mailer)
    assert client.get("/api/auth/me").json()["has_password"] is True
    admin = _second(client)
    sign_in(admin, mailer, ADMIN)
    _hash_directly(service, ADMIN, GOOD)  # posé à la main : l'administrateur reste « par code »
    assert admin.get("/api/auth/me").json()["has_password"] is False


def test_input_errors_of_a_signed_in_person_are_never_401(
    on: tuple[TestClient, AccountService, FakeMailer],
) -> None:
    """Règle du lot UI : une erreur de saisie d'une personne connectée répond 400, 403, 404 ou
    422, jamais 401. Le front lit un 401 comme une session perdue et déconnecte (client.ts)."""
    client, _, mailer = on
    sign_in(client, mailer, LEA)
    answers = {
        "PUT sans preuve récente": client.put("/api/auth/password", json={"password": GOOD}),
        "preuve : code faux": client.post("/api/auth/reauth/verify", json={"code": "000000"}),
        "fermer une session inconnue": client.delete(
            "/api/auth/sessions/00000000-0000-4000-8000-000000000000"
        ),
    }
    assert client.post("/api/auth/reauth/start").status_code == 202
    assert (
        client.post("/api/auth/reauth/verify", json={"code": code_of(mailer, LEA)}).status_code
        == 204
    )
    answers["mot de passe trop court"] = client.put("/api/auth/password", json={"password": "x"})
    answers["mot de passe fuité"] = client.put("/api/auth/password", json={"password": PWNED})
    assert client.put("/api/auth/password", json={"password": GOOD}).status_code == 204
    answers["ancien mot de passe faux"] = client.put(
        "/api/auth/password", json={"current": OTHER, "password": OTHER + "!"}
    )
    answers["ancien mot de passe absent"] = client.put(
        "/api/auth/password", json={"password": OTHER}
    )
    statuses = {what: r.status_code for what, r in answers.items()}
    assert statuses == {
        "PUT sans preuve récente": 403,
        "preuve : code faux": 400,
        "fermer une session inconnue": 404,
        "mot de passe trop court": 422,
        "mot de passe fuité": 422,
        "ancien mot de passe faux": 403,
        "ancien mot de passe absent": 403,
    }
    assert client.get("/api/auth/me").status_code == 200  # toujours connectée


# --- migration ----------------------------------------------------------------------------


def test_migration_kan104_goes_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine, "kan115_0013")
    from uuid import uuid4

    import sqlalchemy as sa

    old_users = sa.table(  # un compte d'avant KAN-104 : les colonnes de 0013 seulement
        "users",
        sa.column("id", sa.Uuid),
        sa.column("email", sa.String),
        sa.column("name", sa.String),
        sa.column("is_admin", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    with engine.begin() as db:
        db.execute(
            old_users.insert().values(
                id=uuid4(),
                email="ancien@example.org",
                name="",
                is_admin=False,
                created_at=datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
            )
        )
    upgrade(engine)
    assert current_revision(engine) == "kan104_0012"
    users = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"password_hash", "password_set_at", "failed_logins", "locked_until"} <= users
    assert {"email_verified_at", "signup_ip", "accepted_terms_at", "terms_version"} <= users
    assert "password" not in users  # jamais en clair
    assert "purpose" in {c["name"] for c in inspect(engine).get_columns("login_tokens")}
    assert {"auth_method", "reauth_at"} <= {
        c["name"] for c in inspect(engine).get_columns("sessions")
    }
    with engine.connect() as con:
        verified, failures = con.exec_driver_sql(
            "SELECT email_verified_at, failed_logins FROM users"
        ).one()
    assert verified is not None and failures == 0  # l'adresse était prouvée par un code
    downgrade(engine, "kan115_0013")
    assert "password_hash" not in {c["name"] for c in inspect(engine).get_columns("users")}
    assert "purpose" not in {c["name"] for c in inspect(engine).get_columns("login_tokens")}
    assert "reauth_at" not in {c["name"] for c in inspect(engine).get_columns("sessions")}
    with engine.connect() as con:
        assert con.exec_driver_sql("SELECT email FROM users").scalar() == "ancien@example.org"
    upgrade(engine)
    assert current_revision(engine) == "kan104_0012"
    engine.dispose()
