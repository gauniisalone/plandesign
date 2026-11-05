"""Droits route par route (note 17, § 4) : **tout est refusé par défaut**.

KAN-105 (L1) : les droits se déclarent dans le registre `openplan.api.policy.registry`, une
ligne par route (cas d'usage, règle d'accès, maillons en plus) ; la garde `route_guard`
(`openplan.api.policy.chain`, posée sur chaque routeur à l'inclusion) en compose la chaîne. Ce
module en garde les noms d'avant, dérivés du registre :

- `PROJECT_ROUTES` : les routes `/api/projects/{project_id}/…` et le rôle minimal du membre
  (`viewer` < `commenter` < `editor` < `owner`) ; non-membre → 404 (on ne révèle pas qu'un
  projet existe), rôle insuffisant → 403 ;
- `GLOBAL_ROUTES` : les autres, `public` (sans session), `user` (session) ou `admin`, et les
  jetons du poste (`worker`) et du module navigateur (`extension`, rôle dans `EXTENSION_ROLES`).

Une route absente du registre est refusée (403) quand les comptes sont actifs. Un test
parcourt toutes les routes de l'application et échoue si l'une d'elles n'a pas la garde, n'est
pas déclarée, ou si le registre nomme une route qui n'existe plus. Une route nouvelle peut
aussi déclarer `Depends(project_member(Role.EDITOR))` elle-même.

Les contrôles faits à l'intérieur des routes passent par ici (`require_role`, `role_of`,
`visible_ids`…), en attendant d'entrer dans la chaîne (KAN-105, L2).

Sans comptes (mode local, ou API exposée par jeton) : la garde ne refuse rien, comme avant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request

from openplan.api.access import accounts_of, signed_in_user
from openplan.api.policy.chain import (
    NO_RULE,
    NOT_A_MEMBER,
    check_member,
    check_role,
    project_id_of,
    route_guard,
)
from openplan.api.policy.registry import (
    API_PREFIX,
    EXTENSION_ROLES,
    EXTENSION_SPACE_KEY,
    GLOBAL_ROUTES,
    PROJECT_ROUTES,
    ROLE_BY_NAME,
    L,
    P,
    Policy,
    Role,
)
from openplan.api.refusal import Refusal
from openplan.scene.store import ProjectStore, ProjectSummary

__all__ = [
    "API_PREFIX",
    "EXTENSION_ROLES",
    "EXTENSION_SPACE_KEY",
    "GLOBAL_ROUTES",
    "NOT_A_MEMBER",
    "NO_RULE",
    "PROJECT_ROUTES",
    "ROLE_BY_NAME",
    "L",
    "P",
    "Policy",
    "ProjectMember",
    "Role",
    "check_member",
    "check_role",
    "forget_project",
    "project_member",
    "register_project",
    "require_role",
    "role_of",
    "route_guard",
    "visible_ids",
    "visible_summaries",
]


def require_role(request: Request, project_id: UUID, role: Role) -> None:
    """À l'intérieur d'une route, pour un champ qui demande plus que la route (comptes
    actifs seulement)."""
    accounts = accounts_of(request)
    if accounts is not None:
        check_member(request, accounts, project_id, role)


class ProjectMember:
    """Dépendance FastAPI : l'utilisateur est membre du projet de la route avec `role`."""

    def __init__(self, role: Role) -> None:
        self.role = role

    def __call__(self, request: Request) -> None:
        accounts = accounts_of(request)
        if accounts is not None:
            check_member(request, accounts, project_id_of(request), self.role)


def project_member(role: Role) -> ProjectMember:
    return ProjectMember(role)


# --- liste et création des projets --------------------------------------------------


def role_of(request: Request, project_id: UUID) -> str:
    """Rôle de l'utilisateur sur le projet ; `owner` sans comptes (tout est permis)."""
    accounts = accounts_of(request)
    if accounts is None:
        return "owner"
    name = accounts.role_name(project_id, signed_in_user(request).id)
    if name is None:
        raise HTTPException(status_code=404, detail=NOT_A_MEMBER.format(project_id))
    return name


def visible_ids(request: Request) -> set[UUID] | None:
    """Projets que l'utilisateur peut voir ; None sans comptes (tous)."""
    accounts = accounts_of(request)
    if accounts is None:
        return None
    return accounts.project_ids_of(signed_in_user(request).id)


def visible_summaries(request: Request, store: ProjectStore) -> list[ProjectSummary]:
    """Projets visibles, avec le rôle de l'utilisateur (`owner` sans comptes) : la galerie
    masque « Renommer » et « Supprimer » selon le rôle (B1e)."""
    accounts = accounts_of(request)
    summaries = store.list()
    if accounts is None:
        return summaries
    roles = accounts.roles_of(signed_in_user(request).id)
    return [s.model_copy(update={"role": roles[s.id]}) for s in summaries if s.id in roles]


def register_project(request: Request, project_id: UUID, name: str) -> None:
    """Création avec les comptes : la ligne du projet et son propriétaire."""
    accounts = accounts_of(request)
    if accounts is not None:
        user = signed_in_user(request)
        _check_project_cap(request, accounts, user.id)
        accounts.add_project(project_id, user.id, name)


def _check_project_cap(request: Request, accounts: object, user_id: UUID) -> None:
    """KAN-104 L2 : un compte créé par l'inscription possède au plus
    OPENPLAN_SIGNUP_MAX_PROJECTS projets (5) ; refusé avant toute écriture (403)."""
    from openplan.api.accounts.repo import AccountsRepo

    cap: int = request.app.state.settings.signup_max_projects
    if cap <= 0 or not isinstance(accounts, AccountsRepo) or not accounts.self_signed_up(user_id):
        return
    if accounts.owned_project_count(user_id) >= cap:
        raise Refusal(
            403,
            f"Votre compte peut avoir {cap} projets : supprimez-en un pour en créer un autre.",
            "project_cap",
        )


def forget_project(request: Request, project_id: UUID) -> None:
    accounts = accounts_of(request)
    if accounts is not None:
        accounts.remove_project(project_id)
        # B2a : plus personne ne reste connecté à un projet supprimé.
        from anyio import from_thread

        from openplan.api.accounts.live import hub_of

        from_thread.run(hub_of(request.app).evict, project_id, None)
