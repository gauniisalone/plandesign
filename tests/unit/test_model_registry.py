"""Le registre des modèles est cohérent avec le code et documenté."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from openplan.adapters import exif_camera
from openplan.adapters.heuristic import box_detector, camera
from openplan.adapters.neural import (
    depth_anything,
    finish,
    inspiration,
    openings,
    siglip,
    web_depth,
    zimage,
)
from openplan.adapters.remote import openai_compatible

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "models" / "registry.yaml"
DOC = ROOT / "docs" / "models" / "model_registry.md"
REQUIRED = {
    "id",
    "role",
    "kind",
    "license",
    "commercial_ok",
    "remote",
    "memory_mb",
    "device",
    "status",
    "notes",
}
STATUSES = {"implemented", "optional", "planned"}
IN_CODE = {
    exif_camera.MODEL_ID,
    camera.MODEL_ID,
    box_detector.MODEL_ID,
    depth_anything.MODEL_ID,
    inspiration.SD_MODEL_ID,
    inspiration.CONTROLNET_MODEL_ID,
    finish.CANNY_MODEL_ID,
    finish.IP_ADAPTER_MODEL_ID,
    web_depth.WEB_MODEL_ID,
    openings.MODEL_ID,
    siglip.MODEL_ID,
    *zimage.Z_MODEL_IDS,
}
"""Algorithmes locaux : identifiant du registre = model_id dans le code."""
REMOTE_IN_CODE = {openai_compatible.REGISTRY_ID}
"""Clients de services distants implémentés (le model_id réel dépend de l'hôte configuré)."""


def load() -> list[dict[str, Any]]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    models: list[dict[str, Any]] = data["models"]
    return models


def test_entries_are_complete_and_documented() -> None:
    doc = DOC.read_text(encoding="utf-8")
    ids = [m["id"] for m in load()]
    assert len(ids) == len(set(ids)), "identifiants dupliqués"
    for model in load():
        missing = REQUIRED - model.keys()
        assert not missing, f"{model['id']} : champs manquants {sorted(missing)}"
        assert model["status"] in STATUSES
        assert "unknown" not in str(model["license"]).lower(), "licence inconnue interdite"
        assert f"`{model['id']}`" in doc, f"{model['id']} absent de docs/models/model_registry.md"


def test_ids_used_in_code_are_implemented() -> None:
    by_id = {m["id"]: m for m in load()}
    for model_id in IN_CODE:
        assert model_id in by_id, f"{model_id} utilisé dans le code mais absent du registre"
        assert by_id[model_id]["status"] == "implemented"
        assert by_id[model_id]["remote"] is False
    implemented = {m["id"] for m in load() if m["status"] == "implemented"}
    assert implemented == IN_CODE | REMOTE_IN_CODE, (
        "un modèle déclaré implémenté doit exister dans le code"
    )


def test_license_acceptance_is_declared_for_rail_weights() -> None:
    for model in load():
        if "rail" in str(model["license"]).lower():
            assert model.get("requires_license_acceptance") is True, model["id"]
            assert model.get("license_url"), model["id"]
            assert "restrictions d'usage" in model["notes"] or "acceptation" in model["notes"]


def test_remote_services_require_consent_and_are_not_default() -> None:
    for model in load():
        if model["remote"]:
            assert model.get("requires_consent") is True, model["id"]
            assert model["commercial_ok"] is False, (
                "un service distant n'est jamais accepté par défaut"
            )
            if model["status"] == "implemented":
                assert model["id"] in REMOTE_IN_CODE
                assert model["module"].startswith("openplan.adapters.remote."), (
                    "le réseau vit uniquement sous adapters.remote (import-linter)"
                )
