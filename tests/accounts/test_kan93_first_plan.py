"""KAN-93 avec les comptes : supprimer le premier plan reste un geste d'éditeur (403 pour le
lecteur et le commentateur, même route que les autres plans, matrice des droits inchangée) ;
l'éditeur supprime le premier plan du propriétaire, et chacun relit un espace de travail où
l'étage garde ses cartes, sans carte orpheline."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.accounts.test_kan51_comments import _team
from tests.accounts.test_kan52_bell import _png


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def test_an_editor_deletes_the_first_plan_every_member_sees_the_storey_keep_its_cards(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    first = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    storey = owner.post(
        f"/api/projects/{pid}/levels", json={"name": "Étage", "kind": "storey"}
    ).json()["level_id"]
    image = owner.post(
        f"/api/projects/{pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    ).json()["image"]["id"]
    before = owner.get(f"/api/projects/{pid}/board").json()
    card = next(i for i in before["items"] if i.get("role") == "photos" and i["ref"] == storey)
    url = f"/api/projects/{pid}/levels/{first}"
    for role in ("viewer", "commenter"):
        assert m[role].delete(url).status_code == 403, role
    done = m["editor"].delete(url)
    assert done.status_code == 200, done.text
    for who in (owner, m["viewer"], m["editor"]):
        board = who.get(f"/api/projects/{pid}/board").json()
        assert not any(i.get("ref") == first for i in board["items"])
        photos = [i for i in board["items"] if i["kind"] == "frame" and i["role"] == "photos"]
        assert [i["id"] for i in photos] == [card["id"]]
        assert next(i for i in board["items"] if i.get("ref") == image)["frame"] == card["id"]
        ids = {i["id"] for i in board["items"]}
        assert all(k["source"] in ids and k["target"] in ids for k in board["links"])
    # Ctrl+Z de l'éditeur : le plan revient pour tous, en premier.
    assert m["editor"].post(f"/api/projects/{pid}/undo").status_code == 200
    levels = list(m["viewer"].get(f"/api/projects/{pid}").json()["levels"])
    assert levels == [first, storey]
