"""KAN-104 L1 : routes du mot de passe, des preuves récentes et des sessions.

Toutes répondent 404 tant qu'OPENPLAN_PASSWORD_LOGIN n'est pas posé (défaut : faux) : en
production, rien de neuf n'est ouvert. Aucune n'entre dans `SITE_MUTATIONS` : la fenêtre du
site renvoie vers l'app pour le mot de passe (pas de CORS à cookies en plus). Aucun mot de passe
n'est jamais journalisé ni renvoyé.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from openplan.api.access import SESSION_COOKIE, AuthUser, SignedInDep
from openplan.api.accounts.password_service import (
    PasswordForbiddenError,
    PasswordRefusedError,
    PasswordThrottledError,
    ReauthRequiredError,
    SessionInfo,
)
from openplan.api.accounts.passwords import PasswordPolicyError
from openplan.api.accounts.routes import (
    NEUTRAL,
    Me,
    StartResult,
    _client_ip,
    _email_step,
    _send,
    record_login,
    service,
    set_session_cookie,
)
from openplan.api.accounts.service import (
    REFUSAL_TEXT,
    InvalidEmailError,
    LoginRefusedError,
    safe_next,
)
from openplan.schemas.common import StrictModel

WRONG = "Adresse ou mot de passe incorrect."
THROTTLED = "Trop d'essais depuis cette connexion : réessayez dans une heure, ou recevez un code."
REAUTH_FIRST = "Confirmez d'abord avec le code envoyé à votre adresse."


def password_enabled(request: Request) -> None:
    """OPENPLAN_PASSWORD_LOGIN faux : la route n'existe pas (404)."""
    if not service(request).settings.password_login:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(tags=["auth"], dependencies=[Depends(password_enabled)])

PasswordField = Annotated[str, Field(min_length=1, max_length=1024)]
"""La politique (12 à 128 caractères) est vérifiée par le service, avec un texte clair."""


class PasswordLoginBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: PasswordField


class ForgotBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)


class SignupStartBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    cgu: bool
    """Les conditions d'utilisation acceptées : exigé (sinon 422, rien ne part)."""
    invitation: str | None = Field(default=None, max_length=128)


class SignupVerifyBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=1, max_length=32)
    password: PasswordField
    name: str = Field(default="", max_length=80)
    invitation: str | None = Field(default=None, max_length=128)


SIGNUP_NEUTRAL = "Si cette adresse peut créer un compte, un code vient de partir."
TERMS_NEEDED = "Acceptez les conditions d'utilisation pour créer un compte."


class ResetBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=1, max_length=32)
    password: PasswordField


class SetPasswordBody(StrictModel):
    current: str | None = Field(default=None, max_length=1024)
    """Le mot de passe actuel ; absent pour un compte qui n'en a pas (preuve `reauth`)."""
    password: PasswordField


class CodeBody(StrictModel):
    code: str = Field(min_length=1, max_length=32)


class SessionOut(StrictModel):
    id: UUID
    created_at: str
    last_used_at: str
    user_agent: str
    auth_method: str
    current: bool


def _me(user: AuthUser) -> Me:
    return Me(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


def _session_raw(request: Request) -> str:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="connexion requise")
    return raw


@router.post("/auth/password/login")
def password_login(body: PasswordLoginBody, request: Request, response: Response) -> Me:
    """Même 401 pour une adresse inconnue, un compte sans mot de passe, l'administrateur
    (code seulement), un délai en cours ou un mot de passe faux."""
    agent = request.headers.get("user-agent", "")
    try:
        user, raw = service(request).passwords.login(
            body.email, body.password, agent, _client_ip(request)
        )
    except PasswordThrottledError as exc:
        raise HTTPException(status_code=429, detail=THROTTLED) from exc
    except PasswordRefusedError as exc:
        raise HTTPException(status_code=401, detail=WRONG) from exc
    record_login(request, user)
    set_session_cookie(response, raw)
    return _me(user)


@router.post("/auth/login/password", response_model=None)
def password_login_page(
    request: Request,
    email: Annotated[str, Form(max_length=320)],
    password: Annotated[str, Form(max_length=1024)],
    next: Annotated[str, Form(max_length=512)] = "/",
) -> Response:
    """La page sans script : adresse et mot de passe ; sinon la même page, même texte."""
    agent = request.headers.get("user-agent", "")
    try:
        user, raw = service(request).passwords.login(email, password, agent, _client_ip(request))
    except PasswordThrottledError:
        return _email_step(safe_next(next), THROTTLED, 429, password=True)
    except PasswordRefusedError:
        return _email_step(safe_next(next), WRONG, 401, password=True)
    record_login(request, user)
    response = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookie(response, raw)
    return response


@router.post("/auth/signup/start", status_code=202)
def signup_start(
    body: SignupStartBody, request: Request, background: BackgroundTasks
) -> StartResult:
    """L2 : même réponse dans tous les cas (inconnue, déjà inscrite, fermée, limite) ;
    KAN-117 : classe `signup` fermée, 503 avant de regarder l'adresse."""
    if not body.cgu:
        raise HTTPException(status_code=422, detail=TERMS_NEEDED)
    accounts = service(request)
    accounts.mail_budget.check("signup")
    try:
        message = accounts.signup.start(body.email, _client_ip(request), body.invitation)
    except InvalidEmailError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if message is not None:
        background.add_task(_send, accounts, message, "signup")
    return StartResult(detail=SIGNUP_NEUTRAL)


@router.post("/auth/signup/verify")
def signup_verify(body: SignupVerifyBody, request: Request, response: Response) -> Me:
    """L2 : le code, le mot de passe, le nom facultatif ; le compte naît ici, la session
    s'ouvre (jeton neuf)."""
    from openplan.api.accounts.routes import claim_invitations

    agent = request.headers.get("user-agent", "")
    try:
        user, raw = service(request).signup.complete(
            body.email,
            body.code,
            body.password,
            body.name,
            _client_ip(request),
            agent,
            body.invitation,
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LoginRefusedError as exc:
        raise HTTPException(status_code=400, detail=REFUSAL_TEXT[exc.reason]) from exc
    claim_invitations(request, user)
    record_login(request, user)
    set_session_cookie(response, raw)
    return Me(
        id=user.id, email=user.email, name=user.name, is_admin=user.is_admin, first_login=True
    )


@router.post("/auth/password/forgot", status_code=202)
def password_forgot(body: ForgotBody, request: Request, background: BackgroundTasks) -> StartResult:
    """Même réponse que l'adresse ait un compte ou non ; l'envoi part après la réponse.
    KAN-117 : classe `signup` fermée, 503 avant de regarder l'adresse."""
    accounts = service(request)
    accounts.mail_budget.check("signup")
    try:
        message = accounts.passwords.start_reset(body.email, _client_ip(request))
    except InvalidEmailError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if message is not None:
        background.add_task(_send, accounts, message, "signup")
    return StartResult(detail=NEUTRAL)


@router.post("/auth/password/reset")
def password_reset(
    body: ResetBody, request: Request, response: Response, background: BackgroundTasks
) -> Me:
    accounts = service(request)
    agent = request.headers.get("user-agent", "")
    try:
        user, raw, notice = accounts.passwords.complete_reset(
            body.email, body.code, body.password, agent
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LoginRefusedError as exc:
        raise HTTPException(status_code=400, detail=REFUSAL_TEXT[exc.reason]) from exc
    background.add_task(_send, accounts, notice)
    record_login(request, user)
    set_session_cookie(response, raw)
    return _me(user)


@router.put("/auth/password", status_code=204)
def set_password(
    body: SetPasswordBody, request: Request, user: SignedInDep, background: BackgroundTasks
) -> Response:
    """Changer (mot de passe actuel) ou poser (preuve récente) : les autres sessions sont
    fermées, celle-ci reçoit un jeton neuf."""
    accounts = service(request)
    agent = request.headers.get("user-agent", "")
    try:
        raw, notice = accounts.passwords.change(
            user.id, _session_raw(request), body.current, body.password, agent
        )
    except ReauthRequiredError as exc:
        raise HTTPException(status_code=403, detail=REAUTH_FIRST) from exc
    except PasswordForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background.add_task(_send, accounts, notice)
    done = Response(status_code=204)
    set_session_cookie(done, raw)
    return done


@router.post("/auth/reauth/start", status_code=202)
def reauth_start(request: Request, user: SignedInDep, background: BackgroundTasks) -> StartResult:
    accounts = service(request)
    accounts.mail_budget.check("signup")
    message = accounts.passwords.start_reauth(user.id, _client_ip(request))
    if message is not None:
        background.add_task(_send, accounts, message, "signup")
    return StartResult(detail="Un code vient de partir vers votre adresse.")


@router.post("/auth/reauth/verify", status_code=204)
def reauth_verify(body: CodeBody, request: Request, user: SignedInDep) -> Response:
    try:
        service(request).passwords.complete_reauth(user.id, _session_raw(request), body.code)
    except LoginRefusedError as exc:
        raise HTTPException(status_code=400, detail=REFUSAL_TEXT[exc.reason]) from exc
    return Response(status_code=204)


def _session_out(info: SessionInfo) -> SessionOut:
    return SessionOut(
        id=info.id,
        created_at=info.created_at.isoformat(),
        last_used_at=info.last_used_at.isoformat(),
        user_agent=info.user_agent,
        auth_method=info.auth_method,
        current=info.current,
    )


@router.get("/auth/sessions")
def list_sessions(request: Request, user: SignedInDep) -> list[SessionOut]:
    infos = service(request).passwords.sessions(user.id, _session_raw(request))
    return [_session_out(i) for i in infos]


@router.delete("/auth/sessions/{session_id}", status_code=204)
def close_session(session_id: UUID, request: Request, user: SignedInDep) -> Response:
    """Seulement une de MES sessions : celle d'un autre compte répond 404, comme absente."""
    if not service(request).passwords.revoke(user.id, session_id):
        raise HTTPException(status_code=404, detail="session inconnue")
    return Response(status_code=204)
