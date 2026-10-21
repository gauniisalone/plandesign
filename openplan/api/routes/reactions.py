"""Réactions aux éléments de l'espace de travail (KAN-78) : six emojis fixes, une réaction par
personne, par élément et par emoji. Lire : lecteur ; réagir : commentateur et plus (table des
gardes) ; on ne retire que sa propre réaction (la route ne touche qu'à celle de l'appelant).
Même code en local et avec les comptes.

Une réaction ajoutée entre au journal du projet (`reaction.added`, KAN-58) avec l'emoji, le nom
de l'élément et son auteur : la cloche (KAN-52) prévient cet auteur, jamais celui qui réagit.
Retirer sa réaction n'est pas un geste du journal."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.api.routes.comments import YOU, AuthorNames, author_of
from openplan.pipeline import board as b
from openplan.pipeline.events import ProjectEvents
from openplan.pipeline.reactions import ReactionStore
from openplan.schemas.board import BoardItem
from openplan.schemas.common import StrictModel
from openplan.schemas.events import MAX_LABEL, ProjectEvent
from openplan.schemas.reactions import EMOJI, Reaction, ReactionKey

router = APIRouter(tags=["réactions"])


class ReactionOut(StrictModel):
    """Une pastille : un emoji sur un élément, combien, si j'en suis, et qui (« Vous » d'abord)."""

    item_id: UUID
    key: ReactionKey
    emoji: str
    count: int
    mine: bool
    names: list[str]


def _summary(
    store: ReactionStore, names: AuthorNames, me: str, items: set[UUID]
) -> list[ReactionOut]:
    order = list(EMOJI)
    grouped: dict[tuple[UUID, str], list[Reaction]] = {}
    for r in store.load().reactions:
        if r.item_id in items:
            grouped.setdefault((r.item_id, r.key), []).append(r)
    out = []
    for (item_id, key), group in sorted(
        grouped.items(), key=lambda kv: (str(kv[0][0]), order.index(kv[0][1]))
    ):
        group.sort(key=lambda r: r.at)
        mine = any(r.author == me for r in group)
        who = [names(r.author)[0] for r in group if r.author != me]
        out.append(
            ReactionOut(
                item_id=item_id,
                key=key,  # type: ignore[arg-type]
                emoji=EMOJI[key],
                count=len(group),
                mine=mine,
                names=([YOU] if mine else []) + who,
            )
        )
    return out


def _items(project_dir: Path) -> dict[UUID, BoardItem]:
    board = b.load_board(project_dir)
    return {i.id: i for i in board.items} if board else {}


def _item_or_404(project_dir: Path, item_id: UUID) -> tuple[BoardItem, dict[UUID, BoardItem]]:
    items = _items(project_dir)
    item = items.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="élément inconnu de l'espace de travail")
    return item, items


def item_author(project_dir: Path, item: BoardItem) -> str | None:
    """Qui prévenir d'une réaction (décision PO du 21 oct.) : le lanceur d'un rendu, sinon
    celui qui a posé l'élément (d'après le journal), sinon le dernier qui l'a écrit ; None : le
    propriétaire du projet."""
    events = ProjectEvents(project_dir).read()
    if item.kind in ("render", "contextual") and item.ref:
        run = item.ref
        for event in events:
            if (
                event.type == "render.done"
                and event.run_id is not None
                and str(event.run_id) == run
            ):
                return event.actor
    for event in events:
        if event.type == "board.changed" and event.item_id == item.id and event.added:
            return event.actor
    if item.updated_by:
        return author_of(item.updated_by)
    return None


def _label(item: BoardItem) -> str | None:
    text = " ".join((item.text or "").split())
    return text[:MAX_LABEL] or None


@router.get("/projects/{project_id}/reactions")
def list_reactions(
    project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> list[ReactionOut]:
    """Les pastilles de tous les éléments présents (un élément retiré n'en a plus)."""
    me = author_of(actor)
    project_dir = store.project_dir(project.id)
    items = set(_items(project_dir))
    return _summary(ReactionStore(project_dir), AuthorNames(request, project.id, me), me, items)


@router.put("/projects/{project_id}/reactions/{item_id}/{key}")
def add_reaction(
    item_id: UUID,
    key: ReactionKey,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
    request: Request,
) -> list[ReactionOut]:
    """Ma réaction sur cet élément (sans effet si elle y est déjà)."""
    me = author_of(actor)
    project_dir = store.project_dir(project.id)
    reactions = ReactionStore(project_dir)
    with store.lock(project.id):
        item, items = _item_or_404(project_dir, item_id)
        data = reactions.load()
        if not any(
            r.item_id == item_id and r.key == key and r.author == me for r in data.reactions
        ):
            data.reactions.append(Reaction(item_id=item_id, key=key, author=me))
            reactions.save(data)
            ProjectEvents(project_dir).append(
                ProjectEvent(
                    type="reaction.added",
                    project_id=project.id,
                    actor=me,
                    item_id=item_id,
                    item_kind=item.kind,
                    label=_label(item),
                    emoji=EMOJI[key],
                    item_author=item_author(project_dir, item),
                )
            )
    return _summary(reactions, AuthorNames(request, project.id, me), me, set(items))


@router.delete("/projects/{project_id}/reactions/{item_id}/{key}")
def remove_reaction(
    item_id: UUID,
    key: ReactionKey,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
    request: Request,
) -> list[ReactionOut]:
    """Retire ma réaction (seulement la mienne : celles des autres ne sont jamais touchées)."""
    me = author_of(actor)
    project_dir = store.project_dir(project.id)
    reactions = ReactionStore(project_dir)
    with store.lock(project.id):
        data = reactions.load()
        kept = [
            r
            for r in data.reactions
            if not (r.item_id == item_id and r.key == key and r.author == me)
        ]
        if len(kept) != len(data.reactions):
            data.reactions = kept
            reactions.save(data)
        items = set(_items(project_dir))
    return _summary(reactions, AuthorNames(request, project.id, me), me, items)
