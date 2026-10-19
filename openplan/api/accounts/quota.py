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

router = APIRouter(tags=["comptes"])

MB = 1024 * 1024
P = "/api/projects/{project_id}"
L = P + "/levels/{level_id}"
UPLOADS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", P + "/images"),
        ("POST", P + "/media"),
    }
)
"""Envois : refusés si la place utilisée plus le corps reçu dépasserait la limite."""
LAUNCHES: frozenset[tuple[str, str]] = frozenset(
    ("POST", route)
    for route in (
        P + "/images/{image_id}/perceive",
        P + "/images/{image_id}/inspire",
        P + "/images/{image_id}/depth",
        P + "/images/{image_id}/attic",
        P + "/renders/{run_id}/finish",
        P + "/renders/{run_id}/catalogue",
        L + "/detect",
        L + "/detect-depth",
        L + "/images/{image_id}/openings",
        L + "/images/{image_id}/autoplan",
        L + "/layouts/render",
        L + "/contextual",
        L + "/autopilot",
        L + "/render/all",
        L + "/render/views",
        L + "/cameras/{camera_id}/render",
        L + "/blender",
        L + "/render",
    )
)
"""Calculs qui écrivent des fichiers : refusés avant le lancement si la place est pleine."""
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


def quota_bytes(request: Request) -> int | None:
    mb: int = request.app.state.settings.quota_mb
    return mb * MB if mb > 0 else None


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
    if key not in UPLOADS and key not in LAUNCHES:
        return
    limit = quota_bytes(request)
    if limit is None:
        return
    payer = accounts.payer_of(UUID(str(request.path_params["project_id"])))
    if payer is None:
        return
    used = used_bytes(request, accounts, payer)
    if key in UPLOADS:
        try:
            incoming = max(0, int(request.headers.get("content-length", "0")))
        except ValueError:
            incoming = 0
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
    return {"used_bytes": used, "quota_bytes": quota_bytes(request)}
