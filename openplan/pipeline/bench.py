"""KAN-33 : banc d'évaluation de l'estimation sur données réelles, et réglage de la confiance.

Lancé par `openplan bench [racine]` (racine par défaut : `data/eval`, jamais versionnée). Trois
sortes de jeux, toutes estimées par le code actuel (lignes de fuite + profondeur + plans) :

- **scène de contrôle KAN-27** (vérité connue : pignon 5,00 m, faîtage 3,54 m, caméra
  1,45 m…) : profondeur enregistrée `tests/fixtures/control_attic_depth.npz`, sans GPU ; on y
  mesure l'erreur selon la source de l'échelle (modèle, hauteur saisie, hauteur supposée,
  mesure de référence), le sol hors champ (bas de l'image retiré) et la focale ;
- **pièce avec une longueur connue** : sous-dossier contenant `regle.json` (longueur en
  mètres et, par photo, les deux extrémités repérées à la main en pixels) ; la longueur 3D
  estimée de la règle est comparée à la vraie, avec la hauteur de prise de vue et la focale
  estimées ;
- **autre sous-dossier avec photos** : cotes estimées, rapportées sans vérité.

Profondeur des photos : `<photo>.depth.npy` à côté de la photo (calculée une fois, modèle sur
GPU) ; absente, elle est calculée et enregistrée si un estimateur est donné, sinon la photo est
sautée. Rien ne quitte la racine : le rapport est écrit dans `<racine>/rapport-kan33.md` (et
`.json`). Aucune photo ni chemin n'est dans le code.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.exif_camera import ExifCameraEstimator
from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import DepthEstimator, ImageArray
from openplan.pipeline.attic import AtticEstimate, Estimate, analyse_attic, rescale
from openplan.pipeline.confidence import EstimateConfidence, ScaleSource, assess
from openplan.pipeline.depthplan import observe_room
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.schemas.common import StrictModel
from openplan.schemas.numbers import decimal, metres

Float = NDArray[np.float64]
REPORT_NAME = "rapport-kan33"
RULER_FILE = "regle.json"
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
DEPTH_SUFFIX = ".depth.npy"
CONTROL_DEPTH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "control_attic_depth.npz"
)

# Vérité de la scène de contrôle KAN-27 (tests/control_attic.py, recopiée : le paquet ne dépend
# pas des tests). Faîtage = égout + largeur / 2 × tan(pente), pente tirée de la cote du velux.
CONTROL_WIDTH = 5.0
CONTROL_EAVE = 1.0
CONTROL_CAMERA = 1.45
CONTROL_GABLE = 4.5
CONTROL_RIDGE = CONTROL_EAVE + CONTROL_WIDTH / 2 * math.tan(math.asin(0.92 / 1.29))
CONTROL_FLOOR_CROP = 0.6
"""Part haute de l'image gardée pour simuler une photo levée, sol hors champ (KAN-45)."""
FOCAL_SPREAD = (0.8, 1.25)
"""Focale faussée de −20 % / +25 % (écart typique entre focale de repli et vraie focale)."""


class Row(StrictModel):
    case: str
    """Jeu et configuration (« contrôle · hauteur saisie 1,45 m »…)."""
    metric: str
    estimated: float | None
    truth: float | None = None
    error: float | None = None
    """Relative, estimé / vrai − 1."""

    @classmethod
    def build(cls, case: str, metric: str, estimated: float | None, truth: float | None) -> Row:
        error = None
        if estimated is not None and truth:
            error = estimated / truth - 1.0
        return cls(case=case, metric=metric, estimated=estimated, truth=truth, error=error)


class PhotoBench(StrictModel):
    room: str
    photo: str
    """Nom du fichier seulement."""
    focal_px: float
    focal_from_vps: bool
    focal_35mm: float
    camera_height_model_m: float | None = None
    """Distance au sol vue dans la profondeur, échelle du modèle."""
    floor_out_of_view: bool = False
    ruler_model_m: float | None = None
    ruler_app_m: float | None = None
    """Règle à l'échelle de l'app : hauteur supposée 1,50 m (par défaut)."""
    ruler_prior_focal_m: float | None = None
    """Règle à l'échelle de l'app, focale de repli au lieu de celle des lignes."""
    camera_height_from_ruler_m: float | None = None
    """Hauteur de prise de vue que la règle impose à la profondeur."""
    attic: dict[str, float | None] = Field(default_factory=dict)
    confidence: EstimateConfidence | None = None
    notes: list[str] = Field(default_factory=list)


class BenchReport(StrictModel):
    rows: list[Row] = Field(default_factory=list)
    photos: list[PhotoBench] = Field(default_factory=list)
    medians: dict[str, float] = Field(default_factory=dict)
    """Erreur médiane absolue des cotes par configuration (clé = configuration)."""
    notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ scène de contrôle
def _value(e: Estimate | None) -> float | None:
    return None if e is None else float(e.value)


def control_cotes(est: AtticEstimate) -> dict[str, float | None]:
    """Cotes comparables à la vérité du contrôle (largeur prise entre égouts estimés)."""
    return {
        "faîtage": _value(est.ridge_height),
        "largeur (pignon)": _value(est.width),
        "distance au pignon": _value(est.gable_distance),
    }


CONTROL_TRUTH = {
    "faîtage": CONTROL_RIDGE,
    "largeur (pignon)": CONTROL_WIDTH,
    "distance au pignon": CONTROL_GABLE,
}


def _median_abs(rows: list[Row]) -> float | None:
    errors = [abs(r.error) for r in rows if r.error is not None]
    return statistics.median(errors) if errors else None


def _control_rows(case: str, est: AtticEstimate, *, camera: bool) -> list[Row]:
    rows = [Row.build(case, k, v, CONTROL_TRUTH[k]) for k, v in control_cotes(est).items()]
    if camera:
        rows.append(Row.build(case, "hauteur de prise de vue", _value(est.camera_height), 1.45))
    return rows


def bench_control(depth_path: Path = CONTROL_DEPTH) -> tuple[list[Row], dict[str, float]]:
    """Erreurs sur la scène de contrôle, par configuration ; et la médiane de chacune."""
    data = np.load(depth_path)
    depth = data["depth"].astype(np.float32)
    focal = float(data["focal_px"])
    h, w = depth.shape
    pp = (w / 2.0, h / 2.0)
    raw, *_ = analyse_attic(depth, focal, pp)
    rows: list[Row] = []
    medians: dict[str, float] = {}

    def add(key: str, case_rows: list[Row]) -> None:
        rows.extend(case_rows)
        m = _median_abs([r for r in case_rows if r.metric in CONTROL_TRUTH])
        if m is not None:
            medians[key] = m

    add(
        "contrôle · échelle du modèle",
        _control_rows("contrôle · échelle du modèle", raw, camera=True),
    )
    add(
        "contrôle · hauteur saisie 1,45 m",
        _control_rows(
            "contrôle · hauteur saisie 1,45 m", rescale(raw, CONTROL_CAMERA), camera=False
        ),
    )
    add(
        "contrôle · hauteur supposée 1,50 m",
        _control_rows(
            "contrôle · hauteur supposée 1,50 m",
            rescale(raw, DEFAULT_CAMERA_HEIGHT_M, entered=False),
            camera=True,
        ),
    )
    # Mesure de référence (KAN-66) : chaque cote vraie prise à tour de rôle comme référence,
    # erreur des autres après recalage.
    ref_rows: list[Row] = []
    cotes = control_cotes(raw)
    for ref_key, ref_value in cotes.items():
        if not ref_value:
            continue
        factor = CONTROL_TRUTH[ref_key] / ref_value
        for key, value in cotes.items():
            if key != ref_key:
                ref_rows.append(
                    Row.build(
                        f"contrôle · référence {ref_key}",
                        key,
                        None if value is None else value * factor,
                        CONTROL_TRUTH[key],
                    )
                )
    add("contrôle · mesure de référence", ref_rows)
    # Égout non vu : vérité 1,00 m.
    eave = (
        statistics.mean(e.value for e in rescale(raw, CONTROL_CAMERA).eave_heights)
        if raw.eave_heights
        else None
    )
    eave_row = Row.build("contrôle · hauteur saisie 1,45 m", "égout", eave, CONTROL_EAVE)
    rows.append(eave_row)
    if eave_row.error is not None:
        medians["contrôle · égout"] = abs(eave_row.error)
    # Sol hors champ : bas de l'image retiré, hauteur saisie (la vraie).
    top = depth[: int(h * CONTROL_FLOOR_CROP)]
    lifted, *_ = analyse_attic(top, focal, pp, camera_height_m=CONTROL_CAMERA)
    case = "contrôle · sol hors champ (hauteur saisie 1,45 m)"
    add(case, _control_rows(case, lifted, camera=False))
    # Focale faussée.
    for k in FOCAL_SPREAD:
        est, *_ = analyse_attic(depth, focal * k, pp)
        case = f"contrôle · focale × {decimal(k)}"
        add(case, _control_rows(case, rescale(est, CONTROL_CAMERA), camera=False))
    return rows, medians


# ------------------------------------------------------------------ règle 3D
def ruler_length(
    depth: NDArray[np.float32] | Float,
    focal_px: float,
    principal_point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    samples: int = 81,
) -> float | None:
    """Longueur 3D d'un segment droit vu de a à b (pixels). Pour un segment droit, l'inverse
    de la profondeur varie linéairement le long de son image : 1/z est ajusté le long du
    segment (moindres carrés, points aberrants écartés une fois), puis les deux extrémités sont
    rétro-projetées. Robuste à un pixel d'extrémité qui tomberait sur le fond."""
    h, w = depth.shape
    ts = np.linspace(0.0, 1.0, samples)
    us = a[0] + ts * (b[0] - a[0])
    vs = a[1] + ts * (b[1] - a[1])
    inside = (us >= 0) & (us <= w - 1) & (vs >= 0) & (vs <= h - 1)
    inner = inside & (ts >= 0.04) & (ts <= 0.96)
    if inner.sum() < 8:
        return None
    zs = np.array(
        [
            float(
                np.median(depth[max(0, int(v) - 1) : int(v) + 2, max(0, int(u) - 1) : int(u) + 2])
            )
            for u, v in zip(us[inner], vs[inner], strict=True)
        ]
    )
    t = ts[inner]
    keep = np.isfinite(zs) & (zs > 0)
    t, inv = t[keep], 1.0 / zs[keep]
    if len(t) < 8:
        return None
    coef = np.polyfit(t, inv, 1)
    resid = np.abs(np.polyval(coef, t) - inv)
    good = resid <= 3.0 * max(float(np.median(resid)), 1e-9)
    coef = np.polyfit(t[good], inv[good], 1)
    cx, cy = principal_point

    def point(u: float, v: float, tt: float) -> Float:
        z = 1.0 / float(np.polyval(coef, tt))
        return np.array([(u - cx) * z / focal_px, (v - cy) * z / focal_px, z])

    if np.polyval(coef, 0.0) <= 0 or np.polyval(coef, 1.0) <= 0:
        return None
    return float(np.linalg.norm(point(*b, 1.0) - point(*a, 0.0)))


# ------------------------------------------------------------------ photos
def _load(path: Path) -> ImageArray:
    from PIL import Image, ImageOps

    with Image.open(path) as raw:
        rgb = np.asarray((ImageOps.exif_transpose(raw) or raw).convert("RGB"), dtype=np.uint8)
    return ImageArray(image_id=uuid4(), pixels=np.ascontiguousarray(rgb))


def photo_depth(path: Path, image: ImageArray, estimator: DepthEstimator | None) -> Float | None:
    cache = path.with_suffix(DEPTH_SUFFIX)
    if cache.is_file():
        return np.asarray(np.load(cache), dtype=np.float64)
    if estimator is None:
        return None
    depth = np.asarray(estimator.predict(image).depth, dtype=np.float32)
    np.save(cache, depth)
    return np.asarray(depth, dtype=np.float64)


def bench_photo(
    room: str,
    path: Path,
    estimator: DepthEstimator | None,
    ruler: tuple[float, tuple[float, float], tuple[float, float]] | None = None,
) -> tuple[PhotoBench | None, list[Row]]:
    image = _load(path)
    depth = photo_depth(path, image, estimator)
    if depth is None:
        return None, []
    if depth.shape != image.pixels.shape[:2]:
        msg = f"profondeur {depth.shape} ≠ image {image.pixels.shape[:2]} : recalculez-la"
        raise ValueError(msg)
    camera = VanishingPointCameraEstimator().analyse(image)
    focal = camera.camera.focal_px
    pp = camera.camera.principal_point
    from_vps = camera.frame is not None and camera.frame.focal_from_vps
    prior = ExifCameraEstimator().estimate(image).focal_px
    long_side = max(image.width, image.height)
    bench = PhotoBench(
        room=room,
        photo=path.name,
        focal_px=focal,
        focal_from_vps=from_vps,
        focal_35mm=focal / long_side * 36.0,
    )
    rows: list[Row] = []
    obs, _ = observe_room(depth, focal, pp)
    frame_up = camera.frame.up if camera.frame is not None else None
    est, *_rest, warnings = analyse_attic(
        depth,
        focal,
        pp,
        frame_up=frame_up,
        camera_height_m=DEFAULT_CAMERA_HEIGHT_M,
        camera_height_entered=False,
    )
    bench.floor_out_of_view = est.floor_assumed
    h_model = obs.camera_height_m if obs is not None else None
    bench.camera_height_model_m = h_model
    scaled = rescale(est, DEFAULT_CAMERA_HEIGHT_M, entered=False) if est.camera_height else est
    bench.attic = {
        "faîtage": _value(scaled.ridge_height),
        "largeur entre égouts": _value(scaled.width),
        "distance au pignon": _value(scaled.gable_distance),
        "égout": statistics.mean(e.value for e in scaled.eave_heights)
        if scaled.eave_heights
        else None,
        "égout au plus": _value(scaled.eave_bound),
        "hauteur sous plafond": _value(scaled.ceiling_height),
    }
    bench.notes.extend(warnings[:4])
    if scaled.eave_rejected:
        bench.notes.append(f"égout inconnu (KAN-73) : {scaled.eave_rejected}")
    eave_unseen = scaled.ridge_height is not None and not scaled.eave_heights
    bench.confidence = assess(
        "assumed",
        focal_from_vps=from_vps,
        floor_out_of_view=est.floor_assumed,
        eave_unseen=eave_unseen,
    )
    if ruler is not None:
        length, a, b = ruler
        model = ruler_length(depth, focal, pp, a, b)
        bench.ruler_model_m = model
        if model and h_model:
            app_factor = DEFAULT_CAMERA_HEIGHT_M / h_model
            bench.ruler_app_m = model * app_factor
            bench.camera_height_from_ruler_m = h_model * length / model
            prior_obs, _ = observe_room(depth, prior, pp)
            prior_len = ruler_length(depth, prior, pp, a, b)
            if prior_obs is not None and prior_len:
                bench.ruler_prior_focal_m = (
                    prior_len * DEFAULT_CAMERA_HEIGHT_M / prior_obs.camera_height_m
                )
        case = f"{room} · {path.name}"
        rows.append(Row.build(f"{case} · échelle du modèle", "règle", model, length))
        rows.append(
            Row.build(f"{case} · hauteur supposée 1,50 m", "règle", bench.ruler_app_m, length)
        )
        rows.append(
            Row.build(
                f"{case} · hauteur supposée 1,50 m, focale de repli",
                "règle",
                bench.ruler_prior_focal_m,
                length,
            )
        )
    return bench, rows


def _photos(room_dir: Path) -> list[Path]:
    """Photos du jeu ; un fichier en « _ » (image de diagnostic…) n'en est pas une."""
    return sorted(
        p
        for p in room_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES and not p.name.startswith("_")
    )


def run_bench(
    root: Path,
    *,
    estimator: DepthEstimator | None = None,
    control_depth: Path = CONTROL_DEPTH,
    progress: Callable[[str], None] | None = None,
) -> BenchReport:
    say = progress or (lambda _m: None)
    report = BenchReport()
    if control_depth.is_file():
        say("scène de contrôle…")
        rows, medians = bench_control(control_depth)
        report.rows.extend(rows)
        report.medians.update(medians)
    else:
        report.notes.append("scène de contrôle absente (fixture des tests non installée)")
    ruler_errors: dict[str, list[float]] = {}
    for room_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        rulers: dict[str, tuple[float, tuple[float, float], tuple[float, float]]] = {}
        ruler_file = room_dir / RULER_FILE
        if ruler_file.is_file():
            spec = json.loads(ruler_file.read_text(encoding="utf-8"))
            for name, (a, b) in spec.get("photos", {}).items():
                rulers[name] = (float(spec["longueur_m"]), (a[0], a[1]), (b[0], b[1]))
        for path in _photos(room_dir):
            say(f"{room_dir.name}/{path.name}…")
            bench, rows = bench_photo(room_dir.name, path, estimator, rulers.get(path.name))
            if bench is None:
                report.notes.append(f"{room_dir.name}/{path.name} : profondeur absente, sautée")
                continue
            report.photos.append(bench)
            report.rows.extend(rows)
            for r in rows:
                if r.error is not None:
                    config = r.case.rsplit(" · ", 1)[-1]
                    ruler_errors.setdefault(f"règle · {config}", []).append(abs(r.error))
    for key, errors in ruler_errors.items():
        report.medians[key] = statistics.median(errors)
    return report


# ------------------------------------------------------------------ rapport
def _fmt_m(v: float | None) -> str:
    return "—" if v is None else metres(v)


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.0f} %"


def render_markdown(report: BenchReport) -> str:
    out = [
        "# Banc KAN-33 : estimation sur données réelles",
        "",
        "Erreur = estimé / vrai − 1. Photos privées : seuls des chiffres figurent ici.",
        "",
        "## Erreur médiane des cotes par configuration",
        "",
        "| Configuration | Erreur médiane (absolue) |",
        "|---|---|",
    ]
    out += [f"| {k} | {v * 100:.0f} % |" for k, v in report.medians.items()]
    out += ["", "## Détail", "", "| Cas | Cote | Estimé | Vrai | Erreur |", "|---|---|---|---|---|"]
    out += [
        f"| {r.case} | {r.metric} | {_fmt_m(r.estimated)} | {_fmt_m(r.truth)} "
        f"| {_fmt_pct(r.error)} |"
        for r in report.rows
    ]
    out += ["", "## Photos", ""]
    for p in report.photos:
        out.append(f"### {p.room} · {p.photo}")
        out.append("")
        out.append(
            f"- focale {p.focal_px:.0f} px ({p.focal_35mm:.0f} mm équiv. 24×36), "
            f"{'lignes de fuite' if p.focal_from_vps else 'repli'}"
        )
        out.append(
            f"- hauteur de prise de vue vue par la profondeur (échelle du modèle) : "
            f"{_fmt_m(p.camera_height_model_m)}"
            + (
                f" ; imposée par la règle : {_fmt_m(p.camera_height_from_ruler_m)}"
                if p.camera_height_from_ruler_m
                else ""
            )
            + (" ; sol hors champ" if p.floor_out_of_view else "")
        )
        if p.ruler_model_m is not None:
            out.append(
                f"- règle : {_fmt_m(p.ruler_model_m)} (modèle), {_fmt_m(p.ruler_app_m)} "
                f"(hauteur supposée 1,50 m), {_fmt_m(p.ruler_prior_focal_m)} "
                "(idem, focale de repli)"
            )
        cotes = ", ".join(f"{k} {_fmt_m(v)}" for k, v in p.attic.items() if v is not None)
        if cotes:
            out.append(f"- cotes estimées (hauteur supposée 1,50 m) : {cotes}")
        if p.confidence is not None:
            out.append(
                f"- confiance (KAN-31) : {p.confidence.label} — {p.confidence.reason} "
                f"(erreur attendue {p.confidence.expected_error * 100:.0f} %)"
            )
        out += [f"- note : {n}" for n in p.notes]
        out.append("")
    out += [f"- {n}" for n in report.notes]
    return "\n".join(out) + "\n"


def write_report(report: BenchReport, root: Path) -> tuple[Path, Path]:
    md = root / f"{REPORT_NAME}.md"
    js = root / f"{REPORT_NAME}.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return md, js


__all__ = ["BenchReport", "ScaleSource", "bench_control", "ruler_length", "run_bench"]
