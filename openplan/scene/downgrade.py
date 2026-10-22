"""KAN-75 : retour du format des projets vers une version précédente, pour un retour arrière
de l'application sans restaurer l'archive des données.

Le pendant de `migrate` : chaque marche descend d'une version et retire ce que la montée a
ajouté. L'ancien code refuse un projet « plus récent » (409) ou un champ inconnu (modèles
stricts) ; après descente, il le relit comme avant. Ce qui est perdu : les seules données des
champs retirés (dit par marche dans `DOWNGRADES`).

Depuis 0.18.0 (KAN-91), une marche peut aussi réécrire le `board.json` du projet (les liens
entre cartes). Hors de ces deux fichiers, rien à réécrire : les lignes d'un type futur dans
`events.jsonl` (réactions, KAN-78) sont ignorées par l'ancien code, jamais une erreur, et
`reactions.json` lui est inconnu (gardé, il revient à la remontée). Le test de garde
(`tests/unit/test_downgrade.py`) compare les champs des modèles lus sur disque à ceux de la
version de production : un champ nouveau que ce module ne retire pas le fait échouer.

Procédure (Infra) : arrêter l'application, `openplan downgrade --to 0.17.0 <racine des
données>`, puis basculer sur l'ancienne version ; l'archive de `data/` reste le dernier recours.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openplan.scene.migrate import UnsupportedSchemaVersionError, _parse
from openplan.scene.store import PROJECT_FILE
from openplan.schemas.common import SCHEMA_VERSION

DowngradeFn = Callable[[dict[str, Any]], int]
"""Modifie le projet en place ; renvoie le nombre de valeurs retirées."""

_DOWNGRADES: dict[str, tuple[str, DowngradeFn, str]] = {}
"""version de départ → (version d'arrivée, fonction, ce qui est perdu)."""

_BOARD_DOWNGRADES: dict[str, DowngradeFn] = {}
"""version de départ → fonction appliquée au `board.json` du projet (KAN-91), s'il existe."""

BOARD_FILE = "board.json"
REMOVED_PATHS: dict[str, set[str]] = {"project.json": set(), BOARD_FILE: set()}
"""Chemins de champs (notation de `tests/unit/test_downgrade.py`) retirés par une marche :
la garde vérifie que tout champ ajouté depuis la production y figure."""


def step(
    from_version: str,
    to_version: str,
    *,
    lost: str,
    removes: set[str],
    board: DowngradeFn | None = None,
    board_removes: set[str] | None = None,
) -> Callable[[DowngradeFn], DowngradeFn]:
    """Une marche de descente : `fn` réécrit `project.json` ; `board`, s'il est donné, réécrit
    le `board.json` du même projet (la version du format est celle du projet)."""

    def decorator(fn: DowngradeFn) -> DowngradeFn:
        if from_version in _DOWNGRADES:
            msg = f"descente déjà enregistrée depuis {from_version}"
            raise ValueError(msg)
        _DOWNGRADES[from_version] = (to_version, fn, lost)
        REMOVED_PATHS["project.json"] |= removes
        if board is not None:
            _BOARD_DOWNGRADES[from_version] = board
            REMOVED_PATHS[BOARD_FILE] |= board_removes or set()
        return fn

    return decorator


def _drop_board_links(board: dict[str, Any]) -> int:
    removed = 0
    for key in ("links", "cut_links"):
        if key in board:
            removed += len(board[key]) if isinstance(board[key], list) else 1
            del board[key]
    for item in board.get("items") or []:
        if isinstance(item, dict) and "ignored_tags" in item:
            removed += len(item["ignored_tags"] or [])
            del item["ignored_tags"]
    return removed


@step(
    "0.18.0",
    "0.17.0",
    lost="les liens entre cartes de l'espace de travail et les étiquettes désactivées ; les "
    "images restent dans leurs cartes (l'ancienne application relit toutes les photos)",
    removes=set(),
    board=_drop_board_links,
    board_removes={".links", ".cut_links", ".items[].ignored_tags"},
)
def _board_links_only(data: dict[str, Any]) -> int:
    return 0  # project.json n'a pas changé en 0.18.0 : seul board.json descend


def _scale_bases(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Chaque `scale_basis` des niveaux, et celles qu'elles gardent en `previous`."""
    for level in (data.get("levels") or {}).values():
        basis = level.get("scale_basis") if isinstance(level, dict) else None
        while isinstance(basis, dict):
            yield basis
            basis = basis.get("previous")


@step(
    "0.17.0",
    "0.16.0",
    lost="la confiance de l'estimation tenue avec l'échelle (elle revient à la prochaine "
    "estimation ou mesure)",
    removes={
        ".levels{}.scale_basis.confidence",
        ".levels{}.scale_basis.confidence_inputs",
    },
)
def _drop_scale_confidence(data: dict[str, Any]) -> int:
    removed = 0
    for basis in _scale_bases(data):
        for key in ("confidence", "confidence_inputs"):
            if key in basis:
                del basis[key]
                removed += 1
    return removed


def downgrade_data(data: dict[str, Any], target: str) -> tuple[dict[str, Any], int, list[str]]:
    """Descend un projet (dictionnaire lu sur disque) jusqu'à `target`. Renvoie le projet, le
    nombre de valeurs retirées et ce qui a été perdu. Sans effet s'il y est déjà ou en dessous."""
    version = data.get("schema_version")
    if not isinstance(version, str):
        msg = "schema_version manquante"
        raise UnsupportedSchemaVersionError(msg)
    if _parse(version) > _parse(SCHEMA_VERSION):
        msg = f"le projet est en {version}, plus récent que ce logiciel ({SCHEMA_VERSION})"
        raise UnsupportedSchemaVersionError(msg)
    removed, lost = 0, []
    while _parse(version) > _parse(target):
        found = _DOWNGRADES.get(version)
        if found is None:
            msg = f"aucune descente connue depuis {version}"
            raise UnsupportedSchemaVersionError(msg)
        to_version, fn, what = found
        removed += fn(data)
        lost.append(what)
        data["schema_version"] = to_version
        version = to_version
    return data, removed, lost


@dataclass
class DowngradeReport:
    target: str
    changed: list[Path] = field(default_factory=list)
    unchanged: int = 0
    removed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)


def project_files(root: Path) -> list[Path]:
    """Les `project.json` sous la racine des données (dossiers de projets, à plat ou dans
    `projects/`), jamais dans les rendus ni la corbeille."""
    found = {
        p
        for pattern in (PROJECT_FILE, f"*/{PROJECT_FILE}", f"*/*/{PROJECT_FILE}")
        for p in root.glob(pattern)
    }
    return sorted(p for p in found if "runs" not in p.parts and ".trash" not in p.parts)


def downgrade_tree(root: Path, target: str, *, dry_run: bool = False) -> DowngradeReport:
    """Tous les projets sous `root` ; idempotent (un projet déjà à `target` n'est pas touché).
    Écriture atomique (fichier temporaire puis remplacement) : un arrêt en cours de route laisse
    chaque projet entier, à l'ancienne ou à la nouvelle version."""
    if target not in {v for v, *_ in _DOWNGRADES.values()} and target != SCHEMA_VERSION:
        msg = f"version cible inconnue : {target}"
        raise UnsupportedSchemaVersionError(msg)
    report = DowngradeReport(target=target)
    for path in project_files(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            before = data.get("schema_version")
            steps = _steps(before, target) if isinstance(before, str) else []
            data, removed, _ = downgrade_data(data, target)
            board_path = path.with_name(BOARD_FILE)
            board = None
            if board_path.is_file() and any(s in _BOARD_DOWNGRADES for s in steps):
                board = json.loads(board_path.read_text(encoding="utf-8"))
                for version in steps:
                    if version in _BOARD_DOWNGRADES:
                        removed += _BOARD_DOWNGRADES[version](board)
        except (OSError, ValueError) as exc:
            report.errors.append((path, str(exc)[:200]))
            continue
        if data.get("schema_version") == before:
            report.unchanged += 1
            continue
        report.changed.append(path)
        report.removed += removed
        if not dry_run:
            # Le tableau d'abord : un arrêt entre les deux laisse un projet encore « récent »,
            # que la prochaine descente reprend (elle est idempotente).
            if board is not None:
                _write(path.with_name(BOARD_FILE), board)
            _write(path, data)
    return report


def _steps(version: str, target: str) -> list[str]:
    """Les versions de départ des marches entre `version` et `target`."""
    out: list[str] = []
    try:
        while _parse(version) > _parse(target) and version in _DOWNGRADES:
            out.append(version)
            version = _DOWNGRADES[version][0]
    except UnsupportedSchemaVersionError:
        return out
    return out


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".downgrade.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
