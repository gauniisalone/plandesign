"""Évaluation sur photos réelles mesurées (roadmap 2.1) : dossier → tableau d'erreurs.

Chaque sous-dossier de la racine est une pièce : `photo.jpg` (ou .png, .jpeg, .webp) et
`mesures.txt` (lignes « clé = valeur » en mètres). Le script enchaîne les étapes locales
(heuristique de lignes, profondeur métrique, ajustement de plans) et compare chaque grandeur
estimée à la mesure correspondante. Aucune donnée ne quitte la machine ; le rapport est écrit
dans la racine. Sans mesure, pas de chiffre : une clé absente laisse la ligne vide.

Plusieurs photos d'une même pièce (roadmap 2.5) : `photo2.jpg`, `photo3.jpg`… sont
confrontées à la scène issue de la première (`pipeline.multiview`) ; les lignes « heuristique
n vues » donnent les grandeurs après chaque photo. La profondeur n'est évaluée que sur la
première photo.
"""

from __future__ import annotations

import re
import statistics
import time
from pathlib import Path
from uuid import UUID

from pydantic import Field

from openplan.adapters.protocols import DepthEstimator, RoomElementDetector
from openplan.pipeline.attic import AtticEstimate, rescale, run_attic
from openplan.pipeline.depthplan import run_depthplan
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import IngestionError, import_image
from openplan.pipeline.openings import run_openings
from openplan.scene.factory import new_project
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.elements import Camera, Wall
from openplan.schemas.scene import Project

KEYS: dict[str, str] = {
    "hauteur_photo": "hauteur de l'objectif au-dessus du sol (m)",
    "distance_mur_face": "de l'objectif au mur vu en face (m)",
    "hauteur_plafond": "hauteur sous plafond (m)",
    "largeur": "d'un mur latéral à l'autre (m)",
    "longueur": "du mur derrière la photo au mur de face (m)",
    "faitage": "hauteur au faîtage (combles, m)",
    "egout": "hauteur des murs latéraux sous pente (combles, m)",
    "largeur_egouts": "largeur entre les deux égouts (combles, m)",
    "fenetre_largeur": "largeur de la plus large fenêtre visible (m)",
    "porte_largeur": "largeur de la plus large porte visible (m)",
    "hauteur_photo2": "hauteur de l'objectif pour photo2.jpg (m), sinon hauteur_photo",
    "hauteur_photo3": "hauteur de l'objectif pour photo3.jpg (m), sinon hauteur_photo",
}
PHOTO_NAMES = ("photo.jpg", "photo.jpeg", "photo.png", "photo.webp")
_PHOTO_RE = re.compile(r"^photo(\d*)\.(jpe?g|png|webp)$", re.IGNORECASE)
MEASURES_FILE = "mesures.txt"
DEFAULT_CAMERA_HEIGHT_M = 1.5


class MetricRow(StrictModel):
    metric: str
    step: str
    estimated: float | None
    truth: float
    error_pct: float | None = None

    @classmethod
    def build(cls, metric: str, step: str, estimated: float | None, truth: float) -> MetricRow:
        error = None if estimated is None or truth <= 0 else (estimated / truth - 1.0) * 100.0
        return cls(metric=metric, step=step, estimated=estimated, truth=truth, error_pct=error)


class RoomEvaluation(StrictModel):
    name: str
    photo: str
    photos: list[str] = Field(default_factory=list)
    """Toutes les photos utilisées, la première en tête."""
    rows: list[MetricRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_s: float = 0.0


class EvaluationReport(StrictModel):
    root: str
    rooms: list[RoomEvaluation] = Field(default_factory=list)
    medians: dict[str, float] = Field(default_factory=dict)
    """Médiane des erreurs absolues (%) par métrique, sur les pièces qui la fournissent."""


def parse_measures(text: str) -> tuple[dict[str, float], list[str]]:
    """« clé = 1,45 » par ligne ; virgule acceptée ; « # » commente. Clés inconnues signalées."""
    values: dict[str, float] = {}
    warnings: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            warnings.append(f"ligne ignorée (pas de « = ») : {line!r}")
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        key = key.lower().replace(" ", "_").replace("-", "_")
        if key not in KEYS:
            warnings.append(f"clé inconnue ignorée : {key} (connues : {', '.join(KEYS)})")
            continue
        try:
            number = float(value.replace(",", ".").replace("m", "").strip())
        except ValueError:
            warnings.append(f"valeur illisible pour {key} : {value!r}")
            continue
        if number <= 0:
            warnings.append(f"{key} doit être positif : {number}")
            continue
        values[key] = number
    return values, warnings


def find_photos(room_dir: Path) -> list[Path]:
    """`photo.*` puis `photo2.*`, `photo3.*`… dans l'ordre des numéros (une par numéro)."""
    found: dict[int, Path] = {}
    for path in sorted(room_dir.iterdir()):
        match = _PHOTO_RE.match(path.name)
        if match and path.is_file():
            found.setdefault(int(match.group(1) or 1), path)
    return [found[n] for n in sorted(found)]


def _heuristic_rows(
    project: Project, level_id: UUID, truth: dict[str, float], step: str = "heuristique"
) -> list[MetricRow]:
    """Distance au mur de face, largeur et longueur d'après le rectangle (murs estimés
    seulement : un mur inféré n'est pas une estimation)."""
    level = project.levels[level_id]
    camera = next((e for e in level.elements.values() if isinstance(e, Camera)), None)
    if camera is None:
        return []
    walls = {w.name: w for w in level.elements.values() if isinstance(w, Wall) and w.name}

    def coordinate(name: str, axis: str) -> float | None:
        wall = walls.get(name)
        if wall is None or wall.source is not Source.ESTIMATED:
            return None
        return float(getattr(level.vertices[wall.start_vertex].position, axis))

    rows: list[MetricRow] = []
    far = coordinate("Mur haut", "y")
    if far is not None and "distance_mur_face" in truth:
        rows.append(
            MetricRow.build(
                "distance_mur_face", step, far - camera.position.y, truth["distance_mur_face"]
            )
        )
    left, right = coordinate("Mur gauche", "x"), coordinate("Mur droit", "x")
    if left is not None and right is not None and "largeur" in truth:
        rows.append(MetricRow.build("largeur", step, abs(right - left), truth["largeur"]))
    near = coordinate("Mur bas", "y")
    if near is not None and far is not None and "longueur" in truth:
        rows.append(MetricRow.build("longueur", step, abs(far - near), truth["longueur"]))
    return rows


def _attic_rows(estimate: AtticEstimate, truth: dict[str, float]) -> list[MetricRow]:
    pairs = (
        ("hauteur_photo", estimate.camera_height),
        ("distance_mur_face", estimate.gable_distance),
        ("faitage", estimate.ridge_height),
        ("hauteur_plafond", estimate.ceiling_height),
        ("largeur_egouts", estimate.width),
        ("largeur", estimate.width),
    )
    rows = [
        MetricRow.build(key, "profondeur brute", value.value if value else None, truth[key])
        for key, value in pairs
        if key in truth
    ]
    if "egout" in truth and estimate.eave_heights:
        rows.append(
            MetricRow.build(
                "egout",
                "profondeur brute",
                statistics.mean(e.value for e in estimate.eave_heights),
                truth["egout"],
            )
        )
    return rows


OPENING_KEYS = (("fenetre_largeur", "window"), ("porte_largeur", "door"))


def _opening_rows(
    project: Project,
    work_dir: Path,
    level_id: UUID,
    image_id: UUID,
    truth: dict[str, float],
    detector: RoomElementDetector,
) -> tuple[list[MetricRow], list[str]]:
    """Portes et fenêtres vues sur la première photo, projetées par sa caméra heuristique :
    une ligne « ouvertures » par clé mesurée (la plus large gardée ; « non vu » sinon), et
    le compte gardées / écartées avec les motifs dans les avertissements."""
    run = run_openings(project, work_dir, level_id, image_id, work_dir / "runs", detector=detector)
    kept = [o for o in run.observations if o.filtered is None]
    rows = [
        MetricRow.build(
            key,
            "ouvertures",
            max((o.width_m for o in kept if o.kind == kind and o.width_m), default=None),
            truth[key],
        )
        for key, kind in OPENING_KEYS
        if key in truth
    ]
    warnings = [
        f"ouvertures : {len(kept)} gardée(s) sur {len(run.observations)} boîte(s) "
        f"({sum(o.kind == 'door' for o in kept)} porte(s), "
        f"{sum(o.kind == 'window' for o in kept)} fenêtre(s))"
    ]
    warnings.extend(f"ouvertures : {w}" for w in run.result.warnings)
    return rows, warnings


def evaluate_room(
    room_dir: Path,
    work_dir: Path,
    *,
    estimator: DepthEstimator | None,
    detector: RoomElementDetector | None = None,
) -> RoomEvaluation:
    started = time.perf_counter()
    name = room_dir.name
    photos = find_photos(room_dir)
    measures_path = room_dir / MEASURES_FILE
    if not photos or not measures_path.is_file():
        return RoomEvaluation(
            name=name,
            photo="",
            warnings=[f"il faut {PHOTO_NAMES[0]} (ou .png/.webp) et {MEASURES_FILE}"],
        )
    photo = photos[0]
    truth, warnings = parse_measures(measures_path.read_text(encoding="utf-8"))
    evaluation = RoomEvaluation(
        name=name, photo=photo.name, photos=[p.name for p in photos], warnings=warnings
    )
    if not truth:
        evaluation.warnings.append("aucune mesure lisible : rien à comparer")
        return evaluation
    project = new_project(name)
    try:
        project, ref, quality = import_image(
            project, work_dir, photo.read_bytes(), original_name=photo.name
        )
    except IngestionError as exc:
        evaluation.warnings.append(f"photo illisible : {exc}")
        return evaluation
    evaluation.warnings.extend(quality)
    level_id = next(iter(project.levels))
    camera_height = truth.get("hauteur_photo", DEFAULT_CAMERA_HEIGHT_M)
    try:
        run = detect_and_propose(
            project, work_dir, level_id, ref.id, work_dir / "runs", camera_height_m=camera_height
        )
        evaluation.rows.extend(_heuristic_rows(run.project, level_id, truth))
        evaluation.warnings.extend(f"heuristique : {w}" for w in run.floorplan.warnings)
        if detector is not None:
            rows, notes = _opening_rows(run.project, work_dir, level_id, ref.id, truth, detector)
            evaluation.rows.extend(rows)
            evaluation.warnings.extend(notes)
        _evaluate_extra_views(run.project, photos[1:], work_dir, truth, evaluation, camera_height)
    except DetectionError as exc:
        evaluation.warnings.append(f"heuristique : {exc}")
    if detector is None and any(key in truth for key, _ in OPENING_KEYS):
        evaluation.warnings.append("ouvertures non évaluées (poids OWLv2 absents ou --no-openings)")
    if estimator is None:
        evaluation.warnings.append("profondeur non évaluée (poids absents ou --no-depth)")
    else:
        try:
            plan = run_depthplan(
                project,
                work_dir,
                level_id,
                ref.id,
                work_dir / "runs",
                estimator=estimator,
                camera_height_m=truth.get("hauteur_photo"),
            )
            evaluation.rows.extend(
                _heuristic_rows(plan.project, level_id, truth, "profondeur plan")
            )
            if plan.camera_height_m is not None and "hauteur_photo" in truth:
                evaluation.rows.append(
                    MetricRow.build(
                        "hauteur_photo",
                        "profondeur plan",
                        plan.camera_height_m,
                        truth["hauteur_photo"],
                    )
                )
            evaluation.warnings.extend(f"profondeur plan : {w}" for w in plan.result.warnings)
        except DetectionError as exc:
            evaluation.warnings.append(f"profondeur plan : {exc}")
        attic = run_attic(project, work_dir, ref.id, work_dir / "runs", estimator=estimator)
        evaluation.rows.extend(_attic_rows(attic.estimate, truth))
        if "hauteur_photo" in truth and attic.estimate.camera_height is not None:
            rescaled = rescale(attic.estimate, truth["hauteur_photo"])
            evaluation.rows.extend(
                r.model_copy(update={"step": "profondeur recalée"})
                for r in _attic_rows(rescaled, truth)
                if r.metric != "hauteur_photo"
            )
        evaluation.warnings.extend(f"profondeur : {w}" for w in attic.result.warnings)
    evaluation.duration_s = time.perf_counter() - started
    return evaluation


def _evaluate_extra_views(
    scene: Project,
    extra: list[Path],
    work_dir: Path,
    truth: dict[str, float],
    evaluation: RoomEvaluation,
    camera_height: float,
) -> None:
    """Photos 2, 3… confrontées à la scène de la première ; une ligne par grandeur et par
    nombre de vues, les contradictions dans les avertissements."""
    level_id = next(iter(scene.levels))
    for n, photo in enumerate(extra, start=2):
        try:
            scene, ref, quality = import_image(
                scene, work_dir, photo.read_bytes(), original_name=photo.name
            )
        except IngestionError as exc:
            evaluation.warnings.append(f"photo {n} illisible : {exc}")
            continue
        evaluation.warnings.extend(f"photo {n} : {w}" for w in quality)
        height = truth.get(f"hauteur_photo{n}", camera_height)
        try:
            run = detect_and_propose(
                scene, work_dir, level_id, ref.id, work_dir / "runs", camera_height_m=height
            )
        except DetectionError as exc:
            evaluation.warnings.append(f"photo {n} : {exc}")
            continue
        scene = run.project
        evaluation.rows.extend(_heuristic_rows(scene, level_id, truth, f"heuristique {n} vues"))
        evaluation.warnings.extend(f"photo {n} : {w}" for w in run.floorplan.warnings)


def evaluate_root(
    root: Path,
    work_dir: Path,
    *,
    estimator: DepthEstimator | None,
    detector: RoomElementDetector | None = None,
) -> EvaluationReport:
    report = EvaluationReport(root=str(root))
    for room_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        report.rooms.append(
            evaluate_room(
                room_dir, work_dir / room_dir.name, estimator=estimator, detector=detector
            )
        )
    errors: dict[str, list[float]] = {}
    for room in report.rooms:
        for row in room.rows:
            if row.error_pct is not None:
                errors.setdefault(f"{row.metric} ({row.step})", []).append(abs(row.error_pct))
    report.medians = {k: statistics.median(v) for k, v in errors.items()}
    return report


def render_markdown(report: EvaluationReport) -> str:
    out = [
        "# Évaluation sur photos réelles",
        "",
        f"Racine : `{report.root}` · {len(report.rooms)} pièce(s). Erreur = estimé / mesuré − 1. "
        "Rien ne quitte la machine ; les mesures viennent de `mesures.txt`.",
        "",
    ]
    for room in report.rooms:
        out.append(f"## {room.name} ({', '.join(room.photos) or room.photo or 'sans photo'})")
        out.append("")
        if room.rows:
            out.append("| Grandeur | Étape | Estimé | Mesuré | Erreur |")
            out.append("|---|---|---|---|---|")
            for r in room.rows:
                est = f"{r.estimated:.2f} m" if r.estimated is not None else "non vu"
                err = f"{r.error_pct:+.0f} %" if r.error_pct is not None else "—"
                out.append(f"| {r.metric} | {r.step} | {est} | {r.truth:.2f} m | {err} |")
            out.append("")
        for w in room.warnings:
            out.append(f"- ⚠ {w}")
        out.append("")
    if report.medians:
        out.append("## Médiane des erreurs absolues")
        out.append("")
        out.append("| Grandeur (étape) | Médiane |")
        out.append("|---|---|")
        for key, value in sorted(report.medians.items()):
            out.append(f"| {key} | {value:.0f} % |")
        out.append("")
    return "\n".join(out)


def write_report(report: EvaluationReport, root: Path) -> tuple[Path, Path]:
    md = root / "rapport.md"
    js = root / "rapport.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return md, js
