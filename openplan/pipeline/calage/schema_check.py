"""KAN-124 : validation stricte d'une sortie contre `extraction.schema.json`.

Le dépôt n'embarque pas la bibliothèque `jsonschema` : ce module valide le sous-ensemble du
draft 2020-12 que le schéma emploie (type, enum, const, required, properties,
additionalProperties, maxLength, minimum, maximum, allOf, if/then). Un mot-clé inconnu du
schéma lève `UnsupportedSchemaError` au chargement : changer le schéma sans ce module ne passe
pas en silence (test sur les 200 « attendu » du banc).
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

SCHEMA_FILE = "extraction.schema.json"

KNOWN = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "maxLength",
        "minimum",
        "maximum",
        "allOf",
        "if",
        "then",
    }
)


class UnsupportedSchemaError(ValueError):
    """Le schéma emploie un mot-clé que ce validateur ne sait pas vérifier."""


@cache
def extraction_schema() -> dict[str, Any]:
    """Le schéma du banc KAN-124, lu une fois (fichier du paquet)."""
    text = resources.files("openplan.pipeline.calage").joinpath(SCHEMA_FILE).read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    _check_keywords(schema)
    return schema


def _check_keywords(node: Any) -> None:
    if not isinstance(node, dict):
        return
    unknown = set(node) - KNOWN
    if unknown:
        msg = f"mot-clé de schéma non pris en charge : {sorted(unknown)}"
        raise UnsupportedSchemaError(msg)
    for key in ("properties",):
        for sub in node.get(key, {}).values():
            _check_keywords(sub)
    for sub in node.get("allOf", []):
        _check_keywords(sub)
    for key in ("if", "then"):
        if key in node:
            _check_keywords(node[key])


_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "string": (str,),
    "boolean": (bool,),
    "null": (type(None),),
    "array": (list,),
}


def _is_type(value: Any, name: str) -> bool:
    if name in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        return name == "number" or float(value).is_integer()
    return isinstance(value, _TYPES[name])


def errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Les écarts de `value` à `schema` (liste vide : conforme)."""
    out: list[str] = []
    kind = schema.get("type")
    if kind is not None:
        names = kind if isinstance(kind, list) else [kind]
        if not any(_is_type(value, n) for n in names):
            return [f"{path} : type {type(value).__name__} hors de {names}"]
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        out.append(f"{path} : {value!r} hors de l'énumération")
    if "const" in schema and not _same(value, schema["const"]):
        out.append(f"{path} : {value!r} ≠ {schema['const']!r}")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{path} : plus de {schema['maxLength']} caractères")
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path} : {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path} : {value} > {schema['maximum']}")
    if isinstance(value, dict):
        out.extend(_object_errors(value, schema, path))
    for sub in schema.get("allOf", []):
        out.extend(errors(value, sub, path))
    if "if" in schema and not errors(value, schema["if"], path) and "then" in schema:
        out.extend(errors(value, schema["then"], path))
    return out


def _object_errors(value: dict[str, Any], schema: dict[str, Any], path: str) -> list[str]:
    out = [f"{path}.{k} : manquant" for k in schema.get("required", []) if k not in value]
    props: dict[str, Any] = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        out.extend(f"{path}.{k} : clé non permise" for k in value if k not in props)
    for key, sub in props.items():
        if key in value:
            out.extend(errors(value[key], sub, f"{path}.{key}"))
    return out


def _same(a: Any, b: Any) -> bool:
    """Égalité JSON : un booléen n'est jamais un nombre."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return bool(a == b)


def is_valid(value: Any) -> bool:
    return not errors(value, extraction_schema())
