"""Étape « floorplan » : arêtes sol-mur détectées → proposition de pièce rectangulaire.

La caméra est placée à l'origine du repère Manhattan, à une hauteur supposée
(`camera_height_m`, 1,50 m par défaut : téléphone tenu à hauteur de poitrine). Les murs
détectés sont `estimated` ; les murs que la photo ne montre pas sont `inferred` et placés
à une distance arbitraire derrière la caméra. L'utilisateur les déplace, puis calibre.

Acteur d'historique : "pipeline:floorplan". La politique de sources autorise cet acteur à
créer des éléments estimés ou inférés, jamais mesurés.
"""

from __future__ import annotations

import time
from uuid import UUID

from openplan.adapters.heuristic.box_detector import BoxAnalysis, FloorEdge
from openplan.scene import history
from openplan.scene.ops import detect_rooms
from openplan.schemas.common import Point2, Point3, Provenance, Source, Uncertainty
from openplan.schemas.elements import Camera, Vertex, Wall
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import Project

ACTOR = "pipeline:floorplan"
STEP = "floorplan.heuristic_box"
DEFAULT_CAMERA_HEIGHT_M = 1.5
HIDDEN_WALL_MARGIN_M = 0.5
"""Distance derrière la caméra à laquelle un mur non visible est placé."""
UNKNOWN_HALF_WIDTH_M = 2.0
"""Sans aucune arête sur un axe : demi-largeur supposée, centrée sur la caméra."""
INFERRED_CONFIDENCE = 0.2
ESTIMATED_SIGMA_M = 0.3


class DetectionError(ValueError):
    """La proposition ne peut pas s'appliquer (niveau non vide, image inconnue...)."""


def propose_floorplan(
    project: Project,
    level_id: UUID,
    analysis: BoxAnalysis,
    *,
    image_id: UUID,
    camera_height_m: float = DEFAULT_CAMERA_HEIGHT_M,
    run_id: UUID | None = None,
) -> tuple[Project, StepResult]:
    """Ajoute au niveau (vide) un rectangle proposé et la caméra estimée de la photo."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = "le niveau contient déjà des murs : la proposition ne s'applique qu'à un niveau vide"
        raise DetectionError(msg)
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise DetectionError(msg)
    model_ids = [analysis.camera.model_id]
    if analysis.frame is None or not analysis.floor_edges:
        return project, StepResult(
            step=STEP,
            confidence=0.0,
            warnings=[*analysis.warnings, "Aucune arête sol-mur : pas de proposition."],
            duration_s=time.perf_counter() - started,
            model_ids=model_ids,
        )
    model_ids.append("heuristic_box@0.1")
    h = camera_height_m
    assumptions = [
        Assumption(
            text=f"Hauteur de caméra supposée {h:.2f} m : toutes les distances en dépendent.",
            impact="high",
        ),
        Assumption(text="Pièce supposée rectangulaire (hypothèse Manhattan).", impact="medium"),
    ]
    warnings = list(analysis.warnings)
    xs, x_sources = _bounds(analysis.edges("y"), h, "latéral", assumptions)
    ys, y_sources = _bounds(analysis.edges("x"), h, "de face", assumptions)
    shift = (-xs[0], -ys[0])
    corners = [
        Point2(x=xs[0] + shift[0], y=ys[0] + shift[1]),
        Point2(x=xs[1] + shift[0], y=ys[0] + shift[1]),
        Point2(x=xs[1] + shift[0], y=ys[1] + shift[1]),
        Point2(x=xs[0] + shift[0], y=ys[1] + shift[1]),
    ]
    # Mur i relie les coins i et i+1 : bas (y min), droite (x max), haut (y max), gauche (x min).
    sides = [y_sources[0], x_sources[1], y_sources[1], x_sources[0]]
    names = ["Mur bas", "Mur droit", "Mur haut", "Mur gauche"]
    provenance = Provenance(
        image_ids=[image_id], step=STEP, model="heuristic_box@0.1", run_id=run_id
    )
    vertices = [
        Vertex(
            position=c,
            source=Source.ESTIMATED,
            confidence=0.4,
            uncertainty=Uncertainty(sigma=ESTIMATED_SIGMA_M),
        )
        for c in corners
    ]
    walls: list[Wall] = []
    for i in range(4):
        edge = sides[i]
        walls.append(
            Wall(
                name=names[i],
                start_vertex=vertices[i].id,
                end_vertex=vertices[(i + 1) % 4].id,
                source=Source.ESTIMATED if edge is not None else Source.INFERRED,
                confidence=edge.score if edge is not None else INFERRED_CONFIDENCE,
                uncertainty=Uncertainty(sigma=ESTIMATED_SIGMA_M if edge is not None else None),
                provenance=provenance,
            )
        )
    frame = analysis.frame
    camera = Camera(
        name="Photo",
        position=Point3(x=shift[0], y=shift[1], z=h),
        yaw=frame.yaw_hint,
        pitch=frame.pitch,
        roll=frame.roll,
        focal_px=frame.focal_px,
        image_id=image_id,
        source=Source.ESTIMATED,
        confidence=analysis.camera.confidence,
        provenance=provenance,
    )
    new_level = level.model_copy(
        update={
            "vertices": {**level.vertices, **{v.id: v for v in vertices}},
            "elements": {**level.elements, **{w.id: w for w in walls}, camera.id: camera},
        }
    )
    new_level = detect_rooms(new_level)
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project, after, actor=ACTOR, label="Proposer un plan depuis la photo", run_id=run_id
    )
    detected = [w for w in walls if w.source is Source.ESTIMATED]
    return after, StepResult(
        step=STEP,
        patch=after.history[-1].patch,
        confidence=min(w.confidence for w in detected),
        assumptions=assumptions,
        warnings=warnings,
        duration_s=time.perf_counter() - started,
        model_ids=model_ids,
    )


def _bounds(
    edges: list[FloorEdge], h: float, label: str, assumptions: list[Assumption]
) -> tuple[tuple[float, float], tuple[FloorEdge | None, FloorEdge | None]]:
    """Coordonnées (min, max) des deux murs d'un axe et l'arête qui les soutient (ou None)."""
    if not edges:
        assumptions.append(
            Assumption(
                text=f"Aucun mur {label} détecté : largeur supposée "
                f"{2 * UNKNOWN_HALF_WIDTH_M:.1f} m centrée sur la caméra.",
                impact="high",
            )
        )
        return (-UNKNOWN_HALF_WIDTH_M, UNKNOWN_HALF_WIDTH_M), (None, None)
    scaled = sorted(((e.offset * h, e) for e in edges), key=lambda t: t[0])
    if len(scaled) >= 2 and scaled[0][0] < 0 < scaled[-1][0]:
        return (scaled[0][0], scaled[-1][0]), (scaled[0][1], scaled[-1][1])
    offset, edge = scaled[-1] if scaled[-1][0] > 0 else scaled[0]
    assumptions.append(
        Assumption(
            text=f"Mur {label} opposé non visible : placé à {HIDDEN_WALL_MARGIN_M:.1f} m "
            "derrière la caméra.",
            impact="high",
        )
    )
    if offset > 0:
        return (-HIDDEN_WALL_MARGIN_M, offset), (None, edge)
    return (offset, HIDDEN_WALL_MARGIN_M), (edge, None)
