"""Édition validée : commande → projet historisé → invariants vérifiés.

Une violation de sévérité « high » rejette l'édition ; les autres sont renvoyées avec le
projet pour que l'interface les affiche.
"""

from __future__ import annotations

from uuid import UUID

from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import Op, apply_op
from openplan.schemas.results import ValidationReport
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants


class InvalidEditError(ValueError):
    """L'édition laisserait la scène dans un état incohérent."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        messages = "; ".join(v.message for v in report.violations if v.severity == "high")
        super().__init__(f"édition refusée : {messages}")


def edit(
    project: Project, level_id: UUID, op: Op, *, actor: str = USER_ACTOR
) -> tuple[Project, ValidationReport]:
    after = apply_op(project, level_id, op, actor=actor)
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    return after, report
