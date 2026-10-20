"""KAN-31 : confiance de l'estimation, dérivée de facteurs nommés et chiffrés.

Avant : un nombre (0,25 + 0,1 × grandeurs trouvées, 0,6 pour des combles…) affiché en
pourcentage, sans lien avec l'erreur réelle. Maintenant chaque facteur porte une erreur
relative typique **mesurée sur le banc** (`openplan bench`, KAN-33 : scène de contrôle KAN-27
et photos réelles avec une règle de 1,00 m) ; les erreurs des facteurs présents se composent
en somme quadratique (sources supposées indépendantes) et l'erreur attendue donne l'un des
trois niveaux affichés :

- « fiable » : erreur attendue sous 10 % (`RELIABLE_MAX`, Must have 4 de KAN-31) ;
- « à vérifier » : sous 40 % (`CHECK_MAX`) : le mieux que le banc atteigne aujourd'hui
  (échelle calée sur une mesure, 31 %) ;
- « approximatif » : au-delà (échelle supposée ou tirée d'une hauteur saisie, 60 à 66 %).

Aucune configuration du banc n'est sous 10 % au 20 oct. 2026 : aucune estimation depuis une
photo n'est donc dite « fiable » (le test le vérifie). La raison affichée est celle du facteur
qui pèse le plus, avec le geste qui l'améliore.

Facteurs (Must have 2) :

1. **Source de l'échelle** : mesure de référence (KAN-66) > hauteur de prise de vue saisie >
   hauteur supposée 1,50 m (par défaut).
2. **Focale** : déduite des lignes de fuite, ou valeur de repli (EXIF absent : 0,8 × grand côté).
3. **Sol hors champ** (KAN-45) : sol posé à la hauteur de l'appareil, verticale prise entre
   les pentes.
4. **Égout non vu** (KAN-61) : hauteur des murs latéraux sous pente supposée.

Chiffres du banc du 20 oct. 2026 (erreur médiane absolue des cotes ; contrôle = faîtage,
largeur du pignon, distance au pignon ; règle = deux photos d'une règle de 1,00 m) :

- hauteur supposée 1,50 m : contrôle 66 %, règle 60 % (une photo +100 % : un plateau de
  meuble pris pour le sol, l'autre −20 %) → 63 % ;
- hauteur saisie, la vraie (1,45 m), contrôle : 61 % : la profondeur apprise place mal le sol
  (caméra vue à 0,85 m), une hauteur juste ne corrige pas la forme ;
- mesure de référence, contrôle : chaque cote vraie prise à tour de rôle comme référence,
  erreur médiane des autres : 31 % (forme déformée par la profondeur apprise) ;
- focale de repli : règle, 5 points d'écart entre focale des lignes et repli (photo du lit) ;
  contrôle, focale faussée de −20 % / +25 % : 26 / 17 points ; le repli (29 mm équivalent)
  s'écarte d'environ 10 % d'un mobile courant (26 mm) → 10 % ;
- sol hors champ : contrôle privé des 40 % bas de l'image, hauteur juste saisie : 14 % ;
- égout non vu : contrôle, égout à 0 au lieu de 1,00 m, la largeur est alors prise au sol
  (6,96 m au lieu de 5,00 m pour la vraie géométrie, +39 %) → 40 %.

KAN-70 (sol pris sur un plateau corrigé) : la règle passe à 17 % avec la hauteur supposée (+14 %
et −20 %) ; le contrôle ne change pas. Les poids ci-dessus restent ceux du 20 oct. en attendant
un banc plus large (le poids « supposée » serait alors à revoir à la baisse).

Le détail chiffré, régénéré à chaque passage du banc, est dans `data/eval/rapport-kan33.md`
(hors dépôt) ; `tests/unit/test_confidence.py` recalcule la partie contrôle sur la fixture.
"""

from __future__ import annotations

import math
from typing import Literal

from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.schemas.common import StrictModel

ScaleSource = Literal["reference", "entered", "assumed"]
Level = Literal["fiable", "a_verifier", "approximatif"]
FactorKey = Literal["scale", "focal", "floor", "eave"]

LEVEL_LABEL: dict[Level, str] = {
    "fiable": "fiable",
    "a_verifier": "à vérifier",
    "approximatif": "approximatif",
}

RELIABLE_MAX = 0.10
"""Erreur attendue sous laquelle l'estimation est dite « fiable » (Must have 4)."""
CHECK_MAX = 0.40
"""Erreur attendue sous laquelle elle est « à vérifier » ; au-delà, « approximatif »."""

SCALE_ERROR: dict[ScaleSource, float] = {
    "reference": 0.31,
    "entered": 0.61,
    "assumed": 0.63,
}
"""Erreur relative médiane des cotes selon la source de l'échelle (banc KAN-33)."""
FOCAL_FALLBACK_ERROR = 0.10
"""Erreur ajoutée quand la focale est la valeur de repli et non celle des lignes de fuite."""
FLOOR_UNSEEN_ERROR = 0.14
"""Erreur ajoutée quand le sol est hors champ (KAN-45)."""
EAVE_UNSEEN_ERROR = 0.40
"""Erreur ajoutée sur les combles quand l'égout n'est pas vu (KAN-61)."""


def _m(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + " m"


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
        height = _m(camera_height_m) if camera_height_m else ""
        return Factor(
            key="scale",
            label=f"hauteur de prise de vue saisie {height}".strip(),
            error=SCALE_ERROR["entered"],
            advice="échelle tirée de la hauteur saisie : ajoutez une mesure de référence",
        )
    return Factor(
        key="scale",
        label=f"hauteur supposée {_m(DEFAULT_CAMERA_HEIGHT_M)} (par défaut)",
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
) -> EstimateConfidence:
    """Niveau de confiance d'une estimation depuis une photo et sa raison principale."""
    factors = [scale_factor(scale, camera_height_m)]
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
    return _combine(factors)


def level_for(expected_error: float) -> Level:
    if expected_error < RELIABLE_MAX:
        return "fiable"
    return "a_verifier" if expected_error < CHECK_MAX else "approximatif"


def _combine(factors: list[Factor]) -> EstimateConfidence:
    expected = math.sqrt(sum(f.error**2 for f in factors))
    level = level_for(expected)
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
