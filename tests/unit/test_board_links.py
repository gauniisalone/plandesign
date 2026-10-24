"""KAN-91 : cartes reliées. Le type d'un lien se déduit des deux cartes ; au plus un lien
photos → plan par plan et un lien plan → rendus par carte Rendus ; plusieurs inspirations par
rendu (D1) ; une génération ne lit que les membres des cartes reliées (C1) ; une carte Photos
n'accepte que des photos (C3) ; `reference` suit l'appartenance ; liens automatiques."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from openplan.pipeline import board_links as bl
from openplan.pipeline.board import patched
from openplan.schemas.board import Board, BoardItem, BoardLink, BoardPatch

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _frame(role: str, ref: str | None = None, text: str = "") -> BoardItem:
    return BoardItem(kind="frame", x=0, y=0, w=600, h=400, role=role, ref=ref, text=text)  # type: ignore[arg-type]


def _plan(level: str, name: str = "RDC") -> BoardItem:
    return BoardItem(kind="plan", x=700, y=0, w=900, h=700, ref=level, text=name)


def _in(card: BoardItem, kind: str = "photo", **kw: object) -> BoardItem:
    return BoardItem(
        kind=kind,  # type: ignore[arg-type]
        x=card.x + 10,
        y=card.y + 10,
        w=280,
        h=190,
        frame=card.id,
        ref=str(uuid4()),
        **kw,  # type: ignore[arg-type]
    )


def _free(kind: str = "photo", **kw: object) -> BoardItem:
    return BoardItem(kind=kind, x=5000, y=5000, w=280, h=190, ref=str(uuid4()), **kw)  # type: ignore[arg-type]


class Scene:
    """Photos · RDC → Plan · RDC → Rendus · A ← Inspirations · A (et B)."""

    def __init__(self) -> None:
        self.level = str(uuid4())
        self.photos = _frame("photos", text="Photos de la pièce")
        self.plan = _plan(self.level)
        self.renders = _frame("renders", text="Rendus · A")
        self.insp_a = _frame("inspirations", text="Inspirations · A")
        self.insp_b = _frame("inspirations", text="Inspirations · B")
        self.p1, self.p2 = _in(self.photos), _in(self.photos)
        self.free = _free()
        self.i1 = _in(self.insp_a, reference=True)
        self.i2 = _in(self.insp_b, "file", reference=True)
        self.items = [
            self.photos, self.plan, self.renders, self.insp_a, self.insp_b,
            self.p1, self.p2, self.free, self.i1, self.i2,
        ]  # fmt: skip

    def board(self, *links: BoardLink) -> Board:
        return Board(items=self.items, links=list(links))

    def feeds(self) -> BoardLink:
        return BoardLink(source=self.photos.id, target=self.plan.id, kind="feeds_plan")

    def view(self) -> BoardLink:
        return BoardLink(source=self.plan.id, target=self.renders.id, kind="renders_view")

    def guides(self, card: BoardItem) -> BoardLink:
        return BoardLink(source=card.id, target=self.renders.id, kind="guides_style")


# ------------------------------------------------------------------ type et règles
def test_the_kind_of_a_link_comes_from_its_two_cards() -> None:
    s = Scene()
    assert bl.link_kind(s.photos, s.plan) == "feeds_plan"
    assert bl.link_kind(s.plan, s.renders) == "renders_view"
    assert bl.link_kind(s.insp_a, s.renders) == "guides_style"
    assert bl.link_kind(s.photos, s.renders) is None
    assert bl.link_kind(s.renders, s.plan) is None, "pas de lien à rebours"
    # Un type envoyé faux est corrigé, jamais cru.
    wrong = BoardLink(source=s.photos.id, target=s.plan.id, kind="guides_style")
    links, _ = bl.written_links(s.board(), s.items, [wrong], [], "user", NOW)
    assert links[0].kind == "feeds_plan"


def test_cards_that_do_not_link_are_refused_with_a_reason() -> None:
    s = Scene()
    bad = BoardLink(source=s.photos.id, target=s.renders.id, kind="feeds_plan")
    with pytest.raises(bl.LinkError, match="une carte Photos se relie à un plan") as caught:
        bl.written_links(s.board(), s.items, [bad], [], "user", NOW)
    assert caught.value.status == 422


def test_a_plan_reads_a_single_photo_card_409() -> None:
    s = Scene()
    other = _frame("photos", text="Photos · Étage")
    items = [*s.items, other]
    second = BoardLink(source=other.id, target=s.plan.id, kind="feeds_plan")
    board = Board(items=items, links=[s.feeds()])
    with pytest.raises(bl.LinkError, match="lit déjà la carte") as caught:
        bl.written_links(board, items, [second], [], "user", NOW)
    assert caught.value.status == 409


def test_a_renders_card_renders_a_single_plan_409() -> None:
    s = Scene()
    other = _plan(str(uuid4()), "Étage")
    items = [*s.items, other]
    board = Board(items=items, links=[s.view()])
    second = BoardLink(source=other.id, target=s.renders.id, kind="renders_view")
    with pytest.raises(bl.LinkError, match="rend déjà") as caught:
        bl.written_links(board, items, [second], [], "user", NOW)
    assert caught.value.status == 409


def test_several_inspiration_cards_guide_one_render_and_one_guides_several_d1() -> None:
    s = Scene()
    other = _frame("renders", ref=str(uuid4()), text="Rendus · B")
    items = [*s.items, other]
    links, _ = bl.written_links(
        Board(items=items),
        items,
        [
            s.guides(s.insp_a),
            s.guides(s.insp_b),
            BoardLink(source=s.insp_a.id, target=other.id, kind="guides_style"),
        ],
        [],
        "user",
        NOW,
    )
    assert len(links) == 3


def test_a_cut_link_is_remembered_and_a_new_one_frees_the_pair() -> None:
    s = Scene()
    link = s.feeds()
    links, cut = bl.written_links(s.board(link), s.items, [], [link.id], "user", NOW)
    assert links == [] and cut == [link.pair]
    again, cut = bl.written_links(
        Board(items=s.items, cut_links=cut), s.items, [s.feeds()], [], "user", NOW
    )
    assert len(again) == 1 and cut == []
    assert again[0].updated_by == "user" and again[0].updated_at == NOW


def test_removing_a_card_removes_its_links() -> None:
    s = Scene()
    board = s.board(s.feeds(), s.view())
    after = patched(board, BoardPatch(delete=[s.plan.id]), "user")
    assert after.links == []


# ------------------------------------------------------------------ appartenance (C1, C3)
def test_a_photo_card_only_accepts_real_photos_c3() -> None:
    s = Scene()
    board = s.board()
    moved = s.i2.model_copy(update={"frame": s.photos.id})
    with pytest.raises(bl.LinkError, match="seule une photo réelle"):
        patched(board, BoardPatch(set=[moved]), "user")
    # Une photo sortie des inspirations y retourne sans difficulté.
    photo_in_insp = _in(s.insp_a, reference=True)
    board = Board(items=[*s.items, photo_in_insp])
    after = patched(
        board, BoardPatch(set=[photo_in_insp.model_copy(update={"frame": s.photos.id})]), "user"
    )
    back = next(i for i in after.items if i.id == photo_in_insp.id)
    assert back.frame == s.photos.id and back.reference is False, "elle sert au plan, plus au style"


def test_reference_follows_membership() -> None:
    s = Scene()
    board = s.board()
    out = patched(board, BoardPatch(set=[s.p1.model_copy(update={"frame": s.insp_a.id})]), "user")
    assert next(i for i in out.items if i.id == s.p1.id).reference is True
    freed = patched(out, BoardPatch(set=[s.i1.model_copy(update={"frame": None})]), "user")
    assert next(i for i in freed.items if i.id == s.i1.id).reference is False
    forced = patched(freed, BoardPatch(set=[s.free.model_copy(update={"reference": True})]), "user")
    assert next(i for i in forced.items if i.id == s.free.id).reference is False, "hors carte"


# ------------------------------------------------------------------ generation_inputs (C1)
def test_a_plan_reads_only_the_members_of_its_linked_photo_card() -> None:
    s = Scene()
    inputs = bl.generation_inputs(s.board(s.feeds()), s.plan.id)
    assert inputs.kind == "plan" and inputs.photo_card == s.photos.id
    assert set(inputs.photos) == {s.p1.ref, s.p2.ref}
    assert s.free.ref not in inputs.photos, "une photo hors carte ne change pas le plan"
    assert bl.photo_feeds_plan(s.board(s.feeds()), s.level, s.p1.ref or "")
    assert not bl.photo_feeds_plan(s.board(s.feeds()), s.level, s.free.ref or "")
    assert bl.generation_inputs(s.board(), s.plan.id).photos == [], "sans lien : rien"


def test_a_render_reads_its_plan_and_the_members_of_its_inspiration_cards() -> None:
    s = Scene()
    board = s.board(s.view(), s.guides(s.insp_a), s.guides(s.insp_b))
    inputs = bl.generation_inputs(board, s.renders.id)
    assert inputs.can_render and inputs.plan_level == s.level
    assert {i.item for i in inputs.inspirations} == {s.i1.id, s.i2.id}
    assert inputs.inspiration_cards == [s.insp_a.id, s.insp_b.id]
    assert s.free.id not in {i.item for i in inputs.inspirations}


def test_no_render_without_a_linked_plan_d2() -> None:
    s = Scene()
    inputs = bl.generation_inputs(s.board(s.guides(s.insp_a)), s.renders.id)
    assert not inputs.can_render and inputs.plan_level is None
    assert bl.NO_PLAN == "Reliez un plan pour rendre"


def test_ignored_tags_travel_with_the_inspiration() -> None:
    s = Scene()
    tagged = s.i1.model_copy(update={"ignored_tags": ["m:terracotta", "s:japandi"]})
    s.items = [tagged if i.id == s.i1.id else i for i in s.items]
    inputs = bl.generation_inputs(s.board(s.view(), s.guides(s.insp_a)), s.renders.id)
    assert inputs.inspirations[0].ignored_tags == ("m:terracotta", "s:japandi")
    with pytest.raises(ValueError, match="étiquette"):
        BoardItem(kind="photo", x=0, y=0, w=1, h=1, ignored_tags=["rouge"])


def test_the_hover_sentence_counts_what_will_be_read() -> None:
    s = Scene()
    board = s.board(s.feeds(), s.view(), s.guides(s.insp_a))
    assert bl.link_sentence(board, s.feeds()) == (
        "Les 2 photos de cette carte construisent Plan · RDC. "
        "Les photos hors carte ne comptent pas."
    )
    guide = next(k for k in board.links if k.kind == "guides_style")
    assert bl.link_sentence(board, guide).startswith(
        "L’image de cette carte guide le style de Rendus · A"
    )
    assert bl.link_sentence(board, s.view()) == "Plan · RDC donne la géométrie de Rendus · A."


# ------------------------------------------------------------------ zéro geste
def test_automatic_links_tie_the_board_together_and_respect_cuts() -> None:
    s = Scene()
    s.renders = s.renders.model_copy(update={"ref": None})  # carte Rendus du premier plan
    s.items[2] = s.renders
    links = bl.auto_links(s.items, [], [])
    kinds = sorted(k.kind for k in links)
    assert kinds == ["feeds_plan", "guides_style", "guides_style", "renders_view"]
    assert all(k.auto for k in links)
    cut = next(k for k in links if k.kind == "feeds_plan").pair
    again = bl.auto_links(s.items, [], [cut])
    assert "feeds_plan" not in {k.kind for k in again}, "une paire coupée n'est jamais recréée"
    assert bl.auto_links(s.items, links, []) == links, "rien de plus au deuxième passage"


class TwoLevels:
    """KAN-91 : une carte Photos par plan. La carte d'avant (sans `ref`) est celle du premier
    plan ; celle de l'étage porte son id ; une carte ajoutée à la main porte son propre id."""

    def __init__(self) -> None:
        self.rdc, self.etage = str(uuid4()), str(uuid4())
        self.photos = _frame("photos", text="Photos de la pièce · RDC")
        self.photos2 = _frame("photos", ref=self.etage, text="Photos de la pièce · Étage")
        hand = _frame("photos", text="Photos de la pièce")
        self.hand = hand.model_copy(update={"ref": str(hand.id)})
        self.plan, self.plan2 = _plan(self.rdc), _plan(self.etage, "Étage")
        self.items = [self.photos, self.photos2, self.hand, self.plan, self.plan2]

    def feeds(self, links: list[BoardLink]) -> set[tuple[object, object]]:
        return {(k.source, k.target) for k in links if k.kind == "feeds_plan"}


def test_each_photo_card_feeds_only_the_plan_of_its_level() -> None:
    t = TwoLevels()
    assert bl.card_level(t.photos, [t.rdc, t.etage]) == t.rdc
    assert bl.card_level(t.photos2, [t.rdc, t.etage]) == t.etage
    assert bl.card_level(t.hand, [t.rdc, t.etage]) is None
    links = bl.auto_links(t.items, [], [], t.rdc)
    assert t.feeds(links) == {(t.photos.id, t.plan.id), (t.photos2.id, t.plan2.id)}
    # Le premier plan est celui du projet, même posé après l'autre sur l'espace de travail.
    swapped = [t.photos, t.photos2, t.hand, t.plan2, t.plan]
    assert t.feeds(bl.auto_links(swapped, [], [], t.rdc)) == t.feeds(links)


def test_an_old_automatic_cross_level_link_leaves_a_hand_made_one_stays() -> None:
    t = TwoLevels()
    # Avant les cartes par plan, la seule carte Photos alimentait aussi l'étage.
    old = BoardLink(source=t.photos.id, target=t.plan2.id, kind="feeds_plan", auto=True)
    links = bl.auto_links(t.items, [old], [], t.rdc)
    assert t.feeds(links) == {(t.photos.id, t.plan.id), (t.photos2.id, t.plan2.id)}
    # Tiré à la main, il reste : au plus un lien photos → plan par plan, la carte de l'étage
    # n'est pas reliée en plus.
    mine = old.model_copy(update={"auto": False})
    kept = bl.auto_links(t.items, [mine], [], t.rdc)
    assert t.feeds(kept) == {(t.photos.id, t.plan.id), (t.photos.id, t.plan2.id)}


def test_a_cut_level_pair_is_not_recreated_and_a_hand_card_stays_free() -> None:
    t = TwoLevels()
    cut = f"{t.photos2.id}>{t.plan2.id}"
    links = bl.auto_links(t.items, [], [cut], t.rdc)
    assert t.feeds(links) == {(t.photos.id, t.plan.id)}
    assert not any(t.hand.id in (k.source, k.target) for k in links)
