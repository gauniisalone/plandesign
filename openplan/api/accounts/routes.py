"""Routes de connexion (note 17, § 2 et § 3) : `/api/auth/*` et révocation par un
administrateur. N'existent que si les comptes sont actifs.

La page « Se connecter » (GET du lien) et la page « Connexion impossible » sont de simples
pages servies par l'API : sans script (CSP stricte), un formulaire qui poste vers la même
origine. La page de connexion de l'application (champ e-mail) est celle du front (B1d).
"""

from __future__ import annotations

import html
import logging
from typing import Annotated
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
from openplan.api.accounts.mail import MailMessage
from openplan.api.accounts.service import (
    CALLBACK_PATH,
    REFUSAL_TEXT,
    AccountService,
    InvalidEmailError,
    LoginRefusedError,
)
from openplan.brand import APP_NAME
from openplan.schemas.common import StrictModel

log = logging.getLogger("openplan.accounts")
router = APIRouter(tags=["auth"])

NEUTRAL = "Si cette adresse peut se connecter, un lien vient de partir."
LOGIN_PAGE = "/"
"""Page de l'application où l'on redemande un lien (celle du front, B1d)."""


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


class StartResult(StrictModel):
    detail: str = NEUTRAL


class Me(StrictModel):
    id: UUID
    email: str
    name: str
    is_admin: bool


class Revoked(StrictModel):
    revoked: int


def _send(accounts: AccountService, message: MailMessage) -> None:
    try:
        accounts.mailer.send(message)
    except Exception:  # l'échec d'envoi ne doit jamais dire si l'adresse a un compte
        log.exception("envoi du lien de connexion impossible")


@router.post("/auth/email/start", status_code=202)
def email_start(body: StartBody, request: Request, background: BackgroundTasks) -> StartResult:
    """Même réponse que l'adresse ait un compte ou non ; l'envoi part après la réponse, pour
    que la durée de la réponse ne le trahisse pas non plus."""
    accounts = service(request)
    try:
        message = accounts.start_login(
            body.email, body.next, request.client.host if request.client else None
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
        "button{font:inherit;padding:.6rem 1.2rem;border-radius:.5rem;border:0;"
        "background:#2d6cdf;color:#fff;cursor:pointer}a{color:#2d6cdf}"
        "@media (prefers-color-scheme: dark){body{color:#e6e6e6;background:#16181c}}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"})


def _refused(reason_text: str) -> HTMLResponse:
    return _page(
        "Connexion impossible",
        f"<p>Connexion impossible : {html.escape(reason_text)}</p>"
        f'<p><a href="{LOGIN_PAGE}">Recevoir un nouveau lien</a></p>',
        status=400,
    )


@router.get(CALLBACK_PATH.removeprefix("/api"), response_class=HTMLResponse)
def email_callback_page(request: Request, token: str = "") -> HTMLResponse:
    """Ouverture du lien : une page avec un bouton ; le jeton n'est **pas** consommé ici
    (un aperçu de messagerie qui suit le lien ne connecte personne)."""
    try:
        service(request).check_login(token)
    except LoginRefusedError as exc:
        return _refused(REFUSAL_TEXT[exc.reason])
    return _page(
        f"Se connecter à {APP_NAME}",
        f'<form method="post" action="{CALLBACK_PATH}">'
        f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
        '<button type="submit">Se connecter</button></form>',
    )


@router.post(CALLBACK_PATH.removeprefix("/api"), response_model=None)
def email_callback_confirm(
    request: Request, token: Annotated[str, Form(max_length=128)]
) -> Response:
    """Bouton « Se connecter » : consomme le jeton, ouvre la session, revient à `next`."""
    agent = request.headers.get("user-agent", "")
    try:
        _, raw, next_path = service(request).complete_login(token, agent)
    except LoginRefusedError as exc:
        return _refused(REFUSAL_TEXT[exc.reason])
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookie(response, raw)
    return response


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
