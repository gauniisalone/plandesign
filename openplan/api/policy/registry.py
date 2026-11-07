"""Registre des politiques d'accès de l'API (KAN-105, L1) : **une ligne par route `/api`**.

Chaque ligne déclare :

- le **cas d'usage** : un nom stable (`ressource:méthode`, `project.…` pour un projet,
  `plan.…` pour un niveau), le même pour HTTP, et demain pour le WebSocket et le jeton du
  module (KAN-102, L3) ;
- la **règle d'accès** : membre du projet avec un rôle minimal (`viewer` < `commenter` <
  `editor` < `owner` ; non-membre 404, rôle insuffisant 403), ou une politique globale
  (`public`, `user`, `admin`, `legacy`, `worker`, `extension`) ;
- les **maillons en plus** de sa chaîne (`Link`) : fonction activée (404 pour tous, en tête),
  bail du plan (409), place du propriétaire (403 `space_full`, KAN-105 L2), débit (429).

La garde des routes (`openplan.api.policy.chain.route_guard`) lit ce registre et compose la
chaîne de chaque route, dans l'ordre des maillons. Les tables d'avant en sont **dérivées**
(`guards.PROJECT_ROUTES`, `lease.LEASED_ROUTES`, `quota.UPLOADS`, `worker_auth.RATE_LIMITED`,
`security.ACCOUNTS_PUBLIC`…) : plus aucune ne s'écrit à la main.

Une route absente du registre est refusée (403) quand les comptes sont actifs. Une route
nouvelle = une ligne ici ; `test_b1c_guards` et `test_kan105_policy_registry` le vérifient,
et la matrice `tests/accounts/access_matrix.md` montre le changement en revue.

Module pur, sans import de l'API : les couches qui lisent leur table (bail, place, poste,
session) l'importent sans cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Literal


class Role(IntEnum):
    VIEWER = 1
    COMMENTER = 2
    EDITOR = 3
    OWNER = 4


ROLE_BY_NAME = {role.name.lower(): role for role in Role}

Policy = Literal["public", "user", "admin", "legacy", "worker", "extension"]
"""`worker` (KAN-89) : le jeton du poste de rendu seulement (`api.worker_auth`), jamais une
session ; ce jeton n'ouvre aucune autre route. `extension` (KAN-102) : le jeton du module
navigateur seulement (`api.accounts.extension`), jamais la session ; ce jeton n'ouvre aucune
autre route."""


class Link(Enum):
    """Maillons en plus de la règle d'accès, dans l'ordre de la chaîne."""

    FEATURE_SHARE_LINKS = "fonction activée : liens publics (OPENPLAN_SHARE_LINKS ; 404 pour tous)"
    """KAN-105 L2 : EN TÊTE de la chaîne, avant la règle : éteinte, la route répond 404 à tous,
    lecteur compris (sinon 403 ou 404 selon le rôle révélerait qu'elle existe)."""
    LEASE = "bail du plan de l'URL (409)"
    LEASE_IMAGE = "bail du plan estimé depuis la photo de l'URL (409)"
    LEASE_PROJECT = "aucun plan du projet tenu par un autre (409)"
    SPACE_UPLOAD = "place du propriétaire : corps reçu (403)"
    SPACE_LINK = "place du propriétaire : réserve d'une image par lien (403)"
    SPACE_LAUNCH = "place du propriétaire : réserve d'un calcul (403)"
    WORKER_RATE = "débit du poste : 1 appel / 5 s par jeton (429, porte du poste)"
    AUTH_RATE = "débit des tentatives : 10 / min par adresse (429, intergiciel de session)"
    OFFER_PRO = "offre Pro de la personne qui agit (403 offer_pro)"
    """KAN-126 : la route entière est de l'offre Pro (exports pro)."""
    OFFER_PRO_OPS = "offre Pro pour les opérations de PRO_OPS (403 offer_pro, dans la route)"
    """KAN-126 : la route reste Essentielle ; seules les opérations Pro (`PRO_OPS`,
    `PUSH_PULL_FIELDS`) demandent l'offre Pro, contrôlées dans la route une fois le corps lu."""


FEATURE_SHARE_LINKS = Link.FEATURE_SHARE_LINKS
LEASE = Link.LEASE
LEASE_IMAGE = Link.LEASE_IMAGE
LEASE_PROJECT = Link.LEASE_PROJECT
SPACE_UPLOAD = Link.SPACE_UPLOAD
SPACE_LINK = Link.SPACE_LINK
SPACE_LAUNCH = Link.SPACE_LAUNCH
WORKER_RATE = Link.WORKER_RATE
AUTH_RATE = Link.AUTH_RATE
OFFER_PRO = Link.OFFER_PRO
OFFER_PRO_OPS = Link.OFFER_PRO_OPS
LEASES = frozenset({LEASE, LEASE_IMAGE, LEASE_PROJECT})
SPACES = frozenset({SPACE_UPLOAD, SPACE_LINK, SPACE_LAUNCH})


@dataclass(frozen=True)
class Access:
    """La règle d'accès : `policy` globale, ou membre avec `role` (policy None) ; le module
    navigateur (`extension`) peut exiger en plus un rôle sur le projet de la route."""

    policy: Policy | None
    role: Role | None = None


@dataclass(frozen=True)
class UseCase:
    name: str
    method: str
    path: str
    access: Access
    links: tuple[Link, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.method, self.path


def member(role: Role) -> Access:
    return Access(None, role)


def extension(role: Role) -> Access:
    return Access("extension", role)


PUBLIC = Access("public")
USER = Access("user")
ADMIN = Access("admin")
LEGACY = Access("legacy")
WORKER = Access("worker")
EXTENSION = Access("extension")


def uc(name: str, method: str, path: str, access: Access, *links: Link) -> UseCase:
    return UseCase(name, method, path, access, links)


API_PREFIX = "/api"
P = API_PREFIX + "/projects/{project_id}"
L = P + "/levels/{level_id}"
V, C, E, O = Role.VIEWER, Role.COMMENTER, Role.EDITOR, Role.OWNER  # noqa: E741 - lisibilité de la table

REGISTRY: tuple[UseCase, ...] = (
    # --- routes de projet : membre avec un rôle minimal (non-membre 404, rôle 403) ---
    # Projet
    uc("project:get", "GET", P, member(V)),
    # B1d : le front masque ce que le rôle ne permet pas
    uc("project.role:get", "GET", P + "/role", member(V)),
    # Partage (B1e) : le propriétaire seul
    uc("project.invitations:get", "GET", P + "/invitations", member(O)),
    uc("project.invitations:post", "POST", P + "/invitations", member(O)),
    uc("project.invitations:delete", "DELETE", P + "/invitations/{invitation_id}", member(O)),
    # KAN-55 : invitation nominative
    uc("project.invitations.email:post", "POST", P + "/invitations/email", member(O)),
    uc("project.members:get", "GET", P + "/members", member(O)),
    uc("project.members:patch", "PATCH", P + "/members/{user_id}", member(O)),
    uc("project.members:delete", "DELETE", P + "/members/{user_id}", member(O)),
    # l'accord pour un modèle distant exige en plus `owner` (settings.py)
    uc("project:patch", "PATCH", P, member(E)),
    uc("project:delete", "DELETE", P, member(O)),
    uc("project.name.auto:post", "POST", P + "/name/auto", member(E)),
    uc("project.thumbnail:get", "GET", P + "/thumbnail", member(V)),
    uc("project.cover:get", "GET", P + "/cover", member(V)),
    uc("project.journal:get", "GET", P + "/journal", member(V)),
    uc("project.journal:delete", "DELETE", P + "/journal", member(O)),
    # mesure d'usage de l'écran, ne touche pas le projet
    uc("project.journal.ui:post", "POST", P + "/journal/ui", member(V)),
    uc("project.metrics:get", "GET", P + "/metrics", member(V)),
    uc("project.network-log:get", "GET", P + "/network-log", member(V)),
    uc("project.validation:get", "GET", P + "/validation", member(V)),
    uc("project.undo:post", "POST", P + "/undo", member(E), LEASE_PROJECT),
    uc("project.redo:post", "POST", P + "/redo", member(E), LEASE_PROJECT),
    uc("project.levels:post", "POST", P + "/levels", member(E)),
    # KAN-91 : « Supprimer le plan »
    uc("plan:delete", "DELETE", P + "/levels/{level_id}", member(E)),
    uc("project.jobs:get", "GET", P + "/jobs/{job_id}", member(V)),
    # annuler un calcul en attente (file d'attente)
    uc("project.jobs:delete", "DELETE", P + "/jobs/{job_id}", member(E)),
    # Espace de travail et fichiers joints
    uc("project.board:get", "GET", P + "/board", member(V)),
    # compatibilité (onglets d'avant B2c)
    uc("project.board:put", "PUT", P + "/board", member(E)),
    # B2c : écritures par élément (KAN-91 : et liens, D3)
    uc("project.board.items:patch", "PATCH", P + "/board/items", member(E)),
    # KAN-91 : ce qu'une génération lit
    uc("project.board.inputs:get", "GET", P + "/board/inputs/{target_id}", member(V)),
    # KAN-91 : la phrase de survol d'un lien
    uc("project.board.links:get", "GET", P + "/board/links/{link_id}", member(V)),
    # Commentaires (KAN-51) : lire, lecteur ; écrire, commentateur et plus. L'auteur seul
    # modifie ou supprime son message ; le propriétaire supprime tout fil (vérifié dans la route).
    # KAN-58 : le journal d'activité, lecteur compris
    uc("project.activity:get", "GET", P + "/activity", member(V)),
    # KAN-43 : ma vue de l'espace de travail ; tout membre garde la sienne, lecteur compris.
    uc("project.view:get", "GET", P + "/view", member(V)),
    uc("project.view:put", "PUT", P + "/view", member(V)),
    uc("project.comments:get", "GET", P + "/comments", member(V)),
    uc("project.comments:post", "POST", P + "/comments", member(C)),
    uc("project.comments.replies:post", "POST", P + "/comments/{thread_id}/replies", member(C)),
    uc(
        "project.comments.messages:patch",
        "PATCH",
        P + "/comments/{thread_id}/messages/{message_id}",
        member(C),
    ),
    uc(
        "project.comments.messages:delete",
        "DELETE",
        P + "/comments/{thread_id}/messages/{message_id}",
        member(C),
    ),
    uc("project.comments.resolve:post", "POST", P + "/comments/{thread_id}/resolve", member(C)),
    uc("project.comments.reopen:post", "POST", P + "/comments/{thread_id}/reopen", member(C)),
    uc("project.comments:delete", "DELETE", P + "/comments/{thread_id}", member(C)),
    # Réactions (KAN-78) : lire, lecteur ; réagir, commentateur et plus ; chacun ne retire que
    # la sienne (la route ne touche qu'à celle de l'appelant).
    uc("project.reactions:get", "GET", P + "/reactions", member(V)),
    uc("project.reactions:put", "PUT", P + "/reactions/{item_id}/{key}", member(C)),
    uc("project.reactions:delete", "DELETE", P + "/reactions/{item_id}/{key}", member(C)),
    uc("project.media:get", "GET", P + "/media", member(V)),
    uc("project.media:post", "POST", P + "/media", member(E), SPACE_UPLOAD),
    # KAN-98 L3 : un lien collé, récupéré par le serveur (garde anti-SSRF) : éditeur.
    uc("project.media.link:post", "POST", P + "/media/link", member(E), SPACE_LINK),
    uc("project.media.thumb:get", "GET", P + "/media/{media_id}/thumb", member(V)),
    uc("project.media.file:get", "GET", P + "/media/{media_id}/file", member(V)),
    uc("project.media:delete", "DELETE", P + "/media/{media_id}", member(E)),
    # Photos
    uc("project.images:post", "POST", P + "/images", member(E), SPACE_UPLOAD),
    uc("project.images.file:get", "GET", P + "/images/{image_id}/file", member(V)),
    uc("project.images.quality:get", "GET", P + "/images/{image_id}/quality", member(V)),
    uc("project.images:delete", "DELETE", P + "/images/{image_id}", member(E)),
    uc(
        "project.images.perceive:post",
        "POST",
        P + "/images/{image_id}/perceive",
        member(E),
        SPACE_LAUNCH,
    ),
    uc(
        "project.images.inspire:post",
        "POST",
        P + "/images/{image_id}/inspire",
        member(E),
        SPACE_LAUNCH,
    ),
    uc(
        "project.images.depth:post", "POST", P + "/images/{image_id}/depth", member(E), SPACE_LAUNCH
    ),
    uc(
        "project.images.attic:post", "POST", P + "/images/{image_id}/attic", member(E), SPACE_LAUNCH
    ),
    # Mesure de référence sur une photo (KAN-66)
    uc(
        "project.images.reference:put",
        "PUT",
        P + "/images/{image_id}/reference",
        member(E),
        LEASE_IMAGE,
    ),
    uc(
        "project.images.reference:delete",
        "DELETE",
        P + "/images/{image_id}/reference/{segment_id}",
        member(E),
        LEASE_IMAGE,
    ),
    uc(
        "plan.images.reestimate:post", "POST", L + "/images/{image_id}/reestimate", member(E), LEASE
    ),
    # KAN-79 : profondeur calculée par le navigateur (bail du plan, place disque)
    uc(
        "plan.images.depth:put",
        "PUT",
        L + "/images/{image_id}/depth",
        member(E),
        LEASE,
        SPACE_UPLOAD,
    ),
    # Mesures
    uc("project.measurements:post", "POST", P + "/measurements", member(E)),
    uc("project.measurements:delete", "DELETE", P + "/measurements/{measurement_id}", member(E)),
    # Exports
    uc("project.exports.json:get", "GET", P + "/exports/json", member(V)),
    uc("project.exports.svg:get", "GET", P + "/exports/svg", member(V)),
    # KAN-126 : exports pro (dxf, section-pdf) ; l'archive reste Essentielle (portabilité) ; le
    # glb aussi : la vue 3D de l'éditeur le lit par cette même route (Editor.tsx, exportGlbUrl).
    uc("project.exports.dxf:get", "GET", P + "/exports/dxf", member(V), OFFER_PRO),
    uc(
        "project.exports.section-pdf:get",
        "GET",
        P + "/exports/section-pdf",
        member(V),
        OFFER_PRO,
    ),
    uc("project.exports.pdf:get", "GET", P + "/exports/pdf", member(V)),
    uc("project.exports.glb:get", "GET", P + "/exports/glb", member(V)),
    uc("project.exports.archive:get", "GET", P + "/exports/archive", member(V)),
    # Rendus
    uc("project.renders:get", "GET", P + "/renders", member(V)),
    uc("project.renders:patch", "PATCH", P + "/renders/{run_id}", member(E)),
    uc("project.renders:delete", "DELETE", P + "/renders/{run_id}", member(E)),
    uc("project.renders.restore:post", "POST", P + "/renders/{run_id}/restore", member(E)),
    uc("project.renders.pick:get", "GET", P + "/renders/{run_id}/pick", member(V)),
    uc("project.renders.outline_png:get", "GET", P + "/renders/{run_id}/outline.png", member(V)),
    uc("project.renders.favorites_zip:get", "GET", P + "/renders/favorites.zip", member(V)),
    uc(
        "project.renders.finish:post",
        "POST",
        P + "/renders/{run_id}/finish",
        member(E),
        SPACE_LAUNCH,
    ),
    uc(
        "project.renders.catalogue:post",
        "POST",
        P + "/renders/{run_id}/catalogue",
        member(E),
        SPACE_LAUNCH,
    ),
    uc("project.contextual:get", "GET", P + "/contextual", member(V)),
    # Artefacts des calculs
    uc("project.runs.blender:get", "GET", P + "/runs/{run_id}/blender/{name}", member(V)),
    uc("project.runs.openings:get", "GET", P + "/runs/{run_id}/openings/{name}", member(V)),
    uc("project.runs.detection:get", "GET", P + "/runs/{run_id}/detection/{name}", member(V)),
    uc("project.runs.intention:get", "GET", P + "/runs/{run_id}/intention/{name}", member(V)),
    uc("project.runs.perception:get", "GET", P + "/runs/{run_id}/perception/{name}", member(V)),
    uc("project.runs.inspiration:get", "GET", P + "/runs/{run_id}/inspiration/{name}", member(V)),
    uc("project.runs.depth:get", "GET", P + "/runs/{run_id}/depth/{name}", member(V)),
    uc("project.runs.attic:get", "GET", P + "/runs/{run_id}/attic/{name}", member(V)),
    uc("project.runs.layout:get", "GET", P + "/runs/{run_id}/layout/{name}", member(V)),
    uc("project.runs.contextual:get", "GET", P + "/runs/{run_id}/contextual/{name}", member(V)),
    # Assistant
    uc("project.assistant.thread:get", "GET", P + "/assistant/thread", member(V)),
    uc("plan.assistant.preview:post", "POST", L + "/assistant/preview", member(E)),
    uc("plan.assistant.say:post", "POST", L + "/assistant/say", member(E), LEASE),
    uc("plan.assistant.act:post", "POST", L + "/assistant/act", member(E), LEASE),
    uc("plan.rooms.instruction:post", "POST", L + "/rooms/{room_id}/instruction", member(E), LEASE),
    # Niveau : plan, calibration, détection, aménagement, rendu
    uc("plan.starter:post", "POST", L + "/starter", member(E), LEASE),
    uc("plan.programme:post", "POST", L + "/programme", member(E), LEASE),
    uc("plan.area-guard:post", "POST", L + "/area-guard", member(E), LEASE),
    uc("plan.area-guard.undo:post", "POST", L + "/area-guard/undo", member(E), LEASE),
    uc("plan.ops:post", "POST", L + "/ops", member(E), LEASE, OFFER_PRO_OPS),
    uc("plan.derived:get", "GET", L + "/derived", member(V)),
    uc("plan.bake:get", "GET", L + "/bake", member(V)),
    uc("plan.calibrate:post", "POST", L + "/calibrate", member(E), LEASE),
    uc(
        "plan.recalibrate.dimensions.preview:post",
        "POST",
        L + "/recalibrate/dimensions/preview",
        member(E),
    ),
    uc("plan.recalibrate.dimensions:post", "POST", L + "/recalibrate/dimensions", member(E), LEASE),
    uc("plan.recalibrate:post", "POST", L + "/recalibrate", member(E), LEASE),
    # KAN-124 : l'assistant de calage (404 sans OPENPLAN_ASSISTANT). Il ne modifie rien : ses
    # actions sont les cas d'usage ci-dessus et plus bas (recalibrate.dimensions, autoplan,
    # render.all), appelés par le front après la confirmation.
    uc("plan.calage.missing:get", "GET", L + "/calage/missing", member(V)),
    uc("plan.calage.answer:post", "POST", L + "/calage/answer", member(E)),
    uc("plan.detect:post", "POST", L + "/detect", member(E), SPACE_LAUNCH),
    uc("plan.detect-depth:post", "POST", L + "/detect-depth", member(E), SPACE_LAUNCH),
    uc(
        "plan.images.openings:post",
        "POST",
        L + "/images/{image_id}/openings",
        member(E),
        SPACE_LAUNCH,
    ),
    uc(
        "plan.images.autoplan:post",
        "POST",
        L + "/images/{image_id}/autoplan",
        member(E),
        SPACE_LAUNCH,
    ),
    # KAN-95 : le plan recoupé sur plusieurs photos (combles, pièce droite), comme autoplan.
    uc("plan.attic-fusion:post", "POST", L + "/attic-fusion", member(E)),
    uc("plan.photo-fusion:post", "POST", L + "/photo-fusion", member(E)),
    uc("plan.views.adopt:post", "POST", L + "/views/{camera_id}/adopt", member(E)),
    uc("plan.views.rotate:post", "POST", L + "/views/{camera_id}/rotate", member(E)),
    uc("plan.views.auto:post", "POST", L + "/views/auto", member(E)),
    # KAN-137 : une vue par pièce, et « Ajouter une vue » dans une pièce, comme views/auto.
    uc("plan.views.rooms:post", "POST", L + "/views/rooms", member(E)),
    uc("plan.views.rooms.add:post", "POST", L + "/views/rooms/{room_id}/add", member(E)),
    # KAN-141 : « Recalculer » une vue de pièce modifiée à la main.
    uc(
        "plan.views.rooms.reset:post",
        "POST",
        L + "/views/rooms/camera/{camera_id}/reset",
        member(E),
    ),
    uc("plan.brief.preview:post", "POST", L + "/brief/preview", member(E)),
    uc("plan.brief:post", "POST", L + "/brief", member(E)),
    uc("plan.brief.zones:post", "POST", L + "/brief/zones", member(E)),
    uc("plan.layouts:post", "POST", L + "/layouts", member(E)),
    uc("plan.layouts.apply:post", "POST", L + "/layouts/apply", member(E), LEASE),
    uc("plan.layouts.critique.preview:post", "POST", L + "/layouts/critique/preview", member(E)),
    uc("plan.layouts.critique:post", "POST", L + "/layouts/critique", member(E)),
    uc("plan.layouts.revise:post", "POST", L + "/layouts/revise", member(E)),
    uc("plan.layouts.render:post", "POST", L + "/layouts/render", member(E), SPACE_LAUNCH),
    uc("plan.furniture.check:post", "POST", L + "/furniture/check", member(E)),
    uc("plan.furniture.element.check:post", "POST", L + "/furniture/{element_id}/check", member(E)),
    uc("plan.elements.check:post", "POST", L + "/elements/{element_id}/check", member(E)),
    uc("plan.attic:post", "POST", L + "/attic", member(E), LEASE),
    uc("plan.import-plan.preview:post", "POST", L + "/import-plan/preview", member(E)),
    uc("plan.import-plan.preview-image:post", "POST", L + "/import-plan/preview-image", member(E)),
    uc("plan.import-plan:post", "POST", L + "/import-plan", member(E), LEASE),
    uc("plan.contextual.prompt:post", "POST", L + "/contextual/prompt", member(E)),
    uc("plan.contextual:post", "POST", L + "/contextual", member(E), SPACE_LAUNCH),
    uc("plan.render.estimate:get", "GET", L + "/render/estimate", member(V)),
    uc("plan.cameras.architect:get", "GET", L + "/cameras/architect", member(V)),
    uc("plan.cameras.section:get", "GET", L + "/cameras/section", member(V)),
    uc("plan.autopilot:post", "POST", L + "/autopilot", member(E), SPACE_LAUNCH),
    uc("plan.render.all:post", "POST", L + "/render/all", member(E), SPACE_LAUNCH),
    uc("plan.render.views:post", "POST", L + "/render/views", member(E), SPACE_LAUNCH),
    uc(
        "plan.cameras.render:post",
        "POST",
        L + "/cameras/{camera_id}/render",
        member(E),
        SPACE_LAUNCH,
    ),
    uc("plan.blender:post", "POST", L + "/blender", member(E), SPACE_LAUNCH),
    uc("plan.render:post", "POST", L + "/render", member(E), SPACE_LAUNCH),
    # Bail d'édition du plan (B2d, comptes seulement) : prendre, rendre, demander la main
    uc("plan.lease:post", "POST", L + "/lease", member(E)),
    uc("plan.lease:delete", "DELETE", L + "/lease", member(E)),
    uc("plan.lease.request:post", "POST", L + "/lease/request", member(E)),
    # KAN-89 : rendus réalistes par le poste d'augus ; en plus du rôle, lancer exige un compte
    # de OPENPLAN_RENDER_ACCOUNTS (403 sinon). KAN-99 : voir (liste, image) ne l'exige plus.
    uc("project.realistic:get", "GET", P + "/realistic", member(V)),
    uc("project.realistic.image:get", "GET", P + "/realistic/{request_id}/image", member(V)),
    uc("plan.realistic:post", "POST", L + "/realistic", member(E)),
    # KAN-91 : rendre depuis la photo (D5), variante, couverture du projet, réactions ; même
    # liste blanche en plus du rôle.
    uc("plan.realistic.from-photo:post", "POST", L + "/realistic/from-photo", member(E)),
    uc("project.realistic.variant:post", "POST", P + "/realistic/{request_id}/variant", member(E)),
    uc("project.realistic.cover:put", "PUT", P + "/realistic/{request_id}/cover", member(E)),
    uc("project.realistic.cover:delete", "DELETE", P + "/realistic/cover", member(E)),
    # KAN-99 (3 nov.) : réagir, tout commentateur ou plus, hors liste blanche, à tout rendu
    # fait du projet (le rôle seul suffit).
    uc(
        "project.realistic.reactions:put",
        "PUT",
        P + "/realistic/{request_id}/reactions/{key}",
        member(C),
    ),
    uc(
        "project.realistic.reactions:delete",
        "DELETE",
        P + "/realistic/{request_id}/reactions/{key}",
        member(C),
    ),
    # KAN-99 : la liste et l'image s'ouvrent à tout membre (lecteur compris), hors liste
    # blanche ; l'étoile (« Favoris », commune au projet) à tout éditeur. Lancer, variante et
    # couverture gardent la liste blanche et le lanceur.
    uc("project.realistic.favorite:put", "PUT", P + "/realistic/{request_id}/favorite", member(E)),
    uc(
        "project.realistic.favorite:delete",
        "DELETE",
        P + "/realistic/{request_id}/favorite",
        member(E),
    ),
    # KAN-101 : liens publics d'un rendu : créer, lister, révoquer, éditeur et plus (lecteur
    # et commentateur 403, non-membre 404). Créer exige en plus un rendu fait de la personne
    # et la liste blanche des rendus. Tout répond 404 tant que OPENPLAN_SHARE_LINKS est éteint.
    # KAN-105 L2 : ce 404 est le maillon FEATURE_SHARE_LINKS, en tête : le même pour tous.
    uc(
        "project.realistic.share-links:post",
        "POST",
        P + "/realistic/{request_id}/share-links",
        member(E),
        FEATURE_SHARE_LINKS,
    ),
    uc("project.share-links:get", "GET", P + "/share-links", member(E), FEATURE_SHARE_LINKS),
    uc(
        "project.share-links:delete",
        "DELETE",
        P + "/share-links/{link_id}",
        member(E),
        FEATURE_SHARE_LINKS,
    ),
    # --- routes globales : public, session, administrateur, poste, extension ---
    uc("health:get", "GET", "/api/health", PUBLIC),
    # Sondes (KAN-44) : déclarée pour l'audit ; à l'exécution, HEAD est lu comme GET.
    uc("health:head", "HEAD", "/api/health", PUBLIC),
    uc("auth.email.start:post", "POST", "/api/auth/email/start", PUBLIC, AUTH_RATE),
    uc("auth.email.verify:post", "POST", "/api/auth/email/verify", PUBLIC, AUTH_RATE),
    uc("auth.login:get", "GET", "/api/auth/login", PUBLIC),
    uc("auth.login.code:post", "POST", "/api/auth/login/code", PUBLIC, AUTH_RATE),
    uc("auth.login.verify:post", "POST", "/api/auth/login/verify", PUBLIC, AUTH_RATE),
    uc("auth.logout:post", "POST", "/api/auth/logout", PUBLIC, AUTH_RATE),
    # Mailer `fake` seulement (tests sur la boucle locale) : absente sinon.
    uc("test.mail.last-code:get", "GET", "/api/test/mail/last-code", PUBLIC),
    # KAN-132 : le lien « Mes données » du dernier message (mailer `fake` seulement).
    uc("test.mail.last-link:get", "GET", "/api/test/mail/last-link", PUBLIC),
    # Formulaire de contact du site (origine du site seulement, voir security.py)
    uc("contact:post", "POST", "/api/contact", PUBLIC),
    uc("auth.me:get", "GET", "/api/auth/me", USER),
    uc("auth.me:patch", "PATCH", "/api/auth/me", USER),
    # KAN-133 : l'écran de nouvelle acceptation des conditions d'utilisation.
    uc("auth.terms.accept:post", "POST", "/api/auth/terms/accept", USER, AUTH_RATE),
    uc("auth.logout-all:post", "POST", "/api/auth/logout-all", USER, AUTH_RATE),
    uc("auth.invitation.accept:post", "POST", "/api/auth/invitation/accept", USER, AUTH_RATE),
    # KAN-104 : mot de passe, preuve récente, sessions ; 404 sans OPENPLAN_PASSWORD_LOGIN.
    # Hors de SITE_MUTATIONS : origine de l'app seulement.
    uc("auth.password.login:post", "POST", "/api/auth/password/login", PUBLIC, AUTH_RATE),
    uc("auth.login.password:post", "POST", "/api/auth/login/password", PUBLIC, AUTH_RATE),
    uc("auth.password.forgot:post", "POST", "/api/auth/password/forgot", PUBLIC, AUTH_RATE),
    uc("auth.password.reset:post", "POST", "/api/auth/password/reset", PUBLIC, AUTH_RATE),
    # KAN-104 L2 : inscription (de plus fermée par OPENPLAN_SIGNUP=invite).
    uc("auth.signup.start:post", "POST", "/api/auth/signup/start", PUBLIC, AUTH_RATE),
    uc("auth.signup.verify:post", "POST", "/api/auth/signup/verify", PUBLIC, AUTH_RATE),
    uc("auth.password:put", "PUT", "/api/auth/password", USER, AUTH_RATE),
    uc("auth.reauth.start:post", "POST", "/api/auth/reauth/start", USER, AUTH_RATE),
    uc("auth.reauth.verify:post", "POST", "/api/auth/reauth/verify", USER, AUTH_RATE),
    uc("auth.sessions:get", "GET", "/api/auth/sessions", USER),
    uc("auth.sessions:delete", "DELETE", "/api/auth/sessions/{session_id}", USER, AUTH_RATE),
    # KAN-55 : mes invitations nominatives (cloche) ; filtrées par l'adresse du compte.
    uc("invitations:get", "GET", "/api/invitations", USER),
    uc("invitations.accept:post", "POST", "/api/invitations/{invitation_id}/accept", USER),
    uc("invitations.decline:post", "POST", "/api/invitations/{invitation_id}/decline", USER),
    uc(
        "admin.users.sessions.revoke:post",
        "POST",
        "/api/admin/users/{user_id}/sessions/revoke",
        ADMIN,
    ),
    # KAN-109 : back-office d'augus (audience du site, santé technique).
    uc("admin.audience:get", "GET", "/api/admin/audience", ADMIN),
    uc("admin.health:get", "GET", "/api/admin/health", ADMIN),
    # KAN-109 L2 : indicateurs d'usage (agrégats)
    uc("admin.usage:get", "GET", "/api/admin/usage", ADMIN),
    # KAN-126b : l'offre d'un compte (recherche par adresse dans le corps, jamais dans l'URL)
    uc("admin.users.find:post", "POST", "/api/admin/users/find", ADMIN),
    uc("admin.users.offer:patch", "PATCH", "/api/admin/users/{user_id}/offer", ADMIN),
    # Projets de l'utilisateur (liste filtrée par appartenance, création = propriétaire)
    uc("projects:get", "GET", "/api/projects", USER),
    uc("projects:post", "POST", "/api/projects", USER),
    # KAN-119 : « Supprimer tous mes projets » ; le propriétaire de chacun, vu dans la route.
    uc("projects.delete-all:post", "POST", "/api/projects/delete-all", USER),
    # KAN-12 : ma place disque
    uc("auth.usage:get", "GET", "/api/auth/usage", USER),
    # KAN-132 : supprimer son compte, exporter ses données ; 404 sans
    # OPENPLAN_ACCOUNT_SELF_SERVICE. Le titulaire seul (le compte de la session) ; débit par
    # compte dans la route (5 / h pour la suppression, 3 / jour pour l'export).
    uc("account.delete:get", "GET", "/api/account/delete", USER),
    uc("account.delete.code:post", "POST", "/api/account/delete/code", USER),
    uc("account.delete:post", "POST", "/api/account/delete", USER),
    uc("account.export:post", "POST", "/api/account/export", USER),
    # Le lien de l'e-mail : ouvert sans session pour montrer « Connectez-vous d'abord » ; la
    # session du même compte est exigée dans la route (`signed_in_user`).
    uc("account.export.download:get", "GET", "/api/account/export/download", PUBLIC),
    # KAN-52 : la cloche ne lit que les projets dont la personne est membre.
    uc("notifications:get", "GET", "/api/notifications", USER),
    uc("notifications.count:get", "GET", "/api/notifications/count", USER),
    uc("notifications.read:post", "POST", "/api/notifications/read", USER),
    uc("notifications.read-all:post", "POST", "/api/notifications/read-all", USER),
    # Lecture de l'installation et du catalogue
    uc("ai:get", "GET", "/api/ai", USER),
    uc("models:get", "GET", "/api/models", USER),
    uc("device:get", "GET", "/api/device", USER),
    uc("blender:get", "GET", "/api/blender", USER),
    uc("jobs.running:get", "GET", "/api/jobs/running", USER),
    uc("catalog:get", "GET", "/api/catalog", USER),
    uc("catalog.symbols:get", "GET", "/api/catalog/symbols", USER),
    uc("catalog.preview:get", "GET", "/api/catalog/{asset_id}/preview", USER),
    uc("catalog.model:get", "GET", "/api/catalog/{asset_id}/model", USER),
    # KAN-79 : modèle de profondeur du navigateur
    uc("web-models.depth:get", "GET", "/api/web-models/depth", USER),
    uc("instruction.preview:post", "POST", "/api/instruction/preview", USER),
    uc("home.settings:get", "GET", "/api/home/settings", USER),
    # Réglages du serveur et bilan de l'installation
    uc("home.settings:put", "PUT", "/api/home/settings", ADMIN),
    uc("doctor:get", "GET", "/api/doctor", ADMIN),
    # Cookie de lecture de l'API exposée par jeton (note 16) : absent avec les comptes
    uc("session:post", "POST", "/api/session", LEGACY),
    uc("session:delete", "DELETE", "/api/session", LEGACY),
    # KAN-89 : ce que le cadre Rendus montre (autorisé ou non, poste en ligne, quota).
    uc("realistic.status:get", "GET", "/api/realistic/status", USER),
    # KAN-89 : API du poste de rendu (jeton du poste, appels sortants du poste).
    uc("worker.claim:post", "POST", "/api/worker/claim", WORKER, WORKER_RATE),
    uc("worker.jobs.files:get", "GET", "/api/worker/jobs/{request_id}/files/{name}", WORKER),
    uc("worker.jobs.result:post", "POST", "/api/worker/jobs/{request_id}/result", WORKER),
    uc("worker.jobs.fail:post", "POST", "/api/worker/jobs/{request_id}/fail", WORKER),
    # KAN-96 : battement du poste pour sa prise (calcul et envoi).
    uc("worker.jobs.beat:post", "POST", "/api/worker/jobs/{request_id}/beat", WORKER),
    # KAN-97 : images de style d'une demande prise (sous bail).
    uc("worker.jobs.style:get", "GET", "/api/worker/jobs/{request_id}/style/{index}", WORKER),
    # KAN-101 : page publique d'un rendu partagé et son image, SANS compte (le jeton du lien
    # fait foi ; 404 si inconnu, révoqué, expiré ou réglage éteint ; débit limité).
    uc("share:get", "GET", "/api/share/{token}", PUBLIC),
    uc("share.image:get", "GET", "/api/share/{token}/image", PUBLIC),
    # KAN-102 : les jetons du module navigateur, gérés depuis la section Compte (session).
    uc("account.extension-tokens:get", "GET", "/api/account/extension-tokens", USER),
    uc("account.extension-tokens:post", "POST", "/api/account/extension-tokens", USER),
    uc(
        "account.extension-tokens:delete",
        "DELETE",
        "/api/account/extension-tokens/{token_id}",
        USER,
    ),
    # KAN-102 : le module lui-même (son jeton seulement) ; rôle exigé dans EXTENSION_ROLES.
    uc("extension.targets:get", "GET", "/api/extension/targets", EXTENSION),
    uc(
        "extension.projects.images:post",
        "POST",
        "/api/extension/projects/{project_id}/images",
        extension(E),
        SPACE_LINK,
    ),
)


def _index(entries: tuple[UseCase, ...]) -> dict[tuple[str, str], UseCase]:
    out: dict[tuple[str, str], UseCase] = {}
    names: set[str] = set()
    for entry in entries:
        if entry.key in out or entry.name in names:
            msg = f"registre : {entry.method} {entry.path} ({entry.name}) déclaré deux fois"
            raise ValueError(msg)
        out[entry.key] = entry
        names.add(entry.name)
    return out


BY_KEY: dict[tuple[str, str], UseCase] = _index(REGISTRY)
"""Le cas d'usage de chaque route, par (méthode, chemin)."""


def _with(link: Link) -> frozenset[tuple[str, str]]:
    return frozenset(entry.key for entry in REGISTRY if link in entry.links)


def _project_routes() -> dict[tuple[str, str], Role]:
    out: dict[tuple[str, str], Role] = {}
    for entry in REGISTRY:
        if entry.access.policy is None and entry.access.role is not None:
            out[entry.key] = entry.access.role
    return out


def _global_routes() -> dict[tuple[str, str], Policy]:
    out: dict[tuple[str, str], Policy] = {}
    for entry in REGISTRY:
        if entry.access.policy is not None:
            out[entry.key] = entry.access.policy
    return out


def _extension_roles() -> dict[tuple[str, str], Role]:
    out: dict[tuple[str, str], Role] = {}
    for entry in REGISTRY:
        if entry.access.policy == "extension" and entry.access.role is not None:
            out[entry.key] = entry.access.role
    return out


# --- tables dérivées (lues par la garde, le bail, la place, le poste et la session) --------

PROJECT_ROUTES: dict[tuple[str, str], Role] = _project_routes()
"""Routes de projet et rôle minimal du membre."""
GLOBAL_ROUTES: dict[tuple[str, str], Policy] = _global_routes()
"""Les autres routes et leur politique."""
EXTENSION_ROLES: dict[tuple[str, str], Role] = _extension_roles()
"""KAN-102 : le rôle exigé du compte du jeton du module sur le projet de la route."""
EXTENSION_SPACE_KEY = ("POST", P + "/media/link")
"""La réserve de place d'une image ajoutée par le module : celle d'un lien collé."""
LEASED_ROUTES = _with(LEASE)
IMAGE_LEASED = _with(LEASE_IMAGE)
PROJECT_WIDE = _with(LEASE_PROJECT)
UPLOADS = _with(SPACE_UPLOAD)
LINKS = _with(SPACE_LINK) - frozenset(EXTENSION_ROLES)
"""Les liens collés (KAN-98 L3) ; l'image du module prend la même réserve, sous sa clé."""
LAUNCHES = _with(SPACE_LAUNCH)
RATE_LIMITED = frozenset(path for _method, path in _with(WORKER_RATE))
"""Chemins de l'API du poste au débit limité (`WorkerGate`)."""
PRO_ROUTES = _with(OFFER_PRO)
"""KAN-126 : routes de l'offre Pro entière (la chaîne refuse en 403 `offer_pro`)."""

# --- KAN-126 : offres Essentiel et Pro (aucun paiement : l'offre est posée par l'administrateur)
PRO_OPS: frozenset[str] = frozenset(
    {"add_truss", "add_post", "add_brace", "add_roof_window", "add_stairs", "add_floor_zone"}
)
"""Opérations d'AJOUT à la main de l'offre Pro (route `plan.ops`). Le calcul du plan (génération,
régénération, combles) crée ces éléments quelle que soit l'offre : il ne passe pas par cette
route. Un élément Pro existant reste lisible, sélectionnable, supprimable et exporté. KAN-161 :
la pose à la main d'un bois incliné (`add_brace`) aussi ; le déplacer ou le modifier reste
Essentiel, comme un poteau."""
PUSH_PULL_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {("wall", "thickness"), ("wall", "height"), ("floor_zone", "elevation")}
)
"""Pousser/Tirer (KAN-92) : `update_element` de ces champs, sur ce type d'élément, est de
l'offre Pro."""

PUBLIC_PATHS = frozenset(
    entry.path for entry in REGISTRY if entry.access.policy == "public" and "{" not in entry.path
)
"""Comptes actifs : les seuls chemins `/api` ouverts sans session (note 17, § 4) ; les liens
publics (`/api/share/…`, KAN-101) ont leur propre porte (`is_share_path`)."""
