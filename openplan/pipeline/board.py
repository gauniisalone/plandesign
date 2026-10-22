"""Tableau du projet (B1) : lecture, écriture, mise en page de départ et synchronisation avec
ce que le projet contient (photos, rendus, images d'inspiration sans plan, plan, demande), et
fichiers joints.

Le tableau vit dans `board.json`, les fichiers joints dans `media/` : jamais dans
`project.json`, pour qu'aucune sauvegarde de la disposition n'écrase la géométrie (et
inversement). Ce que le projet ajoute (une photo, un rendu) entre dans son cadre ; ce que
l'utilisateur a retiré n'y revient pas (`dismissed`)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from openplan.pipeline import board_links as bl
from openplan.pipeline.comments import CommentStore
from openplan.pipeline.mainroom import main_room
from openplan.pipeline.reactions import ReactionStore
from openplan.pipeline.render import RenderEntry, list_renders
from openplan.schemas.board import (
    MAX_ITEMS,
    Board,
    BoardItem,
    BoardPatch,
    BoardRole,
    MediaKind,
    MediaRef,
)
from openplan.schemas.comments import CommentPoint
from openplan.schemas.common import now_utc
from openplan.schemas.scene import Project

BOARD_FILE = "board.json"
MEDIA_DIR = "media"
MEDIA_INDEX = "index.json"
MAX_MEDIA_BYTES = 50 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
THUMB_SIDE = 480
AUTO_KINDS = ("photo", "render", "plan", "file", "video", "pdf", "contextual")
"""Ce que le projet fournit : retiré à la main, noté dans `dismissed`."""
FIXED_FRAMES: dict[str, str] = {
    "photos": "Photos de la pièce",
    "inspirations": "Inspirations",
    "renders": "Rendus",
}
"""Cadres toujours présents sur un tableau (T2), même vides : les photos réelles de la pièce
(elles font le plan) et les images d'ambiance (jointes, cochées Référence). Retirés à la
main, ils ne reviennent pas (`dismissed` : `frame:<rôle>`)."""

# Mise en page de départ (pixels du tableau, maquette d'augus du 16 octobre) : à gauche les
# cadres Photos de la pièce puis Inspirations, au centre le plan, grand, à droite les Rendus.
GAP = 48.0
PAD = 24.0
PAD_TOP = 160.0
"""T7b : le titre des cadres posés par le tableau est dans un en-tête, comme celui du plan ;
leur contenu commence sous lui (l'en-tête grandit quand on dézoome). KAN-77 : 160 (au lieu de
88), l'en-tête ayant 16 px de marge à l'écran : il ne couvre pas le contenu jusqu'au zoom de
départ (≈ 34 %). Les tableaux déjà posés gardent leur place."""
CELL_W, CELL_H = 280.0, 190.0
COLS = 2
FRAME_W = PAD * 2 + COLS * CELL_W + (COLS - 1) * 16.0
NOTE_W, NOTE_H = 300.0, 170.0
PLAN_X, PLAN_W, PLAN_H = FRAME_W + 2 * GAP, 900.0, 700.0
FILE_W, FILE_H = 220.0, 72.0
INSPIRATION_DIR = "inspiration"

_INLINE = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF8",
    "image/webp": b"RIFF",
}
"""Images montrées telles quelles, si leurs premiers octets le confirment ; tout le reste
est servi en pièce jointe, comme des octets."""


class MediaError(ValueError):
    """Fichier joint refusé (vide, trop gros) ou introuvable."""


# ------------------------------------------------------------------ lecture, écriture
def load_board(project_dir: Path) -> Board | None:
    path = project_dir / BOARD_FILE
    if not path.is_file():
        return None
    return Board.model_validate_json(path.read_text(encoding="utf-8"))


class StaleBoardError(ValueError):
    """Un envoi parti d'une révision plus ancienne que celle enregistrée (deux onglets)."""

    def __init__(self, sent: int, current: int) -> None:
        super().__init__(
            f"l'espace de travail a changé ailleurs (révision {current}, envoyée {sent}) : "
            "rechargez-le"
        )
        self.current = current


def save_board(project_dir: Path, board: Board, *, expect: int | None = None) -> Board:
    """Écriture atomique ; la révision avance d'un cran à chaque sauvegarde. `expect` : la
    révision sur laquelle l'envoi s'appuie, refusé si une autre sauvegarde est passée avant."""
    current = load_board(project_dir)
    revision = current.revision if current is not None else 0
    if expect is not None and current is not None and expect != revision:
        raise StaleBoardError(expect, revision)
    board = board.model_copy(update={"updated_at": now_utc(), "revision": revision + 1})
    project_dir.mkdir(parents=True, exist_ok=True)
    tmp = project_dir / f"{BOARD_FILE}.{uuid4().hex}.tmp"
    tmp.write_text(board.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(project_dir / BOARD_FILE)
    if current is not None:
        _detach_comments(project_dir, current, board)
        # KAN-78 : un élément retiré emporte ses réactions.
        kept = {i.id for i in board.items}
        ReactionStore(project_dir).forget(i.id for i in current.items if i.id not in kept)
    return board


_STAMPS = {"updated_at", "updated_by"}
_COLOR = {*_STAMPS, "color"}


@dataclass(frozen=True)
class BoardChanges:
    """KAN-58 : ce qu'un geste a fait sur l'espace de travail (la vue n'en fait pas partie)."""

    added: int
    changed: int
    removed: int
    single: BoardItem | None
    """L'élément touché, s'il n'y en a qu'un (tel qu'il est après le geste, ou avant s'il a été
    retiré)."""
    recolored: int = 0
    """KAN-65 : parmi les éléments modifiés, ceux dont seule la couleur a changé."""

    @property
    def total(self) -> int:
        return self.added + self.changed + self.removed


def board_changes(before: Board | None, after: Board) -> BoardChanges:
    old = {i.id: i for i in (before.items if before else [])}
    new = {i.id: i for i in after.items}
    added = [new[k] for k in new.keys() - old.keys()]
    removed = [old[k] for k in old.keys() - new.keys()]
    changed = [
        new[k]
        for k in new.keys() & old.keys()
        if new[k].model_dump(exclude=_STAMPS) != old[k].model_dump(exclude=_STAMPS)
    ]
    recolored = [
        i
        for i in changed
        if i.color != old[i.id].color
        and i.model_dump(exclude=_COLOR) == old[i.id].model_dump(exclude=_COLOR)
    ]
    touched = [*added, *changed, *removed]
    return BoardChanges(
        added=len(added),
        changed=len(changed),
        removed=len(removed),
        single=touched[0] if len(touched) == 1 else None,
        recolored=len(recolored),
    )


def _detach_comments(project_dir: Path, before: Board, after: Board) -> None:
    """KAN-51 : un élément retiré laisse sa dernière position aux fils qui y sont épinglés ;
    ils restent là, « détachés ». Le seul moment où cette position s'enregistre hors d'une
    écriture de fil (la liste des commentaires, elle, n'écrit rien)."""
    kept = {i.id for i in after.items}
    gone = {i.id: CommentPoint(x=i.x, y=i.y) for i in before.items if i.id not in kept}
    if not gone:
        return
    comments_store = CommentStore(project_dir)
    comments = comments_store.load()
    changed = False
    for thread in comments.threads:
        item = thread.target.item_id
        if item in gone and thread.target.last != gone[item]:
            thread.target.last = gone[item]
            changed = True
    if changed:
        comments_store.save(comments)


def replaced(before: Board | None, after: Board) -> Board:
    """Un tableau reçu du client : ce qu'il a retiré parmi ce que le projet fournit (photo,
    rendu, plan, note de la demande) est noté, pour que la synchronisation ne le remette pas."""
    if before is None:
        return after.model_copy(update={"items": bl.with_membership(after.items)})
    # KAN-91 : un onglet d'avant les liens n'en envoie pas ; ceux d'avant restent (moins ceux
    # dont une carte a disparu), et `reference` suit l'appartenance.
    after = after.model_copy(
        update={
            "items": bl.with_membership(after.items),
            "links": bl.auto_links(after.items, before.links, before.cut_links)
            if not after.links
            else after.links,
            "cut_links": list(dict.fromkeys([*before.cut_links, *after.cut_links])),
        }
    )
    kept = {_key(i) for i in after.items}
    gone = [
        _key(i)
        for i in before.items
        if (i.kind in AUTO_KINDS or i.role == "prompt" or _key(i).startswith("frame:"))
        and _key(i) not in kept
    ]
    dismissed = list(dict.fromkeys([*after.dismissed, *(k for k in gone if k)]))
    return after.model_copy(update={"dismissed": dismissed})


class TooManyItemsError(ValueError):
    """Un geste qui porterait le tableau au-delà de `MAX_ITEMS` éléments."""


def patched(before: Board | None, patch: BoardPatch, actor: str) -> Board:
    """B2c : applique les écritures d'un geste (dernière écriture gagne, élément par élément).
    Chaque élément écrit reçoit l'heure du serveur et son acteur. Ce qui est retiré parmi ce
    que le projet fournit est noté dans `dismissed`, comme pour un tableau entier ; un élément
    dont le cadre a disparu n'est plus dans un cadre."""
    board = before if before is not None else Board()
    stamp = now_utc()
    items = {i.id: i for i in board.items}
    gone = [items.pop(i) for i in patch.delete if i in items]
    previous = {i.id: i.frame for i in board.items}
    for item in patch.set:
        items[item.id] = item.model_copy(update={"updated_at": stamp, "updated_by": actor})
    if len(items) > MAX_ITEMS:
        msg = f"l'espace de travail est limité à {MAX_ITEMS} éléments"
        raise TooManyItemsError(msg)
    # KAN-91 (C3) : une carte métier n'accepte que ce qu'elle sait lire.
    for item in patch.set:
        if item.frame is not None and item.frame != previous.get(item.id):
            reason = bl.accepts(items.get(item.frame), item)
            if reason is not None:
                raise bl.LinkError(reason)
    frames = {i.id for i in items.values() if i.kind == "frame"}
    kept = bl.with_membership(
        i if i.frame is None or i.frame in frames else i.model_copy(update={"frame": None})
        for i in items.values()
    )
    links, cut = bl.written_links(board, kept, patch.links_set, patch.links_delete, actor, stamp)
    # Retiré pour de bon seulement si plus aucune carte ne montre la même chose (une copie
    # « Dupliquer », ou la carte du client qui remplace le doublon de la synchronisation).
    remaining = {_key(i) for i in kept}
    dismissed = [
        _key(i)
        for i in gone
        if i.id not in items
        and _key(i) not in remaining
        and (i.kind in AUTO_KINDS or i.role == "prompt" or _key(i).startswith("frame:"))
    ]
    return board.model_copy(
        update={
            "items": kept,
            "view": patch.view or board.view,
            "dismissed": list(dict.fromkeys([*board.dismissed, *(k for k in dismissed if k)])),
            "links": links,
            "cut_links": cut,
        }
    )


def _key(item: BoardItem) -> str:
    if item.role == "prompt":
        return "prompt"
    if item.kind == "frame" and item.role == "renders" and item.ref:
        # T7e : le cadre Rendus d'un plan autre que le premier.
        return f"frame:renders:{item.ref}"
    if item.kind == "frame" and item.role in FIXED_FRAMES:
        return f"frame:{item.role}"
    return f"{item.kind}:{item.ref}" if item.ref else ""


def renders_title(project: Project, level_id: str) -> str:
    """« Rendus » pour un projet à un seul plan (comme avant T7e), « Rendus · nom » sinon."""
    try:
        level = project.levels.get(UUID(level_id))
    except ValueError:
        level = None
    if len(project.levels) < 2 or level is None:
        return FIXED_FRAMES["renders"]
    return f"{FIXED_FRAMES['renders']} · {level.name}"


def _level_of(project: Project, entry: RenderEntry) -> str | None:
    """Le plan d'un rendu (T7e) : écrit dans le rendu, sinon celui de sa caméra ; None si
    aucun (variante d'aménagement d'avant T7e, caméra supprimée)."""
    if entry.level_id is not None and entry.level_id in project.levels:
        return str(entry.level_id)
    if entry.camera_id is not None:
        for lid, level in project.levels.items():
            if entry.camera_id in level.elements:
                return str(lid)
    return None


# ------------------------------------------------------------------ ce que le projet fournit
@dataclass(frozen=True)
class Provided:
    photos: list[str]
    renders: list[tuple[str, str]]
    """(run, titre) : le dernier rendu de chaque vue, du plus ancien au plus récent."""
    render_levels: dict[str, str]
    """T7e : run → plan (niveau) du rendu ; absent = le premier plan."""
    inspirations: list[str]
    """Images d'inspiration sans plan (rendu de secours, A2)."""
    plans: list[tuple[str, str]]
    """(niveau, nom)."""
    prompt: str | None
    latest: dict[str, str]
    """Run → dernier run de la même vue (un rendu refait remplace l'ancien à sa place)."""
    contextual: list[tuple[str, str]] = field(default_factory=list)
    """(run:variante, titre) : rendus contextualisés (A1), du plus ancien au plus récent."""


def provided(project: Project, runs_dir: Path) -> Provided:
    from openplan.pipeline.contextual import VARIANT_LABELS, list_entries

    contextual = [
        (f"{e.run_id}:{e.variant}", f"{VARIANT_LABELS[e.variant]} · {e.source.label}".strip(" ·"))
        for e in reversed(list_entries(runs_dir))
    ]
    entries = list_renders(runs_dir) if runs_dir.is_dir() else []
    by_view: dict[str, RenderEntry] = {}
    for entry in sorted(entries, key=lambda e: e.created_at):
        by_view[str(entry.camera_id or entry.run_id)] = entry
    latest = {str(e.run_id): str(by_view[str(e.camera_id or e.run_id)].run_id) for e in entries}
    inspirations = sorted(
        (
            d.name
            for d in (runs_dir.iterdir() if runs_dir.is_dir() else [])
            if (d / INSPIRATION_DIR / "inspiration.png").is_file() and not (d / "blender").is_dir()
        ),
        key=lambda name: (runs_dir / name).stat().st_mtime,
    )
    room = None
    for level in project.levels.values():
        room = main_room(level)
        if room is not None:
            break
    prompt = project.pending_instruction or (
        room.intent.instruction if room is not None and room.intent is not None else None
    )
    return Provided(
        photos=[str(i) for i in project.images],
        renders=[(str(e.run_id), e.camera_name) for e in by_view.values()],
        render_levels={
            str(e.run_id): lid for e in by_view.values() if (lid := _level_of(project, e))
        },
        inspirations=inspirations,
        plans=[(str(lid), level.name) for lid, level in project.levels.items()],
        prompt=prompt or None,
        latest=latest,
        contextual=contextual,
    )


# ------------------------------------------------------------------ synchronisation
def board_kind(media: MediaRef) -> str:
    """L'élément du tableau d'un fichier joint (C1) : vidéo lisible, PDF, sinon carte."""
    return {"video": "video", "pdf": "pdf"}.get(media.kind, "file")


def sync_board(  # noqa: PLR0912 - une sorte d'élément par branche
    board: Board | None, project: Project, runs_dir: Path, media: list[MediaRef] | None = None
) -> tuple[Board, bool]:
    """Le tableau à jour : mise en page de départ s'il n'existe pas ; sinon, un rendu refait
    prend la place de l'ancien, ce qui a disparu du projet quitte le tableau, ce qui est
    nouveau entre dans son cadre (sauf ce que l'utilisateur a retiré), fichiers joints compris
    (C1 : joints par l'API, ils entrent dans le cadre Fichiers). Renvoie le tableau et s'il a
    changé."""
    have = provided(project, runs_dir)
    joined = [(board_kind(m), str(m.id), m.name) for m in media or []]
    fresh = board is None
    current = board or Board()
    items = list(current.items)
    dismissed = set(current.dismissed)

    # Un rendu refait remplace l'ancien, à la même place.
    for index, item in enumerate(items):
        if item.kind == "render" and item.role != "inspiration" and item.ref in have.latest:
            newest = have.latest[item.ref]
            if newest != item.ref:
                items[index] = item.model_copy(update={"ref": newest})

    # Ce qui a disparu du projet (photo supprimée, rendu mis à la corbeille) s'en va.
    alive = (
        {f"photo:{p}" for p in have.photos}
        | {f"render:{r}" for r, _ in have.renders}
        | {f"render:{r}" for r in have.inspirations}
        | {f"plan:{lid}" for lid, _ in have.plans}
        | {f"{kind}:{mid}" for kind, mid, _ in joined}
        | {f"contextual:{ref}" for ref, _ in have.contextual}
    )
    items = [i for i in items if i.kind not in AUTO_KINDS or _key(i) in alive]
    # T7e : le cadre Rendus d'un plan disparu (annulé) s'en va avec ce qu'il contient ; ses
    # rendus reviennent dans le cadre du premier plan.
    lids = {lid for lid, _ in have.plans}
    gone = {i.id for i in items if i.kind == "frame" and i.role == "renders" and i.ref}
    gone = {fid for fid in gone if next(i for i in items if i.id == fid).ref not in lids}
    items = [i for i in items if i.id not in gone and i.frame not in gone]

    present = {_key(i) for i in items}
    added = _Adder(items)
    first = have.plans[0][0] if have.plans else None
    for lid, name in have.plans:
        if f"plan:{lid}" not in present | dismissed:
            added.plan(lid, name)
    # T7b : les exigences vivent dans le compositeur flottant, plus sur le tableau. La note
    # Exigences (T1) et l'ancienne note de la demande (B1) s'en vont ; ce qui était posé
    # dessous, dans la colonne de gauche, remonte à leur place. Leur contenu reste dans le
    # projet (`pending_instruction`, `pending_answers`).
    for note in [i for i in added.items if i.kind == "requirements" or i.role == "prompt"]:
        added.lift(note)
    added.make_room_for_heads()
    # T2 : les cadres Photos de la pièce et Inspirations existent dès le départ, même vides.
    for role, title in FIXED_FRAMES.items():
        if role != "renders" and f"frame:{role}" not in present | dismissed:
            added.frame(role, title)  # type: ignore[arg-type]
    # T7e : une rangée par plan, son cadre « Rendus · nom » à droite du plan ; le premier plan
    # garde le cadre Rendus d'avant (un projet à un plan ne change pas).
    for lid, _ in have.plans:
        key = "frame:renders" if lid == first else f"frame:renders:{lid}"
        if key not in present | dismissed:
            added.renders_frame(lid, renders_title(project, lid), first=lid == first)
        else:
            added.retitle(lid, renders_title(project, lid), first=lid == first)
    for photo in have.photos:
        if f"photo:{photo}" not in present | dismissed:
            added.into("photos", FIXED_FRAMES["photos"], "photo", photo, "")
    for run, title in have.renders:
        if f"render:{run}" not in present | dismissed:
            into = have.render_levels.get(run, first)
            added.into_renders(into, first, renders_title(project, into or ""), run, title)
    for run in have.inspirations:
        if f"render:{run}" not in present | dismissed:
            added.into_renders(
                first,
                first,
                renders_title(project, first or ""),
                run,
                "inspiration, sans plan",
                "inspiration",
            )
    # Un fichier joint posé sur le tableau à la main garde sa place et sa sorte d'élément.
    placed_media = {i.ref for i in added.items if i.kind in ("file", "video", "pdf")}
    for kind, mid, name in joined:
        if mid not in placed_media and f"{kind}:{mid}" not in dismissed:
            added.into("files", "Fichiers", kind, mid, name)
    # Rendus contextualisés (A1) : dans le cadre Moodboard, avec leur variante.
    for ref, title in have.contextual:
        if f"contextual:{ref}" not in present | dismissed:
            added.into("moodboard", "Moodboard", "contextual", ref, title)

    # KAN-91 : migration douce. Une image cochée « Référence » hors de toute carte métier (avant
    # les cartes reliées) entre dans la carte Inspirations : elle continue de guider les rendus,
    # et « Référence » ne se décide plus que par l'appartenance.
    by_id = {i.id: i for i in added.items}
    for item in list(added.items):
        if (
            item.reference
            and item.kind in bl.IMAGE_KINDS
            and bl.card_role(by_id.get(item.frame) if item.frame else None) is None
            and "frame:inspirations" not in dismissed
        ):
            added.move_into(item, "inspirations", FIXED_FRAMES["inspirations"])
    items = bl.with_membership(added.items)
    links = bl.auto_links(items, list(current.links), list(current.cut_links))
    changed = fresh or items != list(current.items) or links != list(current.links)
    return current.model_copy(update={"items": items, "links": links}), changed


class _Adder:
    """Pose les nouveaux éléments : dans leur cadre (créé au besoin, agrandi d'une rangée
    quand il est plein), la note sous les cadres, le plan à droite."""

    def __init__(self, items: list[BoardItem]) -> None:
        self.items = items

    def frame(self, role: BoardRole, title: str) -> None:
        """Un cadre nommé, créé s'il manque (T2 : cadres fixes, même vides)."""
        self._frame(role, title)

    def _find_renders(self, level_id: str | None, *, first: bool) -> BoardItem | None:
        """Le cadre Rendus d'un plan : `ref` = son niveau ; celui du premier plan peut être le
        cadre d'avant T7e, sans `ref`."""
        return next(
            (
                i
                for i in self.items
                if i.kind == "frame"
                and i.role == "renders"
                and (i.ref == level_id or (first and i.ref is None))
            ),
            None,
        )

    def renders_frame(self, level_id: str | None, title: str, *, first: bool) -> BoardItem:
        """T4 (maquette), T7e : le cadre Rendus d'un plan, à droite du plan, à sa hauteur."""
        found = self._find_renders(level_id, first=first)
        if found is not None:
            return found
        plan = next((i for i in self.items if i.kind == "plan" and i.ref == level_id), None)
        if plan is not None:
            right, top = plan.x + plan.w + GAP, plan.y
        else:
            plans = [i for i in self.items if i.kind == "plan"]
            right = max((i.x + i.w for i in plans), default=PLAN_X + PLAN_W) + GAP
            top = min((i.y for i in plans), default=0.0)
        frame = BoardItem(
            kind="frame",
            x=right,
            y=top,
            w=FRAME_W,
            h=PLAN_H,
            text=title,
            role="renders",
            ref=None if first else level_id,
            z=-1,
        )
        self.items.append(frame)
        return frame

    def retitle(self, level_id: str | None, title: str, *, first: bool) -> None:
        """Le titre suit le nom du plan, tant qu'il est celui que le tableau a donné."""
        found = self._find_renders(level_id, first=first)
        if found is None or found.text == title:
            return
        if found.text != FIXED_FRAMES["renders"] and not found.text.startswith(
            f"{FIXED_FRAMES['renders']} · "
        ):
            return
        self.items[self.items.index(found)] = found.model_copy(update={"text": title})

    def into_renders(
        self,
        level_id: str | None,
        first: str | None,
        title: str,
        ref: str,
        text: str,
        item_role: BoardRole | None = None,
    ) -> None:
        frame = self.renders_frame(level_id, title, first=level_id == first)
        self.into("renders", title, "render", ref, text, item_role, frame=frame)

    def _frame(self, role: BoardRole, title: str) -> BoardItem:
        found = next((i for i in self.items if i.kind == "frame" and i.role == role), None)
        if found is not None:
            return found
        frame = BoardItem(
            kind="frame",
            x=0.0,
            y=self._left_bottom(),
            w=FRAME_W,
            h=PAD_TOP + PAD + CELL_H,
            text=title,
            role=role,
            z=-1,
        )
        self.items.append(frame)
        return frame

    def _left_bottom(self) -> float:
        left = [i for i in self.items if i.x < PLAN_X - GAP and i.frame is None]
        return max((i.y + i.h for i in left), default=-GAP) + GAP

    def into(
        self,
        role: BoardRole,
        title: str,
        kind: str,
        ref: str,
        text: str,
        item_role: BoardRole | None = None,
        *,
        frame: BoardItem | None = None,
    ) -> None:
        frame = frame or self._frame(role, title)
        x, y, frame = self._slot(frame)
        self.items.append(
            BoardItem(
                kind=kind,  # type: ignore[arg-type]
                x=x,
                y=y,
                w=CELL_W,
                h=CELL_H,
                frame=frame.id,
                ref=ref,
                text=text,
                role=item_role,
            )
        )

    def _slot(self, frame: BoardItem) -> tuple[float, float, BoardItem]:
        """La case libre suivante du cadre (il grandit d'une rangée s'il est plein)."""
        inside = sum(1 for i in self.items if i.frame == frame.id)
        row, col = divmod(inside, COLS)
        x = frame.x + PAD + col * (CELL_W + 16.0)
        y = frame.y + PAD_TOP + row * (CELL_H + 16.0)
        need = PAD_TOP + PAD + (row + 1) * CELL_H + row * 16.0
        if frame.h < need:
            grown = frame.model_copy(update={"h": need})
            self._push_below(frame, need - frame.h)
            self.items[self.items.index(frame)] = grown
            frame = grown
        return x, y, frame

    def move_into(self, item: BoardItem, role: BoardRole, title: str) -> None:
        """KAN-91 : un élément déjà posé entre dans un cadre (à sa prochaine case)."""
        frame = self._frame(role, title)
        x, y, frame = self._slot(frame)
        index = next(k for k, i in enumerate(self.items) if i.id == item.id)
        self.items[index] = self.items[index].model_copy(update={"x": x, "y": y, "frame": frame.id})

    def _push_below(self, frame: BoardItem, dy: float) -> None:
        """Un cadre qui grandit pousse ce qui est posé dessous, dans la même colonne ; à droite
        de la colonne des cadres (T7e), les rangées suivantes entières (plan et ses rendus)."""
        bottom = frame.y + frame.h
        right = frame.x >= PLAN_X - GAP
        moved = {
            i.id
            for i in self.items
            if i.id != frame.id
            and i.frame is None
            and i.y >= bottom
            and (i.x + i.w > PLAN_X - GAP if right else i.x < frame.x + frame.w)
        }
        self.items[:] = [
            i.model_copy(update={"y": i.y + dy}) if i.id in moved or i.frame in moved else i
            for i in self.items
        ]

    def make_room_for_heads(self) -> None:
        """Tableaux d'avant T7b : le contenu des cadres posés par le tableau commençait à 24 px
        du haut ; il descend sous l'en-tête, le cadre grandit d'autant et pousse ce qui est
        dessous."""
        for frame in [i for i in self.items if i.kind == "frame" and i.role is not None]:
            inside = [i for i in self.items if i.frame == frame.id]
            if not inside:
                continue
            top = min(i.y for i in inside)
            # Seulement la mise en page d'avant (contenu à 24 px du haut), jamais un élément
            # qu'on aurait déplacé à la main.
            if abs(top - (frame.y + PAD)) > 1.0:
                continue
            delta = PAD_TOP - PAD
            self.items[:] = [
                i.model_copy(update={"y": i.y + delta}) if i.frame == frame.id else i
                for i in self.items
            ]
            grown = frame.model_copy(update={"h": frame.h + delta})
            self._push_below(frame, delta)
            self.items[self.items.index(frame)] = grown

    def lift(self, gone: BoardItem) -> None:
        """Retire `gone` ; ce qui était posé sous lui dans la même colonne (et le contenu des
        cadres déplacés) remonte de sa hauteur."""
        self.items[:] = [i for i in self.items if i.id != gone.id]
        bottom = gone.y + gone.h
        dy = gone.h + GAP
        moved = {
            i.id
            for i in self.items
            if i.frame is None and i.y >= bottom - 1e-6 and i.x < gone.x + gone.w
        }
        self.items[:] = [
            i.model_copy(update={"y": i.y - dy}) if i.id in moved or i.frame in moved else i
            for i in self.items
        ]

    def plan(self, level_id: str, name: str) -> None:
        """T7e : un plan de plus ouvre une rangée sous la précédente (sous le plus bas des plans
        et de leurs cadres Rendus)."""
        row = [
            i for i in self.items if i.kind == "plan" or (i.kind == "frame" and i.role == "renders")
        ]
        y = max((i.y + i.h for i in row), default=-GAP) + GAP
        self.items.append(
            BoardItem(kind="plan", x=PLAN_X, y=y, w=PLAN_W, h=PLAN_H, ref=level_id, text=name)
        )


# ------------------------------------------------------------------ fichiers joints
_INDEX = TypeAdapter(list[MediaRef])


def media_dir(project_dir: Path) -> Path:
    return project_dir / MEDIA_DIR


def list_media(project_dir: Path) -> list[MediaRef]:
    path = media_dir(project_dir) / MEDIA_INDEX
    if not path.is_file():
        return []
    return _INDEX.validate_json(path.read_text(encoding="utf-8"))


def _write_index(project_dir: Path, refs: list[MediaRef]) -> None:
    directory = media_dir(project_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{MEDIA_INDEX}.{uuid4().hex}.tmp"
    tmp.write_text(json.dumps(_INDEX.dump_python(refs, mode="json"), indent=2) + "\n")
    tmp.replace(directory / MEDIA_INDEX)


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip() or "fichier"
    return base[:200]


def media_type_of(data: bytes) -> str:
    """Le type servi : une image courante, une vidéo (mp4, webm) ou un PDF si ses premiers
    octets le confirment, sinon des octets (jamais du HTML, du SVG ni un script interprétés
    par le navigateur)."""
    for kind, magic in _INLINE.items():
        if data.startswith(magic) and (kind != "image/webp" or data[8:12] == b"WEBP"):
            return kind
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return "application/octet-stream"


def media_kind_of(media_type: str) -> MediaKind:
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("video/"):
        return "video"
    return "pdf" if media_type == "application/pdf" else "file"


def max_bytes(kind: MediaKind) -> int:
    """Une vidéo jusqu'à 200 Mo (C1), tout autre fichier jusqu'à 50 Mo."""
    return MAX_VIDEO_BYTES if kind == "video" else MAX_MEDIA_BYTES


def _pdf_thumb(pdf: Path, out: Path) -> bool:
    """Vignette de la première page d'un PDF, rendue sur la machine (pdfplumber, qui
    s'appuie sur pypdfium2, BSD-3 / Apache-2.0). Faux si le fichier ne se lit pas."""
    try:
        import pdfplumber

        with pdfplumber.open(pdf) as doc:
            if not doc.pages:
                return False
            page = doc.pages[0]
            scale = THUMB_SIDE / max(float(page.width), float(page.height), 1.0)
            image = page.to_image(resolution=max(10, int(72 * scale))).original
            image.convert("RGB").save(out, format="PNG")
        return True
    except Exception:
        return False


def add_media(project_dir: Path, data: bytes, name: str) -> MediaRef:
    if not data:
        msg = "fichier vide"
        raise MediaError(msg)
    media_type = media_type_of(data)
    kind = media_kind_of(media_type)
    limit = max_bytes(kind)
    if len(data) > limit:
        msg = (
            f"fichier trop gros ({len(data) // (1024 * 1024)} Mo, "
            f"{limit // (1024 * 1024)} Mo au plus)"
        )
        raise MediaError(msg)
    safe = _safe_name(name)
    suffix = Path(safe).suffix.lower()
    suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""
    ref = MediaRef(name=safe, media_type=media_type, size=len(data), path="", kind=kind)
    relative = f"{MEDIA_DIR}/{ref.id}{suffix}"
    ref = ref.model_copy(update={"path": relative})
    target = project_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    if kind == "pdf":
        thumb = f"{MEDIA_DIR}/{ref.id}.thumb.png"
        if _pdf_thumb(target, project_dir / thumb):
            ref = ref.model_copy(update={"thumb": thumb})
    _write_index(project_dir, [*list_media(project_dir), ref])
    return ref


def media_thumb(project_dir: Path, media_id: UUID) -> Path:
    ref, _ = media_file(project_dir, media_id)
    path = project_dir / ref.thumb if ref.thumb else None
    if path is None or not path.is_file():
        msg = f"pas de vignette pour ce fichier : {media_id}"
        raise MediaError(msg)
    return path


def media_file(project_dir: Path, media_id: UUID) -> tuple[MediaRef, Path]:
    ref = next((m for m in list_media(project_dir) if m.id == media_id), None)
    path = (project_dir / ref.path) if ref is not None else None
    if ref is None or path is None or not path.is_file():
        msg = f"fichier joint introuvable : {media_id}"
        raise MediaError(msg)
    return ref, path


ARCHIVE_TOP = ("project.json", BOARD_FILE, "comments.json", "reactions.json")
"""KAN-51 : les fils de commentaires suivent le projet ; un auteur local y reste « local »,
sans donnée personnelle (« Utilisateur local » une fois en ligne). KAN-78 : les réactions
aussi."""
ARCHIVE_DIRS = ("images", MEDIA_DIR)
ARCHIVE_RUN_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".json")
"""Des rendus, les images et leurs réglages ; pas les fichiers de travail (scènes,
profondeurs, cartes) ni la corbeille."""


def project_archive(project_dir: Path) -> bytes:
    """Sauvegarde complète en zip, chemins relatifs au dossier du projet : `project.json`
    (plan, historique), `board.json` (tableau), photos, fichiers joints, images des rendus."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ARCHIVE_TOP:
            if (project_dir / name).is_file():
                archive.write(project_dir / name, name)
        for folder in ARCHIVE_DIRS:
            for path in sorted((project_dir / folder).rglob("*")):
                if path.is_file() and not path.name.endswith(".tmp"):
                    archive.write(path, path.relative_to(project_dir).as_posix())
        runs = project_dir / "runs"
        for path in sorted(runs.rglob("*")) if runs.is_dir() else []:
            relative = path.relative_to(project_dir)
            if (
                path.is_file()
                and path.suffix.lower() in ARCHIVE_RUN_SUFFIXES
                and not any(part.startswith(".") for part in relative.parts)
                and not path.name.endswith(".tmp")
            ):
                archive.write(path, relative.as_posix())
    return buffer.getvalue()


def remove_media(project_dir: Path, media_id: UUID) -> None:
    ref, path = media_file(project_dir, media_id)
    path.unlink(missing_ok=True)
    if ref.thumb:
        (project_dir / ref.thumb).unlink(missing_ok=True)
    _write_index(project_dir, [m for m in list_media(project_dir) if m.id != ref.id])
