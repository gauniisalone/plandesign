"""Étape de calibration : mesures utilisateur → échelle du niveau → murs mesurés.

Acteur d'historique : "pipeline:calibration". La politique de sources autorise cette
étape à relever un mur d'`estimated` à `measured` (priorité qui monte), jamais l'inverse.
"""

from __future__ import annotations

from uuid import UUID

from openplan.geom.scale import CalibrationError, apply_scale, solve_scale
from openplan.scene import history
from openplan.schemas.common import Source, Uncertainty
from openplan.schemas.elements import Opening, Wall
from openplan.schemas.scene import Calibration, Project

ACTOR = "pipeline:calibration"


def calibrate_level(
    project: Project, level_id: UUID, *, dry_run: bool = False
) -> tuple[Project, Calibration]:
    """Résout l'échelle du niveau à partir de toutes les mesures du projet qui le ciblent.

    dry_run=True renvoie la calibration sans modifier le projet.
    Les mesures marquées incohérentes n'entraînent pas le passage du mur en `measured`.
    """
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise CalibrationError(msg)
    calibration = solve_scale(level, project.measurements.values())
    if dry_run:
        return project, calibration

    scaled = apply_scale(level, calibration.scale_factor)
    elements = dict(scaled.elements)
    for mid in calibration.measurement_ids:
        if mid in calibration.inconsistent:
            continue
        measurement = project.measurements[mid]
        target = elements.get(measurement.element_id) if measurement.element_id else None
        if isinstance(target, Wall | Opening):
            elements[target.id] = target.model_copy(
                update={
                    "source": Source.MEASURED,
                    "confidence": 1.0,
                    "uncertainty": Uncertainty(sigma=measurement.tolerance),
                }
            )
    scaled = scaled.model_copy(update={"elements": elements})
    after = project.model_copy(
        update={
            "levels": {**project.levels, level_id: scaled},
            "calibrations": [*project.calibrations, calibration],
        }
    )
    count = len(calibration.measurement_ids)
    label = f"Calibrer ({count} mesure(s), ×{calibration.scale_factor:.4f})"
    return history.commit(project, after, actor=ACTOR, label=label), calibration
