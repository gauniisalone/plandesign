"""File des rendus réalistes (KAN-89) : ce que le serveur et le poste de rendu s'échangent.

Le poste ne reçoit que ces messages, validés ici (champs inconnus refusés), et trois fichiers
JSON de taille plafonnée (projet, bake, caméra), eux aussi validés par leur schéma. Aucun champ
ne porte une commande, un chemin ou une adresse : le poste n'exécute rien de ce qu'il reçoit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import Field

from openplan.schemas.common import StrictModel
from openplan.schemas.fidelity import FidelityVerdict

RequestState = Literal["pending", "taken", "done", "failed", "expired"]
WorkerState = Literal["idle", "busy", "paused", "waiting"]
"""`waiting` : le poste passe son tour (verrou du travail lourd, GPU occupé, tests en cours)."""
GuardReason = Literal[
    "vram_depassee",
    "nvidia_smi_illisible",
    "nvidia_smi_illisible_depart",
    "carte_occupee",
    "ram_basse",
    "duree_max",
]
"""KAN-159 : la cause exacte d'un arrêt par le garde-fou de Z-Image, au lieu de « vram » pour
tout. Valeur relevée et seuil, quand il y en a (`WorkerFailure.measured`, `threshold`) :
- `vram_depassee` : Mio occupés sur la carte pendant le calcul, seuil 5 000 Mio ;
- `nvidia_smi_illisible` : lectures ratées d'affilée pendant le calcul, seuil 3 (KAN-146) ;
- `nvidia_smi_illisible_depart` : nvidia-smi illisible avant le départ (ni valeur ni seuil) ;
- `carte_occupee` : Mio déjà occupés avant le départ, seuil 500 Mio ;
- `ram_basse` : Mio de mémoire vive libres, minimum 1 500 Mio ;
- `duree_max` : secondes de calcul, plafond 900 s."""
FailureReason = Literal[
    "unavailable",
    "vram",
    "timeout",
    "engine_error",
    "invalid_input",
    "vram_depassee",
    "nvidia_smi_illisible",
    "nvidia_smi_illisible_depart",
    "carte_occupee",
    "ram_basse",
    "duree_max",
]
"""`vram` reste accepté : un poste d'avant KAN-159 l'envoie pour tout arrêt du garde-fou."""
GUARD_REASONS: tuple[str, ...] = get_args(GuardReason)
FAILURE_REASONS: tuple[str, ...] = get_args(FailureReason)
LEGACY_GUARD_REASON: FailureReason = "vram"
"""Ce qu'un poste envoie à la place d'une cause précise vers un serveur d'avant KAN-159."""
POSTE_FAULTS = frozenset({*GUARD_REASONS, "vram", "unavailable", "timeout", "lost"})
"""KAN-159 : échecs dus au poste lui-même (garde-fou, moteur absent, délai, connexion perdue) :
la demande ne compte pas dans le quota du jour de la personne. `engine_error` et
`invalid_input` comptent toujours : la vue elle-même peut en être la cause."""
MAX_MEASURE = 10_000_000

MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_BAKE_BYTES = 8 * 1024 * 1024
MAX_CAMERA_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
"""Plafond d'un rendu déposé (PNG) ; l'intergiciel refuse un envoi plus gros avant de le lire."""
MAX_IMAGE_SIDE = 2048
"""Plus grand côté d'un rendu déposé (Z-Image rend en 1024 px de grand côté)."""
MAX_DRAW = 2
"""KAN-134 : rang le plus haut d'un tirage d'une même vue (« 1 à 3 rendus par pièce »)."""
JSON_FILES: dict[str, int] = {
    "project": MAX_PROJECT_BYTES,
    "bake": MAX_BAKE_BYTES,
    "camera": MAX_CAMERA_BYTES,
}
"""Fichiers qu'un poste peut télécharger pour une demande, et leur plafond (octets)."""
MAX_SEED = 2**31 - 1
MAX_STYLE_IMAGES = 6
"""KAN-97 : images de style envoyées au poste pour un rendu, au plus."""
MAX_STYLE_IMAGE_BYTES = 2 * 1024 * 1024
"""KAN-97 : une image de style servie au poste (JPEG réduit à STYLE_IMAGE_SIDE), au plus."""
STYLE_IMAGE_SIDE = 512
MAX_STYLE_WORDS = 8
MAX_STYLE_IGNORED = 64
"""KAN-130 : mots désactivés envoyés au poste, au plus (le vocabulaire en compte moins)."""
StyleName = Annotated[str, Field(min_length=1, max_length=120)]
StyleKey = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z -]*$")]
"""Un mot du vocabulaire de style (`pipeline.style_words`), tel que le poste le renvoie."""
TYPICAL_SECONDS = 120.0
"""KAN-91 : durée annoncée d'un rendu tant que le journal n'en a pas assez (« ~2 min »)."""


class WorkerJob(StrictModel):
    """Une demande prise par le poste. `lease` accompagne chaque appel suivant : une demande
    reprise par la file (poste tombé) ne peut plus être déposée par l'ancien bail."""

    request_id: UUID
    lease: UUID
    created_at: datetime
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    """KAN-91 : tirage d'une variante (même vue, autre tirage) ; absent du message pour un
    premier rendu, qui garde le tirage par défaut du moteur."""
    style: list[StyleName] | None = Field(default=None, max_length=MAX_STYLE_IMAGES)
    """KAN-97 : les noms des images de style (cartes Inspirations reliées à la carte Rendus,
    C1), dans l'ordre de `/style/{k}` ; envoyé seulement à un poste qui annonce `STYLE` (un
    ancien poste refuserait ce champ)."""
    style_ignored: list[StyleKey] | None = Field(default=None, max_length=MAX_STYLE_IGNORED)
    """KAN-130 : les mots du vocabulaire désactivés sur ces images (« Guidé par »), que le poste
    ne retient jamais ; envoyé seulement à un poste qui annonce `STYLE_IGNORE`, et absent quand
    aucun mot n'est désactivé (le message reste celui de KAN-97)."""


class WorkerClaim(StrictModel):
    """L'appel périodique du poste (toutes les 10 à 15 s) : il vaut battement de cœur. `idle`
    prend une demande s'il y en a ; `paused` ou `waiting` (le poste passe son tour) ne prend
    rien et dit seulement que le poste est là."""

    state: Literal["idle", "paused", "waiting"] = "idle"
    version: str = Field(default="", max_length=32, pattern=r"^[A-Za-z0-9_.+-]*$")


class WorkerResult(StrictModel):
    """Ce que le poste dit de son rendu, avec l'image (PNG)."""

    seconds: float = Field(ge=0.0, le=6 * 3600)
    vram_peak_mib: int = Field(ge=0, le=1_000_000)
    width: int = Field(ge=16, le=MAX_IMAGE_SIDE)
    height: int = Field(ge=16, le=MAX_IMAGE_SIDE)
    engine: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_.-]+$")
    style_words: list[StyleKey] | None = Field(default=None, max_length=MAX_STYLE_WORDS)
    """KAN-97 : les mots de style retenus (vocabulaire fermé, anglais), [] si aucun ; envoyé
    seulement à un serveur qui sert `STYLE` (un ancien serveur refuserait ce champ)."""
    fidelity: FidelityVerdict | None = None
    """KAN-154 : le verdict de fidélité du rendu à sa vue figée ; envoyé seulement drapeau
    OPENPLAN_FIDELITY_CHECK allumé sur le poste (un serveur d'avant KAN-154 refuserait ce champ :
    le serveur se met à jour avant le poste)."""


class WorkerFailure(StrictModel):
    """Un code seulement : aucun texte libre (ni chemin, ni trace) ne quitte le poste. KAN-159 :
    pour un arrêt du garde-fou, la valeur relevée et le seuil (deux entiers, voir `GuardReason`
    pour leur unité), envoyés seulement à un serveur qui sert `FAILURE_DETAIL`."""

    reason: FailureReason
    measured: int | None = Field(default=None, ge=0, le=MAX_MEASURE)
    threshold: int | None = Field(default=None, ge=0, le=MAX_MEASURE)


class RealisticRequest(StrictModel):
    """Une demande, telle que l'interface la montre."""

    id: UUID
    level_id: UUID
    camera_id: UUID
    state: RequestState
    created_at: datetime
    finished_at: datetime | None = None
    expires_at: datetime
    seconds: float | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None
    """Phrase en français pour un échec ; jamais un détail technique."""
    image: bool = False
    taken_at: datetime | None = None
    """KAN-91 : début du calcul (barre d'avancement de la galerie)."""
    photo_id: UUID | None = None
    """KAN-91 (D5) : la photo dont la caméra estimée est la vue de ce rendu ; None pour un
    point de vue libre. L'avant/après avec cette photo est alors fidèle."""
    guided_by: list[str] | None = None
    """KAN-97 : « Guidé par : … ». Les mots de style retenus par le poste, en français, ou les
    noms des images d'inspiration (voir `guided_kind`) ; [] : aucune inspiration reliée ; None :
    rendu d'avant KAN-97."""
    guided_kind: Literal["words", "unread", "no_style"] | None = None
    """`words` : mots lus ; `unread` : noms des images, non lues par le poste (ancien poste,
    SigLIP2 absent) ; `no_style` : noms des images, lues sans style net retenu."""
    guided_keys: list[StyleKey] | None = None
    """KAN-130 : avec `words`, les clés du vocabulaire de ces mots, dans le même ordre (un clic
    sur un mot le désactive ou le réactive) ; None sinon."""
    mine: bool = False
    """KAN-99 : la personne connectée a lancé ce rendu. Les rendus d'un projet sont montrés à
    tous ses membres ; variante et couverture restent au lanceur."""
    author: str | None = Field(default=None, max_length=80)
    """KAN-115 : le lanceur tel que le voit la personne connectée (« Vous », son nom, « Ancien
    membre » pour un compte effacé) ; None hors de la liste (création, poste)."""


class RealisticCreate(StrictModel):
    camera_id: UUID
    draw: int = Field(default=0, ge=0, le=MAX_DRAW)
    """KAN-134 : rang du tirage de cette vue dans un même envoi (0 : le premier). Une vue garde
    au plus `draw + 1` demandes en attente : le double clic reste fusionné, « 2 rendus par
    pièce » donne 2 demandes (chacune au quota du jour)."""


class RealisticFromPhoto(StrictModel):
    """KAN-91 (D5) : rendre le point de vue exact d'une photo réelle (sa caméra estimée)."""

    image_id: UUID


class RealisticCover(StrictModel):
    """KAN-91 : le rendu choisi comme couverture du projet (None : choix automatique)."""

    cover_id: UUID | None = None


class RealisticReaction(StrictModel):
    """KAN-91 : une pastille de réaction (KAN-78) sur un rendu réaliste."""

    item_id: UUID
    """L'identifiant du rendu (de la demande)."""
    key: str
    emoji: str
    count: int
    mine: bool
    names: list[str]


class RealisticStatus(StrictModel):
    """État du service pour la personne connectée (cadre Rendus)."""

    allowed: bool
    worker: Literal["online", "offline", "paused", "busy", "waiting"] = "offline"
    """`offline` : pas de battement de cœur récent ; le rendu sera fait à son retour.
    `waiting` : en ligne, mais le poste finit un autre travail (verrou, GPU, tests)."""
    quota: int = 0
    used_today: int = 0


class RealisticList(StrictModel):
    status: RealisticStatus
    requests: list[RealisticRequest]
    typical_seconds: float = TYPICAL_SECONDS
    """KAN-91 : médiane des derniers rendus faits (journal du poste), sinon ~2 min."""
    cover_id: UUID | None = None
    """KAN-91 : le rendu choisi comme couverture du projet (liste « Mes projets »)."""
    reactions: list[RealisticReaction] = Field(default_factory=list)
    favorite_ids: list[UUID] = Field(default_factory=list)
    """KAN-99 : les rendus étoilés du projet (« Favoris »), communs à tous les membres."""


MAX_FAVORITES = 1000
"""KAN-99 : plafond du fichier des favoris (une demande pèse ~40 octets)."""


class RealisticFavorites(StrictModel):
    """KAN-99 : les rendus étoilés d'un projet, sans auteur (`realistic/favorites.json`, à
    côté de la couverture ; hors du format du projet). Réponse des routes de l'étoile."""

    favorite_ids: list[UUID] = Field(default_factory=list, max_length=MAX_FAVORITES)


FAILURE_TEXT: dict[str, str] = {
    "unavailable": "Le moteur de rendu n'est pas prêt sur le poste de rendu.",
    "vram": "Le poste de rendu manquait de mémoire : réessayez plus tard.",
    "timeout": "Le rendu a pris trop de temps et a été arrêté.",
    "engine_error": "Le rendu a échoué sur le poste de rendu.",
    "invalid_input": "La vue n'a pas pu être lue par le poste de rendu.",
    "abandoned": "Le poste de rendu ne l'a pas terminé.",
    "lost": "Le poste de rendu a perdu la connexion, relancez le rendu.",
    # KAN-159 : une phrase par cause du garde-fou (écrites ici, jamais par le poste).
    "vram_depassee": (
        "Le calcul a dépassé la mémoire de la carte graphique du poste de rendu et a été "
        "arrêté : réessayez plus tard. Ce rendu ne compte pas dans votre quota du jour."
    ),
    "nvidia_smi_illisible": (
        "Le poste de rendu ne pouvait plus surveiller sa carte graphique : le calcul a été "
        "arrêté par prudence, réessayez plus tard. Ce rendu ne compte pas dans votre quota du "
        "jour."
    ),
    "nvidia_smi_illisible_depart": (
        "Le poste de rendu ne pouvait pas lire l'état de sa carte graphique : le rendu n'a pas "
        "été lancé, réessayez plus tard. Ce rendu ne compte pas dans votre quota du jour."
    ),
    "carte_occupee": (
        "Une autre application utilise la carte graphique du poste de rendu (vue 3D ouverte ?) : "
        "le rendu n'a pas été lancé, réessayez plus tard. Ce rendu ne compte pas dans votre "
        "quota du jour."
    ),
    "ram_basse": (
        "Le poste de rendu manquait de mémoire vive (d'autres applications ouvertes ?) : le "
        "calcul a été arrêté, réessayez plus tard. Ce rendu ne compte pas dans votre quota du "
        "jour."
    ),
    "duree_max": (
        "Le calcul a dépassé la durée maximale sur le poste de rendu et a été arrêté : "
        "réessayez plus tard. Ce rendu ne compte pas dans votre quota du jour."
    ),
}

CAPABILITIES_HEADER = "X-Render-Capabilities"
"""Réponse à `claim` : les capacités annoncées par le poste que ce serveur sait servir (jetons
joints par « + »). En en-tête, pas dans `WorkerJob` (strict chez un ancien poste) : un ancien
poste ou un ancien serveur l'ignore. Le poste n'use d'une capacité que si elle y figure."""
HEARTBEAT = "heartbeat"
"""KAN-96 : le poste bat pour sa prise pendant le calcul et l'envoi (bail court)."""
STYLE = "style"
"""KAN-97 : le poste lit les images de style (`WorkerJob.style`, `/style/{k}`) et renvoie ses
mots (`WorkerResult.style_words`)."""
STYLE_IGNORE = "style-ignore"
"""KAN-130 : le poste sait écarter les mots désactivés (`WorkerJob.style_ignored`)."""
FAILURE_DETAIL = "why"
"""KAN-159 : le poste envoie la cause exacte d'un arrêt du garde-fou (`GuardReason`), avec la
valeur relevée et le seuil ; sans elle, `vram` comme avant. Jeton court : `WorkerClaim.version`
tient en 32 caractères chez un serveur d'avant (« heartbeat+style+style-ignore+why »)."""
BEAT_EVERY_S = 30
"""KAN-96 : cadence du battement d'un rendu pris (calcul et envoi compris)."""


def parse_capabilities(text: str) -> frozenset[str]:
    """Jetons séparés par « + » ; un jeton inconnu est ignoré par celui qui lit."""
    return frozenset(t for t in text.split("+") if t)


def capabilities(claim: WorkerClaim | None) -> frozenset[str]:
    """Les capacités que le poste annonce dans `WorkerClaim.version` (KAN-96, KAN-97) : un seul
    mécanisme, déjà accepté par le serveur de production (un nouveau champ du modèle strict
    vaudrait un 422 à un poste en avance sur la production)."""
    return frozenset() if claim is None else parse_capabilities(claim.version)
