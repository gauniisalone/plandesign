"""Étape « intention.brief » (roadmap 3.3) : le souhait de l'utilisateur → `LayoutBrief`.

Ce qui part au modèle est **du texte seulement** : le souhait tapé par l'utilisateur et une
description de la pièce tirée du plan (`describe_room`). Jamais la photo, jamais le fichier
projet. L'aperçu (`preview_intention`) rend ce texte exact et la destination (local /
distant) **avant** tout envoi, pour que l'interface puisse l'afficher. Aucun patch de
scène : le brief est une proposition `generated` à relire ; le placement viendra du moteur
géométrique (3.4). Les artefacts vont dans <projet>/runs/<run_id>/intention/.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LanguageModel
from openplan.adapters.remote.intention import IntentionAgent, IntentionParseError, parse_brief
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.floorplan import DetectionError
from openplan.scene.catalog import load_catalog
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.elements import (
    OPENING_LABELS,
    Furniture,
    Opening,
    OpeningKind,
    Radiator,
    Room,
    Wall,
)
from openplan.schemas.layout import MAX_BRIEF_CONFIDENCE, LayoutBrief
from openplan.schemas.numbers import area, decimal, metres
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Level, Project

STEP = "intention.brief"
SOURCE_LABELS: dict[Source, str] = {
    Source.MEASURED: "mesurée",
    Source.USER_DEFINED: "saisie",
    Source.OBSERVED: "observée",
    Source.ESTIMATED: "estimée",
    Source.INFERRED: "supposée",
    Source.GENERATED: "générée",
}


class IntentionBody(StrictModel):
    text: str = Field(min_length=3, max_length=2000)
    """Le souhait, en français courant."""
    room_id: UUID | None = None
    """Pièce concernée ; None = la plus grande pièce du niveau (ou le niveau entier)."""
    keep: list[UUID] | None = None
    """Éléments à ne pas déplacer ; None = les meubles cochés « à conserver »."""


class IntentionPreview(StrictModel):
    """Ce qui partirait au modèle, mot pour mot, et vers où : affiché avant tout envoi."""

    text: str
    room_description: str
    categories: list[str]
    keep: list[UUID] = Field(default_factory=list)
    model_id: str | None = None
    destination: str | None = None
    remote: bool | None = None
    characters: int = 0
    """Taille du texte envoyé (souhait + description), pour dire « environ N caractères »."""


class IntentionRun(StrictModel):
    run_id: UUID
    level_id: UUID
    preview: IntentionPreview
    brief: LayoutBrief
    result: StepResult


def _room_of(level: Level, room_id: UUID | None) -> Room | None:
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    if room_id is not None:
        chosen = level.elements.get(room_id)
        if not isinstance(chosen, Room):
            msg = f"pièce inconnue : {room_id}"
            raise DetectionError(msg)
        return chosen
    if not rooms:
        return None
    derived = compute_derived(level)
    return max(rooms, key=lambda r: derived.rooms[r.id].area_m2 if r.id in derived.rooms else 0.0)


LEVEL_KIND_WORDS: dict[str, str] = {
    "ground": "rez-de-chaussée",
    "upper": "étage",
    "attic": "combles / grenier",
    "basement": "sous-sol",
}


def describe_room(project: Project, level_id: UUID, room_id: UUID | None = None) -> str:  # noqa: PLR0912, PLR0915
    """Description textuelle de la pièce pour le modèle : surfaces, hauteur, murs, ouvertures,
    radiateurs, pentes, meubles présents. Cotes du plan avec leur provenance ; rien d'autre."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    derived = compute_derived(level)
    room = _room_of(level, room_id)
    lines: list[str] = []
    wall_ids: list[UUID]
    if room is not None and room.id in derived.rooms:
        d = derived.rooms[room.id]
        usage = f" ({room.room_type})" if room.room_type else ""
        line = f"Pièce « {room.name or 'sans nom'} »{usage} : {area(d.area_m2)} au sol"
        if d.usable_area_m2 is not None:
            line += f", dont {area(d.usable_area_m2)} à plus de 1,80 m (combles)"
        lines.append(line + ".")
        wall_ids = list(room.boundary_walls)
    else:
        bounds = derived.bounds
        if bounds is None:
            lines.append("Niveau sans mur tracé : aucune dimension connue.")
        else:
            lines.append(
                f"Niveau « {level.name} » sans pièce détectée : emprise "
                f"{decimal(bounds.max.x - bounds.min.x)} × "
                f"{metres(bounds.max.y - bounds.min.y)}."
            )
        wall_ids = [e.id for e in level.elements.values() if isinstance(e, Wall)]
    if level.kind is not None:
        lines.append(f"Niveau « {level.name} » ({LEVEL_KIND_WORDS[level.kind]}).")
    lines.append(
        f"Hauteur sous plafond {metres(level.height)} (cote {SOURCE_LABELS[level.height_source]})."
    )
    walls = [w for wid in wall_ids if isinstance(w := level.elements.get(wid), Wall) and w.visible]
    if walls:
        parts = [
            f"{w.name or 'mur'} {metres(derived.walls[w.id].length_m)}"
            for w in walls
            if w.id in derived.walls
        ]
        lines.append("Murs : " + ", ".join(parts) + ".")
    wall_set = {w.id for w in walls}
    openings = [
        e
        for e in level.elements.values()
        if isinstance(e, Opening) and e.host_wall in wall_set and e.visible
    ]
    if openings:
        parts = []
        for o in openings:
            host = level.elements[o.host_wall]
            kind = OPENING_LABELS.get(o.kind, "passage")
            part = f"{kind} de {metres(o.width)} sur {host.name or 'un mur'}"
            if o.kind is OpeningKind.WINDOW:
                part += f" (allège {metres(o.sill_height)})"
            parts.append(part)
        lines.append("Ouvertures : " + " ; ".join(parts) + ".")
    else:
        lines.append("Ouvertures : aucune dans le plan.")
    radiators = [
        e
        for e in level.elements.values()
        if isinstance(e, Radiator) and e.host_wall in wall_set and e.visible
    ]
    if radiators:
        parts = [
            f"{metres(r.width)} sur {level.elements[r.host_wall].name or 'un mur'}"
            for r in radiators
        ]
        lines.append(f"Radiateurs : {len(radiators)} ({', '.join(parts)}).")
    if derived.roofs:
        parts = []
        for roof in derived.roofs.values():
            part = f"égout à {metres(roof.eave_height)}, pente {roof.pitch_deg:.0f}°"
            if roof.usable_offset_m is not None:
                part += f", on tient debout à {metres(roof.usable_offset_m)} de l'égout"
            parts.append(part)
        lines.append(f"Pentes de toit : {len(derived.roofs)} ({' ; '.join(parts)}).")
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture) and e.visible]
    if furniture:
        parts = [
            f"{f.name or f.category} {decimal(f.bbox.width)} × {decimal(f.bbox.depth)} × "
            f"{metres(f.bbox.height)}" + (" (à conserver)" if f.preserve else "")
            for f in furniture
        ]
        lines.append("Meubles déjà présents : " + " ; ".join(parts) + ".")
    else:
        lines.append("Aucun meuble pour l'instant.")
    lines.append("Toutes ces cotes viennent du plan : elles ne se modifient pas.")
    return "\n".join(lines)


def preview_intention(
    project: Project, level_id: UUID, body: IntentionBody, model: LanguageModel | None
) -> IntentionPreview:
    """Ce qui partirait, vers où, sans appeler le modèle."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    room = describe_room(project, level_id, body.room_id)
    keep = (
        list(body.keep)
        if body.keep is not None
        else [e.id for e in level.elements.values() if isinstance(e, Furniture) and e.preserve]
    )
    categories = load_catalog().categories
    prompt = IntentionAgent.user_prompt(body.text, room, categories)
    return IntentionPreview(
        text=body.text,
        room_description=room,
        categories=categories,
        keep=keep,
        model_id=model.model_id if model else None,
        destination=model.destination if model else None,
        remote=model.remote if model else None,
        characters=len(prompt),
    )


def run_intention(
    project: Project,
    level_id: UUID,
    body: IntentionBody,
    runs_dir: Path,
    *,
    model: LanguageModel,
    gateway: ModelGateway,
    run_id: UUID | None = None,
) -> IntentionRun:
    """Consentement (passerelle), appel texte seul, validation tolérante, un réessai si la
    réponse n'est pas du JSON, artefacts. Aucun patch de scène."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    preview = preview_intention(project, level_id, body, model)
    agent = IntentionAgent(model, gateway.bind(model, project))
    out_dir = runs_dir / str(rid) / "intention"
    out_dir.mkdir(parents=True, exist_ok=True)
    response = agent.ask(body.text, preview.room_description, preview.categories)
    raw_path = out_dir / "intention_raw.txt"
    raw_path.write_text(response.text, encoding="utf-8")
    retried = False
    try:
        brief = parse_brief(response.text, preview.categories, body.text)
    except IntentionParseError:
        retried = True
        response = agent.ask(
            body.text, preview.room_description, preview.categories, retry_hint=response.text
        )
        raw_path.write_text(
            raw_path.read_text(encoding="utf-8") + "\n\n--- second essai ---\n" + response.text,
            encoding="utf-8",
        )
        try:
            brief = parse_brief(response.text, preview.categories, body.text)
        except IntentionParseError as exc:
            msg = f"{exc} après un second essai (réponse brute : {raw_path})"
            raise IntentionParseError(msg) from exc
    brief = brief.model_copy(update={"keep": preview.keep})
    brief_path = out_dir / "brief.json"
    brief_path.write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    warnings = list(brief.dropped)
    if retried:
        warnings.append("Première réponse du modèle sans JSON : second essai réussi.")
    if brief.notes:
        warnings.append(f"Modèle : {brief.notes[:300]}")
    if not brief.items:
        warnings.append("Le modèle n'a retenu aucun meuble du catalogue : reformuler le souhait.")
    return IntentionRun(
        run_id=rid,
        level_id=level_id,
        preview=preview,
        brief=brief,
        result=StepResult(
            step=STEP,
            confidence=MAX_BRIEF_CONFIDENCE if brief.items else 0.0,
            assumptions=[
                Assumption(
                    text="Brief produit par un modèle de langage à partir de votre texte et des "
                    "cotes du plan : liste de meubles et préférences, sans aucune position. À "
                    "relire et corriger avant toute proposition d'aménagement.",
                    impact="medium",
                )
            ],
            warnings=warnings,
            artifacts=[
                Artifact(
                    name=raw_path.name,
                    path=raw_path,
                    media_type="text/plain",
                    description="Réponse brute du modèle",
                ),
                Artifact(
                    name=brief_path.name,
                    path=brief_path,
                    media_type="application/json",
                    description="Brief validé",
                ),
            ],
            duration_s=time.perf_counter() - started,
            model_ids=[agent.model_id],
        ),
    )
