"""L'assistant agit sur le plan : des intentions typées → des gestes calculés par le moteur.

Le modèle (ou, sans modèle, une phrase-type) a choisi l'intention et la cible ; ici le moteur
géométrique de 3.4 place, déplace ou retire des meubles sous les règles d'aménagement. Chaque
intention réussie est un commit d'historique attribué à `assistant` (annulable) ; ce qui est
refusé ou ambigu est dit, jamais deviné. Rien ne part au modèle depuis ce module.
"""

from __future__ import annotations

import random
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LanguageModel
from openplan.adapters.remote.assistant import AssistantAgent, AssistantParseError, parse_reply
from openplan.pipeline.assistant_openings import move_opening, remove_opening, resize_opening
from openplan.pipeline.assistant_plan import add_opening, add_stairs, name_room, set_dimension
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.intention import describe_room
from openplan.pipeline.layouts import (
    STEP as LAYOUT_STEP,
)
from openplan.pipeline.layouts import (
    LayoutBody,
    LayoutProposal,
    LayoutRun,
    _best_position,
    _furniture,
    _placed_for,
    _with_furniture,
    build_context,
    expand_brief,
    generate_layouts,
    want_for,
)
from openplan.scene import history
from openplan.schemas.assistant import (
    ASSISTANT_ACTOR,
    AssistantAction,
    AssistantIntent,
    AssistantReply,
)
from openplan.schemas.common import Provenance, StrictModel
from openplan.schemas.elements import Furniture
from openplan.schemas.layout import PLACE_OF, BriefItem, LayoutBrief
from openplan.schemas.scene import Project

STEP = "assistant.act"
MAX_PROPOSALS = 3


class AssistantTurn(StrictModel):
    """Un tour de conversation : la phrase, les actions, l'état du plan après."""

    run_id: UUID
    text: str
    message: str
    """Résumé pour l'utilisateur : ce qui a été fait, ce qui est demandé, ce qui est refusé."""
    actions: list[AssistantAction] = Field(default_factory=list)
    highlighted: list[UUID] = Field(default_factory=list)
    committed: int = 0
    """Nombre de commits d'historique de ce tour (autant d'« Annuler »)."""
    proposals: list[LayoutProposal] = Field(default_factory=list)
    """Dispositions proposées (`propose_layouts`) : rien dans la scène, à appliquer comme dans
    Aménager."""
    layouts: LayoutRun | None = None
    """Le run complet des dispositions, pour la fiche Aménager."""
    dropped: list[str] = Field(default_factory=list)
    model_id: str | None = None


def _fold(text: str) -> str:
    """Minuscules sans accents, pour comparer « Bureau » et « bureau »."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


@dataclass
class _Resolved:
    furniture: list[Furniture]
    """Un seul : cible trouvée ; plusieurs : ambigu ; aucun : introuvable."""
    label: str


def resolve_target(project: Project, level_id: UUID, target: str | None) -> _Resolved:
    """Le meuble que l'utilisateur nomme : par nom, par libellé du catalogue, par catégorie
    (« le lit » → `bed_double`), par mot contenu. Plusieurs candidats = question."""
    from openplan.scene.catalog import load_catalog

    level = project.levels[level_id]
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture) and e.visible]
    if not target:
        return _Resolved(furniture if len(furniture) == 1 else [], "le meuble")
    wanted = _fold(target).replace("_", " ").replace("-", " ")
    words = [w for w in wanted.replace("'", " ").split() if len(w) > 2 and w not in _STOP]
    catalog = load_catalog()
    labels = {a.id: _fold(a.label) for a in catalog.assets}
    categories = {a.id: a.category for a in catalog.assets}

    def matches(f: Furniture) -> bool:
        haystack = " ".join(
            filter(
                None,
                [
                    _fold(f.name or ""),
                    labels.get(f.asset_id, ""),
                    _fold(f.category),
                    _fold(categories.get(f.asset_id, "")),
                ],
            )
        )
        return bool(words) and all(w in haystack for w in words)

    exact = [f for f in furniture if _fold(f.name or "") == wanted]
    if len(exact) == 1:
        return _Resolved(exact, target)
    found = [f for f in furniture if matches(f)]
    if not found:
        # Un seul mot suffit (« le bureau blanc » quand il n'y a qu'un bureau).
        found = [
            f
            for f in furniture
            if any(
                w in " ".join([_fold(f.name or ""), labels.get(f.asset_id, ""), _fold(f.category)])
                for w in words
            )
        ]
    if len(found) > 1:
        # « de gauche », « du fond » : le plan n'a pas de gauche, mais la vue de dessus en a
        # une (x croissant vers la droite, y vers le haut).
        side = _side(wanted)
        if side is not None:
            key, pick = side
            found = [pick(found, key=key)]
    return _Resolved(found, target)


def _side(text: str):  # type: ignore[no-untyped-def]
    x = lambda f: f.transform.position.x  # noqa: E731
    y = lambda f: f.transform.position.y  # noqa: E731
    for words, key, pick in (
        (("gauche",), x, min),
        (("droite",), x, max),
        (("fond", "haut", "loin"), y, max),
        (("devant", "bas", "proche"), y, min),
    ):
        if any(w in text for w in words):
            return key, pick
    return None


_STOP = frozenset(
    {
        "les",
        "des",
        "une",
        "mon",
        "mes",
        "ton",
        "tes",
        "son",
        "ses",
        "notre",
        "votre",
        "leur",
        "ce",
        "cet",
        "cette",
        "ces",
        "meuble",
        "meubles",
        "que",
        "qui",
        "dans",
        "sur",
        "pres",
        "loin",
    }
)


def _label(f: Furniture) -> str:
    return f.name or f.category


_FEMININE = (
    "table",
    "chaise",
    "armoire",
    "commode",
    "bibliotheque",
    "douche",
    "baignoire",
    "cuisiniere",
    "meridienne",
    "lampe",
)
"""Premiers mots des libellés féminins du catalogue (accord de « posé »)."""


def _posed(label: str, count: int) -> str:
    """« Évier posé », « Douche posée », « 2 × Chaise posées », « Lits superposés posés »."""
    head = _fold(label).split(" ")[0]
    plural = count > 1 or head == "lits"
    return "posé" + ("e" if head in _FEMININE else "") + ("s" if plural else "")


def _provenance(rid: UUID) -> Provenance:
    return Provenance(step=STEP, run_id=rid)


def _commit(project: Project, after: Project, label: str, rid: UUID) -> Project:
    return history.commit(project, after, actor=ASSISTANT_ACTOR, label=label, run_id=rid)


def _add(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    ctx = build_context(project, level_id, None)
    item = BriefItem(
        category=intent.category or "",
        count=intent.count,
        dimensions_m=intent.dimensions_m,
        near=intent.near,
        away_from=intent.away_from,
    )
    wants, unplaced = expand_brief(LayoutBrief(items=[item]), ctx)
    if unplaced or not wants:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Ajouter {unplaced[0].label if unplaced else intent.category} : impossible",
            reason=(unplaced[0].reason if unplaced else "catégorie inconnue")
            + " ; posez-le à la main avec l'outil Meuble de la palette",
        )
    existing = [e for e in ctx.level.elements.values() if isinstance(e, Furniture) and e.visible]
    fixed = [_placed_for(f, want_for(f, None, ctx)) for f in existing]
    rng = random.Random(0)
    added: list[Furniture] = []
    notes: list[str] = []
    for want in wants:
        best = _best_position(want, ctx, fixed + [_placed_for(f, want) for f in added], rng, {})
        if best is None:
            notes.append(f"pas de place pour {want.label}")
            continue
        placed = _furniture(best, _provenance(rid))
        added.append(placed)
        for place in want.item.near:
            if not ctx.landmarks.near(place, best.polygon):
                notes.append(f"{want.label} n'a pas pu être mis près {PLACE_OF[place]}")
        for place in want.item.away_from:
            if not ctx.landmarks.far(place, best.polygon):
                notes.append(f"{want.label} reste près {PLACE_OF[place]}")
    if not added:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Ajouter {wants[0].label} : pas de place",
            reason="aucune position sans collision, hors des portes et sous la hauteur libre ; "
            "libérez de la place ou posez-le à la main avec l'outil Meuble",
        )
    after = _with_furniture(project, ctx.level, added)
    label = wants[0].label if len(added) == 1 else f"{len(added)} × {wants[0].label}"
    where = ""
    if intent.near:
        where = " près " + " et ".join(PLACE_OF[p] for p in intent.near)
    summary = f"{label} {_posed(wants[0].label, len(added))}{where}".strip()
    if notes:
        summary += " (" + " ; ".join(notes) + ")"
    return (
        _commit(project, after, f"Assistant : ajouter {label}", rid),
        AssistantAction(
            kind=intent.kind, status="done", summary=summary, element_ids=[f.id for f in added]
        ),
    )


def _move(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    resolved = resolve_target(project, level_id, intent.target)
    if len(resolved.furniture) != 1:
        return project, _ambiguous(intent, resolved)
    target = resolved.furniture[0]
    if not intent.near and not intent.away_from:
        return project, AssistantAction(
            kind=intent.kind,
            status="question",
            summary=f"Déplacer {_label(target)}",
            reason=f"Où mettre {_label(target)} ? (près d'un mur, d'une fenêtre, loin de la "
            "porte…)",
        )
    ctx = build_context(project, level_id, None)
    want = want_for(target, None, ctx)
    want.item = want.item.model_copy(update={"near": intent.near, "away_from": intent.away_from})
    others = [
        e
        for e in ctx.level.elements.values()
        if isinstance(e, Furniture) and e.visible and e.id != target.id
    ]
    fixed = [_placed_for(f, want_for(f, None, ctx)) for f in others]
    best = _best_position(want, ctx, fixed, random.Random(0), {})
    ok = (
        best is not None
        and all(ctx.landmarks.near(p, best.polygon) for p in intent.near)
        and all(ctx.landmarks.far(p, best.polygon) for p in intent.away_from)
    )
    wish = " ".join(
        [
            *(f"près {PLACE_OF[p]}" for p in intent.near),
            *(f"loin {PLACE_OF[p]}" for p in intent.away_from),
        ]
    )
    if best is None or not ok:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Déplacer {_label(target)} {wish}",
            reason=f"aucune position de {_label(target)} {wish} sans collision, hors des portes et "
            "sous la hauteur libre : il reste où il est ; glissez-le à la main dans le plan",
            element_ids=[target.id],
        )
    moved = _furniture(best, target.provenance).model_copy(
        update={
            "id": target.id,
            "name": target.name,
            "source": target.source,
            "confidence": target.confidence,
            "preserve": target.preserve,
        }
    )
    after = _with_furniture(project, ctx.level, [moved])
    return (
        _commit(project, after, f"Assistant : déplacer {_label(target)} {wish}", rid),
        AssistantAction(
            kind=intent.kind,
            status="done",
            summary=f"{_label(target)} déplacé {wish}",
            element_ids=[target.id],
        ),
    )


def _remove(
    project: Project, level_id: UUID, intent: AssistantIntent, rid: UUID
) -> tuple[Project, AssistantAction]:
    resolved = resolve_target(project, level_id, intent.target)
    if len(resolved.furniture) != 1:
        return project, _ambiguous(intent, resolved)
    target = resolved.furniture[0]
    level = project.levels[level_id]
    elements = {k: v for k, v in level.elements.items() if k != target.id}
    after = project.model_copy(
        update={
            "levels": {**project.levels, level_id: level.model_copy(update={"elements": elements})}
        }
    )
    try:
        committed = _commit(project, after, f"Assistant : retirer {_label(target)}", rid)
    except history.PatchRejectedError:
        return project, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary=f"Retirer {_label(target)}",
            reason=f"{_label(target)} a été posé par vous : l'assistant ne le retire pas. "
            "Sélectionnez-le et appuyez sur Suppr.",
            element_ids=[target.id],
        )
    return committed, AssistantAction(
        kind=intent.kind, status="done", summary=f"{_label(target)} retiré", element_ids=[target.id]
    )


PRONOUNS = frozenset(
    {"le", "la", "l", "les", "lui", "celui-ci", "celle-ci", "ca", "ce meuble", "-le", "-la"}
)


def _with_memory(
    project: Project, level_id: UUID, intent: AssistantIntent, last: UUID | None
) -> AssistantIntent:
    """« Déplace-le » : sans cible nommée (ou un simple pronom), le dernier meuble cité."""
    if last is None:
        return intent
    target = _fold(intent.target or "").strip(" .!")
    if target and target not in PRONOUNS:
        return intent
    element = project.levels[level_id].elements.get(last)
    if not isinstance(element, Furniture):
        return intent
    return intent.model_copy(update={"target": element.name or element.category})


def _ambiguous(intent: AssistantIntent, resolved: _Resolved) -> AssistantAction:
    verb = "Déplacer" if intent.kind == "move_furniture" else "Retirer"
    if not resolved.furniture:
        reason = (
            f"je ne trouve pas « {resolved.label} » dans le plan ; pour l'ajouter, dites "
            "« ajoute … » ou utilisez l'outil Meuble"
        )
        return AssistantAction(
            kind=intent.kind, status="refused", summary=f"{verb} {resolved.label}", reason=reason
        )
    ordered = sorted(resolved.furniture, key=lambda f: f.transform.position.x)
    if len(ordered) == 2:
        names = f"{_label(ordered[0])} (à gauche), {_label(ordered[1])} (à droite)"
    else:
        names = ", ".join(f"{_label(f)} (x {f.transform.position.x:.1f} m)" for f in ordered)
    return AssistantAction(
        kind=intent.kind,
        status="question",
        summary=f"{verb} {resolved.label}",
        reason=f"Lequel : {names} ?",
        element_ids=[f.id for f in resolved.furniture],
    )


def _propose(
    project: Project,
    level_id: UUID,
    intent: AssistantIntent,
    runs_dir: Path,
    rid: UUID,
    all_intents: list[AssistantIntent] | None = None,
) -> tuple[LayoutRun | None, AssistantAction]:
    level = project.levels[level_id]
    items: list[BriefItem] = []
    for one in all_intents or [intent]:
        if one.category:
            items.append(
                BriefItem(
                    category=one.category,
                    count=one.count,
                    dimensions_m=one.dimensions_m,
                    near=one.near,
                    away_from=one.away_from,
                )
            )
    if not items:
        counts: dict[str, int] = {}
        for e in level.elements.values():
            if isinstance(e, Furniture) and e.visible and not e.preserve:
                counts[e.category] = counts.get(e.category, 0) + 1
        items.extend(BriefItem(category=c, count=n) for c, n in counts.items())
    if not items:
        return None, AssistantAction(
            kind=intent.kind,
            status="question",
            summary="Proposer des dispositions",
            reason="Quels meubles voulez-vous dans la pièce ? (par exemple : un lit 160, un "
            "bureau, une armoire)",
        )
    keep = [e.id for e in level.elements.values() if isinstance(e, Furniture) and e.preserve]
    brief = LayoutBrief(items=items, keep=keep)
    run = generate_layouts(
        project, level_id, LayoutBody(brief=brief, variants=MAX_PROPOSALS, seed=1), run_id=rid
    )
    if not run.proposals:
        return None, AssistantAction(
            kind=intent.kind,
            status="refused",
            summary="Proposer des dispositions",
            reason="; ".join(run.result.warnings) or "aucune disposition valide",
        )
    return run, AssistantAction(
        kind=intent.kind,
        status="done",
        summary=f"{len(run.proposals)} disposition(s) proposée(s), à relire dans Aménager ; "
        "rien n'est posé",
    )


def act(  # noqa: PLR0912, PLR0915
    project: Project,
    level_id: UUID,
    text: str,
    reply: AssistantReply,
    runs_dir: Path,
    *,
    run_id: UUID | None = None,
    model_id: str | None = None,
    last_element_id: UUID | None = None,
) -> tuple[Project, AssistantTurn]:
    """Exécute les intentions dans l'ordre ; s'arrête à la première question. Renvoie le projet
    (autant de commits que d'actions faites) et le tour à afficher. `last_element_id` : le
    dernier meuble dont on a parlé (« déplace-le », « enlève-le »)."""
    rid = run_id or uuid4()
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    started = time.perf_counter()
    actions: list[AssistantAction] = []
    layouts: LayoutRun | None = None
    current = project
    committed = 0
    proposes = [i for i in reply.intents if i.kind == "propose_layouts"]
    intents = [i for i in reply.intents if i.kind != "propose_layouts"]
    if proposes:
        intents.append(proposes[0].model_copy(update={"text": ""}))
    for intent in intents:
        before = current
        if intent.kind in ("move_furniture", "remove_furniture"):
            intent = _with_memory(current, level_id, intent, last_element_id)  # noqa: PLW2901
        if intent.kind == "add_furniture":
            current, action = _add(current, level_id, intent, rid)
        elif intent.kind == "move_furniture":
            current, action = _move(current, level_id, intent, rid)
        elif intent.kind == "remove_furniture":
            current, action = _remove(current, level_id, intent, rid)
        elif intent.kind == "set_dimension":
            current, action = set_dimension(current, level_id, intent, rid)
        elif intent.kind == "add_opening":
            current, action = add_opening(current, level_id, intent, rid)
        elif intent.kind == "move_opening":
            current, action = move_opening(current, level_id, intent, rid)
        elif intent.kind == "remove_opening":
            current, action = remove_opening(current, level_id, intent, rid)
        elif intent.kind == "resize_opening":
            current, action = resize_opening(current, level_id, intent, rid)
        elif intent.kind == "add_stairs":
            current, action = add_stairs(current, level_id, intent, rid)
        elif intent.kind == "name_room":
            current, action = name_room(current, level_id, intent, rid)
        elif intent.kind == "propose_layouts":
            layouts, action = _propose(current, level_id, intent, runs_dir, rid, proposes)
        elif intent.kind == "ask":
            action = AssistantAction(
                kind="ask", status="question", summary="Question", reason=intent.text
            )
        else:
            action = AssistantAction(
                kind="answer", status="done", summary=intent.text or reply.message
            )
        if current is not before:
            committed += 1
        actions.append(action)
        if action.status == "question":
            break
    parts = [a.summary for a in actions if a.status == "done" and a.kind != "answer"]
    questions = [a.reason for a in actions if a.status == "question"]
    refused = [f"{a.summary} : {a.reason}" for a in actions if a.status == "refused"]
    answers = [a.summary for a in actions if a.kind == "answer"]
    message = ". ".join(filter(None, [*answers, *parts]))
    if refused:
        message = (message + ". " if message else "") + "Non fait : " + " ; ".join(refused)
    if questions:
        message = (message + " " if message else "") + questions[0]
    if not message:
        message = reply.message or "Je n'ai rien compris à faire dans le plan."
    del started
    return current, AssistantTurn(
        run_id=rid,
        text=text,
        message=message.strip(),
        actions=actions,
        highlighted=[i for a in actions if a.status == "done" for i in a.element_ids],
        committed=committed,
        proposals=layouts.proposals if layouts else [],
        layouts=layouts,
        dropped=reply.dropped,
        model_id=model_id,
    )


def understand_with_model(
    project: Project,
    level_id: UUID,
    text: str,
    categories: list[str],
    runs_dir: Path,
    *,
    model: LanguageModel,
    gateway: ModelGateway,
    run_id: UUID | None = None,
    last_label: str | None = None,
) -> tuple[AssistantReply, str]:
    """La phrase → intentions par le modèle (texte seul : la phrase et la description du plan,
    jamais la photo) ; un réessai si la réponse n'est pas du JSON. Renvoie aussi l'identifiant
    du modèle. Lève `AssistantParseError` après deux réponses illisibles."""
    rid = run_id or uuid4()
    from openplan.scene.catalog import load_catalog

    room = describe_room(project, level_id)
    labels = {a.category: a.label for a in reversed(load_catalog().assets)}
    agent = AssistantAgent(model, gateway.bind(model, project), labels=labels)
    out_dir = runs_dir / str(rid) / "assistant"
    out_dir.mkdir(parents=True, exist_ok=True)
    response = agent.ask(text, room, categories, last_label=last_label)
    (out_dir / "assistant_raw.txt").write_text(response.text, encoding="utf-8")
    aliases = category_aliases(categories)
    try:
        reply = parse_reply(response.text, categories, text, aliases)
    except AssistantParseError:
        response = agent.ask(
            text, room, categories, retry_hint=response.text, last_label=last_label
        )
        (out_dir / "assistant_raw_retry.txt").write_text(response.text, encoding="utf-8")
        reply = parse_reply(response.text, categories, text, aliases)
    return with_written_places(reply, text, categories), agent.model_id


def category_aliases(categories: list[str]) -> dict[str, str]:
    """Mots français du catalogue → catégorie (« wc » → toilet), pour une réponse du modèle qui
    nomme le meuble au lieu de sa catégorie."""
    from openplan.pipeline.assistant_phrases import SYNONYMS, fold

    out: dict[str, str] = {}
    for category, words in SYNONYMS.items():
        if category in categories:
            for word in words:
                out.setdefault(word, category)
                out.setdefault(fold(word), category)
    return out


def with_written_places(reply: AssistantReply, text: str, categories: list[str]) -> AssistantReply:
    """Le petit modèle oublie parfois « sous la fenêtre » : les repères que l'utilisateur a
    écrits (lus par les phrases-types) complètent une intention du même meuble restée sans
    repère. Rien n'est inventé : ce sont les mots de la phrase."""
    from openplan.pipeline.assistant_phrases import understand

    local = understand(text, categories)
    if local is None:
        return reply
    written = {(i.kind, i.category): i for i in local.intents if i.near or i.away_from}
    intents = []
    for intent in reply.intents:
        same = written.get((intent.kind, intent.category))
        if same is not None and not intent.near and not intent.away_from:
            intents.append(
                intent.model_copy(update={"near": same.near, "away_from": same.away_from})
            )
        else:
            intents.append(intent)
    return reply.model_copy(update={"intents": intents})


__all__ = ["LAYOUT_STEP", "STEP", "AssistantTurn", "act", "resolve_target", "understand_with_model"]
