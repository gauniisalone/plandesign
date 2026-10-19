"""Bail d'édition du plan (B2d, KAN-11) : une seule personne modifie un plan (un niveau) à la
fois, avec les comptes seulement.

- Prise implicite : une écriture d'une personne sur un plan (table `LEASED_ROUTES`) prend le
  bail s'il est libre ou expiré, et le prolonge ; seule, elle ne voit aucune différence.
- Tenu par un autre : l'écriture est refusée en 409 (« Bruno modifie ce plan »).
- Rendu : en quittant l'onglet Plan (`DELETE …/lease`), quand la personne n'a plus aucune
  connexion au projet, ou après `LEASE_S` sans geste. « Prendre la main » (`POST …/lease`)
  ne réussit que sur un bail libre ou expiré : personne n'arrache la main, pas même le
  propriétaire ; « Demander la main » prévient le seul tenant.
- Les écritures du système (chemin automatique, calculs lancés) ne passent pas par ces
  routes : jamais bloquées.
- En mémoire, dans le processus, comme la présence : perdu au redémarrage (le plan est alors
  libre). Les changements de tenant sont diffusés par le WebSocket du projet (« lease »).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from anyio.from_thread import run as run_from_thread
from fastapi import APIRouter, HTTPException, Request, Response

from openplan.api.access import AuthUser, signed_in_user

router = APIRouter(tags=["présence"])

LEASE_S = 120.0
"""Deux minutes sans geste : le bail expire."""
RENEW_NOTICE_S = 20.0
"""Une prolongation est annoncée aux autres au plus toutes les 20 s (l'expiration qu'ils
affichent reste juste à 20 s près)."""

L = "/api/projects/{project_id}/levels/{level_id}"
LEASED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    ("POST", L + suffix)
    for suffix in (
        "/ops",
        "/calibrate",
        "/recalibrate",
        "/recalibrate/dimensions",
        "/area-guard",
        "/area-guard/undo",
        "/layouts/apply",
        "/assistant/act",
        "/rooms/{room_id}/instruction",
        "/attic",
        "/import-plan",
        "/starter",
        "/programme",
    )
)
"""Écritures d'une personne sur un plan : elles prennent ou prolongent son bail."""
PROJECT_WIDE: frozenset[tuple[str, str]] = frozenset(
    {("POST", "/api/projects/{project_id}/undo"), ("POST", "/api/projects/{project_id}/redo")}
)
"""Annuler / rétablir du plan (historique du projet, tous plans confondus) : refusés tant
qu'une autre personne tient un plan du projet ; ils ne prennent pas de bail."""


@dataclass
class Lease:
    user_id: str
    name: str
    color: str
    until: float
    announced: float


class PlanLeases:
    """Baux des plans de chaque projet, protégés par un verrou (les gardes tournent dans des
    fils, la présence dans la boucle)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._leases: dict[tuple[UUID, str], Lease] = {}

    def _alive(self, key: tuple[UUID, str], now: float) -> Lease | None:
        lease = self._leases.get(key)
        if lease is not None and lease.until <= now:
            del self._leases[key]
            return None
        return lease

    def holder(self, project_id: UUID, level_id: str) -> Lease | None:
        with self._lock:
            return self._alive((project_id, level_id), self.clock())

    def claim(
        self, project_id: UUID, level_id: str, user_id: str, name: str, color: str
    ) -> tuple[Lease | None, bool]:
        """Prend ou prolonge le bail. Renvoie (tenant qui bloque, s'il y en a un ; s'il faut
        l'annoncer aux autres)."""
        key = (project_id, level_id)
        with self._lock:
            now = self.clock()
            current = self._alive(key, now)
            if current is not None and current.user_id != user_id:
                return current, False
            if current is None:
                self._leases[key] = Lease(user_id, name, color, now + LEASE_S, now)
                return None, True
            current.until = now + LEASE_S
            if now - current.announced >= RENEW_NOTICE_S:
                current.announced = now
                return None, True
            return None, False

    def other_than(self, project_id: UUID, user_id: str) -> Lease | None:
        """Un bail tenu par une autre personne sur un plan du projet."""
        with self._lock:
            now = self.clock()
            for key in [k for k in self._leases if k[0] == project_id]:
                lease = self._alive(key, now)
                if lease is not None and lease.user_id != user_id:
                    return lease
        return None

    def release(self, project_id: UUID, user_id: str, level_id: str | None = None) -> list[str]:
        """Rend les baux de cette personne (sur un plan, ou tous) ; renvoie les plans rendus."""
        with self._lock:
            gone = [
                k
                for k, lease in self._leases.items()
                if k[0] == project_id
                and lease.user_id == user_id
                and (level_id is None or k[1] == level_id)
            ]
            for key in gone:
                del self._leases[key]
        return [k[1] for k in gone]

    def state(self, project_id: UUID) -> dict[str, dict[str, Any]]:
        """Les plans tenus du projet, pour le « hello » de la présence."""
        with self._lock:
            now = self.clock()
            out: dict[str, dict[str, Any]] = {}
            for key in [k for k in self._leases if k[0] == project_id]:
                lease = self._alive(key, now)
                if lease is not None:
                    out[key[1]] = message(key[1], lease, now)
            return out


def message(level_id: str, lease: Lease | None, now: float) -> dict[str, Any]:
    """Le message « lease » : qui tient ce plan (ou personne), et pour combien de temps."""
    if lease is None:
        return {"type": "lease", "level_id": level_id, "holder": None}
    return {
        "type": "lease",
        "level_id": level_id,
        "holder": {"user_id": lease.user_id, "name": lease.name, "color": lease.color},
        "expires_in": round(max(0.0, lease.until - now), 1),
    }


def leases_of(app: Any) -> PlanLeases:
    leases: PlanLeases | None = getattr(app.state, "leases", None)
    if leases is None:
        leases = PlanLeases()
        app.state.leases = leases
    return leases


def _announce(request: Request, project_id: UUID, payload: dict[str, Any]) -> None:
    """Diffusion depuis une garde (fil de travail) vers la boucle de la présence."""
    from openplan.api.accounts.live import hub_of

    run_from_thread(hub_of(request.app).publish, project_id, payload)


def _peer(user: AuthUser) -> tuple[str, str, str]:
    from openplan.api.accounts.live import color_of, display_name

    return str(user.id), display_name(user), color_of(user.id)


def taken_by_other(lease: Lease) -> HTTPException:
    return HTTPException(status_code=409, detail=f"{lease.name} modifie ce plan")


def guard_plan_write(request: Request, key: tuple[str, str], user: AuthUser) -> None:
    """Appelée par la garde des routes (comptes actifs) : bail pris ou prolongé, ou 409."""
    if key in PROJECT_WIDE:
        project_id = UUID(str(request.path_params["project_id"]))
        other = leases_of(request.app).other_than(project_id, str(user.id))
        if other is not None:
            raise taken_by_other(other)
        return
    if key not in LEASED_ROUTES:
        return
    if key[1].endswith("/calibrate") and request.query_params.get("dry_run") in ("1", "true"):
        return  # un aperçu n'écrit rien
    project_id = UUID(str(request.path_params["project_id"]))
    level_id = str(request.path_params["level_id"])
    user_id, name, color = _peer(user)
    leases = leases_of(request.app)
    blocking, announce = leases.claim(project_id, level_id, user_id, name, color)
    if blocking is not None:
        raise taken_by_other(blocking)
    if announce:
        lease = leases.holder(project_id, level_id)
        _announce(request, project_id, message(level_id, lease, leases.clock()))


@router.post("/api/projects/{project_id}/levels/{level_id}/lease")
def take_lease(project_id: UUID, level_id: UUID, request: Request) -> dict[str, Any]:
    """« Prendre la main » : seulement sur un plan libre ou dont le bail a expiré."""
    user_id, name, color = _peer(signed_in_user(request))
    leases = leases_of(request.app)
    blocking, announce = leases.claim(project_id, str(level_id), user_id, name, color)
    if blocking is not None:
        raise taken_by_other(blocking)
    lease = leases.holder(project_id, str(level_id))
    payload = message(str(level_id), lease, leases.clock())
    if announce:
        _announce(request, project_id, payload)
    return payload


@router.delete("/api/projects/{project_id}/levels/{level_id}/lease", status_code=204)
def give_lease(project_id: UUID, level_id: UUID, request: Request) -> Response:
    """« Rendre la main », ou la sortie de l'onglet Plan : rend mon bail sur ce plan."""
    user_id = str(signed_in_user(request).id)
    leases = leases_of(request.app)
    for level in leases.release(project_id, user_id, str(level_id)):
        _announce(request, project_id, message(level, None, leases.clock()))
    return Response(status_code=204)


@router.post("/api/projects/{project_id}/levels/{level_id}/lease/request", status_code=204)
def ask_lease(project_id: UUID, level_id: UUID, request: Request) -> Response:
    """« Demander la main » : prévient le seul tenant (tous ses onglets), personne d'autre."""
    from openplan.api.accounts.live import hub_of

    user = signed_in_user(request)
    lease = leases_of(request.app).holder(project_id, str(level_id))
    if lease is not None and lease.user_id != str(user.id):
        _, name, _ = _peer(user)
        run_from_thread(
            hub_of(request.app).tell_user,
            project_id,
            lease.user_id,
            {"type": "lease.request", "level_id": str(level_id), "from": name},
        )
    return Response(status_code=204)
