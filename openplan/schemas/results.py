"""Résultats d'étapes de pipeline et rapports de validation (docs/architecture/02)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue

from openplan.schemas.common import Confidence, StrictModel
from openplan.schemas.scene import JsonPatch

Severity = Literal["low", "medium", "high"]


class Artifact(StrictModel):
    """Fichier inspectable produit par une étape : masque, profondeur, SVG, GLB..."""

    name: str
    path: Path
    """Sous <projet>/runs/<run_id>/<step>/."""
    media_type: str
    description: str = ""


class Assumption(StrictModel):
    text: str
    """Ex. "Pièce supposée rectangulaire (hypothèse Manhattan)"."""
    impact: Severity


class StepResult(StrictModel):
    step: str
    patch: JsonPatch = Field(default_factory=list)
    """JSON Patch à appliquer au Project ; jamais un projet muté."""
    confidence: Confidence
    assumptions: list[Assumption] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    duration_s: float = Field(ge=0.0)
    model_ids: list[str] = Field(default_factory=list)
    """Identifiants du registre de modèles utilisés."""


class RunContext(StrictModel):
    run_id: UUID = Field(default_factory=uuid4)
    project_dir: Path
    artifacts_dir: Path
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class Violation(StrictModel):
    type: str
    severity: Severity
    element_ids: list[UUID] = Field(default_factory=list)
    message: str
    """Compréhensible par l'utilisateur."""


class ValidationReport(StrictModel):
    score: Confidence
    valid: bool
    violations: list[Violation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    @classmethod
    def from_violations(cls, violations: list[Violation]) -> ValidationReport:
        """Un rapport est valide sans violation « high ». Le score n'est pas gradué :
        1.0 si valide, 0.0 sinon, tant qu'aucune métrique justifiée n'existe."""
        valid = not any(v.severity == "high" for v in violations)
        return cls(score=1.0 if valid else 0.0, valid=valid, violations=violations)
