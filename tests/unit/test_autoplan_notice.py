"""Bandeau « plan estimé » quand l'analyse fine manque (retour de la MEP 15) : en ligne, une
phrase fixe, sans « non installée », sans « Pour l'activer », sans identifiant de modèle ; sur
la boucle locale, la commande à lancer reste (l'utilisateur administre sa machine)."""

from __future__ import annotations

from openplan.api.routes.autoplan import FINER_NOT_ONLINE, _missing_notice

LOCAL = "poids absents : uv run openplan models pull owlv2_base@1.0"


def test_online_the_notice_is_a_plain_sentence() -> None:
    assert _missing_notice([LOCAL], exposed=True) == [FINER_NOT_ONLINE]
    text = FINER_NOT_ONLINE
    assert "indisponible en ligne" in text and "@" not in text
    assert "non installée" not in text and "Pour l'activer" not in text


def test_locally_the_notice_says_the_command_once() -> None:
    (notice,) = _missing_notice([LOCAL, LOCAL], exposed=False)
    assert notice.count("models pull") == 1 and "Pour l'activer" in notice
    assert _missing_notice([], exposed=True) == []
