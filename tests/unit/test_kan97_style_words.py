"""KAN-97 sans poids : choix des mots de style (vocabulaire fermé, plafonds, filtre de couleur),
leur place dans la description Z-Image, la lecture des images par un faux SigLIP2 (libéré
ensuite), et le poste qui ne lit les images que face à un serveur qui sert « style »."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.protocols import ImageArray
from openplan.cli.render_worker.engine import FAKE_STYLE_WORDS, render
from openplan.cli.render_worker.loop import Worker
from openplan.cli.render_worker.sandbox import STYLE_DIR
from openplan.pipeline import style_words as sw
from openplan.pipeline.finish import finish_prompt
from openplan.pipeline.scene_prompt import SceneWords
from openplan.pipeline.zimage_render import describe_style, style_images
from openplan.schemas.render_queue import MAX_STYLE_WORDS, STYLE, WorkerJob, WorkerResult
from tests.factories import only_level, rect_room_project

SURFACE_WORDS = ("plaster", "wood", "oak", "concrete", "tile", "floor", "beam", "wall")


def test_the_vocabulary_never_names_a_surface_material() -> None:
    for word in sw.VOCABULARY:
        assert not any(s in word.key for s in SURFACE_WORDS), word.key
        assert word.french and word.group in sw.CAPS
    assert sum(sw.CAPS.values()) == sw.MAX_WORDS == MAX_STYLE_WORDS
    assert len(sw.prompts()) == len(sw.VOCABULARY)


def test_choose_keeps_at_most_eight_words_within_each_group_cap() -> None:
    scores = {w.key: 0.9 - i * 0.01 for i, w in enumerate(sw.VOCABULARY)}
    chosen = sw.choose(scores)
    assert len(chosen) == sw.MAX_WORDS
    groups = [sw.BY_KEY[k].group for k in chosen]
    for group, cap in sw.CAPS.items():
        assert groups.count(group) == cap
    # Sous le seuil, rien n'est forcé ; une clé inconnue est ignorée.
    assert sw.choose({"scandinavian": sw.MIN_SCORE / 2, "neo brutalist": 0.99}) == []


def test_a_palette_colour_already_given_to_a_surface_is_dropped() -> None:
    scores = {"terracotta accents": 0.8, "warm neutrals": 0.7, "cozy": 0.6}
    kept = sw.choose(scores, ["terracotta tiled floor"])
    assert kept == ["warm neutrals", "cozy"]
    assert sw.french(["warm neutrals", "cozy", "inconnu"]) == ["tons chauds", "chaleureux"]


def test_style_words_follow_the_mood_in_the_prompt_without_repeats() -> None:
    project = rect_room_project()
    level = only_level(project)
    plain = SceneWords(surfaces=["white plaster walls"], furniture=["a double bed"])
    styled = SceneWords(
        surfaces=plain.surfaces, furniture=plain.furniture, style=["japandi", "airy"]
    )
    before = finish_prompt(project, None, level.id, scene=plain, engine="zimage")
    after = finish_prompt(project, None, level.id, scene=styled, engine="zimage")
    assert "japandi" not in before and "japandi, airy" in after
    assert after.index("airy") < after.index("white plaster walls"), "avant les surfaces"
    # Un mot déjà dit par l'ambiance (« scandinavian » forcé comme style) n'est pas redit.
    forced = finish_prompt(
        project,
        "scandinavian",
        level.id,
        scene=SceneWords(surfaces=plain.surfaces, style=["scandinavian"]),
        engine="zimage",
    )
    assert forced.lower().count("scandinavian") == 1


class FakeScorer:
    def __init__(self, best: dict[str, float]) -> None:
        self.best = best
        self.released = False
        self.calls = 0

    def scores(self, images: list[ImageArray], texts: list[str]) -> list[float]:
        self.calls += 1
        assert texts == sw.prompts()
        return [self.best.get(w.key, 0.01) for w in sw.VOCABULARY]

    def release(self) -> None:
        self.released = True


def _pixels() -> ImageArray:
    return ImageArray(image_id=uuid4(), pixels=np.full((32, 32, 3), 200, np.uint8))


def test_describe_style_scores_once_and_releases_the_model(tmp_path: Path) -> None:
    scorer = FakeScorer({"scandinavian": 0.7, "bright": 0.5, "sage green accents": 0.4})
    scene = SceneWords(textiles=["sage green linen curtains"])
    words = describe_style([_pixels(), _pixels()], scene, tmp_path, scorer)
    assert words == ["scandinavian", "bright"], "le vert sauge est déjà dans la scène"
    assert scorer.calls == 1 and scorer.released
    assert describe_style([], scene, tmp_path, scorer) == []


def test_style_images_reads_the_folder_and_skips_what_is_not_an_image(tmp_path: Path) -> None:
    folder = tmp_path / STYLE_DIR
    folder.mkdir()
    for k in range(sw.MAX_IMAGES + 2):
        Image.new("RGB", (20, 10), (k * 10, 0, 0)).save(folder / f"{k}.jpg")
    (folder / "x.jpg").write_bytes(b"pas une image")
    (folder / "notes.txt").write_text("rien")
    assert len(style_images(folder)) == sw.MAX_IMAGES
    assert style_images(tmp_path / "absent") == [] and style_images(None) == []


def test_the_fake_engine_reads_words_only_when_images_are_there(tmp_path: Path) -> None:
    from tests.unit.test_kan89_worker import _camera

    camera = tmp_path / "camera.json"
    _camera(camera)
    out = tmp_path / "r.png"
    plain = render("fake", tmp_path, tmp_path, camera, out)
    assert plain.style_words is None
    style = tmp_path / STYLE_DIR
    style.mkdir()
    Image.new("RGB", (8, 8)).save(style / "0.jpg")
    assert render("fake", tmp_path, tmp_path, camera, out, style_dir=style).style_words == list(
        FAKE_STYLE_WORDS
    )


class StyleClient:
    def __init__(self, served: frozenset[str]) -> None:
        self.served = served
        self.fetched: list[int] = []

    def fetch_style(self, job: WorkerJob, index: int, folder: Path) -> Path | None:
        self.fetched.append(index)
        return None


def _worker(tmp_path: Path, client: object) -> Worker:
    return Worker(
        client=client,  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: None,
        say=lambda _text: None,
        sleep=lambda _s: None,
    )


def test_the_worker_reads_style_images_only_from_a_server_that_serves_style(
    tmp_path: Path,
) -> None:
    job = WorkerJob(
        request_id=uuid4(),
        lease=uuid4(),
        created_at=datetime(2026, 10, 27, tzinfo=UTC),
        style=["a", "b", "c"],
    )
    served = StyleClient(frozenset({STYLE}))
    assert _worker(tmp_path, served)._fetch_style(job, tmp_path) is True
    assert served.fetched == [0, 1, 2]
    old = StyleClient(frozenset({"heartbeat"}))
    assert _worker(tmp_path, old)._fetch_style(job, tmp_path) is False and old.fetched == []
    bare = job.model_copy(update={"style": None})
    assert _worker(tmp_path, served)._fetch_style(bare, tmp_path) is False


def test_the_result_keeps_the_old_message_without_words() -> None:
    """Sans mots, le message du dépôt est exactement celui que lit un serveur d'avant KAN-97."""
    result = WorkerResult(seconds=1.0, vram_peak_mib=0, width=64, height=48, engine="fake")
    assert "style_words" not in result.model_dump_json(exclude_none=True)
    with pytest.raises(ValueError):
        WorkerResult(
            seconds=1.0,
            vram_peak_mib=0,
            width=64,
            height=48,
            engine="fake",
            style_words=["x"] * (MAX_STYLE_WORDS + 1),
        )
