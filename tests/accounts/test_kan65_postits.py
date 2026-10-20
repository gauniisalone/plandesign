"""KAN-65 : post-its de couleur. La couleur est un champ de l'élément (pas de nouvelle table) :
huit valeurs, absente pour une note d'avant (jaune à l'écran, sans migration) ; elle s'écrit
élément par élément (B2c) ; le journal d'activité dit « a changé la couleur » quand seule la
couleur a changé ; lecteurs et commentateurs ne la changent pas ; en local, à l'identique."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.board import board_changes
from openplan.schemas.board import Board, BoardItem
from tests.accounts.test_kan51_comments import _team


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _note(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "kind": "note",
        "x": 0,
        "y": 0,
        "w": 200,
        "h": 200,
        "text": text,
        **extra,
    }


def _activity(client: TestClient, pid: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/projects/{pid}/activity")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _local(tmp_path: Path) -> tuple[TestClient, str]:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    local.get(f"/api/projects/{pid}/board")  # mise en page de départ (pas un geste)
    return local, pid


def test_the_color_is_a_field_with_eight_values_and_an_old_note_has_none() -> None:
    for color in ("yellow", "orange", "pink", "violet", "blue", "green", "gray", "white"):
        assert BoardItem.model_validate(_note("x", color=color)).color == color
    with pytest.raises(ValidationError):
        BoardItem.model_validate(_note("x", color="red"))
    with pytest.raises(ValidationError):
        BoardItem.model_validate(_note("x", color="#ff0000"))
    # Une note d'avant KAN-65 se relit telle quelle : sans couleur (jaune à l'écran).
    old = BoardItem.model_validate(_note("ancienne"))
    assert old.color is None
    assert "color" in old.model_dump()


def test_only_a_color_change_counts_as_recolored() -> None:
    a, b = BoardItem.model_validate(_note("a")), BoardItem.model_validate(_note("b"))
    before = Board(items=[a, b])
    recolored = Board(items=[a.model_copy(update={"color": "blue"}), b])
    changes = board_changes(before, recolored)
    assert (changes.changed, changes.recolored) == (1, 1)
    # Couleur et texte changés : une modification, pas seulement une couleur.
    both = Board(items=[a.model_copy(update={"color": "blue", "text": "c"}), b])
    assert (board_changes(before, both).changed, board_changes(before, both).recolored) == (1, 0)
    moved = Board(items=[a.model_copy(update={"x": 50.0}), b])
    assert board_changes(before, moved).recolored == 0


def test_local_color_is_saved_item_by_item_and_journaled(tmp_path: Path) -> None:
    local, pid = _local(tmp_path)
    url = f"/api/projects/{pid}/board/items"
    first, second = _note("Canapé"), _note("Lampe")
    assert local.patch(url, json={"set": [first, second]}).status_code == 200
    # Un geste sur un seul élément ne touche pas l'autre (dernière écriture par objet).
    saved = local.patch(url, json={"set": [{**first, "color": "pink"}]})
    assert saved.status_code == 200, saved.text
    items = {i["id"]: i for i in local.get(f"/api/projects/{pid}/board").json()["items"]}
    assert items[first["id"]]["color"] == "pink"
    assert items[second["id"]]["color"] is None
    line = _activity(local, pid)[0]
    assert line["recolored"] == 1 and line["changed"] == 1
    # Une couleur hors palette est refusée.
    assert local.patch(url, json={"set": [{**second, "color": "red"}]}).status_code == 422


def test_editor_recolors_others_see_it_reader_and_commenter_cannot(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    url = f"/api/projects/{pid}/board/items"
    owner.get(f"/api/projects/{pid}/board")
    a, b = _note("Canapé"), _note("Tapis")
    assert owner.patch(url, json={"set": [a, b]}).status_code == 200
    # L'éditeur change la couleur des deux en un geste.
    recolor = {"set": [{**a, "color": "green"}, {**b, "color": "green"}]}
    assert m["editor"].patch(url, json=recolor).status_code == 200
    seen = {i["id"]: i for i in owner.get(f"/api/projects/{pid}/board").json()["items"]}
    assert seen[a["id"]]["color"] == seen[b["id"]]["color"] == "green"
    line = next(
        i
        for i in _activity(owner, pid)
        if i["type"] == "board.changed" and i["actor_id"] != "local" and not i["mine"]
    )
    assert (line["changed"], line["recolored"], line["added"]) == (2, 2, 0)
    # Lecteur et commentateur : la couleur ne change pas.
    for role in ("viewer", "commenter"):
        denied = m[role].patch(url, json={"set": [{**a, "color": "blue"}]})
        assert denied.status_code == 403, (role, denied.text)
    after = {i["id"]: i for i in owner.get(f"/api/projects/{pid}/board").json()["items"]}
    assert after[a["id"]]["color"] == "green"
