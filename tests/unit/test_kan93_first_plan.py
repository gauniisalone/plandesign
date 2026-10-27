"""KAN-93 : premier plan explicite (schéma 0.21.0). Chaque carte Photos et Rendus porte la `ref`
de son plan, celles du premier comprises (migration à la lecture de l'espace de travail) ; le
premier plan se supprime, le suivant devient premier avec ses cartes ; Ctrl+Z le rend ; aucune
carte orpheline ; la descente vers 0.20.0 retire la `ref` des cartes du premier plan."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from openplan.pipeline.board import explicit_first, sync_board
from openplan.scene import history
from openplan.scene.downgrade import _implicit_first
from openplan.scene.plans import NewPlan, add_plan, remove_plan
from openplan.schemas.board import Board, BoardItem, BoardLink
from openplan.schemas.scene import ImageRef, Project
from openplan.validation.invariants import validate_invariants
from tests.factories import rect_room_project


def _two_plans() -> tuple[Project, str, str]:
    project = rect_room_project()
    after, storey = add_plan(project, NewPlan(name="Étage", kind="storey"))
    return after, str(next(iter(project.levels))), str(storey)


def _cards(board: Board, role: str) -> dict[str | None, BoardItem]:
    return {i.ref: i for i in board.items if i.kind == "frame" and i.role == role}


def _no_orphan(board: Board, project: Project) -> None:
    """Aucune carte orpheline : chaque carte Photos ou Rendus désigne un plan du projet (ou
    elle-même, ajoutée à la main) ; chaque élément rangé l'est dans une carte présente ; chaque
    lien relie deux éléments présents."""
    levels = {str(lid) for lid in project.levels}
    ids = {i.id for i in board.items}
    for item in board.items:
        if item.kind == "frame" and item.role in ("photos", "renders"):
            assert item.ref in levels or item.ref == str(item.id), item
        assert item.frame is None or item.frame in ids, item
    assert all(k.source in ids and k.target in ids for k in board.links)


def test_a_fresh_board_gives_the_first_plan_its_ref(tmp_path: Path) -> None:
    project, first, storey = _two_plans()
    board, _ = sync_board(None, project, tmp_path / "runs")
    assert set(_cards(board, "photos")) == {first, storey}
    assert set(_cards(board, "renders")) == {first, storey}
    _no_orphan(board, project)
    assert sync_board(board, project, tmp_path / "runs") == (board, False), "stable"


def test_an_old_board_is_migrated_at_reading_cards_and_dismissals(tmp_path: Path) -> None:
    """Un espace de travail 0.20.0 : les cartes du premier plan sans `ref`, une carte retirée à
    la main notée `frame:renders`. À la lecture, les mêmes cartes (mêmes ids, mêmes photos)
    reçoivent la `ref` du premier plan ; le retrait est gardé."""
    project, first, _storey = _two_plans()
    board, _ = sync_board(None, project, tmp_path / "runs")
    old_items = [
        i.model_copy(update={"ref": None}) if i.kind == "frame" and i.ref == first else i
        for i in board.items
    ]
    old = board.model_copy(update={"items": old_items, "dismissed": ["frame:inspirations"]})
    migrated, changed = sync_board(old, project, tmp_path / "runs")
    assert changed
    assert _cards(migrated, "photos")[first].id == _cards(board, "photos")[first].id
    assert _cards(migrated, "renders")[first].id == _cards(board, "renders")[first].id
    assert None not in _cards(migrated, "photos") and None not in _cards(migrated, "renders")
    items, dismissed = explicit_first(old_items, ["frame:renders", "frame:photos", "x"], first)
    assert dismissed == [f"frame:renders:{first}", f"frame:photos:{first}", "x"]
    assert [i.ref for i in items if i.kind == "frame" and i.role == "photos"].count(first) == 1


def test_without_a_plan_nothing_moves() -> None:
    card = BoardItem(kind="frame", x=0, y=0, w=10, h=10, role="photos")
    assert explicit_first([card], ["frame:photos"], None) == ([card], ["frame:photos"])


def test_an_explicit_card_already_there_is_never_doubled() -> None:
    """Une carte qui porte déjà la `ref` du premier plan : la carte sans `ref` n'est pas
    adoptée (deux cartes Photos pour un plan, jamais)."""
    mine = BoardItem(kind="frame", x=0, y=0, w=10, h=10, role="photos", ref="L1")
    bare = BoardItem(kind="frame", x=0, y=0, w=10, h=10, role="photos")
    items, _ = explicit_first([mine, bare], [], "L1")
    assert [i.ref for i in items] == ["L1", None]


def test_deleting_the_first_plan_the_next_keeps_its_cards_undo_brings_it_back(
    tmp_path: Path,
) -> None:
    project, first, storey = _two_plans()
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    upstairs = _cards(board, "photos")[storey]
    gone = remove_plan(project, next(iter(project.levels)))
    assert [str(lid) for lid in gone.levels] == [storey]
    after, _ = sync_board(board, gone, runs)
    # Le plan suivant garde sa carte Photos (le même id) et sa carte Rendus ; aucune en double.
    assert set(_cards(after, "photos")) == {storey}
    assert _cards(after, "photos")[storey].id == upstairs.id
    assert set(_cards(after, "renders")) == {storey}
    # Seul plan restant : les titres redeviennent courts.
    assert _cards(after, "photos")[storey].text == "Photos de la pièce"
    _no_orphan(after, gone)
    feeds = [k for k in after.links if k.kind == "feeds_plan"]
    assert len(feeds) == 1 and feeds[0].source == upstairs.id
    # Ctrl+Z : le premier plan revient à sa place, avec ses cartes.
    back = history.undo(gone)
    assert [str(lid) for lid in back.levels] == [first, storey]
    again, _ = sync_board(after, back, runs)
    assert set(_cards(again, "photos")) == {first, storey}
    assert set(_cards(again, "renders")) == {first, storey}
    assert _cards(again, "photos")[storey].id == upstairs.id
    _no_orphan(again, back)
    assert len([k for k in again.links if k.kind == "feeds_plan"]) == 2


def test_the_first_plan_goes_with_its_measurements_undo_puts_both_back_in_place() -> None:
    """Un plan mesuré (le premier, le plus souvent) : ses mesures partent avec lui (sinon le
    projet serait invalide, 422) ; Ctrl+Z rend le plan à sa place (premier) et ses mesures ;
    Ctrl+Y le retire de nouveau."""
    project, first, storey = _two_plans()
    assert project.measurements, "la pièce de test porte des mesures"
    gone = remove_plan(project, next(iter(project.levels)))
    assert validate_invariants(gone).valid
    assert not gone.measurements
    back = history.undo(gone)
    assert [str(lid) for lid in back.levels] == [first, storey]
    assert back.measurements == project.measurements
    assert back.levels == project.levels
    again = history.redo(back)
    assert [str(lid) for lid in again.levels] == [storey] and not again.measurements


def test_deleting_a_middle_plan_keeps_the_order_on_undo() -> None:
    project, first, storey = _two_plans()
    three, attic = add_plan(project, NewPlan(name="Combles", kind="storey"))
    gone = remove_plan(three, UUID(storey))
    assert [str(lid) for lid in gone.levels] == [first, str(attic)]
    back = history.undo(gone)
    assert [str(lid) for lid in back.levels] == [first, storey, str(attic)]
    # Le dernier plan : le patch d'origine suffit (il revient à la fin).
    last = remove_plan(three, attic)
    assert list(history.undo(last).levels) == list(three.levels)


def test_the_photos_of_the_deleted_first_plan_stay_out_of_any_card(tmp_path: Path) -> None:
    """Hors périmètre (ticket) : les photos du plan supprimé ne sont pas rangées d'office dans
    la carte du plan suivant ; elles restent sur l'espace de travail, hors carte."""
    project, first, storey = _two_plans()
    image = ImageRef(
        path="images/x.jpg", sha256="0" * 64, width_px=10, height_px=10, exif_stripped=True
    )
    project = project.model_copy(update={"images": {image.id: image}})
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    photo = next(i for i in board.items if i.kind == "photo" and i.ref == str(image.id))
    assert photo.frame == _cards(board, "photos")[first].id, "une photo nouvelle : premier plan"
    gone = remove_plan(project, next(iter(project.levels)))
    after, _ = sync_board(board, gone, runs)
    kept = next(i for i in after.items if i.id == photo.id)
    assert kept.frame is None
    assert not any(i.frame == _cards(after, "photos")[storey].id for i in after.items)
    _no_orphan(after, gone)


def test_the_start_moodboard_follows_the_next_plan_a_hand_link_goes_cleanly(
    tmp_path: Path,
) -> None:
    """Ajout du PO : la carte Inspirations (Moodboard) de départ guide chaque carte Rendus ; le
    premier plan supprimé, elle guide toujours celle du plan devenu premier. Une carte ajoutée à
    la main et reliée à la main aux Rendus du premier plan : ce lien part avec la carte Rendus
    (jamais un lien vers rien), la carte reste, sans lien reporté d'office."""
    project, first, storey = _two_plans()
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    start = next(i for i in board.items if i.role == "inspirations" and i.ref is None)
    hand = BoardItem(kind="frame", x=-900, y=0, w=600, h=300, role="inspirations", text="Bois")
    hand = hand.model_copy(update={"ref": str(hand.id)})
    first_renders = _cards(board, "renders")[first]
    tied = BoardLink(source=hand.id, target=first_renders.id, kind="guides_style")
    board = board.model_copy(update={"items": [*board.items, hand], "links": [*board.links, tied]})
    gone = remove_plan(project, next(iter(project.levels)))
    after, _ = sync_board(board, gone, runs)
    left = _cards(after, "renders")[storey]
    guides = {(k.source, k.target) for k in after.links if k.kind == "guides_style"}
    assert guides == {(start.id, left.id)}
    assert any(i.id == hand.id for i in after.items), "la carte ajoutée reste"
    _no_orphan(after, gone)


def test_downgrade_takes_the_ref_off_the_first_plan_cards(tmp_path: Path) -> None:
    """0.21.0 → 0.20.0 : les cartes du premier plan redeviennent celles sans `ref` ; celles des
    autres plans gardent la leur ; un retrait à la main redevient `frame:photos`. Après une
    suppression du premier plan, c'est le plan devenu premier qui perd la `ref`."""
    project, first, storey = _two_plans()
    runs = tmp_path / "runs"
    board, _ = sync_board(None, project, runs)
    raw = board.model_dump(mode="json")
    raw["dismissed"] = [f"frame:photos:{first}", "frame:inspirations"]
    data = project.model_dump(mode="json")
    assert _implicit_first(raw, data) == 2
    refs = {(i["role"], i["ref"]) for i in raw["items"] if i["kind"] == "frame" and i.get("role")}
    assert ("photos", None) in refs and ("renders", None) in refs
    assert ("photos", storey) in refs and ("renders", storey) in refs
    assert raw["dismissed"] == ["frame:photos", "frame:inspirations"]
    gone = remove_plan(project, next(iter(project.levels)))
    after, _ = sync_board(board, gone, runs)
    raw = after.model_dump(mode="json")
    assert _implicit_first(raw, gone.model_dump(mode="json")) == 2
    frames = [i for i in raw["items"] if i["kind"] == "frame" and i.get("role") == "photos"]
    assert [i["ref"] for i in frames] == [None]
