"""Étape « attic.apply » : combles validés par l'utilisateur → murs, pentes et caméra.

L'utilisateur part de l'`AtticEstimate` (étape attic.planes), corrige ce qu'il veut, puis
valide : chaque valeur garde sa source (`estimated`, `inferred`, ou `user_defined` si elle
a été modifiée). Le niveau doit être vide de murs. Rien n'est jamais `measured` ici : cela
reste réservé à une mesure au mètre saisie ensuite (calibration).

Repère : faîtage le long de X, pignons en x = 0 et x = longueur, égouts en y = 0 et
y = largeur ; la caméra de la photo, si connue, regarde le pignon en x = longueur.
"""

from __future__ import annotations

import math
import time
from uuid import UUID

from pydantic import Field, model_validator

from openplan.geom.roof import pitch_from_rise, slope_length
from openplan.pipeline.attic import Estimate
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.roof_windows import STANDARD_SIZE_M, RoofWindowSighting
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import detect_rooms
from openplan.schemas.common import (
    Point2,
    Point3,
    Provenance,
    Source,
    StrictModel,
    Uncertainty,
    source_rank,
)
from openplan.schemas.elements import (
    Camera,
    Ceiling,
    RoofPlane,
    RoofWindow,
    Room,
    Vertex,
    Wall,
)
from openplan.schemas.numbers import decimal
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import LEVEL_DEFAULT_NAMES, Project

STEP = "attic.apply"
CONFIDENCE: dict[Source, float] = {
    Source.MEASURED: 1.0,
    Source.USER_DEFINED: 1.0,
    Source.OBSERVED: 0.7,
    Source.ESTIMATED: 0.4,
    Source.INFERRED: 0.2,
    Source.GENERATED: 0.3,
}


class AtticGeometry(StrictModel):
    """Valeurs validées par l'utilisateur, chacune avec sa source."""

    length: Estimate
    """Entre les deux pignons (X)."""
    width: Estimate
    """Entre les deux égouts (Y)."""
    ridge_height: Estimate
    eave_height: Estimate
    """Hauteur des murs latéraux sous les pentes (0 si la pente touche le sol)."""
    camera_height: Estimate | None = None
    gable_distance: Estimate | None = None
    """De la caméra au pignon visible (x = longueur)."""
    image_id: UUID | None = None
    run_id: UUID | None = None
    thickness: float = Field(default=0.15, gt=0.0, lt=1.0)
    roof_windows: list[RoofWindowSighting] = Field(default_factory=list)
    """Fenêtres de toit vues sur la photo (C2), posées comme `RoofWindow` estimés."""

    @model_validator(mode="after")
    def _plausible(self) -> AtticGeometry:
        for name in ("length", "width", "ridge_height"):
            value = getattr(self, name).value
            if not 0.5 <= value <= 50.0:
                msg = f"{name} hors limites (0,5 à 50 m) : {decimal(value)}"
                raise ValueError(msg)
        if self.eave_height.value < 0.0:
            msg = "la hauteur d'égout ne peut pas être négative"
            raise ValueError(msg)
        if self.eave_height.value >= self.ridge_height.value:
            msg = "l'égout doit être plus bas que le faîtage"
            raise ValueError(msg)
        return self


def weaker(*sources: Source) -> Source:
    """La source la moins autoritaire (rang le plus faible) parmi celles données."""
    return min(sources, key=source_rank)


ROOF_WINDOW_WIDTH_M = (0.55, 1.2)
ROOF_WINDOW_LENGTH_M = (0.7, 1.6)
"""Tailles de châssis crédibles (les velux du commerce) ; une taille vue hors de ces bornes
est remplacée par la taille standard 78 × 98 cm."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sighted_roof_windows(
    g: AtticGeometry, roofs: list[RoofPlane], camera_x: float, provenance: Provenance
) -> list[RoofWindow]:
    """Fenêtres de toit vues sur la photo → `RoofWindow` estimés sur « Versant bas » (y = 0,
    à droite de la visée, la caméra regardant +X) ou « Versant haut » (y = largeur)."""
    low, high = roofs
    pitch = low.pitch
    if math.sin(pitch) < 0.1:
        return []
    length = g.length.value
    slope = slope_length(g.eave_height.value, pitch, g.ridge_height.value)
    out: list[RoofWindow] = []
    for seen in g.roof_windows:
        x = camera_x + seen.forward_m
        if not -0.3 <= x <= length + 0.3:
            continue
        roof = low if seen.lateral_m < 0.0 else high
        width, along = STANDARD_SIZE_M
        rise = (seen.z_top_m - seen.z_bottom_m) / math.sin(pitch)
        # Taille ajustée gardée seulement si elle reste crédible une fois à l'échelle du plan
        # (l'ajustement se fait à l'échelle du modèle de profondeur) ; sinon taille standard.
        if (
            not seen.standard_size
            and ROOF_WINDOW_WIDTH_M[0] <= seen.width_m <= ROOF_WINDOW_WIDTH_M[1]
            and ROOF_WINDOW_LENGTH_M[0] <= rise <= ROOF_WINDOW_LENGTH_M[1]
        ):
            width, along = seen.width_m, rise
        if along + 0.1 > slope:
            continue
        # Le centre vu est le plus sûr : la distance à l'égout se compte depuis lui.
        middle = (seen.z_bottom_m + seen.z_top_m) / 2.0
        distance = _clamp(
            (middle - g.eave_height.value) / math.sin(pitch) - along / 2.0, 0.0, slope - along
        )
        # Versant haut : son égout va de (L, W) à (0, W), l'offset se compte depuis x = L.
        start = x - width / 2.0 if roof is low else length - x - width / 2.0
        offset = _clamp(start, 0.0, max(0.0, length - width))
        if any(
            w.roof == roof.id
            and min(w.offset + w.width, offset + width) - max(w.offset, offset)
            > 0.5 * min(w.width, width)
            for w in out
        ):
            continue
        out.append(
            RoofWindow(
                name="Velux vu sur la photo",
                roof=roof.id,
                offset=offset,
                distance=distance,
                width=width,
                length=along,
                source=Source.ESTIMATED,
                confidence=min(0.9, max(0.1, seen.score)),
                uncertainty=Uncertainty(sigma=0.3),
                provenance=provenance.model_copy(update={"model": seen.model_id}),
            )
        )
    return out


def apply_attic(
    project: Project, level_id: UUID, geometry: AtticGeometry
) -> tuple[Project, StepResult]:
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = "le niveau contient déjà des murs : les combles ne s'appliquent qu'à un niveau vide"
        raise DetectionError(msg)
    if geometry.image_id is not None and geometry.image_id not in project.images:
        msg = f"image inconnue : {geometry.image_id}"
        raise DetectionError(msg)

    g = geometry
    length, width = g.length.value, g.width.value
    provenance = Provenance(
        image_ids=[g.image_id] if g.image_id else [], step=STEP, run_id=g.run_id
    )
    plan_source = weaker(g.length.source, g.width.source)
    corners = [
        Point2(x=0.0, y=0.0),
        Point2(x=length, y=0.0),
        Point2(x=length, y=width),
        Point2(x=0.0, y=width),
    ]
    vertices = [
        Vertex(
            position=c,
            source=plan_source,
            confidence=CONFIDENCE[plan_source],
            uncertainty=Uncertainty(sigma=max(g.length.sigma, g.width.sigma)),
        )
        for c in corners
    ]
    # Mur i relie les coins i et i+1 : égout bas (y = 0), pignon (x = L), égout haut (y = W),
    # pignon (x = 0).
    specs = [
        ("Égout bas", g.length, g.eave_height.value),
        ("Pignon", g.width, None),
        ("Égout haut", g.length, g.eave_height.value),
        ("Pignon", g.width, None),
    ]
    walls: list[Wall] = []
    for i, (name, along, height) in enumerate(specs):
        source = along.source if height is None else weaker(along.source, g.eave_height.source)
        walls.append(
            Wall(
                name=name,
                start_vertex=vertices[i].id,
                end_vertex=vertices[(i + 1) % 4].id,
                thickness=g.thickness,
                height=height,
                source=source,
                confidence=CONFIDENCE[source],
                uncertainty=Uncertainty(sigma=along.sigma),
                provenance=provenance,
                # Aspect par défaut d'un comble ancien : pignons en pierre, jambettes enduites ;
                # modifiable élément par élément (metadata.finish).
                metadata={"finish": "stone" if height is None else "plaster"},
            )
        )
    pitch = pitch_from_rise(g.ridge_height.value - g.eave_height.value, width / 2.0)
    roof_source = weaker(g.ridge_height.source, g.eave_height.source, g.width.source)
    roofs = [
        RoofPlane(
            name=name,
            eave_start=corners[i],
            eave_end=corners[(i + 1) % 4],
            eave_height=g.eave_height.value,
            pitch=pitch,
            source=roof_source,
            confidence=CONFIDENCE[roof_source],
            uncertainty=Uncertainty(sigma=g.ridge_height.sigma),
            provenance=provenance,
            metadata={"finish": "wood_boards"},
        )
        for i, name in ((0, "Versant bas"), (2, "Versant haut"))
    ]
    elements = {**{w.id: w for w in walls}, **{r.id: r for r in roofs}}
    if g.camera_height is not None:
        x = length - g.gable_distance.value if g.gable_distance is not None else length / 2.0
        camera = Camera(
            name="Photo",
            position=Point3(x=min(max(x, 0.0), length), y=width / 2.0, z=g.camera_height.value),
            yaw=0.0,
            pitch=0.0,
            image_id=g.image_id,
            source=g.camera_height.source,
            confidence=CONFIDENCE[g.camera_height.source],
            provenance=provenance,
        )
        elements[camera.id] = camera
        for window in sighted_roof_windows(g, roofs, camera.position.x, provenance):
            elements[window.id] = window
    new_level = level.model_copy(
        update={
            "height": g.ridge_height.value,
            "height_source": g.ridge_height.source,
            "vertices": {**level.vertices, **{v.id: v for v in vertices}},
            "elements": {**level.elements, **elements},
        }
    )
    new_level = detect_rooms(new_level)
    # Sous des pentes, le plafond plat au faîtage n'a pas de sens : la pièce prend les versants.
    rooms = [e for e in new_level.elements.values() if isinstance(e, Room)]
    updated = dict(new_level.elements)
    for room in rooms:
        updated[room.id] = room.model_copy(update={"name": "Combles", "room_type": "attic"})
        for rid in (r.id for r in roofs):
            updated[rid] = updated[rid].model_copy(update={"rooms": [room.id]})
    for eid, e in list(updated.items()):
        if isinstance(e, Ceiling):
            del updated[eid]
    new_level = new_level.model_copy(update={"elements": updated})
    # Des combles reconnus : le niveau est un grenier (R2). Un nom choisi par l'utilisateur
    # reste ; seul le nom par défaut suit la sorte de niveau.
    level_update: dict[str, object] = {"kind": "attic"}
    if new_level.name in LEVEL_DEFAULT_NAMES.values():
        level_update["name"] = LEVEL_DEFAULT_NAMES["attic"]
    new_level = new_level.model_copy(update=level_update)
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    # L'utilisateur a validé le formulaire : c'est lui l'acteur (seul autorisé à créer des
    # éléments `user_defined`) ; la provenance de chaque élément garde l'étape et le run.
    after = history.commit(
        project, after, actor=USER_ACTOR, label="Créer les combles estimés", run_id=g.run_id
    )
    sources = {
        "longueur": g.length.source,
        "largeur": g.width.source,
        "faîtage": g.ridge_height.source,
        "égout": g.eave_height.source,
    }
    assumptions = [
        Assumption(
            text="Combles symétriques : faîtage au milieu, deux versants de même pente.",
            impact="medium",
        ),
        Assumption(
            text="Sources : " + ", ".join(f"{k} {v.value}" for k, v in sources.items()) + ".",
            impact="high" if any(s is not Source.USER_DEFINED for s in sources.values()) else "low",
        ),
    ]
    seen = sum(isinstance(e, RoofWindow) for e in elements.values())
    if seen:
        assumptions.append(
            Assumption(
                text=f"{seen} fenêtre(s) de toit vue(s) sur la photo, posée(s) à leur place "
                "estimée (±30 cm) : à déplacer ou supprimer si besoin.",
                impact="medium",
            )
        )
    return after, StepResult(
        step=STEP,
        patch=after.history[-1].patch,
        confidence=min(CONFIDENCE[s] for s in sources.values()),
        assumptions=assumptions,
        duration_s=time.perf_counter() - started,
    )
