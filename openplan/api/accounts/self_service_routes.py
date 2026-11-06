"""KAN-132 : supprimer son compte et exporter ses données, depuis Réglages › Compte.

Derrière OPENPLAN_ACCOUNT_SELF_SERVICE (éteint par défaut : les routes répondent 404, comme si
elles n'existaient pas), le temps de la revue de l'Infra et de la décision d'augus.

Suppression (décisions d'augus du 6 nov.) : le moteur de `accounts forget` (KAN-114), même
ordre et même transaction, confirmé par un code envoyé par e-mail et l'adresse retapée.
- `GET /api/account/delete` : l'aperçu (projets supprimés, projets partagés avec leurs membres
  et l'héritier par défaut, refus) ;
- `POST /api/account/delete/code` : le code de confirmation, par e-mail ;
- `POST /api/account/delete` : code + adresse + héritiers choisis ; efface, ferme les sessions
  et vide le cookie.
Un projet partagé passe au membre choisi, sinon à l'éditeur le plus ancien, sinon au membre le
plus ancien ; il n'est effacé que s'il n'a aucun autre membre. Ses contenus dans les projets
qui restent sont gardés, montrés « Ancien membre ». Refus : un compte administrateur (il passe
par l'opérateur).

Export : `POST /api/account/export` (202, archive en tâche de fond, lien par e-mail, 24 h) et
`GET /api/account/export/download?t=…` (usage unique, connecté au même compte), voir
`account_export`.

Débit limité par compte ; les lignes de journal ne portent que l'id tronqué, jamais un contenu.
"""

from __future__ import annotations

import html
import logging
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field
from sqlalchemy import select

from openplan.api.access import SignedInDep, signed_in_user
from openplan.api.accounts.account_export import (
    LINK_TTL,
    ExportBusyError,
    ExportCard,
    build_export,
    open_export,
    take_export,
)
from openplan.api.accounts.forget import (
    ForgetPlan,
    ForgetRefusedError,
    apply_forget,
    plan_forget,
)
from openplan.api.accounts.mail import (
    delete_code_message,
    export_failed_message,
    export_ready_message,
    mask_address,
)
from openplan.api.accounts.models import Member, User
from openplan.api.accounts.password_service import REAUTH_TTL
from openplan.api.accounts.routes import (
    _client_ip,
    _page,
    _send,
    announce_owner,
    clear_session_cookie,
    service,
)
from openplan.api.accounts.service import AccountService, LoginRefusedError
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.refusal import Refusal
from openplan.api.settings import project_root
from openplan.schemas.common import StrictModel

log = logging.getLogger("openplan.accounts")

DELETE_TRIES = 5
DELETE_WINDOW_S = 3600.0
"""Débit : 5 tentatives de suppression (ou demandes de code) par compte et par heure."""
EXPORT_TRIES = 3
EXPORT_WINDOW_S = 86400.0
"""Débit : 3 demandes d'export par compte et par jour (une seule en cours à la fois)."""
ADMIN_REFUSED = (
    "Un compte administrateur ne se supprime pas ici : écrivez-nous par le formulaire de contact."
)
ADDRESS_MISMATCH = "L'adresse tapée n'est pas celle de votre compte."
BAD_CODE = "Code incorrect ou expiré : demandez-en un nouveau."
IMPOSSIBLE = "La suppression n'est pas possible en l'état : rechargez la page et réessayez."
BAD_HEIR = "Le nouveau propriétaire choisi n'est plus membre du projet : rechargez la page."
EXPORT_BUSY = (
    "Une archive est déjà en préparation, ou un lien encore valable vous a été envoyé : "
    "regardez votre boîte de réception."
)
LINK_GONE = (
    "Ce lien n'est plus valable : il a déjà servi ou il a expiré. Demandez une nouvelle archive "
    "depuis Réglages › Compte."
)


def self_service_enabled(request: Request) -> None:
    """OPENPLAN_ACCOUNT_SELF_SERVICE faux : la route n'existe pas (404)."""
    if not service(request).settings.account_self_service:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(tags=["account"], dependencies=[Depends(self_service_enabled)])


def _limiter(request: Request, name: str, tries: int, window: float) -> SlidingWindowLimiter:
    key = f"self_service_{name}_limiter"
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, key, None)
    if limiter is None:
        limiter = SlidingWindowLimiter(tries, window)
        setattr(request.app.state, key, limiter)
    return limiter


def _throttle(request: Request, name: str, user_id: UUID, tries: int, window: float) -> None:
    if not _limiter(request, name, tries, window).allow(str(user_id)):
        raise Refusal(
            429,
            "Trop de demandes : réessayez dans une heure.",
            "rate_limited",
            headers={"Retry-After": str(int(window))},
        )


def _project_dir(request: Request, project_id: UUID) -> Path:
    return Path(request.app.state.store.project_dir(project_id))


# --- suppression ----------------------------------------------------------------------


class ProjectBrief(StrictModel):
    id: UUID
    name: str


class HeirOption(StrictModel):
    user_id: UUID
    name: str
    """Le nom affiché du membre, sinon son adresse masquée (`a***@example.org`)."""
    role: str


class SharedProject(StrictModel):
    id: UUID
    name: str
    members: list[HeirOption]
    heir: UUID
    """L'héritier par défaut : l'éditeur le plus ancien, sinon le membre le plus ancien."""


class DeletePreview(StrictModel):
    deleted: list[ProjectBrief]
    """Ses projets sans autre membre : effacés avec leurs fichiers."""
    shared: list[SharedProject]
    """Ses projets partagés : passés à un autre membre."""
    co_owned: list[ProjectBrief]
    """Ses projets avec un autre propriétaire : ils restent à celui-ci."""
    memberships: int
    """Ses rôles dans les projets des autres (retirés ; ses contenus y restent, « Ancien
    membre »)."""
    refusal: str | None = None


class DeleteBody(StrictModel):
    code: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=1, max_length=320)
    heirs: dict[UUID, UUID] = Field(default_factory=dict, max_length=1000)


class Sent(StrictModel):
    detail: str


def _plan(request: Request, user_email: str, heirs: dict[UUID, UUID]) -> ForgetPlan:
    accounts = service(request)
    settings = accounts.settings
    return plan_forget(
        accounts,
        user_email,
        projects_dir=project_root(settings),
        data_dir=settings.data_dir,
        admin_email=settings.admin_email,
        heirs=heirs,
    )


def _shown(user: User) -> str:
    return user.name.strip() or mask_address(user.email)


def _members(accounts: AccountService, project_id: UUID, me: UUID) -> list[HeirOption]:
    with accounts.session() as db:
        rows = db.execute(
            select(User, Member.role)
            .join(Member, Member.user_id == User.id)
            .where(Member.project_id == project_id, Member.user_id != me)
            .order_by(Member.added_at)
        ).all()
    return [HeirOption(user_id=u.id, name=_shown(u), role=role) for u, role in rows]


@router.get("/account/delete")
def delete_preview(request: Request, user: SignedInDep) -> DeletePreview:
    """Ce que la suppression ferait ; rien n'est écrit."""
    accounts = service(request)
    plan = _plan(request, user.email, {})
    refusal = ADMIN_REFUSED if user.is_admin else (IMPOSSIBLE if not plan.possible else None)
    return DeletePreview(
        deleted=[ProjectBrief(id=p.id, name=p.name) for p in plan.solo],
        shared=[
            SharedProject(
                id=p.id,
                name=p.name,
                members=_members(accounts, p.id, user.id),
                heir=plan.heirs[p.id],
            )
            for p in plan.shared
        ],
        co_owned=[ProjectBrief(id=p.id, name=p.name) for p in plan.co_owned],
        memberships=len(plan.memberships),
        refusal=refusal,
    )


@router.post("/account/delete/code", status_code=202)
def delete_code(request: Request, user: SignedInDep, background: BackgroundTasks) -> Sent:
    """Le code qui confirme la suppression, par e-mail (même file de codes que `reauth`)."""
    if user.is_admin:
        raise HTTPException(status_code=403, detail=ADMIN_REFUSED)
    _throttle(request, "delete", user.id, DELETE_TRIES, DELETE_WINDOW_S)
    accounts = service(request)
    accounts.mail_budget.check("signup")
    now = accounts.clock()
    with accounts.session() as db, db.begin():
        code = accounts._issue_code(db, user.email, "reauth", REAUTH_TTL, now, _client_ip(request))
    if code is not None:
        minutes = int(REAUTH_TTL.total_seconds() // 60)
        background.add_task(
            _send, accounts, delete_code_message(user.email, code, minutes), "signup"
        )
    return Sent(detail="Un code vient de partir vers votre adresse.")


@router.post("/account/delete", status_code=204)
def delete_account(body: DeleteBody, request: Request, user: SignedInDep) -> Response:
    """L'effacement : l'adresse retapée, le plan (refus d'abord), le code consommé, puis le
    moteur de `forget`. Les sessions sont fermées, le cookie vidé."""
    if user.is_admin:
        raise HTTPException(status_code=403, detail=ADMIN_REFUSED)
    _throttle(request, "delete", user.id, DELETE_TRIES, DELETE_WINDOW_S)
    accounts = service(request)
    if body.email.strip().lower() != user.email:
        raise HTTPException(status_code=422, detail=ADDRESS_MISMATCH)
    try:
        plan = _plan(request, user.email, dict(body.heirs))
    except ForgetRefusedError as exc:  # pragma: no cover - arguments fixés ici
        raise HTTPException(status_code=409, detail=IMPOSSIBLE) from exc
    if not plan.possible:
        heir = any("héritier" in r or "projet inconnu" in r for r in plan.refusals)
        raise HTTPException(status_code=409, detail=BAD_HEIR if heir else IMPOSSIBLE)
    try:
        accounts._consume_code(user.email, body.code, "reauth", accounts.clock())
    except LoginRefusedError as exc:
        raise HTTPException(status_code=400, detail=BAD_CODE) from exc
    settings = accounts.settings
    try:
        apply_forget(
            accounts,
            plan,
            projects_dir=project_root(settings),
            data_dir=settings.data_dir,
            clock=accounts.clock,
            origin="suppression du compte par son titulaire",
        )
    except ForgetRefusedError as exc:
        raise HTTPException(status_code=409, detail=IMPOSSIBLE) from exc
    # KAN-145 : chaque héritier lit « Vous êtes maintenant propriétaire de … » (cloche et
    # activité ; pas d'e-mail).
    if plan.shared_action == "heirs":
        for pid, heir_id in plan.heirs.items():
            announce_owner(request, pid, heir_id)
    done = Response(status_code=204)
    clear_session_cookie(done)
    return done


# --- export ---------------------------------------------------------------------------


def _export_task(request: Request, card: ExportCard, token: str, email: str) -> None:
    """La tâche de fond : l'archive, puis l'e-mail (le lien, ou l'échec)."""
    accounts = service(request)
    settings = accounts.settings
    root = settings.exports_path
    done = build_export(
        accounts,
        root,
        card,
        project_dir=lambda pid: _project_dir(request, pid),
        data_dir=settings.data_dir,
        clock=accounts.clock,
    )
    if done.state == "ready":
        link = f"{settings.public_url}/api/account/export/download?t={quote(token)}"
        hours = int(LINK_TTL.total_seconds() // 3600)
        _send(accounts, export_ready_message(email, link, hours), "signup")
    else:
        log.warning("export des données : archive non préparée (%s)", card.user_id[:8])
        _send(accounts, export_failed_message(email), "signup")


@router.post("/account/export", status_code=202)
def export_start(request: Request, user: SignedInDep, background: BackgroundTasks) -> Sent:
    """L'archive « Mes données » se prépare en tâche de fond ; le lien part par e-mail."""
    _throttle(request, "export", user.id, EXPORT_TRIES, EXPORT_WINDOW_S)
    accounts = service(request)
    accounts.mail_budget.check("signup")
    try:
        card, token = open_export(accounts.settings.exports_path, user.id, accounts.clock())
    except ExportBusyError as exc:
        raise HTTPException(status_code=409, detail=EXPORT_BUSY) from exc
    background.add_task(_export_task, request, card, token, user.email)
    return Sent(
        detail="Votre archive se prépare : vous recevrez un lien par e-mail, valable 24 heures."
    )


@router.get("/account/export/download", response_model=None)
def export_download(
    request: Request, t: Annotated[str, Query(max_length=128)] = ""
) -> FileResponse | HTMLResponse:
    """Le lien de l'e-mail : la session du même compte est exigée (sinon une page qui invite
    à se connecter, puis à rouvrir le lien : le jeton ne passe jamais par la page de connexion,
    ni par un Referer) ; l'archive part une fois, puis est effacée. Les refus ne disent pas si
    le jeton existe."""
    try:
        user = signed_in_user(request)
    except HTTPException:
        return _no_referrer(
            _page(
                "Connectez-vous d'abord",
                "<p>Pour télécharger vos données, connectez-vous au compte qui les a demandées, "
                "puis rouvrez le lien de l'e-mail.</p>"
                '<p><a href="/api/auth/login?next=/">Se connecter</a></p>',
                status=401,
            )
        )
    accounts = service(request)
    path = take_export(accounts.settings.exports_path, user.id, t, accounts.clock())
    if path is None:
        return _no_referrer(_page("Lien expiré", f"<p>{html.escape(LINK_GONE)}</p>", status=404))
    log.warning("export des données : archive téléchargée (%s)", str(user.id)[:8])
    name = f"mes-donnees-plandesign-{date.today().isoformat()}.zip"
    background = BackgroundTasks()
    background.add_task(path.unlink, missing_ok=True)
    return _no_referrer(
        FileResponse(path, media_type="application/zip", filename=name, background=background)
    )


def _no_referrer[R: Response](response: R) -> R:
    """Revue Infra : le jeton de l'URL ne part jamais dans un Referer (comme /api/share)."""
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response
