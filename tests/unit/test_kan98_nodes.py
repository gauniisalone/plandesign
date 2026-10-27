"""KAN-98, lot L1 : « Inspirations » devient « Moodboard » (seulement les titres encore égaux au
défaut d'avant, jamais un titre choisi à la main, jamais une position), le cadre A1 des rendus
contextualisés prend « Variantes » pour libérer le nom, et les cases d'images remplissent la
largeur d'une carte plus large (une carte de la largeur d'avant garde ses cases)."""

from __future__ import annotations

from pathlib import Path

from openplan.pipeline.board import (
    CELL_H,
    CELL_W,
    FRAME_W,
    GAP,
    NODE_H,
    NODE_W,
    VARIANTS_TITLE,
    _renamed_moodboards,
    cell_size,
    sync_board,
)
from openplan.scene.plans import NewPlan, add_plan
from openplan.schemas.board import Board, BoardItem
from tests.factories import only_level, rect_room_project


def _frame(role: str, text: str, **kw: float) -> BoardItem:
    return BoardItem(
        kind="frame",
        role=role,
        text=text,
        x=kw.get("x", 0.0),
        y=10.0,
        w=kw.get("w", FRAME_W),
        h=374.0,
    )  # type: ignore[arg-type]


def test_default_titles_become_moodboard_user_titles_and_places_stay() -> None:
    items = [
        _frame("inspirations", "Inspirations", x=12.0),
        _frame("inspirations", "Inspirations · Étage"),
        _frame("inspirations", "Ambiance salon"),
        _frame("photos", "Photos de la pièce"),
    ]
    out = _renamed_moodboards(items)
    assert [i.text for i in out] == [
        "Moodboard",
        "Moodboard · Étage",
        "Ambiance salon",
        "Photos de la pièce",
    ]
    assert [(i.x, i.y, i.w, i.h, i.id) for i in out] == [(i.x, i.y, i.w, i.h, i.id) for i in items]
    assert _renamed_moodboards(out) == out, "une seconde lecture ne change rien"


def test_a_board_with_both_frames_shows_one_moodboard_the_inspirations() -> None:
    """PO : le cadre A1 (rôle moodboard, titre par défaut « Moodboard ») devient « Variantes » ;
    un seul « Moodboard » reste, celui des inspirations. Un titre A1 choisi à la main ne bouge
    pas."""
    items = [_frame("moodboard", "Moodboard"), _frame("inspirations", "Inspirations")]
    out = _renamed_moodboards(items)
    assert [i.text for i in out] == [VARIANTS_TITLE, "Moodboard"]
    assert [i.text for i in out].count("Moodboard") == 1
    assert [i.role for i in out if i.text == "Moodboard"] == ["inspirations"]
    kept = _renamed_moodboards([_frame("moodboard", "Mes variantes")])
    assert kept[0].text == "Mes variantes"


def test_cells_fill_a_wider_card_and_keep_their_size_in_an_old_one() -> None:
    assert cell_size(_frame("photos", "x")) == (CELL_W, CELL_H)
    wide = cell_size(_frame("photos", "x", w=780.0))
    assert wide[0] > CELL_W and abs(wide[1] / wide[0] - CELL_H / CELL_W) < 1e-9
    # Deux colonnes et les marges remplissent exactement la carte.
    assert abs(2 * wide[0] + 16.0 + 2 * 24.0 - 780.0) < 1e-9


def _nodes(board: Board) -> dict[str, BoardItem]:
    """Le premier node de chaque sorte (plan, photos, inspirations, renders)."""
    out: dict[str, BoardItem] = {}
    for i in board.items:
        key = "plan" if i.kind == "plan" else (i.role if i.kind == "frame" else None)
        if key and key not in out:
            out[key] = i
    return out


def test_a_new_workspace_is_laid_out_wide_moodboard_above_renders(tmp_path: Path) -> None:
    """KAN-98 L2 : Moodboard en haut, au-dessus de Rendus (même colonne, 48 d'écart), puis la
    rangée Photos → Plan → Rendus ; Photos, Moodboard et Rendus en 780 de large."""
    board, _ = sync_board(None, rect_room_project(), tmp_path / "runs")
    n = _nodes(board)
    mood, renders, photos, plan = n["inspirations"], n["renders"], n["photos"], n["plan"]
    assert (photos.w, mood.w, renders.w) == (NODE_W, NODE_W, NODE_W)
    assert (photos.h, mood.h) == (NODE_H, NODE_H)
    assert mood.x == renders.x and renders.y - (mood.y + mood.h) == GAP
    assert photos.y == plan.y == renders.y
    assert photos.x + photos.w < plan.x < plan.x + plan.w < renders.x
    # Les 4 nodes sont reliés dès le départ (liens automatiques).
    kinds = sorted(k.kind for k in board.links)
    assert kinds == ["feeds_plan", "guides_style", "renders_view"]
    # Aucun node ne chevauche un autre.
    frames = [mood, renders, photos, plan]
    for a in frames:
        for b in frames:
            if a.id != b.id:
                assert a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y


def test_a_second_plan_opens_a_wide_row_below(tmp_path: Path) -> None:
    project = rect_room_project()
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    after, storey = add_plan(project, NewPlan(name="Étage", kind="storey"))
    board2, _ = sync_board(board, after, runs)
    plans = {i.ref: i for i in board2.items if i.kind == "plan"}
    first, second = plans[str(only_level(project).id)], plans[str(storey)]
    assert second.x == first.x and second.y >= first.y + first.h + GAP
    card = next(i for i in board2.items if i.role == "photos" and i.ref == str(storey))
    rendus = next(i for i in board2.items if i.role == "renders" and i.ref == str(storey))
    assert (card.w, rendus.w) == (NODE_W, NODE_W)
    assert card.y == second.y == rendus.y


def test_an_old_workspace_keeps_its_sizes_and_places(tmp_path: Path) -> None:
    """Un espace posé avant L2 (nodes de 624) garde tailles et places, et un plan de plus s'y
    pose à l'ancienne ; « Ranger » (KAN-100) viendra."""
    project = rect_room_project()
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    # Le même espace, posé à l'ancienne : nodes de 624, colonne de gauche, plan à 720.
    old = [i.model_copy(update={"w": FRAME_W}) if i.kind == "frame" else i for i in board.items]
    legacy = board.model_copy(update={"items": old})
    again, _ = sync_board(legacy, project, runs)
    assert [(i.id, i.x, i.y, i.w, i.h) for i in again.items] == [
        (i.id, i.x, i.y, i.w, i.h) for i in legacy.items
    ]
    after, storey = add_plan(project, NewPlan(name="Étage", kind="storey"))
    grown, _ = sync_board(again, after, runs)
    card = next(i for i in grown.items if i.role == "photos" and i.ref == str(storey))
    assert card.w == FRAME_W
