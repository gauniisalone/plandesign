"""KAN-31 : confiance de l'estimation, dérivée de facteurs nommés et chiffrés.

Avant : un nombre (0,25 + 0,1 × grandeurs trouvées, 0,6 pour des combles…) affiché en
pourcentage, sans lien avec l'erreur réelle. Maintenant chaque facteur porte une erreur
relative typique **mesurée sur le banc** (`openplan bench`, KAN-33 : scène de contrôle KAN-27
et photos réelles avec une règle de 1,00 m) ; les erreurs des facteurs présents se composent
en somme quadratique (sources supposées indépendantes) et l'erreur attendue donne l'un des
trois niveaux affichés :

- « fiable » : erreur attendue sous 10 % (`RELIABLE_MAX`, Must have 4 de KAN-31) **et** banc
  d'au moins `MIN_BENCH_SCENES_FOR_RELIABLE` scènes mesurées (plancher, décision PO du
  21 oct.) ;
- « à vérifier » : sous 40 % (`CHECK_MAX`), ou sous 10 % tant que le banc n'a qu'une scène ;
- « approximatif » : à partir de 40 % (égout non vu, échelle devinée sur un dessin).

Plancher : le banc ne compte qu'une scène qui mesure les trois sources d'échelle (le contrôle
KAN-27, `BENCH_SCENES = 1`) ; une seule scène ne suffit pas à promettre « fiable », le niveau le
plus haut est donc « à vérifier ». Une seconde scène mesurée (la chambre de KAN-33, privée,
hors dépôt, une fois ses cotes relevées) le lèvera en passant `BENCH_SCENES` à 2. La raison
affichée est celle du facteur qui pèse le plus, avec le geste qui l'améliore.

Facteurs (Must have 2) :

1. **Source de l'échelle** : mesure de référence (KAN-66), hauteur de prise de vue saisie, ou
   hauteur supposée 1,50 m (par défaut). Depuis KAN-72, le contrôle ne les range plus dans
   cet ordre (voir plus bas).
2. **Focale** : déduite des lignes de fuite, ou valeur de repli (EXIF absent : 0,8 × grand côté).
3. **Sol hors champ** (KAN-45) : sol posé à la hauteur de l'appareil, verticale prise entre
   les pentes.
4. **Égout non vu** (KAN-61) : hauteur des murs latéraux sous pente supposée.

Chiffres du banc du 21 oct. 2026, après KAN-72 (verticale corrigée, toit supposé symétrique).
Erreur médiane absolue des cotes ; contrôle = faîtage, largeur du pignon, distance au pignon ;
règle = deux photos d'une règle de 1,00 m. Les trois sources d'échelle sont comparées sur le
contrôle, seul jeu qui les mesure toutes (décision PO, option A) :

- hauteur saisie, la vraie (1,45 m), contrôle : 5 % (avant KAN-72 : 35 %) ;
- hauteur supposée 1,50 m, contrôle : 9 % (avant : 40 %). Recoupement sur la règle de la
  chambre : 17 % (+14 % et −20 %), non pris dans le poids ;
- mesure de référence, contrôle : chaque cote vraie prise à tour de rôle comme référence,
  erreur médiane des autres : 10 % (avant : 17 %).

**L'ordre s'inverse** : la référence (10 %) passe derrière les deux hauteurs. Deux raisons, qui
tiennent au banc plus qu'à la méthode : la « hauteur saisie » du banc est la vraie hauteur, au
centimètre (une saisie réelle ne l'est pas : `PRIOR_REL_SIGMA` du module attic la suppose à
±10 %) ; et la référence prise à tour de rôle hérite de l'erreur de la cote choisie (la distance
au pignon, −13 % avec la hauteur saisie, recalée seule, donne +22 % sur le faîtage). Les poids
sont gardés tels que mesurés (pas d'ordre forcé) ; avec le plancher, les trois sources
s'affichent « à vérifier », l'inversion ne se voit pas à l'écran.

- focale de repli : contrôle, focale faussée de −20 % / +25 % : 13 / 5 %, soit 8 / 0 points
  autour de la hauteur saisie ; règle, 5 points entre focale des lignes et repli (photo du
  lit) ; le repli (29 mm équivalent) s'écarte d'environ 10 % d'un mobile courant (26 mm),
  la moitié de l'écart −20 % → 5 % ;
- sol hors champ : contrôle privé des 40 % bas de l'image, hauteur juste saisie : 14 % ;
- égout non vu : tiré de la géométrie du contrôle : égout ramené au sol au lieu de 1,00 m, la
  largeur est prise au sol (6,96 m au lieu de 5,00 m, +39 %) → 40 %. L'égout vu, lui, est à
  5 % sur le contrôle (1,05 m pour 1,00) : ce chiffre mesure un égout vu, pas l'égout manquant.

KAN-95 (25 oct.) : le banc compte un comble réel mesuré vu par six photos (privé, hors dépôt ;
`BENCH_SCENES` inchangé, décision PO). Deux facteurs de forme s'y ajoutent : combles lus sur
une seule photo (`ATTIC_SINGLE_ERROR`, 15 %) et combles recoupés (concordance des photos,
`FUSION_MIN_ERROR` au moins). Depuis le faîtage lu sur le profil (lot B), le contrôle n'est
presque plus sensible à la focale (focale × 0,80 : faîtage +13 % → −3 %) ; le poids de la focale
de repli (5 %) est gardé, appuyé sur les photos réelles (règle de la chambre, grenier).

Le détail chiffré, régénéré à chaque passage du banc, est dans `data/eval/rapport-kan33.md`
(hors dépôt) ; `tests/unit/test_confidence.py` recalcule la partie contrôle sur la fixture.
"""

from __future__ import annotations

import math
from typing import Literal

from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.schemas.common import StrictModel
from openplan.schemas.numbers import metres

ScaleSource = Literal["reference", "entered", "assumed"]
Level = Literal["fiable", "a_verifier", "approximatif"]
FactorKey = Literal["scale", "focal", "floor", "eave", "room", "shape"]

LEVEL_LABEL: dict[Level, str] = {
    "fiable": "fiable",
    "a_verifier": "à vérifier",
    "approximatif": "approximatif",
}

RELIABLE_MAX = 0.10
"""Erreur attendue sous laquelle l'estimation est dite « fiable » (Must have 4)."""
CHECK_MAX = 0.40
"""Erreur attendue sous laquelle elle est « à vérifier » ; au-delà, « approximatif »."""

BENCH_SCENES = 1
"""Scènes du banc dont la vérité mesure les trois sources d'échelle (le contrôle KAN-27)."""
MIN_BENCH_SCENES_FOR_RELIABLE = 2
"""Plancher (décision PO du 21 oct.) : « fiable » n'est jamais affiché tant que le banc compte
moins de scènes que cela ; le niveau le plus haut est alors « à vérifier »."""

SCALE_ERROR: dict[ScaleSource, float] = {
    "reference": 0.10,
    "entered": 0.05,
    "assumed": 0.09,
}
"""Erreur relative médiane des cotes selon la source de l'échelle (banc KAN-33, contrôle,
après KAN-72) ; ordre inversé par le banc, voir la docstring du module."""
FOCAL_FALLBACK_ERROR = 0.05
"""Erreur ajoutée quand la focale est la valeur de repli et non celle des lignes de fuite."""
FLOOR_UNSEEN_ERROR = 0.14
"""Erreur ajoutée quand le sol est hors champ (KAN-45)."""
EAVE_UNSEEN_ERROR = 0.40
"""Erreur ajoutée sur les combles quand l'égout n'est pas vu (KAN-61)."""
MIN_ROOM_WIDTH_M = 2.0
"""KAN-87 : sous cette largeur, le plan estimé n'est pas une pièce habitable plausible (une
chambre vue de près, « 0,92 × 2,14 m ») : la photo montre sans doute un détail."""
NARROW_ROOM_ERROR = 0.45
"""Erreur ajoutée à un plan plus étroit que MIN_ROOM_WIDTH_M : seule, elle dépasse CHECK_MAX
(« approximatif »)."""
CLOSE_UP_REACH = 1.5
"""KAN-91 : portée (`depthplan.depth_reach`, 90ᵉ centile de la profondeur en hauteurs de prise
de vue) sous laquelle la photo ne voit pas le fond de la pièce. Mesuré : bureau vu de près 1,17 ;
chambre 2,05, combles 2,72, pièce en L Blender 3,48, scène de contrôle 4,45."""


ATTIC_SINGLE_ERROR = 0.15
"""KAN-95 : combles lus sur une seule photo (forme lue sur cette photo). Banc du 25 oct.,
grenier mesuré (faîtage 2,25 m, largeur au sol 7,20 m), 5 photos qui donnent des combles,
hauteur supposée 1,50 m, après le lot B : erreurs −9, +13, +1, +15 % (faîtage), −4 % (largeur)
et une photo masquée par un doigt à −87 % ; médiane 11 %. Avant le lot B : +57, +17, +530,
+182 % sur le faîtage. 15 % : avec la hauteur supposée (9 %), l'erreur annoncée (18 %) couvre
l'erreur réelle médiane et 5 cotes sur 6."""
FUSION_MIN_ERROR = 0.05
"""KAN-95 : combles recoupés : la forme est à ±concordance (une photo retirée à tour de rôle,
`attic_fusion`), jamais moins que 5 % ; sans photo de biais, jamais moins que 50 %
(`attic_fusion.AXIAL_ONLY_ERROR`). Grenier mesuré, 6 photos : concordance ±28 %, erreur
annoncée 29 % ; erreur réelle 13 % (faîtage) et 17 % (largeur), 4 % sur la largeur avec le
faîtage connu (forme seule). Sur dix sous-ensembles de ces photos, l'erreur annoncée couvre
l'erreur réelle de faîtage et de largeur, à une cote près (−19 % pour 18 % annoncés)."""


def close_up(narrow_room: bool, reach: float | None) -> bool:
    """KAN-91 : alerte « gros plan » après l'import. Il faut les deux signes : un plan plus
    étroit que MIN_ROOM_WIDTH_M (KAN-87) et une photo qui ne voit pas loin (ou pas de
    profondeur pour le dire). Un vrai réduit photographié de loin voit loin ; une grande pièce
    vue de près donne un plan large : ni l'un ni l'autre n'est un détail."""
    return narrow_room and (reach is None or reach < CLOSE_UP_REACH)


class Factor(StrictModel):
    key: FactorKey
    label: str
    """Ce que le facteur dit de cette estimation, en clair."""
    error: float
    """Erreur relative typique apportée par ce facteur (banc KAN-33)."""
    advice: str
    """Raison affichée quand ce facteur pèse le plus."""


class EstimateConfidence(StrictModel):
    level: Level
    label: str
    """« fiable », « à vérifier » ou « approximatif »."""
    reason: str
    """Raison principale (facteur le plus lourd), avec le geste qui l'améliore."""
    expected_error: float
    """Erreur relative attendue sur les cotes (somme quadratique des facteurs)."""
    factors: list[Factor]


def scale_factor(source: ScaleSource, camera_height_m: float | None = None) -> Factor:
    """Facteur « source de l'échelle ». Textes alignés sur KAN-62 (« hauteur supposée 1,50 m
    (par défaut) ») et KAN-66 (« échelle calée sur votre mesure »)."""
    if source == "reference":
        return Factor(
            key="scale",
            label="échelle calée sur votre mesure",
            error=SCALE_ERROR["reference"],
            advice="échelle calée sur votre mesure : vérifiez les autres cotes à vue",
        )
    if source == "entered":
        height = metres(camera_height_m) if camera_height_m else ""
        return Factor(
            key="scale",
            label=f"hauteur de prise de vue saisie {height}".strip(),
            error=SCALE_ERROR["entered"],
            advice="échelle tirée de la hauteur saisie : ajoutez une mesure de référence",
        )
    return Factor(
        key="scale",
        label=f"hauteur supposée {metres(DEFAULT_CAMERA_HEIGHT_M)} (par défaut)",
        error=SCALE_ERROR["assumed"],
        advice="échelle supposée : ajoutez une mesure de référence",
    )


def assess(
    scale: ScaleSource,
    *,
    camera_height_m: float | None = None,
    focal_from_vps: bool = True,
    floor_out_of_view: bool = False,
    eave_unseen: bool = False,
    narrow_room: bool = False,
    attic_single: bool = False,
    fused_photos: int = 0,
    fusion_spread: float | None = None,
    bench_scenes: int = BENCH_SCENES,
) -> EstimateConfidence:
    """Niveau de confiance d'une estimation depuis une photo et sa raison principale.
    `bench_scenes` : taille du banc qui fonde les poids (plancher de « fiable ») ; ne se change
    que dans les tests, ou ici même quand une seconde scène mesurée rejoint le banc.
    KAN-95 : `attic_single`, combles lus sur une seule photo ; `fused_photos` (au moins 2) et
    `fusion_spread`, combles recoupés et leur concordance."""
    factors = [scale_factor(scale, camera_height_m)]
    if fused_photos >= 2:
        spread = max(
            fusion_spread if fusion_spread is not None else ATTIC_SINGLE_ERROR, FUSION_MIN_ERROR
        )
        pct = math.ceil(spread * 100)
        advice = (
            f"les {fused_photos} photos ne concordent qu'à ±{pct} % : ajoutez une photo de biais "
            "vers un versant, ou une mesure de référence"
            if fusion_spread is not None
            else f"les {fused_photos} photos ne se recoupent pas assez (une seule voit le "
            "faîtage ou la pente) : ajoutez une photo de biais vers un versant"
        )
        factors.append(
            Factor(
                key="shape",
                label=f"combles recoupés sur {fused_photos} photos (±{pct} %)",
                error=spread,
                advice=advice,
            )
        )
    elif attic_single:
        factors.append(
            Factor(
                key="shape",
                label="combles lus sur une seule photo",
                error=ATTIC_SINGLE_ERROR,
                advice="pente lue sur une seule photo : ajoutez au node Photos de la pièce une "
                "photo de biais vers un versant",
            )
        )
    if not focal_from_vps:
        factors.append(
            Factor(
                key="focal",
                label="focale de repli (lignes de fuite insuffisantes)",
                error=FOCAL_FALLBACK_ERROR,
                advice="focale devinée faute de lignes droites : les largeurs peuvent être "
                "faussées, ajoutez une mesure de référence",
            )
        )
    if floor_out_of_view:
        factors.append(
            Factor(
                key="floor",
                label="sol hors champ",
                error=FLOOR_UNSEEN_ERROR,
                advice="sol hors champ : reprenez la photo en montrant le sol",
            )
        )
    if eave_unseen:
        factors.append(
            Factor(
                key="eave",
                label="égout non vu",
                error=EAVE_UNSEEN_ERROR,
                advice="égout non vu : saisissez la hauteur des murs sous pente",
            )
        )
    if narrow_room:
        factors.append(
            Factor(
                key="room",
                label=f"pièce de moins de {metres(MIN_ROOM_WIDTH_M)} de large",
                error=NARROW_ROOM_ERROR,
                advice=f"pièce estimée à moins de {metres(MIN_ROOM_WIDTH_M)} de large : la photo "
                "montre peut-être un détail (un meuble, un coin) ; prenez la pièce entière, "
                "ou ajoutez une mesure de référence",
            )
        )
    return _combine(factors, bench_scenes)


def level_for(expected_error: float, bench_scenes: int = BENCH_SCENES) -> Level:
    """Niveau d'une erreur attendue. Plancher : sous `MIN_BENCH_SCENES_FOR_RELIABLE` scènes
    au banc, jamais « fiable » (« à vérifier » au mieux)."""
    if expected_error < RELIABLE_MAX and bench_scenes >= MIN_BENCH_SCENES_FOR_RELIABLE:
        return "fiable"
    return "a_verifier" if expected_error < CHECK_MAX else "approximatif"


def _combine(factors: list[Factor], bench_scenes: int = BENCH_SCENES) -> EstimateConfidence:
    expected = math.sqrt(sum(f.error**2 for f in factors))
    level = level_for(expected, bench_scenes)
    main = max(factors, key=lambda f: f.error)
    return EstimateConfidence(
        level=level,
        label=LEVEL_LABEL[level],
        reason=main.advice,
        expected_error=round(expected, 3),
        factors=factors,
    )


PLAN_TEXT_SCALE_ERROR = 0.15
"""Plan dessiné, échelle lue dans les cotes (OCR) : hors banc (aucun plan mesuré dans
data/eval), valeur prudente, jamais « fiable »."""
PLAN_GUESSED_SCALE_ERROR = 0.50
"""Plan dessiné, échelle devinée (épaisseur des murs, largeur de porte) : hors banc."""


def assess_plan_image(scale_from_text: bool) -> EstimateConfidence:
    """Image d'un plan dessiné : échelle lue dans les cotes (OCR) ou devinée sur le dessin."""
    if scale_from_text:
        factor = Factor(
            key="scale",
            label="échelle lue dans les cotes du plan",
            error=PLAN_TEXT_SCALE_ERROR,
            advice="échelle lue dans les cotes : vérifiez une longueur connue",
        )
    else:
        factor = Factor(
            key="scale",
            label="échelle devinée sur le dessin",
            error=PLAN_GUESSED_SCALE_ERROR,
            advice="échelle devinée sur le dessin : ajoutez une mesure de référence",
        )
    return _combine([factor])
