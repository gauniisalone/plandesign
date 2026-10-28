"""KAN-98 L3 : l'image d'un lien collé, récupérée par le serveur sous une garde anti-SSRF.

Le lien vient de l'utilisateur : sans garde, le serveur irait chercher pour lui une adresse
interne (base, métadonnées du VPS, service local). La garde (conception validée par le PO le
28 oct.) :

- http ou https seulement, ports 80 et 443, aucun identifiant dans l'adresse ;
- une seule résolution DNS par connexion, TOUTES les adresses rendues vérifiées, puis la
  connexion ouverte sur l'adresse vérifiée elle-même (le backend réseau de httpcore reçoit le
  nom et se connecte à l'IP) : rien à détourner entre la vérification et la connexion
  (rebinding) ; TLS reste vérifié sur le nom d'origine (SNI et certificat) ;
- une adresse est permise si elle est globale et pas multicast, formes IPv6 qui portent une
  IPv4 déroulées (mappée, NAT64, 6to4, Teredo) : bouclage, privé, lien local (dont
  169.254.169.254), CGNAT, réservé et documentation sont refusés ;
- 3 redirections au plus, chacune revérifiée en entier, jamais de https vers http ;
- 10 Mo au plus pour une image, lus en flux ; 512 Ko de page au plus pour trouver son image
  d'aperçu (og:image), récupérée par la même garde, sur un seul niveau ;
- délais courts (connexion 3 s, lecture 5 s, 10 s pour toute la chaîne) ;
- ni cookie, ni autorisation, ni Referer, ni proxy de l'environnement : httpcore seul, sans
  le client httpx qui les gère.

Le type de l'image est reconnu à ses premiers octets, jamais à l'en-tête du site ; le
contrôle complet (Pillow, taille en pixels) est fait par l'appelant.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpcore
import httpx

SCHEMES = ("http", "https")
PORTS = (80, 443)
MAX_REDIRECTS = 3
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 512 * 1024
CONNECT_S = 3.0
READ_S = 5.0
TOTAL_S = 10.0
CHUNK = 64 * 1024
USER_AGENT = "PlanDesign/1.0 (image d'un lien collé)"
IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}
"""Images acceptées, d'après leurs premiers octets ; ni SVG (il peut porter un script), ni GIF
(KAN-113 : seuls les décodeurs JPEG, PNG et WebP sont essayés), ni rien d'autre."""
PREVIEW_META = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_REDIRECTS = (301, 302, 303, 307, 308)

Resolver = Callable[[str, int], list[str]]


class LinkError(Exception):
    """Refus ou échec, avec le code HTTP que la route rend et un message pour la personne
    (jamais l'adresse IP résolue)."""

    status = 400


class LinkRefusedError(LinkError):
    status = 400


class LinkTooBigError(LinkError):
    status = 413


class LinkNoImageError(LinkError):
    status = 422


class LinkUnreachableError(LinkError):
    status = 502


class LinkTooSlowError(LinkError):
    status = 504


@dataclass(frozen=True)
class FetchedImage:
    data: bytes
    media_type: str
    image_url: str
    """L'adresse de l'image, après redirections."""
    page_url: str | None
    """La page dont l'image d'aperçu a été prise, si le lien était une page."""


# ------------------------------------------------------------------ adresses


def _embedded_v4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """Les IPv4 que porte une IPv6 (mappée, NAT64, 6to4, Teredo) : vérifiées elles aussi."""
    found: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip in _NAT64:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)
    return found


def allowed_ip(address: str) -> bool:
    """Vrai seulement pour une adresse publique : globale, pas multicast, et toute IPv4
    qu'elle porte aussi."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        found.extend(_embedded_v4(ip))
    return all(a.is_global and not a.is_multicast for a in found)


def system_resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def vetted_address(host: str, port: int, resolve: Resolver) -> str:
    """Une seule résolution ; refus si une seule des adresses rendues est interdite (on ne
    choisit pas « la bonne »). Rend l'adresse à laquelle se connecter."""
    try:
        addresses = resolve(host, port)
    except (OSError, UnicodeError) as exc:
        msg = "Ce site est introuvable."
        raise LinkUnreachableError(msg) from exc
    if not addresses:
        msg = "Ce site est introuvable."
        raise LinkUnreachableError(msg)
    if not all(allowed_ip(a) for a in addresses):
        msg = "Cette adresse n'est pas publique : elle ne peut pas être récupérée."
        raise LinkRefusedError(msg)
    return addresses[0]


class GuardedBackend(httpcore.NetworkBackend):
    """Le backend réseau de httpcore : chaque connexion TCP passe ici, avec le nom du site ;
    elle est ouverte sur l'adresse vérifiée, jamais sur le nom. httpcore fait ensuite TLS sur
    cette connexion avec le nom d'origine (SNI et vérification du certificat)."""

    def __init__(
        self, resolve: Resolver = system_resolve, inner: httpcore.NetworkBackend | None = None
    ) -> None:
        self.resolve = resolve
        self.inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        address = vetted_address(host, port, self.resolve)
        return self.inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        msg = "connexion locale refusée"
        raise LinkRefusedError(msg)

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


# ------------------------------------------------------------------ adresses de lien


def checked_url(raw: str) -> httpx.URL:
    """Schéma, identifiants et port : refus avant toute résolution."""
    try:
        url = httpx.URL(raw.strip())
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        msg = "Ce lien n'est pas une adresse web valide."
        raise LinkRefusedError(msg) from exc
    if url.scheme not in SCHEMES:
        msg = "Seuls les liens http:// et https:// sont acceptés."
        raise LinkRefusedError(msg)
    if not url.host:
        msg = "Ce lien n'a pas de site."
        raise LinkRefusedError(msg)
    if url.userinfo:
        msg = "Un lien avec identifiants n'est pas accepté."
        raise LinkRefusedError(msg)
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    if port not in PORTS:
        msg = "Seuls les ports web habituels (80 et 443) sont acceptés."
        raise LinkRefusedError(msg)
    return url.copy_with(fragment=None)


# ------------------------------------------------------------------ page


class _PreviewParser(HTMLParser):
    """Les balises meta d'aperçu (og:image…) et le titre d'une page ; rien n'est exécuté."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        name = (values.get("property") or values.get("name") or "").strip().lower()
        content = values.get("content", "").strip()
        if name in PREVIEW_META and content and name not in self.meta:
            self.meta[name] = content


def preview_image(html: str) -> str | None:
    parser = _PreviewParser()
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:  # HTML trop abîmé : ce qui a été lu suffit
        pass
    return next((parser.meta[n] for n in PREVIEW_META if n in parser.meta), None)


def image_type_of(head: bytes) -> str | None:
    for kind, magics in IMAGE_MAGIC.items():
        if head.startswith(magics) and (kind != "image/webp" or head[8:12] == b"WEBP"):
            return kind
    return None


def _looks_like_html(content_type: str, head: bytes) -> bool:
    if content_type.split(";", 1)[0].strip().lower() in ("text/html", "application/xhtml+xml"):
        return True
    start = head[:512].lstrip().lower()
    return start.startswith((b"<!doctype html", b"<html", b"<head", b"<meta"))


# ------------------------------------------------------------------ récupération


@dataclass
class _Body:
    url: httpx.URL
    kind: str
    """« image » ou « page »."""
    media_type: str
    data: bytes


class LinkFetcher:
    """Une récupération : un pool httpcore neuf (aucun état gardé d'un appel à l'autre), le
    backend gardé, une échéance pour toute la chaîne."""

    def __init__(
        self,
        resolve: Resolver = system_resolve,
        *,
        inner: httpcore.NetworkBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.backend = GuardedBackend(resolve, inner)
        self.clock = clock
        self.ssl_context = ssl_context

    def fetch(self, raw: str) -> FetchedImage:
        deadline = self.clock() + TOTAL_S
        with httpcore.ConnectionPool(
            ssl_context=self.ssl_context,
            network_backend=self.backend,
            max_connections=2,
            retries=0,
            http1=True,
            http2=False,
        ) as pool:
            first = self._follow(pool, checked_url(raw), deadline)
            if first.kind == "image":
                return FetchedImage(first.data, first.media_type, str(first.url), None)
            found = preview_image(first.data.decode("utf-8", errors="replace"))
            if not found:
                msg = "Cette page ne propose pas d'image : copiez l'image elle-même ou son adresse."
                raise LinkNoImageError(msg)
            try:
                target = checked_url(str(first.url.join(found)))
            except LinkRefusedError as exc:
                msg = "L'image d'aperçu de cette page n'a pas une adresse acceptée."
                raise LinkNoImageError(msg) from exc
            if first.url.scheme == "https" and target.scheme == "http":
                msg = "L'image d'aperçu de cette page n'est pas sécurisée (http)."
                raise LinkNoImageError(msg)
            image = self._follow(pool, target, deadline, images_only=True)
            return FetchedImage(image.data, image.media_type, str(image.url), str(first.url))

    def _follow(
        self,
        pool: httpcore.ConnectionPool,
        url: httpx.URL,
        deadline: float,
        *,
        images_only: bool = False,
    ) -> _Body:
        for _hop in range(MAX_REDIRECTS + 1):
            location = self._get(pool, url, deadline, images_only=images_only)
            if isinstance(location, _Body):
                return location
            nxt = checked_url(str(url.join(location)))
            if url.scheme == "https" and nxt.scheme == "http":
                msg = "Le site redirige vers une adresse non sécurisée (http) : refusé."
                raise LinkRefusedError(msg)
            url = nxt
        msg = f"Trop de redirections (plus de {MAX_REDIRECTS})."
        raise LinkRefusedError(msg)

    def _left(self, deadline: float) -> float:
        left = deadline - self.clock()
        if left <= 0:
            msg = "Le site a mis trop de temps à répondre."
            raise LinkTooSlowError(msg)
        return left

    def _get(
        self,
        pool: httpcore.ConnectionPool,
        url: httpx.URL,
        deadline: float,
        *,
        images_only: bool,
    ) -> _Body | str:
        """Une requête GET ; rend le corps (image ou page) ou l'adresse d'une redirection."""
        left = self._left(deadline)
        timeout = {
            "connect": min(CONNECT_S, left),
            "read": min(READ_S, left),
            "write": min(READ_S, left),
            "pool": min(CONNECT_S, left),
        }
        accept = "image/*" if images_only else "image/*, text/html;q=0.8"
        headers = [
            (b"Host", url.netloc),
            (b"User-Agent", USER_AGENT.encode()),
            (b"Accept", accept.encode()),
            (b"Accept-Encoding", b"identity"),
        ]
        try:
            with pool.stream(
                b"GET", str(url).encode(), headers=headers, extensions={"timeout": timeout}
            ) as response:
                return self._read(response, url, deadline, images_only=images_only)
        except LinkError:
            raise
        except httpcore.TimeoutException as exc:
            msg = "Le site a mis trop de temps à répondre."
            raise LinkTooSlowError(msg) from exc
        except (httpcore.NetworkError, httpcore.ProtocolError, ssl.SSLError, OSError) as exc:
            msg = "Le site n'a pas pu être joint."
            raise LinkUnreachableError(msg) from exc

    def _read(
        self, response: httpcore.Response, url: httpx.URL, deadline: float, *, images_only: bool
    ) -> _Body | str:
        heads = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in response.headers}
        if response.status in _REDIRECTS:
            location = heads.get("location", "").strip()
            if not location:
                msg = "Le site a rendu une redirection sans adresse."
                raise LinkUnreachableError(msg)
            return location
        if response.status != 200:
            msg = f"Le site a répondu {response.status} : rien à récupérer."
            raise LinkUnreachableError(msg)
        try:
            declared = int(heads.get("content-length", "-1"))
        except ValueError:
            declared = -1
        content_type = heads.get("content-type", "")
        if content_type.lower().startswith("image/") and declared > MAX_IMAGE_BYTES:
            raise LinkTooBigError(_too_big())
        data = bytearray()
        kind = ""
        media_type = ""
        limit = MAX_IMAGE_BYTES
        for chunk in response.iter_stream():
            data.extend(chunk)
            self._left(deadline)
            if not kind and len(data) >= 16:
                kind, media_type, limit = self._kind(data, content_type, images_only=images_only)
            if len(data) > limit:
                if kind == "page":
                    del data[limit:]
                    break
                raise LinkTooBigError(_too_big())
        if not kind:
            kind, media_type, limit = self._kind(data, content_type, images_only=images_only)
        return _Body(url, kind, media_type, bytes(data))

    @staticmethod
    def _kind(data: bytearray, content_type: str, *, images_only: bool) -> tuple[str, str, int]:
        media_type = image_type_of(bytes(data[:16]))
        if media_type is not None:
            return "image", media_type, MAX_IMAGE_BYTES
        if images_only:
            msg = "L'image d'aperçu de cette page n'est pas une image lisible."
            raise LinkNoImageError(msg)
        if _looks_like_html(content_type, bytes(data[:512])):
            return "page", "text/html", MAX_PAGE_BYTES
        msg = "Ce lien ne mène ni à une image (JPEG, PNG ou WebP) ni à une page web."
        raise LinkRefusedError(msg)


def _too_big() -> str:
    return f"Image trop lourde : {MAX_IMAGE_BYTES // (1024 * 1024)} Mo au plus."


def fetch_link_image(raw: str) -> FetchedImage:
    return LinkFetcher().fetch(raw)
