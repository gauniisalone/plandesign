"""Assistant unifié (P5a) : une seule phrase, deux lecteurs. Ce qui touche l'ambiance de la pièce
(style, humeur, lumière, matières, envies de meubles) passe par le lecteur de consignes et
devient un `SetIntent` ; les gestes sur le plan (ajouter, déplacer, retirer un meuble, cotes,
ouvertures, nom, propositions d'aménagement) passent par l'assistant. Un tour garde chaque
partie avec son issue (fait, refusé, question) et sa raison : un échec partiel n'annule pas ce
qui a réussi ; « annuler » défait tout le tour (`committed` pas d'historique).

Qui lit : les phrases simples, sur la machine ; le modèle configuré seulement si la case
« Gemini » de l'accueil est cochée pour un modèle distant (texte seul), et l'accord du projet
est toujours exigé par la passerelle ; un modèle indisponible retombe sur les phrases simples,
en le disant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue
from shapely.geometry import Point

from openplan.adapters.gateway import ConsentRequiredError, ModelGateway, ModelUnavailableError
from openplan.adapters.protocols import LanguageModel
from openplan.adapters.remote.assistant import AssistantParseError
from openplan.adapters.remote.openai_compatible import ModelCallError
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.assistant import AssistantTurn, act, understand_with_model
from openplan.pipeline.assistant_phrases import explicit_add, fold, understand
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.instruction import (
    Translation,
    intent_fields,
    read_instruction,
    translate_phrases,
)
from openplan.pipeline.mainroom import main_room, rooms_of
from openplan.scene.ops import OpError, SetIntent, apply_op
from openplan.schemas.assistant import AssistantIntent, AssistantReply
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Furniture, Room
from openplan.schemas.scene import Level, Project

ACTOR = "assistant"
PartKind = Literal["ambiance", "action", "answer"]
PartStatus = Literal["done", "refused", "question"]
Reader = Literal["phrases", "model"]
FURNITURE_ACTIONS = frozenset(
    {"add_furniture", "move_furniture", "remove_furniture", "propose_layouts"}
)
"""Gestes sur des meubles : quand la phrase en contient, les envies de meubles de la consigne
(« un bureau ») ne sont pas recopiées dans l'ambiance, pour ne pas compter deux fois."""
OPENING_EDITS = frozenset({"move_opening", "remove_opening", "resize_opening"})
"""KAN-32 : gestes sur une porte ou une fenêtre déjà posée."""
FIXED_FORMS = frozenset(
    {
        "set_dimension",
        "add_opening",
        "move_opening",
        "remove_opening",
        "resize_opening",
        "name_room",
    }
)
"""Formes fixes (cotes, ouvertures, nom) : lues par les phrases simples avant le modèle, qui
ne lit que ce qu'elles ne savent pas lire ; le geste est le même avec ou sans Gemini."""
NOT_UNDERSTOOD = (
    "Je n'ai pas compris. Je sais changer l'ambiance (« plus chaleureux », « lumière du soir », "
    "« parquet clair »), ajouter, déplacer ou retirer un meuble (« ajoute un bureau près de la "
    "fenêtre », « enlève le tapis ») et proposer des aménagements."
)


class SayPart(StrictModel):
    """Une partie d'un tour : ce qui a été fait, refusé ou demandé, et pourquoi."""

    kind: PartKind
    status: PartStatus
    summary: str
    reason: str = ""
    by: Reader = "phrases"
    element_ids: list[UUID] = Field(default_factory=list)


class SayTurn(StrictModel):
    """Un tour de l'assistant unifié."""

    text: str
    parts: list[SayPart]
    reader: Reader
    """« model » si une partie au moins a été lue par le modèle, sinon « phrases »."""
    model_id: str | None = None
    notice: str = ""
    """Pourquoi le modèle n'a pas servi (case décochée, accord manquant, indisponible)."""
    committed: int = 0
    """Pas d'historique de ce tour : « annuler » les défait tous."""
    room_id: UUID | None = None
    """La pièce dont l'ambiance est touchée (contexte, sinon pièce principale)."""
    highlighted: list[UUID] = Field(default_factory=list)
    assistant: AssistantTurn | None = None
    """Le tour de l'assistant d'origine (propositions d'aménagement, éléments touchés)."""


@dataclass(frozen=True)
class Readers:
    """Le modèle à utiliser (None : phrases simples) et la passerelle du projet."""

    model: LanguageModel | None
    gateway: ModelGateway | None
    notice: str = ""


def readers_for(model: LanguageModel | None, gemini_case: bool, gateway: ModelGateway) -> Readers:
    """La case « Gemini » de l'accueil vaut pour le fil du projet : un modèle distant ne lit
    la phrase que si elle est cochée ; un modèle local (s'il y en a un) toujours."""
    if model is None:
        return Readers(None, None)
    if model.remote and not gemini_case:
        return Readers(None, None, "case Gemini décochée : phrases simples")
    return Readers(model, gateway)


def target_room(level: Level, context_id: UUID | None) -> Room | None:
    """La pièce en contexte : la pièce choisie, celle où se trouve le meuble choisi, sinon la
    pièce principale."""
    element = level.elements.get(context_id) if context_id is not None else None
    if isinstance(element, Room):
        return element
    if isinstance(element, Furniture):
        at = Point(element.transform.position.x, element.transform.position.y)
        try:
            graph = WallGraph.from_level(level)
            for room in rooms_of(level):
                if graph.room_axis_polygon(room.id).contains(at):
                    return room
        except GeometryError:
            pass
    return main_room(level)


_CLAUSES = re.compile(
    # Sonar S8786 : texte borné à MAX_TEXT_CHARS dans clauses() avant le découpage ; test de durée
    # dans test_kan108_regex.
    r"\s*(?:[;!?]|(?<!\d)\.|\.(?!\d)|,\s*(?:et|puis)\b|(?<!\d),|,(?!\d)|\bpuis\b)\s*"  # NOSONAR
)
"""Une virgule ou un point entre deux chiffres est décimal (« 1,20 m ») : pas une coupure."""
MAX_TEXT_CHARS = 4000
"""KAN-108 : au-delà, le texte n'est pas lu (les routes le bornent déjà à 500 ou 2 000
caractères) ; la regex des propositions revient en arrière sur une longue suite d'espaces
(Sonar S8786) : bornée ainsi, elle reste rapide quel que soit le texte."""


def clauses(text: str) -> list[str]:
    """La phrase coupée en propositions (« lumière du soir, et enlève l'armoire » → deux) : les
    phrases simples lisent un geste par proposition, le verbe en tête."""
    return [c for c in (part.strip() for part in _CLAUSES.split(text[:MAX_TEXT_CHARS])) if c]


def understand_clauses(text: str, categories: list[str]) -> AssistantReply | None:
    """Les phrases simples, proposition par proposition ; une proposition qui n'est pas un
    geste (« plus chaleureux ») est laissée au lecteur de consignes. Un meuble nommé sans verbe
    d'ajout (« deux lits », « sans tapis ») est une envie de l'ambiance, pas un ajout."""
    intents: list[AssistantIntent] = []
    for clause in clauses(text):
        reply = understand(clause, categories)
        if reply is None:
            continue
        intents.extend(
            i
            for i in reply.intents
            if i.kind != "answer" and (i.kind != "add_furniture" or explicit_add(clause))
        )
    return AssistantReply(intents=intents) if intents else None


_DECOR = re.compile(
    r"\b(?:plantes?|fleurs?|bouquets?|cadres?|tableaux?|affiches?|posters?|coussins?|plaids?"
    r"|couvertures?|rideaux?|voilages?|livres?|bougies?|vases?|paniers?|bibelots?|objets?"
    r"|deco|decoration|decorations)\b"
)
"""Objets du décor (posés au rendu, pas des meubles du catalogue)."""
DENSER = {"sobre": "normal", "normal": "riche"}


def decor_clauses(text: str, categories: list[str]) -> list[str]:
    """Les propositions qui demandent un objet de décor (« ajoute une plante ») : il n'y a pas
    de meuble au catalogue pour lui, c'est la densité du décor de la pièce qui monte."""
    out: list[str] = []
    for clause in clauses(text):
        if not explicit_add(clause) or not _DECOR.search(fold(clause)):
            continue
        reply = understand(clause, categories)
        if reply is not None and any(i.kind == "add_furniture" for i in reply.intents):
            continue
        out.append(clause)
    return out


def unread_clauses(text: str, categories: list[str]) -> list[str]:
    """Les propositions que ni les gestes, ni le lecteur de consignes, ni le décor ne
    comprennent."""
    decor = set(decor_clauses(text, categories))
    return [
        clause
        for clause in clauses(text)
        if clause not in decor
        and understand(clause, categories) is None
        and not translate_phrases(clause, categories).fields
    ]


def _ambiance_fields(
    translation: Translation, has_furniture_actions: bool, has_opening_edits: bool = False
) -> dict[str, JsonValue]:
    fields = dict(translation.fields)
    if has_furniture_actions or has_opening_edits:
        for key in ("furniture", "avoid"):
            fields.pop(key, None)
    if has_opening_edits:
        # « la fenêtre de la chambre », « la porte du placard » : la pièce et le meuble nommés
        # désignent l'ouverture, ils ne changent ni l'usage ni les envies de la pièce.
        fields.pop("room_type", None)
    return fields


def say(  # noqa: PLR0912, PLR0915 - une phrase, deux lecteurs, un tour
    project: Project,
    level_id: UUID,
    text: str,
    categories: list[str],
    runs_dir: Path,
    *,
    readers: Readers,
    context_id: UUID | None = None,
    last_element_id: UUID | None = None,
) -> tuple[Project, SayTurn]:
    """Lit la phrase, applique l'ambiance puis les gestes ; renvoie le projet et le tour."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    text = text.strip()
    notice = readers.notice
    model_id: str | None = None
    parts: list[SayPart] = []

    # 1. Les gestes : formes fixes (cotes, ouvertures, nom) par les phrases simples d'abord,
    #    puis le modèle s'il est permis, sinon les phrases simples.
    reply: AssistantReply | None = None
    action_reader: Reader = "phrases"
    typed = understand_clauses(text, categories)
    if typed is not None and all(i.kind in FIXED_FORMS for i in typed.intents):
        reply = typed
    if reply is None and readers.model is not None and readers.gateway is not None:
        last_label = None
        if last_element_id is not None:
            last = level.elements.get(last_element_id)
            if last is not None:
                last_label = last.name or getattr(last, "category", None) or last.type
        try:
            reply, model_id = understand_with_model(
                project,
                level_id,
                text,
                categories,
                runs_dir,
                model=readers.model,
                gateway=readers.gateway,
                last_label=last_label,
            )
            action_reader = "model"
        except ConsentRequiredError:
            notice = notice or "le modèle attend l'accord du projet : phrases simples"
        except (ModelUnavailableError, ModelCallError, AssistantParseError) as exc:
            notice = notice or f"modèle indisponible ({str(exc)[:60]}) : phrases simples"
    if reply is None:
        reply = understand_clauses(text, categories)
    gestures = [
        i for i in (reply.intents if reply is not None else []) if i.kind not in ("ask", "answer")
    ]
    answers = [i for i in (reply.intents if reply is not None else []) if i.kind == "answer"]

    # 2. L'ambiance : le lecteur de consignes, sur la pièce en contexte.
    room = target_room(level, context_id)
    reading = read_instruction(
        text,
        categories,
        model=readers.model if notice == readers.notice else None,
        gateway=readers.gateway if notice == readers.notice else None,
        project=project,
    )
    if reading.notice and not notice:
        notice = reading.notice
    ambiance_reader: Reader = "model" if reading.understanding == "model" else "phrases"
    fields = _ambiance_fields(
        reading.translation,
        any(i.kind in FURNITURE_ACTIONS for i in gestures),
        any(i.kind in OPENING_EDITS for i in gestures),
    )

    current = project
    committed = 0
    if fields and room is not None:
        try:
            update = intent_fields(room.intent, fields)
            update.update({"instruction": text, "translated_by": reading.translation.by})
            # Une consigne appliquée remplace la consigne gardée de l'accueil (L1b).
            current = apply_op(
                current.model_copy(update={"pending_instruction": None}),
                level_id,
                SetIntent(room_id=room.id, fields=update),
                actor="user",
            )
            committed += 1
            summary = " · ".join(reading.translation.summary) or "ambiance mise à jour"
            parts.append(
                SayPart(
                    kind="ambiance",
                    status="done",
                    summary=f"Ambiance de {room.name or 'la pièce'} : {summary}",
                    by=ambiance_reader,
                    element_ids=[room.id],
                )
            )
        except OpError as exc:
            parts.append(
                SayPart(
                    kind="ambiance",
                    status="refused",
                    summary="Ambiance",
                    reason=str(exc),
                    by=ambiance_reader,
                )
            )
    elif fields:
        parts.append(
            SayPart(
                kind="ambiance",
                status="refused",
                summary="Ambiance",
                reason="aucune pièce fermée : tracez les murs, puis Détecter les pièces",
                by=ambiance_reader,
            )
        )

    # 2b. Le décor (« ajoute une plante ») : la densité du décor de la pièce monte d'un cran,
    #     sauf si la phrase la fixe déjà (« bohème »).
    if decor_clauses(text, categories) and "decor_density" not in fields:
        if room is None:
            parts.append(
                SayPart(
                    kind="ambiance",
                    status="refused",
                    summary="Décor",
                    reason="aucune pièce fermée : tracez les murs, puis Détecter les pièces",
                )
            )
        else:
            here = current.levels[level_id].elements.get(room.id)
            intent = here.intent if isinstance(here, Room) else room.intent
            density = intent.decor_density if intent is not None else "normal"
            denser = DENSER.get(density)
            if denser is None:
                parts.append(
                    SayPart(
                        kind="ambiance",
                        status="refused",
                        summary="Décor",
                        reason="le décor est déjà au plus fourni (plantes et objets)",
                        element_ids=[room.id],
                    )
                )
            else:
                try:
                    current = apply_op(
                        current,
                        level_id,
                        SetIntent(
                            room_id=room.id,
                            fields=intent_fields(intent, {"decor_density": denser}),
                        ),
                        actor="user",
                    )
                    committed += 1
                    parts.append(
                        SayPart(
                            kind="ambiance",
                            status="done",
                            summary="décor plus fourni : plantes et objets ajoutés "
                            "(au prochain rendu)",
                            element_ids=[room.id],
                        )
                    )
                except OpError as exc:
                    parts.append(
                        SayPart(kind="ambiance", status="refused", summary="Décor", reason=str(exc))
                    )

    # 3. Les gestes, chacun son pas d'historique ; un refus n'efface pas ce qui a réussi.
    assistant_turn: AssistantTurn | None = None
    if gestures:
        try:
            current, assistant_turn = act(
                current,
                level_id,
                text,
                AssistantReply(intents=gestures),
                runs_dir,
                model_id=model_id,
                last_element_id=last_element_id,
            )
            committed += assistant_turn.committed
            for action in assistant_turn.actions:
                parts.append(
                    SayPart(
                        kind="action",
                        status=action.status,
                        summary=action.summary,
                        reason=action.reason,
                        by=action_reader,
                        element_ids=list(action.element_ids),
                    )
                )
        except (DetectionError, OpError) as exc:
            parts.append(
                SayPart(
                    kind="action",
                    status="refused",
                    summary="Gestes sur le plan",
                    reason=str(exc)[:200],
                    by=action_reader,
                )
            )
    for answer in answers if not parts else []:
        parts.append(
            SayPart(kind="answer", status="done", summary=answer.text or "", by=action_reader)
        )
    if not parts:
        parts.append(SayPart(kind="answer", status="refused", summary=NOT_UNDERSTOOD))
    elif action_reader == "phrases" and ambiance_reader == "phrases":
        # Phrases simples : une proposition que ni les gestes ni l'ambiance n'ont lue est dite
        # (✗), jamais passée sous silence (« …, et ajoute une plante » sans plante au catalogue).
        for clause in unread_clauses(text, categories):
            parts.append(
                SayPart(
                    kind="action",
                    status="refused",
                    summary=f"« {clause} »",
                    reason="pas compris (je sais l'ambiance, les meubles du catalogue et les "
                    "aménagements)",
                )
            )
    reader: Reader = "model" if any(p.by == "model" for p in parts) else "phrases"
    highlighted = [e for p in parts if p.status == "done" for e in p.element_ids]
    return current, SayTurn(
        text=text,
        parts=parts,
        reader=reader,
        model_id=model_id or (reading.translation.by if ambiance_reader == "model" else None),
        notice=notice,
        committed=committed,
        room_id=room.id if room is not None else None,
        highlighted=highlighted,
        assistant=assistant_turn,
    )
