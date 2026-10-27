"""KAN-93 par l'API, en local : « Supprimer le plan » sur le premier plan (200), le plan
suivant devient premier avec ses cartes, la photo du plan supprimé reste hors carte, Ctrl+Z le
rend ; un espace de travail encore en 0.20.0 sur le disque (pas relu depuis la montée) ne donne
jamais les cartes du plan supprimé au plan suivant."""

from __future__ import annotations

import json
from typing import Any

from tests.integration.test_board_links_api import Local, _png


def _frames(board: dict[str, Any], role: str) -> dict[str | None, dict[str, Any]]:
    return {i["ref"]: i for i in board["items"] if i["kind"] == "frame" and i.get("role") == role}


def _no_orphan(board: dict[str, Any], levels: list[str]) -> None:
    ids = {i["id"] for i in board["items"]}
    for item in board["items"]:
        if item["kind"] == "frame" and item.get("role") in ("photos", "renders"):
            assert item["ref"] in levels or item["ref"] == item["id"], item
        assert item.get("frame") is None or item["frame"] in ids, item
    assert all(k["source"] in ids and k["target"] in ids for k in board["links"])


def _storey_with_photos(w: Local) -> tuple[str, str, str]:
    storey: str = w.client.post(
        f"/api/projects/{w.pid}/levels", json={"name": "Étage", "kind": "storey"}
    ).json()["level_id"]
    ground = w.photo()  # sans niveau : la carte du premier plan
    upstairs = w.client.post(
        f"/api/projects/{w.pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    ).json()["image"]["id"]
    return storey, ground, upstairs


def test_delete_the_first_plan_next_keeps_its_cards_undo_brings_it_back(tmp_path: Any) -> None:
    w = Local(tmp_path)
    first = str(w.level)
    storey, ground, upstairs = _storey_with_photos(w)
    board = w.board()
    assert set(_frames(board, "photos")) == {first, storey}
    assert set(_frames(board, "renders")) == {first, storey}
    card = _frames(board, "photos")[storey]

    done = w.client.delete(f"/api/projects/{w.pid}/levels/{first}")
    assert done.status_code == 200, done.text
    assert list(done.json()["levels"]) == [storey]
    board = w.board()
    assert set(_frames(board, "photos")) == {storey}
    assert set(_frames(board, "renders")) == {storey}
    assert _frames(board, "photos")[storey]["id"] == card["id"]
    assert w.item(board, upstairs)["frame"] == card["id"]
    assert w.item(board, ground)["frame"] is None
    _no_orphan(board, [storey])
    # Une photo nouvelle sans niveau entre dans la carte du plan devenu premier.
    later = w.photo()
    assert w.item(w.board(), later)["frame"] == card["id"]

    assert w.client.post(f"/api/projects/{w.pid}/undo").status_code == 200
    board = w.board()
    assert list(w.client.get(f"/api/projects/{w.pid}").json()["levels"]) == [first, storey]
    assert set(_frames(board, "photos")) == {first, storey}
    assert set(_frames(board, "renders")) == {first, storey}
    _no_orphan(board, [first, storey])


def test_a_board_still_in_0_20_never_hands_the_deleted_cards_to_the_next_plan(
    tmp_path: Any,
) -> None:
    """Le disque garde un espace de travail 0.20.0 (cartes du premier plan sans `ref`) ; le
    premier plan est supprimé avant toute relecture : ses cartes partent, celles de l'étage
    restent seules (une carte Photos et une carte Rendus pour un plan)."""
    w = Local(tmp_path)
    first = str(w.level)
    storey, ground, upstairs = _storey_with_photos(w)
    w.board()
    path = w.store.project_dir(w.pid) / "board.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw["items"]:
        framed = item["kind"] == "frame" and item.get("role") in ("photos", "renders")
        if framed and item.get("ref") == first:
            item["ref"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert w.client.delete(f"/api/projects/{w.pid}/levels/{first}").status_code == 200
    board = w.board()
    assert set(_frames(board, "photos")) == {storey}
    assert set(_frames(board, "renders")) == {storey}
    assert w.item(board, ground)["frame"] is None
    assert w.item(board, upstairs)["frame"] == _frames(board, "photos")[storey]["id"]
    _no_orphan(board, [storey])


def test_the_only_plan_and_a_storey_with_variants_are_still_refused(tmp_path: Any) -> None:
    w = Local(tmp_path)
    only = w.client.delete(f"/api/projects/{w.pid}/levels/{w.level}")
    assert only.status_code == 409 and "seul plan" in only.json()["detail"]
    made = w.client.post(
        f"/api/projects/{w.pid}/levels",
        json={"name": "Variante", "kind": "variant", "source_level_id": str(w.level)},
    )
    assert made.status_code in (200, 201), made.text
    refused = w.client.delete(f"/api/projects/{w.pid}/levels/{w.level}")
    assert refused.status_code == 409 and "variantes" in refused.json()["detail"]
