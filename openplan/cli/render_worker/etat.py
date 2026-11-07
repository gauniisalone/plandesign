"""État local du poste de rendu (KAN-162), lu par les agents de la machine (`pd-poste-libre`).

    /tmp/pd-poste.etat   (droits 644, JSON sur une ligne)
    {"en_file": 2, "rendu_en_cours": false, "horodatage": "2026-11-07T13:05:12+00:00", "version": 1}

- `en_file` : le nombre de demandes en attente que le poste pourrait prendre, lu dans la
  réponse du serveur à la dernière prise (`X-Render-Pending`) ; null si le serveur ne le dit
  pas (serveur d'avant KAN-162, serveur injoignable, poste arrêté).
- `rendu_en_cours` : vrai pendant un calcul et son envoi ; jamais d'identifiant de demande.
- `horodatage` : l'heure de l'écriture (ISO 8601, UTC). Le poste réécrit le fichier à chaque
  tour (pris, passé, en pause, en erreur), pendant ses attentes et pendant un calcul, toutes les
  `REFRESH_S` au plus : un état plus vieux que 300 s est périmé (poste arrêté).
- `version` : la version de ce format (`FORMAT_VERSION`).

Écriture atomique : un fichier temporaire du même dossier, puis `os.replace`. Rien du projet,
aucun identifiant, aucun contenu : seulement ces quatre clés.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# Sonar S5443, faux positif : un état public (644) sans rien de secret, lu par les agents de la
# machine ; un tiers local qui l'écraserait ne ferait que retarder un agent.
STATE_FILE = Path("/tmp/pd-poste.etat")  # NOSONAR
FORMAT_VERSION = 1
MODE = 0o644
REFRESH_S = 60.0
"""Écart le plus long entre deux écritures (attente, calcul) ; la garde des agents tient un
état pour périmé au-delà de 300 s."""


def state_payload(
    en_file: int | None, rendu_en_cours: bool, now: datetime | None = None
) -> dict[str, object]:
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    return {
        "en_file": en_file if isinstance(en_file, int) and en_file >= 0 else None,
        "rendu_en_cours": bool(rendu_en_cours),
        "horodatage": stamp,
        "version": FORMAT_VERSION,
    }


def write_state(
    path: Path, en_file: int | None, rendu_en_cours: bool, now: datetime | None = None
) -> bool:
    """Écrit l'état (temporaire du même dossier, droits 644, puis `os.replace`) ; False si
    l'écriture a échoué : le poste continue sans (l'état n'est qu'une aide aux agents)."""
    data = json.dumps(state_payload(en_file, rendu_en_cours, now), ensure_ascii=False) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), MODE)  # mkstemp crée en 600, quel que soit l'umask
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return False
    return True
