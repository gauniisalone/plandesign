"""Le paquet s'importe et sa version est cohérente avec les métadonnées installées."""

from importlib import import_module, metadata

import pytest

import openplan

LAYERS = ["schemas", "geom", "scene", "validation", "adapters", "pipeline", "api", "cli"]


def test_version_matches_installed_metadata() -> None:
    assert openplan.__version__ == metadata.version("openplan-studio")


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_is_importable_and_documented(layer: str) -> None:
    module = import_module(f"openplan.{layer}")
    assert module.__doc__, f"openplan.{layer} doit avoir une docstring"
