"""Ressources du paquet (4.10b) : lues dans le dépôt, sinon dans la copie embarquée du wheel."""

from __future__ import annotations

from pathlib import Path

from openplan.adapters.registry import REGISTRY_PATH
from openplan.pipeline.blender_bridge import SCRIPT
from openplan.resources import REPO_ROOT, bundled_web_dir, resource
from openplan.scene.catalog import CATALOG_PATH


def test_repo_resources_are_found_in_a_checkout() -> None:
    assert REGISTRY_PATH == REPO_ROOT / "models" / "registry.yaml" and REGISTRY_PATH.is_file()
    assert CATALOG_PATH == REPO_ROOT / "assets" / "catalog.yaml" and CATALOG_PATH.is_file()
    assert SCRIPT == REPO_ROOT / "blender" / "scripts" / "build_scene.py" and SCRIPT.is_file()
    assert bundled_web_dir().parts[-3:] == ("apps", "web", "dist")


def test_installed_package_falls_back_to_the_bundled_copy(tmp_path: Path) -> None:
    site = tmp_path / "site-packages"
    bundled = site / "openplan" / "_bundled"
    (bundled / "models").mkdir(parents=True)
    (bundled / "models" / "registry.yaml").write_text("models: []\n", encoding="utf-8")
    (bundled / "web").mkdir()
    (bundled / "web" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    # Pas de dépôt autour du paquet installé : c'est la copie embarquée qui sert.
    found = resource("models", "registry.yaml", repo_root=site, bundled=bundled)
    assert found == bundled / "models" / "registry.yaml"
    assert bundled_web_dir(repo_root=site, bundled=bundled) == bundled / "web"
    # Une ressource absente des deux côtés pointe quand même vers l'embarqué (message clair
    # à l'ouverture, pas de chemin du dépôt de développement).
    missing = resource("assets", "catalog.yaml", repo_root=site, bundled=bundled)
    assert missing == bundled / "assets" / "catalog.yaml" and not missing.exists()
