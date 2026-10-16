"""Cohérence entre vues (roadmap 2.5) : chaque caméra de photo garde dans ses métadonnées ce
que la photo a observé (largeur entre deux murs opposés vus, distance à un mur vu). La scène
reste unique ; quand un mur bouge ou qu'une photo contredit les murs, la violation
`inconsistent_views` (sévérité moyenne : la scène reste valide) le dit en clair, avec les
murs concernés et l'écart en centimètres, et l'utilisateur tranche : garder la photo (le mur
le moins sûr la suit, `pipeline.multiview.adopt_view`) ou l'écarter (`metadata.excluded` :
la photo et sa caméra restent dans le projet, mais ne pèsent plus sur le plan).
"""

from __future__ import annotations

from typing import Literal

from openplan.schemas.elements import Camera, Wall
from openplan.schemas.results import Violation
from openplan.schemas.scene import Level, Project

EXTENTS_KEY = "observed_extents_m"
"""Métadonnée de caméra : {"x": largeur, "y": profondeur} vues entre deux murs opposés."""
DISTANCES_KEY = "observed_distances_m"
"""Métadonnée de caméra : {"x-": d, "x+": d, "y-": d, "y+": d} au mur vu de ce côté."""
EXCLUDED_KEY = "excluded"
"""Métadonnée de caméra : True = photo écartée par l'utilisateur (conservée, sans effet)."""
VIEW_TOLERANCE = 0.15
"""Écart relatif toléré entre une étendue observée et la scène : la proposition mono-photo
est elle-même donnée à ±15 % (protocole d'évaluation)."""
AXIS_LABELS = {"x": "largeur (axe x)", "y": "profondeur (axe y)"}
SceneAxis = Literal["x", "y"]


def walls_box(level: Level) -> tuple[float, float, float, float] | None:
    """Emprise (x min, y min, x max, y max) des sommets portés par les murs ; None sans mur."""
    xs: list[float] = []
    ys: list[float] = []
    for element in level.elements.values():
        if not isinstance(element, Wall):
            continue
        for vid in (element.start_vertex, element.end_vertex):
            vertex = level.vertices.get(vid)
            if vertex is not None:
                xs.append(vertex.position.x)
                ys.append(vertex.position.y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def side_wall(level: Level, axis: SceneAxis, value: float, tol: float = 1e-6) -> Wall | None:
    """Le mur dont les deux sommets sont sur la droite axis = value (bord de l'emprise)."""
    for element in level.elements.values():
        if not isinstance(element, Wall):
            continue
        a = level.vertices.get(element.start_vertex)
        b = level.vertices.get(element.end_vertex)
        if a is None or b is None:
            continue
        if (
            abs(getattr(a.position, axis) - value) < tol
            and abs(getattr(b.position, axis) - value) < tol
        ):
            return element
    return None


def observed_extents(camera: Camera) -> dict[str, float]:
    raw = camera.metadata.get(EXTENTS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        axis: float(value)
        for axis, value in raw.items()
        if axis in AXIS_LABELS and isinstance(value, int | float) and value > 0
    }


def is_excluded(camera: Camera) -> bool:
    return camera.metadata.get(EXCLUDED_KEY) is True


def wall_pair_label(level: Level, axis: SceneAxis) -> str:
    """« Mur gauche et Mur droit » quand les deux bords de l'emprise sont des murs nommés."""
    box = walls_box(level)
    if box is None:
        return AXIS_LABELS[axis]
    lo, hi = (box[0], box[2]) if axis == "x" else (box[1], box[3])
    first, second = side_wall(level, axis, lo), side_wall(level, axis, hi)
    if first is not None and second is not None and first.name and second.name:
        return f"{first.name} et {second.name}"
    return f"les deux murs opposés ({AXIS_LABELS[axis]})"


def check_views(project: Project) -> list[Violation]:
    """Une étendue observée par une photo (non écartée) qui s'écarte de plus de
    `VIEW_TOLERANCE` de l'emprise des murs sur cet axe."""
    out: list[Violation] = []
    for level in project.levels.values():
        box = walls_box(level)
        if box is None:
            continue
        actual = {"x": box[2] - box[0], "y": box[3] - box[1]}
        for element in level.elements.values():
            if not isinstance(element, Camera) or element.image_id is None:
                continue
            if is_excluded(element):
                continue
            for axis, observed in observed_extents(element).items():
                scene = actual[axis]
                if scene <= 0:
                    continue
                ratio = observed / scene - 1.0
                if abs(ratio) > VIEW_TOLERANCE:
                    pair = wall_pair_label(level, axis)  # type: ignore[arg-type]
                    gap_cm = (observed - scene) * 100.0
                    out.append(
                        Violation(
                            type="inconsistent_views",
                            severity="medium",
                            element_ids=[element.id],
                            message=(
                                f"Photo « {element.name} » voit {pair} à {observed:.2f} m "
                                f"l'un de l'autre ; dans le plan ils sont à {scene:.2f} m "
                                f"(écart {gap_cm:+.0f} cm, {ratio * 100:+.0f} %). Garder la "
                                "photo déplace le mur le moins sûr ; l'écarter la laisse dans "
                                "le projet sans peser sur le plan."
                            ),
                        )
                    )
    return out
