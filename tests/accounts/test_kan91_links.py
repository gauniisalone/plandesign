"""KAN-91 avec les comptes : tous les éditeurs modifient les liens, même ceux posés par un
autre (D3) ; lecteur et commentateur ne les touchent pas ; ce qu'un éditeur relie ou coupe,
l'autre le relit ; une demande de rendu réaliste sans plan relié est refusée en 409 « Reliez un
plan pour rendre » (D2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from openplan.pipeline.board_links import NO_PLAN
from tests.accounts.test_kan51_comments import _team
from tests.accounts.test_kan52_bell import _png
from tests.accounts.test_kan89_realistic import World


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _of(board: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(k for k in board["links"] if k["kind"] == kind)


def test_every_editor_changes_links_readers_and_commenters_cannot(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    url = f"/api/projects/{pid}/board/items"
    board = owner.get(f"/api/projects/{pid}/board").json()
    guides = _of(board, "guides_style")
    for role in ("viewer", "commenter"):
        denied = m[role].patch(url, json={"links_delete": [guides["id"]]})
        assert denied.status_code == 403, (role, denied.text)
    # L'éditeur coupe un lien posé automatiquement ; le propriétaire le voit à la relecture.
    cut = m["editor"].patch(url, json={"links_delete": [guides["id"]]})
    assert cut.status_code == 200, cut.text
    seen = owner.get(f"/api/projects/{pid}/board").json()
    assert not any(k["kind"] == "guides_style" for k in seen["links"])
    # Le propriétaire le retend ; l'éditeur, à son tour, le relit et peut le couper (D3).
    again = {
        "id": str(uuid4()),
        "source": guides["source"],
        "target": guides["target"],
        "kind": "guides_style",
    }
    retied = owner.patch(url, json={"links_set": [again]})
    assert retied.status_code == 200, retied.text
    mine = _of(m["editor"].get(f"/api/projects/{pid}/board").json(), "guides_style")
    assert mine["auto"] is False and mine["updated_by"] != "local"
    assert m["editor"].patch(url, json={"links_delete": [mine["id"]]}).status_code == 200
    # Lecteur : il lit ce qu'une génération lit.
    plan = next(i for i in seen["items"] if i["kind"] == "plan")
    inputs = m["viewer"].get(f"/api/projects/{pid}/board/inputs/{plan['id']}")
    assert inputs.status_code == 200 and inputs.json()["kind"] == "plan"


def test_no_realistic_render_without_a_linked_plan_d2(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    pid = world.project_id
    board = world.client.get(f"/api/projects/{pid}/board").json()
    view = _of(board, "renders_view")
    url = f"/api/projects/{pid}/board/items"
    assert world.client.patch(url, json={"links_delete": [view["id"]]}).status_code == 200
    refused = world.client.post(
        f"/api/projects/{pid}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id)},
    )
    assert refused.status_code == 409 and refused.json()["detail"] == NO_PLAN
    renders = next(i for i in board["items"] if i.get("role") == "renders")
    inputs = world.client.get(f"/api/projects/{pid}/board/inputs/{renders['id']}").json()
    assert inputs["can_render"] is False and inputs["reason"] == NO_PLAN
    # Plan relié de nouveau : la demande passe.
    link = {**view, "id": str(uuid4())}
    link.pop("updated_at", None)
    link.pop("updated_by", None)
    link.pop("auto", None)
    assert world.client.patch(url, json={"links_set": [link]}).status_code == 200
    assert world.ask()["state"] == "pending"
    world.close()


def test_two_levels_each_photo_card_feeds_only_its_plan(tmp_path: Path, migrated_url: str) -> None:
    """Le propriétaire ajoute un étage ; un éditeur importe une photo depuis l'étage : elle
    entre dans « Photos de la pièce · Étage » et ne construit que l'étage, ce que le lecteur
    relit."""
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    made = owner.post(f"/api/projects/{pid}/levels", json={"name": "Étage", "kind": "storey"})
    assert made.status_code in (200, 201), made.text
    storey = made.json()["level_id"]
    sent = m["editor"].post(
        f"/api/projects/{pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    )
    assert sent.status_code == 201, sent.text
    image = sent.json()["image"]["id"]
    board = m["viewer"].get(f"/api/projects/{pid}/board").json()
    cards = {i["ref"]: i for i in board["items"] if i["kind"] == "frame" and i["role"] == "photos"}
    # KAN-93 : la carte du premier plan porte la `ref` de son plan, comme celle de l'étage.
    first = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    assert set(cards) == {first, storey} and cards[storey]["text"] == "Photos de la pièce · Étage"
    photo = next(i for i in board["items"] if i.get("ref") == image)
    assert photo["frame"] == cards[storey]["id"]
    plans = {i["ref"]: i["id"] for i in board["items"] if i["kind"] == "plan"}
    for ref, expected in ((storey, [image]), (next(r for r in plans if r != storey), [])):
        inputs = m["viewer"].get(f"/api/projects/{pid}/board/inputs/{plans[ref]}").json()
        assert inputs["photos"] == expected, ref


def test_deleting_a_plan_is_for_editors_and_every_member_sees_it(
    tmp_path: Path, migrated_url: str
) -> None:
    """Retours de la MEP 14 : « Supprimer le plan » est un geste d'éditeur (403 pour le lecteur
    et le commentateur) ; l'éditeur supprime l'étage du propriétaire, tous relisent un
    espace de travail sans sa carte Photos ni ses liens, la photo hors carte."""
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    storey = owner.post(
        f"/api/projects/{pid}/levels", json={"name": "Étage", "kind": "storey"}
    ).json()["level_id"]
    image = owner.post(
        f"/api/projects/{pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    ).json()["image"]["id"]
    url = f"/api/projects/{pid}/levels/{storey}"
    for role in ("viewer", "commenter"):
        assert m[role].delete(url).status_code == 403, role
    done = m["editor"].delete(url)
    assert done.status_code == 200, done.text
    for who in (owner, m["viewer"]):
        board = who.get(f"/api/projects/{pid}/board").json()
        assert not any(i.get("ref") == storey for i in board["items"])
        assert next(i for i in board["items"] if i.get("ref") == image)["frame"] is None
        ids = {i["id"] for i in board["items"]}
        assert all(k["source"] in ids and k["target"] in ids for k in board["links"])
