"""Envoi des codes de connexion (note 17, § 2 ; avenant B1b2) derrière une seule interface
`Mailer`.

- `console` : le code est écrit dans le journal du serveur (développement, essai local) ;
- `fake` : boîte aux lettres en mémoire, lue par les tests et les e2e ;
- `smtp_api` : l'API transactionnelle HTTPS de Brevo (`POST /v3/smtp/email`).

B1b2 (décision PO du 18 oct.) : Brevo peut réécrire et journaliser les liens d'un e-mail, et
son API ne permet pas de couper le suivi message par message ; le secret ne voyage donc plus
dans un lien. L'e-mail ne porte qu'un code à 6 chiffres, en TEXTE SEUL (pas de HTML : pas de
pixel d'ouverture, rien à réécrire), avec `contactPixelTrackingConsent: false` pour le
destinataire (événements anonymisés si le consentement par contact est actif sur le compte).

Aucune clé n'est dans le dépôt : OPENPLAN_BREVO_API_KEY vient du `.env` du VPS (infra).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

from openplan.brand import APP_NAME

log = logging.getLogger("openplan.mail")

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
BREVO_TIMEOUT_S = 10.0


class MailerNotConfiguredError(RuntimeError):
    """Le service d'envoi choisi n'est pas configuré (clé absente)."""


class MailSendError(RuntimeError):
    """Le service d'envoi a refusé le message (le code HTTP seul, jamais la clé)."""


@dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    text: str
    code: str = field(default="", repr=False)
    """Le code seul, pour la console et la boîte des tests ; jamais journalisé ailleurs."""
    reply_to: str | None = None
    """Formulaire de contact : l'adresse du visiteur, pour lui répondre directement."""


class Mailer(Protocol):
    def send(self, message: MailMessage) -> None: ...


def mask_address(address: str) -> str:
    """`a***@example.org` : assez pour s'y retrouver dans un journal de développement."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


class ConsoleMailer:
    """Développement : le code part dans le journal du serveur, rien ne sort de la machine."""

    def send(self, message: MailMessage) -> None:
        if not message.code:
            # KAN-55 : ni l'adresse, même masquée, ni l'objet (qui peut nommer un projet).
            log.warning("un message (envoi « console », rien n'est parti)")
            return
        log.warning(
            "code de connexion (envoi « console », rien n'est parti) pour %s : %s",
            mask_address(message.to),
            message.code,
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

    def last_code_to(self, address: str) -> MailMessage | None:
        """Le dernier code de connexion (KAN-55 : une invitation n'en porte pas)."""
        with self._lock:
            found = [m for m in self.outbox if m.to == address and m.code]
        return found[-1] if found else None


class BrevoApiMailer:
    """API transactionnelle de Brevo : texte seul, suivi par contact refusé, clé en en-tête."""

    def __init__(self, sender: str, api_key: str | None = None) -> None:
        self.sender = sender
        self.api_key = api_key

    def payload(self, message: MailMessage) -> dict[str, object]:
        """Le corps JSON envoyé : jamais de `htmlContent` (B1b2)."""
        payload: dict[str, object] = {
            "sender": {"name": APP_NAME, "email": self.sender},
            "to": [{"email": message.to, "contactPixelTrackingConsent": False}],
            "subject": message.subject,
            "textContent": message.text,
        }
        if message.reply_to:
            payload["replyTo"] = {"email": message.reply_to}
        return payload

    def send(self, message: MailMessage) -> None:
        if not self.api_key:
            msg = "OPENPLAN_BREVO_API_KEY absent : l'envoi par Brevo n'est pas configuré"
            raise MailerNotConfiguredError(msg)
        import httpx

        response = httpx.post(
            BREVO_ENDPOINT,
            json=self.payload(message),
            headers={"api-key": self.api_key, "accept": "application/json"},
            timeout=BREVO_TIMEOUT_S,
        )
        if response.status_code >= 300:
            msg = f"Brevo a refusé l'envoi (HTTP {response.status_code})"
            raise MailSendError(msg)


def build_mailer(kind: str, sender: str, api_key: str | None = None) -> Mailer:
    if kind == "console":
        return ConsoleMailer()
    if kind == "fake":
        return FakeMailer()
    if kind == "smtp_api":
        if not api_key:
            msg = (
                "OPENPLAN_MAILER=smtp_api : OPENPLAN_BREVO_API_KEY est absent ; garder "
                "« console » tant que la clé n'est pas posée sur le serveur"
            )
            raise MailerNotConfiguredError(msg)
        return BrevoApiMailer(sender, api_key)
    msg = f"OPENPLAN_MAILER inconnu : {kind}"
    raise ValueError(msg)


def login_message(to: str, code: str, minutes: int) -> MailMessage:
    """Le message ne contient que le code, sa durée et « ne pas répondre » : aucun lien."""
    subject = f"Votre code de connexion à {APP_NAME} : {code}"
    text = (
        f"Bonjour,\n\nVotre code de connexion à {APP_NAME} :\n\n    {code}\n\n"
        f"Ce code expire dans {minutes} minutes.\n\n"
        "Si vous n'avez rien demandé, ignorez ce message.\n"
        "Ceci est un envoi automatique : merci de ne pas répondre.\n"
    )
    return MailMessage(to=to, subject=subject, text=text, code=code)


def invitation_message(
    to: str, inviter: str, project: str, role: str, app_url: str, days: int
) -> MailMessage:
    """KAN-55 : invitation nominative, pour une adresse sans compte. Texte seul et aucun
    secret (B1b2) : l'adresse publique de l'application, sans jeton ; c'est la connexion avec
    cette adresse qui ouvre le projet."""
    subject = f"{inviter} vous invite sur « {project} » ({APP_NAME})"
    text = (
        f"Bonjour,\n\n{inviter} vous invite sur « {project} » dans {APP_NAME}, "
        f"comme {role}.\n\n"
        f"Pour accepter, connectez-vous avec cette adresse e-mail :\n\n    {app_url}\n\n"
        "Un code de connexion vous sera envoyé ; le projet apparaîtra dans vos espaces de "
        f"travail. L'invitation expire dans {days} jours.\n\n"
        "Si vous ne connaissez pas cette personne, ignorez ce message.\n"
        "Ceci est un envoi automatique : merci de ne pas répondre.\n"
    )
    return MailMessage(to=to, subject=subject, text=text)
