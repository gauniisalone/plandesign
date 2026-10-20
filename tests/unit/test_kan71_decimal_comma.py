"""KAN-71 : la virgule décimale partout où une personne lit un nombre.

1. Le formateur unique (`openplan/schemas/numbers.py`) : mêmes cas que le test du front
   (`apps/web/unit/format.test.ts`), qui vérifie `apps/web/src/format.ts`.
2. Garde : les textes visibles produits par les fixtures (cotes et étiquettes du plan SVG et
   PDF, messages de validation, description du plan montrée avant envoi, textes de
   l'Assistant) ne contiennent aucun nombre à point décimal. Les formats techniques (JSON,
   DXF, attributs SVG) restent au point.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from openplan.pipeline.assistant import act
from openplan.pipeline.export import render_dxf, render_pdf, render_svg
from openplan.pipeline.intention import describe_room
from openplan.pipeline.say import Readers, say
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas import Opening
from openplan.schemas.assistant import AssistantIntent, AssistantReply
from openplan.schemas.common import Point2
from openplan.schemas.numbers import area, centimetres, decimal, length, metres, percent
from openplan.validation.invariants import check_invariants
from tests.factories import attic_project, l_room_project, only_level, rect_room_project

CATEGORIES = load_catalog().categories
POINT = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")
"""Un nombre à point décimal (« 1.20 »), pas une version ni un nom de fichier."""


# ------------------------------------------------------------------------- le formateur
@pytest.mark.parametrize(
    ("got", "expected"),
    [
        (decimal(1.2), "1,20"),
        (decimal(1.234, 1), "1,2"),
        (decimal(-0.001, 2), "0,00"),
        (decimal(-0.3, 2, signed=True), "-0,30"),
        (decimal(0.3, 2, signed=True), "+0,30"),
        (metres(1.2), "1,20 m"),
        (centimetres(0.5), "50 cm"),
        (centimetres(0.025, 1), "2,5 cm"),
        (area(12.54), "12,5 m²"),
        (percent(0.125, 1), "12,5 %"),
        (percent(0.5), "50 %"),
        (length(1.2, "cm"), "120 cm"),
        (length(1.2, "mm"), "1200 mm"),
        (length(1.2, "m"), "1,20 m"),
        (decimal(float("nan")), "—"),
    ],
)
def test_one_formatter_for_lengths_areas_and_percentages(got: str, expected: str) -> None:
    assert got == expected


def test_values_do_not_change_only_their_display() -> None:
    for value in (0.0, 0.83, 1.2, 2.04, 4.5, 12.345, 123.456):
        for digits in (0, 1, 2, 3):
            assert decimal(value, digits) == f"{value:.{digits}f}".replace(".", ",")


# ------------------------------------------------------------------------------- la garde
def _assert_comma(texts: list[str], where: str) -> None:
    bad = [t for t in texts if POINT.search(t)]
    assert not bad, f"{where} : nombre à point décimal dans {bad[:5]}"


def _svg_texts(svg: str) -> list[str]:
    """Le texte lu : contenu des <text>, <title>, et attributs aria-label."""
    out = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<text\b[^>]*>(.*?)</text>", svg, re.S)]
    out += re.findall(r"<title>(.*?)</title>", svg, re.S)
    out += re.findall(r'aria-label="([^"]*)"', svg)
    return [t for t in out if t.strip()]


PROJECTS = {
    "rectangle": lambda: rect_room_project(4.25, 3.35),
    "pièce en L": l_room_project,
    "combles": attic_project,
}


@pytest.mark.parametrize("name", list(PROJECTS))
def test_plan_exports_write_decimal_commas(name: str) -> None:
    project = PROJECTS[name]()
    level_id = only_level(project).id
    svg = render_svg(project, level_id)
    texts = _svg_texts(svg)
    assert texts, "le plan SVG a des cotes et des étiquettes"
    _assert_comma(texts, f"SVG ({name})")
    # Les attributs numériques du SVG restent au point (format technique).
    assert re.search(r'(?:x|y|points|viewBox)="[^"]*\d\.\d', svg)


@pytest.mark.parametrize("name", list(PROJECTS))
def test_pdf_writes_decimal_commas(name: str) -> None:
    pdfplumber = pytest.importorskip("pdfplumber")
    project = PROJECTS[name]()
    pdf = render_pdf(project, only_level(project).id)
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        lines = [line for page in doc.pages for line in (page.extract_text() or "").splitlines()]
    assert lines
    _assert_comma(lines, f"PDF ({name})")


def test_dxf_stays_technical() -> None:
    project = rect_room_project(4.25, 3.35)
    dxf = render_dxf(project, only_level(project).id)
    assert re.search(r"\n\d+\.\d+\n", dxf), "les coordonnées DXF restent au point"


def test_validation_messages_write_decimal_commas() -> None:
    project = rect_room_project(4.25, 3.35)
    level = only_level(project)
    # Une ouverture qui dépasse son mur et en chevauche une autre : deux messages chiffrés.
    window = next(e for e in level.elements.values() if isinstance(e, Opening) and e.name)
    wide = window.model_copy(update={"offset": 3.9, "width": 1.25})
    project = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(update={"elements": {**level.elements, wide.id: wide}})
            }
        }
    )
    project = apply_op(
        project, level.id, AddFurniture(asset_id="sofa_2", position=Point2(x=0.1, y=0.1))
    )
    messages = [v.message for v in check_invariants(project)]
    assert messages
    _assert_comma(messages, "validation")


@pytest.mark.parametrize("name", list(PROJECTS))
def test_the_plan_description_shown_before_sending_writes_commas(name: str) -> None:
    project = PROJECTS[name]()
    text = describe_room(project, only_level(project).id)
    assert re.search(r"\d,\d", text), text
    _assert_comma([text], f"description du plan ({name})")


def test_assistant_texts_write_decimal_commas(tmp_path: Path) -> None:
    project = rect_room_project(4.25, 3.35)
    level_id = only_level(project).id
    reply = AssistantReply(
        intents=[
            AssistantIntent(kind="set_dimension", dimension="length", value_m=5.25),
            AssistantIntent(kind="set_dimension", dimension="height", value_m=2.55),
            AssistantIntent(kind="add_opening", opening="window", target="le mur du fond"),
            AssistantIntent(kind="add_opening", opening="door", value_m=9.5),
            AssistantIntent(kind="add_furniture", category="desk"),
        ]
    )
    _after, turn = act(project, level_id, "", reply, tmp_path)
    texts = [turn.message] + [a.summary for a in turn.actions] + [a.reason for a in turn.actions]
    _assert_comma([t for t in texts if t], "Assistant")
    _after, said = say(
        project,
        level_id,
        "la pièce fait 5,25 m sur 3,5, hauteur sous plafond 2,55",
        CATEGORIES,
        tmp_path,
        readers=Readers(None, None),
    )
    _assert_comma([p.summary for p in said.parts] + [p.reason for p in said.parts], "say")
    # Le JSON du projet garde ses nombres (format technique).
    assert json.loads(_after.model_dump_json())["levels"]
