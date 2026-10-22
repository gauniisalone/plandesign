"""Chemin « zéro geste » (V1, note 10, point 6) : après le plan automatique, sans autre geste,
l'ambiance déduite, l'aménagement par zones appliqué, les trois vues, leurs rendus et leur
finition IA.

Principes (augus, 5 octobre) :
- **le chemin automatique est mis en avant, l'éditeur reste entier** : chaque étape est un
  pas d'historique ordinaire, annulable ;
- **le manuel prime** : une étape ne réécrit rien de ce que l'utilisateur a choisi
  (`user_defined`) : une ambiance choisie est gardée, des meubles déjà posés ne sont pas
  remplacés (l'aménagement est sauté), une vue retouchée reste ;
- **l'utilisateur garde la main pendant le calcul** : chaque modification se fait sur le
  projet tel qu'il est enregistré à cet instant, sous le verrou du projet (ses gestes faits
  entre-temps sont gardés) ; les rendus ne modifient pas le projet ;
- **la finition enchaînée garde l'image Blender à côté** et se coupe dans les réglages
  (`Project.render.auto_finish`) ; sans les poids du modèle d'images, elle est sautée et c'est
  dit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue

from openplan.adapters.protocols import AdapterUnavailableError, ImageFinisher
from openplan.pipeline.blender_bridge import new_run_dir
from openplan.pipeline.finish import FinishOptions, finish_render
from openplan.pipeline.guided import RequestItem, answer_fields, check_request, merge_fields
from openplan.pipeline.inspire import InspirationOptions, inspire_render
from openplan.pipeline.instruction import (
    MAX_INSTRUCTION,
    Reading,
    Translation,
    intent_fields,
    translate_phrases,
)
from openplan.pipeline.layouts import (
    LayoutBody,
    ZoneBriefBody,
    apply_layout,
    brief_from_intent,
    generate_layouts,
)
from openplan.pipeline.mainroom import (
    GENERATED_KEY,
    ZONES_KEY,
    habitable_rooms,
    is_generated,
    main_room,
)
from openplan.pipeline.naming import rename
from openplan.pipeline.render import Daylight, camera_view, render_camera_view, render_expectation
from openplan.pipeline.timings import Countdown, Timings
from openplan.pipeline.views import AUTO_KEY, ROLES, apply_auto_views
from openplan.scene.catalog import load_catalog
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import SetIntent, apply_op, apply_ops
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Camera, Furniture, Room
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import Level, Project
from openplan.validation.invariants import validate_invariants

ACTOR = "pipeline:autopilot"
StepStatus = Literal["done", "skipped", "failed"]
LAYOUT_BUDGET_S = 20.0
FINISH_EXPECTED_S = 25.0
INSPIRATION_EXPECTED_S = 30.0
AUTO_FINISH_STRENGTH = 0.35
"""Finition du chemin automatique (sortie A, référence) : force bloquée à 0,35 (PO, 6 oct.)."""
LIGHT_TO_DAYLIGHT: dict[str, Daylight] = {
    "matin": "matin",
    "midi": "midi",
    "heure_doree": "heure_doree",
    "soir": "soir",
}


class AutopilotStep(StrictModel):
    name: str
    label: str
    status: StepStatus
    note: str = ""
    seconds: float = 0.0


class AutopilotRun(StrictModel):
    """Ce que le chemin automatique a fait, étape par étape ; les rendus rejoignent la
    galerie (`run_ids`), les finitions sont à côté de leur image Blender."""

    steps: list[AutopilotStep] = Field(default_factory=list)
    run_ids: list[UUID] = Field(default_factory=list)
    finished: list[UUID] = Field(default_factory=list)
    inspired: list[UUID] = Field(default_factory=list)
    """Rendus qui ont leur image d'inspiration (sortie B)."""
    total_s: float = 0.0
    request: list[RequestItem] = Field(default_factory=list)
    """Respect de la demande écrite ou choisie sur l'accueil (L2) : vide sans demande."""


class Workspace:
    """Accès au projet enregistré : charger → modifier → sauver sous le verrou du projet."""

    def __init__(
        self,
        load: Callable[[], Project],
        save: Callable[[Project], object],
        lock: AbstractContextManager[object] | None = None,
    ) -> None:
        self.load = load
        self.save = save
        self.lock = lock

    def change(self, edit: Callable[[Project], Project | None]) -> Project:
        """Applique `edit` au projet enregistré (None = rien à changer) ; renvoie le projet."""
        with self.lock if self.lock is not None else nullcontext():
            project = self.load()
            after = edit(project)
            if after is None or after is project:
                return project
            report = validate_invariants(after)
            # Refusé seulement si l'étape casse un projet valide (un défaut d'avant ne bloque
            # pas le chemin).
            if not report.valid and validate_invariants(project).valid:
                msg = "; ".join(v.message for v in report.violations[:2])
                raise AutopilotStepError(msg or "invariants non tenus")
            self.save(after)
            return after


@dataclass(frozen=True)
class HomeRequest:
    """Ce qui a été écrit sur l'accueil (L1a) : la consigne, traduite à l'étape Ambiance
    (modèle consenti par `read`, sinon phrases simples) ; le pas d'historique est celui de
    l'utilisateur (`actor`), puisque c'est son choix : les champs sont `user_defined`."""

    instruction: str = ""
    read: Callable[[str, Project], Reading] | None = None
    actor: str = USER_ACTOR
    answers: GuidedAnswers | None = None
    """Questions guidées (L2) : elles priment sur ce que dit le texte."""

    def reading(self, project: Project) -> Reading:
        text = self.instruction.strip()[:MAX_INSTRUCTION]
        if not text:
            return Reading(translation=Translation(), understanding="none")
        if self.read is not None:
            return self.read(text, project)
        translation = translate_phrases(text, load_catalog().categories)
        return Reading(translation=translation, understanding="phrases")

    def note(self, reading: Reading) -> str:
        understood = ", ".join(reading.translation.understood)
        if self.answers is not None:
            understood = ", ".join(x for x in ("réponses guidées", understood) if x)
        return f"consigne traduite : {understood or 'rien de reconnu'}"[:200]

    def apply(self, project: Project, level_id: UUID, room: Room, reading: Reading) -> Project:
        translation = reading.translation
        # Plan généré (L3) : les chambres sont des pièces, pas des coins nuit dans le séjour.
        rooms_known = GENERATED_KEY in room.metadata
        chosen = (
            answer_fields(self.answers, load_catalog().categories, rooms_known=rooms_known)
            if self.answers
            else {}
        )
        said = dict(translation.fields)
        if rooms_known:
            said = _for_generated_room(said, room, project.levels[level_id])
        fields = intent_fields(room.intent, merge_fields(said, chosen))
        _add_programme_zones(fields, room)
        text = self.instruction.strip()[:MAX_INSTRUCTION]
        if text:
            fields.update({"instruction": text, "translated_by": translation.by})
        op = SetIntent(room_id=room.id, fields=fields)
        # Appliquées : la consigne et les réponses gardées (L1b, T1) n'attendent plus.
        project = project.model_copy(update={"pending_instruction": None, "pending_answers": None})
        return apply_op(project, level_id, op, actor=self.actor)


class AutopilotStepError(RuntimeError):
    """Une étape n'a pas pu s'appliquer ; les suivantes continuent si elles le peuvent."""


def _main_room(level: Level) -> Room | None:
    return main_room(level)


WET_FURNITURE = ("toilet", "shower", "bathtub", "washbasin", "bathroom_cabinet")
ZONE_FURNITURE: dict[str, tuple[str, ...]] = {
    "bureau": ("desk", "desk_corner", "chair"),
    "couchage": ("bed_double", "bed_single", "bed_bunk", "bed_builtin", "nightstand"),
}


def _for_generated_room(
    fields: dict[str, JsonValue], room: Room, level: Level
) -> dict[str, JsonValue]:
    """Plan généré (L3) : les pièces sont déjà typées et les zones déjà réparties ; la consigne
    ne retype pas le séjour (« cuisine ouverte » n'en fait pas une cuisine) et ne lui ajoute
    pas une zone (ni ses meubles) que le programme a mise dans une autre pièce."""
    out = {k: v for k, v in fields.items() if k not in ("room_type", "usage")}
    own = room.metadata.get(ZONES_KEY) or []
    elsewhere = {
        z
        for r in level.elements.values()
        if isinstance(r, Room) and r.id != room.id
        for z in (r.metadata.get(ZONES_KEY) or [])  # type: ignore[union-attr]
        if z not in own  # type: ignore[operator]
    }
    if isinstance(out.get("zones"), list):
        out["zones"] = [z for z in out["zones"] if z not in elsewhere]  # type: ignore[union-attr]
    # WC et salle de bain sont des pièces du plan : « wc séparés » n'est pas un meuble à poser
    banned = {c for z in elsewhere for c in ZONE_FURNITURE.get(str(z), ())} | set(WET_FURNITURE)
    if isinstance(out.get("furniture"), list):
        out["furniture"] = [
            f
            for f in out["furniture"]  # type: ignore[union-attr]
            if not (isinstance(f, dict) and f.get("category") in banned)
        ]
    return out


def _add_programme_zones(fields: dict[str, JsonValue], room: Room) -> None:
    """Zones voulues par le programme d'un plan généré (cuisine ouverte, coin bureau, coin
    nuit du studio), ajoutées à celles de la consigne."""
    wanted = room.metadata.get(ZONES_KEY)
    if not isinstance(wanted, list) or not wanted:
        return
    said = fields.get("zones")
    zones = list(said) if isinstance(said, list) else []
    for zone in wanted:
        if zone not in zones:
            zones.append(zone)
    fields["zones"] = zones


def _other_rooms(project: Project, level_id: UUID, main: Room) -> tuple[Project, int]:
    """Plan généré (L3) : chaque autre pièce habitable sans ambiance prend celle du séjour
    (style), son type et ses zones ; un seul pas d'historique attribué au chemin."""
    level = project.levels[level_id]
    style = main.intent.style if main.intent is not None else "naturel"
    ops = []
    for room in habitable_rooms(level)[1:]:
        if room.intent is not None:
            continue
        fields: dict[str, JsonValue] = {"style": style, "room_type": room.room_type}
        _add_programme_zones(fields, room)
        ops.append(SetIntent(room_id=room.id, fields=fields))
    if not ops:
        return project, 0
    after = apply_ops(project, level_id, ops, actor=ACTOR, label="Ambiance des autres pièces")  # type: ignore[arg-type]
    return after, len(ops)


def step_estimates(
    device: str,
    *,
    finish: bool,
    inspiration: bool = False,
    timings: Timings | None = None,
) -> list[float]:
    """Durée attendue (s) de chaque étape : ambiance, aménagement, vues, trois rendus (sonde
    d'exposition comprise), trois finitions et trois inspirations si elles sont enchaînées.
    Les durées mesurées sur cette machine (moyenne glissante) remplacent les valeurs du
    protocole."""
    one, _ = render_expectation(device, 64)
    measured = timings or Timings(None)
    render = measured.estimate("render", one * 1.3)
    out = [1.0, measured.estimate("layout", 5.0), 1.0, render, render, render]
    if finish:
        out += [measured.estimate("finish", FINISH_EXPECTED_S)] * 3
    if inspiration:
        out += [measured.estimate("inspiration", INSPIRATION_EXPECTED_S)] * 3
    return out


def plan_estimate(
    project: Project,
    level_id: UUID,
    device: str,
    *,
    finish: bool,
    inspiration: bool = False,
    timings: Timings | None = None,
) -> float:
    """Durée attendue (s) du chemin entier."""
    return sum(step_estimates(device, finish=finish, inspiration=inspiration, timings=timings))


def run_autopilot(  # noqa: PLR0912, PLR0913, PLR0915 - un chemin de bout en bout
    space: Workspace,
    level_id: UUID,
    runs_dir: Path,
    *,
    stage: Callable[..., None],
    blender_bin: str | None,
    device: str,
    gpu: bool = True,
    samples: int = 64,
    finisher: Callable[[], ImageFinisher] | None = None,
    timings: Timings | None = None,
    home: HomeRequest | None = None,
    taken_names: Callable[[], list[str]] | None = None,
    ridge_view: bool = False,
) -> AutopilotRun:
    """Le chemin de bout en bout, une étape après l'autre ; une étape qui échoue est dite
    et les suivantes continuent si elles le peuvent. Le temps restant est annoncé en direct
    (`stage(texte, remaining=…)`), d'après les durées mesurées ; chaque durée réelle de
    rendu, de finition et d'aménagement est rangée pour les prochaines estimations.
    `ridge_view` (KAN-88, finition Z-Image) : la vue dans l'axe du faîtage s'ajoute aux vues."""
    run = AutopilotRun()
    home_reading: Reading | None = None
    started = time.perf_counter()
    project = space.load()
    finish = project.render.auto_finish and finisher is not None
    inspiration = project.render.auto_inspiration and finisher is not None
    countdown = Countdown(
        step_estimates(device, finish=finish, inspiration=inspiration, timings=timings)
    )
    total_steps = len(countdown.estimates)
    done_steps = 0

    def announce(label: str) -> None:
        countdown.start(done_steps)
        stage(f"Étape {done_steps + 1}/{total_steps} : {label}", remaining=countdown.remaining)

    def record(name: str, label: str, status: StepStatus, note: str, t0: float) -> None:
        nonlocal done_steps
        done_steps += 1
        seconds = time.perf_counter() - t0
        kind = name.split(":", 1)[0]
        measured = ("render", "finish", "layout", "inspiration")
        if timings is not None and status == "done" and kind in measured:
            timings.record(kind, seconds)
        run.steps.append(
            AutopilotStep(
                name=name,
                label=label,
                status=status,
                note=note,
                seconds=round(seconds, 1),
            )
        )

    # 1. Ambiance : la consigne écrite sur l'accueil si elle est là (traduite, champs
    # `user_defined`) ; sinon déduite, seulement si la pièce n'en a pas (rien de choisi n'est
    # réécrit). Puis le nom automatique du projet, s'il est encore automatique.
    t0 = time.perf_counter()
    announce("ambiance")
    try:
        note = ""
        # La traduction (modèle éventuel) se fait hors du verrou du projet.
        reading = home.reading(space.load()) if home is not None else None
        home_reading = reading

        def _with_others(p: Project, main_id: UUID, *, keep_none: bool = False) -> Project | None:
            nonlocal note
            main = p.levels[level_id].elements.get(main_id)
            if not isinstance(main, Room):
                return p
            after, count = _other_rooms(p, level_id, main)
            if count:
                rooms = "pièce" if count == 1 else "pièces"
                note = f"{note} ; même style pour {count} autre{'' if count == 1 else 's'} {rooms}"
                note = note.removeprefix("ambiance déjà posée : gardée ; ")
            return None if keep_none and not count else after

        def intent(p: Project) -> Project | None:
            nonlocal note
            room = _main_room(p.levels[level_id])
            if room is None:
                note = "pas de pièce fermée"
                return None
            if home is not None and reading is not None:
                note = home.note(reading)
                after = home.apply(p, level_id, room, reading)
                return _with_others(after, room.id)
            if room.intent is not None:
                note = "ambiance déjà posée : gardée"
                return _with_others(p, room.id, keep_none=True)
            fields: dict[str, JsonValue] = {"style": "naturel"}
            room_type = room.room_type or ("attic" if p.levels[level_id].kind == "attic" else None)
            if room_type:
                fields["room_type"] = room_type
            _add_programme_zones(fields, room)
            note = "ambiance déduite : naturel (modifiable dans la fiche)"
            after = apply_op(p, level_id, SetIntent(room_id=room.id, fields=fields), actor=ACTOR)
            return _with_others(after, room.id)

        space.change(intent)
        names = taken_names() if taken_names is not None else []
        space.change(lambda p: rename(p, names, actor=ACTOR) if p.name_auto else None)
        record(
            "intent",
            "Ambiance",
            "skipped" if "gardée" in note or "pas de" in note else "done",
            note,
            t0,
        )
    except Exception as exc:
        record("intent", "Ambiance", "failed", str(exc)[:160], t0)

    # 2. Aménagement par zones : seulement si le niveau n'a encore aucun meuble.
    t0 = time.perf_counter()
    announce("aménagement par zones")
    try:
        note = ""

        def layout(p: Project) -> Project | None:
            nonlocal note
            level = p.levels[level_id]
            if any(isinstance(e, Furniture) for e in level.elements.values()):
                note = "des meubles sont déjà posés : rien n'est remplacé"
                return None
            # Une pièce seule : elle ; un plan généré (L3) : le séjour, puis chaque chambre,
            # le bureau et la cuisine fermée, chacun avec son brief.
            rooms = habitable_rooms(level)
            single = len(rooms) <= 1
            after = p
            placed = 0
            furnished: list[str] = []
            targets: list[Room | None] = list(rooms) or [None]
            for room in targets:
                room_id = None if single or room is None else room.id
                body = LayoutBody(
                    brief=brief_from_intent(after, level_id, ZoneBriefBody(room_id=room_id)),
                    # plusieurs pièces chargées (L3) : trois variantes, la mieux notée gardée
                    variants=1 if single else 3,
                    budget_s=LAYOUT_BUDGET_S if single else LAYOUT_BUDGET_S / 3,
                    room_id=room_id,
                )
                proposals = generate_layouts(after, level_id, body).proposals
                if not proposals:
                    continue
                placed += len(proposals[0].furniture)
                furnished.append(room.name or "pièce" if room is not None else "pièce")
                after = apply_layout(after, level_id, proposals[0], actor=ACTOR)
            if not placed:
                note = "aucune disposition valide trouvée"
                return None
            note = f"{placed} meubles posés (variante 1, annulable)"
            if not single:
                note += " dans " + ", ".join(furnished)
            return after

        space.change(layout)
        status: StepStatus = "done" if "posés" in note and "déjà" not in note else "skipped"
        record("layout", "Aménagement", status, note, t0)
    except Exception as exc:
        record("layout", "Aménagement", "failed", str(exc)[:160], t0)

    # 3. Trois vues automatiques (une vue retouchée à la main reste).
    t0 = time.perf_counter()
    announce("trois vues")
    try:
        holder: dict[str, str] = {}

        def views(p: Project) -> Project:
            after, report = apply_auto_views(p, level_id, actor=ACTOR, ridge=ridge_view)
            holder["note"] = "; ".join([*report.notes, *report.kept, *report.skipped])
            return after

        project = space.change(views)
        record("views", "Vues", "done", holder.get("note", ""), t0)
    except Exception as exc:
        record("views", "Vues", "failed", str(exc)[:160], t0)
        project = space.load()

    # 4. Rendus des vues automatiques, puis 5. leur finition (l'image Blender reste).
    level = project.levels[level_id]
    cameras = sorted(
        (
            e
            for e in level.elements.values()
            if isinstance(e, Camera) and e.metadata.get(AUTO_KEY) in ROLES
        ),
        key=lambda c: ROLES.index(c.metadata[AUTO_KEY]),
    )
    room = _main_room(level)
    light = room.intent.light.time if room is not None and room.intent is not None else "midi"
    daylight = LIGHT_TO_DAYLIGHT.get(light)
    rendered: list[tuple[UUID, Path, str]] = []
    for camera in cameras:
        t0 = time.perf_counter()
        name = camera.name or "vue"
        announce(f"rendu « {name} »")
        try:
            camera_view(project, level_id, camera.id)
            run_id, out_dir = new_run_dir(runs_dir)
            render_camera_view(
                project,
                level_id,
                camera.id,
                out_dir,
                blender_bin=blender_bin,
                samples=samples,
                gpu=gpu,
                run_id=run_id,
                daylight=daylight,
            )
            run.run_ids.append(run_id)
            rendered.append((run_id, out_dir, name))
            record(f"render:{camera.id}", f"Rendu {name}", "done", "", t0)
        except Exception as exc:
            record(f"render:{camera.id}", f"Rendu {name}", "failed", str(exc)[:160], t0)
    missing = 3 - len(cameras)
    done_steps += max(0, missing)
    # 5. Finition (A, référence, force 0,35) puis 6. inspiration (B, « cotes non
    # garanties ») : le même modèle d'images, chargé une fois ; chacune se coupe à part.
    model: object | None = None
    if (finish or inspiration) and finisher is not None and rendered:
        try:
            model = finisher()
        except AdapterUnavailableError as exc:
            t0 = time.perf_counter()
            note = f"modèle d'images absent : {exc}"[:200]
            record("finish", "Finition IA et inspiration", "skipped", note, t0)
    if finish and model is not None:
        for run_id, out_dir, name in rendered:
            t0 = time.perf_counter()
            announce(f"finition « {name} »")
            try:
                finish_render(
                    project,
                    out_dir,
                    run_id,
                    FinishOptions(strength=AUTO_FINISH_STRENGTH),
                    finisher=model,  # type: ignore[arg-type]
                )
                run.finished.append(run_id)
                record(f"finish:{run_id}", f"Finition {name}", "done", "image Blender gardée", t0)
            except Exception as exc:
                record(f"finish:{run_id}", f"Finition {name}", "failed", str(exc)[:160], t0)
    if inspiration and model is not None and hasattr(model, "inspire"):
        for run_id, out_dir, name in rendered:
            t0 = time.perf_counter()
            announce(f"inspiration « {name} »")
            try:
                inspire_render(
                    project,
                    out_dir,
                    run_id,
                    InspirationOptions(),
                    inspirer=model,  # type: ignore[arg-type]
                )
                run.inspired.append(run_id)
                record(
                    f"inspiration:{run_id}",
                    f"Inspiration {name}",
                    "done",
                    "cotes non garanties ; image Blender et finition gardées",
                    t0,
                )
            except Exception as exc:
                record(f"inspiration:{run_id}", f"Inspiration {name}", "failed", str(exc)[:160], t0)
    for wanted, setting, label in (
        (project.render.auto_finish, "finish", "Finition IA"),
        (project.render.auto_inspiration, "inspiration", "Inspiration"),
    ):
        if not wanted:
            run.steps.append(
                AutopilotStep(
                    name=setting,
                    label=label,
                    status="skipped",
                    note="coupée dans les réglages du projet",
                )
            )
    if home is not None or is_generated(space.load().levels[level_id]):
        try:
            run.request = check_request(
                space.load(),
                level_id,
                home.answers if home is not None else None,
                home_reading.translation if home_reading else None,
            )
        except Exception:
            run.request = []
    run.total_s = round(time.perf_counter() - started, 1)
    return run
