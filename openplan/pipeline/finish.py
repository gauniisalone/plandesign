"""Finition IA d'un rendu (D5, piste hybride de la note 09) : Blender fait la vérité
(géométrie, lumière, meubles), la diffusion fait la finition photo à force bornée, guidée par
la profondeur et les arêtes du rendu. Bouton explicite, jamais automatique ; l'image Blender
est gardée ; l'image finie est toujours `generated`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.protocols import FinishRequest, ImageFinisher
from openplan.pipeline.scene_prompt import SceneWords, load_scene_words
from openplan.schemas.common import DECODED_IMAGE_FORMATS, Source, StrictModel
from openplan.schemas.elements import Room
from openplan.schemas.intent import DesignIntent
from openplan.schemas.scene import Project

STEP = "rendering.finish"
DEFAULT_STRENGTH = 0.35
MAX_STRENGTH = 0.55
FINISHED = "finished.png"
FINISH_SIDECAR = "finish.json"
FinishStyle = Literal["naturel", "chalet", "scandinave", "industriel", "contemporain"]
STYLE_WORDS: dict[str, str] = {
    "naturel": "natural materials, warm wood, linen, soft natural daylight",
    "chalet": "rustic chalet, old timber, stone, wool throws, cosy warm light",
    "scandinave": "scandinavian design, light oak, white linen, minimalist, bright daylight",
    "industriel": "industrial loft, dark metal, raw wood, concrete accents, moody light",
    "contemporain": "contemporary design, clean lines, neutral palette, elegant lighting",
}
ROOM_WORDS: dict[str, str] = {
    "bedroom": "bedroom",
    "living_room": "living room",
    "kitchen": "kitchen",
    "bathroom": "bathroom",
    "office": "home office",
    "attic": "converted attic bedroom",
}
NEGATIVE = (
    "blurry, deformed, distorted geometry, extra windows, extra furniture, text, watermark, "
    "people, cartoon, low quality, oversaturated"
)
EDGE_LOW, EDGE_HIGH = 80, 160


class FinishOptions(StrictModel):
    strength: float = Field(default=DEFAULT_STRENGTH, ge=0.1, le=MAX_STRENGTH)
    """Part laissée à la diffusion : 0,35 par défaut (finition légère), 0,55 au plus."""
    style: FinishStyle | None = None
    """None : le style de l'ambiance de la pièce (naturel sans ambiance) ; sinon il la force."""
    seed: int = Field(default=0, ge=0)


class FinishEntry(StrictModel):
    run_id: UUID
    image: str = FINISHED
    source: Source = Source.GENERATED
    strength: float
    style: str
    prompt: str
    seed: int
    model_ids: list[str]
    duration_s: float
    peak_vram_mb: float | None = None
    guided_by: list[str]
    """« profondeur », « arêtes » : ce qui a tenu la géométrie du rendu des cotes."""
    created_at: datetime
    note: str = (
        "Finition IA sur le rendu des cotes : matières et lumière retouchées par un modèle "
        "d'images, la géométrie vient de Blender ; petits objets possibles, rien de mesuré."
    )


MOOD_WORDS: dict[str, str] = {
    "chaleureux": "warm and inviting",
    "lumineux": "bright and airy",
    "epure": "uncluttered",
    "cosy": "cosy",
    "brut": "raw textures",
}
FINISH_WORDS: dict[str, str] = {
    "wood_floor": "light pine floorboards",
    "wood_dark": "old dark parquet",
    "wood_boards": "wooden boards",
    "oak_dark": "dark oak",
    "painted_wood": "white painted wood",
    "timber": "exposed timber beams",
    "stone": "exposed stone wall",
    "stone_rubble": "rubble stone wall",
    "plaster": "plastered walls",
    "limewash": "white limewash walls",
    "concrete_floor": "polished concrete floor",
}
SURFACE_WORDS: dict[str, str] = {
    "floor": "floor",
    "walls": "walls",
    "gables": "gable",
    "slopes": "sloped ceiling",
    "timber": "beams",
}
LIGHT_WORDS: dict[str, str] = {
    "matin": "soft morning light",
    "midi": "natural daylight",
    "heure_doree": "golden hour light",
    "soir": "evening lamplight",
}
WARMTH_WORDS: dict[str, str] = {"chaude": "warm tones", "froide": "cool tones", "neutre": ""}
DENSITY_WORDS: dict[str, str] = {
    "sobre": "minimal decor",
    "normal": "",
    "riche": "richly layered decor",
}
COLOR_WORDS: dict[str, str] = {
    "blanc casse": "off-white",
    "lin": "linen",
    "sable": "sand",
    "terracotta": "terracotta",
    "ocre": "ochre",
    "vert sauge": "sage green",
    "vert foret": "forest green",
    "bleu nuit": "navy blue",
    "bleu gris": "grey blue",
    "gris anthracite": "charcoal grey",
    "noir": "black",
    "chene clair": "light oak",
    "noyer": "walnut",
    "rose poudre": "dusty pink",
}
MAX_PROMPT_WORDS = 60
"""Le texte du modèle d'images est coupé vers 75 jetons : on reste en dessous."""
PromptEngine = Literal["sd15", "zimage"]
ENGINE_WORDS: dict[str, int] = {"sd15": MAX_PROMPT_WORDS, "zimage": 300}
"""KAN-86 : budget de mots par moteur. SD 1.5 lit 75 jetons (CLIP) ; Z-Image lit 512 jetons
(encodeur Qwen3), la description complète de la scène y tient."""


def render_level(render_dir: Path) -> UUID | None:
    """T7e : le plan d'un rendu, écrit dans son `render.json` (absent avant T7e)."""
    try:
        data = json.loads((render_dir / "render.json").read_text(encoding="utf-8"))
        return UUID(str(data["level_id"])) if data.get("level_id") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _room_word(project: Project, level_id: UUID | None = None) -> tuple[str, DesignIntent | None]:
    """La pièce et son ambiance : celles du plan rendu (T7e), sinon du premier plan."""
    room, intent = "room", None
    chosen = project.levels.get(level_id) if level_id is not None else None
    for level in [chosen] if chosen is not None else project.levels.values():
        rooms = [e for e in level.elements.values() if isinstance(e, Room)]
        intent = next((r.intent for r in rooms if r.intent is not None), None)
        types = [r.room_type for r in rooms if r.room_type]
        if intent is not None and intent.room_type:
            types.insert(0, intent.room_type)
        if types:
            room = ROOM_WORDS.get(types[0], room)
        if level.kind == "attic" and room in ("room", "bedroom"):
            room = "converted attic bedroom"
        break
    return room, intent


def finish_prompt(
    project: Project,
    style: str | None = None,
    level_id: UUID | None = None,
    *,
    scene: SceneWords | None = None,
    engine: PromptEngine = "sd15",
) -> str:
    """Le prompt (anglais, que le modèle comprend mieux), dérivé de l'ambiance de la pièce
    (V1, point 5) : la pièce, le style, l'humeur, les matières par surface, les couleurs, la
    lumière, la densité du décor. `style` force le style ; sans ambiance, le style seul (D5).

    KAN-86 : avec `scene` (la scène rendue), les matières par surface sont celles de la scène
    et non celles par défaut du style, et les meubles vus et l'habillage sont nommés ; la
    longueur suit le moteur (`ENGINE_WORDS`). KAN-97 : les mots lus dans les images
    d'inspiration (`scene.style`) suivent le style et l'humeur de l'ambiance, sans redite."""
    room, intent = _room_word(project, level_id)
    limit = ENGINE_WORDS[engine]
    if scene is None:
        return prompt_words(room, intent, style, limit=limit)
    groups = prompt_groups(room, intent, style)
    surfaces = scene.surfaces or groups["surfaces"]
    said = " ".join([*groups["lead"], *groups["style"], *groups["mood"]]).lower()
    inspired = [w for w in scene.style if w.lower() not in said]
    parts = [
        *groups["lead"],
        *groups["style"],
        *groups["mood"],
        *inspired,
        *surfaces,
        *scene.furniture,
        *scene.textiles,
        *groups["palette"],
        *groups["light"],
        *groups["warmth"],
        *groups["density"],
        ENGLISH.tail,
    ]
    return _cut([p for p in parts if p], limit)


def _cut(parts: list[str], limit: int) -> str:
    """Les morceaux joints par des virgules, coupés au dernier morceau entier sous `limit` mots."""
    words: list[str] = []
    for part in parts:
        chunk = part.split()
        if len(words) + len(chunk) > limit:
            break
        words.extend([*chunk[:-1], chunk[-1] + ","])
    return " ".join(words).rstrip(",")


@dataclass(frozen=True)
class PromptWords:
    """Le vocabulaire d'un prompt : l'anglais pour le modèle d'images (ci-dessous), le
    français pour la traduction montrée à côté (rendu contextualisé, A2)."""

    lead: str
    """Formule d'ouverture, avec `{room}`."""
    style: dict[str, str]
    mood: dict[str, str]
    finish: dict[str, str]
    slopes: str
    """Formule des matières sous les pentes, avec `{word}`."""
    palette: str
    """Formule de la palette, avec `{colors}`."""
    color: dict[str, str]
    light: dict[str, str]
    warmth: dict[str, str]
    density: dict[str, str]
    tail: str


ENGLISH = PromptWords(
    lead="photorealistic interior photograph of a {room}",
    style=STYLE_WORDS,
    mood=MOOD_WORDS,
    finish=FINISH_WORDS,
    slopes="{word} sloped ceiling",
    palette="palette of {colors}",
    color=COLOR_WORDS,
    light=LIGHT_WORDS,
    warmth=WARMTH_WORDS,
    density=DENSITY_WORDS,
    tail="architectural digest, high detail, realistic textures",
)


PROMPT_GROUPS = ("lead", "style", "mood", "surfaces", "palette", "light", "warmth", "density")
"""Les groupes du prompt, dans l'ordre de la finition ; le rendu contextualisé les réordonne
(texte de l'utilisateur et géométrie d'abord) et coupe les derniers quand le budget de
jetons est dépassé."""


def prompt_groups(
    room: str,
    intent: DesignIntent | None,
    style: str | None = None,
    *,
    words: PromptWords = ENGLISH,
) -> dict[str, list[str]]:
    """Les morceaux du prompt par groupe (voir PROMPT_GROUPS) pour une pièce et une ambiance
    données ; un groupe peut être vide."""
    chosen = style or (intent.style if intent is not None else "naturel")
    groups: dict[str, list[str]] = {name: [] for name in PROMPT_GROUPS}
    groups["lead"] = [words.lead.format(room=room)]
    groups["style"] = [words.style.get(chosen, words.style["naturel"])]
    if intent is not None:
        from openplan.pipeline.style import STYLE_SURFACES

        groups["mood"] = [words.mood[m] for m in intent.mood if m in words.mood]
        surfaces = dict(STYLE_SURFACES.get(chosen, STYLE_SURFACES["naturel"]))
        wanted = intent.materials.model_dump()
        surfaces.update({k: v for k, v in wanted.items() if v and k in SURFACE_WORDS})
        for surface in ("floor", "walls", "slopes", "timber"):
            word = words.finish.get(surfaces.get(surface, ""))
            if word:
                groups["surfaces"].append(
                    word if surface != "slopes" else words.slopes.format(word=word)
                )
        if intent.palette:
            colors = ", ".join(words.color.get(c, c) for c in intent.palette[:4])
            groups["palette"] = [words.palette.format(colors=colors)]
        groups["light"] = [words.light.get(intent.light.time, "")]
        groups["warmth"] = [words.warmth.get(intent.light.warmth, "")]
        groups["density"] = [words.density.get(intent.decor_density, "")]
    return {name: [p for p in parts if p] for name, parts in groups.items()}


def prompt_parts(
    room: str,
    intent: DesignIntent | None,
    style: str | None = None,
    *,
    extra: tuple[str, ...] = (),
    words: PromptWords = ENGLISH,
) -> list[str]:
    """Les morceaux du prompt pour une pièce et une ambiance données, dans l'ordre : la
    pièce, le style, l'humeur, les matières par surface, les couleurs, la lumière, la densité
    du décor, puis `extra` (géométrie, budget, texte) et la finale."""
    groups = prompt_groups(room, intent, style, words=words)
    parts = [p for name in PROMPT_GROUPS for p in groups[name]]
    parts.extend(extra)
    parts.append(words.tail)
    return [p for p in parts if p]


def prompt_words(
    room: str,
    intent: DesignIntent | None,
    style: str | None = None,
    *,
    extra: tuple[str, ...] = (),
    limit: int = MAX_PROMPT_WORDS,
) -> str:
    """Le prompt anglais (partagé avec le rendu contextualisé, A1), coupé à `limit` mots."""
    return _cut(prompt_parts(room, intent, style, extra=extra), limit)


def depth_control(depth_z: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Profondeur Cycles (mètres) → image de contrôle, proche = clair."""
    z = depth_z.astype(np.float32)
    finite = np.isfinite(z) & (z > 0) & (z < 1e4)
    if not finite.any():
        return np.full(z.shape, 128, np.uint8)
    near = float(np.percentile(z[finite], 1))
    far = float(np.percentile(z[finite], 99))
    inv = 1.0 - np.clip((z - near) / max(far - near, 1e-6), 0.0, 1.0)
    inv[~finite] = 0.0
    out: NDArray[np.uint8] = (inv * 255).astype(np.uint8)
    return out


def edges_of(pixels: NDArray[np.uint8]) -> NDArray[np.uint8]:
    import cv2

    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    edges: NDArray[np.uint8] = np.asarray(cv2.Canny(gray, EDGE_LOW, EDGE_HIGH), dtype=np.uint8)
    return edges


def scene_edges(render_dir: Path, width: int, height: int) -> NDArray[np.uint8] | None:
    """KAN-88 : arêtes de la vue rastérisées depuis bake.json et camera.json du rendu ; None
    s'ils manquent ou sont illisibles (rendu d'avant : le Canny du rendu sert)."""
    from openplan.pipeline.guides import view_guides
    from openplan.schemas.bake import Bake, RenderCamera

    try:
        bake = Bake.model_validate_json((render_dir / "bake.json").read_text(encoding="utf-8"))
        camera = RenderCamera.model_validate_json(
            (render_dir / "camera.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return view_guides(bake, camera, (width, height)).edges


def finisher_engine(finisher: object) -> str:
    """Moteur d'un adaptateur de finition (`prompt_engine`, KAN-88) ; sd15 par défaut."""
    return str(getattr(finisher, "prompt_engine", "sd15"))


def finish_render(
    project: Project,
    render_dir: Path,
    run_id: UUID,
    options: FinishOptions,
    *,
    finisher: ImageFinisher,
) -> FinishEntry:
    """Finit le rendu de `render_dir` (preview.png, et depth_z.npy s'il existe) ; écrit
    finished.png et finish.json à côté, l'image Blender reste."""
    from PIL import Image

    with Image.open(render_dir / "preview.png", formats=DECODED_IMAGE_FORMATS) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    depth_npy = render_dir / "depth_z.npy"
    depth = depth_control(np.load(depth_npy)) if depth_npy.is_file() else None
    edges = edges_of(pixels)
    level_id = render_level(render_dir)
    scene = load_scene_words(project, render_dir, level_id)
    # KAN-88 : Z-Image lit la description entière (Qwen3, 512 jetons) ; SD 1.5 en garde 60 mots.
    engine: PromptEngine = "zimage" if finisher_engine(finisher) == "zimage" else "sd15"
    prompt = finish_prompt(project, options.style, level_id, scene=scene, engine=engine)
    if engine == "zimage":
        # Arêtes de la géométrie (bake + caméra), sans la texture : celles de l'essai KAN-84b.
        geometric = scene_edges(render_dir, pixels.shape[1], pixels.shape[0])
        if geometric is not None:
            edges = geometric
    result = finisher.finish(
        FinishRequest(
            prompt=prompt,
            negative_prompt=NEGATIVE,
            image=pixels,
            depth=depth,
            edges=edges,
            strength=options.strength,
            seed=options.seed,
        )
    )
    Image.fromarray(result.pixels).save(render_dir / FINISHED)
    if engine == "zimage":
        guided = ["arêtes"]  # ControlNet Union guidé par les seules arêtes du rendu
    else:
        guided = (["profondeur"] if depth is not None else []) + (
            ["arêtes"] if any("canny" in m for m in result.model_ids) else []
        )
    _, intent = _room_word(project, level_id)
    entry = FinishEntry(
        run_id=run_id,
        strength=options.strength,
        style=options.style or (intent.style if intent is not None else "naturel"),
        prompt=prompt,
        seed=options.seed,
        model_ids=result.model_ids,
        duration_s=round(result.duration_s, 1),
        peak_vram_mb=result.peak_vram_mb,
        guided_by=guided,
        created_at=datetime.now(UTC),
    )
    (render_dir / FINISH_SIDECAR).write_text(
        entry.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return entry


def read_finish(render_dir: Path) -> FinishEntry | None:
    path = render_dir / FINISH_SIDECAR
    if not path.is_file() or not (render_dir / FINISHED).is_file():
        return None
    try:
        return FinishEntry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return None
