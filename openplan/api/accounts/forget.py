"""`openplan accounts forget <adresse>` (KAN-114) : le droit à l'effacement d'un compte.

Ce module ne fait pour l'instant que le PLAN, en lecture seule (`--dry-run`, par défaut) :
il compte et liste ce qui partirait, sans rien écrire, ni dans la base ni sur le disque.
L'effacement lui-même (`--apply`) attend la validation d'augus.

Ce qui partirait (décisions du PO, 30 oct. 2026) :
- le compte, ses sessions, ses codes de connexion (par adresse : sans clé étrangère) ;
- ses jetons du module navigateur (KAN-102) ;
- ses événements d'usage (pseudonymes, `EventLog.forget_account`) ;
- les invitations qu'il a créées, et celles adressées à son adresse ;
- les liens publics de rendus qu'il a créés (KAN-101) ;
- ses rôles de membre dans les projets des autres ;
- ses projets dont il est le seul membre, avec leurs fichiers.

Un projet qu'il possède et que d'autres partagent : refusé tant que l'administrateur n'a pas
choisi `--transfer-to <adresse d'un membre éditeur>` (rôle et `projects.owner_id` transférés)
ou `--delete-shared`. S'il a un autre propriétaire, ce dernier devient le propriétaire inscrit.

Les fichiers des projets des autres ne sont PAS réécrits : commentaires, réactions et activité
gardent un identifiant sans adresse ni nom, que l'application montre « Ancien membre ».
KAN-115 : ses rendus réalistes dans les projets qui restent sont gardés (lanceur effacé par
`SET NULL`, montrés « Ancien membre ») ; ses demandes encore en attente sont annulées
(`cancel_pending_renders`, à appeler avant la suppression de la ligne `users`).
Hors de la commande : le journal d'accès de l'API (rotation par Infra) et les sauvegardes.

Refus : le dernier administrateur, et le compte que désigne OPENPLAN_ADMIN_EMAIL (il serait
recréé administrateur à sa prochaine connexion). Aucune autre adresse que celles tapées n'est
jamais affichée.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from openplan.api.accounts.events import SECRET_FILE
from openplan.api.accounts.models import (
    AuthSession,
    Base,
    Event,
    ExtensionToken,
    Invitation,
    LoginToken,
    Member,
    ProjectRow,
    RenderRequest,
    ShareLink,
    User,
)
from openplan.api.accounts.repo import AccountsRepo, normalize_email
from openplan.schemas.numbers import decimal


class ForgetRefusedError(RuntimeError):
    """Adresse vide, ou choix contradictoires (`--transfer-to` et `--delete-shared`)."""


@dataclass(frozen=True)
class OwnedProject:
    id: UUID
    name: str
    others: int
    """Autres membres que le compte effacé."""
    size: int
    """Octets du dossier du projet sur le disque."""
    co_owner: UUID | None = None
    """Un autre propriétaire, qui deviendrait le propriétaire inscrit."""


@dataclass
class ForgetPlan:
    email: str
    user_id: UUID | None = None
    is_admin: bool = False
    sessions: int = 0
    extension_tokens: int = 0
    """KAN-102 : jetons du module navigateur (partent avec le compte)."""
    login_codes: int = 0
    events: int | None = 0
    """None : la clé des pseudonymes n'existe pas encore (aucun événement n'a été écrit)."""
    invitations_created: int = 0
    invitations_to_address: int = 0
    share_links: int = 0
    """KAN-101 : les liens publics de rendus qu'il a créés (actifs ou non) : ils tombent avec
    le compte (clé étrangère en cascade)."""
    memberships: list[tuple[UUID, str, str]] = field(default_factory=list)
    """Rôles dans les projets des autres : (projet, nom, rôle)."""
    solo: list[OwnedProject] = field(default_factory=list)
    """Ses projets dont il est le seul membre : supprimés avec leurs fichiers."""
    co_owned: list[OwnedProject] = field(default_factory=list)
    shared: list[OwnedProject] = field(default_factory=list)
    """Ses projets que d'autres partagent, sans autre propriétaire : le choix de l'admin."""
    shared_action: str = ""
    """« transfer », « delete » ou vide (refus)."""
    transfer_to: UUID | None = None
    renders: int = 0
    """KAN-99 : ses demandes de rendu réaliste."""
    renders_kept_projects: int = 0
    """KAN-115 : parmi elles, celles des projets qui restent : GARDÉES (clé étrangère en
    `SET NULL`), montrées à tous les membres avec l'auteur « Ancien membre » ; personne n'en
    est plus le lanceur. Les autres partent avec ses projets supprimés."""
    renders_pending: int = 0
    """KAN-115 : parmi elles, celles encore en attente : annulées à l'effacement
    (`cancel_pending_renders`) ; celles déjà prises par le poste suivent leur sort habituel."""
    refusals: list[str] = field(default_factory=list)

    @property
    def possible(self) -> bool:
        return not self.refusals


def _size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def _events(repo: AccountsRepo, data_dir: Path, user_id: UUID) -> int | None:
    """Les événements du compte, par son pseudonyme. La clé est LUE, jamais créée : un plan
    n'écrit rien (`events.load_secret` la créerait)."""
    path = data_dir / SECRET_FILE
    if not path.is_file():
        return None
    secret = path.read_text(encoding="ascii").strip().encode()
    actor = hmac.new(secret, f"u:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]
    with repo.session() as db:
        return int(
            db.scalar(select(func.count()).select_from(Event).where(Event.actor == actor)) or 0
        )


def _count(db: Session, model: type[Base], *where: ColumnElement[bool]) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _owned(
    db: Session, plan: ForgetPlan, user_id: UUID, projects_dir: Path, target: User | None
) -> None:
    """Ses rôles dans les projets des autres, et ses projets classés : seul membre, avec un
    autre propriétaire, ou partagés (le choix de l'admin)."""
    owned_ids = set(db.scalars(select(ProjectRow.id).where(ProjectRow.owner_id == user_id)))
    rows = db.execute(
        select(Member.project_id, Member.role, ProjectRow.name)
        .join(ProjectRow, ProjectRow.id == Member.project_id)
        .where(Member.user_id == user_id)
    ).all()
    for pid, role, name in rows:
        if role == "owner":
            owned_ids.add(pid)
        elif pid not in owned_ids:
            plan.memberships.append((pid, name, role))
    for pid in sorted(owned_ids, key=str):
        members = db.execute(select(Member.user_id, Member.role).where(Member.project_id == pid))
        others = [(uid, r) for uid, r in members.all() if uid != user_id]
        name = db.scalar(select(ProjectRow.name).where(ProjectRow.id == pid)) or ""
        co = next((uid for uid, r in others if r == "owner"), None)
        project = OwnedProject(pid, name, len(others), _size(projects_dir / str(pid)), co)
        if not others:
            plan.solo.append(project)
        elif co is not None:
            plan.co_owned.append(project)
        else:
            plan.shared.append(project)
            if target is not None and (target.id, "editor") not in others:
                plan.refusals.append(
                    f"--transfer-to : pas membre éditeur de « {name} » ({str(pid)[:8]})"
                )


def _pending(user_id: UUID) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """KAN-115 : ses demandes de rendu encore EN ATTENTE (pas celles déjà prises par le
    poste, qui suivent le sort habituel : faites, perdues ou en échec)."""
    return RenderRequest.user_id == user_id, RenderRequest.state == "pending"


def cancel_pending_renders(db: Session, user_id: UUID, now: datetime) -> int:
    """KAN-115 (décision du PO, 3 nov.) : l'effacement du compte ANNULE ses demandes en
    attente, dans la même transaction, AVANT de supprimer la ligne `users` : sans lanceur,
    aucun poste ne les prendrait plus (`RenderQueue.claim` ne prend que les comptes
    autorisés). Annulée = sortie de la file comme une demande expirée (`expired`, état final
    déjà connu de la galerie). Renvoie leur nombre. Chemin d'`accounts forget --apply`,
    qui attend la validation d'augus."""
    rows = list(db.scalars(select(RenderRequest).where(*_pending(user_id))))
    for row in rows:
        row.state, row.finished_at, row.lease_id = "expired", now, None
    return len(rows)


def _renders(repo: AccountsRepo, plan: ForgetPlan, user_id: UUID) -> None:
    """KAN-99 : ses rendus réalistes ; KAN-115 : ceux des projets qui restent (tous sauf ses
    projets supprimés : seul membre, ou partagés avec `--delete-shared`) sont gardés ; ses
    demandes en attente sont annulées (`cancel_pending_renders`)."""
    gone = {p.id for p in plan.solo}
    if plan.shared_action == "delete":
        gone |= {p.id for p in plan.shared}
    with repo.session() as db:
        projects = list(
            db.scalars(select(RenderRequest.project_id).where(RenderRequest.user_id == user_id))
        )
        plan.renders_pending = _count(db, RenderRequest, *_pending(user_id))
    plan.renders = len(projects)
    plan.renders_kept_projects = sum(1 for pid in projects if pid not in gone)


def plan_forget(
    repo: AccountsRepo,
    email: str,
    *,
    projects_dir: Path,
    data_dir: Path,
    admin_email: str | None = None,
    transfer_to: str | None = None,
    delete_shared: bool = False,
) -> ForgetPlan:
    """Ce que l'effacement de ce compte ferait ; lecture seule."""
    address = normalize_email(email)
    if not address:
        msg = "adresse vide"
        raise ForgetRefusedError(msg)
    if transfer_to and delete_shared:
        msg = "--transfer-to et --delete-shared s'excluent : choisir l'un ou l'autre"
        raise ForgetRefusedError(msg)
    plan = ForgetPlan(email=address)
    with repo.session() as db:
        plan.login_codes = _count(db, LoginToken, LoginToken.email == address)
        plan.invitations_to_address = _count(db, Invitation, Invitation.email == address)
        user = db.scalar(select(User).where(User.email == address))
        if user is None:
            return plan
        plan.user_id, plan.is_admin = user.id, user.is_admin
        plan.sessions = _count(db, AuthSession, AuthSession.user_id == user.id)
        plan.extension_tokens = _count(db, ExtensionToken, ExtensionToken.user_id == user.id)
        plan.invitations_created = _count(
            db,
            Invitation,
            Invitation.created_by == user.id,
            or_(Invitation.email.is_(None), Invitation.email != address),
        )
        plan.share_links = _count(db, ShareLink, ShareLink.created_by == user.id)
        admins = _count(db, User, User.is_admin.is_(True))
        target = None
        if transfer_to:
            target = db.scalar(select(User).where(User.email == normalize_email(transfer_to)))
            if target is None or target.id == user.id:
                target = None
                plan.refusals.append("--transfer-to : aucun autre compte pour cette adresse")
        _owned(db, plan, user.id, projects_dir, target)
    plan.events = _events(repo, data_dir, user.id)
    if plan.shared:
        if target is not None:
            plan.shared_action, plan.transfer_to = "transfer", target.id
        elif delete_shared:
            plan.shared_action = "delete"
        elif not transfer_to:
            plan.refusals.append(
                f"{len(plan.shared)} projet(s) partagé(s) dont il est propriétaire : choisir "
                "--transfer-to <adresse d'un membre éditeur> ou --delete-shared"
            )
    _renders(repo, plan, user.id)
    if plan.is_admin and admins <= 1:
        plan.refusals.append("dernier administrateur : en nommer un autre d'abord")
    if admin_email and normalize_email(admin_email) == address:
        plan.refusals.append(
            "compte désigné par OPENPLAN_ADMIN_EMAIL : il serait recréé administrateur à sa "
            "prochaine connexion (changer ce réglage d'abord)"
        )
    return plan


def _mo(n: int) -> str:
    return f"{decimal(n / 1_000_000, 1)} Mo"


def _line(p: OwnedProject) -> str:
    return f"    {str(p.id)[:8]}  « {p.name} » · {p.others} autre(s) membre(s) · {_mo(p.size)}"


def _describe_projects(plan: ForgetPlan, say: Callable[[str], None]) -> None:
    """Ses projets : seul membre, avec un autre propriétaire, partagés."""
    if plan.solo:
        total = sum(p.size for p in plan.solo)
        say(f"  ses projets, seul membre, supprimés avec leurs fichiers ({_mo(total)}) :")
        for p in plan.solo:
            say(_line(p))
    if plan.co_owned:
        say("  ses projets avec un autre propriétaire (qui devient le propriétaire inscrit) :")
        for p in plan.co_owned:
            say(_line(p))
    if plan.shared:
        what = {
            "transfer": "transférés au membre éditeur donné (rôle et propriétaire inscrit)",
            "delete": "SUPPRIMÉS avec leurs fichiers (--delete-shared)",
        }.get(plan.shared_action, "en attente d'un choix")
        say(f"  ses projets partagés, {what} :")
        for p in plan.shared:
            say(_line(p))


def describe(plan: ForgetPlan, say: Callable[[str], None] = print) -> None:
    """Le plan, lisible ; aucune autre adresse que celle tapée."""
    say(f"Effacement de {plan.email} : simulation (--dry-run), rien n'est écrit.")
    if plan.user_id is None:
        say("  aucun compte pour cette adresse.")
    else:
        role = "administrateur" if plan.is_admin else "compte"
        say(f"  {role} {str(plan.user_id)[:8]} : supprimé, avec {plan.sessions} session(s)")
        say(f"  jetons de l'extension navigateur supprimés : {plan.extension_tokens}")
        events = "inconnu (clé absente)" if plan.events is None else str(plan.events)
        say(f"  événements d'usage supprimés : {events}")
    say(f"  codes de connexion supprimés : {plan.login_codes}")
    say(
        f"  invitations supprimées : {plan.invitations_created} créée(s) par lui, "
        f"{plan.invitations_to_address} adressée(s) à cette adresse"
    )
    if plan.user_id is not None:
        say(f"  liens publics de rendus supprimés : {plan.share_links}")
    if plan.memberships:
        say(f"  rôles retirés dans les projets des autres : {len(plan.memberships)}")
        for pid, name, role in plan.memberships:
            say(f"    {str(pid)[:8]}  « {name} » ({role})")
    _describe_projects(plan, say)
    if plan.renders:
        say(
            f"  rendus réalistes qu'il a lancés : {plan.renders} ; gardés dans les projets qui "
            f"restent : {plan.renders_kept_projects} (montrés « Ancien membre », sans lanceur) ; "
            "les autres partent avec ses projets supprimés"
        )
        say(f"  demandes en attente annulées : {plan.renders_pending}")
    say(
        "  Inchangés : commentaires, réactions et activité dans les projets des autres "
        "(montrés « Ancien membre », sans adresse ni nom)."
    )
    for refusal in plan.refusals:
        say(f"  REFUS : {refusal}")
    say("Possible tel quel." if plan.possible else "Impossible tel quel (voir les refus).")
