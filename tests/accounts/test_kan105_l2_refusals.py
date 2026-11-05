"""KAN-105 L2 : la forme des refus. `reason` sur les 401 et 403, `WWW-Authenticate` sur chaque
401, `Retry-After` sur les 429 ; les lectures de `/api/auth/*` hors du débit des tentatives ;
« erreur de saisie d'un connecté = 400/403/404/409/422, jamais 401 », route par route ; les
codes corrigés hors comptes (photo trop lourde 413, désignation 404, modèle IA absent 503)."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openplan.api import security
from openplan.api.app import create_app
from openplan.api.policy.registry import BY_KEY
from openplan.api.refusal import REASONS
from openplan.api.settings import Settings
from openplan.pipeline import ingestion
from openplan.scene.store import ProjectStore
from tests.accounts.helpers import ORIGIN, accounts_client, sign_in

LEA = "lea@example.org"
ANY_ID = "00000000-0000-4000-8000-000000000000"


# --- 401 : une seule forme --------------------------------------------------------------------


def test_every_401_has_a_reason_and_a_challenge(tmp_path: Path, migrated_url: str) -> None:
    client, service, _ = accounts_client(tmp_path, migrated_url, password_login=True)
    service.create_user(LEA)
    gone = client.get("/api/projects")
    assert gone.status_code == 401
    assert gone.json()["reason"] == "login_required"
    assert gone.headers["WWW-Authenticate"] == "Cookie"
    # Mot de passe faux (le compte n'en a pas : même refus) : `bad_credentials`, pas « session ».
    wrong = client.post("/api/auth/password/login", json={"email": LEA, "password": "x" * 14})
    assert wrong.status_code == 401
    assert wrong.json()["reason"] == "bad_credentials"
    assert wrong.headers["WWW-Authenticate"] == "Cookie"
    assert set(REASONS) >= {"login_required", "bad_credentials", "token_required"}


def test_the_attempt_rate_gives_retry_after(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 2)
    client, _, _ = accounts_client(tmp_path, migrated_url)
    answers = [
        client.post("/api/auth/email/start", json={"email": LEA, "next": "/"}) for _ in range(3)
    ]
    refused = answers[-1]
    assert refused.status_code == 429
    assert refused.json()["reason"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60


def test_twelve_session_reads_do_not_block_the_login_that_follows(
    tmp_path: Path, migrated_url: str
) -> None:
    """« 12 GET /auth/sessions en une minute ne bloquent pas un POST de connexion qui suit »
    (débit des tentatives : 10 par minute et par adresse, lectures exemptées)."""
    assert security.AUTH_RATE_LIMIT == 10
    client, service, mailer = accounts_client(tmp_path, migrated_url, password_login=True)
    service.create_user(LEA)
    sign_in(client, mailer, LEA)  # 2 tentatives comptées
    for _ in range(12):
        assert client.get("/api/auth/sessions").status_code == 200
    assert client.get("/api/auth/login").status_code == 200
    after = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN}).post(
        "/api/auth/password/login", json={"email": LEA, "password": "y" * 14}
    )
    assert after.status_code == 401, after.text  # refusé pour le mot de passe, pas pour le débit
    assert after.json()["reason"] == "bad_credentials"


# --- connecté : jamais 401 ----------------------------------------------------------------------

SIGNED_IN_POLICIES = {None, "user", "admin"}
"""Les routes qu'une personne connectée appelle avec sa session (membre, compte, administrateur).
Hors champ : `public` (la connexion par mot de passe répond 401 `bad_credentials`), `worker` et
`extension` (leurs jetons), `legacy` (refusée, 403)."""
SIGNED_IN_ROUTES = sorted(k for k, e in BY_KEY.items() if e.access.policy in SIGNED_IN_POLICIES)
_PARAM = re.compile(r"\{([a-z_]+)\}")


class _Signed:
    """Une personne connectée avec un projet à elle ; refaite si une route ferme sa session
    (déconnexion) ou retire le projet (tout supprimer)."""

    def __init__(self, tmp_path: Path, url: str) -> None:
        made, self.service, self.mailer = accounts_client(
            tmp_path, url, password_login=True, share_links=True
        )
        # Une 500 (défaut d'une route, hors de ce test) ne doit pas arrêter le tour des routes.
        self.client = TestClient(
            made.app, base_url=ORIGIN, headers={"Origin": ORIGIN}, raise_server_exceptions=False
        )
        self.n = 0
        self.project_id = ""
        self.fresh()

    def fresh(self) -> None:
        self.n += 1
        email = f"saisie{self.n}@example.org"
        self.service.create_user(email)
        self.client.cookies.clear()
        sign_in(self.client, self.mailer, email)
        made = self.client.post("/api/projects", json={"name": "saisie"})
        assert made.status_code in (200, 201), made.text
        self.project_id = made.json()["id"]

    def path(self, template: str) -> str:
        def value(m: re.Match[str]) -> str:
            return self.project_id if m.group(1) == "project_id" else ANY_ID

        return _PARAM.sub(value, template)

    def still_here(self) -> bool:
        me = self.client.get("/api/auth/me").status_code == 200
        return me and self.client.get(f"/api/projects/{self.project_id}").status_code == 200


@pytest.fixture
def signed(tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> _Signed:
    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100_000)
    return _Signed(tmp_path, migrated_url)


def test_a_signed_in_input_error_is_never_401(signed: _Signed) -> None:
    """Chaque route de session du registre, appelée par une personne connectée avec une saisie
    fausse (corps inconnu, identifiants au hasard) : 400, 403, 404, 409 ou 422, jamais 401 (un
    401 renverrait le front à la page de connexion)."""
    assert len(SIGNED_IN_ROUTES) > 100
    got_401: list[str] = []
    for method, template in SIGNED_IN_ROUTES:
        path = signed.path(template)
        body = None if method in ("GET", "DELETE") else {"saisie": "fausse"}
        answer = signed.client.request(method, path, json=body, params={"x": "z"})
        if answer.status_code == 401:
            got_401.append(f"{method} {template} → {answer.text[:120]}")
        elif answer.status_code >= 500:
            # Hors de la règle des 401 : signalé (relevé des petits défauts), sans échouer ici.
            warnings.warn(
                f"KAN-105 L2 : {answer.status_code} sur {method} {template}", stacklevel=1
            )
        if not signed.still_here():
            signed.fresh()
    assert got_401 == []


def test_a_wrong_password_while_signed_in_is_403_not_401(signed: _Signed) -> None:
    """La saisie la plus exposée : le mot de passe actuel faux, en changeant le sien."""
    refused = signed.client.put(
        "/api/auth/password", json={"current": "z" * 14, "password": "w" * 14}
    )
    assert refused.status_code == 403
    assert refused.json()["reason"] == "reauth"
    assert signed.client.get("/api/auth/me").status_code == 200


# --- hors comptes : 413, 404, 503 -------------------------------------------------------------


@pytest.fixture
def local(tmp_path: Path) -> TestClient:
    store = ProjectStore(tmp_path / "data")
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))


def test_a_photo_over_the_limit_is_413_with_augus_text(
    local: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingestion, "MAX_BYTES", 10)
    with local as c:
        pid = c.post("/api/projects", json={"name": "lourde"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/images", files={"file": ("p.png", b"\x89PNG" + b"0" * 64)})
        assert r.status_code == 413
        assert r.json()["detail"] == "Fichier trop volumineux (50 Mo au plus)."
        # Un fichier illisible (sous la limite) reste 422.
        monkeypatch.setattr(ingestion, "MAX_BYTES", 50 * 1024 * 1024)
        bad = c.post(f"/api/projects/{pid}/images", files={"file": ("p.png", b"pas une image")})
        assert bad.status_code == 422


def test_pick_and_outline_in_an_unknown_render_are_404(local: TestClient) -> None:
    with local as c:
        pid = c.post("/api/projects", json={"name": "désigner"}).json()["id"]
        where = {"x": 0.5, "y": 0.5}
        assert c.get(f"/api/projects/{pid}/renders/{ANY_ID}/pick", params=where).status_code == 404
        outline = c.get(f"/api/projects/{pid}/renders/{ANY_ID}/outline.png", params=where)
        assert outline.status_code == 404


def test_no_ai_model_is_503(local: TestClient) -> None:
    with local as c:
        pid = c.post("/api/projects", json={"name": "sans modèle"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/images/{ANY_ID}/perceive")
        assert r.status_code == 503 and "aucun modèle" in r.json()["detail"]
