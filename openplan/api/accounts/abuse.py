"""KAN-104 L2 : garde-fous des nouvelles portes publiques (conception, § 5).

- Domaines jetables : aucune inscription. La liste est courte et tenue dans le code ; elle se
  relit chaque mois (une ligne à ajouter, rien d'autre).
- Le plafond quotidien d'e-mails (D4) est celui de KAN-117 (`mail_budget`), classe `signup`.
"""

from __future__ import annotations

DISPOSABLE_DOMAINS = frozenset(
    {
        "10minutemail.com",
        "dispostable.com",
        "getnada.com",
        "guerrillamail.com",
        "jetable.org",
        "mailinator.com",
        "maildrop.cc",
        "sharklasers.com",
        "temp-mail.org",
        "tempmail.com",
        "trashmail.com",
        "yopmail.com",
        "yopmail.fr",
    }
)
"""Relue le 2 nov. 2026 ; à relire chaque mois."""


def disposable(address: str) -> bool:
    return address.rpartition("@")[2] in DISPOSABLE_DOMAINS
