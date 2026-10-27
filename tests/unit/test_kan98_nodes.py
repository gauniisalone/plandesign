"""KAN-98, lot L1 : « Inspirations » devient « Moodboard » (seulement les titres encore égaux au
défaut d'avant, jamais un titre choisi à la main, jamais une position), le cadre A1 des rendus
contextualisés prend « Variantes » pour libérer le nom, et les cases d'images remplissent la
largeur d'une carte plus large (une carte de la largeur d'avant garde ses cases)."""

from __future__ import annotations

from openplan.pipeline.board import (
    CELL_H,
    CELL_W,
    FRAME_W,
    VARIANTS_TITLE,
    _renamed_moodboards,
    cell_size,
)
from openplan.schemas.board import BoardItem


def _frame(role: str, text: str, **kw: float) -> BoardItem:
    return BoardItem(kind="frame", role=role, text=text, x=kw.get("x", 0.0), y=10.0, w=kw.get("w", FRAME_W), h=374.0)  # type: ignore[arg-type]


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
