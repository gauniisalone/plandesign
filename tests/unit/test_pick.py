"""P4a : désigner un élément dans un rendu. Le décor qui habille un meuble le désigne ; la
carte des éléments (écrite par Blender) se lit en coordonnées relatives ; le décor sans
porteur et le fond sont dits comme tels ; un rendu sans carte ni scène est refusé."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openplan.pipeline.decor import HOSTED_KINDS, dress_level
from openplan.pipeline.pick import IDMAP_JSON, IDMAP_NPZ, PickError, pick
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Furniture, Wall
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _bedroom(asset: str) -> Project:
    project = rect_room_project(5.0, 4.5)
    level = only_level(project)
    return apply_op(
        project, level.id, AddFurniture(asset_id=asset, position=Point2(x=2.5, y=1.2), yaw=0.0)
    )


def _render_dir(tmp_path: Path, ids: list[str], decor: list[dict[str, object]]) -> Path:
    folder = tmp_path / "run" / "blender"
    folder.mkdir(parents=True)
    index = np.zeros((4, 8), dtype=np.uint16)
    index[:, 0:4] = 1  # moitié gauche : premier élément
    index[0:2, 4:8] = 2  # quart haut droit : second
    np.savez_compressed(folder / IDMAP_NPZ, index=index)
    (folder / IDMAP_JSON).write_text(json.dumps({"ids": ids, "size": [8, 4], "render_s": 1.0}))
    (folder / "bake.json").write_text(json.dumps({"decor": decor}))
    return folder


def test_bed_linen_and_bedside_lamp_point_to_their_furniture() -> None:
    level = only_level(_bedroom("bed_builtin"))
    bed = next(e for e in level.elements.values() if isinstance(e, Furniture))
    decor = dress_level(level)
    linen = [d for d in decor if d.kind in ("pillow", "duvet", "throw")]
    assert linen and all(d.host == bed.id for d in linen)
    unhosted = [d for d in decor if d.kind not in HOSTED_KINDS]
    assert all(d.host is None for d in unhosted), "tapis, plantes, rideaux : sans porteur"


def test_pick_reads_the_map_in_relative_coordinates(tmp_path: Path) -> None:
    project = _bedroom("bed_builtin")
    level = only_level(project)
    bed = next(e for e in level.elements.values() if isinstance(e, Furniture))
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    folder = _render_dir(tmp_path, [str(bed.id), str(wall.id)], [])
    hit = pick(project, folder, 0.1, 0.9)
    assert hit.kind == "element" and hit.element_id == bed.id
    assert hit.element_type == "furniture" and hit.category == bed.category
    assert hit.name == (bed.name or bed.category)
    assert pick(project, folder, 0.9, 0.1).element_type == "wall"
    nothing = pick(project, folder, 0.9, 0.9)
    assert nothing.kind == "none" and not nothing.on_demand


def test_decor_without_host_and_removed_elements(tmp_path: Path) -> None:
    project = _bedroom("bed_double_160")
    folder = _render_dir(
        tmp_path,
        ["decor-1", "8b6f3f5e-7d3c-4d55-9a1b-2b4c6d8e0f12"],
        [{"kind": "pillow"}, {"kind": "floor_plant"}],
    )
    plant = pick(project, folder, 0.2, 0.5)
    assert plant.kind == "decor" and plant.decor_kind == "floor_plant"
    assert plant.name == "grande plante"
    gone = pick(project, folder, 0.8, 0.2)
    assert gone.kind == "none" and "retiré" in gone.name


def test_a_render_without_map_nor_scene_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "old" / "blender"
    folder.mkdir(parents=True)
    with pytest.raises(PickError, match="scène Blender"):
        pick(_bedroom("bed_double_160"), folder, 0.5, 0.5)


def test_replace_furniture_keeps_place_id_and_takes_the_new_model() -> None:
    from openplan.scene.ops import OpError, ReplaceFurniture

    project = _bedroom("bed_double_160")
    level = only_level(project)
    bed = next(e for e in level.elements.values() if isinstance(e, Furniture))
    after = apply_op(
        project, level.id, ReplaceFurniture(element_id=bed.id, asset_id="bed_single_90")
    )
    new = only_level(after).elements[bed.id]
    assert isinstance(new, Furniture) and new.asset_id == "bed_single_90"
    assert new.transform.yaw == bed.transform.yaw
    assert new.bbox.width < bed.bbox.width

    # Le dos reste en place (contre le mur) : face avant +y pour un lacet nul.
    def back(f: Furniture) -> float:
        return f.transform.position.y - f.bbox.depth * f.transform.scale / 2

    assert back(new) == pytest.approx(back(bed))
    assert new.transform.position.x == pytest.approx(bed.transform.position.x)
    sofa = apply_op(
        project, level.id, AddFurniture(asset_id="sofa_2", position=Point2(x=2.5, y=3.5))
    )
    sofa_el = next(
        e
        for e in only_level(sofa).elements.values()
        if isinstance(e, Furniture) and e.category == "sofa"
    )
    armchair = apply_op(
        sofa, level.id, ReplaceFurniture(element_id=sofa_el.id, asset_id="armchair")
    )
    swapped = only_level(armchair).elements[sofa_el.id]
    assert isinstance(swapped, Furniture) and back(swapped) == pytest.approx(back(sofa_el))
    assert len(armchair.history) == len(sofa.history) + 1, "un seul pas d'historique"
    assert after.history[-1].label == "Remplacer un meuble"
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    with pytest.raises(OpError, match="meuble inconnu"):
        apply_op(project, level.id, ReplaceFurniture(element_id=wall.id, asset_id="bed_single_90"))


def test_outline_png_marks_the_picked_element(tmp_path: Path) -> None:
    from io import BytesIO

    from PIL import Image

    from openplan.pipeline.pick import OUTLINE_RGB, outline_png

    folder = _render_dir(tmp_path, ["a", "b"], [])
    image = Image.open(BytesIO(outline_png(folder, 0.1, 0.9, width_px=2)))
    rgba = np.asarray(image)
    assert image.mode == "RGBA" and rgba.shape == (4, 8, 4)
    assert tuple(rgba[3, 3][:3]) == OUTLINE_RGB and rgba[3, 3][3] == 255, "bord du premier élément"
    assert rgba[3, 7][3] == 0, "le fond reste transparent"
    empty = np.asarray(Image.open(BytesIO(outline_png(folder, 0.9, 0.9))))
    assert empty[..., 3].max() == 0
