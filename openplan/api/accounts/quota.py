"""Place disque par compte (KAN-12), avec les comptes seulement.

- La place d'un compte est la taille sur disque des projets dont il est le premier
  propriétaire (photos, fichiers joints, plans, rendus, tableau), mesurée par le serveur à
  chaque demande : une suppression libère la place tout de suite.
- Limite : OPENPLAN_QUOTA_MB (500 par défaut ; 0 = sans limite).
- Un envoi (photo, fichier joint, plan importé) qui ferait dépasser la limite est refusé avant
  toute écriture (413) ; un calcul qui écrit des fichiers (rendus, détections, chemin
  automatique) est refusé AVANT son lancement quand la place est pleine : il n'échoue jamais à
  moitié. Un membre invité consomme la place du propriétaire du projet ; le refus le dit, sans
  révéler la place de ce dernier.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from openplan.api.access import AccountsBackend, AuthUser, accounts_of, signed_in_user
from openplan.api.policy.registry import LAUNCHES, LINKS, UPLOADS

router = APIRouter(tags=["comptes"])

MB = 1024 * 1024
# KAN-105 (L1) : les trois tables sont dérivées du registre des politiques (maillons
# `SPACE_UPLOAD`, `SPACE_LINK`, `SPACE_LAUNCH`), plus écrites ici.
# - UPLOADS : envois, refusés si la place utilisée plus le corps reçu dépasserait la limite ;
# - LINKS (KAN-98 L3) : une image d'un lien collé ; le corps reçu n'est que l'adresse, la place
#   réservée est donc la taille la plus grande permise pour cette image (LINK_RESERVE) ;
# - LAUNCHES : calculs qui écrivent des fichiers, refusés avant le lancement si la place est
#   pleine (LAUNCH_RESERVE).
LINK_RESERVE = 10 * MB
"""Comme `link_fetch.MAX_IMAGE_BYTES` (l'API ne lit pas l'adaptateur réseau ici)."""
LAUNCH_RESERVE = 20 * MB
"""Place réservée à un calcul avant son lancement (un jeu de rendus et leurs vignettes) : il
est refusé si la place utilisée plus cette réserve dépasserait la limite."""


def folder_bytes(folder: Path) -> int:
    """Taille des fichiers d'un dossier, sous-dossiers compris (liens non suivis)."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:  # effacé pendant le parcours
                continue
    return total


def used_bytes(request: Request, accounts: AccountsBackend, user_id: UUID) -> int:
    store = request.app.state.store
    return sum(folder_bytes(store.project_dir(pid)) for pid in accounts.paid_project_ids(user_id))


def quota_bytes(request: Request, payer: UUID | None = None) -> int | None:
    """La limite de ce compte : OPENPLAN_QUOTA_MB, et KAN-104 L2, pour un compte créé par
    l'inscription, au plus OPENPLAN_SIGNUP_QUOTA_MB (200 Mo par défaut)."""
    settings = request.app.state.settings
    limits = [settings.quota_mb] if settings.quota_mb > 0 else []
    if payer is not None and settings.signup_quota_mb > 0:
        signed_up = getattr(accounts_of(request), "self_signed_up", None)
        if callable(signed_up) and signed_up(payer):
            limits.append(settings.signup_quota_mb)
    return min(limits) * MB if limits else None


def mb(n: int) -> str:
    return f"{n / MB:.0f} Mo"


def full(user: AuthUser, payer: UUID, used: int, limit: int) -> HTTPException:
    if payer == user.id:
        detail = (
            f"Votre espace est plein ({mb(used)} sur {mb(limit)}) : "
            "supprimez des fichiers ou des projets."
        )
    else:
        detail = (
            "L'espace du propriétaire de ce projet est plein : "
            "il doit libérer de la place avant tout ajout."
        )
    return HTTPException(status_code=413, detail=detail)


def guard_space(
    request: Request, accounts: AccountsBackend, key: tuple[str, str], user: AuthUser
) -> None:
    """Appelée par la garde des routes (comptes actifs) : envoi ou calcul refusé (413) quand
    la place du propriétaire du projet serait dépassée."""
    if key not in UPLOADS and key not in LAUNCHES and key not in LINKS:
        return
    payer = accounts.payer_of(UUID(str(request.path_params["project_id"])))
    if payer is None:
        return
    limit = quota_bytes(request, payer)
    if limit is None:
        return
    used = used_bytes(request, accounts, payer)
    if key in UPLOADS:
        try:
            incoming = max(0, int(request.headers.get("content-length", "0")))
        except ValueError:
            incoming = 0
    elif key in LINKS:
        incoming = min(LINK_RESERVE, limit)
    else:
        # Un calcul ne connaît pas d'avance la taille de ce qu'il écrira : on lui réserve
        # LAUNCH_RESERVE, pour qu'il ne dépasse jamais la limite en cours de route.
        incoming = min(LAUNCH_RESERVE, limit)
    if used + incoming > limit:
        raise full(user, payer, used, limit)


@router.get("/api/auth/usage")
def usage(request: Request) -> dict[str, Any]:
    """Ma place : utilisée (octets) et limite (null : sans limite)."""
    user = signed_in_user(request)
    accounts = accounts_of(request)
    used = 0 if accounts is None else used_bytes(request, accounts, user.id)
    return {"used_bytes": used, "quota_bytes": quota_bytes(request, user.id)}
