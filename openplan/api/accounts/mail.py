"""Envoi des liens de connexion (note 17, § 2) derrière une seule interface `Mailer`.

- `console` : le lien est écrit dans le journal du serveur (développement, essai local) ;
- `fake` : boîte aux lettres en mémoire, lue par les tests et les e2e ;
- `smtp_api` : squelette pour l'API HTTPS de Brevo (service pressenti, non choisi) : il sait
  construire la requête mais **n'envoie rien** tant que le service n'est pas choisi et branché.

Aucune clé n'est dans le dépôt : celle du service ira dans le `.env` du VPS.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

from openplan.brand import APP_NAME

log = logging.getLogger("openplan.mail")

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


class MailerNotConfiguredError(RuntimeError):
    """Le service d'envoi choisi n'est pas encore branché."""


@dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    text: str
    html: str
    link: str = field(repr=False)
    """Le lien seul, pour la console et la boîte des tests ; jamais journalisé ailleurs."""


class Mailer(Protocol):
    def send(self, message: MailMessage) -> None: ...


def mask_address(address: str) -> str:
    """`a***@example.org` : assez pour s'y retrouver dans un journal de développement."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


class ConsoleMailer:
    """Développement : le lien part dans le journal du serveur, rien ne sort de la machine."""

    def send(self, message: MailMessage) -> None:
        log.warning(
            "lien de connexion (envoi « console », rien n'est parti) pour %s : %s",
            mask_address(message.to),
            message.link,
        )


class FakeMailer:
    """Tests : les messages restent en mémoire, dans l'ordre d'envoi."""

    def __init__(self) -> None:
        self.outbox: list[MailMessage] = []
        self._lock = Lock()

    def send(self, message: MailMessage) -> None:
        with self._lock:
            self.outbox.append(message)

    def last_to(self, address: str) -> MailMessage | None:
        with self._lock:
            found = [m for m in self.outbox if m.to == address]
        return found[-1] if found else None


class BrevoApiMailer:
    """Squelette : la requête HTTPS de l'API transactionnelle de Brevo, sans envoi."""

    def __init__(self, sender: str, api_key: str | None = None) -> None:
        self.sender = sender
        self.api_key = api_key

    def payload(self, message: MailMessage) -> dict[str, object]:
        return {
            "sender": {"name": APP_NAME, "email": self.sender},
            "to": [{"email": message.to}],
            "subject": message.subject,
            "textContent": message.text,
            "htmlContent": message.html,
        }

    def send(self, message: MailMessage) -> None:
        del message
        msg = "envoi par Brevo pas encore branché (service d'envoi à choisir, note 17 § 13)"
        raise MailerNotConfiguredError(msg)


def build_mailer(kind: str, sender: str) -> Mailer:
    if kind == "console":
        return ConsoleMailer()
    if kind == "fake":
        return FakeMailer()
    if kind == "smtp_api":
        msg = (
            "OPENPLAN_MAILER=smtp_api : l'envoi par l'API de Brevo n'est pas encore branché ; "
            "garder « console » tant que le service d'envoi n'est pas choisi"
        )
        raise MailerNotConfiguredError(msg)
    msg = f"OPENPLAN_MAILER inconnu : {kind}"
    raise ValueError(msg)


def login_message(to: str, link: str, minutes: int) -> MailMessage:
    """Le message ne contient que le lien, sa durée et la phrase « ignorez ce message »."""
    subject = f"Votre lien de connexion à {APP_NAME}"
    text = (
        f"Bonjour,\n\nPour vous connecter à {APP_NAME}, ouvrez ce lien (valable {minutes} "
        f"minutes, une seule fois) :\n\n{link}\n\n"
        "Si vous n'avez rien demandé, ignorez ce message.\n"
    )
    safe = html.escape(link, quote=True)
    body = (
        '<!doctype html><html lang="fr"><body style="font-family:sans-serif">'
        f"<p>Bonjour,</p><p>Pour vous connecter à {APP_NAME}, ouvrez ce lien (valable "
        f"{minutes} minutes, une seule fois) :</p>"
        f'<p><a href="{safe}">Se connecter à {APP_NAME}</a></p>'
        "<p>Si vous n'avez rien demandé, ignorez ce message.</p></body></html>"
    )
    return MailMessage(to=to, subject=subject, text=text, html=body, link=link)
