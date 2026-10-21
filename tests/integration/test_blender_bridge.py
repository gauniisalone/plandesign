"""Pont Blender : ignoré si Blender est absent. Compare le GLB Blender au GLB trimesh."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import BlenderError, find_blender, run_blender
from openplan.pipeline.scene3d import bake_level, build_scene, scene_volume
from openplan.schemas import Project
from tests.factories import attic_project, cut_corner_project, only_level

BLENDER = find_blender(Settings.from_env().blender_bin)


def test_missing_blender_is_a_clear_error(rect_project: Project, tmp_path: Path) -> None:
    level = only_level(rect_project)
    with pytest.raises(BlenderError, match="introuvable"):
        run_blender(rect_project, level.id, tmp_path, blender_bin="/nonexistent/blender")
    assert find_blender("/nonexistent/blender") is None, "un chemin explicite fait autorité"


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
@pytest.mark.parametrize("kind", ["rect", "attic", "oblique"])
def test_blender_builds_same_volume_as_trimesh(
    kind: str, rect_project: Project, tmp_path: Path
) -> None:
    """Pièce droite (prismes), combles (polyèdres : murs découpés, pentes) et pièce à pan
    coupé (KAN-29 : murs en onglet, fenêtre dans le mur oblique)."""
    project = {"rect": rect_project, "attic": attic_project(), "oblique": cut_corner_project()}[
        kind
    ]
    level = only_level(project)
    result = run_blender(
        project, level.id, tmp_path / "run", blender_bin=str(BLENDER), preview=kind == "rect"
    )
    names = {a.name for a in result.artifacts}
    assert {"bake.json", "scene.blend", "scene.glb", "blender_report.json"} <= names
    if kind == "rect":
        assert "preview.png" in names, "l'aperçu Cycles CPU doit être produit"
    report = json.loads((tmp_path / "run" / "blender_report.json").read_text())
    bake = bake_level(project, level.id)
    assert report["objects"] == len(bake.prisms) + len(bake.polyhedra)
    assert result.model_ids[0].startswith("blender@4.")

    blender_scene = trimesh.load(tmp_path / "run" / "scene.glb", file_type="glb")
    reference = build_scene(bake)
    assert scene_volume(blender_scene) == pytest.approx(scene_volume(reference), rel=0.01)


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
@pytest.mark.parametrize(
    ("axis", "value", "above"), [("z", 1.2, False), ("x", 2.0, False), ("x", 2.0, True)]
)
def test_blender_section_cuts_the_scene(
    axis: str, value: float, above: bool, rect_project: Project, tmp_path: Path
) -> None:
    """Coupe (E3) : plus rien au-delà du plan, faces coupées bouchées (volume fermé)."""
    level = only_level(rect_project)
    run_blender(
        rect_project,
        level.id,
        tmp_path / "run",
        blender_bin=str(BLENDER),
        preview=False,
        section=(axis, value),
        section_keep_above=above,
        backdrop=axis != "z",
    )
    report = json.loads((tmp_path / "run" / "blender_report.json").read_text())
    assert report.get("backdrop", False) is (axis != "z")
    cut = trimesh.load(tmp_path / "run" / "scene.glb", file_type="glb")
    whole = build_scene(bake_level(rect_project, level.id))
    # glTF : y en haut ; x reste x.
    column = 1 if axis == "z" else 0
    if above:
        assert cut.bounds[0][column] >= value - 1e-3
    else:
        assert cut.bounds[1][column] <= value + 1e-3
    # Le poché (E3b) sépare les faces coupées en primitives à part : le volume se mesure sur
    # l'ensemble des triangles réunis.
    cut_volume = abs(trimesh.util.concatenate(cut.dump()).volume)
    assert 0.2 * scene_volume(whole) < cut_volume < 0.8 * scene_volume(whole)


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
def test_photo_render_with_materials(tmp_path: Path) -> None:
    """Rendu depuis la caméra des combles, matières procédurales, GPU si le pilote le permet."""
    from openplan.pipeline.render import render_photo_view

    project = attic_project()
    level = only_level(project)
    result = render_photo_view(
        project, level.id, tmp_path / "run", blender_bin=str(BLENDER), samples=16, gpu=True
    )
    preview = next(a for a in result.artifacts if a.name == "preview.png")
    assert preview.path.stat().st_size > 10_000, "image non vide"
    assert result.step == "rendering.photo"
    assert any(m.startswith("cycles@") for m in result.model_ids)
    assert any("Image de synthèse" in a.text for a in result.assumptions)
    report = json.loads((tmp_path / "run" / "blender_report.json").read_text())
    assert report["style"] == "realistic" and report["device"] in {"CPU", "CUDA", "OPTIX"}
    print(f"rendu {report['device']} 16 échantillons : {report['render_s']:.1f} s")


def test_blender_places_furniture_models(tmp_path: Path) -> None:
    """F2 : Blender importe le modèle du meuble, à la taille et à la place de sa boîte (même
    emprise que trimesh, à 2 cm près)."""
    import math

    import trimesh

    from openplan.scene.ops import AddFurniture, apply_op
    from openplan.schemas.common import Point2
    from tests.factories import only_level, rect_room_project

    if find_blender(None) is None:
        pytest.skip("Blender absent")
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bed_double_160", position=Point2(x=2.0, y=1.5), yaw=math.pi / 2),
    )
    result = run_blender(project, level_id, tmp_path, preview=False)
    assert not [w for w in result.warnings if "modèle" in w], result.warnings
    bed = next(
        e
        for e in only_level(project).elements.values()
        if getattr(e, "category", "") == "bed_double"
    )
    glb = trimesh.load(tmp_path / "scene.glb", force="scene")
    # Blender exporte un morceau par matière : on réunit tous ceux du meuble.
    pieces = []
    for node in glb.graph.nodes_geometry:
        transform, name = glb.graph[node]
        if str(bed.id) in node or str(bed.id) in name:
            piece = glb.geometry[name].copy()
            piece.apply_transform(transform)
            pieces.append(piece)
    blender_bed = trimesh.util.concatenate(pieces)
    reference = build_scene(bake_level(project, level_id)).geometry[str(bed.id)]
    assert len(blender_bed.faces) > 12
    # Le GLB de Blender est Y vers le haut : on compare les étendues triées (emprise et hauteur).
    assert sorted(blender_bed.extents) == pytest.approx(sorted(reference.extents), abs=0.02)


def test_blender_renders_roof_windows(tmp_path: Path) -> None:
    """D1 : la pente percée, le cadre et le vitrage passent dans Blender sans avertissement."""
    import trimesh

    from openplan.scene.ops import AddRoofWindow, apply_op
    from openplan.schemas.elements import RoofPlane
    from tests.factories import attic_project, only_level

    if find_blender(None) is None:
        pytest.skip("Blender absent")
    project = attic_project()
    level_id = only_level(project).id
    roof = next(e for e in only_level(project).elements.values() if isinstance(e, RoofPlane))
    project = apply_op(project, level_id, AddRoofWindow(roof=roof.id, offset=1.0, distance=0.6))
    result = run_blender(project, level_id, tmp_path, preview=False)
    assert not [w for w in result.warnings if "polyèdre" in w], result.warnings
    window = next(e for e in only_level(project).elements.values() if e.type == "roof_window")
    glb = trimesh.load(tmp_path / "scene.glb", force="scene")
    assert any(str(window.id) in name for name in glb.graph.nodes), "vitrage et cadre exportés"


def test_blender_uses_installed_materials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 : une matière installée habille sa finition (image PBR), les autres restent dessinées ;
    le ciel HDRI installé devient le fond du monde."""
    import json

    import numpy as np
    from PIL import Image

    from openplan.adapters import materials as store
    from tests.factories import attic_project, only_level

    if find_blender(None) is None:
        pytest.skip("Blender absent")
    root = tmp_path / "materials"
    registry = store.load_materials()
    floor = next(m for m in registry.materials if m.finish == "wood_floor")
    sky = next(m for m in registry.materials if m.kind == "sky")
    for entry in (floor, sky):
        folder = root / entry.id
        folder.mkdir(parents=True)
        for c in entry.files:
            target = folder / store.file_name(entry, c)
            if c == "hdri":
                Image.fromarray(np.full((8, 16, 3), 200, dtype=np.uint8)).save(
                    target.with_suffix(".png")
                )
                target.with_suffix(".png").rename(target)
            else:
                Image.fromarray(np.full((8, 8, 3), 128, dtype=np.uint8)).save(target, format="JPEG")
        (folder / store.MARKER).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENPLAN_MATERIALS_DIR", str(root))
    project = attic_project()
    out = tmp_path / "render"
    run_blender(project, only_level(project).id, out, preview=False, style="realistic")
    sent = json.loads((out / "materials.json").read_text(encoding="utf-8"))
    assert set(sent["surfaces"]) == {"wood_floor"} and sent["sky"].endswith("hdri.hdr")


def test_blender_dresses_the_render(tmp_path: Path) -> None:
    """D4 : lampe de chevet, cadre et tapis passent dans Blender, sans modèle manquant."""
    from openplan.scene.ops import AddFurniture, apply_op
    from openplan.schemas.common import Point2
    from tests.factories import only_level, rect_room_project

    if find_blender(None) is None:
        pytest.skip("Blender absent")
    project = rect_room_project()
    level_id = only_level(project).id
    for asset_id, x, y, yaw in (
        ("bed_single_90", 2.0, 1.905, 3.14159265),
        ("nightstand", 3.2, 2.6, 3.14159265),
        ("rug_rect", 2.0, 1.2, 0.0),
    ):
        project = apply_op(
            project, level_id, AddFurniture(asset_id=asset_id, position=Point2(x=x, y=y), yaw=yaw)
        )
    result = run_blender(project, level_id, tmp_path, preview=False, style="realistic")
    assert not [w for w in result.warnings if "absent" in w or "vide" in w], result.warnings


def test_blender_writes_the_element_map_and_linen_points_to_its_bed(tmp_path: Path) -> None:
    """P4a : après l'aperçu, la carte des éléments ; le linge du lit porte l'identifiant du lit
    (aucun « decor-n » pour lui), et un clic sur le lit le désigne."""
    import numpy as np

    from openplan.pipeline.pick import IDMAP_JSON, IDMAP_NPZ, pick
    from openplan.scene.ops import AddFurniture, apply_op
    from openplan.schemas.bake import RenderCamera
    from openplan.schemas.common import Point2, Point3
    from tests.factories import only_level, rect_room_project

    if find_blender(None) is None:
        pytest.skip("Blender absent")
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        AddFurniture(asset_id="bed_double_160", position=Point2(x=2.0, y=1.5), yaw=0.0),
    )
    bed = next(e for e in only_level(project).elements.values() if e.type == "furniture")
    camera = RenderCamera(
        position=Point3(x=2.0, y=4.2, z=1.6),
        yaw=-1.5708,
        pitch=-0.45,
        focal_px=110.0,
        width_px=160,
        height_px=120,
    )
    result = run_blender(
        project, level_id, tmp_path, camera=camera, samples=2, style="realistic", idmap=True
    )
    assert any(a.name == IDMAP_NPZ for a in result.artifacts)
    index = np.load(tmp_path / IDMAP_NPZ)["index"]
    ids = json.loads((tmp_path / IDMAP_JSON).read_text(encoding="utf-8"))["ids"]
    assert index.shape == (120, 160)
    bake = json.loads((tmp_path / "bake.json").read_text(encoding="utf-8"))
    hosted = [k for k, d in enumerate(bake["decor"]) if d.get("host")]
    assert hosted, "le lit est habillé"
    assert not [k for k in hosted if f"decor-{k}" in ids], "le linge désigne le lit"
    rows, cols = np.nonzero(index == ids.index(str(bed.id)) + 1)
    assert len(rows) > 100
    hit = pick(project, tmp_path, (cols[0] + 0.5) / 160, (rows[0] + 0.5) / 120)
    assert hit.kind == "element" and hit.element_id == bed.id
