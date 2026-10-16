"""Réglages de l'accueil retenus pour la machine (L2, décision d'augus du 7 octobre 17:46).

`gemini_text` : la case « Mieux comprendre mon texte avec Gemini » sous la zone de texte,
décochée par défaut. Cochée, elle vaut accord, texte seul, pour l'aperçu « Compris » et pour
les projets créés depuis l'accueil. Le fichier est rangé à côté des projets, jamais envoyé ;
le journal réseau de l'aperçu (sans contenu) aussi.
"""

from __future__ import annotations

import json
from pathlib import Path

from openplan.schemas.common import StrictModel

SETTINGS_FILE = "home_settings.json"
NETWORK_LOG = "home_network_log.jsonl"


class HomeSettings(StrictModel):
    gemini_text: bool = False


def load(root: Path) -> HomeSettings:
    path = root / SETTINGS_FILE
    if not path.is_file():
        return HomeSettings()
    try:
        return HomeSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return HomeSettings()


def save(root: Path, settings: HomeSettings) -> HomeSettings:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{SETTINGS_FILE}.tmp"
    tmp.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(root / SETTINGS_FILE)
    return settings


def network_log(root: Path) -> Path:
    return root / NETWORK_LOG
