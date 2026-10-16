"""D2 : matières de rendu CC0 à la demande : registre, empreintes, installation, Blender."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openplan.adapters import materials as store
from openplan.schemas.materials import MaterialEntry, MaterialRegistry


def test_registry_is_cc0_with_evidence_and_fingerprints() -> None:
    registry = store.load_materials()
    finishes = {m.finish for m in registry.materials if m.kind == "surface"}
    assert finishes == {
        "plaster",
        "wood_floor",
        "wood_dark",
        "wood_boards",
        "stone",
        "stone_rubble",
        "concrete",
        "timber",
        # V1 (ambiances) : chaux, béton de sol, chêne foncé, bois peint blanc.
        "limewash",
        "concrete_floor",
        "oak_dark",
        "painted_wood",
    }
    assert sum(1 for m in registry.materials if m.kind == "sky") == 1
    for m in registry.materials:
        assert m.license == "cc0" and m.license_evidence.startswith("https://polyhaven.com/license")
        assert all(len(f.sha256) == 64 for f in m.files.values())
    assert sum(m.megabytes for m in registry.materials) < 30, "1K : léger"


def test_registry_refuses_what_is_not_proven() -> None:
    entry = store.load_materials().materials[0].model_dump()
    with pytest.raises(ValueError):
        MaterialEntry.model_validate({**entry, "license": "cc_by"})
    with pytest.raises(ValueError):
        MaterialEntry.model_validate({**entry, "files": {"diff": entry["files"]["diff"]}})
    with pytest.raises(ValueError):
        MaterialRegistry.model_validate({"materials": [entry, entry]})


def _fake(entry: MaterialEntry, tmp: Path, monkeypatch: pytest.MonkeyPatch, *, wrong: bool = False):  # type: ignore[no-untyped-def]
    payload = {spec.url: f"{entry.id}-{c}".encode() for c, spec in entry.files.items()}
    files = {
        c: spec.model_copy(update={"sha256": hashlib.sha256(payload[spec.url]).hexdigest()})
        for c, spec in entry.files.items()
    }
    fixed = entry.model_copy(update={"files": files})
    monkeypatch.setattr(store, "_fetch", lambda url: b"faux" if wrong else payload[url])
    return fixed


def test_pull_checks_every_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = store.load_materials().materials[0]
    good = _fake(entry, tmp_path, monkeypatch)
    assert not store.is_installed(good, tmp_path)
    store.pull(good, tmp_path)
    assert store.is_installed(good, tmp_path)
    assert "cc0" in (tmp_path / good.id / store.MARKER).read_text(encoding="utf-8")
    other = store.load_materials().materials[1]
    bad = _fake(other, tmp_path, monkeypatch, wrong=True)
    with pytest.raises(store.MaterialError, match="empreinte"):
        store.pull(bad, tmp_path)
    assert not store.is_installed(bad, tmp_path)


def test_installed_map_lists_only_what_is_there(tmp_path: Path) -> None:
    assert store.installed_map(tmp_path) == {"surfaces": {}, "sky": None}
    registry = store.load_materials()
    floor = next(m for m in registry.materials if m.finish == "wood_floor")
    folder = tmp_path / floor.id
    folder.mkdir()
    for c in floor.files:
        (folder / store.file_name(floor, c)).write_bytes(b"x")
    (folder / store.MARKER).write_text("{}", encoding="utf-8")
    mapped = store.installed_map(tmp_path)
    assert set(mapped["surfaces"]) == {"wood_floor"} and mapped["sky"] is None  # type: ignore[arg-type]
    plaster = next(m for m in registry.materials if m.finish == "plaster")
    assert plaster.brightness > 1.0, "enduit éclairci : murets clairs par défaut"
