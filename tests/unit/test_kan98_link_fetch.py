"""KAN-98 L3 : la garde anti-SSRF du lien collé, cas par cas, sans aucun réseau réel (la
résolution et les connexions sont simulées ; le backend simulé note l'adresse à laquelle
chaque connexion est ouverte)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpcore
import pytest

from openplan.adapters.remote import link_fetch as lf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 200
GIF = b"GIF89a" + b"\x00" * 200
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"


def reply(status: int = 200, body: bytes = b"", **headers: str) -> list[bytes]:
    lines = [f"HTTP/1.1 {status} X", "Connection: close"]
    if "content_length" not in headers:
        lines.append(f"Content-Length: {len(body)}")
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    head = ("\r\n".join(lines) + "\r\n\r\n").encode()
    return [head, body] if body else [head]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Stream(httpcore.MockStream):
    def __init__(self, buffer: list[bytes], net: Net) -> None:
        super().__init__(buffer)
        self.net = net
        self.sent = b""

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.sent += buffer

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.net.clock.now += self.net.tick
        return super().read(max_bytes, timeout)


class Net(httpcore.NetworkBackend):
    """Le réseau simulé : `answers[ip]`, les réponses rendues l'une après l'autre ;
    `opened`, les adresses auxquelles une connexion a été ouverte."""

    def __init__(self) -> None:
        self.answers: dict[str, list[list[bytes]]] = {}
        self.opened: list[str] = []
        self.streams: list[Stream] = []
        self.clock = Clock()
        self.tick = 0.0

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        self.opened.append(host)
        return self._open(host)

    def _open(self, host: str) -> httpcore.NetworkStream:
        queue = self.answers.get(host)
        if not queue:
            raise httpcore.ConnectError("rien n'écoute")
        stream = Stream(queue.pop(0), self)
        self.streams.append(stream)
        return stream


class DNS:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.calls: list[str] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        found = self.table.get(host)
        if found is None:
            raise OSError("nom inconnu")
        if found and isinstance(found[0], list):  # une réponse différente à chaque appel
            return [str(a) for a in found.pop(0)]
        return [str(a) for a in found]


def fetcher(dns: DNS, net: Net) -> lf.LinkFetcher:
    return lf.LinkFetcher(dns, inner=net, clock=net.clock)


# ------------------------------------------------------------------ adresses refusées


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.10",
        "169.254.169.254",  # métadonnées du nuage
        "169.254.1.1",
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "192.0.2.1",  # documentation
        "198.18.0.1",  # bancs d'essai
        "240.0.0.1",  # réservé
        "224.0.0.1",  # multicast
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%eth0",
        "fc00::1",
        "fd12:3456::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",  # IPv4 mappée
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",  # NAT64 vers 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 vers 169.254.169.254
        "2002:7f00:1::",  # 6to4 vers 127.0.0.1
        "2002:c0a8:101::",  # 6to4 vers 192.168.1.1
        "pas-une-ip",
    ],
)
def test_forbidden_addresses(address: str) -> None:
    assert not lf.allowed_ip(address)


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses(address: str) -> None:
    assert lf.allowed_ip(address)


def test_teredo_carrying_a_private_client_is_refused() -> None:
    # Teredo : serveur 65.54.227.120, client 192.168.1.1 (bits inversés : 3f57:fefe).
    assert not lf.allowed_ip("2001:0:4136:e378:8000:63bf:3f57:fefe")


@pytest.mark.parametrize("host", ["2130706433", "0177.0.0.1", "0x7f.1", "127.1"])
def test_disguised_loopback_is_checked_on_the_resolved_address(host: str) -> None:
    """Le système lit ces écritures comme 127.0.0.1 : la garde vérifie ce qu'il rend."""
    net = Net()
    dns = DNS({host: ["127.0.0.1"]})
    with pytest.raises(lf.LinkRefusedError):
        fetcher(dns, net).fetch(f"http://{host}/a.png")
    assert net.opened == []


def test_one_forbidden_address_among_public_ones_refuses() -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(body=PNG, content_type="image/png")]
    dns = DNS({"mixte.example": [PUBLIC, "10.0.0.5"]})
    with pytest.raises(lf.LinkRefusedError):
        fetcher(dns, net).fetch("https://mixte.example/a.png")
    assert net.opened == []


def test_localhost_by_name_is_refused() -> None:
    net = Net()
    with pytest.raises(lf.LinkRefusedError):
        fetcher(DNS({"localhost": ["127.0.0.1", "::1"]}), net).fetch("http://localhost/x.png")
    assert net.opened == []


# ------------------------------------------------------------------ connexion à l'IP vérifiée


def test_connection_goes_to_the_vetted_address_not_the_name() -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(body=PNG, content_type="image/png")]
    dns = DNS({"images.example": [PUBLIC]})
    got = fetcher(dns, net).fetch("https://images.example/a.png")
    assert got.data == PNG and got.media_type == "image/png"
    assert net.opened == [PUBLIC]
    assert dns.calls == ["images.example"]
    sent = net.streams[0].sent
    assert b"Host: images.example" in sent  # le nom d'origine reste celui de la requête


def test_rebinding_cannot_swap_the_address_after_the_check() -> None:
    """Un nom qui résout d'abord public puis privé : une seule résolution par connexion, et
    la connexion part sur l'adresse vérifiée."""
    net = Net()
    net.answers[PUBLIC] = [reply(body=PNG, content_type="image/png")]
    dns = DNS({"rebind.example": [[PUBLIC], ["127.0.0.1"]]})
    fetcher(dns, net).fetch("http://rebind.example/a.png")
    assert dns.calls == ["rebind.example"]
    assert net.opened == [PUBLIC]


def test_tls_is_started_with_the_original_name() -> None:
    names: list[str | None] = []

    class TLSStream(Stream):
        def start_tls(
            self,
            ssl_context: Any,
            server_hostname: str | None = None,
            timeout: float | None = None,
        ) -> httpcore.NetworkStream:
            names.append(server_hostname)
            return self

    class TLSNet(Net):
        def _open(self, host: str) -> httpcore.NetworkStream:
            return TLSStream(self.answers[host].pop(0), self)

    net = TLSNet()
    net.answers[PUBLIC] = [reply(body=PNG, content_type="image/png")]
    fetcher(DNS({"secure.example": [PUBLIC]}), net).fetch("https://secure.example/a.png")
    assert names == ["secure.example"]
    assert net.opened == [PUBLIC]


def test_unix_socket_is_refused() -> None:
    with pytest.raises(lf.LinkRefusedError):
        lf.GuardedBackend(DNS({})).connect_unix_socket("/var/run/docker.sock")


def test_unknown_name_is_unreachable() -> None:
    with pytest.raises(lf.LinkUnreachableError):
        fetcher(DNS({}), Net()).fetch("https://inconnu.example/a.png")


# ------------------------------------------------------------------ adresses de lien


@pytest.mark.parametrize(
    "url",
    [
        "ftp://images.example/a.png",
        "file:///etc/passwd",
        "gopher://images.example/",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
        "https://user:pass@images.example/a.png",
        "https://user@images.example/a.png",
        "https://images.example:8080/a.png",
        "http://images.example:22/",
        "https://images.example:6379/",
        "http:///a.png",
        "pas une adresse",
    ],
)
def test_refused_before_any_resolution(url: str) -> None:
    net, dns = Net(), DNS({"images.example": [PUBLIC]})
    with pytest.raises(lf.LinkRefusedError):
        fetcher(dns, net).fetch(url)
    assert dns.calls == []
    assert net.opened == []


@pytest.mark.parametrize("url", ["http://images.example:443/a.png", "https://images.example:80/"])
def test_ports_80_and_443_are_allowed(url: str) -> None:
    assert lf.checked_url(url).host == "images.example"


# ------------------------------------------------------------------ redirections


def _redirect(to: str, status: int = 302) -> list[bytes]:
    return reply(status, location=to)


def test_three_redirects_are_followed_each_one_checked() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        _redirect("https://b.example/1"),
        _redirect("/2"),
        _redirect("https://c.example/3", 301),
    ]
    net.answers[PUBLIC_2] = [reply(body=JPEG, content_type="image/jpeg")]
    dns = DNS({"a.example": [PUBLIC], "b.example": [PUBLIC], "c.example": [PUBLIC_2]})
    got = fetcher(dns, net).fetch("https://a.example/0")
    assert got.media_type == "image/jpeg"
    assert got.image_url == "https://c.example/3"
    assert dns.calls == ["a.example", "b.example", "b.example", "c.example"]


def test_a_fourth_redirect_is_refused() -> None:
    net = Net()
    net.answers[PUBLIC] = [_redirect(f"https://a.example/{i}") for i in range(1, 5)]
    with pytest.raises(lf.LinkRefusedError, match="redirections"):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/0")
    assert len(net.opened) == 4


@pytest.mark.parametrize(
    ("target", "table"),
    [
        ("http://169.254.169.254/latest/meta-data/", {}),
        ("http://interne.example/admin", {"interne.example": ["10.1.2.3"]}),
        ("http://[::1]/", {}),
        ("ftp://a.example/x", {}),
        ("https://a.example:2375/", {}),
    ],
)
def test_redirect_to_a_forbidden_target_is_refused(
    target: str, table: dict[str, list[str]]
) -> None:
    net = Net()
    net.answers[PUBLIC] = [_redirect(target)]
    dns = DNS({"a.example": [PUBLIC], "169.254.169.254": ["169.254.169.254"], "::1": ["::1"]})
    dns.table.update(table)
    with pytest.raises(lf.LinkRefusedError):
        fetcher(dns, net).fetch("http://a.example/x")
    assert net.opened == [PUBLIC]


def test_https_to_http_redirect_is_refused() -> None:
    net = Net()
    net.answers[PUBLIC] = [_redirect("http://a.example/clair.png")]
    with pytest.raises(lf.LinkRefusedError, match="non sécurisée"):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/a.png")
    assert len(net.opened) == 1


def test_no_cookie_or_credentials_are_ever_sent() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        reply(302, location="/b", set_cookie="session=secret; Path=/"),
        reply(body=PNG, content_type="image/png"),
    ]
    fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/a")
    for stream in net.streams:
        head = stream.sent.split(b"\r\n\r\n", 1)[0].lower()
        assert b"cookie" not in head
        assert b"authorization" not in head
        assert b"referer" not in head
        assert b"proxy" not in head


# ------------------------------------------------------------------ taille, délai, type


def test_declared_too_big_is_refused_before_reading() -> None:
    net = Net()
    big = str(lf.MAX_IMAGE_BYTES + 1)
    net.answers[PUBLIC] = [
        [
            (
                "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: image/png\r\n"
                f"Content-Length: {big}\r\n\r\n"
            ).encode(),
            PNG,
        ]
    ]
    with pytest.raises(lf.LinkTooBigError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/a.png")


def test_stream_over_the_limit_without_length_is_cut() -> None:
    net = Net()
    chunk = b"\x00" * (1024 * 1024)
    body = [PNG] + [chunk] * 11
    head = "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: image/png\r\n\r\n"
    net.answers[PUBLIC] = [[head.encode(), *body]]
    with pytest.raises(lf.LinkTooBigError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/a.png")


def test_slow_site_hits_the_overall_deadline() -> None:
    net = Net()
    net.tick = 2.0  # chaque lecture prend 2 s : sous le délai de lecture, au-delà du total
    head = "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: image/png\r\n\r\n"
    net.answers[PUBLIC] = [[head.encode(), *([PNG] * 20)]]
    with pytest.raises(lf.LinkTooSlowError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/a.png")


def test_read_timeout_is_too_slow() -> None:
    class Hanging(Net):
        def _open(self, host: str) -> httpcore.NetworkStream:
            raise httpcore.ConnectTimeout("délai")

    with pytest.raises(lf.LinkTooSlowError):
        fetcher(DNS({"a.example": [PUBLIC]}), Hanging()).fetch("https://a.example/a.png")


@pytest.mark.parametrize(
    ("body", "kind"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")],
)
def test_image_type_comes_from_the_bytes(body: bytes, kind: str) -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(body=body, content_type="application/octet-stream")]
    got = fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/x")
    assert got.media_type == kind


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (SVG, "image/svg+xml"),
        (GIF, "image/gif"),  # KAN-113 : pas de décodeur GIF
        (b"MZ\x90\x00 un programme", "image/png"),
        (b'{"pas": "une image"}', "image/png"),
        (b"", "image/png"),
    ],
)
def test_not_an_image_is_refused_whatever_the_header(body: bytes, content_type: str) -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(body=body, content_type=content_type)]
    with pytest.raises(lf.LinkRefusedError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/x")


def test_error_status_is_unreachable() -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(404, b"introuvable", content_type="text/plain")]
    with pytest.raises(lf.LinkUnreachableError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/x")


# ------------------------------------------------------------------ page et og:image


def _page(meta: str) -> bytes:
    head = f"<head><title>Salon</title>{meta}</head>"
    return f"<!doctype html><html>{head}<body>x</body></html>".encode()


def test_page_preview_image_relative_url() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        reply(
            body=_page('<meta property="og:image" content="/img/salon.jpg">'),
            content_type="text/html",
        ),
        reply(body=JPEG, content_type="image/jpeg"),
    ]
    got = fetcher(DNS({"deco.example": [PUBLIC]}), net).fetch("https://deco.example/article")
    assert got.media_type == "image/jpeg"
    assert got.page_url == "https://deco.example/article"
    assert got.image_url == "https://deco.example/img/salon.jpg"


def test_page_preview_image_on_another_site_is_checked_too() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        reply(
            body=_page('<meta property="og:image" content="http://10.0.0.1/x.png">'),
            content_type="text/html",
        )
    ]
    with pytest.raises(lf.LinkNoImageError):
        fetcher(DNS({"deco.example": [PUBLIC], "10.0.0.1": ["10.0.0.1"]}), net).fetch(
            "https://deco.example/a"
        )
    assert net.opened == [PUBLIC]


def test_page_preview_image_private_host_is_refused() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        reply(
            body=_page('<meta name="twitter:image" content="https://cdn.interne/x.png">'),
            content_type="text/html",
        )
    ]
    dns = DNS({"deco.example": [PUBLIC], "cdn.interne": ["192.168.0.4"]})
    with pytest.raises(lf.LinkRefusedError):
        fetcher(dns, net).fetch("https://deco.example/a")
    assert net.opened == [PUBLIC]


def test_page_without_preview_image_says_so() -> None:
    net = Net()
    net.answers[PUBLIC] = [reply(body=_page(""), content_type="text/html; charset=utf-8")]
    with pytest.raises(lf.LinkNoImageError, match="ne propose pas d'image"):
        fetcher(DNS({"blog.example": [PUBLIC]}), net).fetch("https://blog.example/a")


def test_preview_that_is_a_page_again_is_not_followed() -> None:
    net = Net()
    page = _page('<meta property="og:image" content="/autre-page">')
    net.answers[PUBLIC] = [
        reply(body=page, content_type="text/html"),
        reply(body=page, content_type="text/html"),
    ]
    with pytest.raises(lf.LinkNoImageError):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/")
    assert len(net.opened) == 2  # un seul niveau


def test_preview_downgraded_to_http_is_refused() -> None:
    net = Net()
    net.answers[PUBLIC] = [
        reply(
            body=_page('<meta property="og:image" content="http://a.example/x.png">'),
            content_type="text/html",
        )
    ]
    with pytest.raises(lf.LinkNoImageError, match="sécurisée"):
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/")


def test_big_page_is_read_only_up_to_its_limit() -> None:
    net = Net()
    filler = b"<!-- " + b"x" * (lf.MAX_PAGE_BYTES * 2) + b" -->"
    late = b'<meta property="og:image" content="/tard.png">'
    head = "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: text/html\r\n\r\n"
    net.answers[PUBLIC] = [[head.encode(), b"<!doctype html><html><head>", filler, late]]
    with pytest.raises(lf.LinkNoImageError):  # la balise après la limite n'est jamais lue
        fetcher(DNS({"a.example": [PUBLIC]}), net).fetch("https://a.example/")


def test_preview_meta_order_and_broken_html() -> None:
    assert lf.preview_image('<meta property="og:image" content="/a.png">') == "/a.png"
    assert (
        lf.preview_image(
            '<meta name="twitter:image" content="/t.png">'
            '<meta property="og:image:secure_url" content="/s.png">'
        )
        == "/s.png"
    )
    assert lf.preview_image("<meta property='og:image' content=''>") is None
    assert lf.preview_image("<html><head><meta property=og:image content=/b.png") in (
        None,
        "/b.png",
    )
