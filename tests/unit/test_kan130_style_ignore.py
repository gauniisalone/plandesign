"""KAN-130 : les mots désactivés, du schéma du tableau jusqu'au choix du poste (sans modèle)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openplan.cli.render_worker import engine, sandbox
from openplan.pipeline import style_words as sw
from openplan.schemas.board import BoardItem


@pytest.mark.parametrize("word", sw.VOCABULARY, ids=lambda w: w.key)
def test_every_word_fits_the_tag_pattern_and_comes_back(word: sw.StyleWord) -> None:
    tag = sw.tag_of(word.key)
    BoardItem.model_validate(
        {
            "id": "00000000-0000-4000-8000-000000000130",
            "kind": "file",
            "x": 0,
            "y": 0,
            "w": 10,
            "h": 10,
            "ignored_tags": [tag],
        }
    )
    assert sw.key_of(tag) == word.key


def test_the_web_mirror_of_the_vocabulary_is_equal() -> None:
    """Le front lit les mots désactivés sur le tableau, sans rendu : son miroir (clé → mot
    affiché) doit rester celui du poste."""
    source = (Path(__file__).resolve().parents[2] / "apps/web/src/board/styleWords.ts").read_text(
        encoding="utf-8"
    )
    block = source.split("STYLE_FRENCH: Record<string, string> = {", 1)[1].split("};", 1)[0]
    pairs = re.findall(r'^\s*"?([a-z][a-z -]*?)"?: "([^"]+)",$', block, re.MULTILINE)
    assert pairs == [(w.key, w.french) for w in sw.VOCABULARY]


def test_no_key_holds_the_space_marker() -> None:
    assert not any("_" in w.key for w in sw.VOCABULARY)


@pytest.mark.parametrize("tag", ["m:lin", "s:inconnu", "s:white and beige", "white_and_beige"])
def test_other_tags_are_not_words(tag: str) -> None:
    assert sw.key_of(tag) is None


def test_a_disabled_word_is_never_chosen_and_the_next_one_takes_its_place() -> None:
    scores = {"scandinavian": 0.9, "japandi": 0.8, "industrial": 0.7, "bright": 0.6}
    assert sw.choose(scores) == ["scandinavian", "japandi", "bright"]
    assert sw.choose(scores, ignored=["scandinavian"]) == ["japandi", "industrial", "bright"]


def test_the_worker_reads_its_disabled_words(tmp_path: Path) -> None:
    assert sandbox.read_ignored(tmp_path) == ()
    (tmp_path / sandbox.STYLE_IGNORED).write_text(json.dumps(["bright", 3]), encoding="utf-8")
    assert sandbox.read_ignored(tmp_path) == ("bright",)
    (tmp_path / sandbox.STYLE_IGNORED).write_text("{", encoding="utf-8")
    assert sandbox.read_ignored(tmp_path) == ()


def test_the_zimage_call_is_unchanged_without_disabled_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un appel sans mot désactivé garde les arguments de KAN-97 (essai du poste)."""
    calls: list[dict[str, object]] = []

    class Result:
        seconds, vram_peak_mib, width, height = 1.0, 0, 16, 16

    class Module:
        @staticmethod
        def render_scene(*_args: object, **kwargs: object) -> Result:
            calls.append(kwargs)
            return Result()

    monkeypatch.setattr("importlib.import_module", lambda _name: Module)
    style = tmp_path / "style"
    style.mkdir()
    out = tmp_path / "r.png"
    engine.zimage_render(tmp_path, tmp_path, tmp_path, out, None, style)
    engine.zimage_render(tmp_path, tmp_path, tmp_path, out, None, style, ("bright",))
    assert set(calls[0]) == {"project_path", "style_dir"}
    assert calls[1]["style_ignored"] == ("bright",)
