"""KAN-108 (suite, SonarCloud S2083) : le processus Z-Image ne lit et n'écrit que dans le dossier
de sa tâche (celui de `job.json`, créé par le parent). Un chemin qui en sort, par `..`, par un
chemin absolu ou par un lien symbolique, est refusé avant toute lecture ou écriture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openplan.adapters.neural import zimage_worker as w


def _task(work: Path, **files: str) -> Path:
    job = {"control": "control.png", "out": "out.png", "result": "result.json", **files}
    task = work / "job.json"
    task.write_text(json.dumps(job), encoding="utf-8")
    return task


def test_the_files_of_the_task_stay_in_its_folder(tmp_path: Path) -> None:
    work = tmp_path / "openplan-zimage-1"
    work.mkdir()
    absolute = str(work / "out.png")
    job, files = w.task_paths(_task(work, out=absolute))
    assert job["out"] == absolute
    real = work.resolve()
    assert files == {
        "control": real / "control.png",
        "out": real / "out.png",
        "result": real / "result.json",
    }


@pytest.mark.parametrize("key", w.TASK_FILES)
@pytest.mark.parametrize("escape", ["../ailleurs.json", "/etc/passwd", "sous/../../x.png"])
def test_a_path_out_of_the_task_folder_is_refused(tmp_path: Path, key: str, escape: str) -> None:
    work = tmp_path / "openplan-zimage-2"
    work.mkdir()
    with pytest.raises(w.OutsideTaskError, match="hors du dossier de la tâche"):
        w.task_paths(_task(work, **{key: escape}))


def test_a_symlink_that_leads_out_is_refused_one_that_stays_in_is_kept(tmp_path: Path) -> None:
    work = tmp_path / "openplan-zimage-3"
    work.mkdir()
    outside = tmp_path / "dehors"
    outside.mkdir()
    (work / "fuite").symlink_to(outside)
    (work / "lien.json").symlink_to(outside / "result.json")
    for trap in ("fuite/result.json", "lien.json"):
        with pytest.raises(w.OutsideTaskError):
            w.task_paths(_task(work, result=trap))
    (work / "ici").mkdir()
    (work / "raccourci").symlink_to(work / "ici")
    _, files = w.task_paths(_task(work, result="raccourci/result.json"))
    assert files["result"] == (work / "ici" / "result.json").resolve()


def test_main_refuses_before_writing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le refus tombe avant le calcul : aucun fichier n'est écrit hors du dossier (ni dedans)."""
    monkeypatch.setattr(w, "block_network", lambda: None)  # ne pas couper le réseau des tests
    work = tmp_path / "openplan-zimage-4"
    work.mkdir()
    target = tmp_path / "vole.json"
    with pytest.raises(w.OutsideTaskError):
        w.main(["zimage_worker", str(_task(work, result=str(target)))])
    assert not target.exists()
    assert sorted(p.name for p in work.iterdir()) == ["job.json"]
