"""Étape « layout.critique » (roadmap 3.5) : une variante d'aménagement → remarques.

Deux sources, toujours dans cet ordre : **les règles** (géométrie de `validation/layout.py`
et souhaits du brief non tenus, calculés ici sans modèle : gratuit, déterministe, disponible
sans configuration) puis, si un modèle est configuré et autorisé, **le modèle** (texte seul :
description de la pièce, brief, meubles avec leurs distances aux repères, remarques déjà
connues ; jamais la photo). Chaque remarque cite un meuble et une règle, est triée par
importance et porte, quand c'est possible, une action en un clic (`CritiqueAction`) qui
recalcule une variante révisée à relire (`layouts.revise_proposal`). Rien n'est appliqué ici :
pas de patch de scène, source `generated`, confiance plafonnée. Artefacts dans
<projet>/runs/<run_id>/layout/ (critique_raw.txt, critique.json).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from uuid import UUID, uuid4

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LanguageModel
from openplan.adapters.remote.critique import (
    CritiqueAgent,
    CritiqueParseError,
    FurnitureRef,
    parse_critique,
)
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.intention import describe_room
from openplan.pipeline.layouts import Context, LayoutProposal, assess, build_context
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Furniture
from openplan.schemas.layout import (
    MAX_BRIEF_CONFIDENCE,
    PLACE_OF,
    RULE_LABELS,
    USAGE_RULES,
    Critique,
    CritiqueAction,
    LayoutBrief,
    Place,
    Remark,
    action_label,
    sort_remarks,
)
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "layout.critique"
_DISTANCE_PLACES: tuple[Place, ...] = ("window", "door", "radiator", "wall")
_DISTANCE_LABELS: dict[str, str] = {
    "window": "de la fenêtre",
    "door": "de la porte",
    "radiator": "du radiateur",
    "wall": "du mur le plus proche",
}


class CritiqueBody(StrictModel):
    proposal: LayoutProposal
    brief: LayoutBrief | None = None
    """Brief d'origine : ses souhaits non tenus font des remarques avec action."""
    room_id: UUID | None = None
    use_model: bool = True
    """False : règles seules, même si un modèle est configuré (aucun envoi)."""


class CritiquePreview(StrictModel):
    """Ce qui partirait au modèle, mot pour mot, et vers où."""

    text: str
    model_id: str | None = None
    destination: str | None = None
    remote: bool | None = None
    characters: int = 0


class CritiqueRun(StrictModel):
    run_id: UUID
    level_id: UUID
    proposal_index: int
    proposal_seed: int
    critique: Critique
    rules_count: int = 0
    model_count: int = 0
    preview: CritiquePreview
    result: StepResult


def furniture_labels(furniture: list[Furniture]) -> list[FurnitureRef]:
    """Un libellé lisible et unique par meuble : « Bureau 140 × 70 » ; deux exemplaires se
    distinguent par leur place vue de dessus (« Table de chevet (à gauche) / (à droite) »,
    « (en bas) / (en haut) ») ; au-delà, « (n° 1) », « (n° 2) »… de gauche à droite."""
    base = [f.name or f.category for f in furniture]
    groups: dict[str, list[Furniture]] = {}
    for f, name in zip(furniture, base, strict=True):
        groups.setdefault(name, []).append(f)
    suffix: dict[object, str] = {}
    for same in groups.values():
        if len(same) < 2:
            continue
        if len(same) == 2:
            one, two = same
            dx = two.transform.position.x - one.transform.position.x
            dy = two.transform.position.y - one.transform.position.y
            if abs(dx) >= abs(dy) and abs(dx) > 0.05:
                left, right = (one, two) if dx > 0 else (two, one)
                suffix[left.id], suffix[right.id] = "(à gauche)", "(à droite)"
                continue
            if abs(dy) > 0.05:
                low, high = (one, two) if dy > 0 else (two, one)
                suffix[low.id], suffix[high.id] = "(en bas)", "(en haut)"
                continue
        ordered = sorted(same, key=lambda f: (f.transform.position.x, f.transform.position.y))
        for k, f in enumerate(ordered, start=1):
            suffix[f.id] = f"(n° {k})"
    return [
        (f.id, f"{b} {suffix[f.id]}" if f.id in suffix else b)
        for f, b in zip(furniture, base, strict=True)
    ]


def _action_for_violation(
    rule: str, furniture: Furniture, label: str, brief: LayoutBrief | None
) -> CritiqueAction | None:
    target: Place | None = None
    if rule == "window_blocked":
        target = "window"
    elif rule == "door_swing_blocked":
        target = "door_swing"
    elif rule == "velux_casing":
        target = "skylight"  # KAN-67 : « Déplacer la commode loin d'un velux »
    elif rule == "element_too_tall_under_slope":
        return CritiqueAction(
            kind="move_near",
            furniture_id=furniture.id,
            target="ridge",
            label=action_label("move_near", "ridge", label),
        )
    if target is not None:
        return CritiqueAction(
            kind="move_away",
            furniture_id=furniture.id,
            target=target,
            label=action_label("move_away", target, label),
        )
    items = brief.items if brief is not None else []
    optional = any(i.category == furniture.category and i.priority == "nice_to_have" for i in items)
    if optional:
        return CritiqueAction(
            kind="remove", furniture_id=furniture.id, label=action_label("remove", None, label)
        )
    return None


def rule_remarks(
    project: Project, level_id: UUID, body: CritiqueBody, *, ctx: Context | None = None
) -> list[Remark]:
    """Remarques calculées sans modèle : violations (dures et souples) qui touchent un meuble
    de la variante, souhaits du brief non tenus ; chacune cite le meuble et la règle."""
    context = ctx or build_context(project, level_id, body.room_id)
    furniture = body.proposal.furniture
    labels = dict(furniture_labels(furniture))
    by_id = {f.id: f for f in furniture}
    judged = assess(project, context, furniture, body.brief)
    remarks: list[Remark] = []
    for violation in judged.violations:
        target = next((by_id[i] for i in violation.element_ids if i in by_id), None)
        if target is None:
            continue
        importance = "high" if violation.severity == "high" else "medium"
        if violation.severity == "low":
            importance = "low"
        rule = violation.type if violation.type in RULE_LABELS else "other"
        remarks.append(
            Remark(
                importance=importance,  # type: ignore[arg-type]
                rule=rule,
                furniture_id=target.id,
                furniture_label=labels[target.id],
                message=violation.message,
                action=_action_for_violation(rule, target, labels[target.id], body.brief),
                origin="rules",
            )
        )
    for target, kind, place in judged.misses:
        label = labels[target.id]
        if kind == "near":
            remarks.append(
                Remark(
                    importance="low",
                    rule="preference_near",
                    furniture_id=target.id,
                    furniture_label=label,
                    message=f"{label} n'est pas près {PLACE_OF[place]}, comme le brief le "
                    "demandait.",
                    action=CritiqueAction(
                        kind="move_near",
                        furniture_id=target.id,
                        target=place,
                        label=action_label("move_near", place, label),
                    ),
                )
            )
        else:
            remarks.append(
                Remark(
                    importance="medium",
                    rule="preference_away",
                    furniture_id=target.id,
                    furniture_label=label,
                    message=f"{label} est trop près {PLACE_OF[place]} alors que le brief "
                    "demandait de l'en éloigner.",
                    action=CritiqueAction(
                        kind="move_away",
                        furniture_id=target.id,
                        target=place,
                        label=action_label("move_away", place, label),
                    ),
                )
            )
    return sort_remarks(remarks)


def _describe_brief(brief: LayoutBrief | None) -> str:
    if brief is None or not brief.items:
        return "non précisé"
    parts = []
    for item in brief.items:
        text = f"{item.category} × {item.count}"
        if item.priority == "nice_to_have":
            text += " (si possible)"
        if item.near:
            text += " près " + ", ".join(PLACE_OF[p] for p in item.near)
        if item.away_from:
            text += " loin " + ", ".join(PLACE_OF[p] for p in item.away_from)
        parts.append(text)
    text = " ; ".join(parts)
    if brief.avoid:
        text += ". À éviter : " + " ; ".join(brief.avoid)
    return text


def describe_furniture(ctx: Context, furniture: list[Furniture], brief: LayoutBrief | None) -> str:
    """Une ligne numérotée par meuble : nom, dimensions, centre, distances aux repères."""
    judged = assess(ctx.project, ctx, furniture, brief)
    lines = []
    for index, (f, label) in enumerate(zip(furniture, furniture_labels(furniture), strict=True)):
        placed = judged.placed[index]
        distances = []
        for place in _DISTANCE_PLACES:
            d = ctx.landmarks.distance(place, placed.polygon)
            if math.isfinite(d):
                distances.append(f"{metres(d)} {_DISTANCE_LABELS[place]}")
        lines.append(
            f"{index + 1}. {label[1]} ({f.category}) {decimal(f.bbox.width)} × "
            f"{decimal(f.bbox.depth)} × {metres(f.bbox.height)}, "
            f"centre ({decimal(f.transform.position.x)} ; "
            f"{decimal(f.transform.position.y)}), à " + ", ".join(distances or ["repères inconnus"])
        )
    return "\n".join(lines) or "aucun meuble"


def preview_critique(
    project: Project, level_id: UUID, body: CritiqueBody, model: LanguageModel | None
) -> CritiquePreview:
    """Ce qui partirait au modèle, vers où, sans rien envoyer."""
    ctx = build_context(project, level_id, body.room_id)
    room = describe_room(project, level_id, ctx.room.id if ctx.room else None)
    known = "\n".join(
        f"- {r.furniture_label} : {r.rule_label} — {r.message}"
        for r in rule_remarks(project, level_id, body, ctx=ctx)
    )
    prompt = CritiqueAgent.user_prompt(
        room,
        _describe_brief(body.brief),
        describe_furniture(ctx, body.proposal.furniture, body.brief),
        known,
    )
    return CritiquePreview(
        text=prompt,
        model_id=model.model_id if model else None,
        destination=model.destination if model else None,
        remote=model.remote if model else None,
        characters=len(prompt),
    )


def _filter_model_remarks(
    remarks: list[Remark], known: set[tuple[UUID | None, str]]
) -> tuple[list[Remark], list[str]]:
    """Le modèle n'est écouté que sur l'usage : une remarque qui répète une règle calculée est
    fondue (déjà dite), une qui affirme une règle géométrique que le calcul ne confirme pas est
    écartée et dite (le 3B local inventait des collisions)."""
    fresh: list[Remark] = []
    dropped: list[str] = []
    for remark in remarks:
        key = (remark.furniture_id, remark.rule)
        if key in known:
            continue
        if remark.rule not in USAGE_RULES:
            dropped.append(
                f"{remark.furniture_label} : « {remark.rule_label} » affirmé par le modèle mais "
                "contredit par la géométrie calculée, écarté"
            )
            continue
        # Un avis d'usage non vérifiable ne passe jamais devant une règle calculée.
        fresh.append(
            remark.model_copy(update={"importance": "medium"})
            if remark.importance == "high"
            else remark
        )
    return fresh, dropped


def _ask_model(
    agent: CritiqueAgent, prompt: str, refs: list[FurnitureRef], raw_path: Path
) -> tuple[Critique, bool]:
    response = agent.ask(prompt)
    raw_path.write_text(response.text, encoding="utf-8")
    try:
        return parse_critique(response.text, refs), False
    except CritiqueParseError:
        response = agent.ask(prompt, retry_hint=response.text)
        raw_path.write_text(
            raw_path.read_text(encoding="utf-8") + "\n\n--- second essai ---\n" + response.text,
            encoding="utf-8",
        )
        try:
            return parse_critique(response.text, refs), True
        except CritiqueParseError as exc:
            msg = f"{exc} après un second essai (réponse brute : {raw_path})"
            raise CritiqueParseError(msg) from exc


def run_critique(
    project: Project,
    level_id: UUID,
    body: CritiqueBody,
    runs_dir: Path,
    *,
    model: LanguageModel | None = None,
    gateway: ModelGateway | None = None,
    run_id: UUID | None = None,
) -> CritiqueRun:
    """Règles d'abord (toujours), modèle ensuite (si configuré, autorisé et demandé) ; les
    remarques du modèle qui répètent une règle déjà dite sont fondues ; tri par importance ;
    artefacts ; aucun patch de scène."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    if not body.proposal.furniture:
        msg = "cette variante ne contient aucun meuble"
        raise DetectionError(msg)
    rules = rule_remarks(project, level_id, body)
    preview = preview_critique(project, level_id, body, model)
    out_dir = runs_dir / str(rid) / "layout"
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    artifacts: list[Artifact] = []
    model_ids: list[str] = []
    critique = Critique(remarks=rules)
    model_count = 0
    if body.use_model and model is not None and gateway is not None:
        agent = CritiqueAgent(model, gateway.bind(model, project))
        raw_path = out_dir / "critique_raw.txt"
        refs = furniture_labels(body.proposal.furniture)
        parsed, retried = _ask_model(agent, preview.text, refs, raw_path)
        known = {(r.furniture_id, r.rule) for r in rules}
        fresh, dropped = _filter_model_remarks(parsed.remarks, known)
        model_count = len(fresh)
        critique = Critique(
            verdict=parsed.verdict,
            remarks=sort_remarks([*rules, *fresh]),
            strengths=parsed.strengths,
            dropped=[*parsed.dropped, *dropped],
        )
        model_ids = [agent.model_id]
        warnings.extend([*parsed.dropped, *dropped])
        if retried:
            warnings.append("Première réponse du modèle sans JSON : second essai réussi.")
        artifacts.append(
            Artifact(
                name=raw_path.name,
                path=raw_path,
                media_type="text/plain",
                description="Réponse brute du modèle",
            )
        )
    else:
        reason = (
            "aucun modèle configuré"
            if model is None
            else "modèle non sollicité"
            if not body.use_model
            else "passerelle absente"
        )
        warnings.append(f"Analyse par les règles seules ({reason}).")
    if not critique.remarks:
        warnings.append("Aucune remarque : la variante respecte les règles connues et le brief.")
    path = out_dir / "critique.json"
    path.write_text(critique.model_dump_json(indent=2) + "\n", encoding="utf-8")
    artifacts.append(
        Artifact(
            name=path.name,
            path=path,
            media_type="application/json",
            description="Critique validée",
        )
    )
    return CritiqueRun(
        run_id=rid,
        level_id=level_id,
        proposal_index=body.proposal.index,
        proposal_seed=body.proposal.seed,
        critique=critique,
        rules_count=len(rules),
        model_count=model_count,
        preview=preview,
        result=StepResult(
            step=STEP,
            confidence=MAX_BRIEF_CONFIDENCE,
            assumptions=[
                Assumption(
                    text="Remarques calculées par les règles géométriques et, le cas échéant, "
                    "proposées par un modèle de langage à partir du texte de la variante : "
                    "aucune n'est appliquée ; chaque action recalcule une variante à relire.",
                    impact="low",
                )
            ],
            warnings=warnings,
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=model_ids,
        ),
    )
