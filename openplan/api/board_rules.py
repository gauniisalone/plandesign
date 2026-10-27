"""KAN-91 (C1) : une génération ne lit que les membres des cartes reliées. Gardes communes aux
routes qui construisent le plan depuis une photo et à la demande de rendu ; elles lisent
l'espace de travail à jour (synchronisé comme à sa lecture : une photo qui vient d'être
importée entre dans la carte Photos, et les liens automatiques sont posés)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from openplan.pipeline import board as b
from openplan.pipeline import board_links as bl
from openplan.scene.store import ProjectStore
from openplan.schemas.board import Board
from openplan.schemas.scene import Project

NOT_A_MEMBER = (
    "Cette photo n'est pas dans le node Photos de la pièce relié à ce plan : glissez-la dans le "
    "node pour qu'elle serve au plan (une photo hors node n'est jamais utilisée)."
)
NO_PHOTO_CARD = (
    "Aucun node Photos de la pièce n'est relié à ce plan : tirez un lien depuis un node Photos "
    "de la pièce pour que ses photos le construisent."
)


def board_now(project: Project, store: ProjectStore) -> Board:
    project_dir = store.project_dir(project.id)
    with store.lock(project.id):
        board, changed = b.sync_board(
            b.load_board(project_dir),
            project,
            store.runs_dir(project.id),
            b.list_media(project_dir),
        )
        if changed:
            board = b.save_board(project_dir, board)
    return board


def photo_card(board: Board, project: Project, level_id: UUID) -> UUID | None:
    """La carte Photos d'un plan : celle qui lui est reliée, sinon celle de son niveau."""
    plan = bl.plan_item(board, level_id)
    if plan is not None:
        source = bl.generation_inputs(board, plan.id).photo_card
        if source is not None:
            return source
    first = str(next(iter(project.levels))) if project.levels else None
    levels = [first, *(str(lid) for lid in project.levels if str(lid) != first)] if first else []
    return next(
        (
            i.id
            for i in board.items
            if bl.card_role(i) == "photos" and bl.card_level(i, levels) == str(level_id)
        ),
        None,
    )


def place_new_photo(project: Project, store: ProjectStore, level_id: UUID, image_id: UUID) -> None:
    """KAN-91 : une photo importée pour le plan ouvert entre dans la carte Photos de ce plan
    (sinon la synchronisation la range dans la carte du premier plan)."""
    if level_id not in project.levels:
        return
    project_dir = store.project_dir(project.id)
    with store.lock(project.id):
        board, changed = b.sync_board(
            b.load_board(project_dir),
            project,
            store.runs_dir(project.id),
            b.list_media(project_dir),
        )
        card = photo_card(board, project, level_id)
        placed = b.photo_into_card(board, str(image_id), card) if card is not None else board
        if changed or placed is not board:
            b.save_board(project_dir, placed)


def require_plan_photo(
    project: Project, store: ProjectStore, level_id: UUID, image_id: UUID
) -> None:
    """409 si la photo n'est pas membre de la carte Photos reliée au plan de ce niveau."""
    if level_id not in project.levels or image_id not in project.images:
        return  # la route dit elle-même « niveau inconnu » ou « image inconnue »
    board = board_now(project, store)
    plan = bl.plan_item(board, level_id)
    if plan is None:
        return  # plan retiré de l'espace de travail à la main : rien à vérifier ici
    inputs = bl.generation_inputs(board, plan.id)
    if inputs.photo_card is None:
        raise HTTPException(status_code=409, detail=NO_PHOTO_CARD)
    if str(image_id) not in inputs.photos:
        raise HTTPException(status_code=409, detail=NOT_A_MEMBER)


REFERENCE_LABELS = {"photo": "Photo", "file": "Image", "render": "Rendu", "contextual": "Variante"}


def style_references(
    project: Project, store: ProjectStore, level_id: UUID, limit: int
) -> list[tuple[str, str, str]]:
    """(sorte, ref, nom) des images qui guident le style des rendus de ce niveau : les membres
    des cartes Inspirations reliées à sa carte Rendus (C1), à poids égal entre cartes (prises
    tour à tour), au plus `limit`. Remplace les images cochées « Référence »."""
    board = board_now(project, store)
    first = str(next(iter(project.levels))) if project.levels else None
    card = bl.renders_card(board, level_id, first)
    if card is None:
        return []
    inputs = bl.generation_inputs(board, card.id)
    names = {i.id: i.text for i in board.items}
    queues = [
        [
            (i.kind, i.ref, (names.get(i.item) or REFERENCE_LABELS.get(i.kind, "Image"))[:120])
            for i in inputs.inspirations
            if i.card == c and i.ref
        ]
        for c in inputs.inspiration_cards
    ]
    out: list[tuple[str, str, str]] = []
    while len(out) < limit and any(queues):
        for queue in queues:
            if queue and len(out) < limit:
                out.append(queue.pop(0))
    return out


def render_inputs(
    project: Project, store: ProjectStore, level_id: UUID
) -> bl.GenerationInputs | None:
    """Ce que lit un rendu du plan de ce niveau (sa carte Rendus), ou None sans carte Rendus.
    409 « Reliez un plan pour rendre » si la carte n'a pas de plan relié (D2)."""
    board = board_now(project, store)
    first = str(next(iter(project.levels))) if project.levels else None
    card = bl.renders_card(board, level_id, first)
    if card is None:
        return None
    inputs = bl.generation_inputs(board, card.id)
    if not inputs.can_render:
        raise HTTPException(status_code=409, detail=bl.NO_PLAN)
    return inputs
