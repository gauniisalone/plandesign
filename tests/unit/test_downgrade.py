"""KAN-75 : descente du format vers la version de production (3da4d52, schéma 0.16.0), pour
un retour arrière sans restaurer l'archive. Marches, arbre de données (idempotent, --dry-run),
commande, et la garde : tout champ ajouté depuis la production est retiré par une marche, ou
toléré par l'ancien code (raison dite)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openplan.cli.main import main
from openplan.pipeline.reference import camera_basis, with_basis
from openplan.scene.downgrade import REMOVED_PATHS, downgrade_data, downgrade_tree
from openplan.scene.factory import new_project
from openplan.scene.migrate import UnsupportedSchemaVersionError, load_project
from openplan.scene.store import ProjectStore
from openplan.schemas.common import SCHEMA_VERSION
from openplan.schemas.scene import ImageRef, Project
from tests.format_paths import current_paths

PRODUCTION = Path(__file__).parents[1] / "fixtures" / "format_paths_3da4d52.json"
"""Instantané des chemins de champs de la production (MEP 5, 3da4d52), produit par
`python -m tests.format_paths` avec le code de 3da4d52 en PYTHONPATH."""
TOLERATED: dict[str, dict[str, str]] = {
    "events.jsonl": {
        "": "une ligne d'un type ou d'un champ inconnu est ignorée par l'ancien code "
        "(pipeline/events.py : « jamais une erreur »)",
    },
}
"""Champs nouveaux laissés en place : l'ancien code les relit sans erreur (raison dite)."""


def _with_confidence() -> Project:
    project = new_project("Grenier")
    ref = ImageRef(
        path="images/x.jpg", sha256="0" * 64, width_px=10, height_px=10, exif_stripped=True
    )
    project = project.model_copy(update={"images": {ref.id: ref}})
    level = next(iter(project.levels))
    camera = camera_basis(ref.id, 0.5, 1.45, entered=True)
    reference = camera.model_copy(update={"source": "reference", "previous": camera})
    return with_basis(project, level, reference)


def _dump(project: Project) -> dict[str, object]:
    data: dict[str, object] = json.loads(project.model_dump_json())
    return data


def test_the_step_removes_the_confidence_everywhere_and_is_idempotent() -> None:
    data = _dump(_with_confidence())
    assert data["schema_version"] == SCHEMA_VERSION == "0.17.0"
    down, removed, lost = downgrade_data(data, "0.16.0")
    assert down["schema_version"] == "0.16.0" and removed == 4  # la mesure et sa « previous »
    assert "confiance" in lost[0]
    text = json.dumps(down)
    assert "confidence" not in text
    again, removed_again, _ = downgrade_data(down, "0.16.0")
    assert again == down and removed_again == 0
    # La remontée relit le projet descendu sans perte d'autre chose que la confiance.
    assert load_project(json.loads(json.dumps(down))).levels


def test_a_newer_project_is_refused_with_its_reason() -> None:
    with pytest.raises(UnsupportedSchemaVersionError, match="plus récent"):
        downgrade_data({"schema_version": "9.0.0"}, "0.16.0")


def test_the_tree_dry_run_then_for_real_then_again(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data" / "projects")
    store.save(_with_confidence())
    store.save(new_project("Sans échelle"))
    file = next(
        p for p in (tmp_path / "data").rglob("project.json") if "confidence" in p.read_text()
    )
    before = file.read_bytes()
    dry = downgrade_tree(tmp_path / "data", "0.16.0", dry_run=True)
    assert len(dry.changed) == 2 and dry.removed == 4 and not dry.errors
    assert file.read_bytes() == before, "--dry-run n'écrit rien"
    done = downgrade_tree(tmp_path / "data", "0.16.0")
    assert len(done.changed) == 2 and done.removed == 4
    assert json.loads(file.read_text())["schema_version"] == "0.16.0"
    assert "confidence" not in file.read_text()
    again = downgrade_tree(tmp_path / "data", "0.16.0")
    assert again.changed == [] and again.unchanged == 2


def test_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.save(_with_confidence())
    assert main(["downgrade", "--to", "0.16.0", "--dry-run", str(tmp_path)]) == 0
    assert "1 projet(s) à descendre" in capsys.readouterr().out
    assert json.loads(project.read_text())["schema_version"] == "0.17.0"
    assert main(["downgrade", "--to", "0.16.0", str(tmp_path)]) == 0
    assert json.loads(project.read_text())["schema_version"] == "0.16.0"
    assert main(["downgrade", "--to", "0.1.0", str(tmp_path)]) == 2


def test_every_field_added_since_production_is_removed_or_tolerated() -> None:
    """Un ticket qui ajoute un champ à un fichier relu par un modèle strict doit l'ajouter à
    une marche de descente (REMOVED_PATHS), ou dire ici pourquoi l'ancien code le tolère."""
    production = json.loads(PRODUCTION.read_text(encoding="utf-8"))
    missing: list[str] = []
    for file, paths in current_paths().items():
        added = set(paths) - set(production.get(file) or [])
        removed = REMOVED_PATHS.get(file, set())
        tolerated = TOLERATED.get(file, {})
        for path in sorted(added):
            if any(
                path == r or path.startswith((r + ".", r + "=", r + "[", r + "{")) for r in removed
            ):
                continue
            if any(path.startswith(t) for t in tolerated):
                continue
            missing.append(f"{file} : {path}")
    assert not missing, "champs nouveaux sans descente :\n" + "\n".join(missing)
