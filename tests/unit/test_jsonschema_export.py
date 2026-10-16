"""Export JSON Schema : un fichier par modèle public, JSON valide, structure attendue."""

import json
from pathlib import Path

from openplan.schemas.jsonschema_export import EXPORTED, export_json_schemas, main


def test_export_writes_one_schema_per_model(tmp_path: Path) -> None:
    written = export_json_schemas(tmp_path / "schemas")
    assert {p.stem for p in written} == {f"{name}.schema" for name in EXPORTED}
    project_schema = json.loads((tmp_path / "schemas" / "project.schema.json").read_text())
    assert "levels" in project_schema["properties"]
    assert "Wall" in project_schema["$defs"]


def test_schema_has_no_property_titles_and_all_fields_required() -> None:
    from openplan.schemas.jsonschema_export import build_schema
    from openplan.schemas.scene import Level

    schema = build_schema(Level)
    assert "title" not in schema["properties"]["height"]  # type: ignore[index]
    assert set(schema["required"]) >= {"height", "vertices", "elements", "id"}  # type: ignore[arg-type]
    wall = schema["$defs"]["Wall"]  # type: ignore[index]
    assert wall["title"] == "Wall" and "title" not in wall["properties"]["thickness"]


def test_cli_entry_point(tmp_path: Path) -> None:
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / "step_result.schema.json").is_file()
