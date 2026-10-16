"""Symboles 2D des meubles (F4) : une famille, un symbole ; plan, SVG et PDF les dessinent."""

from __future__ import annotations

import math

import pytest

from openplan.geom.furniture_symbols import GENERIC, SYMBOLS, place_symbol, symbol_for
from openplan.pipeline.export import render_pdf, render_svg
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2
from tests.factories import only_level, rect_room_project


def test_every_catalog_family_has_its_own_symbol_inside_its_box() -> None:
    categories = {a.category for a in load_catalog().assets}
    assert categories <= set(SYMBOLS), sorted(categories - set(SYMBOLS))
    for category, symbol in SYMBOLS.items():
        assert symbol.paths, f"{category} : symbole vide"
        for path in symbol.paths:
            assert len(path.points) >= 2
            for u, v in path.points:
                assert -0.5 - 1e-9 <= u <= 0.5 + 1e-9 and -0.5 - 1e-9 <= v <= 0.5 + 1e-9, category
    # Deux familles différentes ne se confondent pas (sauf réfrigérateurs entre eux).
    shapes = [s.model_dump_json() for c, s in SYMBOLS.items() if c not in {"fridge_small"}]
    assert len(shapes) == len(set(shapes))
    assert symbol_for("licorne") == GENERIC


def test_symbol_follows_size_position_and_yaw_with_front_at_plus_v() -> None:
    """Oreillers du lit à l'arrière (−v) ; tourné de 90°, l'arrière passe en +x."""
    outline, paths = place_symbol(symbol_for("bed_double"), 2.0, 1.0, 0.0, 1.6, 2.0)
    assert {(round(p.x, 6), round(p.y, 6)) for p in outline} == {
        (1.2, 0.0),
        (2.8, 0.0),
        (2.8, 2.0),
        (1.2, 2.0),
    }
    pillow = paths[0][0]
    assert max(p.y for p in pillow) < 1.0 - 0.5, "les oreillers sont au dos du lit"
    _, turned = place_symbol(symbol_for("bed_double"), 2.0, 1.0, math.pi / 2, 1.6, 2.0)
    assert min(p.x for p in turned[0][0]) > 2.0 + 0.5
    round_outline, _ = place_symbol(symbol_for("round_table"), 0.0, 0.0, 0.0, 1.0, 1.0)
    assert len(round_outline) == 32
    assert all(math.hypot(p.x, p.y) == pytest.approx(0.5, abs=1e-3) for p in round_outline)


def _furnished():  # type: ignore[no-untyped-def]
    project = rect_room_project()
    level_id = only_level(project).id
    for asset_id, x in (("toilet", 1.0), ("stove", 2.0), ("round_table", 3.0)):
        project = apply_op(
            project, level_id, AddFurniture(asset_id=asset_id, position=Point2(x=x, y=1.5))
        )
    return project, level_id


def test_svg_and_pdf_draw_each_piece_with_its_symbol() -> None:
    project, level_id = _furnished()
    svg = render_svg(project, level_id)
    assert svg.count('<g class="furniture"') == 3
    assert svg.count('class="furniture-outline"') == 3
    assert 'data-category="stove"' in svg
    # WC : réservoir + cuvette ; cuisinière : quatre feux ; table ronde : un plateau.
    assert svg.count('class="furniture-symbol"') == 2 + 4 + 1
    # Les meubles sont dessinés avant les murs : un mur passe par-dessus un meuble trop près.
    assert svg.index('class="furniture-outline"') < svg.index('class="wall')
    empty = rect_room_project()
    bare = render_pdf(empty, only_level(empty).id)
    pdf = render_pdf(project, level_id)
    assert pdf.startswith(b"%PDF-") and len(pdf) > len(bare) + 500
