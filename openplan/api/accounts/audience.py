"""KAN-109 L1 : audience du site public, tirée du journal d'accès que Caddy écrit pour
plandesign.fr (et www), monté en lecture seule (`OPENPLAN_ACCESS_LOG_DIR`).

Aucune donnée personnelle n'est gardée :
- l'IP arrive déjà tronquée par Caddy (/24 en IPv4, /48 en IPv6) et n'est jamais écrite ;
- les visiteurs uniques d'un jour se comptent par une empreinte (sel aléatoire du jour, IP
  tronquée, User-Agent), effacée avec le sel sous 48 h ;
- on garde par jour : les vues, les visiteurs, les pages (chemin sans la requête), les sources
  (domaine du référent ou `utm_source`), la classe d'appareil ; 25 mois, puis purge.

Une vue : un GET d'une page HTML de plandesign.fr, statut < 400, hors robots et hors
ressources ; www (redirection 301) compte comme « arrivée par www », jamais comme une vue.

Lecture : le fichier courant par inode et position (la rotation le remplace), chaque fichier
tourné (.gz) une seule fois ; l'état de lecture est en base, un redémarrage reprend où il en
était. Un dossier vide ou un fichier absent (Caddy ne le crée qu'à la première requête) n'est
pas une erreur : « aucune donnée »."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.models import (
    AccessLogCursor,
    SiteDay,
    SiteDayPage,
    SiteDaySource,
    SiteSalt,
    SiteVisitor,
)

log = logging.getLogger("openplan.audience")

SITE_HOST = "plandesign.fr"
WWW_HOST = "www.plandesign.fr"
CURRENT = "site.log"
"""Le fichier courant ; les tournés s'appellent `site-<horodatage>.log.gz`."""
INTERVAL_S = 300
KEEP_DAYS = 760
"""25 mois d'agrégats, puis purge."""
KEEP_DIGESTS = timedelta(hours=48)
MAX_PATH = 300
MAX_SOURCE = 200

ASSET = re.compile(
    r"^/(?:_astro|assets|fonts|images|img|favicons?)/"
    r"|\.(?:js|mjs|css|map|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf|xml|txt|json|"
    r"webmanifest|pdf|mp4|webm)$",
    re.IGNORECASE,
)
ROBOT = re.compile(
    r"bot|crawl|spider|slurp|preview|monitor|uptime|health|curl|wget|python|httpclient|"
    r"headless|lighthouse|facebookexternalhit|embedly|whatsapp|go-http|java/|okhttp|axios|"
    r"node-fetch|scrapy|petal|semrush|ahrefs|mj12|yandex|baidu|claude|perplexity|bytespider|"
    r"gatus|pingdom|statuscake|validator|feedfetcher|scan",
    re.IGNORECASE,
)
TABLET = re.compile(r"ipad|tablet|kindle|silk|playbook|android(?!.*mobile)", re.IGNORECASE)
MOBILE = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini|iemobile", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    """Une requête lue dans le journal (rien n'en est gardé tel quel)."""

    day: str
    host: str
    method: str
    path: str
    status: int
    user_agent: str
    referer: str
    utm: str
    client: str
    """IP déjà tronquée par Caddy : sert seulement à l'empreinte du jour."""


def _first(headers: object, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            if isinstance(value, list) and value:
                return str(value[0])
            return str(value)
    return ""


def _day(ts: object) -> str | None:
    try:
        if isinstance(ts, int | float):
            moment = datetime.fromtimestamp(float(ts), UTC)
        else:
            moment = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return moment.date().isoformat()


def parse(line: str) -> Hit | None:
    """Une ligne JSON du journal de Caddy ; None si elle est illisible."""
    try:
        entry = json.loads(line)
        request = entry["request"]
        day = _day(entry.get("ts"))
        if day is None:
            return None
        uri = urlsplit(str(request.get("uri", "/")))
        utm = parse_qs(uri.query).get("utm_source", [""])[0]
        headers = request.get("headers", {})
        return Hit(
            day=day,
            host=str(request.get("host", "")).lower().split(":")[0],
            method=str(request.get("method", "")).upper(),
            path=uri.path or "/",
            status=int(entry.get("status", 0)),
            user_agent=_first(headers, "User-Agent"),
            referer=_first(headers, "Referer"),
            utm=utm,
            client=str(request.get("client_ip") or request.get("remote_ip") or ""),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def kind_of(hit: Hit) -> str:
    """`view`, `www`, `bot`, `4xx`, `5xx` ou `skip` (autre site, ressource, autre méthode)."""
    if hit.host not in (SITE_HOST, WWW_HOST) or hit.method != "GET":
        return "skip"
    if not hit.user_agent.strip() or ROBOT.search(hit.user_agent):
        return "bot"
    if hit.host == WWW_HOST:
        return "www"
    return _by_status(hit)


def _by_status(hit: Hit) -> str:
    """Une requête d'une personne sur plandesign.fr : erreur, ressource ou vue de page."""
    if hit.status >= 500:
        return "5xx"
    if hit.status >= 400:
        return "4xx"
    if ASSET.search(hit.path) or not 200 <= hit.status < 300:
        return "skip"
    return "view"


def device_of(user_agent: str) -> str:
    if TABLET.search(user_agent):
        return "tablet"
    return "mobile" if MOBILE.search(user_agent) else "desktop"


def source_of(hit: Hit) -> str | None:
    """`utm:<source>` s'il est là, sinon le domaine du référent (jamais son chemin), hors du
    site lui-même ; None : arrivée directe."""
    if hit.utm.strip():
        return f"utm:{hit.utm.strip().lower()[:60]}"
    host = urlsplit(hit.referer).hostname or ""
    host = host.lower().removeprefix("www.")
    if not host or host == SITE_HOST:
        return None
    return host[:MAX_SOURCE]


@dataclass
class _Day:
    views: int = 0
    www: int = 0
    bots: int = 0
    errors_4xx: int = 0
    errors_5xx: int = 0
    devices: Counter[str] = field(default_factory=Counter)
    pages: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    visitors: set[tuple[str, str]] = field(default_factory=set)


def tally(hits: Iterable[Hit]) -> dict[str, _Day]:
    """Les comptes par jour d'un lot de requêtes (pur, testé seul)."""
    days: dict[str, _Day] = {}
    for hit in hits:
        kind = kind_of(hit)
        if kind == "skip":
            continue
        d = days.setdefault(hit.day, _Day())
        if kind == "bot":
            d.bots += 1
        elif kind == "www":
            d.www += 1
        elif kind == "4xx":
            d.errors_4xx += 1
        elif kind == "5xx":
            d.errors_5xx += 1
        else:
            d.views += 1
            d.devices[device_of(hit.user_agent)] += 1
            d.pages[hit.path[:MAX_PATH]] += 1
            source = source_of(hit)
            if source:
                d.sources[source] += 1
            d.visitors.add((hit.client, hit.user_agent))
    return days


def _lines(path: Path, start: int = 0) -> tuple[list[str], int]:
    """Les lignes complètes à partir de `start` (une ligne en cours d'écriture attend le tour
    suivant) et la nouvelle position."""
    with path.open("rb") as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], start
    chunk = data[: end + 1]
    return chunk.decode("utf-8", errors="replace").splitlines(), start + len(chunk)


class SiteAudience:
    """Lecture du journal du site et agrégats en base (un seul lecteur à la fois)."""

    def __init__(
        self,
        engine: Engine,
        log_dir: Path | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.log_dir = log_dir
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def session(self) -> Session:
        return self.sessions()

    # --- lecture du journal ---------------------------------------------------------------

    def _files(self) -> list[Path]:
        """Les tournés (.gz, du plus ancien au plus récent) puis le courant."""
        if self.log_dir is None or not self.log_dir.is_dir():
            return []
        rotated = sorted(p for p in self.log_dir.glob("site-*.log.gz") if p.is_file())
        current = self.log_dir / CURRENT
        return [*rotated, *([current] if current.is_file() else [])]

    def ingest(self) -> int:
        """Lit ce qui est nouveau ; renvoie le nombre de lignes lues."""
        with self._lock, self.session() as db:
            read = 0
            for path in self._files():
                cursor = self._row(
                    db, AccessLogCursor, path.name, name=path.name, inode=0, position=0, done=False
                )
                if cursor.done:
                    continue
                if path.suffix == ".gz":
                    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
                        lines = f.read().splitlines()
                    cursor.done = True
                else:
                    inode = path.stat().st_ino
                    if inode != cursor.inode or path.stat().st_size < cursor.position:
                        # Rotation : un nouveau fichier courant, lu depuis le début (l'ancien
                        # est devenu un .gz, lu à part).
                        cursor.inode, cursor.position = inode, 0
                    lines, cursor.position = _lines(path, cursor.position)
                hits = [h for h in (parse(line) for line in lines) if h is not None]
                if hits and cursor.first_day is None:
                    cursor.first_day = min(h.day for h in hits)
                self._add(db, tally(hits))
                read += len(lines)
            self._forget(db)
            db.commit()
            return read

    def _salt(self, db: Session, day: str) -> str:
        row = db.get(SiteSalt, day)
        if row is None:
            row = SiteSalt(day=day, salt=secrets.token_hex(32))
            db.add(row)
            db.flush()
        return row.salt

    @staticmethod
    def _row[T](db: Session, model: type[T], key: object, **zero: object) -> T:
        """La ligne de cette clé, créée à zéro au besoin (suivie par la session)."""
        row = db.get(model, key)
        if row is None:
            row = model(**zero)
            db.add(row)
        return row

    def _add(self, db: Session, days: dict[str, _Day]) -> None:
        counts = ("views", "visitors", "www", "bots", "errors_4xx", "errors_5xx")
        devices = ("mobile", "tablet", "desktop")
        for day, d in days.items():
            row = self._row(db, SiteDay, day, day=day, **dict.fromkeys(counts + devices, 0))
            row.views += d.views
            row.www += d.www
            row.bots += d.bots
            row.errors_4xx += d.errors_4xx
            row.errors_5xx += d.errors_5xx
            row.mobile += d.devices["mobile"]
            row.tablet += d.devices["tablet"]
            row.desktop += d.devices["desktop"]
            for path, n in d.pages.items():
                page = self._row(db, SiteDayPage, (day, path), day=day, path=path, views=0)
                page.views += n
            for source, n in d.sources.items():
                src = self._row(db, SiteDaySource, (day, source), day=day, source=source, views=0)
                src.views += n
            if d.visitors and self._open(day):
                salt = self._salt(db, day)
                for client, agent in d.visitors:
                    digest = hashlib.sha256(f"{salt}|{client}|{agent}".encode()).hexdigest()
                    if db.get(SiteVisitor, (day, digest)) is None:
                        db.add(SiteVisitor(day=day, digest=digest))
                db.flush()
                row.visitors = db.scalar(select(func.count()).where(SiteVisitor.day == day)) or 0

    def _open(self, day: str) -> bool:
        """Un jour dont on compte encore les visiteurs (48 h au plus)."""
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC)
        return self.clock() - start < KEEP_DIGESTS

    def _forget(self, db: Session) -> None:
        """Efface les empreintes et sels de plus de 48 h, et les agrégats de plus de 25 mois."""
        now = self.clock()
        # Un jour dont le début date de 48 h ou plus : ses empreintes et son sel partent.
        old = (now - KEEP_DIGESTS).date().isoformat()
        db.execute(delete(SiteVisitor).where(SiteVisitor.day <= old))
        db.execute(delete(SiteSalt).where(SiteSalt.day <= old))
        gone = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
        for table in (SiteDay, SiteDayPage, SiteDaySource):
            db.execute(delete(table).where(table.day < gone))

    # --- lecture des agrégats -------------------------------------------------------------

    def summary(self, days: int) -> dict[str, object]:
        """Ce que montre le back-office : séries, pages et sources en tête, appareils."""
        since = (self.clock() - timedelta(days=days - 1)).date().isoformat()
        with self.session() as db:
            rows = list(
                db.scalars(select(SiteDay).where(SiteDay.day >= since).order_by(SiteDay.day))
            )
            pages = db.execute(
                select(SiteDayPage.path, func.sum(SiteDayPage.views))
                .where(SiteDayPage.day >= since)
                .group_by(SiteDayPage.path)
                .order_by(func.sum(SiteDayPage.views).desc(), SiteDayPage.path)
                .limit(15)
            ).all()
            sources = db.execute(
                select(SiteDaySource.source, func.sum(SiteDaySource.views))
                .where(SiteDaySource.day >= since)
                .group_by(SiteDaySource.source)
                .order_by(func.sum(SiteDaySource.views).desc(), SiteDaySource.source)
                .limit(15)
            ).all()
            first = db.scalar(select(func.min(AccessLogCursor.first_day)))
        return {
            "connected": self.log_dir is not None,
            "since": first,
            "days": [
                {
                    "day": r.day,
                    "views": r.views,
                    "visitors": r.visitors,
                    "www": r.www,
                    "bots": r.bots,
                    "errors_4xx": r.errors_4xx,
                    "errors_5xx": r.errors_5xx,
                }
                for r in rows
            ],
            "devices": {
                "mobile": sum(r.mobile for r in rows),
                "tablet": sum(r.tablet for r in rows),
                "desktop": sum(r.desktop for r in rows),
            },
            "pages": [{"path": p, "views": int(n)} for p, n in pages],
            "sources": [{"source": s, "views": int(n)} for s, n in sources],
        }

    # --- tâche de fond ----------------------------------------------------------------------

    def start(self) -> None:
        """Lit le journal toutes les 5 min, dans un fil à part (rien sans dossier)."""
        if self.log_dir is None or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.ingest()
                except Exception:
                    log.exception("audience du site : lecture du journal en échec")
                self._stop.wait(INTERVAL_S)

        self._thread = threading.Thread(target=loop, name="site-audience", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
