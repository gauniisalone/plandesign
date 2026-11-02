"""KAN-104 : mots de passe (conception, § 4).

- Empreinte argon2id (`argon2-cffi`), paramètres OWASP : m = 19 456 KiB, t = 2, p = 1 ; au plus
  2 calculs à la fois (les 2 cœurs du serveur). Une requête attend une place 2 s au plus, puis
  `HashBusyError` (503 avec Retry-After) : elle ne garde pas un fil du pool commun en file.
- Politique NIST 800-63B : 12 à 128 caractères après normalisation NFKC, sans règle de
  composition ; refusés : l'adresse du compte, sa partie locale, « plandesign ».
- Mots de passe connus des fuites : Pwned Passwords (HIBP) en k-anonymat. Seuls les 5 premiers
  caractères du SHA-1 partent, avec `Add-Padding`, délai 2 s. HIBP indisponible : le mot de
  passe est accepté et l'incident journalisé (décision du PO). Les tests remplacent
  `fetch_range` : aucun appel réseau réel.
- Empreinte factice : vérifiée quand le compte n'existe pas ou n'a pas de mot de passe, pour
  que la durée de la réponse ne le trahisse pas.

Aucun mot de passe, ni aucune empreinte, n'est jamais journalisé.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cache
from threading import BoundedSemaphore

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from openplan.brand import APP_NAME

log = logging.getLogger("openplan.accounts")

MIN_LENGTH = 12
MAX_LENGTH = 128
HIBP_RANGE = "https://api.pwnedpasswords.com/range/"
HIBP_TIMEOUT_S = 2.0
HASHER = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)
HASH_SLOTS = 2
SLOT_WAIT_S = 2.0
_SLOTS = BoundedSemaphore(HASH_SLOTS)


class PasswordPolicyError(ValueError):
    """Le mot de passe ne suit pas la politique ; le texte est montré tel quel."""


class HashBusyError(RuntimeError):
    """Toutes les places de calcul sont prises depuis SLOT_WAIT_S : rien n'a été calculé."""

    retry_after_s = int(SLOT_WAIT_S)


@contextmanager
def _slot() -> Iterator[None]:
    if not _SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise HashBusyError
    try:
        yield
    finally:
        _SLOTS.release()


def normalized(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def check_policy(password: str, email: str, pwned: Callable[[str], bool | None]) -> str:
    """Le mot de passe normalisé, ou PasswordPolicyError avec un texte pour la personne."""
    value = normalized(password)
    if len(value) < MIN_LENGTH:
        msg = f"Le mot de passe doit faire au moins {MIN_LENGTH} caractères."
        raise PasswordPolicyError(msg)
    if len(value) > MAX_LENGTH:
        msg = f"Le mot de passe doit faire au plus {MAX_LENGTH} caractères."
        raise PasswordPolicyError(msg)
    lowered = value.casefold()
    local = email.partition("@")[0].casefold()
    banned = (email.casefold(), APP_NAME.casefold().replace(" ", ""), "plandesign")
    if any(b and b in lowered for b in banned) or (len(local) >= 4 and local in lowered):
        msg = "Le mot de passe ne doit contenir ni votre adresse ni le nom de l'application."
        raise PasswordPolicyError(msg)
    if pwned(value):
        msg = "Ce mot de passe figure dans des fuites de données connues : choisissez-en un autre."
        raise PasswordPolicyError(msg)
    return value


def fetch_range(prefix: str) -> str:
    """La liste HIBP des suffixes de ce préfixe (5 caractères hexadécimaux)."""
    import httpx

    response = httpx.get(
        HIBP_RANGE + prefix,
        headers={"Add-Padding": "true", "User-Agent": APP_NAME},
        timeout=HIBP_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.text


def is_pwned(password: str) -> bool | None:
    """Vrai : connu des fuites ; faux : inconnu ; None : HIBP indisponible (accepté)."""
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = fetch_range(prefix)
    except Exception as exc:  # réseau, délai, réponse refusée : jamais bloquant
        log.warning("HIBP indisponible (%s) : mot de passe accepté", type(exc).__name__)
        return None
    for line in body.splitlines():
        found, _, count = line.strip().partition(":")
        if found.upper() == suffix and count.strip() not in ("", "0"):
            return True
    return False


def hash_password(password: str) -> str:
    with _slot():
        return HASHER.hash(password)


def hash_after(check: Callable[[], object], password: str) -> str:
    """Une place de calcul d'abord, puis `check()` (consommer un code), puis l'empreinte. Sans
    place (HashBusyError), `check` n'est pas appelé et le code reste valable ; si `check` lève,
    rien n'est calculé (un code faux ne coûte pas un calcul)."""
    with _slot():
        check()
        return HASHER.hash(password)


def verify_password(stored: str | None, password: str) -> tuple[bool, bool]:
    """(juste, à recalculer) ; `stored` absent : l'empreinte factice est vérifiée quand même,
    pour la même durée, et la réponse est fausse."""
    with _slot():
        try:
            ok = HASHER.verify(stored or _dummy(), normalized(password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False
    if stored is None:
        return False, False
    return ok, HASHER.check_needs_rehash(stored)


@cache
def _dummy() -> str:
    return HASHER.hash("empreinte factice : aucun compte ne l'a")
