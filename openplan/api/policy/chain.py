"""Chaîne des contrôles de chaque route `/api` (KAN-105, L1), lue dans le registre.

`route_guard` est posé sur chaque routeur à l'inclusion (`app.py`). Avec les comptes, il
trouve le cas d'usage de la route (`registry.BY_KEY`) et passe ses maillons dans l'ordre ;
chaque maillon a une seule responsabilité et laisse passer la requête ou l'arrête :

0. (KAN-105 L2) la fonction activée, en tête (`FEATURE_SHARE_LINKS`) : éteinte, 404 pour tous ;
1. la règle d'accès : session (`user`, `admin`), jeton du poste (`worker`), jeton du module
   (`extension`), ou membre du projet avec un rôle minimal (404 non-membre, 403 rôle) ;
   (KAN-126) puis l'offre Pro de la personne qui agit (`OFFER_PRO`, 403 `offer_pro`) ;
2. le bail du plan (B2d, 409) : il PREND ou prolonge le bail, il vient donc après la règle ;
3. la place du propriétaire (KAN-12 ; 403 `space_full` depuis KAN-105 L2) ;
4. la trace d'activité du jour (KAN-109 L2).

Les maillons d'avant la route restent à la périphérie, dans l'intergiciel de session
(`security.py`) : origine, débit des tentatives (`Link.AUTH_RATE`), porte et débit du poste
(`Link.WORKER_RATE`), porte du module. Le registre les déclare, ils ne changent pas de place.

Une route absente du registre, ou `legacy` (cookie de l'API exposée par jeton), est refusée
(403) quand les comptes sont actifs. Sans comptes (mode local, ou API exposée par jeton) : la
garde ne refuse rien, comme avant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from openplan.api.access import AccountsBackend, AuthUser, accounts_of, signed_in_user
from openplan.api.policy.registry import (
    API_PREFIX,
    BY_KEY,
    EXTENSION_SPACE_KEY,
    FEATURE_SHARE_LINKS,
    LEASES,
    OFFER_PRO,
    ROLE_BY_NAME,
    SPACE_LINK,
    SPACES,
    Policy,
    Role,
    UseCase,
)
from openplan.api.refusal import Refusal

NOT_A_MEMBER = "projet inconnu : {}"
NO_RULE = "route sans droits déclarés : refusée"


@dataclass
class Passage:
    """Une requête qui traverse la chaîne : la règle d'accès y pose le compte reconnu."""

    request: Request
    accounts: AccountsBackend
    key: tuple[str, str]
    user: AuthUser | None = None

    def known_user(self) -> AuthUser:
        if self.user is None:  # pragma: no cover - un maillon de règle passe toujours avant
            raise Refusal(403, NO_RULE, "no_rule")
        return self.user


@dataclass(frozen=True)
class Step:
    """Un maillon : son nom (lu par les tests et la revue) et ce qu'il vérifie."""

    label: str
    run: Callable[[Passage], None]


# --- contrôles partagés (aussi appelés depuis les routes, voir `guards`) ---------------


def route_key(request: Request) -> tuple[str, str]:
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    if not path.startswith(API_PREFIX + "/"):
        # Les routeurs inclus gardent leur chemin sans le préfixe d'inclusion (`/api`).
        path = API_PREFIX + path
    method = "GET" if request.method == "HEAD" else request.method
    return method, path


def project_id_of(request: Request) -> UUID:
    raw = request.path_params.get("project_id", "")
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=NOT_A_MEMBER.format(raw)) from exc


def check_member(
    request: Request, accounts: AccountsBackend, project_id: UUID, role: Role
) -> AuthUser:
    """Membre du projet avec au moins `role` : sinon 404 (non-membre) ou 403 (rôle)."""
    user = signed_in_user(request)
    check_role(accounts, project_id, user, role)
    return user


def check_role(accounts: AccountsBackend, project_id: UUID, user: AuthUser, role: Role) -> None:
    """Le contrôle de `check_member` pour un compte déjà connu (session ou jeton du module)."""
    name = accounts.role_name(project_id, user.id)
    if name is None:
        raise HTTPException(status_code=404, detail=NOT_A_MEMBER.format(project_id))
    if ROLE_BY_NAME[name] < role:
        raise Refusal(403, f"droits insuffisants : rôle « {role.name.lower()} » requis", "role")


def mark_active(request: Request, user_id: UUID) -> None:
    """KAN-109 L2 : la personne a utilisé l'application aujourd'hui (au plus un « active » par
    jour et par compte ; cache en mémoire)."""
    from openplan.api.accounts.events import EventLog

    events = getattr(request.app.state, "events", None)
    if isinstance(events, EventLog):
        events.active(user_id)


# --- maillons -------------------------------------------------------------------------


def _member(role: Role) -> Step:
    def run(p: Passage) -> None:
        p.user = check_member(p.request, p.accounts, project_id_of(p.request), role)

    return Step(f"membre ≥ {role.name.lower()}", run)


def _lease(p: Passage) -> None:
    from openplan.api.accounts.lease import guard_plan_write

    guard_plan_write(p.request, p.key, p.known_user())


def _space(p: Passage) -> None:
    from openplan.api.accounts.quota import guard_space

    guard_space(p.request, p.accounts, p.key, p.known_user())


def _offer_pro(p: Passage) -> None:
    from openplan.api.policy.offers import check_pro

    check_pro(p.request, p.known_user())


def _active(p: Passage) -> None:
    mark_active(p.request, p.known_user().id)


def _session(p: Passage) -> None:
    p.user = signed_in_user(p.request)


def _admin(p: Passage) -> None:
    if not p.known_user().is_admin:
        raise Refusal(403, "réservé à un administrateur", "admin")


def _worker(p: Passage) -> None:
    from openplan.api.worker_auth import require_worker

    require_worker(p.request)


def _extension_token(p: Passage) -> None:
    from openplan.api.accounts.extension import require_extension

    p.user = require_extension(p.request)


def _extension_role(role: Role) -> Step:
    def run(p: Passage) -> None:
        check_role(p.accounts, project_id_of(p.request), p.known_user(), role)

    return Step(f"membre ≥ {role.name.lower()} (jeton du module)", run)


def _extension_space(p: Passage) -> None:
    from openplan.api.accounts.quota import guard_space

    guard_space(p.request, p.accounts, EXTENSION_SPACE_KEY, p.known_user())


def _refuse(_p: Passage) -> None:
    raise Refusal(403, NO_RULE, "no_rule")


REFUSED: tuple[Step, ...] = (Step("refus : aucune règle", _refuse),)
ACTIVE = Step("activité du jour", _active)
SESSION = Step("session", _session)
GLOBAL_CHAINS: dict[Policy, tuple[Step, ...]] = {
    "public": (),
    "user": (SESSION, ACTIVE),
    "admin": (SESSION, Step("administrateur", _admin), ACTIVE),
    "worker": (Step("jeton du poste", _worker),),
    # `legacy` : le cookie de lecture de l'API par jeton n'existe pas avec les comptes.
    "legacy": REFUSED,
}
"""Les chaînes des politiques globales sans maillon en plus."""


def _share_links_on(p: Passage) -> None:
    from openplan.api.accounts.share_links import enabled

    if not enabled(p.request):
        raise HTTPException(status_code=404, detail="Not Found")


FEATURE_SHARE = Step("fonction activée : liens publics", _share_links_on)


def chain_of(entry: UseCase) -> tuple[Step, ...]:
    """Les maillons de la route, dans l'ordre, d'après sa ligne du registre. KAN-105 L2 : une
    fonction éteinte (`FEATURE_SHARE_LINKS`) arrête la requête en tête, avant la règle d'accès :
    le même 404 pour tous, quel que soit le rôle."""
    head = (FEATURE_SHARE,) if FEATURE_SHARE_LINKS in entry.links else ()
    return (*head, *_chain_after_feature(entry))


def _chain_after_feature(entry: UseCase) -> tuple[Step, ...]:
    access, links = entry.access, set(entry.links)
    if access.policy is None and access.role is not None:
        steps = [_member(access.role)]
        if OFFER_PRO in links:
            steps.append(Step("offre Pro", _offer_pro))
        if links & LEASES:
            steps.append(Step("bail du plan", _lease))
        if links & SPACES:
            steps.append(Step("place du propriétaire", _space))
        return (*steps, ACTIVE)
    if access.policy == "extension":
        steps = [Step("jeton du module", _extension_token)]
        if access.role is not None:
            steps.append(_extension_role(access.role))
        if SPACE_LINK in links:
            steps.append(Step("place du propriétaire", _extension_space))
        return (*steps, ACTIVE)
    if access.policy is None:
        return REFUSED
    return GLOBAL_CHAINS[access.policy]


CHAINS: dict[tuple[str, str], tuple[Step, ...]] = {
    key: chain_of(entry) for key, entry in BY_KEY.items()
}
"""La chaîne de chaque route, composée une fois au chargement."""


def route_guard(request: Request) -> None:
    """Garde de toutes les routes `/api` (voir le haut du module)."""
    accounts = accounts_of(request)
    if accounts is None:
        return
    key = route_key(request)
    passage = Passage(request, accounts, key)
    for step in CHAINS.get(key, REFUSED):
        step.run(passage)
