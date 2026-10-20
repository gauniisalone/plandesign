"""Rendu contextualisé (A1, directive d'augus du 14 octobre) : depuis le tableau, une source
(photo de la pièce ou rendu d'une vue 3D) et une intention (style, ambiance, matières, budget,
contraintes) donnent un prompt enrichi par ce que le plan sait — pièce et usage, dimensions
avec « ≈ » quand elles sont estimées, portes, fenêtres et radiateurs, orientation de la
lumière, ce qu'il faut garder — affiché et modifiable avant la génération. Trois variantes,
même graine : conservatrice, créative, radicale (force de l'img2img et poids des contraintes).

Le plan 2D n'est jamais un guide d'image : il enrichit le texte, et la source d'image est une
photo ou le rendu Blender d'une vue. Une génération ne modifie ni le plan ni la 3D : rien ne
passe par l'historique du projet, les images vont dans `runs/<run>/contextual/`. Chaque image
porte « proportions estimées » sur elle-même et dans ses métadonnées.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from pydantic import Field, JsonValue

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import (
    DepthEstimator,
    ImageInspirer,
    InspirationRequest,
    LanguageModel,
)
from openplan.pipeline.board import INSPIRATION_DIR, list_media
from openplan.pipeline.depth import depth_to_image
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.finish import (
    ENGLISH,
    NEGATIVE,
    ROOM_WORDS,
    PromptWords,
    depth_control,
    edges_of,
    prompt_groups,
    prompt_parts,
)
from openplan.pipeline.ingestion import load_image
from openplan.pipeline.inspire import stamp
from openplan.pipeline.instruction import (
    Understanding,
    accented,
    intent_fields,
    read_instruction,
)
from openplan.pipeline.intention import _room_of
from openplan.pipeline.render import list_renders
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.elements import Furniture, Opening, OpeningKind, Radiator, Room, Wall
from openplan.schemas.intent import DesignIntent
from openplan.schemas.numbers import area, decimal, metres
from openplan.schemas.scene import Level, Project

Variant = Literal["conservatrice", "creative", "radicale"]
VARIANTS: dict[Variant, tuple[float, float]] = {
    "conservatrice": (0.45, 1.2),
    "creative": (0.65, 1.0),
    "radicale": (0.85, 0.8),
}
"""Par variante, source = rendu d'une vue 3D : force de l'img2img (part refaite) et poids des
contraintes profondeur et arêtes (décision PO du 14 octobre)."""
PHOTO_VARIANTS: dict[Variant, tuple[float, float]] = {
    "conservatrice": (0.5, 0.9),
    "creative": (0.75, 0.6),
    "radicale": (0.92, 0.4),
}
"""Source = photo brute (A4) : profondeur seule, sans arêtes, forces plus hautes et contraintes
plus basses, sinon chaque détail de la photo (objets, câbles, gravats) est reproduit et les
meubles de l'intention n'apparaissent jamais (planche photo 2 du 14 octobre)."""
RADICAL_REFERENCE_BONUS = 0.2
"""La variante radicale écoute davantage les références (0,6 → 0,8 par défaut, A4)."""
TOKEN_BUDGET = 75
"""Le modèle d'images coupe à 77 jetons CLIP (bornes comprises), silencieusement : on reste
en dessous, et le texte de l'utilisateur passe avant la géométrie pour n'être jamais coupé
(A4)."""
VARIANT_LABELS: dict[Variant, str] = {
    "conservatrice": "Conservatrice",
    "creative": "Créative",
    "radicale": "Radicale",
}
Budget = Literal["economique", "standard", "haut_de_gamme"]
BUDGET_WORDS: dict[Budget, str] = {
    "economique": "simple affordable materials",
    "standard": "",
    "haut_de_gamme": "premium finishes",
}
"""Le budget n'est lu que comme une contrainte de matières, rien de plus (l'écran le dit)."""
BUDGET_FR: dict[Budget, str] = {
    "economique": "matières simples et abordables",
    "standard": "",
    "haut_de_gamme": "matières et finitions haut de gamme",
}
SourceKind = Literal["photo", "render"]
ReferenceKind = Literal["photo", "render", "contextual", "file"]
MAX_REFERENCES = 4
DEFAULT_REFERENCE_SCALE = 0.6
FRENCH = PromptWords(
    lead="photographie d'intérieur réaliste d'une {room}",
    style={
        "naturel": "matières naturelles, bois chaleureux, lin, douce lumière du jour",
        "chalet": "chalet rustique, vieux bois, pierre, plaids en laine, lumière chaude",
        "scandinave": "design scandinave, chêne clair, lin blanc, minimaliste, grande clarté",
        "industriel": "loft industriel, métal sombre, bois brut, touches de béton, lumière tamisée",
        "contemporain": "design contemporain, lignes nettes, palette neutre, éclairage élégant",
    },
    mood={
        "chaleureux": "chaleureux et accueillant",
        "lumineux": "lumineux et aéré",
        "epure": "épuré",
        "cosy": "cosy",
        "brut": "textures brutes",
    },
    finish={
        "wood_floor": "parquet en pin clair",
        "wood_dark": "vieux parquet sombre",
        "wood_boards": "planches de bois",
        "oak_dark": "chêne foncé",
        "painted_wood": "bois peint en blanc",
        "timber": "poutres apparentes",
        "stone": "mur en pierre apparente",
        "stone_rubble": "mur en moellons",
        "plaster": "murs enduits",
        "limewash": "murs à la chaux blanche",
        "concrete_floor": "sol en béton ciré",
    },
    slopes="{word} sous les pentes",
    palette="palette {colors}",
    color={},
    light={
        "matin": "douce lumière du matin",
        "midi": "lumière du jour",
        "heure_doree": "lumière d'heure dorée",
        "soir": "lampes du soir",
    },
    warmth={"chaude": "tons chauds", "froide": "tons froids", "neutre": ""},
    density={"sobre": "décor minimal", "normal": "", "riche": "décor riche et superposé"},
    tail="style magazine d'architecture, grand détail, textures réalistes",
)
"""Les mêmes mots en français : la traduction du prompt construit, montrée en lecture seule
au-dessus de l'anglais modifiable (demande du PO, A2)."""
ROOM_PROMPT_FR: dict[str, str] = {
    "bedroom": "chambre",
    "living_room": "séjour",
    "kitchen": "cuisine",
    "bathroom": "salle de bain",
    "office": "bureau à la maison",
    "attic": "chambre sous combles aménagés",
}
CONTEXTUAL_DIR = "contextual"
SIDECAR = "contextual.json"
LABEL = "Rendu contextualisé · proportions estimées"
STEPS = 30
MAX_SIDE = 640
"""Grand côté des variantes (A4b) : à 768 px avec l'IP-Adapter chargé, la VRAM de pointe
dépassait les 6,1 Go de la carte et une variante mettait sept minutes au lieu de vingt
secondes ; à 640 px tout tient sous 5,5 Go."""
MAX_INTENTION = 1000
MAX_PROMPT = 2000
INTENTION_WORDS = 25
"""Mots du texte de l'utilisateur repris tels quels dans le prompt (le modèle lit l'anglais
mieux, mais un mot de matière en français passe souvent)."""
CONTEXTUAL_EXPECTED_S = 30.0
NEGATIVE_CONTEXTUAL = NEGATIVE + ", moved doors, moved windows, different room layout"
CARDINALS = ("nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest")
OPENING_WORDS: dict[OpeningKind, str] = {
    OpeningKind.DOOR: "porte",
    OpeningKind.WINDOW: "fenêtre",
    OpeningKind.FRENCH_WINDOW: "porte-fenêtre",
    OpeningKind.SLIDING_WINDOW: "baie coulissante",
}


class ContextualSource(StrictModel):
    """Ce qui guide l'image : une photo du projet, ou le rendu Blender d'une vue (3D)."""

    kind: SourceKind
    id: UUID
    """L'image (photo) ou le run du rendu (render)."""
    label: str = Field(default="", max_length=120)


class ContextualPromptBody(StrictModel):
    intention: str = Field(default="", max_length=MAX_INTENTION)
    budget: Budget = "standard"
    source: ContextualSource
    room_id: UUID | None = None
    """La pièce regardée ; sinon la plus grande."""


class ContextualPrompt(StrictModel):
    """Le prompt enrichi, et ce qui l'a nourri, montré avant toute génération."""

    prompt: str
    prompt_fr: str = ""
    """La même chose en français, en lecture seule (le modèle lit l'anglais)."""
    tokens: int = 0
    """Jetons CLIP estimés du prompt (≤ TOKEN_BUDGET ; le modèle coupe à 77)."""
    context: list[str]
    """Ce que le plan a apporté, en français : pièce, dimensions, ouvertures, lumière…"""
    estimated: bool
    """Des cotes estimées (photo, plan généré) : « proportions estimées »."""
    understood: list[str]
    summary: list[str] = Field(default_factory=list)
    """Ce que le lecteur a compris, en mots simples et en français (« chambre · chaleureux ·
    1 lit double · chêne clair »)."""
    fields: dict[str, JsonValue]
    reader: Understanding
    notice: str = ""


class ContextualReference(StrictModel):
    """Une image du tableau cochée « Référence » (A2) : une photo du projet, un rendu (ou
    une image d'inspiration), une variante contextualisée, ou une image jointe. Son allure
    guide la génération par l'IP-Adapter ; jamais sa géométrie."""

    kind: ReferenceKind
    ref: str = Field(min_length=1, max_length=80)
    """Ce que montre l'élément du tableau : id de l'image, du run, `run:variante`, id du
    fichier joint."""
    label: str = Field(default="", max_length=120)


class ContextualBody(ContextualPromptBody):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    """Le prompt final, tel qu'affiché et éventuellement modifié par l'utilisateur."""
    variants: list[Variant] = Field(default_factory=lambda: list(VARIANTS), min_length=1)
    seed: int = Field(default=0, ge=0)
    references: list[ContextualReference] = Field(default_factory=list, max_length=MAX_REFERENCES)
    reference_scale: float = Field(default=DEFAULT_REFERENCE_SCALE, ge=0.2, le=1.0)
    """Poids des références face au texte ; sans référence, il ne sert pas."""


class ContextualEntry(StrictModel):
    """Une variante générée : l'image et ses métadonnées (prompt final, source spatiale,
    horodatage, paramètres), telles que la carte du tableau les montre."""

    run_id: UUID
    variant: Variant
    image: str
    prompt: str
    intention: str
    budget: Budget
    source: ContextualSource
    context: list[str]
    estimated: bool = True
    strength: float
    control_scale: float
    seed: int
    steps: int = STEPS
    model_ids: list[str]
    guided_by: list[str] = Field(default_factory=lambda: ["profondeur", "arêtes"])
    """Ce qui a tenu la géométrie : profondeur seule sur une photo (A4), profondeur et arêtes
    sur le rendu d'une vue."""
    references: list[str] = Field(default_factory=list)
    """Libellés des images de référence qui ont guidé l'allure (A2) ; vide : texte seul."""
    reference_scale: float | None = None
    duration_s: float
    peak_vram_mb: float | None = None
    created_at: datetime
    label: str = LABEL
    provenance: Source = Source.GENERATED
    note: str = (
        "Image générée par un modèle d'images local, guidée par la profondeur et les arêtes de "
        "la source ; proportions estimées, rien de mesuré ; le plan et la 3D n'ont pas bougé."
    )


class ContextualRun(StrictModel):
    run_id: UUID
    entries: list[ContextualEntry]
    total_s: float


class ContextualError(ValueError):
    """Source introuvable ou inutilisable (pas de rendu pour cette vue)."""


# ------------------------------------------------------------------ contexte du plan
def _cardinal(bearing_deg: float) -> str:
    return CARDINALS[int(((bearing_deg % 360) + 22.5) // 45) % 8]


def _wall_outward_bearing(level: Level, wall: Wall, centroid: tuple[float, float]) -> float | None:
    """Cap (0° = nord, sens horaire) de la normale du mur tournée vers l'extérieur de la
    pièce, d'après le nord du niveau ; None si le mur est dégénéré."""
    a = level.vertices.get(wall.start_vertex)
    b = level.vertices.get(wall.end_vertex)
    if a is None or b is None:
        return None
    dx, dy = b.position.x - a.position.x, b.position.y - a.position.y
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    nx, ny = -dy / length, dx / length
    mx, my = (a.position.x + b.position.x) / 2, (a.position.y + b.position.y) / 2
    if (centroid[0] - mx) * nx + (centroid[1] - my) * ny > 0:
        nx, ny = -nx, -ny
    angle = math.degrees(math.atan2(ny, nx))
    return (level.north_deg - angle) % 360


def _approx(value: float, estimated: bool) -> str:
    return f"{'≈ ' if estimated else ''}{metres(value)}"


def context_lines(  # noqa: PLR0912, PLR0915 - une ligne par sorte d'information
    project: Project, level_id: UUID, room_id: UUID | None
) -> tuple[list[str], bool, Room | None]:
    """Ce que le plan apporte au prompt, en français, avec « ≈ » sur les cotes estimées ;
    renvoie aussi si des proportions sont estimées, et la pièce regardée."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise ContextualError(msg)
    derived = compute_derived(level)
    room = _room_of(level, room_id)
    lines: list[str] = []
    walls = [e for e in level.elements.values() if isinstance(e, Wall) and e.visible]
    wall_ids = set(room.boundary_walls) if room is not None else {w.id for w in walls}
    walls = [w for w in walls if w.id in wall_ids]
    estimated = any(w.source in _UNCERTAIN for w in walls)
    centroid = (0.0, 0.0)
    if room is not None and room.id in derived.rooms:
        d = derived.rooms[room.id]
        xs = [p.x for p in d.polygon]
        ys = [p.y for p in d.polygon]
        centroid = (d.centroid.x, d.centroid.y)
        kind = ROOM_FR.get(room.room_type or "", room.room_type or "pièce")
        name = room.name or "sans nom"
        lines.append(f"Pièce « {name} » ({kind}) : {'≈ ' if estimated else ''}{area(d.area_m2)}.")
        width = _approx(max(xs) - min(xs), estimated)
        depth = _approx(max(ys) - min(ys), estimated)
        height = _approx(level.height, level.height_source in _UNCERTAIN)
        lines.append(f"Emprise {width} × {depth}, hauteur sous plafond {height}.")
        if d.usable_area_m2 is not None and d.usable_area_m2 < d.area_m2 - 1e-6:
            lines.append(f"Combles : {area(d.usable_area_m2)} à plus de 1,80 m.")
    elif derived.bounds is not None:
        b = derived.bounds
        lines.append(
            f"Niveau « {level.name} » sans pièce détectée : emprise "
            f"{_approx(b.max.x - b.min.x, estimated)} × {_approx(b.max.y - b.min.y, estimated)}."
        )
        centroid = ((b.min.x + b.max.x) / 2, (b.min.y + b.max.y) / 2)
    else:
        lines.append("Plan sans mur : aucune dimension connue, proportions libres.")
        estimated = True
    openings = [
        e
        for e in level.elements.values()
        if isinstance(e, Opening) and e.visible and e.host_wall in wall_ids
    ]
    windows: list[str] = []
    for o in openings:
        host = level.elements.get(o.host_wall)
        word = OPENING_WORDS.get(o.kind, "passage")
        where = ""
        if isinstance(host, Wall):
            bearing = _wall_outward_bearing(level, host, centroid)
            if bearing is not None:
                where = f" côté {_cardinal(bearing)}"
        part = f"{word} de {metres(o.width)}{where}"
        if o.kind is OpeningKind.WINDOW and o.sill_height > 0:
            part += f" (allège {metres(o.sill_height)})"
        lines.append(f"Ouverture : {part}.")
        if o.kind is not OpeningKind.DOOR and where:
            windows.append(where.removeprefix(" côté "))
    if not openings:
        lines.append("Ouvertures : aucune dans le plan.")
    radiators = [
        e
        for e in level.elements.values()
        if isinstance(e, Radiator) and e.visible and e.host_wall in wall_ids
    ]
    if radiators:
        lines.append(
            f"Radiateurs : {len(radiators)} ({', '.join(metres(r.width) for r in radiators)})."
        )
    if windows:
        lines.append(
            f"Lumière naturelle : {', '.join(dict.fromkeys(windows))} (nord du plan connu)."
        )
    kept = [
        e for e in level.elements.values() if isinstance(e, Furniture) and e.visible and e.preserve
    ]
    if kept:
        lines.append(
            "À conserver : " + ", ".join(f.name or f.category for f in kept) + " (meubles marqués)."
        )
    lines.append("Contraintes : murs, portes et fenêtres restent en place ; circulation gardée.")
    return lines, estimated, room


ROOM_FR: dict[str, str] = {
    "bedroom": "chambre",
    "living_room": "séjour",
    "kitchen": "cuisine",
    "bathroom": "salle de bain",
    "office": "bureau",
    "attic": "combles",
}
_UNCERTAIN = frozenset({Source.ESTIMATED, Source.INFERRED, Source.GENERATED})


def _geometry_words(project: Project, level_id: UUID, room: Room | None) -> list[str]:
    """La géométrie en mots anglais pour le modèle d'images : taille, ouvertures, ce qui
    reste en place."""
    level = project.levels[level_id]
    derived = compute_derived(level)
    words: list[str] = []
    if room is not None and room.id in derived.rooms:
        d = derived.rooms[room.id]
        xs = [p.x for p in d.polygon]
        ys = [p.y for p in d.polygon]
        words.append(f"room about {max(xs) - min(xs):.1f} by {max(ys) - min(ys):.1f} metres")
        wall_ids = set(room.boundary_walls)
    else:
        wall_ids = {e.id for e in level.elements.values() if isinstance(e, Wall)}
    openings = [
        e for e in level.elements.values() if isinstance(e, Opening) and e.host_wall in wall_ids
    ]
    doors = sum(1 for o in openings if o.kind is OpeningKind.DOOR)
    glazed = len(openings) - doors
    if glazed:
        words.append(f"{glazed} window{'s' if glazed > 1 else ''}")
    if doors:
        words.append(f"{doors} door{'s' if doors > 1 else ''}")
    if any(isinstance(e, Radiator) for e in level.elements.values()):
        words.append("radiator on the wall")
    words.append("same walls doors and windows in place")
    return words


def _geometry_words_fr(project: Project, level_id: UUID, room: Room | None) -> list[str]:
    """La même géométrie, en français, pour la traduction du prompt."""
    level = project.levels[level_id]
    derived = compute_derived(level)
    words: list[str] = []
    if room is not None and room.id in derived.rooms:
        d = derived.rooms[room.id]
        xs = [p.x for p in d.polygon]
        ys = [p.y for p in d.polygon]
        words.append(
            f"pièce d'environ {decimal(max(xs) - min(xs), 1)} × {metres(max(ys) - min(ys), 1)}"
        )
        wall_ids = set(room.boundary_walls)
    else:
        wall_ids = {e.id for e in level.elements.values() if isinstance(e, Wall)}
    openings = [
        e for e in level.elements.values() if isinstance(e, Opening) and e.host_wall in wall_ids
    ]
    doors = sum(1 for o in openings if o.kind is OpeningKind.DOOR)
    glazed = len(openings) - doors
    if glazed:
        words.append(f"{glazed} fenêtre{'s' if glazed > 1 else ''}")
    if doors:
        words.append(f"{doors} porte{'s' if doors > 1 else ''}")
    if any(isinstance(e, Radiator) for e in level.elements.values()):
        words.append("radiateur au mur")
    words.append("mêmes murs, portes et fenêtres en place, disposition existante gardée")
    return words


# ------------------------------------------------------------------ prompt
def build_prompt(
    project: Project,
    level_id: UUID,
    body: ContextualPromptBody,
    *,
    categories: list[str],
    model: LanguageModel | None,
    gateway: ModelGateway | None,
) -> ContextualPrompt:
    """Le prompt enrichi : l'intention lue par le lecteur interne (phrases simples ; Gemini
    seulement si la case est cochée, texte seul), l'ambiance qui en sort, la géométrie du
    plan, le budget comme contrainte de matières, et le texte de l'utilisateur."""
    lines, estimated, room = context_lines(project, level_id, body.room_id)
    text = body.intention.strip()
    if text:
        reading = read_instruction(text, categories, model=model, gateway=gateway, project=project)
        translation, reader, notice = reading.translation, reading.understanding, reading.notice
        fields = dict(translation.fields)
        understood = list(translation.understood)
        summary = list(translation.summary)
    else:
        fields, understood, summary, reader, notice = {}, [], [], "none", ""
    base = (room.intent if room is not None else None) or DesignIntent()
    merged = {**base.model_dump(mode="json"), **intent_fields(base, fields)}
    merged.pop("instruction", None)
    intent = DesignIntent.model_validate(merged)
    # La pièce dite dans l'intention (« chambre ») prime sur le type de la pièce du plan.
    kind = intent.room_type or ((room.room_type or "") if room is not None else "")
    room_word, room_fr = ROOM_WORDS.get(kind, "room"), ROOM_PROMPT_FR.get(kind, "pièce")
    level = project.levels[level_id]
    if level.kind == "attic" and room_word in ("room", "bedroom"):
        room_word, room_fr = "converted attic bedroom", ROOM_PROMPT_FR["attic"]
    said = " ".join(text.split()[:INTENTION_WORDS]) if text else ""
    extra = [*_geometry_words(project, level_id, room), BUDGET_WORDS[body.budget]]
    extra_fr = [*_geometry_words_fr(project, level_id, room), BUDGET_FR[body.budget], said]
    prompt = assemble_prompt(room_word, intent, said, tuple(e for e in extra if e))
    return ContextualPrompt(
        prompt=prompt,
        prompt_fr=prompt_french(room_fr, intent, extra=tuple(e for e in extra_fr if e)),
        tokens=approx_clip_tokens(prompt),
        context=lines,
        estimated=estimated,
        understood=understood,
        summary=summary,
        fields=fields,
        reader=reader,
        notice=notice,
    )


_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ]+|[0-9]|[^\sA-Za-zÀ-ÿ0-9]")


def approx_clip_tokens(text: str) -> int:
    """Estimation des jetons CLIP (BPE anglais) sans charger le tokenizer (absent sans les
    poids) : un mot anglais courant vaut un jeton jusqu'à neuf lettres, puis un par tranche
    de six ; un mot accentué (français) un de plus ; chaque chiffre et chaque signe un ; plus
    les deux bornes. Calé sur un cas réel : 70 mots dont du français = 100 jetons."""
    count = 2
    for token in _TOKEN_RE.findall(text):
        if token.isalpha():
            count += 1 if len(token) <= 9 else math.ceil(len(token) / 6)
            if any(ord(c) > 127 for c in token):
                count += 1
        else:
            count += 1
    return count


def assemble_prompt(room_word: str, intent: DesignIntent, said: str, extra: tuple[str, ...]) -> str:
    """Le prompt anglais dans l'ordre : pièce, style, **le texte de l'utilisateur**, géométrie
    et budget, puis l'ambiance (humeur, lumière, chaleur), la finale, et enfin palette,
    matières par surface et décor ; coupé par morceaux entiers à TOKEN_BUDGET jetons estimés,
    en commençant par la fin (décor, matières, palette…), jamais par le texte de
    l'utilisateur ni la pièce."""
    groups = prompt_groups(room_word, intent, None)
    ordered = [
        *groups["lead"],
        *groups["style"],
        *([said] if said else []),
        *extra,
        *groups["mood"],
        *groups["light"],
        *groups["warmth"],
        ENGLISH.tail,
        *groups["palette"],
        *groups["surfaces"],
        *groups["density"],
    ]
    protected = 3 if said else 2
    kept = list(ordered)
    while len(kept) > protected and approx_clip_tokens(", ".join(kept)) > TOKEN_BUDGET:
        kept.pop()
    return ", ".join(kept)


def prompt_french(room_fr: str, intent: DesignIntent, *, extra: tuple[str, ...] = ()) -> str:
    """La traduction française du prompt construit, pièce par pièce : la pièce, le style,
    l'humeur (libellés de l'ambiance), les matières, la palette (les couleurs sont déjà
    des mots français), la lumière, le décor, la géométrie, le budget, le texte."""
    words = replace(FRENCH, color={c: accented(c) for c in intent.palette})
    return ", ".join(prompt_parts(room_fr, intent, None, extra=extra, words=words))


# ------------------------------------------------------------------ génération
def _source_pixels(
    project: Project,
    project_dir: Path,
    runs_dir: Path,
    source: ContextualSource,
    estimator: Callable[[], DepthEstimator] | None,
) -> tuple[np.ndarray, np.ndarray | None, str]:
    """Pixels de la source, sa carte de profondeur (proche = clair) si on peut l'avoir, et
    son libellé."""
    from PIL import Image

    if source.kind == "photo":
        ref = project.images.get(source.id)
        if ref is None:
            msg = f"photo inconnue : {source.id}"
            raise ContextualError(msg)
        image = load_image(project_dir, ref)
        pixels = np.asarray(image.pixels, dtype=np.uint8)
        depth = depth_to_image(estimator().predict(image).depth) if estimator else None
        return pixels, depth, source.label or "Photo"
    render_dir = runs_dir / str(source.id) / "blender"
    if not (render_dir / "preview.png").is_file():
        msg = f"aucun rendu pour cette vue : {source.id} (rendez-la d'abord)"
        raise ContextualError(msg)
    with Image.open(render_dir / "preview.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    depth_npy = render_dir / "depth_z.npy"
    depth = depth_control(np.load(depth_npy)) if depth_npy.is_file() else None
    label = source.label
    if not label:
        entry = next((e for e in list_renders(runs_dir) if e.run_id == source.id), None)
        label = entry.camera_name if entry is not None else "Vue 3D"
    return pixels, depth, label


def reference_path(
    project: Project, project_dir: Path, runs_dir: Path, reference: ContextualReference
) -> Path:
    """Le fichier image d'une référence du tableau ; ContextualError s'il n'existe pas ou
    si le fichier joint n'est pas une image."""
    kind, ref = reference.kind, reference.ref
    if kind == "photo":
        try:
            image = project.images[UUID(ref)]
        except (KeyError, ValueError):
            msg = f"référence : photo inconnue ({ref})"
            raise ContextualError(msg) from None
        return project_dir / image.path
    if kind == "render":
        run_dir = runs_dir / ref
        for candidate in (
            run_dir / "blender" / "finished.png",
            run_dir / "blender" / "preview.png",
            run_dir / INSPIRATION_DIR / "inspiration.png",
        ):
            if candidate.is_file():
                return candidate
        msg = f"référence : rendu introuvable ({ref})"
        raise ContextualError(msg)
    if kind == "contextual":
        run, _, variant = ref.partition(":")
        try:
            return image_path(runs_dir, UUID(run), f"{variant}.png")
        except ValueError as exc:
            msg = f"référence : {exc}"
            raise ContextualError(msg) from exc
    media = next((m for m in list_media(project_dir) if str(m.id) == ref), None)
    if media is None or media.kind != "image":
        msg = f"référence : fichier joint introuvable ou pas une image ({ref})"
        raise ContextualError(msg)
    return project_dir / media.path


def check_references(
    project: Project, project_dir: Path, runs_dir: Path, references: list[ContextualReference]
) -> None:
    """Chaque référence existe (avant de lancer la tâche) ; ContextualError sinon."""
    for reference in references:
        reference_path(project, project_dir, runs_dir, reference)


def _reference_pixels(
    project: Project, project_dir: Path, runs_dir: Path, references: list[ContextualReference]
) -> list[np.ndarray]:
    from PIL import Image

    out: list[np.ndarray] = []
    for reference in references:
        with Image.open(reference_path(project, project_dir, runs_dir, reference)) as img:
            out.append(np.asarray(img.convert("RGB"), dtype=np.uint8))
    return out


def _reference_label(reference: ContextualReference) -> str:
    return (
        reference.label
        or {
            "photo": "photo",
            "render": "rendu",
            "contextual": "variante",
            "file": "image jointe",
        }[reference.kind]
    )


def generate(
    project: Project,
    level_id: UUID,
    project_dir: Path,
    runs_dir: Path,
    body: ContextualBody,
    *,
    inspirer: ImageInspirer,
    estimator: Callable[[], DepthEstimator] | None = None,
    stage: Callable[..., None] | None = None,
    run_id: UUID | None = None,
) -> ContextualRun:
    """Les variantes demandées, même graine, dans `runs/<run>/contextual/` ; chaque image est
    marquée « proportions estimées » et accompagnée de ses métadonnées. Les références
    cochées sur le tableau guident l'allure (IP-Adapter), jamais la géométrie. Le projet
    n'est ni lu pour écriture ni modifié."""
    from PIL import Image

    started = time.perf_counter()
    rid = run_id or uuid4()
    pixels, depth, label = _source_pixels(project, project_dir, runs_dir, body.source, estimator)
    # Photo brute (A4) : profondeur seule et réglages plus libres, pour que l'aménagement de
    # l'intention apparaisse ; rendu d'une vue : profondeur et arêtes, l'aménagement y est déjà.
    from_photo = body.source.kind == "photo"
    edges = None if from_photo else edges_of(pixels)
    settings = PHOTO_VARIANTS if from_photo else VARIANTS
    guided_by = ["profondeur"] + ([] if from_photo else ["arêtes"])
    references = _reference_pixels(project, project_dir, runs_dir, body.references)
    labels = [_reference_label(r) for r in body.references]
    lines, estimated, _ = context_lines(project, level_id, body.room_id)
    out_dir = runs_dir / str(rid) / CONTEXTUAL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    source = body.source.model_copy(update={"label": label})
    entries: list[ContextualEntry] = []
    for index, variant in enumerate(body.variants):
        strength, scale = settings[variant]
        ref_scale = body.reference_scale
        if variant == "radicale":
            ref_scale = min(1.0, round(ref_scale + RADICAL_REFERENCE_BONUS, 2))
        if stage is not None:
            with_refs = f", {len(references)} référence(s)" if references else ""
            stage(
                f"Variante {index + 1}/{len(body.variants)} : "
                f"{VARIANT_LABELS[variant].lower()}{with_refs}"
            )
        result = inspirer.inspire(
            InspirationRequest(
                prompt=body.prompt,
                negative_prompt=NEGATIVE_CONTEXTUAL,
                image=pixels,
                depth=depth,
                edges=edges,
                mode="img2img",
                strength=strength,
                control_scale=scale,
                seed=body.seed,
                steps=STEPS,
                max_side=MAX_SIDE,
                references=references,
                reference_scale=ref_scale,
            )
        )
        name = f"{variant}.png"
        Image.fromarray(stamp(result.pixels, LABEL)).save(out_dir / name)
        entries.append(
            ContextualEntry(
                run_id=rid,
                variant=variant,
                image=name,
                prompt=body.prompt,
                intention=body.intention,
                budget=body.budget,
                source=source,
                context=lines,
                estimated=estimated,
                strength=strength,
                control_scale=scale,
                seed=body.seed,
                model_ids=result.model_ids,
                guided_by=guided_by,
                references=labels,
                reference_scale=ref_scale if references else None,
                duration_s=round(result.duration_s, 1),
                peak_vram_mb=result.peak_vram_mb,
                created_at=datetime.now(UTC),
            )
        )
    run = ContextualRun(
        run_id=rid, entries=entries, total_s=round(time.perf_counter() - started, 1)
    )
    (out_dir / SIDECAR).write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return run


def list_entries(runs_dir: Path) -> list[ContextualEntry]:
    """Toutes les variantes générées du projet, de la plus récente à la plus ancienne."""
    out: list[ContextualEntry] = []
    if not runs_dir.is_dir():
        return out
    for sidecar in runs_dir.glob(f"*/{CONTEXTUAL_DIR}/{SIDECAR}"):
        try:
            run = ContextualRun.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        out.extend(e for e in run.entries if (sidecar.parent / e.image).is_file())
    out.sort(key=lambda e: e.created_at, reverse=True)
    return out


def image_path(runs_dir: Path, run_id: UUID, name: str) -> Path:
    if name not in {f"{v}.png" for v in VARIANTS}:
        msg = f"image inconnue : {name}"
        raise ContextualError(msg)
    path = runs_dir / str(run_id) / CONTEXTUAL_DIR / name
    if not path.is_file():
        msg = f"rendu contextualisé introuvable : {run_id}/{name}"
        raise ContextualError(msg)
    return path


__all__ = [
    "ContextualBody",
    "ContextualEntry",
    "ContextualError",
    "ContextualPrompt",
    "ContextualPromptBody",
    "ContextualReference",
    "ContextualRun",
    "ContextualSource",
    "build_prompt",
    "check_references",
    "context_lines",
    "generate",
    "image_path",
    "list_entries",
]
