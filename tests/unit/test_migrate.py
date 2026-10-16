"""Migrations de schema_version."""

from __future__ import annotations

from typing import Any

import pytest

from openplan.scene import migrate as m
from openplan.schemas import SCHEMA_VERSION, Project


def test_current_version_is_identity(rect_project: Project) -> None:
    data = rect_project.model_dump(mode="json")
    assert m.migrate(data) == data
    assert m.load_project(data) == rect_project


def test_missing_version_is_rejected() -> None:
    with pytest.raises(m.UnsupportedSchemaVersionError, match="manquante"):
        m.migrate({"name": "x"})


def test_newer_version_is_rejected() -> None:
    with pytest.raises(m.UnsupportedSchemaVersionError, match="plus récent"):
        m.migrate({"schema_version": "9.0.0"})


def test_malformed_version_is_rejected() -> None:
    with pytest.raises(m.UnsupportedSchemaVersionError, match="invalide"):
        m.migrate({"schema_version": "abc"})


def test_unknown_older_version_is_rejected() -> None:
    with pytest.raises(m.UnsupportedSchemaVersionError, match="aucune migration"):
        m.migrate({"schema_version": "0.0.1"})


def test_registered_migration_chain_runs(
    rect_project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "_MIGRATIONS", {})

    @m.register("0.0.9", SCHEMA_VERSION)
    def _rename_title(data: dict[str, Any]) -> dict[str, Any]:
        data["name"] = data.pop("title")
        return data

    legacy = rect_project.model_dump(mode="json")
    legacy["title"] = legacy.pop("name")
    legacy["schema_version"] = "0.0.9"

    migrated = m.load_project(legacy)
    assert migrated.name == rect_project.name
    assert migrated.schema_version == SCHEMA_VERSION
    assert legacy["schema_version"] == "0.0.9", "l'entrée n'est pas mutée"


def test_duplicate_registration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "_MIGRATIONS", {})
    m.register("0.0.1", "0.0.2")(lambda d: d)
    with pytest.raises(ValueError, match="déjà enregistrée"):
        m.register("0.0.1", "0.0.3")(lambda d: d)
