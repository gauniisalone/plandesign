"""Catalogue de meubles (3.1) : chargement, licences, plages, pose dans la scène et en 3D."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openplan.pipeline.scene3d import bake_level, build_scene, glb_bytes
from openplan.scene.catalog import (
    CATALOG_PATH,
    FURNITURE_DIR,
    CatalogError,
    asset,
    furniture_from_asset,
    load_catalog,
    load_sources,
)
from openplan.scene.ops import AddFurniture, OpError, RemoveElement, UpdateElement, apply_op
from openplan.schemas import Furniture, Source
from openplan.schemas.catalog import ACCEPTED_LICENSES, Catalog, check_assets
from openplan.schemas.common import Point2
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import check_layout
from tests.factories import attic_project, only_level, rect_room_project

MINIMAL = """
version: 1
assets:
  - id: {id}
    category: test
    label: Test
    width: {{min: 0.5, default: 1.0, max: 2.0}}
    depth: {{min: 0.5, default: 0.6, max: 0.8}}
    height: {{min: 0.5, default: 0.8, max: 1.0}}
    license: {license}
    author: test
"""


ORIGINAL_IDS = {
    "bed_double_160",
    "bed_single_90",
    "nightstand",
    "desk_140",
    "office_chair",
    "wardrobe_150",
    "chest_of_drawers",
    "low_storage_eave",
    "bookshelf_80",
    "sofa_2",
    "table_2",
}


def test_library_covers_every_room_and_keeps_the_original_ids() -> None:
    catalog = load_catalog()
    assert len(catalog.assets) >= 34
    ids = {a.id for a in catalog.assets}
    assert ids >= ORIGINAL_IDS, "les projets existants doivent rester valides"
    assert catalog.rooms == ["sejour", "chambre", "bureau", "cuisine", "salle_de_bain"]
    assert all(a.license in ACCEPTED_LICENSES for a in catalog.assets)
    assert all(
        a.label and a.author and ((a.model and a.preview) or a.procedural) for a in catalog.assets
    ), "un modèle et une vignette, sauf meuble paramétrique (H1)"
    low = catalog.get("low_storage_eave")
    assert low is not None and low.anchor == "eave_wall" and low.height.max <= 0.9
    assert {"bed_double", "desk", "wardrobe", "low_storage", "toilet", "shower", "fridge"} <= set(
        catalog.categories
    )
    assert {a.room for a in catalog.assets if a.category in ("toilet", "shower")} == {
        "salle_de_bain"
    }
    assert catalog.get("absent") is None
    assert CATALOG_PATH.name == "catalog.yaml"


def test_every_model_and_preview_has_a_cc0_source() -> None:
    """Licences (demande d'augus) : chaque fichier vient d'une source CC0 vérifiée, consignée
    dans le registre avec la preuve ; aucun fichier n'est sans source."""
    sources = load_sources()
    assert check_assets(load_catalog(), sources, FURNITURE_DIR) == []
    kenney = sources.sources[0]
    assert (
        kenney.license == "cc0" and "kenney.nl" in kenney.url and len(kenney.license_evidence) >= 2
    )
    assert (FURNITURE_DIR / "LICENSE-kenney-furniture-kit.txt").is_file()
    total = sum(f.stat().st_size for f in FURNITURE_DIR.rglob("*") if f.is_file())
    assert total < 5_000_000, f"bibliothèque trop lourde : {total} octets"


def test_assets_without_source_or_licence_are_refused(tmp_path: Path) -> None:
    import shutil

    from openplan.schemas.catalog import FurnitureSources, ModelSource

    catalog = load_catalog()
    sources = load_sources()
    stray = tmp_path / "models"
    stray.mkdir()
    shutil.copy(FURNITURE_DIR / "models" / "bedDouble.glb", stray / "intrus.glb")
    problems = check_assets(catalog, sources, tmp_path)
    assert any("intrus.glb" in p for p in problems), "fichier sans source"
    assert any("fichier absent" in p for p in problems), "modèle déclaré mais absent"
    empty = FurnitureSources(sources=[sources.sources[0].model_copy(update={"files": ["autre"]})])
    assert any("n'a pas de source" in p for p in check_assets(catalog, empty, FURNITURE_DIR))
    with pytest.raises(ValueError, match="cc0"):
        ModelSource.model_validate({**sources.sources[0].model_dump(), "license": "cc_by"})
    with pytest.raises(ValueError, match="chemin de modèle invalide"):
        Catalog.model_validate(
            {"assets": [{**catalog.assets[0].model_dump(), "model": "../../etc/passwd"}]}
        )


def test_unknown_license_and_bad_ranges_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(MINIMAL.format(id="x_test", license="unknown"), encoding="utf-8")
    with pytest.raises(CatalogError, match="licence 'unknown' refusée"):
        load_catalog(bad)
    restricted = tmp_path / "restricted.yaml"
    restricted.write_text(MINIMAL.format(id="x_test", license="commercial_restricted"))
    with pytest.raises(CatalogError, match="refusée"):
        load_catalog(restricted)
    short_id = tmp_path / "short.yaml"
    short_id.write_text(MINIMAL.format(id="x", license="cc0"))
    with pytest.raises(CatalogError, match="identifiant d'asset invalide"):
        load_catalog(short_id)
    with pytest.raises(ValueError, match="plage incohérente"):
        Catalog.model_validate(
            {
                "assets": [
                    {
                        "id": "y_test",
                        "category": "c",
                        "label": "Y",
                        "width": {"min": 1.0, "default": 0.5, "max": 2.0},
                        "depth": {"min": 0.5, "default": 0.6, "max": 0.8},
                        "height": {"min": 0.5, "default": 0.8, "max": 1.0},
                        "license": "cc0",
                        "author": "t",
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="en double"):
        Catalog.model_validate(
            {"assets": [load_catalog().assets[0].model_dump()] * 2}  # même id deux fois
        )
    with pytest.raises(CatalogError, match="introuvable"):
        load_catalog(tmp_path / "absent.yaml")
    with pytest.raises(CatalogError, match="meuble inconnu"):
        asset("licorne")


def test_furniture_from_asset_respects_the_range() -> None:
    bed = asset("bed_double_160")
    default = furniture_from_asset(bed, Point2(x=2.0, y=1.5), math.pi / 2)
    assert (default.bbox.width, default.bbox.depth, default.bbox.height) == (1.6, 2.0, 0.5)
    assert default.name == "Lit double 160 × 200" and default.source is Source.USER_DEFINED
    assert default.transform.position.z == 0.0 and default.transform.yaw == pytest.approx(
        math.pi / 2
    )
    wide = furniture_from_asset(bed, Point2(x=0, y=0), width=1.8, name="Lit")
    assert wide.bbox.width == 1.8 and wide.name == "Lit"
    with pytest.raises(CatalogError, match=r"largeur 3,00 m hors de la plage"):
        furniture_from_asset(bed, Point2(x=0, y=0), width=3.0)


def test_add_furniture_op_then_edit_and_remove() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    after = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=2.5))
    )
    assert after.history[-1].label == "Ajouter un meuble"
    desk = next(e for e in only_level(after).elements.values() if isinstance(e, Furniture))
    assert desk.asset_id == "desk_140" and desk.category == "desk" and desk.bbox.width == 1.4
    assert validate_invariants(after).valid
    edited = apply_op(
        after,
        level_id,
        UpdateElement(
            element_id=desk.id,
            fields={"preserve": True, "bbox": {**desk.bbox.model_dump(), "width": 1.6}},
        ),
    )
    changed = only_level(edited).elements[desk.id]
    assert isinstance(changed, Furniture) and changed.preserve and changed.bbox.width == 1.6
    removed = apply_op(edited, level_id, RemoveElement(element_id=desk.id))
    assert desk.id not in only_level(removed).elements
    with pytest.raises(OpError, match="meuble inconnu"):
        apply_op(project, level_id, AddFurniture(asset_id="licorne", position=Point2(x=0, y=0)))
    with pytest.raises(OpError, match="hors de la plage"):
        apply_op(
            project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=0, y=0), width=5)
        )


def test_furniture_is_baked_as_its_model_at_the_size_of_its_box() -> None:
    """F2 : la boîte reste dans le bake (règles, repli) et porte son modèle ; la scène 3D et le
    GLB montrent le modèle low-poly, à la taille et au lacet de la boîte."""
    project = rect_room_project()
    level_id = only_level(project).id
    after = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="wardrobe_150", position=Point2(x=2.0, y=1.5), yaw=math.pi / 4),
    )
    bake = bake_level(after, level_id, style="realistic")
    boxes = [p for p in bake.prisms if p.kind == "furniture"]
    assert len(boxes) == 1
    box = boxes[0]
    assert len(box.exterior) == 4 and box.z0 == 0.0 and box.z1 == pytest.approx(2.0)
    assert box.model == "models/bookcaseClosedDoors.glb" and box.yaw == pytest.approx(math.pi / 4)
    xs = [p.x for p in box.exterior]
    assert max(xs) - min(xs) == pytest.approx((1.5 + 0.6) / math.sqrt(2))  # tourné de 45°
    scene = build_scene(bake)
    furniture = next(e for e in only_level(after).elements.values() if isinstance(e, Furniture))
    mesh = scene.geometry[str(furniture.id)]
    assert len(mesh.faces) > 12, "un modèle, pas une boîte"
    lo, hi = mesh.bounds
    assert hi[2] - lo[2] == pytest.approx(2.0, abs=1e-6) and lo[2] == pytest.approx(0.0, abs=1e-6)
    assert (hi[0] - lo[0]) == pytest.approx((1.5 + 0.6) / math.sqrt(2), rel=0.02)
    colors = {tuple(c) for c in mesh.visual.face_colors[:, :3]}
    assert len(colors) > 1, "les couleurs des matières du modèle sont gardées"
    assert glb_bytes(after, level_id)[:4] == b"glTF"


@pytest.mark.parametrize("asset_id", ["toilet", "sofa_2", "bed_double_160"])
def test_model_front_faces_plus_y(asset_id: str) -> None:
    """Orientation : réservoir du WC, dossier du canapé, tête du lit à l'arrière (−y), donc la
    face avant regarde +y local, comme la boîte et l'accrochage au mur."""
    from openplan.pipeline.scene3d import furniture_mesh

    project = rect_room_project()
    level_id = only_level(project).id
    after = apply_op(
        project, level_id, AddFurniture(asset_id=asset_id, position=Point2(x=2.0, y=1.5))
    )
    prism = next(p for p in bake_level(after, level_id).prisms if p.kind == "furniture")
    mesh = furniture_mesh(prism)
    assert mesh is not None
    v = mesh.vertices
    high = v[v[:, 2] > v[:, 2].min() + 0.75 * (v[:, 2].max() - v[:, 2].min())]
    assert high[:, 1].mean() < 1.5, f"{asset_id} : la partie haute doit être à l'arrière"


def test_missing_model_falls_back_to_the_box(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.pipeline import scene3d

    project = rect_room_project()
    level_id = only_level(project).id
    after = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    monkeypatch.setattr(scene3d, "_model_of", lambda _f: None)
    bake = bake_level(after, level_id)
    box = next(p for p in bake.prisms if p.kind == "furniture")
    assert box.model is None
    furniture = next(e for e in only_level(after).elements.values() if isinstance(e, Furniture))
    assert len(build_scene(bake).geometry[str(furniture.id)].faces) == 12


def test_low_storage_fits_under_the_eave_but_a_wardrobe_does_not() -> None:
    """Le onzième meuble (rangement bas ≤ 0,90 m) tient contre le mur d'égout d'un comble ;
    une armoire au même endroit est signalée par la règle de hauteur sous pente."""
    project = attic_project()
    level_id = only_level(project).id
    near_eave = Point2(x=2.0, y=0.5)
    low = apply_op(project, level_id, AddFurniture(asset_id="low_storage_eave", position=near_eave))
    assert check_layout(low) == []
    tall = apply_op(project, level_id, AddFurniture(asset_id="wardrobe_150", position=near_eave))
    (violation,) = check_layout(tall)
    assert violation.type == "element_too_tall_under_slope" and "Armoire" in violation.message
