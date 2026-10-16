"""Exporte les JSON Schema des modèles publics, source des types TypeScript du front.

Usage : python -m openplan.schemas.jsonschema_export <dossier_de_sortie>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from openplan.schemas.bake import Bake
from openplan.schemas.derived import DerivedLevel
from openplan.schemas.results import StepResult, ValidationReport
from openplan.schemas.scene import Project

EXPORTED: dict[str, type[BaseModel]] = {
    "project": Project,
    "step_result": StepResult,
    "validation_report": ValidationReport,
    "derived_level": DerivedLevel,
    "bake": Bake,
}


def _normalize_for_responses(node: object) -> None:
    """Adapte le schéma à ce que l'API **renvoie** : tous les champs présents, sans alias.

    - Retire les `title` des propriétés : Pydantic titre chaque champ ("Height", "Id"...),
      ce que les générateurs de types transforment en alias parasites (`Height5`).
    - Marque toutes les propriétés `required` : la sérialisation émet toujours tous les
      champs, défauts compris. Les corps de requête ont leurs propres types côté client.
    """
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            for prop in properties.values():
                if isinstance(prop, dict):
                    prop.pop("title", None)
            node["required"] = sorted(properties)
        for value in node.values():
            _normalize_for_responses(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_for_responses(item)


def build_schema(model: type[BaseModel]) -> dict[str, object]:
    """JSON Schema en mode sérialisation, normalisé pour les réponses de l'API."""
    schema: dict[str, object] = model.model_json_schema(mode="serialization")
    _normalize_for_responses(schema)
    return schema


def export_json_schemas(out_dir: Path) -> list[Path]:
    """Écrit un fichier <nom>.schema.json par modèle exporté et renvoie les chemins."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in EXPORTED.items():
        schema = build_schema(model)
        target = out_dir / f"{name}.schema.json"
        target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    for path in export_json_schemas(args.out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
