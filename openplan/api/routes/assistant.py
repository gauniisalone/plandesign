"""L'assistant agit sur le plan : intentions typées → gestes calculés, commits `assistant`."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field, JsonValue

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LanguageModel
from openplan.api.deps import ActorDep, LanguageModelDep, ProjectDep, StoreDep
from openplan.api.routes.scene import EditResult, _result
from openplan.pipeline import home_settings, journal
from openplan.pipeline.assistant import AssistantTurn, act
from openplan.pipeline.guided import answer_fields, merge_fields
from openplan.pipeline.instruction import (
    MAX_INSTRUCTION,
    Reading,
    Translation,
    intent_fields,
    read_instruction,
    translate_phrases,
)
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.say import SayTurn, readers_for, say
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import OpError, SetIntent, apply_op
from openplan.schemas.assistant import AssistantReply
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Room
from openplan.schemas.home import GuidedAnswers
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["assistant"])

Understanding = Literal["model", "phrases", "none"]


class AssistantActBody(StrictModel):
    text: str = ""
    """La phrase de l'utilisateur (gardée dans le tour)."""
    reply: AssistantReply
    """Les intentions déjà comprises (par le modèle côté serveur en B3, ou des phrases-types)."""


class AssistantActResult(EditResult):
    turn: AssistantTurn


class AssistantBody(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    last_element_id: UUID | None = None
    """Dernier meuble dont on a parlé (mémoire courte de la conversation, tenue par le front)."""


class AssistantPreview(StrictModel):
    """Ce qui partirait au modèle pour cette phrase, et vers où ; rien n'est envoyé."""

    configured: bool
    model_id: str | None
    destination: str | None
    remote: bool | None
    consented: bool
    text: str
    """Le message exact (phrase + description du plan) ; vide sans modèle."""
    never: list[str]


NEVER_SENT = [
    "les photos et leurs métadonnées",
    "le fichier du projet et son historique",
    "votre nom et le nom du projet",
]


@router.post("/projects/{project_id}/levels/{level_id}/assistant/preview")
def assistant_preview(
    level_id: UUID,
    body: AssistantBody,
    project: ProjectDep,
    model: LanguageModelDep,
) -> AssistantPreview:
    """Aperçu avant envoi (directive 3, B5) : le texte mot pour mot et la destination.
    Aucun appel au modèle."""
    from openplan.adapters.remote.assistant import AssistantAgent
    from openplan.pipeline.intention import describe_room

    privacy = project.privacy
    consented = privacy.allow_remote_models and privacy.remote_consent
    if model is None:
        return AssistantPreview(
            configured=False,
            model_id=None,
            destination=None,
            remote=None,
            consented=consented,
            text="",
            never=NEVER_SENT,
        )
    catalog = load_catalog()
    labels = {a.category: a.label for a in reversed(catalog.assets)}
    agent = AssistantAgent(model, lambda request: None, labels=labels)  # type: ignore[arg-type, return-value]
    text = agent.user_prompt(body.text, describe_room(project, level_id), catalog.categories)
    return AssistantPreview(
        configured=True,
        model_id=model.model_id,
        destination=model.destination,
        remote=model.remote,
        consented=consented,
        text=text,
        never=NEVER_SENT,
    )


class SayBody(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    context_id: UUID | None = None
    """« À propos de » : la pièce ou le meuble choisi (ou désigné dans un rendu)."""
    last_element_id: UUID | None = None


class SayResult(EditResult):
    turn: SayTurn
    gemini_case: bool
    """La case « Gemini » de l'accueil (rappel du panneau)."""
    remote_model: bool
    """Un modèle distant est configuré : la case a un sens."""


@router.post("/projects/{project_id}/levels/{level_id}/assistant/say")
def assistant_say(
    level_id: UUID,
    body: SayBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
) -> SayResult:
    """P5a : l'assistant unifié. Une phrase → l'ambiance de la pièce en contexte (lecteur de
    consignes) et les gestes sur le plan (assistant), en un tour : chaque partie dit ce qui a
    été fait, refusé ou demandé ; un échec partiel garde ce qui a réussi ; `committed` pas
    d'historique, que « annuler » défait ensemble. Le modèle distant ne lit la phrase que si la
    case Gemini de l'accueil est cochée (texte seul, accord du projet exigé)."""
    case = home_settings.load(store.root).gemini_text
    gateway = ModelGateway(store.project_dir(project.id) / "network_log.jsonl")
    after, turn = say(
        project,
        level_id,
        body.text,
        load_catalog().categories,
        store.runs_dir(project.id),
        readers=readers_for(model, case, gateway),
        context_id=body.context_id,
        last_element_id=body.last_element_id,
    )
    report = validate_invariants(after)
    if after is not project:
        store.save(after)
    journal.record(
        store.project_dir(project.id),
        after,
        JournalEvent(
            project_id=project.id,
            kind="assistant",
            text=turn.text,
            translation={
                "parts": [
                    {"kind": p.kind, "status": p.status, "summary": p.summary[:200]}
                    for p in turn.parts
                ]
            },
            by=turn.model_id or turn.reader,
            element_ids=[turn.room_id] if turn.room_id else [],
            history_ids=journal.history_ids(project, after),
        ),
    )
    base = _result(after, level_id, report)
    return SayResult(
        **base.model_dump(),
        turn=turn,
        gemini_case=case,
        remote_model=model is not None and model.remote,
    )


THREAD_TURNS = 20
THREAD_ORIGINS = {
    "autopilot": "Consigne de l'accueil, appliquée par le chemin automatique",
    "instruction": "Consigne appliquée à l'ambiance de la pièce",
}


class ThreadTurn(StrictModel):
    text: str
    parts: list[dict[str, JsonValue]]
    by: str | None = None


class AssistantThread(StrictModel):
    """Le fil de l'assistant relu depuis le journal local (P5), ou vide s'il est coupé."""

    journal_enabled: bool
    turns: list[ThreadTurn]


@router.get("/projects/{project_id}/assistant/thread")
def assistant_thread(project: ProjectDep, store: StoreDep) -> AssistantThread:
    """Les 20 derniers tours de l'assistant unifié, relus du journal local (texte et issue de
    chaque partie, rien d'autre). Journal coupé : liste vide, le fil ne vit qu'en mémoire."""
    if not project.privacy.design_journal:
        return AssistantThread(journal_enabled=False, turns=[])
    turns: list[ThreadTurn] = []
    for event in journal.read(store.project_dir(project.id)):
        if event.kind in THREAD_ORIGINS and event.text:
            # La consigne de l'accueil (chemin automatique) ou d'une fiche, avant P5 : le
            # premier tour du fil.
            turns.append(
                ThreadTurn(
                    text=event.text,
                    parts=[
                        {
                            "kind": "ambiance",
                            "status": "done",
                            "summary": THREAD_ORIGINS[event.kind],
                        }
                    ],
                    by=event.by,
                )
            )
            continue
        parts = (event.translation or {}).get("parts")
        if event.kind != "assistant" or not event.text or not isinstance(parts, list):
            continue
        turns.append(
            ThreadTurn(
                text=event.text,
                parts=[p for p in parts if isinstance(p, dict)],
                by=event.by,
            )
        )
    return AssistantThread(journal_enabled=True, turns=turns[-THREAD_TURNS:])


class InstructionBody(StrictModel):
    text: str = Field(default="", max_length=MAX_INSTRUCTION)
    use_model: bool = True
    """Faux : phrases simples seulement, même avec un modèle configuré."""
    answers: GuidedAnswers | None = None
    """Questions guidées de l'accueil (L2) : elles priment sur le texte."""


class InstructionResult(EditResult):
    translation: Translation
    understanding: Understanding
    notice: str = ""


@router.post("/projects/{project_id}/levels/{level_id}/rooms/{room_id}/instruction")
def room_instruction(
    level_id: UUID,
    room_id: UUID,
    body: InstructionBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
    actor: ActorDep,
) -> InstructionResult:
    """V1, point 5 : une consigne libre (« grenier chaleureux, bois et lin, deux lits ») →
    champs de l'ambiance de la pièce, par le modèle configuré (texte seul, consentement du
    projet pour un modèle distant), sinon par les phrases simples. Appliqué en un pas
    d'historique annulable, champs `user_defined` ; la consigne est gardée dans l'ambiance."""
    level = project.levels.get(level_id)
    room = level.elements.get(room_id) if level is not None else None
    if not isinstance(room, Room):
        msg = f"pièce inconnue : {room_id}"
        raise OpError(msg)
    text = body.text.strip()
    if not text and body.answers is None:
        msg = "une consigne ou des réponses guidées sont attendues"
        raise OpError(msg)
    use = body.use_model and model is not None and bool(text)
    if text:
        reading = read_instruction(
            text,
            load_catalog().categories,
            model=model if use else None,
            gateway=ModelGateway(store.project_dir(project.id) / "network_log.jsonl")
            if use
            else None,
            project=project,
        )
    else:
        reading = Reading(translation=Translation(), understanding="none")
    translation, understanding, notice = reading.translation, reading.understanding, reading.notice
    chosen = answer_fields(body.answers, load_catalog().categories) if body.answers else {}
    fields = intent_fields(room.intent, merge_fields(translation.fields, chosen))
    # Une consigne appliquée à la main remplace la consigne gardée de l'accueil (L1b) et les
    # réponses de la note Exigences (T1).
    project = project.model_copy(update={"pending_instruction": None, "pending_answers": None})
    if text:
        fields.update({"instruction": text, "translated_by": translation.by})
    after = apply_op(project, level_id, SetIntent(room_id=room_id, fields=fields), actor=actor)
    report = validate_invariants(after)
    store.save(after)
    journal.record(
        store.project_dir(project.id),
        after,
        JournalEvent(
            project_id=project.id,
            kind="instruction",
            text=text or None,
            translation=dict(translation.fields),
            by=translation.by,
            element_ids=[room_id],
            history_ids=journal.history_ids(project, after),
            note=understanding,
        ),
    )
    base = _result(after, level_id, report)
    return InstructionResult(
        **base.model_dump(), translation=translation, understanding=understanding, notice=notice
    )


class InstructionPreviewBody(StrictModel):
    text: str = Field(min_length=1, max_length=MAX_INSTRUCTION)
    use_model: bool = False
    """Case « Mieux comprendre mon texte avec Gemini » cochée (L2) ; sans effet si l'accord
    n'est pas retenu pour la machine."""


class InstructionPreviewResult(StrictModel):
    translation: Translation
    understanding: Understanding
    """« model » : le modèle distant a lu le texte ; sinon les phrases simples, sur la machine."""
    notice: str = ""


@router.post("/instruction/preview")
def instruction_preview(
    body: InstructionPreviewBody, store: StoreDep, model: LanguageModelDep
) -> InstructionPreviewResult:
    """Accueil : ce qui est compris de la consigne, avant tout projet (la ligne « Compris »).
    Par les phrases simples, hors ligne ; par le modèle distant seulement si la case est
    cochée (accord retenu pour la machine), texte seul, une ligne sans contenu au journal
    réseau, et retour aux phrases simples s'il échoue (quota, réseau), en le disant."""
    use = body.use_model and model is not None and home_settings.load(store.root).gemini_text
    if not use:
        translation = translate_phrases(body.text, load_catalog().categories)
        understanding: Understanding = "phrases" if translation.fields else "none"
        return InstructionPreviewResult(translation=translation, understanding=understanding)
    gateway = ModelGateway(home_settings.network_log(store.root))
    reading = read_instruction(
        body.text, load_catalog().categories, model=model, gateway=gateway, project=None
    )
    return InstructionPreviewResult(
        translation=reading.translation,
        understanding=reading.understanding,
        notice=reading.notice,
    )


class HomeSettingsView(StrictModel):
    gemini_text: bool
    """La case est cochée (accord retenu pour la machine, décoché par défaut)."""
    remote_model: bool
    """Un modèle distant est configuré (clé dans `.env`) : la case n'apparaît qu'alors."""
    destination: str | None = None


def _home_view(
    settings: home_settings.HomeSettings, model: LanguageModel | None
) -> HomeSettingsView:
    remote = model is not None and model.remote
    return HomeSettingsView(
        gemini_text=settings.gemini_text and remote,
        remote_model=remote,
        destination=model.destination if model is not None and remote else None,
    )


@router.get("/home/settings")
def home_settings_get(store: StoreDep, model: LanguageModelDep) -> HomeSettingsView:
    return _home_view(home_settings.load(store.root), model)


@router.put("/home/settings")
def home_settings_put(
    body: home_settings.HomeSettings, store: StoreDep, model: LanguageModelDep
) -> HomeSettingsView:
    return _home_view(home_settings.save(store.root, body), model)


@router.post("/projects/{project_id}/levels/{level_id}/assistant/act")
def assistant_act(
    level_id: UUID, body: AssistantActBody, project: ProjectDep, store: StoreDep
) -> AssistantActResult:
    """Exécute des intentions sur les meubles (ajouter, déplacer, retirer, proposer des
    dispositions) : chaque geste réussi est un commit attribué à `assistant`, annulable ; le
    tour dit ce qui a été fait, refusé ou demandé, et les éléments à surligner."""
    after, turn = act(project, level_id, body.text, body.reply, store.runs_dir(project.id))
    report = validate_invariants(after)
    if after is not project:
        store.save(after)
    base = _result(after, level_id, report)
    return AssistantActResult(**base.model_dump(), turn=turn)
