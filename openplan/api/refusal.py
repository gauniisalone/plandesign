"""KAN-105 (L2) : un refus lisible par l'application, en plus du message lu par la personne.

Le corps d'un refus garde `detail` (le texte en français, affiché tel quel par le front) et y
ajoute `reason`, une étiquette stable que le front lit pour réagir au bon cas (place pleine,
limite de jetons, déconnexion…) sans deviner d'après le code. Les débits ajoutent
`Retry-After`.

Raisons (`REASONS`) : une liste fermée, lue aussi par le front (`apps/web/src/api/client.ts`).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse

Reason = Literal[
    # 401
    "login_required",
    "bad_credentials",
    "token_required",
    # 403
    "origin",
    "role",
    "admin",
    "no_rule",
    "not_author",
    "project_cap",
    "token_cap",
    "space_full",
    "consent",
    "reauth",
    "renders_off",
    # 429
    "daily_quota",
    "rate_limited",
]
REASONS: tuple[str, ...] = Reason.__args__  # type: ignore[attr-defined]

COOKIE_CHALLENGE = {"WWW-Authenticate": "Cookie"}
"""Un 401 de session (cookie `pd_session`) : la même forme partout (KAN-105 L2, ligne 12)."""
BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}
"""Un 401 d'un jeton (API exposée, poste de rendu, module navigateur)."""


class Refusal(HTTPException):
    """Un refus HTTP avec sa raison : `{"detail": …, "reason": …}`, et ses en-têtes."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        reason: Reason,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.reason: Reason = reason


def body(detail: str, reason: Reason) -> dict[str, str]:
    return {"detail": detail, "reason": reason}


def response(
    status_code: int, detail: str, reason: Reason, headers: dict[str, str] | None = None
) -> JSONResponse:
    """La réponse d'un refus hors des routes (intergiciels, gestionnaires d'exception)."""
    return JSONResponse(status_code=status_code, content=body(detail, reason), headers=headers)


def as_response(exc: Refusal) -> JSONResponse:
    headers = dict(exc.headers) if exc.headers else None
    return response(exc.status_code, str(exc.detail), exc.reason, headers)


def seconds_to_midnight_utc(now: datetime | None = None) -> int:
    """Délai d'un quota du jour (`Retry-After`) : jusqu'à minuit UTC, comme le budget d'e-mails
    (`mail_budget`), au moins une seconde."""
    current = now or datetime.now(UTC)
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, math.ceil((midnight - current).total_seconds()))


def retry_after(seconds: float) -> dict[str, str]:
    """L'en-tête `Retry-After` d'un débit, en secondes entières (au moins 1)."""
    return {"Retry-After": str(max(1, math.ceil(seconds)))}
