"""KAN-108 : les regex qui lisent une entrée externe (texte de la personne, réponse d'un modèle,
message d'erreur) restent rapides sur une entrée piège (Sonar S8786 : retour arrière
super-linéaire). La clôture Markdown des réponses de modèle est retirée en temps linéaire, sans
changer ce qui est lu ; les autres lectures sont bornées avant le match."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable

import pytest

from openplan.adapters.remote.fence import strip_fence
from openplan.api.accounts.service import InvalidEmailError, checked_email
from openplan.monitoring import MAX_SCRUB_CHARS, scrub_text
from openplan.pipeline import assistant_phrases, say

OLD_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
QUICK_S = 1.0
"""Large : la version d'avant prend des minutes sur ces entrées, la nouvelle des millisecondes."""


def _quick(call: Callable[[], object]) -> object:
    started = time.perf_counter()
    result = call()
    elapsed = time.perf_counter() - started
    assert elapsed < QUICK_S, f"{elapsed:.2f} s"
    return result


@pytest.mark.parametrize(
    "reply",
    [
        '```json\n{"a": 1}\n```',
        '```\n{"a": [1, 2]}\n```',
        '{"a": 1}',
        '  ```json\n{"a": {"b": "c"}}\n```  ',
        '```json {"a": 1}```',
        'Voici :\n```json\n{"a": 1}\n```',
    ],
)
def test_the_fence_is_removed_as_before(reply: str) -> None:
    """Le même objet JSON qu'avec la regex d'avant, quelle que soit la forme de la réponse."""

    def obj(text: str) -> object:
        return json.loads(text[text.find("{") : text.rfind("}") + 1])

    assert obj(strip_fence(reply)) == obj(OLD_FENCE.sub("", reply.strip()))
    assert "```" not in strip_fence(reply)


def test_a_model_reply_full_of_spaces_is_read_in_linear_time() -> None:
    trap = "```" + " " * 200_000 + "x"
    assert _quick(lambda: strip_fence(trap)).endswith("x")  # type: ignore[union-attr]
    trap = "{" + " \n" * 100_000 + "}"
    _quick(lambda: strip_fence(trap))


def test_a_long_sentence_is_split_and_read_quickly() -> None:
    _quick(lambda: say.clauses(" " * 200_000 + "x"))
    _quick(lambda: say.clauses("a" + " ," * 100_000))
    _quick(lambda: assistant_phrases.understand("le mur " + "a " * 100_000 + "x", []))
    _quick(lambda: assistant_phrases.understand("1" * 200_000, []))
    _quick(lambda: assistant_phrases.understand("appelle la " + "piece " * 50_000, []))
    # Une phrase ordinaire est toujours comprise.
    reply = assistant_phrases.understand("appelle la piece bureau", [])
    assert reply is not None and reply.intents[0].name == "Bureau"


def test_an_error_message_is_scrubbed_quickly_and_never_half_a_token() -> None:
    _quick(lambda: scrub_text("a" * 200_000))
    _quick(lambda: scrub_text("photo" * 50_000 + ".jpg"))
    # Un jeton à cheval sur la borne n'est jamais gardé à moitié (il ne serait plus masqué).
    token = "Bearer " + "A1b2" * 20
    text = "x " * (MAX_SCRUB_CHARS // 2 - 10) + token + " fin"
    assert "A1b2" not in scrub_text(text)
    # Sous la borne, rien ne change.
    short = "x " * 100 + token
    assert scrub_text(short) == scrub_text(short[: MAX_SCRUB_CHARS - 1])
    assert "A1b2" not in scrub_text(short)


def test_an_address_is_bounded_before_its_check() -> None:
    with pytest.raises(InvalidEmailError):
        _quick(lambda: checked_email("a@b" + "." * 100_000))
    assert checked_email("camille@example.org") == "camille@example.org"
