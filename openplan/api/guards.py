"""Droits route par route (note 17, § 4) : **tout est refusé par défaut**.

Chaque route `/api` passe par `route_guard` (posé sur chaque routeur à l'inclusion) qui cherche
la route dans l'une des deux tables ci-dessous :

- `PROJECT_ROUTES` : les routes `/api/projects/{project_id}/…` et le rôle minimal du membre
  (`viewer` < `commenter` < `editor` < `owner`) ; non-membre → 404 (on ne révèle pas qu'un
  projet existe), rôle insuffisant → 403 ;
- `GLOBAL_ROUTES` : les autres, `public` (sans session), `user` (session) ou `admin`.

Une route absente des deux tables est refusée (403) quand les comptes sont actifs. Un test
parcourt toutes les routes de l'application et échoue si l'une d'elles n'a pas la garde, n'est
pas dans une table, ou si une table nomme une route qui n'existe plus. Une route nouvelle peut
aussi déclarer `Depends(project_member(Role.EDITOR))` elle-même.

Sans comptes (mode local, ou API exposée par jeton) : la garde ne refuse rien, comme avant.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request

from openplan.api.access import AccountsBackend, AuthUser, accounts_of, signed_in_user
from openplan.scene.store import ProjectStore, ProjectSummary


class Role(IntEnum):
    VIEWER = 1
    COMMENTER = 2
    EDITOR = 3
    OWNER = 4


ROLE_BY_NAME = {role.name.lower(): role for role in Role}

Policy = Literal["public", "user", "admin", "legacy"]

API_PREFIX = "/api"
P = API_PREFIX + "/projects/{project_id}"
L = P + "/levels/{level_id}"
V, E, O = Role.VIEWER, Role.EDITOR, Role.OWNER  # noqa: E741 - lisibilité de la table

PROJECT_ROUTES: dict[tuple[str, str], Role] = {
    # Projet
    ("GET", P): V,
    ("GET", P + "/role"): V,  # B1d : le front masque ce que le rôle ne permet pas
    ("PATCH", P): E,  # l'accord pour un modèle distant exige en plus `owner` (settings.py)
    ("DELETE", P): O,
    ("POST", P + "/name/auto"): E,
    ("GET", P + "/thumbnail"): V,
    ("GET", P + "/cover"): V,
    ("GET", P + "/journal"): V,
    ("DELETE", P + "/journal"): O,
    ("POST", P + "/journal/ui"): V,  # mesure d'usage de l'écran, ne touche pas le projet
    ("GET", P + "/metrics"): V,
    ("GET", P + "/network-log"): V,
    ("GET", P + "/validation"): V,
    ("POST", P + "/undo"): E,
    ("POST", P + "/redo"): E,
    ("POST", P + "/levels"): E,
    ("GET", P + "/jobs/{job_id}"): V,
    ("DELETE", P + "/jobs/{job_id}"): E,  # annuler un calcul en attente (file d'attente)
    # Espace de travail et fichiers joints
    ("GET", P + "/board"): V,
    ("PUT", P + "/board"): E,
    ("GET", P + "/media"): V,
    ("POST", P + "/media"): E,
    ("GET", P + "/media/{media_id}/thumb"): V,
    ("GET", P + "/media/{media_id}/file"): V,
    ("DELETE", P + "/media/{media_id}"): E,
    # Photos
    ("POST", P + "/images"): E,
    ("GET", P + "/images/{image_id}/file"): V,
    ("GET", P + "/images/{image_id}/quality"): V,
    ("DELETE", P + "/images/{image_id}"): E,
    ("POST", P + "/images/{image_id}/perceive"): E,
    ("POST", P + "/images/{image_id}/inspire"): E,
    ("POST", P + "/images/{image_id}/depth"): E,
    ("POST", P + "/images/{image_id}/attic"): E,
    # Mesures
    ("POST", P + "/measurements"): E,
    ("DELETE", P + "/measurements/{measurement_id}"): E,
    # Exports
    ("GET", P + "/exports/json"): V,
    ("GET", P + "/exports/svg"): V,
    ("GET", P + "/exports/dxf"): V,
    ("GET", P + "/exports/section-pdf"): V,
    ("GET", P + "/exports/pdf"): V,
    ("GET", P + "/exports/glb"): V,
    ("GET", P + "/exports/archive"): V,
    # Rendus
    ("GET", P + "/renders"): V,
    ("PATCH", P + "/renders/{run_id}"): E,
    ("DELETE", P + "/renders/{run_id}"): E,
    ("POST", P + "/renders/{run_id}/restore"): E,
    ("GET", P + "/renders/{run_id}/pick"): V,
    ("GET", P + "/renders/{run_id}/outline.png"): V,
    ("GET", P + "/renders/favorites.zip"): V,
    ("POST", P + "/renders/{run_id}/finish"): E,
    ("POST", P + "/renders/{run_id}/catalogue"): E,
    ("GET", P + "/contextual"): V,
    # Artefacts des calculs
    ("GET", P + "/runs/{run_id}/blender/{name}"): V,
    ("GET", P + "/runs/{run_id}/openings/{name}"): V,
    ("GET", P + "/runs/{run_id}/detection/{name}"): V,
    ("GET", P + "/runs/{run_id}/intention/{name}"): V,
    ("GET", P + "/runs/{run_id}/perception/{name}"): V,
    ("GET", P + "/runs/{run_id}/inspiration/{name}"): V,
    ("GET", P + "/runs/{run_id}/depth/{name}"): V,
    ("GET", P + "/runs/{run_id}/attic/{name}"): V,
    ("GET", P + "/runs/{run_id}/layout/{name}"): V,
    ("GET", P + "/runs/{run_id}/contextual/{name}"): V,
    # Assistant
    ("GET", P + "/assistant/thread"): V,
    ("POST", L + "/assistant/preview"): E,
    ("POST", L + "/assistant/say"): E,
    ("POST", L + "/assistant/act"): E,
    ("POST", L + "/rooms/{room_id}/instruction"): E,
    # Niveau : plan, calibration, détection, aménagement, rendu
    ("POST", L + "/starter"): E,
    ("POST", L + "/programme"): E,
    ("POST", L + "/area-guard"): E,
    ("POST", L + "/area-guard/undo"): E,
    ("POST", L + "/ops"): E,
    ("GET", L + "/derived"): V,
    ("GET", L + "/bake"): V,
    ("POST", L + "/calibrate"): E,
    ("POST", L + "/recalibrate/dimensions/preview"): E,
    ("POST", L + "/recalibrate/dimensions"): E,
    ("POST", L + "/recalibrate"): E,
    ("POST", L + "/detect"): E,
    ("POST", L + "/detect-depth"): E,
    ("POST", L + "/images/{image_id}/openings"): E,
    ("POST", L + "/images/{image_id}/autoplan"): E,
    ("POST", L + "/views/{camera_id}/adopt"): E,
    ("POST", L + "/views/{camera_id}/rotate"): E,
    ("POST", L + "/views/auto"): E,
    ("POST", L + "/brief/preview"): E,
    ("POST", L + "/brief"): E,
    ("POST", L + "/brief/zones"): E,
    ("POST", L + "/layouts"): E,
    ("POST", L + "/layouts/apply"): E,
    ("POST", L + "/layouts/critique/preview"): E,
    ("POST", L + "/layouts/critique"): E,
    ("POST", L + "/layouts/revise"): E,
    ("POST", L + "/layouts/render"): E,
    ("POST", L + "/furniture/check"): E,
    ("POST", L + "/furniture/{element_id}/check"): E,
    ("POST", L + "/elements/{element_id}/check"): E,
    ("POST", L + "/attic"): E,
    ("POST", L + "/import-plan/preview"): E,
    ("POST", L + "/import-plan/preview-image"): E,
    ("POST", L + "/import-plan"): E,
    ("POST", L + "/contextual/prompt"): E,
    ("POST", L + "/contextual"): E,
    ("GET", L + "/render/estimate"): V,
    ("GET", L + "/cameras/architect"): V,
    ("GET", L + "/cameras/section"): V,
    ("POST", L + "/autopilot"): E,
    ("POST", L + "/render/all"): E,
    ("POST", L + "/render/views"): E,
    ("POST", L + "/cameras/{camera_id}/render"): E,
    ("POST", L + "/blender"): E,
    ("POST", L + "/render"): E,
}

GLOBAL_ROUTES: dict[tuple[str, str], Policy] = {
    ("GET", "/api/health"): "public",
    ("POST", "/api/auth/email/start"): "public",
    ("POST", "/api/auth/email/verify"): "public",
    ("GET", "/api/auth/login"): "public",
    ("POST", "/api/auth/login/code"): "public",
    ("POST", "/api/auth/login/verify"): "public",
    ("POST", "/api/auth/logout"): "public",
    # Mailer `fake` seulement (tests sur la boucle locale) : absente sinon.
    ("GET", "/api/test/mail/last-code"): "public",
    ("GET", "/api/auth/me"): "user",
    ("POST", "/api/auth/logout-all"): "user",
    ("POST", "/api/admin/users/{user_id}/sessions/revoke"): "admin",
    # Projets de l'utilisateur (liste filtrée par appartenance, création = propriétaire)
    ("GET", "/api/projects"): "user",
    ("POST", "/api/projects"): "user",
    # Lecture de l'installation et du catalogue
    ("GET", "/api/ai"): "user",
    ("GET", "/api/models"): "user",
    ("GET", "/api/device"): "user",
    ("GET", "/api/blender"): "user",
    ("GET", "/api/jobs/running"): "user",
    ("GET", "/api/catalog"): "user",
    ("GET", "/api/catalog/symbols"): "user",
    ("GET", "/api/catalog/{asset_id}/preview"): "user",
    ("GET", "/api/catalog/{asset_id}/model"): "user",
    ("POST", "/api/instruction/preview"): "user",
    ("GET", "/api/home/settings"): "user",
    # Réglages du serveur et bilan de l'installation
    ("PUT", "/api/home/settings"): "admin",
    ("GET", "/api/doctor"): "admin",
    # Cookie de lecture de l'API exposée par jeton (note 16) : absent avec les comptes
    ("POST", "/api/session"): "legacy",
    ("DELETE", "/api/session"): "legacy",
}

NOT_A_MEMBER = "projet inconnu : {}"
NO_RULE = "route sans droits déclarés : refusée"


def _route_key(request: Request) -> tuple[str, str]:
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    if not path.startswith(API_PREFIX + "/"):
        # Les routeurs inclus gardent leur chemin sans le préfixe d'inclusion (`/api`).
        path = API_PREFIX + path
    method = "GET" if request.method == "HEAD" else request.method
    return method, path


def _project_id(request: Request) -> UUID:
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
    name = accounts.role_name(project_id, user.id)
    if name is None:
        raise HTTPException(status_code=404, detail=NOT_A_MEMBER.format(project_id))
    if ROLE_BY_NAME[name] < role:
        raise HTTPException(
            status_code=403, detail=f"droits insuffisants : rôle « {role.name.lower()} » requis"
        )
    return user


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
            check_member(request, accounts, _project_id(request), self.role)


def project_member(role: Role) -> ProjectMember:
    return ProjectMember(role)


def route_guard(request: Request) -> None:
    """Garde de toutes les routes `/api` (voir le haut du module)."""
    accounts = accounts_of(request)
    if accounts is None:
        return
    key = _route_key(request)
    role = PROJECT_ROUTES.get(key)
    if role is not None:
        check_member(request, accounts, _project_id(request), role)
        return
    policy = GLOBAL_ROUTES.get(key)
    if policy == "public":
        return
    if policy is None or policy == "legacy":
        raise HTTPException(status_code=403, detail=NO_RULE)
    user = signed_in_user(request)
    if policy == "admin" and not user.is_admin:
        raise HTTPException(status_code=403, detail="réservé à un administrateur")


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
    ids = visible_ids(request)
    summaries = store.list()
    return summaries if ids is None else [s for s in summaries if s.id in ids]


def register_project(request: Request, project_id: UUID, name: str) -> None:
    """Création avec les comptes : la ligne du projet et son propriétaire."""
    accounts = accounts_of(request)
    if accounts is not None:
        accounts.add_project(project_id, signed_in_user(request).id, name)


def forget_project(request: Request, project_id: UUID) -> None:
    accounts = accounts_of(request)
    if accounts is not None:
        accounts.remove_project(project_id)
