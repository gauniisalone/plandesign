"""Questions guidées de l'accueil (L2, note 12, décisions d'augus du 7 octobre).

- `answer_fields` : les réponses deviennent des champs de l'intention ; elles priment sur ce
  que dit le texte (les envies, lues comme une consigne).
- `area_guard` : avec une surface donnée et un plan construit depuis une photo d'une seule
  pièce, un écart de plus de 15 % recale l'échelle (facteur √(S donnée / S estimée), borné
  entre 0,6 et 1,6), en un pas attribué au pipeline, signalé et annulable en un clic.
- `check_request` : « respect de la demande », à l'arrivée sur « Vos rendus » : chaque demande
  avec ✓ si elle est tenue, sinon la raison.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import JsonValue
from shapely.geometry import Point, Polygon

from openplan.geom.scale import apply_scale
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.instruction import Translation, furniture_words
from openplan.pipeline.mainroom import GENERATED_KEY, is_generated, main_room
from openplan.pipeline.starter import STARTER_KEY
from openplan.scene import history
from openplan.schemas.assistant import ROOM_TYPES
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Furniture, Room
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.intent import MOOD_LABELS, STYLES, ZONE_LABELS
from openplan.schemas.scene import Level, Project

AREA_GUARD_KEY = "area_guard"
AREA_TOLERANCE = 0.15
FACTOR_BOUNDS = (0.6, 1.6)
GUARD_LABEL = "Recaler sur la surface donnée"
UNDO_LABEL = "Annuler le recalage sur la surface"
BEDS = ("bed_double", "bed_single", "bed_bunk", "bed_builtin")
TIME_LABELS = {"matin": "matin", "midi": "midi", "heure_doree": "heure dorée", "soir": "soir"}


def has_answers(answers: GuidedAnswers | None) -> bool:
    """Une réponse au moins est donnée (jumeau de `hasAnswers` côté front)."""
    if answers is None:
        return False
    return bool(
        answers.space
        or answers.area_m2
        or answers.bedrooms is not None
        or answers.style
        or answers.aspect
        or answers.light_time
        or answers.mood
        or answers.furniture
        or answers.avoid
    )


# ------------------------------------------------------------------ réponses → intention
def answer_fields(
    answers: GuidedAnswers, categories: list[str], *, rooms_known: bool = False
) -> dict[str, JsonValue]:
    """Champs de l'intention tirés des réponses (rien pour une réponse vide). `rooms_known` :
    plan généré (L3), les chambres sont des pièces et ne se meublent pas dans le séjour."""
    fields: dict[str, JsonValue] = {}
    if answers.space == "combles":
        fields["room_type"] = "attic"
    elif answers.space == "piece" and answers.room_type in ROOM_TYPES:
        fields["room_type"] = answers.room_type
    if answers.style in STYLES:
        fields["style"] = answers.style
    if answers.mood:
        moods: list[JsonValue] = []
        for mood in dict.fromkeys(answers.mood):
            moods.append(mood)
        fields["mood"] = moods[:2]
    if answers.aspect:
        fields["views"] = {"aspect": answers.aspect}
    if answers.light_time:
        fields["light"] = {"time": answers.light_time}
    wishes: dict[str, int] = {c: 1 for c in answers.furniture if c in categories}
    # Avant L3, une seule pièce : N chambres = une zone couchage et un lit par chambre (le
    # contrôle le dit, et dit si la place manque).
    if answers.bedrooms and not rooms_known:
        fields["zones"] = ["couchage"]
        if "bed_double" in categories:
            wishes["bed_double"] = max(wishes.get("bed_double", 0), answers.bedrooms)
    if wishes:
        fields["furniture"] = [{"category": c, "count": n} for c, n in wishes.items()]
    avoid = [c for c in answers.avoid if c in categories]
    if avoid:
        fields["avoid"] = list(avoid)
    return fields


def merge_fields(
    text_fields: dict[str, JsonValue], answers: dict[str, JsonValue]
) -> dict[str, Any]:
    """Les réponses priment ; les meubles et les catégories à éviter s'additionnent."""
    merged: dict[str, Any] = {**text_fields, **answers}
    if "furniture" in text_fields and "furniture" in answers:
        counts: dict[str, int] = {}
        for item in [*text_fields["furniture"], *answers["furniture"]]:  # type: ignore[misc]
            counts[item["category"]] = max(counts.get(item["category"], 0), int(item["count"]))
        merged["furniture"] = [{"category": c, "count": n} for c, n in counts.items()]
    if "avoid" in text_fields and "avoid" in answers:
        merged["avoid"] = list(dict.fromkeys([*text_fields["avoid"], *answers["avoid"]]))  # type: ignore[misc]
    return merged


# ------------------------------------------------------------------ garde-fou de surface
class AreaGuard(StrictModel):
    applied: bool
    factor: float = 1.0
    before_m2: float | None = None
    wanted_m2: float
    reason: str = ""


def _single_room(level: Level) -> tuple[Room, float] | None:
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    if len(rooms) != 1:
        return None
    derived = compute_derived(level).rooms.get(rooms[0].id)
    return (rooms[0], derived.area_m2) if derived else None


def area_guard(
    project: Project, level_id: UUID, wanted_m2: float, *, actor: str
) -> tuple[Project, AreaGuard]:
    """Recale le plan sur la surface donnée si une seule pièce, construite depuis une photo,
    s'en écarte de plus de 15 % ; sinon ne change rien et dit pourquoi."""
    level = project.levels[level_id]
    found = _single_room(level)
    if found is None:
        return project, AreaGuard(applied=False, wanted_m2=wanted_m2, reason="pas une seule pièce")
    room, area = found
    if STARTER_KEY in room.metadata:
        return project, AreaGuard(
            applied=False,
            wanted_m2=wanted_m2,
            before_m2=round(area, 1),
            reason="plan de départ déjà à cette surface",
        )
    if area <= 0 or abs(area - wanted_m2) / wanted_m2 <= AREA_TOLERANCE:
        return project, AreaGuard(
            applied=False,
            wanted_m2=wanted_m2,
            before_m2=round(area, 1),
            reason="écart de moins de 15 %",
        )
    factor = min(FACTOR_BOUNDS[1], max(FACTOR_BOUNDS[0], math.sqrt(wanted_m2 / area)))
    scaled = apply_scale(level, factor)
    note = {"factor": round(factor, 3), "before_m2": round(area, 1), "wanted_m2": wanted_m2}
    elements = dict(scaled.elements)
    elements[room.id] = scaled.elements[room.id].model_copy(
        update={"metadata": {**scaled.elements[room.id].metadata, AREA_GUARD_KEY: note}}
    )
    after = project.model_copy(
        update={
            "levels": {**project.levels, level_id: scaled.model_copy(update={"elements": elements})}
        }
    )
    after = history.commit(project, after, actor=actor, label=GUARD_LABEL)
    return after, AreaGuard(
        applied=True, factor=round(factor, 3), before_m2=round(area, 1), wanted_m2=wanted_m2
    )


def undo_area_guard(project: Project, level_id: UUID, *, actor: str) -> Project | None:
    """Annule le recalage en un clic : l'échelle inverse, même si d'autres pas ont suivi."""
    level = project.levels[level_id]
    room = next(
        (
            e
            for e in level.elements.values()
            if isinstance(e, Room) and AREA_GUARD_KEY in e.metadata
        ),
        None,
    )
    if room is None:
        return None
    note = room.metadata[AREA_GUARD_KEY]
    factor = float(note["factor"]) if isinstance(note, dict) else 1.0  # type: ignore[arg-type]
    scaled = apply_scale(level, 1.0 / factor)
    elements = dict(scaled.elements)
    metadata = {k: v for k, v in scaled.elements[room.id].metadata.items() if k != AREA_GUARD_KEY}
    elements[room.id] = scaled.elements[room.id].model_copy(update={"metadata": metadata})
    after = project.model_copy(
        update={
            "levels": {**project.levels, level_id: scaled.model_copy(update={"elements": elements})}
        }
    )
    return history.commit(project, after, actor=actor, label=UNDO_LABEL)


def _kind_words(programme: dict[str, Any]) -> str:
    kind = programme.get("kind")
    if kind in ("studio", "maison"):
        return str(kind)
    return f"appartement T{int(programme.get('bedrooms') or 0) + 1}"


def _programme(level: Level) -> dict[str, Any] | None:
    for r in level.elements.values():
        if isinstance(r, Room) and isinstance(r.metadata.get(GENERATED_KEY), dict):
            programme = r.metadata[GENERATED_KEY].get("programme")  # type: ignore[union-attr]
            return programme if isinstance(programme, dict) else None
    return None


# ------------------------------------------------------------------ respect de la demande
Status = Literal["ok", "partial", "missing"]


class RequestItem(StrictModel):
    """Une demande et ce qu'il en est : ✓ tenue, ~ en partie, ✗ non tenue (avec la raison)."""

    label: str
    wanted: str
    status: Status
    detail: str = ""


def _main_room(level: Level) -> Room | None:
    return main_room(level)


def _placed(level: Level) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in level.elements.values():
        if isinstance(e, Furniture):
            counts[e.category] = counts.get(e.category, 0) + 1
    return counts


_UNKNOWN = re.compile(r"« (?P<what>[^»]+) » : pas au catalogue")


def _count(n: int, one: str, many: str) -> str:
    """« 1 lit posé », « 2 lits posés » : l'accord en toutes lettres, jamais « (s) »."""
    return f"{n} {one if n <= 1 else many}"


def _choice(label: str, wanted: str, ok: bool, why: str = "") -> RequestItem:
    return RequestItem(
        label=label, wanted=wanted, status="ok" if ok else "missing", detail="" if ok else why
    )


def _area_item(level: Level, wanted_m2: float) -> RequestItem:
    wanted = f"{wanted_m2:g} m²"
    if is_generated(level):
        total = sum(d.area_m2 for d in compute_derived(level).rooms.values())
        close = abs(total - wanted_m2) / wanted_m2 <= AREA_TOLERANCE
        return RequestItem(
            label="Surface",
            wanted=wanted,
            status="ok" if close else "partial",
            detail=f"plan {total:.1f} m²".replace(".", ","),
        )
    found = _single_room(level)
    if found is None:
        return RequestItem(
            label="Surface", wanted=wanted, status="missing", detail="pas de pièce fermée à mesurer"
        )
    room, area = found
    guard = room.metadata.get(AREA_GUARD_KEY)
    if isinstance(guard, dict):
        factor = float(str(guard.get("factor", 1.0)))
        before = float(str(guard.get("before_m2", area)))
        detail = f"plan recalé ×{factor:.2f} sur votre surface (estimée {before:.0f} m²)"
    elif STARTER_KEY in room.metadata:
        detail = "plan de départ simplifié de cette surface"
    else:
        detail = f"{area:.0f} m² mesurés"
    close = abs(area - wanted_m2) / wanted_m2 <= AREA_TOLERANCE
    return RequestItem(
        label="Surface", wanted=wanted, status="ok" if close else "partial", detail=detail
    )


def _bedrooms_item(placed: dict[str, int], n: int) -> RequestItem:
    beds = sum(placed.get(c, 0) for c in BEDS)
    if n == 0:
        return RequestItem(label="Chambres", wanted="aucune", status="ok")
    if beds < n:
        return RequestItem(
            label="Chambres",
            wanted=f"{n}",
            status="missing",
            detail=f"{_count(beds, 'lit posé', 'lits posés')} sur {n} : place insuffisante",
        )
    if n == 1:
        return RequestItem(
            label="Chambres", wanted="1", status="ok", detail=_count(beds, "lit posé", "lits posés")
        )
    return RequestItem(
        label="Chambres",
        wanted=f"{n}",
        status="partial",
        detail=(
            f"{n} coins nuit dans une seule pièce : le découpage en chambres viendra avec le "
            "plan généré (L3)"
        ),
    )


ZONE_ANCHORS: dict[str, tuple[str, ...]] = {
    "bureau": ("desk", "desk_corner"),
    "lecture": ("armchair",),
    "couchage": BEDS,
    "detente": ("sofa", "sofa_lounge", "sofa_corner"),
    "cuisine": ("stove", "kitchen_sink"),
    "repas": ("table", "round_table"),
}
"""Meuble qui dit qu'une zone demandée est là."""


def _rooms_with(level: Level) -> dict[str, list[str]]:
    """Catégorie → pièces (noms) où au moins un meuble de cette catégorie est posé."""
    graph_rooms = {
        r.id: (r.name or "pièce", Polygon([(p.x, p.y) for p in d.polygon]))
        for r in level.elements.values()
        if isinstance(r, Room) and (d := compute_derived(level).rooms.get(r.id)) is not None
    }
    out: dict[str, list[str]] = {}
    for e in level.elements.values():
        if not isinstance(e, Furniture):
            continue
        at = Point(e.transform.position.x, e.transform.position.y)
        for name, polygon in graph_rooms.values():
            if polygon.contains(at):
                out.setdefault(e.category, [])
                if name not in out[e.category]:
                    out[e.category].append(name)
                break
    return out


def _bedrooms_rooms_item(level: Level, n: int) -> RequestItem:
    """Plan généré (L3) : de vraies chambres, chacune avec son lit."""
    by_room = _rooms_with(level)
    with_bed = {name for c in BEDS for name in by_room.get(c, [])}
    rooms = [
        r.name or "chambre"
        for r in level.elements.values()
        if isinstance(r, Room) and r.room_type == "bedroom"
    ]
    wanted = f"{n}"
    if len(rooms) < n:
        return RequestItem(
            label="Chambres",
            wanted=wanted,
            status="missing",
            detail=f"{_count(len(rooms), 'chambre dessinée', 'chambres dessinées')} sur {n}",
        )
    empty = [name for name in rooms if name not in with_bed]
    if empty:
        return RequestItem(
            label="Chambres",
            wanted=wanted,
            status="partial",
            detail=f"{' et '.join(empty)} sans lit : place insuffisante",
        )
    detail = "une chambre avec son lit" if n == 1 else f"{n} chambres, un lit dans chacune"
    return RequestItem(label="Chambres", wanted=wanted, status="ok", detail=detail)


def _zone_items(level: Level, zones: list[str]) -> list[RequestItem]:
    by_room = _rooms_with(level)
    items = []
    for zone in dict.fromkeys(zones):
        anchors = ZONE_ANCHORS.get(zone)
        if anchors is None:
            continue
        where = [name for c in anchors for name in by_room.get(c, [])]
        label = ZONE_LABELS.get(zone, zone)
        items.append(
            RequestItem(
                label="Zone",
                wanted=f"coin {label}" if zone not in ("cuisine", "repas") else label,
                status="ok" if where else "missing",
                detail=f"dans {where[0].lower()}" if where else "pas de place pour ce coin",
            )
        )
    return items


def _furniture_items(
    placed: dict[str, int], wanted: dict[str, int], avoid: list[str]
) -> list[RequestItem]:
    items = []
    for category, count in wanted.items():
        got = placed.get(category, 0)
        items.append(
            RequestItem(
                label="Meuble",
                wanted=furniture_words(category, count),
                status="ok" if got >= count else ("partial" if got else "missing"),
                detail=""
                if got >= count
                else f"{_count(got, 'posé', 'posés')} sur {count} : place insuffisante",
            )
        )
    for category in dict.fromkeys(avoid):
        got = placed.get(category, 0)
        items.append(
            _choice(
                "À éviter",
                furniture_words(category, 1),
                got == 0,
                f"{_count(got, 'posé', 'posés')} malgré tout",
            )
        )
    return items


def check_request(  # noqa: PLR0912 - une ligne par demande
    project: Project,
    level_id: UUID,
    answers: GuidedAnswers | None,
    translation: Translation | None,
) -> list[RequestItem]:
    """Chaque demande (réponses guidées et consigne) confrontée au projet final."""
    level = project.levels[level_id]
    room = _main_room(level)
    intent = room.intent if room else None
    placed = _placed(level)
    asked = answers or GuidedAnswers()
    said: dict[str, Any] = dict(translation.fields) if translation else {}
    items: list[RequestItem] = []

    style = asked.style if asked.style in STYLES else said.get("style")
    if style:
        items.append(
            _choice(
                "Style",
                STYLES.get(str(style), str(style)),
                intent is not None and intent.style == style,
                "un autre style a été retenu",
            )
        )
    for mood in asked.mood or list(said.get("mood") or []):
        items.append(
            _choice(
                "Ambiance", MOOD_LABELS.get(mood, mood), intent is not None and mood in intent.mood
            )
        )
    programme = _programme(level)
    if programme is not None:
        # Plan généré (L3b) : le type de logement demandé, tenu puisque le plan en vient.
        items.append(RequestItem(label="Type", wanted=_kind_words(programme), status="ok"))
    area = asked.area_m2
    if area is None and programme is not None and programme.get("area_m2"):
        area = float(programme["area_m2"])
    if area is not None:
        items.append(_area_item(level, area))
    bedrooms = asked.bedrooms
    if bedrooms is None and programme is not None and programme.get("bedrooms"):
        bedrooms = int(programme["bedrooms"])
    if bedrooms is not None:
        if is_generated(level):
            items.append(_bedrooms_rooms_item(level, bedrooms))
        else:
            items.append(_bedrooms_item(placed, bedrooms))
    zones = [z for z in (said.get("zones") or []) if isinstance(z, str) and z != "couchage"]
    if programme is not None and programme.get("office") == "coin":
        zones.append("bureau")
    items += _zone_items(level, zones)
    wanted: dict[str, int] = {}
    for item in said.get("furniture") or []:
        wanted[item["category"]] = max(wanted.get(item["category"], 0), int(item["count"]))
    for category in asked.furniture:
        wanted[category] = max(wanted.get(category, 0), 1)
    if is_generated(level):
        # Plan généré (L3) : WC et salle de bain sont des pièces, pas des meubles demandés.
        for category in ("toilet", "shower", "bathtub", "washbasin", "bathroom_cabinet"):
            wanted.pop(category, None)
    items += _furniture_items(placed, wanted, [*asked.avoid, *(said.get("avoid") or [])])
    aspect = asked.aspect or (said.get("views") or {}).get("aspect")
    if aspect:
        items.append(
            _choice(
                "Format",
                "portrait 4:5" if aspect == "4:5" else "paysage 4:3",
                intent is not None and intent.views.aspect == aspect,
            )
        )
    light = asked.light_time or (said.get("light") or {}).get("time")
    if light:
        items.append(
            _choice(
                "Lumière",
                TIME_LABELS.get(light, light),
                intent is not None and intent.light.time == light,
            )
        )
    for note in translation.notes if translation else []:
        match = _UNKNOWN.search(note)
        if match:
            items.append(
                RequestItem(
                    label="Demande",
                    wanted=match["what"],
                    status="missing",
                    detail="pas au catalogue",
                )
            )
    return items
