"""KAN-132 : « Exporter mes données » (Réglages › Compte), décisions d'augus du 6 nov.

Une archive ZIP préparée en tâche de fond, un lien envoyé par e-mail, valable 24 heures et à
usage unique (il faut de plus être connecté au même compte). Elle contient :

- `compte.txt` : un fichier lisible qui décrit le compte (adresse, nom, dates) et la liste de
  ses projets avec son rôle ;
- `projets/` : l'archive de chaque projet dont il est propriétaire (photos, rendus, plans en
  JSON, comme « Exporter le projet ») ;
- `contributions/` : SES messages et SES réactions dans les projets des autres, rien d'autre ;
- `evenements-d-usage.csv` : ses événements d'usage (pseudonymes, KAN-109).

Jamais une donnée d'un autre membre (ni nom, ni adresse, ni message).

Les archives se préparent dans `OPENPLAN_EXPORTS_DIR` (défaut `<data>/exports`), un fichier
de fiche JSON à côté (empreinte du jeton, compte, état, expiration) : pas de table, pas de
migration. Une seule demande en cours par compte, une taille maximale (`MAX_BYTES`). La purge
quotidienne (KAN-142) efface les archives expirées. Ce dossier est à EXCLURE des sauvegardes
(copies de données personnelles), voir le rapport de KAN-132.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import secrets
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from openplan.api.accounts.db import aware
from openplan.api.accounts.forget import _actor
from openplan.api.accounts.models import Event, Member, ProjectRow, User
from openplan.api.accounts.repo import AccountsRepo
from openplan.pipeline.board import project_archive

log = logging.getLogger("openplan.accounts")

LINK_TTL = timedelta(hours=24)
"""Décision du PO (6 nov.) : le lien d'export vaut 24 heures (à écrire dans la page)."""
PREPARING_STALE = timedelta(hours=6)
"""Une préparation interrompue (redémarrage) ne bloque pas le compte plus longtemps."""
MAX_BYTES = 1024**3
"""1 Gio au plus, avant compression : au-delà, l'export est refusé (exporter projet par projet)."""
CONTRIBUTION_FILES = ("comments.json", "reactions.json")


class ExportBusyError(Exception):
    """Une demande en cours, ou un lien encore valable, pour ce compte."""


@dataclass
class ExportCard:
    """La fiche d'une archive : jamais le jeton, seulement son empreinte."""

    id: str
    user_id: str
    token_hash: str
    created_at: str
    expires_at: str
    state: str
    """`preparing`, `ready`, `failed`."""
    size: int = 0

    @property
    def expires(self) -> datetime:
        return datetime.fromisoformat(self.expires_at)

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _card_path(root: Path, export_id: str) -> Path:
    return root / f"{export_id}.json"


def zip_path(root: Path, export_id: str) -> Path:
    return root / f"{export_id}.zip"


def _write_card(root: Path, card: ExportCard) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{card.id}.json.tmp"
    tmp.write_text(json.dumps(asdict(card)), encoding="utf-8")
    tmp.replace(_card_path(root, card.id))


def cards(root: Path) -> Iterator[ExportCard]:
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        try:
            yield ExportCard(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue


def _live(card: ExportCard, now: datetime) -> bool:
    """La fiche bloque-t-elle une nouvelle demande ?"""
    if card.state == "preparing":
        return now - card.created < PREPARING_STALE
    return card.state == "ready" and card.expires > now


def open_export(root: Path, user_id: UUID, now: datetime) -> tuple[ExportCard, str]:
    """Une demande : la fiche (état `preparing`) et le jeton du lien, à mettre dans l'e-mail
    seulement. Refus si une demande est en cours ou un lien encore valable."""
    if any(c.user_id == str(user_id) and _live(c, now) for c in cards(root)):
        raise ExportBusyError
    token = secrets.token_urlsafe(32)
    card = ExportCard(
        id=uuid4().hex,
        user_id=str(user_id),
        token_hash=_hash(token),
        created_at=now.isoformat(),
        expires_at=(now + LINK_TTL).isoformat(),
        state="preparing",
    )
    _write_card(root, card)
    return card, token


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def _slug(name: str) -> str:
    kept = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return "-".join(part for part in kept.split("-") if part)[:40] or "projet"


def _iso(value: datetime | None) -> str:
    return aware(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def _mine(node: Any, me: str) -> list[dict[str, Any]]:
    """Les objets d'un fichier de contributions dont l'auteur est le compte : ses messages,
    ses réactions ; rien d'un autre membre."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("author") == me:
            found.append({k: v for k, v in node.items() if not isinstance(v, (dict, list))})
        for value in node.values():
            found.extend(_mine(value, me))
    elif isinstance(node, list):
        for value in node:
            found.extend(_mine(value, me))
    return found


@dataclass(frozen=True)
class _Project:
    id: UUID
    name: str
    role: str
    created_at: datetime
    owner: bool


def _projects(repo: AccountsRepo, user_id: UUID) -> list[_Project]:
    with repo.session() as db:
        rows = db.execute(
            select(ProjectRow.id, ProjectRow.name, ProjectRow.created_at, Member.role)
            .join(Member, Member.project_id == ProjectRow.id)
            .where(Member.user_id == user_id)
            .order_by(ProjectRow.created_at)
        ).all()
    return [_Project(r.id, r.name, r.role, r.created_at, r.role == "owner") for r in rows]


def _account_text(user: User, projects: list[_Project], now: datetime) -> str:
    password = f"posé le {_iso(user.password_set_at)}" if user.password_hash else "aucun"
    lines = [
        "Vos données PlanDesign",
        f"Archive préparée le {_iso(now)}.",
        "",
        "Compte",
        f"  Adresse : {user.email}",
        f"  Nom affiché : {user.name or '—'}",
        f"  Créé le : {_iso(user.created_at)}",
        f"  Dernière visite : {_iso(user.last_seen_at)}",
        f"  Mot de passe : {password}",
        f"  Conditions acceptées : {_iso(user.accepted_terms_at)}"
        + (f" (version {user.terms_version})" if user.terms_version else ""),
        "",
        f"Projets ({len(projects)})",
    ]
    roles = {"owner": "propriétaire", "editor": "éditeur", "commenter": "commentateur"}
    for p in projects:
        role = roles.get(p.role, "lecteur")
        lines.append(f"  « {p.name or 'Sans nom'} » : {role}, créé le {_iso(p.created_at)}")
    lines += [
        "",
        "Contenu de l'archive",
        "  projets/ : l'archive complète de chaque projet dont vous êtes propriétaire.",
        "  contributions/ : vos messages et vos réactions dans les projets des autres.",
        "  evenements-d-usage.csv : la mesure de l'usage liée à votre compte (pseudonyme).",
        "",
    ]
    return "\n".join(lines)


def _events_csv(repo: AccountsRepo, data_dir: Path, user_id: UUID) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "événement", "projet (pseudonyme)", "valeur"])
    actor = _actor(data_dir, user_id)
    if actor is not None:
        with repo.session() as db:
            for row in db.scalars(select(Event).where(Event.actor == actor).order_by(Event.at)):
                writer.writerow([_iso(row.at), row.name, row.project or "", row.value or ""])
    return out.getvalue()


def build_export(
    repo: AccountsRepo,
    root: Path,
    card: ExportCard,
    *,
    project_dir: Callable[[UUID], Path],
    data_dir: Path,
    clock: Callable[[], datetime],
) -> ExportCard:
    """La tâche de fond : l'archive, puis la fiche passe à `ready` (ou `failed` si trop
    grande, ou en cas d'erreur). Écrit d'abord un `.tmp`, renommé une fois complet."""
    user_id = UUID(card.user_id)
    try:
        with repo.session() as db:
            user = db.get(User, user_id)
        if user is None:
            raise LookupError("compte parti")
        projects = _projects(repo, user_id)
        owned = [p for p in projects if p.owner]
        if sum(_dir_size(project_dir(p.id)) for p in owned) > MAX_BYTES:
            card.state = "failed"
            _write_card(root, card)
            return card
        tmp = root / f"{card.id}.zip.tmp"
        me = str(user_id)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("compte.txt", _account_text(user, projects, clock()))
            for p in owned:
                name = f"projets/{_slug(p.name)}-{str(p.id)[:8]}.zip"
                archive.writestr(name, project_archive(project_dir(p.id)))
            for p in projects:
                if p.owner:
                    continue
                mine: dict[str, list[dict[str, Any]]] = {}
                for filename in CONTRIBUTION_FILES:
                    path = project_dir(p.id) / filename
                    if path.is_file():
                        mine[filename.removesuffix(".json")] = _mine(
                            json.loads(path.read_text(encoding="utf-8")), me
                        )
                if any(mine.values()):
                    doc = {"projet": p.name, **mine}
                    archive.writestr(
                        f"contributions/{_slug(p.name)}-{str(p.id)[:8]}.json",
                        json.dumps(doc, ensure_ascii=False, indent=2),
                    )
            archive.writestr("evenements-d-usage.csv", _events_csv(repo, data_dir, user_id))
        tmp.replace(zip_path(root, card.id))
        card.state, card.size = "ready", zip_path(root, card.id).stat().st_size
    except Exception:
        log.exception("export des données d'un compte en échec (%s)", card.user_id[:8])
        (root / f"{card.id}.zip.tmp").unlink(missing_ok=True)
        card.state = "failed"
    _write_card(root, card)
    return card


def take_export(root: Path, user_id: UUID, token: str, now: datetime) -> Path | None:
    """Le lien suivi : l'archive de CE compte, prête, non expirée, jamais téléchargée. La fiche
    est retirée avant de répondre (usage unique) ; l'archive part après l'envoi."""
    digest = _hash(token)
    for card in cards(root):
        if card.user_id != str(user_id) or not hmac.compare_digest(card.token_hash, digest):
            continue
        if card.state != "ready" or card.expires <= now:
            return None
        path = zip_path(root, card.id)
        _card_path(root, card.id).unlink(missing_ok=True)
        return path if path.is_file() else None
    return None


def purge_exports(root: Path, now: datetime) -> int:
    """La purge quotidienne (KAN-142) : fiches et archives expirées, échouées ou orphelines.
    Renvoie le nombre d'archives (ou de fiches) effacées."""
    if not root.is_dir():
        return 0
    removed = 0
    kept: set[str] = set()
    for card in cards(root):
        stale = card.state == "preparing" and now - card.created >= PREPARING_STALE
        if card.expires <= now or card.state == "failed" or stale:
            zip_path(root, card.id).unlink(missing_ok=True)
            _card_path(root, card.id).unlink(missing_ok=True)
            removed += 1
        else:
            kept.add(card.id)
    for path in root.iterdir():
        stem = path.name.split(".", 1)[0]
        if path.is_file() and stem not in kept and path.suffix in (".zip", ".tmp"):
            path.unlink(missing_ok=True)
            removed += 1
    return removed
