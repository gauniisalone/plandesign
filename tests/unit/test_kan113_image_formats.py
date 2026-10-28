"""KAN-113 : Pillow ne décode que JPEG, PNG et WebP. Un fichier d'un autre format (SPIDER,
GIF, TIFF, EPS), même renommé en .jpg, est refusé proprement et n'atteint jamais son
décodeur : le débordement de tas du décodeur JPEG de Pillow 12.3 (GHSA-8g5g-8vmr-9782)
rappelle que chaque décodeur essayé est une surface d'attaque."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from openplan.pipeline import board as b
from openplan.pipeline.contextual import (
    ContextualError,
    ContextualReference,
    _reference_pixels,
    reference_path,
)
from openplan.pipeline.ingestion import IngestionError, import_image
from openplan.scene.factory import new_project
from openplan.schemas.common import DECODED_IMAGE_FORMATS

OTHERS = ("SPIDER", "GIF", "TIFF", "EPS")


def other_format(fmt: str) -> bytes:
    """Une petite image dans un format que Pillow sait lire mais que l'application refuse."""
    mode = {"SPIDER": "F", "GIF": "P", "TIFF": "RGB", "EPS": "RGB"}[fmt]
    image = Image.new(mode, (16, 12))
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def png() -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.full((12, 16, 3), 120, dtype=np.uint8)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def no_other_decoder(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Note chaque décodeur hors JPEG/PNG/WebP que Pillow essaierait (il ne doit y en avoir
    aucun) : la fabrique de chaque format refusé est remplacée par un témoin."""
    Image.init()
    tried: list[str] = []
    for fmt in OTHERS:
        factory, accept = Image.OPEN[fmt]

        def spy(*args: Any, _fmt: str = fmt, _factory: Any = factory, **kw: Any) -> Any:
            tried.append(_fmt)
            return _factory(*args, **kw)

        monkeypatch.setitem(Image.OPEN, fmt, (spy, accept))
    yield tried


@pytest.mark.parametrize("fmt", OTHERS)
def test_pillow_itself_would_read_these_files(fmt: str) -> None:
    """Le test a du sens : sans restriction, Pillow reconnaît bien chacun de ces fichiers."""
    with Image.open(io.BytesIO(other_format(fmt))) as img:
        assert img.format == fmt


@pytest.mark.parametrize("fmt", OTHERS)
def test_upload_of_another_format_renamed_jpg_is_refused(
    fmt: str, tmp_path: Path, no_other_decoder: list[str]
) -> None:
    project = new_project("Atelier")
    with pytest.raises(IngestionError, match="JPEG, PNG ou WebP seulement"):
        import_image(project, tmp_path, other_format(fmt), original_name="photo.jpg")
    assert no_other_decoder == []
    assert not (tmp_path / "images").exists()  # rien d'écrit


def test_accepted_formats_still_import(tmp_path: Path, no_other_decoder: list[str]) -> None:
    project = new_project("Atelier")
    after, ref, _ = import_image(project, tmp_path, png(), original_name="salon.png")
    assert ref.id in after.images and ref.path.endswith(".png")
    assert no_other_decoder == []
    assert DECODED_IMAGE_FORMATS == ("JPEG", "PNG", "WEBP")


def test_moodboard_gif_reference_is_refused_before_launch(tmp_path: Path) -> None:
    """Un fichier joint est gardé tel quel (ses premiers octets disent seulement « image ») :
    une référence du Moodboard en GIF est refusée avant le lancement du rendu."""
    project = new_project("Atelier")
    ref = b.add_media(tmp_path, other_format("GIF"), "ambiance.gif")
    assert ref.kind == "image" and ref.media_type == "image/gif"
    reference = ContextualReference(kind="file", ref=str(ref.id))
    with pytest.raises(ContextualError, match="JPEG, PNG ou WebP"):
        reference_path(project, tmp_path, tmp_path / "runs", reference)


def test_moodboard_reference_that_lies_about_its_format_is_refused(
    tmp_path: Path, no_other_decoder: list[str]
) -> None:
    """Un fichier joint dont l'index dit PNG mais qui contient un TIFF : décodé comme
    JPEG/PNG/WebP seulement, il est refusé (ContextualError, jamais une erreur brute)."""
    project = new_project("Atelier")
    ref = b.add_media(tmp_path, png(), "ambiance.png")
    (tmp_path / ref.path).write_bytes(other_format("TIFF"))
    reference = ContextualReference(kind="file", ref=str(ref.id))
    with pytest.raises(ContextualError, match="illisible"):
        _reference_pixels(project, tmp_path, tmp_path / "runs", [reference])
    assert no_other_decoder == []
