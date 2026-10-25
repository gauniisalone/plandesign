"""KAN-95 (P2b) : pièce droite recoupée sur plusieurs photos.

Chaque photo de la carte Photos reliée au plan (C1) est lue par la brique générique
`photo_fusion` (profondeur gardée, focale commune, gros plans écartés, mesure de référence),
puis ses murs sont observés comme pour une photo seule (`depthplan.observe_room` : sol, repère
des murs dominants, murs droits ou obliques KAN-89), à la hauteur de prise de vue.

**Recalage** : sans faîtage, rien ne fixe d'avance la position d'une photo par rapport à une
autre. Le repère d'une photo n'est connu qu'au quart de tour près ; pour chaque quart de tour
et chaque translation proposée par une paire de murs parallèles, on compte les murs qui
coïncident (même axe, même droite à la tolérance près, étendues qui se touchent). Une photo
n'est recalée que si :

- elle partage au moins un mur sur chaque axe (un coin) avec les photos déjà recalées ;
- et cette position est la seule à le faire : une autre position aussi bien soutenue mais qui
  donnerait une autre pièce (dimensions à plus de 10 %) rend le recalage ambigu.

Sinon, **rien n'est inventé** : la photo ne compte pas, et si aucune photo ne se recale, le
plan reste celui de la meilleure photo seule, avec la phrase « Ces photos ne voient pas de
murs en commun : le plan vient de la photo N ; ajoutez une photo qui recoupe les autres. »

Réunion (`MERGE_ROOMS`, **désactivée**) : les murs des photos recalées seraient réunis dans le
repère de la première et passeraient par le même polygone que pour une photo seule
(`depthplan.room_polygon`), avec la concordance par retrait d'une photo. Sur scènes
synthétiques, elle n'apporte aucun gain médian et se trompe une fois sur 19 : le plan vient
donc de la **meilleure photo seule** (la plus soutenue en murs, jamais un gros plan), et la
phrase dit si les autres la recoupent (« voient les mêmes murs ») ou non.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openplan.adapters.protocols import DepthEstimator
from openplan.pipeline.confidence import EstimateConfidence, assess
from openplan.pipeline.depthplan import (
    RoomObservation,
    WallObservation,
    observe_room,
    propose_floorplan_from_depth,
    room_polygon,
)
from openplan.pipeline.floorplan import ACTOR, DEFAULT_CAMERA_HEIGHT_M, DetectionError
from openplan.pipeline.photo_fusion import (
    FUSION_STRIDE,
    MIN_PHOTOS,
    PhotoContribution,
    PhotoReading,
    camera_metadata,
    common_focal_norm,
    concordance,
    default_scales,
    fused_basis,
    grid_focal,
    jackknife,
    read_photos,
    reference_scale,
    scale_source,
    set_camera_height,
    stripped,
    structure_is_estimated,
    with_provenance,
)
from openplan.scene import history
from openplan.schemas.common import Point3, Source, StrictModel
from openplan.schemas.elements import Camera, Element, Room, Wall
from openplan.schemas.numbers import metres
from openplan.schemas.results import StepResult
from openplan.schemas.scene import ConfidenceInputs, Project

STEP = "floorplan.fusion"
STRUCTURE = (Wall, Room)
MATCH_TOL_M = 0.20
MATCH_TOL_REL = 0.08
"""Deux murs coïncident à 20 cm + 8 % de leur distance près (échelle de chaque photo à ±10 %)."""
MIN_OVERLAP_M = 0.3
"""Leurs étendues vues se recouvrent d'au moins 30 cm : deux photos voient la même portion
de mur (deux murs alignés mais disjoints ne prouvent rien)."""
MERGE_ROOMS = False
"""Réunion des murs de photos recalées. Désactivée : sur 144 scènes synthétiques (rectangles,
L, murs obliques, 2 à 4 vues au hasard ou selon la consigne), 19 réunions appliquées, dont 1
fausse, et aucun gain médian sur les dimensions (des photos qui partagent un coin vu voient les
mêmes murs). Le recalage sert seulement à dire si les photos se recoupent ; le plan vient de la
meilleure photo seule (rapport KAN-95, P2b)."""
MIN_SHARED_CORNERS = 1
ROOM_MAX_SPREAD = 0.25
"""Réunion refusée au-delà de ±25 % entre photos (les réunions fausses des essais étaient à
±42 % et plus)."""
CORNER_GAP_M = 0.35
"""Un mur s'arrête à un coin si le bout de son étendue vue est à 35 cm du croisement."""
FREE_SPACE_MARGIN_M = 0.3
"""Un mur d'une photo à plus de 30 cm à l'intérieur de la pièce vue par les autres contredit la
position essayée (elles y voient du vide)."""
AMBIGUOUS_DIMS = 0.10
"""Deux positions aussi bien soutenues qui donnent des pièces différentes de plus de 10 % :
recalage ambigu, la photo ne compte pas."""
NO_COMMON = (
    "Ces photos ne voient pas de murs en commun : le plan vient de la photo {n} ; ajoutez une "
    "photo qui recoupe les autres."
)
SAME_WALLS = (
    "Le plan vient de la photo {n}, la plus complète : les autres photos voient les mêmes murs "
    "et ne l'agrandissent pas."
)


class RoomFusion(StrictModel):
    photos: list[PhotoContribution]
    used: int
    spread: float | None = None
    summary: str
    length_m: float
    width_m: float
    area_m2: float
    common_focal: bool
    registered: bool
    """Faux : aucune photo ne s'est recalée, le plan vient de la meilleure photo seule."""
    best_photo: UUID | None = None
    reference_image: UUID | None = None


class RoomFusionRun(StrictModel):
    run_id: UUID
    level_id: UUID
    applied: bool
    fusion: RoomFusion | None = None
    notices: list[str] = Field(default_factory=list)
    confidence: EstimateConfidence | None = None
    project: Project
    result: StepResult


@dataclass
class _Reading(PhotoReading):
    observation: RoomObservation | None = None
    walls: list[WallObservation] = field(default_factory=list)
    """Murs en mètres (hauteur de prise de vue), repère de la photo."""
    support: int = 0


@dataclass
class Placement:
    """Position d'une photo dans le repère commun : quarts de tour puis translation."""

    turns: int
    shift: tuple[float, float]
    matched: int
    matched_axes: tuple[int, int]
    overlap: float
    corners: int = 0
    """Coins vus sur cette photo et sur les photos déjà recalées."""


# ------------------------------------------------------------------ murs et repères
def rotate_wall(wall: WallObservation, turns: int) -> WallObservation:
    """Le mur tourné de `turns` quarts de tour (sens direct) autour de la caméra :
    le point (x, y) devient (−y, x) à chaque quart."""
    w = wall
    for _ in range(turns % 4):
        normal = None if w.normal is None else (-w.normal[1], w.normal[0])
        if w.axis == "x":  # x = o, étendue en y  →  y = o, étendue en x = −y
            w = replace(w, axis="y", span=(-w.span[1], -w.span[0]), normal=normal)
        else:  # y = o, étendue en x  →  x = −o, étendue en y = x
            w = replace(w, axis="x", offset=-w.offset, normal=normal)
    return w


def place(walls: list[WallObservation], p: Placement) -> list[WallObservation]:
    return [rotate_wall(w, p.turns).shifted(*p.shift) for w in walls]


def _tol(offset: float) -> float:
    return MATCH_TOL_M + MATCH_TOL_REL * abs(offset)


def _same(a: WallObservation, b: WallObservation) -> float | None:
    """Recouvrement des étendues si les deux murs sont le même (sinon None)."""
    if a.axis != b.axis or abs(a.offset - b.offset) > _tol(max(abs(a.offset), abs(b.offset))):
        return None
    overlap = min(a.span[1], b.span[1]) - max(a.span[0], b.span[0])
    return overlap if overlap >= MIN_OVERLAP_M else None


def _score(base: list[WallObservation], moved: list[WallObservation]) -> tuple[int, int, float]:
    """Murs de `moved` qui coïncident avec un mur de `base` (un pour un), par axe, et le
    recouvrement total."""
    used: set[int] = set()
    mx = my = 0
    overlap = 0.0
    for w in moved:
        best: tuple[float, int] | None = None
        for i, b in enumerate(base):
            if i in used:
                continue
            o = _same(b, w)
            if o is not None and (best is None or o > best[0]):
                best = (o, i)
        if best is not None:
            used.add(best[1])
            overlap += max(best[0], 0.0)
            if w.axis == "x":
                mx += 1
            else:
                my += 1
    return mx, my, overlap


Corner = tuple[float, float, int, int]
"""Coin vu : (x, y), sens du mur y = cte depuis le coin (+1 vers les x croissants) et sens du mur
x = cte (+1 vers les y croissants)."""


def _end(span: tuple[float, float], value: float) -> int:
    """+1 si `value` est au début de l'étendue (le mur part vers les valeurs croissantes), −1 à
    sa fin, 0 ailleurs."""
    if abs(value - span[0]) <= CORNER_GAP_M:
        return 1
    if abs(value - span[1]) <= CORNER_GAP_M:
        return -1
    return 0


def corners(walls: list[WallObservation]) -> list[Corner]:
    """Coins vus : un mur x = a et un mur y = b dont les étendues s'arrêtent toutes deux à leur
    croisement (à CORNER_GAP_M près)."""
    out: list[Corner] = []
    for a in walls:
        if a.axis != "x":
            continue
        for b in walls:
            if b.axis != "y":
                continue
            along_a = _end(a.span, b.offset)
            along_b = _end(b.span, a.offset)
            if along_a and along_b:
                out.append((a.offset, b.offset, along_b, along_a))
    return out


def _shared_corners(base: list[Corner], moved: list[Corner]) -> int:
    used: set[int] = set()
    count = 0
    for x, y, sx, sy in moved:
        for i, (bx, by, bsx, bsy) in enumerate(base):
            if i in used or (sx, sy) != (bsx, bsy):
                continue
            if math.dist((x, y), (bx, by)) <= _tol(max(abs(bx), abs(by))):
                used.add(i)
                count += 1
                break
    return count


View = tuple[tuple[float, float], list[WallObservation]]
"""Une photo recalée : position de son appareil et ses murs, dans le repère commun."""


def _free(views: list[View]) -> BaseGeometry:
    """Le vide vu : pour chaque photo, les triangles entre l'appareil et chaque mur vu (rien
    ne s'y trouve, sinon le mur serait caché), rétrécis de FREE_SPACE_MARGIN_M."""
    triangles = []
    for camera, walls in views:
        for w in walls:
            a, b = w.segment()
            tri = Polygon([camera, a, b])
            if tri.is_valid and tri.area > 1e-3:
                triangles.append(tri)
    if not triangles:
        return Polygon()
    return unary_union(triangles).buffer(-FREE_SPACE_MARGIN_M)


def _inside(free: BaseGeometry, walls: list[WallObservation]) -> int:
    """Murs dont un point (quart, milieu, trois quarts) tombe dans le vide vu par d'autres
    photos : une position qui en met est contredite."""
    if free.is_empty:
        return 0
    count = 0
    for w in walls:
        (ax, ay), (bx, by) = w.segment()
        if any(
            free.contains(Point(ax + t * (bx - ax), ay + t * (by - ay))) for t in (0.25, 0.5, 0.75)
        ):
            count += 1
    return count


def _dims(walls: list[WallObservation]) -> tuple[float, float] | None:
    try:
        polygon, _kept = room_polygon(walls, [])
    except (ValueError, AssertionError):
        return None
    minx, miny, maxx, maxy = polygon.bounds
    return maxx - minx, maxy - miny


def register(views: list[View], walls: list[WallObservation]) -> Placement | None:
    """Position de `walls` (une photo, appareil à l'origine) dans le repère des photos déjà
    recalées `views`, ou None si elle ne partage pas un mur sur chaque axe, si toute position
    met un mur dans le vide vu par l'autre, ou si une autre position aussi bien soutenue
    donnerait une autre pièce."""
    base = [w for _, ws in views for w in ws]
    base_free = _free(views)
    base_corners = corners(base)
    hypotheses: list[Placement] = []
    for turns in range(4):
        turned = [rotate_wall(w, turns) for w in walls]
        xs = {
            round(b.offset - w.offset, 3) for b in base for w in turned if b.axis == w.axis == "x"
        }
        ys = {
            round(b.offset - w.offset, 3) for b in base for w in turned if b.axis == w.axis == "y"
        }
        for tx in xs:
            for ty in ys:
                moved = [w.shifted(tx, ty) for w in turned]
                mx, my, overlap = _score(base, moved)
                if mx < 1 or my < 1:
                    continue
                # Un coin vu sur les deux photos (les deux murs s'y arrêtent, même sens) : sans
                # lui, deux murs perpendiculaires se placent n'importe où le long l'un de l'autre.
                shared = _shared_corners(base_corners, corners(moved))
                if shared < MIN_SHARED_CORNERS:
                    continue
                # Vide contre mur : aucune photo ne voit de mur là où l'autre voit du vide.
                if _inside(base_free, moved) or _inside(_free([((tx, ty), moved)]), base):
                    continue
                hypotheses.append(
                    Placement(turns, (tx, ty), mx + my, (mx, my), overlap, corners=shared)
                )
    if not hypotheses:
        return None
    hypotheses.sort(key=lambda p: (p.corners, p.matched, p.overlap), reverse=True)
    best = hypotheses[0]
    best_dims = _dims([*base, *place(walls, best)])
    for other in hypotheses[1:]:
        if (other.corners, other.matched) < (best.corners, best.matched):
            break
        same_place = other.turns == best.turns and math.dist(other.shift, best.shift) <= _tol(0.0)
        if same_place:
            continue
        dims = _dims([*base, *place(walls, other)])
        if best_dims is None or dims is None:
            return None
        if any(abs(a / b - 1.0) > AMBIGUOUS_DIMS for a, b in zip(dims, best_dims, strict=True)):
            return None  # autre pièce aussi bien soutenue : rien n'est inventé
    return best


# ------------------------------------------------------------------ lecture
def _analyse(readings: list[_Reading], height: float, *, use_common: bool = True) -> bool:
    common = common_focal_norm(list(readings)) if use_common else None
    for r in readings:
        if r.stored is None:
            continue
        obs, _planes = observe_room(
            r.stored.depth, grid_focal(r, common), r.stored.principal, stride=FUSION_STRIDE
        )
        if obs is None or obs.camera_height_m <= 0:
            r.reason = "sol non vu"
            continue
        r.observation = obs
        r.reason = "lue"
        set_camera_height(r, obs.camera_height_m)
        k = height / obs.camera_height_m
        r.walls = [w.scaled(k) for w in obs.walls]
        r.support = sum(w.count for w in obs.walls)
        if not r.walls and not r.close_up:
            r.reason = "aucun mur vu"
    return common is not None


@dataclass
class _Union:
    order: list[UUID]
    placements: dict[UUID, Placement]
    walls: list[WallObservation]


def _union(readings: list[_Reading], skip: UUID | None = None) -> _Union | None:
    """Photos recalées les unes sur les autres, de la mieux soutenue à la moins soutenue ;
    une photo qui ne se recale pas ne compte pas."""
    pool = sorted(
        (r for r in readings if r.usable and r.walls and r.image_id != skip),
        key=lambda r: -r.support,
    )
    if not pool:
        return None
    first = pool[0]
    views: list[View] = [((0.0, 0.0), list(first.walls))]
    placements = {first.image_id: Placement(0, (0.0, 0.0), len(first.walls), (0, 0), 0.0)}
    pending = pool[1:]
    progress = True
    while pending and progress:  # une photo recalée peut en accrocher une autre
        progress = False
        for r in list(pending):
            p = register(views, r.walls)
            if p is None:
                continue
            placements[r.image_id] = p
            views.append((p.shift, place(r.walls, p)))
            pending.remove(r)
            progress = True
    walls = [w for _, ws in views for w in ws]
    return _Union([r.image_id for r in pool if r.image_id in placements], placements, walls)


def _room_dims(walls: list[WallObservation]) -> tuple[float, float, float] | None:
    try:
        polygon, _ = room_polygon(walls, [])
    except (ValueError, AssertionError):
        return None
    minx, miny, maxx, maxy = polygon.bounds
    return maxx - minx, maxy - miny, polygon.area


def _spread(readings: list[_Reading], full: _Union) -> float | None:
    dims = _room_dims(full.walls)
    if dims is None:
        return None

    def refit(image_id: UUID) -> dict[str, float] | None:
        other = _union(readings, skip=image_id)
        if other is None:
            return None
        d = _room_dims(other.walls)
        return None if d is None else {"x": d[0], "y": d[1]}

    return jackknife(full.order, refit, {"x": dims[0], "y": dims[1]})


# ------------------------------------------------------------------ plan
def _rebuild(
    project: Project,
    level_id: UUID,
    walls: list[WallObservation],
    reading: _Reading,
    height: float,
    *,
    entered: bool,
    run_id: UUID,
) -> tuple[Project, tuple[float, float]]:
    """Le polygone des murs réunis, posé sur le niveau vidé de sa structure ; renvoie aussi le
    décalage appliqué (la caméra de référence y est)."""
    assert reading.observation is not None
    observation = RoomObservation(
        camera_height_m=height, axes=reading.observation.axes, walls=walls
    )
    built, _step = propose_floorplan_from_depth(
        stripped(project, level_id, STRUCTURE),
        level_id,
        observation,
        image_id=reading.image_id,
        camera_height_m=height,
        camera_height_entered=entered,
        run_id=run_id,
    )
    level = built.levels[level_id]
    camera = next(
        (
            e
            for e in level.elements.values()
            if isinstance(e, Camera) and e.image_id == reading.image_id
        ),
        None,
    )
    shift = (camera.position.x, camera.position.y) if camera is not None else (0.0, 0.0)
    return built, shift


def _place_cameras(
    elements: dict[UUID, Element],
    contributions: list[PhotoContribution],
    placements: dict[UUID, Placement],
    shift: tuple[float, float],
    summary: str,
    spread: float | None,
    height: float,
) -> dict[UUID, Element]:
    for c in contributions:
        meta = camera_metadata(c, summary, spread)
        existing = next(
            (e for e in elements.values() if isinstance(e, Camera) and e.image_id == c.image_id),
            None,
        )
        if existing is not None:
            elements[existing.id] = existing.model_copy(
                update={"metadata": {**existing.metadata, **meta}}
            )
            continue
        p = placements.get(c.image_id)
        x, y = (shift[0] + p.shift[0], shift[1] + p.shift[1]) if p else shift
        camera = Camera(
            name="Photo",
            position=Point3(x=x, y=y, z=c.camera_height_m or height),
            yaw=0.0,
            pitch=0.0,
            image_id=c.image_id,
            source=Source.ESTIMATED,
            confidence=0.4,
            metadata=meta,
        )
        elements[camera.id] = camera
    kept_ids = [c.image_id for c in contributions if c.kept]
    return with_provenance(elements, STRUCTURE, "floorplan.depth", kept_ids)


def _contributions(
    readings: list[_Reading],
    placements: dict[UUID, Placement],
    final: dict[UUID, float],
    best: UUID,
    registered: bool,
) -> list[PhotoContribution]:
    used = len(placements) if registered else 1
    out: list[PhotoContribution] = []
    for r in readings:
        kept = r.image_id in placements if registered else r.image_id == best
        if kept:
            reason = "murs recoupés" if registered else "photo du plan (la plus complète)"
        elif r.close_up:
            reason = r.reason
        elif r.image_id in placements:
            reason = "recoupe la photo du plan (mêmes murs), sans l'agrandir"
        elif r.usable and r.walls:
            reason = "écartée : ne voit pas de murs en commun avec les autres"
        elif r.reason in ("profondeur absente", "photo absente du projet", "sol non vu"):
            reason = f"écartée : {r.reason}"
        else:
            reason = "écartée : aucun mur vu"
        out.append(
            PhotoContribution(
                image_id=r.image_id,
                kept=kept,
                reason=reason,
                weight=round(1.0 / used, 3) if kept else 0.0,
                camera_height_m=None
                if r.camera_h is None or r.image_id not in final
                else round(r.camera_h * final[r.image_id], 3),
                factor=final.get(r.image_id),
                close_up=r.close_up,
            )
        )
    return out


def fuse_room(  # noqa: PLR0911, PLR0912, PLR0915
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_ids: list[UUID],
    *,
    estimator: DepthEstimator | None = None,
    camera_height_m: float | None = None,
    run_id: UUID | None = None,
) -> RoomFusionRun:
    """Recoupe les photos `image_ids` (membres de la carte Photos reliée) d'une pièce droite et
    remplace la structure estimée du niveau. Sans murs en commun, le plan est celui de la
    meilleure photo seule, et c'est dit."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    entered = camera_height_m is not None
    height = camera_height_m if camera_height_m is not None else DEFAULT_CAMERA_HEIGHT_M
    ids = list(dict.fromkeys(image_ids))

    def done(
        applied: bool,
        after: Project,
        notices: list[str],
        fusion: RoomFusion | None = None,
        trust: EstimateConfidence | None = None,
    ) -> RoomFusionRun:
        return RoomFusionRun(
            run_id=rid,
            level_id=level_id,
            applied=applied,
            fusion=fusion,
            notices=notices,
            confidence=trust,
            project=after,
            result=StepResult(
                step=STEP,
                confidence=0.0 if trust is None else max(0.0, 1.0 - trust.expected_error),
                warnings=[] if applied else notices,
                duration_s=time.perf_counter() - started,
                model_ids=[m for m in (getattr(estimator, "model_id", None),) if m],
            ),
        )

    if len(ids) < MIN_PHOTOS:
        return done(False, project, ["Une seule photo dans la carte : rien à recouper."])
    readings = [_Reading(**vars(r)) for r in read_photos(project, project_dir, ids, estimator)]
    common_focal = _analyse(readings, height)
    union = _union(readings)
    if union is None:
        # Aucune profondeur ou aucun mur lisible (en ligne sans profondeur, par exemple) : rien
        # à dire de plus que l'analyse de chaque photo.
        return done(False, project, [])
    best = union.order[0]
    best_n = ids.index(best) + 1
    # `shared` : les photos se recoupent (un coin vu en commun). Tant que la réunion est
    # désactivée (MERGE_ROOMS), le plan reste celui de la meilleure photo, et c'est dit.
    shared = len(union.order) >= MIN_PHOTOS
    registered = shared and MERGE_ROOMS
    scales = default_scales(list(readings), height)
    final = {i: scales[i] for i in (union.order if registered else [best]) if i in scales}
    referenced, rho = reference_scale(list(readings), final)
    final = {k: v * rho for k, v in final.items()}
    walls = [
        w.scaled(rho) for w in (union.walls if registered else readings_by(readings, best).walls)
    ]
    measured_spread = _spread(readings, union) if registered else None
    used = len(union.order) if registered else 1
    if registered:
        summary = concordance(used, measured_spread)
    elif shared:
        summary = SAME_WALLS.format(n=best_n)
    else:
        summary = NO_COMMON.format(n=best_n)
    dims = _room_dims(walls)
    if dims is None:
        return done(False, project, ["Recoupement impossible : murs incohérents."])
    contributions = _contributions(readings, union.placements, final, best, registered)
    fusion = RoomFusion(
        photos=contributions,
        used=used,
        spread=measured_spread,
        summary=summary,
        length_m=max(dims[0], dims[1]),
        width_m=min(dims[0], dims[1]),
        area_m2=dims[2],
        common_focal=common_focal,
        registered=registered,
        best_photo=best,
        reference_image=referenced.image_id if referenced else None,
    )
    if measured_spread is not None and measured_spread > ROOM_MAX_SPREAD:
        return done(
            False,
            project,
            [f"Les photos se contredisent ({summary}) : le plan n'est pas remplacé."],
            fusion,
        )
    if not structure_is_estimated(project, level_id, STRUCTURE):
        return done(
            False,
            project,
            [f"Plan retouché à la main : il n'est pas remplacé ({summary})."],
            fusion,
        )
    notices = [] if registered else [summary]
    if any(r.close_up for r in readings):
        notices.append(
            "Une photo prise de près (gros plan) n'a pas compté : elle ne voit pas le fond de "
            "la pièce."
        )
    current = project.levels[level_id].scale_basis
    if not registered and current is not None and current.image_id == best:
        # Rien à recouper et le plan vient déjà de la meilleure photo : il reste tel quel.
        return done(False, project, notices, fusion)
    if not registered:
        # Le plan vient d'une autre photo (un gros plan analysé d'abord) : celui de la
        # meilleure photo seule, lue avec sa propre focale, comme si elle était seule.
        alone = readings_by(readings, best)
        _analyse([alone], height, use_common=False)
        walls = [w.scaled(rho) for w in alone.walls]
    main = next(c for c in contributions if c.image_id == best)
    built, shift = _rebuild(
        project,
        level_id,
        walls,
        readings_by(readings, best),
        height * rho,
        entered=entered,
        run_id=rid,
    )
    level = built.levels[level_id]
    placements = union.placements if registered else {best: union.placements[best]}
    elements = _place_cameras(
        dict(level.elements), contributions, placements, shift, summary, measured_spread, height
    )
    inputs = ConfidenceInputs(focal_from_vps=common_focal or readings_by(readings, best).from_vps)
    basis = fused_basis(
        main,
        height,
        entered=entered,
        inputs=inputs,
        referenced=referenced,
        rho=rho,
        final=final,
        summary=summary,
        used=used,
        spread=measured_spread,
    )
    new_level = level.model_copy(update={"elements": elements, "scale_basis": basis})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    label = f"Pièce recoupée sur {used} photos" if registered else "Plan de la meilleure photo"
    after = history.commit(project, after, actor=ACTOR, label=label, run_id=rid)
    trust = (
        assess(
            scale_source(referenced, entered=entered),
            camera_height_m=height if entered else None,
            focal_from_vps=inputs.focal_from_vps,
            fused_photos=used,
            fusion_spread=measured_spread,
        )
        if registered
        else None
    )
    if registered:
        notices.insert(
            0,
            f"Pièce recoupée sur {used} photos : {metres(fusion.length_m)} × "
            f"{metres(fusion.width_m)} ({summary}).",
        )
    if referenced is not None:
        notices.append("Votre mesure de référence vaut pour toutes les photos recoupées.")
    return done(True, after, notices, fusion, trust)


def readings_by(readings: list[_Reading], image_id: UUID) -> _Reading:
    return next(r for r in readings if r.image_id == image_id)
