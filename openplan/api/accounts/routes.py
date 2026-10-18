"""Routes de connexion (note 17, § 2 et § 3) : `/api/auth/*` et révocation par un
administrateur. N'existent que si les comptes sont actifs.

B1b2 (18 oct.) : plus de lien de connexion. L'e-mail porte un code à 6 chiffres, tapé dans
la fenêtre « Se connecter ». En attendant celle du front (B1d), l'API sert une page en deux
temps, sans script (CSP stricte) : l'adresse, puis le code ; chaque formulaire poste vers la
même origine. Le front utilisera `POST /api/auth/email/start` puis `/api/auth/email/verify`.
"""

from __future__ import annotations

import html
import logging
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import Field

from openplan.api.access import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    AdminDep,
    SignedInDep,
)
from openplan.api.accounts.mail import FakeMailer, MailMessage
from openplan.api.accounts.service import (
    LOGIN_TTL,
    REFUSAL_TEXT,
    AccountService,
    InvalidEmailError,
    LoginRefusal,
    LoginRefusedError,
    safe_next,
)
from openplan.api.accounts.sharing import INVITE_REFUSED, InvitationRefusedError
from openplan.brand import APP_NAME
from openplan.schemas.common import StrictModel

log = logging.getLogger("openplan.accounts")
router = APIRouter(tags=["auth"])
test_router = APIRouter(tags=["tests"])
"""Enregistré seulement avec OPENPLAN_MAILER=fake, lui-même refusé hors boucle locale."""

NEUTRAL = "Si cette adresse peut se connecter, un code vient de partir."
LOGIN_PAGE = "/api/auth/login"
"""Fenêtre « Se connecter » servie par l'API (celle du front la remplacera, B1d)."""
MINUTES = int(LOGIN_TTL.total_seconds() // 60)


def service(request: Request) -> AccountService:
    accounts: AccountService = request.app.state.accounts
    return accounts


def set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=SESSION_MAX_AGE_S,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


class StartBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    next: str | None = Field(default=None, max_length=512)
    invitation: str | None = Field(default=None, max_length=128)
    """B1e : jeton d'un lien d'invitation (inscription « sur invitation »)."""


class StartResult(StrictModel):
    detail: str = NEUTRAL


class VerifyBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=1, max_length=32)
    invitation: str | None = Field(default=None, max_length=128)


class AcceptBody(StrictModel):
    token: str = Field(min_length=1, max_length=128)


class Accepted(StrictModel):
    project_id: UUID
    role: str


class Me(StrictModel):
    id: UUID
    email: str
    name: str
    is_admin: bool


class Revoked(StrictModel):
    revoked: int


class LastCode(StrictModel):
    code: str


def _send(accounts: AccountService, message: MailMessage) -> None:
    try:
        accounts.mailer.send(message)
    except Exception:  # l'échec d'envoi ne doit jamais dire si l'adresse a un compte
        log.exception("envoi du code de connexion impossible")


@router.post("/auth/email/start", status_code=202)
def email_start(body: StartBody, request: Request, background: BackgroundTasks) -> StartResult:
    """Même réponse que l'adresse ait un compte ou non ; l'envoi part après la réponse, pour
    que la durée de la réponse ne le trahisse pas non plus."""
    accounts = service(request)
    try:
        message = accounts.start_login(
            body.email,
            body.next,
            request.client.host if request.client else None,
            body.invitation,
        )
    except InvalidEmailError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if message is not None:
        background.add_task(_send, accounts, message)
    return StartResult()


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    page = (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
        "padding:0 1rem;line-height:1.5;color:#1f2328;background:#fff}"
        "label{display:block;margin:.8rem 0 .3rem}"
        "input{font:inherit;width:100%;box-sizing:border-box;padding:.55rem .7rem;"
        "border:1px solid #c9ced6;border-radius:.5rem}"
        "input.code{letter-spacing:.4em;font-size:1.4rem;text-align:center}"
        "button{font:inherit;margin-top:1rem;padding:.6rem 1.2rem;border-radius:.5rem;border:0;"
        "background:#2d6cdf;color:#fff;cursor:pointer}a{color:#2d6cdf}.error{color:#b42318}"
        "@media (prefers-color-scheme: dark){body{color:#e6e6e6;background:#16181c}"
        "input{background:#1f2228;color:#e6e6e6;border-color:#3a3f48}"
        "a{color:#8ab4ff}.error{color:#ff8f85}}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"})


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{html.escape(value, quote=True)}">'


def _email_step(next_path: str, error: str = "", status: int = 200) -> HTMLResponse:
    """Premier temps : l'adresse."""
    note = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _page(
        f"Se connecter à {APP_NAME}",
        f'{note}<form method="post" action="{LOGIN_PAGE}/code">'
        f"{_hidden('next', next_path)}"
        '<label for="email">Adresse e-mail</label>'
        '<input id="email" name="email" type="email" autocomplete="email" required '
        'maxlength="320">'
        '<button type="submit">Recevoir un code</button></form>',
        status,
    )


def _code_step(email: str, next_path: str, error: str = "", status: int = 200) -> HTMLResponse:
    """Second temps : le code reçu (même texte que l'adresse ait un compte ou non)."""
    note = (
        f'<p class="error">{html.escape(error)}</p>'
        if error
        else f"<p>{html.escape(NEUTRAL)} Il expire dans {MINUTES} minutes.</p>"
    )
    return _page(
        f"Se connecter à {APP_NAME}",
        f'{note}<form method="post" action="{LOGIN_PAGE}/verify">'
        f"{_hidden('email', email)}{_hidden('next', next_path)}"
        '<label for="code">Code à 6 chiffres</label>'
        '<input id="code" class="code" name="code" inputmode="numeric" '
        'autocomplete="one-time-code" pattern="[0-9 ]{6,8}" maxlength="8" required>'
        '<button type="submit">Se connecter</button></form>'
        f'<p><a href="{LOGIN_PAGE}?next={html.escape(quote(next_path, safe="/"))}">'
        "Recevoir un nouveau code</a></p>",
        status,
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(next: str = "/") -> HTMLResponse:
    """Fenêtre « Se connecter » (premier temps : l'adresse)."""
    return _email_step(safe_next(next))


@router.post("/auth/login/code", response_class=HTMLResponse)
def login_code_page(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form(max_length=320)],
    next: Annotated[str, Form(max_length=512)] = "/",
) -> HTMLResponse:
    """Demande d'un code depuis la page : même réponse que l'adresse ait un compte ou non."""
    accounts = service(request)
    try:
        message = accounts.start_login(email, next, _client_ip(request))
    except InvalidEmailError:
        return _email_step(safe_next(next), "Cette adresse e-mail n'est pas valable.", 422)
    if message is not None:
        background.add_task(_send, accounts, message)
    return _code_step(email.strip(), safe_next(next))


@router.post("/auth/login/verify", response_model=None)
def login_verify_page(
    request: Request,
    email: Annotated[str, Form(max_length=320)],
    code: Annotated[str, Form(max_length=32)],
    next: Annotated[str, Form(max_length=512)] = "/",
) -> Response:
    """Code tapé dans la page : juste, la session s'ouvre et l'on revient à `next` ; sinon la
    même page, avec le même texte quel que soit le motif."""
    agent = request.headers.get("user-agent", "")
    try:
        _, raw, next_path = service(request).complete_login(email, code, agent)
    except LoginRefusedError as exc:
        text = REFUSAL_TEXT[exc.reason]
        return _code_step(email, safe_next(next), f"Connexion impossible : {text}", 400)
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookie(response, raw)
    return response


@router.post("/auth/email/verify")
def email_verify(body: VerifyBody, request: Request, response: Response) -> Me:
    """Le front (B1d) : le code de cette adresse ; juste, la session s'ouvre (cookie)."""
    agent = request.headers.get("user-agent", "")
    try:
        user, raw, _ = service(request).complete_login(
            body.email, body.code, agent, body.invitation
        )
    except LoginRefusedError as exc:
        status = 403 if exc.reason is LoginRefusal.CLOSED else 400
        raise HTTPException(status_code=status, detail=REFUSAL_TEXT[exc.reason]) from exc
    set_session_cookie(response, raw)
    return Me(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


@router.post("/auth/invitation/accept")
def accept_invitation(body: AcceptBody, request: Request, user: SignedInDep) -> Accepted:
    """B1e : le lien d'invitation ouvert par un utilisateur connecté (jeton lu dans l'ancre
    du lien par le front, jamais dans une URL) ; il devient membre avec le rôle du lien."""
    try:
        project_id, role = service(request).accept_invitation(body.token, user.id)
    except InvitationRefusedError as exc:
        raise HTTPException(status_code=410, detail=INVITE_REFUSED) from exc
    return Accepted(project_id=project_id, role=role)


@router.get("/auth/me")
def me(user: SignedInDep) -> Me:
    return Me(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    """Révoque la session de ce navigateur et efface le cookie."""
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        service(request).revoke_session(raw)
    gone = Response(status_code=204)
    clear_session_cookie(gone)
    return gone


@router.post("/auth/logout-all", status_code=204)
def logout_all(request: Request, user: SignedInDep) -> Response:
    """« Me déconnecter partout » : toutes les sessions de l'utilisateur."""
    service(request).revoke_all(user.id)
    gone = Response(status_code=204)
    clear_session_cookie(gone)
    return gone


@router.post("/admin/users/{user_id}/sessions/revoke")
def admin_revoke(user_id: UUID, request: Request, admin: AdminDep) -> Revoked:
    """Un administrateur révoque toutes les sessions d'un utilisateur."""
    del admin
    return Revoked(revoked=service(request).revoke_all(user_id))


@test_router.get("/test/mail/last-code")
def last_code(email: str, request: Request) -> LastCode:
    """Tests de bout en bout : le dernier code envoyé à cette adresse par le mailer `fake`."""
    mailer = service(request).mailer
    message = mailer.last_to(email.strip().lower()) if isinstance(mailer, FakeMailer) else None
    if message is None:
        raise HTTPException(status_code=404, detail="aucun code pour cette adresse")
    return LastCode(code=message.code)
