"""KAN-91 : espace de travail en cartes reliées.

Les liens vont de carte à carte : carte Photos → plan (`feeds_plan`), plan → carte Rendus
(`renders_view`), carte Inspirations → carte Rendus (`guides_style`). Ce qui entre dans une
génération, ce sont les membres des cartes reliées (décision C1 d'augus) : une image posée
librement sur l'espace de travail n'est jamais utilisée. L'appartenance est le champ `frame`
d'un élément (le cadre qui le contient).

Règles (vérifiées au serveur, pour tout écrivain) :
- le type d'un lien se déduit des deux cartes (`LINK_ROLES`), jamais choisi ;
- au plus un lien photos → plan par plan, au plus un lien plan → rendus par carte Rendus
  (409 sinon) ; plusieurs cartes Inspirations par carte Rendus, et une carte Inspirations peut
  guider plusieurs rendus (D1) ;
- une carte Rendus sans plan relié ne rend pas : « Reliez un plan pour rendre » (D2) ;
- supprimer une carte supprime ses liens ; couper un lien note sa paire (`cut_links`) pour que
  la synchronisation ne le recrée pas.

`generation_inputs` est la seule fonction qui dit ce qu'une génération lit : elle sert à la
construction du plan, à la demande de rendu et à la phrase de survol du front, pour que ce qui
est affiché soit exactement ce qui sera lu."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from openplan.schemas.board import LINK_ROLES, Board, BoardItem, BoardLink, LinkKind

CardRole = str
INPUT_ROLES = ("photos", "inspirations")
OUTPUT_ROLES = ("renders", "moodboard")
IMAGE_KINDS = ("photo", "render", "contextual", "file")
"""Ce qui peut être membre d'une carte Inspirations (une image jointe est un `file`)."""
NO_PLAN = "Reliez un plan pour rendre"
OFF_CARD = "hors carte : non utilisée"


class LinkError(ValueError):
    """Un lien refusé : `status` 409 (règle de nombre) ou 422 (cartes qui ne se relient pas)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def card_role(item: BoardItem | None) -> CardRole | None:
    """Le rôle métier d'une carte : `plan` (un plan), `photos`, `inspirations`, `renders` (un
    cadre posé par l'espace de travail), sinon None (cadre libre, autre élément)."""
    if item is None:
        return None
    if item.kind == "plan":
        return "plan"
    if item.kind == "frame" and item.role in ("photos", "inspirations", "renders"):
        return item.role
    return None


def card_title(item: BoardItem | None) -> str:
    if item is None:
        return "?"
    if item.kind == "plan":
        return f"Plan · {item.text}" if item.text else "Plan"
    return item.text or {"photos": "Photos de la pièce", "inspirations": "Moodboard"}.get(
        item.role or "", "Rendus"
    )


def link_kind(source: BoardItem | None, target: BoardItem | None) -> LinkKind | None:
    """Le type du lien entre deux cartes, None si elles ne se relient pas."""
    pair = (card_role(source), card_role(target))
    for kind, roles in LINK_ROLES.items():
        if pair == roles:
            return kind  # type: ignore[return-value]
    return None


def refusal(source: BoardItem | None, target: BoardItem | None) -> str:
    """Pourquoi deux cartes ne se relient pas, en une phrase."""
    s = card_role(source)
    if source is None or target is None:
        return "lien refusé : une des deux cartes n'existe plus"
    if source.id == target.id:
        return "lien refusé : une carte ne se relie pas à elle-même"
    if s == "photos":
        return "une carte Photos se relie à un plan : ses photos le construisent"
    if s == "inspirations":
        return "une carte Inspirations se relie à une carte Rendus : ses images en guident le style"
    if s == "plan":
        return "un plan se relie à une carte Rendus : il en donne la géométrie"
    return "seules les cartes Photos, Inspirations et les plans tirent un lien"


def is_member(item: BoardItem, items: dict[UUID, BoardItem], role: CardRole) -> bool:
    return item.frame is not None and card_role(items.get(item.frame)) == role


NOT_FOR_PLAN = ("file", "render", "contextual")
"""Images qui ne sont pas des photos réelles de la pièce (image jointe, rendu, variante)."""


def accepts(card: BoardItem | None, item: BoardItem) -> str | None:
    """None si `item` peut entrer dans `card` ; sinon la raison du refus (C3). Seule la carte
    Photos trie : une inspiration n'y entre pas (une note, un lien y restent possibles, ils ne
    construisent rien). La carte Inspirations accepte tout ; seules ses images guident."""
    if card_role(card) == "photos" and item.kind in NOT_FOR_PLAN:
        return (
            "une inspiration ne construit pas le plan : seule une photo réelle de la pièce "
            "entre dans la carte Photos"
        )
    return None


def with_membership(items: Iterable[BoardItem]) -> list[BoardItem]:
    """`reference` dérivé de l'appartenance (KAN-91) : vrai si et seulement si l'image est
    membre d'une carte Inspirations. Une image jointe (`file`) garde la valeur posée par le
    front dans la carte (il sait si c'est une image), et la perd hors d'elle."""
    items = list(items)
    by_id = {i.id: i for i in items}
    out = []
    for item in items:
        inside = is_member(item, by_id, "inspirations")
        if item.kind in ("photo", "render", "contextual"):
            want = inside
        elif item.kind == "file":
            want = inside and item.reference
        else:
            want = item.reference
        out.append(item if item.reference == want else item.model_copy(update={"reference": want}))
    return out


# ------------------------------------------------------------------ écriture des liens
def _checked(link: BoardLink, items: dict[UUID, BoardItem]) -> BoardLink:
    source, target = items.get(link.source), items.get(link.target)
    kind = link_kind(source, target)
    if kind is None:
        raise LinkError(refusal(source, target))
    if kind != link.kind:
        link = link.model_copy(update={"kind": kind})
    return link


def _unique(links: list[BoardLink], items: dict[UUID, BoardItem]) -> None:
    """Au plus un lien photos → plan par plan, au plus un lien plan → rendus par carte Rendus."""
    seen: dict[tuple[str, UUID], BoardLink] = {}
    for link in links:
        if link.kind == "guides_style":
            continue
        key = (link.kind, link.target)
        if key in seen:
            other = items.get(seen[key].source)
            target = card_title(items.get(link.target))
            if link.kind == "feeds_plan":
                msg = (
                    f"{target} lit déjà la carte « {card_title(other)} » : coupez ce lien "
                    "d'abord (un plan se construit depuis une seule carte Photos)"
                )
            else:
                msg = (
                    f"« {target} » rend déjà {card_title(other)} : coupez ce lien d'abord "
                    "(une carte Rendus rend un seul plan)"
                )
            raise LinkError(msg, status=409)
        seen[key] = link


def written_links(
    board: Board,
    items: list[BoardItem],
    links_set: list[BoardLink],
    links_delete: list[UUID],
    actor: str,
    stamp: datetime,
) -> tuple[list[BoardLink], list[str]]:
    """Liens et paires coupées après un geste. Les liens écrits sont vérifiés (cartes, nombre)
    et horodatés ; un lien coupé note sa paire ; un lien posé à nouveau sur une paire coupée la
    libère ; un lien dont une carte a disparu s'en va."""
    by_id = {i.id: i for i in items}
    links = {link.id: link for link in board.links}
    cut = list(board.cut_links)
    for lid in links_delete:
        gone = links.pop(lid, None)
        if gone is not None:
            cut.append(gone.pair)
    for sent in links_set:
        link = _checked(sent, by_id)
        # Une même paire n'a qu'un lien : le nouveau remplace l'ancien.
        for other in [o for o in links.values() if o.pair == link.pair and o.id != link.id]:
            del links[other.id]
        links[link.id] = link.model_copy(update={"updated_at": stamp, "updated_by": actor})
        cut = [p for p in cut if p != link.pair]
    alive = [k for k in links.values() if k.source in by_id and k.target in by_id]
    _unique(alive, by_id)
    return alive, list(dict.fromkeys(cut))


def card_level(card: BoardItem, levels: list[str]) -> str | None:
    """Le plan (niveau) d'une carte Photos ou Rendus : `ref` s'il désigne un niveau ; la carte
    sans `ref` est celle du premier plan ; une carte ajoutée à la main (`ref` = son id) n'en a
    aucun."""
    if card.ref is None:
        return levels[0] if levels else None
    return card.ref if card.ref in levels else None


def auto_links(
    items: list[BoardItem],
    links: list[BoardLink],
    cut: list[str],
    first_level: str | None = None,
) -> list[BoardLink]:
    """Zéro geste (KAN-91) : chaque carte Photos est reliée au plan de son niveau, chaque plan à
    sa carte Rendus si elle n'en rend aucun, la carte Inspirations de départ à chaque carte
    Rendus (une carte Inspirations ajoutée à la main se relie à la main, N-N). Une
    paire coupée à la main n'est jamais recréée. Un lien automatique d'une carte Photos vers le
    plan d'un autre niveau (avant les cartes par plan) s'en va : la photo d'un étage ne
    construit pas le rez-de-chaussée (C1). `first_level` : le premier plan du projet (sinon le
    premier plan posé)."""
    by_id = {i.id: i for i in items}
    plans = [i for i in items if i.kind == "plan"]
    levels = [p.ref for p in plans if p.ref]
    if first_level is not None and first_level in levels:
        levels = [first_level, *(lvl for lvl in levels if lvl != first_level)]
    out = [
        k
        for k in links
        if k.source in by_id
        and k.target in by_id
        and not (
            k.kind == "feeds_plan"
            and k.auto
            and card_level(by_id[k.source], levels) != by_id[k.target].ref
        )
    ]
    pairs = {k.pair for k in out} | set(cut)
    fed = {k.target for k in out if k.kind == "feeds_plan"}
    viewed = {k.target for k in out if k.kind == "renders_view"}
    photos = [i for i in items if card_role(i) == "photos"]
    inspirations = [i for i in items if card_role(i) == "inspirations"]
    renders = [i for i in items if card_role(i) == "renders"]
    first_plan = levels[0] if levels else None

    def add(source: BoardItem, target: BoardItem, kind: LinkKind) -> None:
        link = BoardLink(source=source.id, target=target.id, kind=kind, auto=True)
        if link.pair not in pairs:
            out.append(link)
            pairs.add(link.pair)

    for card in photos:
        level = card_level(card, levels)
        own = next((p for p in plans if level is not None and p.ref == level), None)
        if own is not None and own.id not in fed:
            add(card, own, "feeds_plan")
            fed.add(own.id)
    for frame in renders:
        level = frame.ref or first_plan
        rendered = next((p for p in plans if p.ref == level), None)
        if rendered is not None and frame.id not in viewed:
            add(rendered, frame, "renders_view")
            viewed.add(frame.id)
        # Seule la carte Inspirations de départ (sans `ref`) guide d'elle-même chaque carte
        # Rendus ; une carte ajoutée à la main n'est reliée à rien (retours de la MEP 14).
        for card in inspirations:
            if card.ref is None:
                add(card, frame, "guides_style")
    return out


# ------------------------------------------------------------------ ce qu'une génération lit
@dataclass(frozen=True)
class Inspiration:
    item: UUID
    kind: str
    ref: str | None
    card: UUID
    ignored_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationInputs:
    """Ce que lit la génération d'une carte : un plan (ses photos) ou une carte Rendus (son
    plan et ses inspirations). Les images hors carte n'y sont jamais."""

    target: UUID
    kind: str
    """`plan` ou `renders` (ou `none` : ni l'un ni l'autre)."""
    photos: list[str] = field(default_factory=list)
    """Plan : ids des images du projet, membres de la carte Photos reliée."""
    photo_card: UUID | None = None
    plan_level: str | None = None
    """Rendus : le niveau du plan relié (None : pas de plan relié, D2)."""
    plan_item: UUID | None = None
    inspirations: list[Inspiration] = field(default_factory=list)
    """Rendus : membres des cartes Inspirations reliées, à poids égal (décision d'augus)."""
    inspiration_cards: list[UUID] = field(default_factory=list)

    @property
    def can_render(self) -> bool:
        return self.kind == "renders" and self.plan_level is not None


def generation_inputs(board: Board | None, target_id: UUID) -> GenerationInputs:
    """La seule réponse à « que lit cette génération ? » (C1)."""
    items = list(board.items) if board else []
    links = list(board.links) if board else []
    by_id = {i.id: i for i in items}
    target = by_id.get(target_id)
    role = card_role(target)
    if role == "plan":
        source = next(
            (k.source for k in links if k.kind == "feeds_plan" and k.target == target_id), None
        )
        photos = [
            i.ref
            for i in items
            if source is not None and i.frame == source and i.kind == "photo" and i.ref
        ]
        return GenerationInputs(target=target_id, kind="plan", photos=photos, photo_card=source)
    if role == "renders":
        plan = next(
            (
                by_id.get(k.source)
                for k in links
                if k.kind == "renders_view" and k.target == target_id
            ),
            None,
        )
        cards = [k.source for k in links if k.kind == "guides_style" and k.target == target_id]
        cards = list(dict.fromkeys(c for c in cards if c in by_id))
        found = [
            Inspiration(
                item=i.id, kind=i.kind, ref=i.ref, card=i.frame, ignored_tags=tuple(i.ignored_tags)
            )
            for card in cards
            for i in items
            if i.frame == card and i.kind in IMAGE_KINDS and i.reference and i.frame is not None
        ]
        return GenerationInputs(
            target=target_id,
            kind="renders",
            plan_level=plan.ref if plan is not None else None,
            plan_item=plan.id if plan is not None else None,
            inspirations=found,
            inspiration_cards=cards,
        )
    return GenerationInputs(target=target_id, kind="none")


def plan_item(board: Board | None, level_id: UUID | str) -> BoardItem | None:
    if board is None:
        return None
    return next((i for i in board.items if i.kind == "plan" and i.ref == str(level_id)), None)


def renders_card(
    board: Board | None, level_id: UUID | str, first_level: str | None
) -> BoardItem | None:
    """La carte Rendus d'un niveau (`ref` = son niveau ; celle du premier plan peut n'en avoir
    aucun, T7e)."""
    if board is None:
        return None
    lid = str(level_id)
    frames = [i for i in board.items if card_role(i) == "renders"]
    return next(
        (f for f in frames if f.ref == lid),
        next((f for f in frames if f.ref is None), None) if lid == first_level else None,
    )


def photo_feeds_plan(board: Board | None, level_id: UUID | str, image_id: UUID | str) -> bool:
    plan = plan_item(board, level_id)
    if plan is None:
        return False
    return str(image_id) in generation_inputs(board, plan.id).photos


def link_sentence(board: Board, link: BoardLink) -> str:
    """La phrase de survol d'un lien, tirée de `generation_inputs` (ce qui sera vraiment lu)."""
    by_id = {i.id: i for i in board.items}
    source, target = by_id.get(link.source), by_id.get(link.target)
    inputs = generation_inputs(board, link.target)
    if link.kind == "feeds_plan":
        n = len(inputs.photos)
        if n == 0:
            return (
                f"Aucune photo dans cette carte : {card_title(target)} ne se construit pas depuis "
                "des photos. Les photos hors carte ne comptent pas."
            )
        what = (
            "La photo de cette carte construit"
            if n == 1
            else (f"Les {n} photos de cette carte construisent")
        )
        return f"{what} {card_title(target)}. Les photos hors carte ne comptent pas."
    if link.kind == "guides_style":
        n = sum(1 for i in inputs.inspirations if i.card == link.source)
        rendu = card_title(target)
        if n == 0:
            return f"Aucune image dans cette carte : style par défaut pour {rendu}."
        what = (
            "L’image de cette carte guide" if n == 1 else (f"Les {n} images de cette carte guident")
        )
        return f"{what} le style de {rendu}. Les images hors carte ne comptent pas."
    return f"{card_title(source)} donne la géométrie de {card_title(target)}."
