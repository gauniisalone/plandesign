"""KAN-130 : un mot de « Guidé par » désactivé sur les images d'un Moodboard ne guide plus les
rendus suivants, par les vraies routes (sans poids).

Le mot est noté sur chaque image (`ignored_tags`, `s:<clé>`, espaces en « _ ») ; la demande garde
ceux du moment du clic. Un poste qui annonce « style-ignore » les reçoit (`style_ignored`) et ne
les retient jamais ; un poste qui ne l'annonce pas reçoit le message de KAN-97, à l'identique."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.accounts.test_kan89_realistic import World
from tests.accounts.test_kan97_style import STYLED, _claim, _deposit, _inspire, _row

IGNORING = {"state": "idle", "version": "heartbeat+style+style-ignore"}
ITEM = "00000000-0000-4000-8000-00000000a097"
"""L'image d'inspiration posée par `_inspire`."""


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> Iterator[World]:
    w = World(tmp_path, migrated_url)
    yield w
    w.close()


def _ignore(world: World, tags: list[str]) -> None:
    pid = world.project_id
    board = world.client.get(f"/api/projects/{pid}/board").json()
    item = next(i for i in board["items"] if i["id"] == ITEM)
    item["ignored_tags"] = tags
    saved = world.client.put(f"/api/projects/{pid}/board", json=board)
    assert saved.status_code == 200, saved.text


def test_a_worker_that_can_ignore_receives_the_disabled_words(world: World) -> None:
    _inspire(world)
    _ignore(world, ["s:white_and_beige", "s:scandinavian", "m:lin", "s:inconnu"])
    world.ask()
    job, served = _claim(world, IGNORING)
    assert served == "heartbeat+style+style-ignore"
    # Ordre du vocabulaire ; « m:lin » (matière) et un mot inconnu ne partent pas.
    assert job["style_ignored"] == ["scandinavian", "white and beige"]


def test_an_old_worker_gets_the_same_job_as_before(world: World) -> None:
    """PO : un poste sans la capacité reçoit un WorkerJob identique à celui de KAN-97."""
    _inspire(world)
    _ignore(world, ["s:bright"])
    world.ask()
    job, served = _claim(world, STYLED)
    assert served == "heartbeat+style"
    # Les champs d'un premier rendu guidé de KAN-97 (test_kan97_style), rien de plus.
    assert set(job) == {"request_id", "lease", "created_at", "style"}
    assert job["style"] == ["salon-clair.png"]


def test_the_words_are_those_of_the_click_and_absent_without_any(world: World) -> None:
    _inspire(world)
    world.ask()
    _ignore(world, ["s:bright"])  # après la demande : elle garde l'état du clic
    job, _ = _claim(world, IGNORING)
    assert "style_ignored" not in job, "absent sans mot désactivé au moment du clic"


def test_the_card_gives_the_keys_of_its_words(world: World) -> None:
    _inspire(world)
    world.ask()
    job, _ = _claim(world, IGNORING)
    _deposit(world, job, ["white and beige", "bright"])
    row: dict[str, Any] = _row(world)
    assert row["guided_by"] == ["blanc et beige", "lumineux"]
    assert row["guided_keys"] == ["white and beige", "bright"]


def test_a_tag_with_a_space_is_refused_by_the_schema(world: World) -> None:
    """Le motif d'`ignored_tags` n'a pas changé : la clé s'écrit avec « _ »."""
    _inspire(world)
    pid = world.project_id
    board = world.client.get(f"/api/projects/{pid}/board").json()
    next(i for i in board["items"] if i["id"] == ITEM)["ignored_tags"] = ["s:white and beige"]
    assert world.client.put(f"/api/projects/{pid}/board", json=board).status_code == 422


def test_the_real_worker_leaves_out_the_disabled_word(world: World, tmp_path: Path) -> None:
    """Le vrai poste (client, boucle, calcul isolé, moteur factice « scandinavian, bright ») :
    il annonce « style-ignore », écrit le mot pour le calcul, qui ne le retient pas."""
    from tests.accounts.test_kan89_worker_flow import _worker

    _inspire(world)
    _ignore(world, ["s:bright"])
    world.ask()
    worker, said = _worker(world, tmp_path)
    worker.cycle()
    assert worker.rendered == 1, said
    assert "style-ignore" in worker.client.served
    row = _row(world)
    assert row["guided_by"] == ["scandinave"] and row["guided_keys"] == ["scandinavian"]
