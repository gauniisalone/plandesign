"""Scène de contrôle des combles (KAN-27) : la vérité de référence pour l'égout et les velux.

Reconstruite le 19 oct. 2026 d'après le « rendu de contrôle » du lot C2 (perdu) : égout 1,00 m,
centre du châssis à 1,92 m, velux à 75 % de la longueur et à 0,80 m du pied de pente. Les
dimensions manquantes sont celles du test C2 (largeur 5,0 m, longueur 6,0 m, caméra à 1,45 m,
4,5 m du pignon du fond) ; seul le faîtage est ajusté pour tenir la cote du châssis : bord bas
à 0,80 m le long de la pente, châssis de 0,98 m, donc centre à 1,29 m le long de la pente,
1,00 + 1,29 × sin(pente) = 1,92 m → pente de 45,5° → faîtage à 3,54 m.

Un velux sur chaque versant (« 2 sur 2 » dans C2). Rendu et profondeur apprise de cette scène :
scratchpad v1/canvas/kan27 (créneau GPU), jamais nécessaires aux tests sans GPU.
"""

from __future__ import annotations

import math

from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.scene.factory import new_project
from openplan.scene.ops import AddRoofWindow, apply_op
from openplan.schemas import RoofPlane
from openplan.schemas.common import Source
from openplan.schemas.scene import Project
from tests.factories import only_level

LENGTH = 6.0
WIDTH = 5.0
EAVE = 1.0
VELUX_DISTANCE = 0.80
"""Bord bas du châssis, le long de la pente depuis l'égout."""
VELUX_LENGTH = 0.98
VELUX_WIDTH = 0.78
VELUX_CENTER_Z = 1.92
VELUX_ALONG = 0.75
"""Centre du velux à 75 % de la longueur (depuis le pignon x = 0)."""
CAMERA_HEIGHT = 1.45
GABLE_DISTANCE = 4.5
"""Distance de la caméra au pignon du fond (x = LENGTH) : caméra en x = 1,5."""

PITCH = math.asin((VELUX_CENTER_Z - EAVE) / (VELUX_DISTANCE + VELUX_LENGTH / 2))
RIDGE = EAVE + (WIDTH / 2) * math.tan(PITCH)
"""≈ 3,54 m."""


def _truth(value: float) -> Estimate:
    return Estimate(value=value, sigma=0.0, source=Source.USER_DEFINED)


def control_attic() -> Project:
    """La scène de contrôle, velux compris, avec la caméra « Photo » de la prise de vue."""
    project = new_project("Contrôle des combles (KAN-27)")
    level_id = only_level(project).id
    geometry = AtticGeometry(
        length=_truth(LENGTH),
        width=_truth(WIDTH),
        ridge_height=_truth(RIDGE),
        eave_height=_truth(EAVE),
        camera_height=_truth(CAMERA_HEIGHT),
        gable_distance=_truth(GABLE_DISTANCE),
    )
    project, _ = apply_attic(project, level_id, geometry)
    roofs = {e.name: e for e in only_level(project).elements.values() if isinstance(e, RoofPlane)}
    center_x = VELUX_ALONG * LENGTH
    # « Versant bas » : égout de x = 0 à x = L ; « Versant haut » : de x = L à x = 0.
    for name, offset in (
        ("Versant bas", center_x - VELUX_WIDTH / 2),
        ("Versant haut", LENGTH - center_x - VELUX_WIDTH / 2),
    ):
        project = apply_op(
            project,
            level_id,
            AddRoofWindow(
                roof=roofs[name].id,
                offset=offset,
                distance=VELUX_DISTANCE,
                width=VELUX_WIDTH,
                length=VELUX_LENGTH,
                name=f"Velux de contrôle ({name.lower()})",
            ),
        )
    return project
