"""Réglages des rendus réalistes calculés par le poste d'augus (KAN-89), lus dans
l'environnement. Module pur (ni SQLAlchemy ni FastAPI) : `Settings` le charge en mode local
aussi, où il ne sert à rien.

- OPENPLAN_RENDER_ACCOUNTS : identifiants (uuid) des comptes autorisés, séparés par des
  virgules ou des espaces ; vide (défaut) = personne. Jamais une adresse : un uuid ne dit rien
  de la personne et ne se devine pas.
- OPENPLAN_WORKER_TOKEN_SHA256 : empreinte(s) SHA-256 (64 caractères hexadécimaux) du jeton du
  poste de rendu ; plusieurs, séparées par des virgules, pour une rotation sans coupure. Le
  serveur ne connaît jamais le jeton lui-même (`openplan worker token` le crée sur le poste et
  n'affiche que son empreinte). Vide = API du poste fermée (401).
- OPENPLAN_RENDER_EXPIRE_H : une demande non prise après ce délai expire (24 h par défaut).
- OPENPLAN_RENDER_DAILY_QUOTA : demandes par compte sur 24 h glissantes (20 par défaut ;
  0 = aucune).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

DEFAULT_EXPIRE_H = 24.0
DEFAULT_DAILY_QUOTA = 20
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RealisticConfig:
    accounts: frozenset[UUID] = field(default_factory=frozenset)
    worker_token_sha256: tuple[str, ...] = field(default=(), repr=False)
    expire_h: float = DEFAULT_EXPIRE_H
    daily_quota: int = DEFAULT_DAILY_QUOTA

    def allows(self, user_id: UUID) -> bool:
        return user_id in self.accounts

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RealisticConfig:
        return cls(
            accounts=parse_accounts(env.get("OPENPLAN_RENDER_ACCOUNTS")),
            worker_token_sha256=parse_hashes(env.get("OPENPLAN_WORKER_TOKEN_SHA256")),
            expire_h=parse_hours(env.get("OPENPLAN_RENDER_EXPIRE_H")),
            daily_quota=parse_quota(env.get("OPENPLAN_RENDER_DAILY_QUOTA")),
        )


def _chunks(text: str | None) -> list[str]:
    return [c for c in re.split(r"[,\s]+", (text or "").strip()) if c]


def parse_accounts(text: str | None) -> frozenset[UUID]:
    out: set[UUID] = set()
    for chunk in _chunks(text):
        try:
            out.add(UUID(chunk))
        except ValueError:
            msg = "OPENPLAN_RENDER_ACCOUNTS : des identifiants de compte (uuid), séparés par « , »"
            raise ValueError(msg) from None
    return frozenset(out)


def parse_hashes(text: str | None) -> tuple[str, ...]:
    out: list[str] = []
    for chunk in _chunks(text):
        value = chunk.lower()
        if not _HEX64.match(value):
            # Jamais la valeur reçue dans le message : ce pourrait être le jeton lui-même.
            msg = (
                "OPENPLAN_WORKER_TOKEN_SHA256 : l'empreinte SHA-256 du jeton (64 caractères "
                "hexadécimaux), jamais le jeton ; `openplan worker token` la donne"
            )
            raise ValueError(msg)
        out.append(value)
    return tuple(out)


def parse_hours(text: str | None) -> float:
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        return DEFAULT_EXPIRE_H
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not 0 < value <= 24 * 30:
        msg = "OPENPLAN_RENDER_EXPIRE_H : un nombre d'heures entre 0 et 720"
        raise ValueError(msg)
    return value


def parse_quota(text: str | None) -> int:
    raw = (text or "").strip()
    if not raw:
        return DEFAULT_DAILY_QUOTA
    if not raw.isdigit():
        msg = "OPENPLAN_RENDER_DAILY_QUOTA : un nombre entier de rendus par jour"
        raise ValueError(msg)
    return int(raw)
