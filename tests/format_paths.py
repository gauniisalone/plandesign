"""KAN-75 : chemins des champs (et valeurs d'énumération) des modèles que l'application relit
sur disque, pour comparer deux versions du format. Sert au test de garde de la descente et à
produire l'instantané d'une version de production (`python -m tests.format_paths`, lancé
avec le code de cette version en PYTHONPATH)."""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

MODELS = {
    "project.json": "openplan.schemas.scene:Project",
    "board.json": "openplan.schemas.board:Board",
    "comments.json": "openplan.pipeline.comments:CommentFile",
    "events.jsonl": "openplan.schemas.events:ProjectEvent",
    # Une entrée de l'index des fichiers joints (liste de MediaRef, relue strictement).
    "media/index.json": "openplan.schemas.board:MediaRef",
}
"""Fichiers d'un projet et le modèle strict qui les relit (un champ inconnu y est refusé)."""


def _model(spec: str) -> Any:
    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


def schema_paths(schema: dict[str, Any]) -> set[str]:
    """`.levels{}.scale_basis.confidence` pour un champ, `…level=fiable` pour une valeur
    d'énumération, `{}` pour une clé de dictionnaire, `[]` pour un élément de liste ; une
    définition récursive s'arrête à `→Nom`."""
    defs = schema.get("$defs", {})
    out: set[str] = set()

    def walk(node: dict[str, Any], path: str, seen: frozenset[str]) -> None:
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            if name in seen:
                out.add(f"{path}→{name}")
                return
            walk(defs[name], path, seen | {name})
            return
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                walk(sub, path, seen)
        for value in node.get("enum", []):
            out.add(f"{path}={value}")
        if "const" in node:
            out.add(f"{path}={node['const']}")
        for prop, sub in node.get("properties", {}).items():
            out.add(f"{path}.{prop}")
            walk(sub, f"{path}.{prop}", seen)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]", seen)
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"], f"{path}{{}}", seen)

    walk(schema, "", frozenset())
    return out


def current_paths() -> dict[str, list[str]]:
    return {
        file: sorted(schema_paths(_model(spec).model_json_schema(mode="validation")))
        for file, spec in MODELS.items()
    }


if __name__ == "__main__":
    json.dump(current_paths(), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
