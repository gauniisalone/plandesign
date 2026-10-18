"""B1e (demande PO) : l'API ne croit `X-Forwarded-For` que depuis FORWARDED_ALLOW_IPS (uvicorn,
127.0.0.1 par défaut). Un client non autorisé qui change d'en-tête à chaque essai ne contourne
pas la limite de 10 tentatives par minute et par IP sur /api/auth/*."""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from openplan.api.app import create_app
from openplan.cli import main as cli
from tests.accounts.helpers import ORIGIN, accounts_settings


def _client(app: object, address: tuple[str, int]) -> TestClient:
    """Le client ASGI vu depuis `address` (l'adresse de la connexion TCP)."""
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}, client=address)  # type: ignore[arg-type]


def _statuses(client: TestClient, n: int) -> list[int]:
    return [
        client.post(
            "/api/auth/email/start",
            json={"email": "z@example.org"},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
        ).status_code
        for i in range(n)
    ]


def test_forwarded_for_from_an_untrusted_client_is_ignored(
    tmp_path: Path, migrated_url: str
) -> None:
    app = create_app(settings=accounts_settings(tmp_path, migrated_url))
    # Comme `openplan serve` (uvicorn, proxy_headers par défaut) : seul 127.0.0.1 est cru.
    served = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")  # type: ignore[arg-type]
    direct = _client(served, ("203.0.113.9", 50000))
    assert 429 in _statuses(direct, 12)  # l'en-tête change, la limite tient (IP réelle)


def test_forwarded_for_from_the_trusted_proxy_is_used(tmp_path: Path, migrated_url: str) -> None:
    app = create_app(settings=accounts_settings(tmp_path, migrated_url))
    served = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")  # type: ignore[arg-type]
    proxy = _client(served, ("127.0.0.1", 50000))
    assert 429 not in _statuses(proxy, 12)  # une adresse de visiteur par essai : 12 compteurs


def test_serve_keeps_uvicorn_default_trust() -> None:
    """`openplan serve` ne relâche pas la confiance (ni `forwarded_allow_ips="*"`) ; sans
    FORWARDED_ALLOW_IPS, uvicorn ne croit que la boucle locale."""
    assert "forwarded_allow_ips" not in inspect.getsource(cli)
    previous = os.environ.pop("FORWARDED_ALLOW_IPS", None)
    try:
        config = uvicorn.Config(app=lambda *_: None)
        trusted = {h.strip() for h in str(config.forwarded_allow_ips).split(",")}
        assert trusted <= {"127.0.0.1", "::1"} and config.proxy_headers
    finally:
        if previous is not None:
            os.environ["FORWARDED_ALLOW_IPS"] = previous
