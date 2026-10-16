"""Galerie des rendus du projet (US2) : titre, favoris, vue et ambiance de chaque rendu,
corbeille douce, favoris en zip.

- **Où** : `gallery.json` à côté des images d'un rendu (`runs/<run>/blender/`), dans le
  dossier du projet. Le format de scène ne change pas ; `render.json` non plus (un rendu
  d'avant US2 n'a simplement pas de `gallery.json` : sa vue et son ambiance sont relues dans
  la scène actuelle).
- **Favoris** : par image, pas par rendu : `blender` (image Blender), `finished` (référence :
  Blender + finition), `inspiration` (B), `catalogue` (version catalogue).
- **Corbeille douce** : supprimer déplace `runs/<run>` dans `runs/.trash/<run>` (rien n'est
  perdu pendant `TRASH_TTL_S` secondes, « Annuler » le remet) ; passé ce délai, ou à
  l'arrêt et au démarrage du serveur, le dossier est effacé pour de bon et le journal V2 note
  « annulé / rendu supprimé », rattaché à la demande qui l'avait produit.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from shapely.geometry import Point

from openplan.geom.wallgraph import WallGraph
from openplan.pipeline import journal
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.mainroom import main_room, rooms_of
from openplan.pipeline.render import RENDER_SIDECAR, RenderEntry, list_renders
from openplan.pipeline.views import AUTO_KEY, ROLES
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Camera, Room
from openplan.schemas.intent import MOOD_LABELS, STYLES
from openplan.schemas.scene import Project

GALLERY_SIDECAR = "gallery.json"
TRASH_DIR = ".trash"
DELETED_MARK = "deleted.json"
TRASH_TTL_S = 30.0
"""Délai avant l'effacement réel ; l'écran laisse 8 s pour « Annuler »."""
MAX_TITLE = 80

Variant = Literal["blender", "finished", "inspiration", "catalogue"]
VARIANTS: tuple[Variant, ...] = ("blender", "finished", "inspiration", "catalogue")
VARIANT_LABELS: dict[str, str] = {
    "blender": "blender",
    "finished": "reference",
    "inspiration": "inspiration",
    "catalogue": "catalogue",
}
ViewGroup = Literal["overall", "zone", "detail", "hand", "photo", "variant"]


class GalleryMeta(StrictModel):
    """`gallery.json` (version 1) : ce que l'utilisateur range, et la vue et l'ambiance du
    rendu notées au moment du rendu."""

    version: Literal[1] = 1
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    favorites: list[Variant] = Field(default_factory=list)
    view_group: ViewGroup | None = None
    ambiance: str | None = Field(default=None, max_length=120)


class GalleryEntry(RenderEntry):
    """Un rendu de la galerie : l'entrée de `render.json` et ses métadonnées de rangement."""

    title: str | None = None
    favorites: list[Variant] = Field(default_factory=list)
    view_group: ViewGroup = "hand"
    ambiance: str | None = None


class GalleryError(ValueError):
    """Rendu absent, image absente ou corbeille vide : 409 côté API."""


# ------------------------------------------------------------------ vue et ambiance
def view_group_of(camera: Camera | None, *, variant: int | None = None) -> ViewGroup:
    if variant is not None:
        return "variant"
    if camera is None:
        return "hand"
    role = camera.metadata.get(AUTO_KEY)
    if role in ROLES:
        return role
    return "photo" if camera.image_id is not None else "hand"


def _room_of(project: Project, level_id: UUID, camera: Camera) -> Room | None:
    level = project.levels.get(level_id)
    if level is None:
        return None
    rooms = [r for r in rooms_of(level) if r.intent is not None]
    if len(rooms) > 1:
        where = Point(camera.position.x, camera.position.y)
        graph = WallGraph(level)
        for room in rooms:
            try:
                if graph.room_axis_polygon(room.id).contains(where):
                    return room
            except (ValueError, KeyError):  # pièce dont la boucle de murs est incomplète
                continue
    main = main_room(level)
    if main is not None and main.intent is not None:
        return main
    return rooms[0] if rooms else None


def ambiance_of(project: Project, level_id: UUID, camera: Camera) -> str | None:
    """« Scandinave · chaleureux » : l'ambiance de la pièce où se trouve le point de vue."""
    room = _room_of(project, level_id, camera)
    if room is None or room.intent is None:
        return None
    intent = room.intent
    parts = [STYLES.get(intent.style, intent.style)]
    parts += [MOOD_LABELS.get(m, m) for m in intent.mood]
    return " · ".join(parts)


def _find_camera(project: Project, camera_id: UUID | None) -> tuple[UUID, Camera] | None:
    if camera_id is None:
        return None
    for level_id, level in project.levels.items():
        element = level.elements.get(camera_id)
        if isinstance(element, Camera):
            return level_id, element
    return None


# ------------------------------------------------------------------ métadonnées
def read_meta(render_dir: Path) -> GalleryMeta:
    path = render_dir / GALLERY_SIDECAR
    if not path.is_file():
        return GalleryMeta()
    try:
        return GalleryMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return GalleryMeta()


def write_meta(render_dir: Path, meta: GalleryMeta) -> None:
    tmp = render_dir / f"{GALLERY_SIDECAR}.{uuid4().hex}.tmp"
    tmp.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(render_dir / GALLERY_SIDECAR)


def note_render(
    render_dir: Path, project: Project, level_id: UUID, camera: Camera | None, variant: int | None
) -> None:
    """Au rendu : la vue (ensemble, zone, détail, à la main, photo, variante) et l'ambiance
    du moment, pour que la galerie les montre même si la scène change ensuite."""
    meta = read_meta(render_dir).model_copy(
        update={
            "view_group": view_group_of(camera, variant=variant),
            "ambiance": ambiance_of(project, level_id, camera) if camera is not None else None,
        }
    )
    write_meta(render_dir, meta)


def _image_name(entry: RenderEntry, variant: Variant) -> str | None:
    """Nom du fichier d'une variante, s'il existe pour ce rendu."""
    if variant == "blender":
        return entry.image
    if variant == "finished":
        return entry.finished.image if entry.finished else None
    if variant == "inspiration":
        return entry.inspiration.image if entry.inspiration else None
    return entry.catalogue.image if entry.catalogue else None


def _entry(project: Project, render_dir: Path, entry: RenderEntry) -> GalleryEntry:
    meta = read_meta(render_dir)
    group, ambiance = meta.view_group, meta.ambiance
    if group is None:  # rendu d'avant US2 : vue et ambiance relues dans la scène actuelle
        found = _find_camera(project, entry.camera_id)
        group = view_group_of(found[1] if found else None, variant=entry.variant)
        if found is not None:
            ambiance = ambiance_of(project, found[0], found[1])
    # Une image favorite disparue (finition refaite, fichier retiré) n'est plus montrée.
    favorites = [v for v in meta.favorites if _image_name(entry, v)]
    return GalleryEntry(
        **entry.model_dump(),
        title=meta.title,
        favorites=favorites,
        view_group=group,
        ambiance=ambiance,
    )


def gallery(project: Project, runs_dir: Path) -> list[GalleryEntry]:
    """Les rendus du projet, du plus récent au plus ancien, avec leurs métadonnées."""
    return [
        _entry(project, runs_dir / str(e.run_id) / "blender", e) for e in list_renders(runs_dir)
    ]


def _render_dir(runs_dir: Path, run_id: UUID) -> Path:
    render_dir = runs_dir / str(run_id) / "blender"
    if not (render_dir / RENDER_SIDECAR).is_file():
        msg = f"rendu introuvable : {run_id}"
        raise GalleryError(msg)
    return render_dir


def one(project: Project, runs_dir: Path, run_id: UUID) -> GalleryEntry:
    render_dir = _render_dir(runs_dir, run_id)
    found = next((e for e in list_renders(runs_dir) if e.run_id == run_id), None)
    if found is None:
        msg = f"rendu introuvable : {run_id}"
        raise GalleryError(msg)
    return _entry(project, render_dir, found)


_UNSET = object()


def update(
    project: Project,
    runs_dir: Path,
    run_id: UUID,
    *,
    title: str | object | None = _UNSET,
    favorites: list[Variant] | None = None,
) -> GalleryEntry:
    """Renomme (titre vide = nom du point de vue) et/ou fixe les images favorites."""
    render_dir = _render_dir(runs_dir, run_id)
    current = one(project, runs_dir, run_id)
    meta = read_meta(render_dir)
    changes: dict[str, object] = {}
    if title is not _UNSET:
        text = " ".join(str(title or "").split())[:MAX_TITLE]
        changes["title"] = text or None
    if favorites is not None:
        missing = [v for v in favorites if not _image_name(current, v)]
        if missing:
            msg = f"image absente pour ce rendu : {', '.join(missing)}"
            raise GalleryError(msg)
        changes["favorites"] = [v for v in VARIANTS if v in favorites]
    write_meta(render_dir, meta.model_copy(update=changes))
    return one(project, runs_dir, run_id)


# ------------------------------------------------------------------ corbeille douce
def trash(runs_dir: Path, run_id: UUID, *, now: datetime | None = None) -> None:
    """Met le rendu à la corbeille (tout le dossier du run) ; réversible par `restore`."""
    _render_dir(runs_dir, run_id)
    bin_dir = runs_dir / TRASH_DIR
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / str(run_id)
    if target.exists():
        shutil.rmtree(target)
    (runs_dir / str(run_id)).replace(target)
    stamp = (now or datetime.now(UTC)).isoformat()
    (target / DELETED_MARK).write_text(json.dumps({"deleted_at": stamp}) + "\n", "utf-8")


def restore(runs_dir: Path, run_id: UUID) -> None:
    source = runs_dir / TRASH_DIR / str(run_id)
    if not source.is_dir():
        msg = f"ce rendu n'est plus dans la corbeille (effacé pour de bon) : {run_id}"
        raise GalleryError(msg)
    target = runs_dir / str(run_id)
    if target.exists():
        msg = f"un rendu porte déjà cet identifiant : {run_id}"
        raise GalleryError(msg)
    (source / DELETED_MARK).unlink(missing_ok=True)
    source.replace(target)


def _deleted_at(folder: Path) -> datetime | None:
    try:
        raw = json.loads((folder / DELETED_MARK).read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(raw["deleted_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def purge(
    runs_dir: Path, *, older_than_s: float = TRASH_TTL_S, now: datetime | None = None
) -> list[UUID]:
    """Efface pour de bon les rendus mis à la corbeille depuis plus de `older_than_s`
    secondes (0 : tous) ; renvoie leurs identifiants."""
    bin_dir = runs_dir / TRASH_DIR
    if not bin_dir.is_dir():
        return []
    moment = now or datetime.now(UTC)
    gone: list[UUID] = []
    for folder in sorted(bin_dir.iterdir()):
        try:
            run_id = UUID(folder.name)
        except ValueError:
            continue
        at = _deleted_at(folder)
        if at is not None and (moment - at).total_seconds() < older_than_s:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        gone.append(run_id)
    return gone


def record_deleted(project_dir: Path, project: Project, run_id: UUID) -> bool:
    """Journal V2 : « annulé / rendu supprimé », rattaché à la demande qui avait produit ce
    rendu (chemin automatique) quand elle est au journal ; ni image ni chemin."""
    origin = next(
        (
            e
            for e in reversed(journal.read(project_dir))
            if run_id in e.run_ids and e.kind != "undo"
        ),
        None,
    )
    return journal.record(
        project_dir,
        project,
        JournalEvent(
            project_id=project.id,
            kind="undo",
            run_ids=[run_id],
            request_id=origin.id if origin else None,
            note="rendu supprimé",
        ),
    )


# ------------------------------------------------------------------ téléchargement
def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-").lower()[:60] or "rendu"


def favorites_zip(project: Project, runs_dir: Path) -> tuple[bytes, int]:
    """Les images favorites du projet en un zip (nom lisible par image) ; renvoie aussi
    leur nombre. `GalleryError` s'il n'y en a aucune."""
    buffer = io.BytesIO()
    count = 0
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for entry in gallery(project, runs_dir):
            for variant in entry.favorites:
                name = _image_name(entry, variant)
                if name is None:
                    continue
                path = runs_dir / str(entry.run_id) / "blender" / name
                if not path.is_file():
                    continue
                base = f"{_slug(entry.title or entry.camera_name)}-{VARIANT_LABELS[variant]}"
                arcname, n = f"{base}.png", 2
                while arcname in used:
                    arcname, n = f"{base}-{n}.png", n + 1
                used.add(arcname)
                archive.write(path, arcname)
                count += 1
    if count == 0:
        msg = "aucune image favorite : marquez-en d'abord d'une étoile"
        raise GalleryError(msg)
    return buffer.getvalue(), count
