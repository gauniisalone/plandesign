"""Formulaire de contact du site (`POST /api/contact`) : un e-mail en texte seul vers
OPENPLAN_CONTACT_TO (défaut : OPENPLAN_ADMIN_EMAIL), sujet « PlanDesign · contact », Reply-To
sur l'adresse du visiteur. Rien n'est stocké en base. Ouvert à l'origine du site seulement
(security.py). Anti-spam : champ piège caché (rempli : rien ne part) et 3 envois par IP et par
heure ; dans tous ces cas, la même réponse.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import Field

from openplan.api.accounts.mail import MailMessage
from openplan.api.accounts.routes import service
from openplan.api.accounts.service import InvalidEmailError, checked_email
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.brand import APP_NAME
from openplan.schemas.common import StrictModel

log = logging.getLogger("openplan.contact")
router = APIRouter(tags=["contact"])

CONTACT_LIMIT = 3
CONTACT_WINDOW_S = 3600.0
MESSAGE_MAX = 2000
SUBJECT = f"{APP_NAME} · contact"
THANKS = "Merci : votre message est parti. Nous vous répondrons à l'adresse indiquée."


class ContactBody(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)
    consent: bool
    """« J'accepte que mon adresse serve à me répondre » : obligatoire."""
    website: str = Field(default="", max_length=200)
    """Champ piège, caché aux personnes : rempli, rien ne part (même réponse)."""


class ContactResult(StrictModel):
    detail: str = THANKS


def _limiter(request: Request) -> SlidingWindowLimiter:
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, "contact_limiter", None)
    if limiter is None:
        limiter = SlidingWindowLimiter(CONTACT_LIMIT, CONTACT_WINDOW_S)
        request.app.state.contact_limiter = limiter
    return limiter


def contact_message(to: str, visitor: str, text: str) -> MailMessage:
    body = (
        f"Message reçu par le formulaire de contact de {APP_NAME}.\n"
        f"Adresse du visiteur (Répondre lui écrit directement) : {visitor}\n\n"
        f"{text.strip()}\n"
    )
    return MailMessage(to=to, subject=SUBJECT, text=body, reply_to=visitor)


def _send(request: Request, message: MailMessage) -> None:
    try:
        service(request).mailer.send(message)
    except Exception:  # l'échec d'envoi ne change pas la réponse
        log.exception("envoi du message de contact impossible")


@router.post("/contact", status_code=202)
def contact(body: ContactBody, request: Request, background: BackgroundTasks) -> ContactResult:
    if not body.consent:
        raise HTTPException(
            status_code=422, detail="Cochez la case pour que nous puissions vous répondre."
        )
    try:
        visitor = checked_email(body.email)
    except InvalidEmailError as exc:
        raise HTTPException(
            status_code=422, detail="Cette adresse e-mail n'est pas valable."
        ) from exc
    accounts = service(request)
    to = accounts.settings.contact_to or accounts.settings.admin_email
    client = request.client.host if request.client else "-"
    if body.website or not to or not _limiter(request).allow(client):
        return ContactResult()  # piège rempli, aucun destinataire ou limite : rien ne part
    background.add_task(_send, request, contact_message(to, visitor, body.message))
    return ContactResult()
