"""`openplan accounts forget <adresse>` (KAN-114) : le droit à l'effacement d'un compte.

Le PLAN (`--dry-run`, par défaut) est en lecture seule : il compte et liste ce qui partirait,
sans rien écrire, ni dans la base ni sur le disque. L'effacement (`--apply`, décisions du
30 oct. confirmées par augus) refait le plan et ses refus, demande de retaper l'adresse, puis
agit dans UNE transaction, dans cet ordre : sessions révoquées d'abord, demandes de rendu en
attente annulées (`cancel_pending_renders`), puis le reste, et la ligne `users` en dernier.
Les dossiers des projets supprimés partent après la validation de la transaction. Un second
`--apply` sur la même adresse ne trouve « rien à faire ». La sortie (`--dry-run` comme
`--apply`) masque l'adresse (`a***@example.org`) ; le journal ne garde que les 8 premiers
caractères de l'identifiant du compte, qui ne se relient plus à personne.

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
import logging
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.orm import Session

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.events import SECRET_FILE
from openplan.api.accounts.mail import mask_address
from openplan.api.accounts.models import (
    AuthSession,
    Base,
    Event,
    ExtensionToken,
    Invitation,
    LoginToken,
    Member,
    NotificationRead,
    NotificationReadItem,
    ProjectRow,
    RenderRequest,
    ShareLink,
    User,
)
from openplan.api.accounts.repo import AccountsRepo, normalize_email
from openplan.schemas.numbers import decimal

log = logging.getLogger("openplan.accounts")


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
    """« transfer », « delete », « heirs » (KAN-132 : un héritier par projet) ou vide (refus)."""
    transfer_to: UUID | None = None
    heirs: dict[UUID, UUID] = field(default_factory=dict)
    """KAN-132 : l'héritier de chaque projet partagé (`shared_action == "heirs"`)."""
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


def _actor(data_dir: Path, user_id: UUID) -> str | None:
    """Le pseudonyme des événements du compte (comme `EventLog.pseudonym`). La clé est LUE,
    jamais créée (`events.load_secret` la créerait) ; absente : aucun événement n'existe."""
    path = data_dir / SECRET_FILE
    if not path.is_file():
        return None
    secret = path.read_text(encoding="ascii").strip().encode()
    return hmac.new(secret, f"u:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def _events(repo: AccountsRepo, data_dir: Path, user_id: UUID) -> int | None:
    """Les événements du compte, par son pseudonyme ; un plan n'écrit rien."""
    actor = _actor(data_dir, user_id)
    if actor is None:
        return None
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
    déjà connu de la galerie). Renvoie leur nombre. Appelée par `apply_forget`."""
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


HEIR_ORDER = {"editor": 0, "commenter": 1, "viewer": 2}
"""KAN-132 (décisions d'augus du 6 nov.) : sans choix, l'éditeur le plus ancien ; s'il n'y a
aucun éditeur, le membre le plus ancien, quel que soit son rôle (le PO, même jour)."""


def default_heirs(db: Session, project_ids: list[UUID], user_id: UUID) -> dict[UUID, UUID]:
    """L'héritier par défaut de chaque projet : l'éditeur le plus ancien, sinon le membre le
    plus ancien (date d'ajout `added_at`, puis l'id pour départager)."""
    out: dict[UUID, UUID] = {}
    for pid in project_ids:
        rows = db.execute(
            select(Member.user_id, Member.role, Member.added_at).where(
                Member.project_id == pid, Member.user_id != user_id
            )
        ).all()
        ranked = sorted(rows, key=lambda r: (HEIR_ORDER.get(r.role, 9), r.added_at, str(r.user_id)))
        if ranked:
            out[pid] = ranked[0].user_id
    return out


def _choose_heirs(
    repo: AccountsRepo, plan: ForgetPlan, user_id: UUID, chosen: Mapping[UUID, UUID]
) -> None:
    """Le choix du titulaire, projet par projet ; un héritier qui n'est pas membre du projet
    est refusé (jamais un compte pris au hasard)."""
    shared = [p.id for p in plan.shared]
    with repo.session() as db:
        plan.heirs = default_heirs(db, shared, user_id)
        for pid, heir in chosen.items():
            if pid not in plan.heirs:
                plan.refusals.append(f"projet inconnu ou non partagé : {str(pid)[:8]}")
                continue
            if heir == user_id or not _count(
                db, Member, Member.project_id == pid, Member.user_id == heir
            ):
                plan.refusals.append(f"héritier qui n'est pas membre du projet {str(pid)[:8]}")
                continue
            plan.heirs[pid] = heir
    plan.shared_action = "heirs"


def plan_forget(  # noqa: PLR0912 - les refus et le choix des projets partagés, en un lieu
    repo: AccountsRepo,
    email: str,
    *,
    projects_dir: Path,
    data_dir: Path,
    admin_email: str | None = None,
    transfer_to: str | None = None,
    delete_shared: bool = False,
    heirs: Mapping[UUID, UUID] | None = None,
) -> ForgetPlan:
    """Ce que l'effacement de ce compte ferait ; lecture seule. `heirs` (KAN-132, suppression
    par le titulaire) : l'héritier choisi par projet partagé ; les autres vont à l'éditeur le
    plus ancien, à défaut au membre le plus ancien (`default_heirs`)."""
    if heirs is not None and (transfer_to or delete_shared):
        msg = "heirs exclut --transfer-to et --delete-shared"
        raise ForgetRefusedError(msg)
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
    if plan.shared and heirs is not None:
        _choose_heirs(repo, plan, user.id, heirs)
    elif plan.shared:
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


def describe(plan: ForgetPlan, say: Callable[[str], None] = print, *, apply: bool = False) -> None:
    """Le plan, lisible ; aucune autre adresse que celle tapée, et masquée (`a***@example.org`)
    en `--dry-run` comme en `--apply`."""
    if apply:
        say(f"Effacement de {mask_address(plan.email)} (--apply) : ce qui va être fait.")
    else:
        say(f"Effacement de {mask_address(plan.email)} : simulation (--dry-run), rien n'est écrit.")
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


# --- effacement (`--apply`) -------------------------------------------------------------


@dataclass
class ForgetResult:
    """Ce que `--apply` a fait. `done` faux : rien à faire (compte et traces déjà partis)."""

    done: bool
    sessions: int = 0
    renders_cancelled: int = 0
    projects_deleted: list[UUID] = field(default_factory=list)
    projects_transferred: int = 0
    folders_left: list[UUID] = field(default_factory=list)
    """Dossiers de projets supprimés que le disque a refusé d'effacer (à retirer à la main)."""


def nothing_to_do(plan: ForgetPlan) -> bool:
    """Ni compte, ni code de connexion, ni invitation à cette adresse : un second `--apply`."""
    return plan.user_id is None and not plan.login_codes and not plan.invitations_to_address


def _drop_project(db: Session, pid: UUID) -> None:
    """Un projet supprimé et tout ce qui s'y rattache (explicite : sans compter sur les
    cascades, pareil sur SQLite et Postgres)."""
    db.execute(delete(ShareLink).where(ShareLink.project_id == pid))
    db.execute(delete(RenderRequest).where(RenderRequest.project_id == pid))
    db.execute(delete(Invitation).where(Invitation.project_id == pid))
    db.execute(delete(NotificationReadItem).where(NotificationReadItem.project_id == pid))
    db.execute(delete(Member).where(Member.project_id == pid))
    db.execute(delete(ProjectRow).where(ProjectRow.id == pid))


def _hand_over(db: Session, pid: UUID, user_id: UUID, heir: UUID, *, promote: bool) -> None:
    """Le projet passe à `heir` : propriétaire inscrit (`owner_id`) et, pour un transfert
    (`--transfer-to`), son rôle de membre éditeur devient `owner` (décision 1 du 30 oct.)."""
    if promote:
        db.execute(
            update(Member)
            .where(Member.project_id == pid, Member.user_id == heir)
            .values(role="owner")
        )
    db.execute(
        update(ProjectRow)
        .where(ProjectRow.id == pid, ProjectRow.owner_id == user_id)
        .values(owner_id=heir)
    )


def _apply_projects(db: Session, plan: ForgetPlan, user_id: UUID) -> tuple[list[UUID], int]:
    """Ses projets : supprimés (seul membre, ou partagés avec `--delete-shared`), ou passés à
    l'autre propriétaire / au membre éditeur donné. Renvoie (supprimés, transférés)."""
    gone = [p.id for p in plan.solo]
    if plan.shared_action == "delete":
        gone += [p.id for p in plan.shared]
    moved = 0
    for p in plan.co_owned:
        if p.co_owner is not None:
            _hand_over(db, p.id, user_id, p.co_owner, promote=False)
            moved += 1
    if plan.shared_action == "transfer" and plan.transfer_to is not None:
        for p in plan.shared:
            _hand_over(db, p.id, user_id, plan.transfer_to, promote=True)
            moved += 1
    if plan.shared_action == "heirs":
        for p in plan.shared:
            _hand_over(db, p.id, user_id, plan.heirs[p.id], promote=True)
            moved += 1
    for pid in gone:
        _drop_project(db, pid)
    return gone, moved


def _remove_folders(projects_dir: Path, gone: list[UUID]) -> list[UUID]:
    """Après la transaction : les dossiers des projets supprimés. Renvoie ceux restés."""
    left: list[UUID] = []
    root = projects_dir.resolve()
    for pid in gone:
        folder = (projects_dir / str(pid)).resolve()
        if folder.parent != root or not folder.exists():
            continue
        try:
            shutil.rmtree(folder)
        except OSError:
            left.append(pid)
    return left


def apply_forget(
    repo: AccountsRepo,
    plan: ForgetPlan,
    *,
    projects_dir: Path,
    data_dir: Path,
    clock: Callable[[], datetime] = utcnow,
    origin: str = "accounts forget --apply",
) -> ForgetResult:
    """L'effacement selon un plan possible, dans une seule transaction. `origin` : ce que dit
    la ligne de journal (KAN-132 : « suppression par le titulaire »). Ordre : sessions
    révoquées d'abord (décision 5 ; les WebSocket tombent au recontrôle de 30 s), demandes en
    attente annulées (`cancel_pending_renders`, KAN-115) AVANT le `DELETE users`, codes et
    invitations effacés par adresse (décision 6). Les fichiers des projets des autres ne sont
    pas réécrits (décision 2)."""
    if not plan.possible:
        msg = "le plan a des refus : rien n'est écrit"
        raise ForgetRefusedError(msg)
    if nothing_to_do(plan):
        return ForgetResult(done=False)
    address, user_id = plan.email, plan.user_id
    result = ForgetResult(done=True)
    with repo.session() as db, db.begin():
        if user_id is not None:
            user = db.get(User, user_id)
            if user is None or user.email != address:
                msg = "le compte a changé depuis le plan : relancer la commande"
                raise ForgetRefusedError(msg)
            gone = db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
            result.sessions = int(getattr(gone, "rowcount", 0) or 0)
            result.renders_cancelled = cancel_pending_renders(db, user_id, clock())
            db.flush()
        db.execute(delete(LoginToken).where(LoginToken.email == address))
        by_address: ColumnElement[bool] = Invitation.email == address
        if user_id is not None:
            by_address = or_(by_address, Invitation.created_by == user_id)
        db.execute(delete(Invitation).where(by_address))
        if user_id is not None:
            db.execute(delete(ExtensionToken).where(ExtensionToken.user_id == user_id))
            actor = _actor(data_dir, user_id)
            if actor is not None:
                db.execute(delete(Event).where(Event.actor == actor))
            db.execute(delete(ShareLink).where(ShareLink.created_by == user_id))
            result.projects_deleted, result.projects_transferred = _apply_projects(
                db, plan, user_id
            )
            db.execute(delete(Member).where(Member.user_id == user_id))
            db.execute(delete(NotificationReadItem).where(NotificationReadItem.user_id == user_id))
            db.execute(delete(NotificationRead).where(NotificationRead.user_id == user_id))
            # KAN-115 : ses rendus des projets qui restent sont gardés, sans lanceur ; une
            # prise en cours garde son bail et suit son sort habituel.
            db.execute(
                update(RenderRequest).where(RenderRequest.user_id == user_id).values(user_id=None)
            )
            db.execute(delete(User).where(User.id == user_id))
    result.folders_left = _remove_folders(projects_dir, result.projects_deleted)
    if user_id is None:
        log.warning("accounts forget --apply : codes et invitations d'une adresse sans compte")
    else:
        log.warning(
            "%s : compte %s effacé (%d projet(s) supprimé(s), %d transféré(s))",
            origin,
            str(user_id)[:8],
            len(result.projects_deleted),
            result.projects_transferred,
        )
    return result


def describe_result(result: ForgetResult, say: Callable[[str], None] = print) -> None:
    """Après l'effacement ; ni adresse ni nom."""
    if not result.done:
        say("Rien à faire : aucun compte, code ni invitation pour cette adresse.")
        return
    say(
        f"Effacé : {result.sessions} session(s) révoquée(s), {result.renders_cancelled} "
        f"demande(s) en attente annulée(s), {len(result.projects_deleted)} projet(s) "
        f"supprimé(s), {result.projects_transferred} projet(s) transféré(s)."
    )
    for pid in result.folders_left:
        say(f"  ATTENTION : dossier du projet {str(pid)[:8]} resté sur le disque, à retirer")


def run_apply(
    repo: AccountsRepo,
    plan: ForgetPlan,
    *,
    projects_dir: Path,
    data_dir: Path,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] = print,
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """`--apply` : le plan (adresse masquée), ses refus, la confirmation en retapant l'adresse,
    puis l'effacement. 0 : effacé ou rien à faire ; 1 : refus ou confirmation manquée."""
    if nothing_to_do(plan) and plan.possible:
        describe_result(ForgetResult(done=False), say)
        return 0
    describe(plan, say, apply=True)
    if not plan.possible:
        say("Rien n'est écrit.")
        return 1
    try:  # `input` lu à l'appel (et non à la définition) : remplaçable dans les tests
        typed = (ask or input)("Pour confirmer, retapez l'adresse du compte : ")
    except (EOFError, OSError):  # pas de terminal : pas de confirmation
        typed = ""
    if normalize_email(typed) != plan.email:
        say("Confirmation manquée : rien n'est écrit.")
        return 1
    result = apply_forget(repo, plan, projects_dir=projects_dir, data_dir=data_dir, clock=clock)
    describe_result(result, say)
    return 0
