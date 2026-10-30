"""KAN-98 L3 : la garde anti-SSRF face à un VRAI serveur local. Un serveur HTTP écoute
réellement sur 127.0.0.1 et sert une image (contrôle : une requête ordinaire la reçoit) ; la
garde, avec la vraie résolution du système, le refuse sans jamais s'y connecter : le serveur
compte chaque connexion acceptée, il doit rester à zéro. Le port du serveur est ajouté aux ports
permis, pour que le refus vienne de l'adresse et non du port."""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.adapters.remote import link_fetch as lf
from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.unit.test_kan113_image_formats import png


class _Image(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = png()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _Counting(ThreadingHTTPServer):
    """Compte chaque connexion TCP acceptée, avant même la lecture de la requête."""

    accepted = 0

    def verify_request(self, request: Any, client_address: Any) -> bool:
        type(self).accepted += 1
        return True


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    _Counting.accepted = 0
    httpd = _Counting(("127.0.0.1", 0), _Image)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # Contrôle : le serveur répond vraiment (un refus ne viendra pas d'un serveur absent).
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/a.png", timeout=5) as r:
        assert r.status == 200 and r.read() == png()
    assert _Counting.accepted == 1
    _Counting.accepted = 0
    monkeypatch.setattr(lf, "PORTS", (*lf.PORTS, port))
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _hosts(port: int) -> list[str]:
    hosts = [f"http://127.0.0.1:{port}/a.png", f"http://localhost:{port}/a.png"]
    hosts.append(f"http://[::ffff:127.0.0.1]:{port}/a.png")  # IPv4 dans une IPv6 mappée
    hosts.append(f"http://2130706433:{port}/a.png")  # 127.0.0.1 écrit en entier
    return hosts


def test_the_guard_refuses_a_real_local_server_without_connecting(server: int) -> None:
    for url in _hosts(server):
        with pytest.raises(lf.LinkError):
            lf.fetch_link_image(url)
    assert _Counting.accepted == 0, "aucune connexion au serveur local"


def test_localhost_really_resolves_to_a_refused_address(server: int) -> None:
    """La vraie résolution du système, pas une table de test : « localhost » donne une
    adresse de bouclage, refusée."""
    found = lf.system_resolve("localhost", server)
    assert found and not any(lf.allowed_ip(a) for a in found)
    with pytest.raises(lf.LinkRefusedError):
        lf.vetted_address("localhost", server, lf.system_resolve)
    assert _Counting.accepted == 0


def test_the_api_route_refuses_it_too(server: int, tmp_path: Path) -> None:
    client = TestClient(
        create_app(settings=Settings(data_dir=tmp_path / "data")), raise_server_exceptions=False
    )
    pid = client.post("/api/projects", json={"name": "Moodboard"}).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/media/link", json={"url": f"http://127.0.0.1:{server}/a.png"}
    )
    assert r.status_code == 400, r.text
    assert "127.0.0.1" not in r.json()["detail"], "jamais l'adresse résolue dans le message"
    assert client.get(f"/api/projects/{pid}/media").json() == []
    assert _Counting.accepted == 0


def test_without_the_test_port_the_port_alone_refuses(
    server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lf, "PORTS", (80, 443))
    with pytest.raises(lf.LinkRefusedError, match="ports web"):
        lf.fetch_link_image(f"http://127.0.0.1:{server}/a.png")
    assert _Counting.accepted == 0
