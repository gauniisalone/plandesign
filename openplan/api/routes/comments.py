"""Commentaires de l'espace de travail (KAN-51) : fils épinglés n'importe où ou sur un élément,
réponses, Résoudre / Rouvrir. Même code en local et avec les comptes ; les droits par rôle sont
dans la table des gardes (lire : lecteur ; écrire : commentateur et plus), et ici pour ce qui
dépend de la personne : seul l'auteur modifie ou supprime son message ; un fil se supprime par
le propriétaire du projet, ou par son auteur tant qu'il n'a pas de réponse.

Chaque fil, réponse, résolution ou réouverture publie un événement (`events.jsonl`) pour la
future cloche (KAN-52) : qui, quoi, où, quand ; jamais le texte ni une adresse."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from openplan.api.access import accounts_of
from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.api.guards import role_of
from openplan.pipeline import board as b
from openplan.pipeline.comments import CommentStore
from openplan.scene.history import USER_ACTOR, USER_PREFIX
from openplan.schemas.comments import (
    LOCAL_AUTHOR,
    MAX_TEXT,
    CommentFile,
    CommentMessage,
    CommentPoint,
    CommentTarget,
    CommentThread,
)
from openplan.schemas.common import StrictModel, now_utc
from openplan.schemas.events import MAX_LABEL, EventType, ProjectEvent

router = APIRouter(tags=["commentaires"])

FORMER_MEMBER = "Ancien membre"
LOCAL_USER = "Utilisateur local"
YOU = "Vous"
NEUTRAL = "#8b8d98"
# La palette de la présence (accounts/live.py), recopiée : ce module se charge aussi en local,
# sans la couche des comptes.
PALETTE = ("#e5484d", "#f76b15", "#ffc53d", "#46a758", "#12a594", "#0090ff", "#6e56cf", "#d6409f")


class NewThread(StrictModel):
    target: CommentTarget
    text: str


class NewText(StrictModel):
    text: str


class MessageOut(StrictModel):
    id: UUID
    author_name: str
    author_color: str
    mine: bool
    text: str
    created_at: datetime
    edited_at: datetime | None


class ThreadOut(StrictModel):
    id: UUID
    target: CommentTarget
    resolved: bool
    resolved_at: datetime | None
    messages: list[MessageOut]
    can_delete: bool


def author_of(actor: str) -> str:
    """L'auteur gardé : `local` sans comptes, sinon l'id du compte (ou le nom d'un jeton)."""
    if actor == USER_ACTOR:
        return LOCAL_AUTHOR
    return actor.removeprefix(USER_PREFIX)


def _text(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Le commentaire est vide.")
    if len(text) > MAX_TEXT:
        raise HTTPException(
            status_code=422, detail=f"{MAX_TEXT} caractères au plus par commentaire."
        )
    return text


def _color(author: str) -> str:
    try:
        uid = UUID(author)
    except ValueError:
        return NEUTRAL
    return PALETTE[hashlib.sha256(uid.bytes).digest()[0] % len(PALETTE)]


class AuthorNames:
    """Nom et couleur de chaque auteur, comme les voit la personne qui lit ; un membre parti ou
    un compte supprimé : « Ancien membre », jamais d'erreur, jamais d'adresse."""

    def __init__(self, request: Request, project_id: UUID, me: str) -> None:
        self.accounts = accounts_of(request)
        self.project_id = project_id
        self.me = me
        self.cache: dict[str, tuple[str, str]] = {}

    def __call__(self, author: str) -> tuple[str, str]:
        if author not in self.cache:
            self.cache[author] = self._resolve(author)
        return self.cache[author]

    def _resolve(self, author: str) -> tuple[str, str]:
        if author == LOCAL_AUTHOR:
            return (YOU if self.accounts is None else LOCAL_USER), NEUTRAL
        if self.accounts is None:
            return author[:80], NEUTRAL
        try:
            uid = UUID(author)
        except ValueError:
            return FORMER_MEMBER, NEUTRAL
        if self.accounts.role_name(self.project_id, uid) is None:
            return FORMER_MEMBER, NEUTRAL
        name = self.accounts.display_of(uid)
        return (name, _color(author)) if name else (FORMER_MEMBER, NEUTRAL)


def _out(thread: CommentThread, names: AuthorNames, me: str, owner: bool) -> ThreadOut:
    messages = []
    for m in thread.messages:
        name, color = names(m.author)
        messages.append(
            MessageOut(
                id=m.id,
                author_name=name,
                author_color=color,
                mine=m.author == me,
                text=m.text,
                created_at=m.created_at,
                edited_at=m.edited_at,
            )
        )
    first = thread.messages[0].author if thread.messages else None
    return ThreadOut(
        id=thread.id,
        target=thread.target,
        resolved=thread.resolved,
        resolved_at=thread.resolved_at,
        messages=messages,
        can_delete=owner or (first == me and len(thread.messages) <= 1),
    )


def _find(comments: CommentFile, thread_id: UUID) -> CommentThread:
    for thread in comments.threads:
        if thread.id == thread_id:
            return thread
    raise HTTPException(status_code=404, detail="fil de commentaires inconnu")


def _refresh_positions(comments: CommentFile, project_dir: Path) -> None:
    """Position actuelle de chaque élément commenté, d'après le tableau courant. En mémoire :
    la liste s'en sert sans rien écrire ; une écriture de fil l'enregistre avec le reste. Un
    élément retiré a laissé sa dernière position au fil (`board.save_board`)."""
    board = b.load_board(project_dir)
    if board is None:
        return
    where = {i.id: CommentPoint(x=i.x, y=i.y) for i in board.items}
    for thread in comments.threads:
        item = thread.target.item_id
        if item is not None and item in where:
            thread.target.last = where[item]


def _event(
    store: CommentStore, kind: EventType, project_id: UUID, me: str, thread: CommentThread
) -> None:
    store.publish(
        ProjectEvent(
            type=kind,
            project_id=project_id,
            actor=me,
            thread_id=thread.id,
            item_id=thread.target.item_id,
            label=_item_label(store.project_dir, thread.target.item_id),
        )
    )


def _item_label(project_dir: Path, item_id: UUID | None) -> str | None:
    """KAN-52 : l'objet commenté pour la cloche (« Photo 2 »), tel qu'il s'appelle à cet
    instant ; une épingle libre n'en a pas (« l'espace de travail »)."""
    if item_id is None:
        return None
    board = b.load_board(project_dir)
    item = next((i for i in board.items if i.id == item_id), None) if board else None
    text = " ".join((item.text or "").split()) if item is not None else ""
    return text[:MAX_LABEL] or None


@router.get("/projects/{project_id}/comments")
def list_comments(
    project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> list[ThreadOut]:
    """Tous les fils du projet, ouverts et résolus (le front les range). Une lecture pure :
    rien n'est écrit (le cookie de lecture y vaut donc)."""
    me = author_of(actor)
    project_dir = store.project_dir(project.id)
    with store.lock(project.id):
        comments = CommentStore(project_dir).load()
        _refresh_positions(comments, project_dir)
    names = AuthorNames(request, project.id, me)
    owner = role_of(request, project.id) == "owner"
    return [_out(t, names, me, owner) for t in comments.threads]


@router.post("/projects/{project_id}/comments", status_code=201)
def create_thread(
    body: NewThread, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> ThreadOut:
    """Une épingle et son premier message."""
    me = author_of(actor)
    thread = CommentThread(
        target=body.target, messages=[CommentMessage(author=me, text=_text(body.text))]
    )
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        comments.threads.append(thread)
        _refresh_positions(comments, store.project_dir(project.id))
        comments_store.save(CommentFile.model_validate(comments.model_dump()))
        _event(comments_store, "comment.created", project.id, me, thread)
    owner = role_of(request, project.id) == "owner"
    return _out(thread, AuthorNames(request, project.id, me), me, owner)


@router.post("/projects/{project_id}/comments/{thread_id}/replies", status_code=201)
def reply(
    thread_id: UUID,
    body: NewText,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
    request: Request,
) -> ThreadOut:
    me = author_of(actor)
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        thread = _find(comments, thread_id)
        thread.messages.append(CommentMessage(author=me, text=_text(body.text)))
        _refresh_positions(comments, store.project_dir(project.id))
        comments_store.save(CommentFile.model_validate(comments.model_dump()))
        _event(comments_store, "comment.replied", project.id, me, thread)
    owner = role_of(request, project.id) == "owner"
    return _out(thread, AuthorNames(request, project.id, me), me, owner)


@router.patch("/projects/{project_id}/comments/{thread_id}/messages/{message_id}")
def edit_message(
    thread_id: UUID,
    message_id: UUID,
    body: NewText,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
    request: Request,
) -> ThreadOut:
    """Seul l'auteur modifie son message."""
    me = author_of(actor)
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        thread = _find(comments, thread_id)
        message = next((m for m in thread.messages if m.id == message_id), None)
        if message is None:
            raise HTTPException(status_code=404, detail="message inconnu")
        if message.author != me:
            raise HTTPException(status_code=403, detail="seul l'auteur modifie son message")
        message.text = _text(body.text)
        message.edited_at = now_utc()
        _refresh_positions(comments, store.project_dir(project.id))
        comments_store.save(comments)
    owner = role_of(request, project.id) == "owner"
    return _out(thread, AuthorNames(request, project.id, me), me, owner)


@router.delete("/projects/{project_id}/comments/{thread_id}/messages/{message_id}", status_code=204)
def delete_message(
    thread_id: UUID, message_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep
) -> Response:
    """Seul l'auteur supprime son message ; le fil vidé disparaît."""
    me = author_of(actor)
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        thread = _find(comments, thread_id)
        message = next((m for m in thread.messages if m.id == message_id), None)
        if message is None:
            raise HTTPException(status_code=404, detail="message inconnu")
        if message.author != me:
            raise HTTPException(status_code=403, detail="seul l'auteur supprime son message")
        thread.messages.remove(message)
        if not thread.messages:
            comments.threads.remove(thread)
        comments_store.save(comments)
    return Response(status_code=204)


def _set_resolved(
    thread_id: UUID,
    resolved: bool,
    project: ProjectDep,
    store: StoreDep,
    actor: str,
    request: Request,
) -> ThreadOut:
    me = author_of(actor)
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        thread = _find(comments, thread_id)
        if thread.resolved != resolved:
            thread.resolved = resolved
            thread.resolved_at = now_utc() if resolved else None
            _refresh_positions(comments, store.project_dir(project.id))
            comments_store.save(comments)
            kind: EventType = "comment.resolved" if resolved else "comment.reopened"
            _event(comments_store, kind, project.id, me, thread)
    owner = role_of(request, project.id) == "owner"
    return _out(thread, AuthorNames(request, project.id, me), me, owner)


@router.post("/projects/{project_id}/comments/{thread_id}/resolve")
def resolve(
    thread_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> ThreadOut:
    """« Résoudre » : l'épingle se masque ; le fil reste consultable et peut être rouvert."""
    return _set_resolved(thread_id, True, project, store, actor, request)


@router.post("/projects/{project_id}/comments/{thread_id}/reopen")
def reopen(
    thread_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> ThreadOut:
    return _set_resolved(thread_id, False, project, store, actor, request)


@router.delete("/projects/{project_id}/comments/{thread_id}", status_code=204)
def delete_thread(
    thread_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> Response:
    """Le propriétaire supprime tout fil ; l'auteur, le sien tant qu'il n'a pas de réponse."""
    me = author_of(actor)
    owner = role_of(request, project.id) == "owner"
    comments_store = CommentStore(store.project_dir(project.id))
    with store.lock(project.id):
        comments = comments_store.load()
        thread = _find(comments, thread_id)
        first = thread.messages[0].author if thread.messages else None
        if not (owner or (first == me and len(thread.messages) <= 1)):
            raise HTTPException(
                status_code=403,
                detail=(
                    "seul le propriétaire du projet, ou l'auteur d'un fil sans réponse, le supprime"
                ),
            )
        comments.threads.remove(thread)
        comments_store.save(comments)
    return Response(status_code=204)
