"""KAN-91 : cartes reliées par l'API, en local. Liens automatiques posés à la lecture, liens
écrits lien par lien (422 entre cartes qui ne se relient pas, 409 pour un deuxième plan),
coupure retenue, ce que lit une génération (C1) : plan construit seulement depuis la carte
Photos reliée, références de style seulement depuis les cartes Inspirations reliées."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.board_rules import NO_PHOTO_CARD, NOT_A_MEMBER, style_references
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore
from tests.factories import rect_room_project


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


class Local:
    def __init__(self, tmp_path: Path) -> None:
        project = rect_room_project(4.0, 3.0)
        self.store = ProjectStore(tmp_path / "data")
        self.store.save(project)
        self.app = create_app(store=self.store, settings=Settings(data_dir=tmp_path / "data"))
        self.client = TestClient(self.app)
        self.pid = project.id
        self.level = next(iter(project.levels))
        self.url = f"/api/projects/{self.pid}/board/items"

    def board(self) -> dict[str, Any]:
        out: dict[str, Any] = self.client.get(f"/api/projects/{self.pid}/board").json()
        return out

    def card(self, board: dict[str, Any], role: str) -> dict[str, Any]:
        return next(i for i in board["items"] if i.get("role") == role and i["kind"] == "frame")

    def plan(self, board: dict[str, Any]) -> dict[str, Any]:
        return next(i for i in board["items"] if i["kind"] == "plan")

    def patch(self, body: dict[str, Any]) -> Any:
        return self.client.patch(self.url, json=body)

    def photo(self) -> str:
        r = self.client.post(
            f"/api/projects/{self.pid}/images", files={"file": ("p.png", _png(), "image/png")}
        )
        assert r.status_code == 201, r.text
        image_id: str = r.json()["image"]["id"]
        return image_id

    def item(self, board: dict[str, Any], ref: str) -> dict[str, Any]:
        return next(i for i in board["items"] if i.get("ref") == ref)


def _link(source: str, target: str, kind: str = "feeds_plan") -> dict[str, str]:
    return {"id": str(uuid4()), "source": source, "target": target, "kind": kind}


def test_the_board_is_linked_from_the_start(tmp_path: Path) -> None:
    w = Local(tmp_path)
    board = w.board()
    kinds = sorted(k["kind"] for k in board["links"])
    assert kinds == ["feeds_plan", "guides_style", "renders_view"]
    assert all(k["auto"] for k in board["links"])
    plan = w.plan(board)
    feeds = next(k for k in board["links"] if k["kind"] == "feeds_plan")
    assert feeds["source"] == w.card(board, "photos")["id"] and feeds["target"] == plan["id"]
    # La synchronisation suivante n'en ajoute pas.
    assert len(w.board()["links"]) == 3


def test_links_are_checked_and_a_cut_is_remembered(tmp_path: Path) -> None:
    w = Local(tmp_path)
    board = w.board()
    photos, renders, plan = w.card(board, "photos"), w.card(board, "renders"), w.plan(board)
    bad = w.patch({"links_set": [_link(photos["id"], renders["id"])]})
    assert bad.status_code == 422 and "se relie à un plan" in bad.json()["detail"]
    # Une deuxième carte Photos ne peut pas construire le même plan (409).
    second = {
        "id": str(uuid4()), "kind": "frame", "x": -2000, "y": 0, "w": 600, "h": 400,
        "role": "photos", "text": "Photos · Étage",
    }  # fmt: skip
    assert w.patch({"set": [second]}).status_code == 200
    taken = w.patch({"links_set": [_link(second["id"], plan["id"])]})
    assert taken.status_code == 409 and "lit déjà le node" in taken.json()["detail"]
    # Couper puis relire : le lien automatique ne revient pas.
    feeds = next(k for k in board["links"] if k["kind"] == "feeds_plan")
    cut = w.patch({"links_delete": [feeds["id"]]})
    assert (
        cut.status_code == 200 and f"{feeds['source']}>{feeds['target']}" in cut.json()["cut_links"]
    )
    assert not any(k["kind"] == "feeds_plan" for k in w.board()["links"])
    # La deuxième carte peut maintenant construire le plan.
    ok = w.patch({"links_set": [_link(second["id"], plan["id"])]})
    assert ok.status_code == 200, ok.text
    link = next(k for k in ok.json()["links"] if k["source"] == second["id"])
    assert link["auto"] is False and link["updated_by"] == "user"


def test_the_plan_reads_only_its_photo_card_c1(tmp_path: Path) -> None:
    w = Local(tmp_path)
    member = w.photo()
    board = w.board()
    plan = w.plan(board)
    inputs = w.client.get(f"/api/projects/{w.pid}/board/inputs/{plan['id']}").json()
    assert inputs["kind"] == "plan" and inputs["photos"] == [member]
    # Sortie de la carte : elle ne compte plus, et le serveur refuse d'en construire le plan.
    out = {**w.item(board, member), "frame": None, "x": 9000}
    assert w.patch({"set": [out]}).status_code == 200
    inputs = w.client.get(f"/api/projects/{w.pid}/board/inputs/{plan['id']}").json()
    assert inputs["photos"] == []
    refused = w.client.post(f"/api/projects/{w.pid}/levels/{w.level}/images/{member}/autoplan")
    assert refused.status_code == 409 and refused.json()["detail"] == NOT_A_MEMBER
    detect = w.client.post(
        f"/api/projects/{w.pid}/levels/{w.level}/detect", json={"image_id": member}
    )
    assert detect.status_code == 409
    # Sans carte Photos reliée : la raison est dite.
    feeds = next(k for k in w.board()["links"] if k["kind"] == "feeds_plan")
    w.patch({"links_delete": [feeds["id"]]})
    again = w.client.post(f"/api/projects/{w.pid}/levels/{w.level}/images/{member}/autoplan")
    assert again.status_code == 409 and again.json()["detail"] == NO_PHOTO_CARD


def test_a_new_photo_enters_its_card_and_feeds_the_plan(tmp_path: Path) -> None:
    w = Local(tmp_path)
    image = w.photo()
    started = w.client.post(f"/api/projects/{w.pid}/levels/{w.level}/images/{image}/autoplan")
    assert started.status_code == 202, started.text


def test_style_references_are_the_members_of_linked_inspiration_cards(tmp_path: Path) -> None:
    w = Local(tmp_path)
    board = w.board()
    insp = w.card(board, "inspirations")
    renders = w.card(board, "renders")
    joined = [
        w.client.post(
            f"/api/projects/{w.pid}/media", files={"file": (f"{k}.png", _png(), "image/png")}
        ).json()
        for k in range(3)
    ]

    def image(ref: str, frame: str | None, x: float) -> dict[str, Any]:
        return {
            "id": str(uuid4()), "kind": "file", "x": x, "y": 0, "w": 280, "h": 190,
            "ref": ref, "frame": frame, "reference": True,
        }  # fmt: skip

    second = {
        "id": str(uuid4()), "kind": "frame", "x": 3000, "y": 0, "w": 600, "h": 400,
        "role": "inspirations", "text": "Inspirations · B",
    }  # fmt: skip
    items = [
        image(joined[0]["id"], insp["id"], insp["x"] + 24),
        image(joined[1]["id"], None, 9000),  # libre : jamais utilisée
        second,
        image(joined[2]["id"], second["id"], 3024),
    ]
    links = [_link(second["id"], renders["id"], "guides_style")]
    saved = w.patch({"set": items, "links_set": links})
    assert saved.status_code == 200, saved.text
    free = next(i for i in saved.json()["items"] if i["ref"] == joined[1]["id"])
    assert free["reference"] is False, "hors carte : « Référence » ne tient plus"
    project = w.store.load(w.pid)
    refs = style_references(project, w.store, w.level, 4)
    assert [r[:2] for r in refs] == [("file", joined[0]["id"]), ("file", joined[2]["id"])], (
        "tour à tour"
    )
    assert refs[0][2] == "Image", "sans nom sur la carte : sa sorte"
    inputs = w.client.get(f"/api/projects/{w.pid}/board/inputs/{renders['id']}").json()
    assert inputs["can_render"] is True and inputs["reason"] is None
    assert len(inputs["inspiration_cards"]) == 2


def test_an_inspiration_is_refused_by_the_photo_card_c3(tmp_path: Path) -> None:
    w = Local(tmp_path)
    board = w.board()
    photos = w.card(board, "photos")
    joined = w.client.post(
        f"/api/projects/{w.pid}/media", files={"file": ("i.png", _png(), "image/png")}
    ).json()
    item = {
        "id": str(uuid4()), "kind": "file", "x": photos["x"] + 24, "y": photos["y"] + 24,
        "w": 280, "h": 190, "ref": joined["id"], "frame": photos["id"],
    }  # fmt: skip
    refused = w.patch({"set": [item]})
    assert refused.status_code == 422 and "seule une photo réelle" in refused.json()["detail"]


def test_the_hover_sentence_and_unknown_cards(tmp_path: Path) -> None:
    w = Local(tmp_path)
    w.photo()
    board = w.board()
    feeds = next(k for k in board["links"] if k["kind"] == "feeds_plan")
    summary = w.client.get(f"/api/projects/{w.pid}/board/links/{feeds['id']}").json()
    assert summary["sentence"].startswith("La photo de ce node construit Plan")
    assert summary["auto"] is True and "éditeurs" in summary["rights"]
    assert w.client.get(f"/api/projects/{w.pid}/board/links/{uuid4()}").status_code == 404
    assert w.client.get(f"/api/projects/{w.pid}/board/inputs/{uuid4()}").status_code == 404


def test_an_old_tab_without_links_keeps_them(tmp_path: Path) -> None:
    """Un onglet d'avant KAN-91 enregistre le tableau entier (PUT) sans liens : ils restent."""
    w = Local(tmp_path)
    board = w.board()
    old = {k: v for k, v in board.items() if k not in ("links", "cut_links")}
    out = w.client.put(f"/api/projects/{w.pid}/board", json=old)
    assert out.status_code == 200, out.text
    assert len(out.json()["links"]) == 3
    assert UUID(out.json()["links"][0]["id"])


def test_each_level_has_its_photo_card_and_feeds_only_its_plan(tmp_path: Path) -> None:
    """KAN-91 : « Nouvel étage » pose sa carte « Photos de la pièce · Étage », reliée à son plan ;
    une photo importée depuis l'étage entre dans sa carte et ne construit que l'étage."""
    w = Local(tmp_path)
    old = w.photo()
    made = w.client.post(f"/api/projects/{w.pid}/levels", json={"name": "Étage", "kind": "storey"})
    assert made.status_code in (200, 201), made.text
    storey = made.json()["level_id"]
    board = w.board()
    cards = {i["ref"]: i for i in board["items"] if i["kind"] == "frame" and i["role"] == "photos"}
    assert set(cards) == {None, storey}
    assert cards[storey]["text"] == "Photos de la pièce · Étage"
    assert cards[None]["text"].startswith("Photos de la pièce · ")
    plans = {i["ref"]: i["id"] for i in board["items"] if i["kind"] == "plan"}
    feeds = {(k["source"], k["target"]) for k in board["links"] if k["kind"] == "feeds_plan"}
    assert feeds == {
        (cards[None]["id"], plans[str(w.level)]),
        (cards[storey]["id"], plans[storey]),
    }
    # L'ancienne photo reste dans la carte du premier plan.
    assert w.item(board, old)["frame"] == cards[None]["id"]

    r = w.client.post(
        f"/api/projects/{w.pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    )
    assert r.status_code == 201, r.text
    upstairs = r.json()["image"]["id"]
    board = w.board()
    assert w.item(board, upstairs)["frame"] == cards[storey]["id"]
    inputs = w.client.get(f"/api/projects/{w.pid}/board/inputs/{plans[storey]}").json()
    assert inputs["photos"] == [upstairs]
    ground = w.client.get(f"/api/projects/{w.pid}/board/inputs/{plans[str(w.level)]}").json()
    assert ground["photos"] == [old]
    # La photo de l'étage ne construit pas le rez-de-chaussée, et inversement.
    refused = w.client.post(f"/api/projects/{w.pid}/levels/{w.level}/images/{upstairs}/autoplan")
    assert refused.status_code == 409 and refused.json()["detail"] == NOT_A_MEMBER
    refused = w.client.post(f"/api/projects/{w.pid}/levels/{storey}/images/{old}/autoplan")
    assert refused.status_code == 409 and refused.json()["detail"] == NOT_A_MEMBER


def test_delete_a_plan_cards_and_links_go_photos_stay_undo_brings_it_back(
    tmp_path: Path,
) -> None:
    """Retours de la MEP 14 : « Supprimer le plan » par l'API ; 409 pour le premier plan ;
    les photos de la carte de l'étage restent, hors carte ; Ctrl+Z rend le plan."""
    w = Local(tmp_path)
    storey = w.client.post(
        f"/api/projects/{w.pid}/levels", json={"name": "Étage", "kind": "storey"}
    ).json()["level_id"]
    r = w.client.post(
        f"/api/projects/{w.pid}/images",
        params={"level_id": storey},
        files={"file": ("e.png", _png(), "image/png")},
    )
    upstairs = r.json()["image"]["id"]
    first = w.client.delete(f"/api/projects/{w.pid}/levels/{w.level}")
    assert first.status_code == 409 and "premier plan" in first.json()["detail"]
    assert w.client.delete(f"/api/projects/{w.pid}/levels/{uuid4()}").status_code == 404
    done = w.client.delete(f"/api/projects/{w.pid}/levels/{storey}")
    assert done.status_code == 200, done.text
    assert list(done.json()["levels"]) == [str(w.level)]
    board = w.board()
    assert not any(i.get("ref") == storey for i in board["items"])
    assert len([i for i in board["items"] if i.get("role") == "photos"]) == 1
    photo = w.item(board, upstairs)
    assert photo["frame"] is None
    ids = {i["id"] for i in board["items"]}
    assert all(k["source"] in ids and k["target"] in ids for k in board["links"])
    # Ctrl+Z : le plan revient, avec une carte Photos vide reliée ; la photo reste hors carte.
    assert w.client.post(f"/api/projects/{w.pid}/undo").status_code == 200
    board = w.board()
    card = next(i for i in board["items"] if i.get("role") == "photos" and i["ref"] == storey)
    plan = next(i for i in board["items"] if i["kind"] == "plan" and i["ref"] == storey)
    assert any(k["source"] == card["id"] and k["target"] == plan["id"] for k in board["links"])
    assert w.item(board, upstairs)["frame"] is None


def test_a_hand_made_inspirations_card_links_by_hand_to_several_renders(tmp_path: Path) -> None:
    """N-N : une carte Inspirations ajoutée (`ref` = son id) n'est reliée à rien ; reliée à la
    main à deux cartes Rendus, chacune lit les deux cartes Inspirations (poids égal)."""
    w = Local(tmp_path)
    w.client.post(f"/api/projects/{w.pid}/levels", json={"name": "Étage", "kind": "storey"})
    card_id = str(uuid4())
    hand = {
        "id": card_id,
        "kind": "frame",
        "x": -900,
        "y": 0,
        "w": 624,
        "h": 374,
        "role": "inspirations",
        "ref": card_id,
        "text": "Inspirations · bois",
    }
    assert w.patch({"set": [hand]}).status_code == 200
    board = w.board()
    frames = [i for i in board["items"] if i.get("role") == "inspirations"]
    assert len(frames) == 2, "la carte ajoutée reste, la carte de départ aussi"
    assert not any(k["source"] == card_id for k in board["links"])
    renders = [i["id"] for i in board["items"] if i.get("role") == "renders"]
    assert len(renders) == 2
    out = w.patch({"links_set": [_link(card_id, r, "guides_style") for r in renders]})
    assert out.status_code == 200, out.text
    for r in renders:
        guides = [
            k for k in out.json()["links"] if k["kind"] == "guides_style" and k["target"] == r
        ]
        assert len(guides) == 2, r


def test_a_second_photo_link_to_a_level_is_refused_409(tmp_path: Path) -> None:
    w = Local(tmp_path)
    storey = w.client.post(
        f"/api/projects/{w.pid}/levels", json={"name": "Étage", "kind": "storey"}
    ).json()["level_id"]
    board = w.board()
    first = next(i for i in board["items"] if i.get("role") == "photos" and i["ref"] is None)
    plan2 = next(i for i in board["items"] if i["kind"] == "plan" and i["ref"] == storey)
    out = w.patch({"links_set": [_link(first["id"], plan2["id"])]})
    assert out.status_code == 409, out.text
