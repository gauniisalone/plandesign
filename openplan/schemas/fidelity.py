"""KAN-154 (étape 2, squelette) : le verdict de fidélité d'un rendu réaliste à son plan.

Envoyé par le poste avec le rendu (`WorkerResult.fidelity`) et gardé avec la demande
(`realistic-style/<id>.fidelity.json`). Comme `WorkerFailure`, aucun texte libre ne quitte le
poste : des noms de mesures (vocabulaire fermé), des valeurs et des seuils ; le texte honnête
(« ne respecte pas les dimensions ») sera écrit par le serveur, avec l'interface.
"""

from __future__ import annotations

from pydantic import Field

from openplan.schemas.common import StrictModel

MeasureName = str
NAME_PATTERN = r"^[a-z0-9_]{1,40}$"
MAX_MEASURES = 8
MAX_ATTEMPTS = 3
"""Le premier tirage et deux relances au plus (une autre graine à chaque fois)."""


class MeasureResult(StrictModel):
    name: MeasureName = Field(pattern=NAME_PATTERN)
    """`edge_f1`, `edge_scale`, `openings_iou`… (la mesure par élément de la TMA viendra)."""
    value: float | None = Field(default=None, ge=-1e6, le=1e6)
    """La mesure ; None : non mesurée (pas d'ouverture vue, pas de détecteur)."""
    threshold: float = Field(ge=-1e6, le=1e6)
    ok: bool | None = None
    """None : non mesurée, ne fait pas échouer le verdict."""


class FidelityVerdict(StrictModel):
    ok: bool
    """Toutes les mesures faites sont dans leur seuil."""
    failed: list[MeasureName] = Field(default_factory=list, max_length=MAX_MEASURES)
    """Les mesures hors seuil (noms)."""
    measures: list[MeasureResult] = Field(default_factory=list, max_length=MAX_MEASURES)
    attempts: int = Field(default=1, ge=1, le=MAX_ATTEMPTS)
    """Tirages faits pour ce rendu (relances comprises)."""
