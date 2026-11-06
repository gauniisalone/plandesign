"""KAN-126 : les offres Essentiel et Pro. Aucun paiement : l'offre est posée par l'administrateur
(126b : colonne `users.offer`, kan126b_0014, et PATCH /api/admin/users/{id}/offer).

L'offre qui compte est celle de la personne qui AGIT (comme une licence d'outil), jamais celle
du propriétaire du projet. Tous les comptes, actuels et nouveaux, sont en Pro par défaut : rien
ne change pour personne tant que l'administrateur n'a rien posé (décision du PO).

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
    """L'offre du compte : la colonne `users.offer` (126b), lue à l'ouverture de la session
    (`AuthUser.offer`) ; une valeur inconnue vaut le défaut (Pro). `app.state.offer_of`, s'il est
    posé (tests), passe avant."""
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
