"""KAN-126 (126a, sans migration) : les offres Essentiel et Pro. Aucun paiement : l'offre est
posée par l'administrateur (colonne du compte au lot 126b).

L'offre qui compte est celle de la personne qui AGIT (comme une licence d'outil), jamais celle
du propriétaire du projet. Tant que la colonne n'existe pas, tous les comptes sont en Pro : rien
ne change pour personne (décision du PO, en attendant celle d'augus).

Les fonctions Pro sont déclarées dans le registre (`registry.OFFER_PRO`, `PRO_OPS`,
`PUSH_PULL_FIELDS`) ; ce module seul les vérifie. Sans comptes (mode local), rien n'est
réservé.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from fastapi import Request

from openplan.api.access import AuthUser, accounts_of, optional_user
from openplan.api.policy.registry import PRO_OPS, PUSH_PULL_FIELDS
from openplan.api.refusal import Refusal
from openplan.schemas.scene import Project

Offer = Literal["essentiel", "pro"]
OFFERS: tuple[Offer, ...] = ("essentiel", "pro")
DEFAULT_OFFER: Offer = "pro"
"""Comptes actuels ET nouveaux : Pro, tant qu'augus n'a rien décidé (126b garde ce défaut)."""

OFFER_PRO_TEXT = "Fonction de l'offre Pro : demandez-la à l'administrateur."
OfferOf = Callable[[AuthUser], Offer]


def offer_of(request: Request, user: AuthUser) -> Offer:
    """L'offre du compte. 126a : le défaut (Pro) ; `app.state.offer_of` la remplace dans les
    tests ; 126b lira la colonne du compte (`user.offer`)."""
    hook = cast("OfferOf | None", getattr(request.app.state, "offer_of", None))
    if hook is not None:
        return hook(user)
    value = getattr(user, "offer", None)
    return value if value in OFFERS else DEFAULT_OFFER


def check_pro(request: Request, user: AuthUser) -> None:
    """403 `offer_pro` si la personne qui agit n'est pas en Pro."""
    if offer_of(request, user) != "pro":
        raise Refusal(403, OFFER_PRO_TEXT, "offer_pro")


def is_pro_op(project: Project, level_id: object, op: object) -> bool:
    """L'opération est-elle de l'offre Pro ? L'ajout à la main d'un élément de `PRO_OPS`, ou
    Pousser/Tirer (`PUSH_PULL_FIELDS`) ; tout le reste (supprimer, déplacer, renommer un
    élément Pro existant…) est Essentiel."""
    kind = getattr(op, "op", None)
    if kind in PRO_OPS:
        return True
    if kind != "update_element":
        return False
    level = project.levels.get(level_id)  # type: ignore[call-overload]
    element = level.elements.get(getattr(op, "element_id", None)) if level else None
    if element is None:
        return False
    fields = getattr(op, "fields", {}) or {}
    return any((element.type, name) in PUSH_PULL_FIELDS for name in fields)


def check_op_offer(request: Request, project: Project, level_id: object, op: object) -> None:
    """La route `plan.ops` (maillon `OFFER_PRO_OPS`) : une opération Pro demande l'offre Pro
    de la personne connectée. Sans comptes : rien."""
    if accounts_of(request) is None or not is_pro_op(project, level_id, op):
        return
    user = optional_user(request)
    if user is not None:
        check_pro(request, user)
