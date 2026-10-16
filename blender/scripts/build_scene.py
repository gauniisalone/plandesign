"""Construit une scène Blender depuis un « bake » OpenPlan (liste de prismes).

Dépendances : bpy, bmesh et la bibliothèque standard uniquement. Licence : GPL-3.0-or-later
(les scripts qui importent bpy sont des œuvres dérivées de Blender).

Usage :
    blender -b --python build_scene.py -- --bake bake.json --report report.json \
        [--out-blend scene.blend] [--out-glb scene.glb] [--preview preview.png] \
        [--preview-size 640x480] [--samples 8]
"""

import argparse
import json
import math
import os
import sys
import time
import traceback

import bmesh
import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bake", required=True)
    parser.add_argument("--assets", default=None, help="dossier des modèles du catalogue")
    parser.add_argument("--materials", default=None, help="JSON des matières installées (D2)")
    parser.add_argument("--report", required=True)
    parser.add_argument("--out-blend")
    parser.add_argument("--out-glb")
    parser.add_argument("--preview")
    parser.add_argument("--preview-size", default="640x480")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--camera", help="JSON RenderCamera : pose et focale imposées")
    parser.add_argument("--denoise", action="store_true")
    parser.add_argument(
        "--style",
        choices=("provenance", "realistic"),
        default="provenance",
        help="provenance : couleurs plates par source ; realistic : matières procédurales",
    )
    parser.add_argument("--gpu", action="store_true", help="Cycles sur GPU si disponible")
    parser.add_argument(
        "--depth", help="écrit la passe de profondeur (m, distance au plan caméra) en .npy"
    )
    parser.add_argument(
        "--light", help="JSON d'éclairage (soleil, ciel, lampes) : voir render.daylight_spec"
    )
    parser.add_argument(
        "--section",
        help="plan de coupe (E3) « axe:valeur[:+] », axe z, x ou y, en mètres ; + garde le dessus",
    )
    parser.add_argument(
        "--backdrop",
        action="store_true",
        help="fond gris clair uni vu par la caméra (rendu en coupe, E3b)",
    )
    parser.add_argument(
        "--idmap",
        help="dossier où écrire la carte des éléments (P4, idmap.npz/json) après l'aperçu",
    )
    return parser.parse_args(argv)


def tag_decor(index, item, collection):
    """P4 : chaque objet d'un décor (cadre et passe-partout, plaid et franges…) désigne son
    meuble porteur s'il en a un, sinon « decor-n »."""
    base = "decor-%d" % index
    key = item.get("host") or base
    for obj in collection.all_objects:
        name = obj.name
        if name == base or name.startswith(base + "_") or name.startswith(base + "."):
            obj["openplan_id"] = key


def apply_section(collection, spec):
    """Coupe (E3) : chaque maillage est tranché par le plan, on garde le côté d'où l'on
    regarde la coupe : sous le plan par défaut, au-dessus avec le suffixe « :+ » (le rendu
    retire la moitié où se trouve la caméra)."""
    import mathutils

    parts = spec.split(":")
    axis, value = parts[0], float(parts[1])
    sign = -1.0 if len(parts) > 2 and parts[2] == "+" else 1.0
    co = {"z": (0.0, 0.0, value), "x": (value, 0.0, 0.0), "y": (0.0, value, 0.0)}[axis]
    no = {"z": (0.0, 0.0, sign), "x": (sign, 0.0, 0.0), "y": (0.0, sign, 0.0)}[axis]
    for obj in list(collection.all_objects):
        if obj.type != "MESH" or obj.data is None:
            continue
        inverse = obj.matrix_world.inverted()
        plane_co = inverse @ mathutils.Vector(co)
        plane_no = (obj.matrix_world.to_3x3().transposed() @ mathutils.Vector(no)).normalized()
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        cut = bmesh.ops.bisect_plane(
            bm,
            geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
            plane_co=plane_co,
            plane_no=plane_no,
            clear_outer=True,
        )
        # Faces coupées bouchées en poché (gris très foncé, comme l'aperçu 3D) : un mur
        # tranché se lit plein, pas creux.
        edges = [e for e in cut["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_valid]
        filled = []
        if edges:
            try:
                filled = bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"]
            except (RuntimeError, ValueError):
                filled = []
        if filled:
            slots = obj.data.materials
            poche = poche_material()
            if poche.name not in [m.name for m in slots if m is not None]:
                slots.append(poche)
            index = [m.name if m is not None else "" for m in slots].index(poche.name)
            for face in filled:
                face.material_index = index
        bm.to_mesh(obj.data)
        bm.free()


def poche_material():
    """Poché des faces coupées (E3b) : mat, presque noir."""
    mat = bpy.data.materials.get("openplan_poche")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name="openplan_poche")
    mat.diffuse_color = (0.05, 0.05, 0.055, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.05, 0.05, 0.055, 1.0)
        bsdf.inputs["Roughness"].default_value = 1.0
    return mat


def neutral_backdrop(scene):
    """Rendu en coupe (E3b) : la caméra voit un fond gris clair uni à la place du ciel ; la
    lumière du ciel, elle, ne change pas (seuls les rayons caméra sont remplacés)."""
    world = scene.world
    if world is None or not world.use_nodes:
        return False
    nodes, links = world.node_tree.nodes, world.node_tree.links
    output = next((n for n in nodes if n.type == "OUTPUT_WORLD"), None)
    if output is None or not output.inputs["Surface"].links:
        return False
    current = output.inputs["Surface"].links[0].from_socket
    path = nodes.new("ShaderNodeLightPath")
    plain = nodes.new("ShaderNodeBackground")
    plain.inputs["Color"].default_value = (0.92, 0.92, 0.92, 1.0)
    plain.inputs["Strength"].default_value = 1.0
    mix = nodes.new("ShaderNodeMixShader")
    links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(current, mix.inputs[1])
    links.new(plain.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    return True


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    return scene


_materials = {}
_STYLE = {"value": "provenance"}
_PALETTE = {"value": None}
"""Ambiance de l'habillage (C3), lue dans le bake : tapis, linge, suspension."""


def material_for(color):
    key = tuple(color)
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_%02x%02x%02x" % key)
    rgba = (key[0] / 255.0, key[1] / 255.0, key[2] / 255.0, 1.0)
    mat.diffuse_color = rgba
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = rgba
        principled.inputs["Roughness"].default_value = 0.7
    _materials[key] = mat
    return mat


def _ramp(nodes, stops):
    ramp = nodes.new("ShaderNodeValToRGB")
    elements = ramp.color_ramp.elements
    elements[0].position, elements[0].color = stops[0]
    elements[1].position, elements[1].color = stops[1]
    for position, color in stops[2:]:
        e = elements.new(position)
        e.color = color
    return ramp


def finish_material(finish):
    """Matière procédurale (nœuds Cycles, aucune texture externe) : bois, pierre, enduit."""
    key = ("finish", finish)
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_finish_%s" % finish)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    principled = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    if finish in ("wood_floor", "wood_boards"):
        # Lames le long de X : veinage = bandes fortement distordues par un bruit fin, et une
        # variation de teinte lame par lame (bruit à grande échelle étiré le long de X).
        wave = nodes.new("ShaderNodeTexWave")
        wave.wave_type = "BANDS"
        wave.bands_direction = "Y"
        wave.wave_profile = "SAW"
        wave.inputs["Scale"].default_value = 9.0 if finish == "wood_floor" else 6.0
        wave.inputs["Distortion"].default_value = 9.0
        wave.inputs["Detail"].default_value = 10.0
        wave.inputs["Detail Scale"].default_value = 8.0
        wave.inputs["Detail Roughness"].default_value = 0.7
        links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
        dark, light = (
            ((0.40, 0.26, 0.14, 1.0), (0.55, 0.39, 0.23, 1.0))
            if finish == "wood_floor"
            else ((0.46, 0.31, 0.17, 1.0), (0.62, 0.46, 0.28, 1.0))
        )
        grain = _ramp(nodes, [(0.0, dark), (1.0, light)])
        links.new(wave.outputs["Fac"], grain.inputs["Fac"])
        planks = nodes.new("ShaderNodeMapping")
        planks.inputs["Scale"].default_value = (0.15, 8.0, 1.0)  # étiré le long des lames
        links.new(coords.outputs["Object"], planks.inputs["Vector"])
        plank_noise = nodes.new("ShaderNodeTexNoise")
        plank_noise.inputs["Scale"].default_value = 1.0
        plank_noise.inputs["Detail"].default_value = 0.0
        links.new(planks.outputs["Vector"], plank_noise.inputs["Vector"])
        tint = _ramp(nodes, [(0.0, (0.62, 0.56, 0.5, 1.0)), (1.0, (1.25, 1.18, 1.1, 1.0))])
        links.new(plank_noise.outputs["Fac"], tint.inputs["Fac"])
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = 1.0
        links.new(grain.outputs["Color"], mix.inputs["Color1"])
        links.new(tint.outputs["Color"], mix.inputs["Color2"])
        links.new(mix.outputs["Color"], principled.inputs["Base Color"])
        principled.inputs["Roughness"].default_value = 0.5
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.04
        links.new(wave.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    elif finish == "stone":
        # Moellons : cellules de Voronoï (≈ 25 cm), joints fins et sombres, teinte propre à
        # chaque pierre, grain de surface, relief léger.
        voronoi = nodes.new("ShaderNodeTexVoronoi")
        voronoi.feature = "DISTANCE_TO_EDGE"
        voronoi.inputs["Scale"].default_value = 4.5
        voronoi.inputs["Randomness"].default_value = 0.9
        links.new(mapping.outputs["Vector"], voronoi.inputs["Vector"])
        cells = nodes.new("ShaderNodeTexVoronoi")
        cells.feature = "F1"
        cells.inputs["Scale"].default_value = 4.5
        cells.inputs["Randomness"].default_value = 0.9
        links.new(mapping.outputs["Vector"], cells.inputs["Vector"])
        joints = _ramp(
            nodes,
            [
                (0.0, (0.22, 0.20, 0.18, 1.0)),
                (0.035, (0.45, 0.42, 0.38, 1.0)),
                (0.09, (1.0, 1.0, 1.0, 1.0)),
            ],
        )
        links.new(voronoi.outputs["Distance"], joints.inputs["Fac"])
        # Teinte par pierre : la couleur aléatoire de la cellule réduite à un gris, puis
        # ramenée dans une gamme beige-gris étroite (pas de mosaïque colorée).
        cell_grey = nodes.new("ShaderNodeRGBToBW")
        links.new(cells.outputs["Color"], cell_grey.inputs["Color"])
        per_stone = _ramp(nodes, [(0.0, (0.46, 0.43, 0.38, 1.0)), (1.0, (0.68, 0.64, 0.57, 1.0))])
        links.new(cell_grey.outputs["Val"], per_stone.inputs["Fac"])
        grain = nodes.new("ShaderNodeTexNoise")
        grain.inputs["Scale"].default_value = 40.0
        grain.inputs["Detail"].default_value = 8.0
        links.new(mapping.outputs["Vector"], grain.inputs["Vector"])
        grain_ramp = _ramp(nodes, [(0.0, (0.8, 0.78, 0.75, 1.0)), (1.0, (1.05, 1.03, 1.0, 1.0))])
        links.new(grain.outputs["Fac"], grain_ramp.inputs["Fac"])
        stone = nodes.new("ShaderNodeMixRGB")
        stone.blend_type = "MULTIPLY"
        stone.inputs["Fac"].default_value = 1.0
        links.new(per_stone.outputs["Color"], stone.inputs["Color1"])
        links.new(grain_ramp.outputs["Color"], stone.inputs["Color2"])
        final = nodes.new("ShaderNodeMixRGB")
        final.blend_type = "MULTIPLY"
        final.inputs["Fac"].default_value = 1.0
        links.new(stone.outputs["Color"], final.inputs["Color1"])
        links.new(joints.outputs["Color"], final.inputs["Color2"])
        links.new(final.outputs["Color"], principled.inputs["Base Color"])
        principled.inputs["Roughness"].default_value = 0.9
        relief = nodes.new("ShaderNodeMixRGB")
        relief.blend_type = "ADD"
        relief.inputs["Fac"].default_value = 0.3
        links.new(joints.outputs["Color"], relief.inputs["Color1"])
        links.new(grain.outputs["Fac"], relief.inputs["Color2"])
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.12
        links.new(relief.outputs["Color"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    else:
        # Enduit ou béton : teinte unie légèrement bruitée.
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 25.0
        noise.inputs["Detail"].default_value = 3.0
        links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        base = (0.86, 0.84, 0.79, 1.0) if finish == "plaster" else (0.60, 0.60, 0.59, 1.0)
        lighter = (0.93, 0.91, 0.87, 1.0) if finish == "plaster" else (0.70, 0.70, 0.69, 1.0)
        ramp = _ramp(nodes, [(0.0, base), (1.0, lighter)])
        links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
        principled.inputs["Roughness"].default_value = 0.85
    _materials[key] = mat
    return mat


def skylight_glass():
    """Vitrage de velux : surtout transparent (le soleil et le ciel entrent, ombres nettes),
    un peu de reflet ; pas de réfraction, donc pas de caustiques à calculer."""
    key = ("glass", "skylight")
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_skylight_glass")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    out = nodes.new("ShaderNodeOutputMaterial")
    clear = nodes.new("ShaderNodeBsdfTransparent")
    clear.inputs["Color"].default_value = (0.95, 0.98, 1.0, 1.0)
    shine = nodes.new("ShaderNodeBsdfGlossy")
    shine.inputs["Roughness"].default_value = 0.03
    mix = nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.12
    links.new(clear.outputs["BSDF"], mix.inputs[1])
    links.new(shine.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    _materials[key] = mat
    return mat


def material_of(solid, uv=False):
    if solid.get("finish") == "glass" or solid.get("kind") == "roof_window":
        return skylight_glass()
    if _STYLE["value"] == "realistic":
        finish = solid.get("finish", "plaster")
        if finish == "metal":
            return _metal("railing", (0.04, 0.04, 0.045), rough=0.45)
        if finish == "linen":
            # Matelas du lit encastré (H1) : lin écru fin, sans la trame grosse des tapis.
            return _fabric("bed_linen", (0.86, 0.83, 0.77), rough=0.95, bump=0.08)
        return pbr_material(finish, uv=uv) or finish_material(_FALLBACK.get(finish, finish))
    return material_for(solid["color"])


_ASSETS = {"dir": None}
_MATERIALS = {"surfaces": {}, "sky": None}
_FALLBACK = {
    "wood_dark": "wood_floor",
    "stone_rubble": "stone",
    "timber": "wood_floor",
    "limewash": "plaster",
    "concrete_floor": "concrete",
    "oak_dark": "wood_floor",
    "painted_wood": "plaster",
}


def pbr_material(finish, tile_override=None, uv=False):
    """Matière PBR installée (D2) : couleur, normales, rugosité en projection boîte (pas d'UV) ;
    `uv` : d'après les coordonnées de texture de l'objet (pentes, V1)."""
    spec = _MATERIALS["surfaces"].get(finish)
    key = ("pbr", finish, tile_override, uv)
    if spec is None:
        return None
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_pbr_%s%s" % (finish, "_uv" if uv else ""))
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    tile = float(tile_override or spec.get("tile_m", 1.0))
    mapping.inputs["Scale"].default_value = (1.0 / tile, 1.0 / tile, 1.0 / tile)
    links.new(coords.outputs["UV" if uv else "Object"], mapping.inputs["Vector"])

    def image(path, colorspace):
        node = nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path, check_existing=True)
        node.image.colorspace_settings.name = colorspace
        if not uv:
            node.projection = "BOX"
            node.projection_blend = 0.2
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    color = image(spec["diff"], "sRGB").outputs["Color"]
    brightness = float(spec.get("brightness", 1.0))
    saturation = float(spec.get("saturation", 1.0))
    if abs(brightness - 1.0) > 1e-3 or abs(saturation - 1.0) > 1e-3:
        tint = nodes.new("ShaderNodeHueSaturation")
        tint.inputs["Value"].default_value = brightness
        tint.inputs["Saturation"].default_value = saturation
        links.new(color, tint.inputs["Color"])
        color = tint.outputs["Color"]
    links.new(color, bsdf.inputs["Base Color"])
    links.new(image(spec["rough"], "Non-Color").outputs["Color"], bsdf.inputs["Roughness"])
    normal_map = nodes.new("ShaderNodeNormalMap")
    if uv:
        normal_map.uv_map = SLOPE_UV
    normal_map.inputs["Strength"].default_value = 0.8
    links.new(image(spec["nor"], "Non-Color").outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    _materials[key] = mat
    return mat


_LINEN = {
    "carpet": (0.80, 0.76, 0.70),
    "carpetWhite": (0.93, 0.92, 0.89),
    "carpetBlue": (0.50, 0.57, 0.62),
    "carpetDarker": (0.56, 0.51, 0.45),
    "fur": (0.90, 0.88, 0.84),
}


def _fabric(name, color, rough=0.9, bump=0.15, woven=False):
    """Tissu (D4) : lin du linge de lit et des assises, ou jute tressée des tapis."""
    key = ("fabric", name)
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_%s" % name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    if woven:
        # Trame croisée : deux séries de bandes (chaîne et trame), multipliées, bien visibles
        # à quelques mètres ; teinte assombrie dans les creux.
        warp = nodes.new("ShaderNodeTexWave")
        warp.wave_type = "BANDS"
        warp.bands_direction = "X"
        warp.inputs["Scale"].default_value = 14.0
        warp.inputs["Distortion"].default_value = 1.5
        weft = nodes.new("ShaderNodeTexWave")
        weft.wave_type = "BANDS"
        weft.bands_direction = "Y"
        weft.inputs["Scale"].default_value = 14.0
        weft.inputs["Distortion"].default_value = 1.5
        links.new(coords.outputs["Object"], warp.inputs["Vector"])
        links.new(coords.outputs["Object"], weft.inputs["Vector"])
        cross = nodes.new("ShaderNodeMath")
        cross.operation = "MULTIPLY"
        links.new(warp.outputs["Fac"], cross.inputs[0])
        links.new(weft.outputs["Fac"], cross.inputs[1])
        dark = tuple(c * 0.55 for c in color) + (1.0,)
        ramp = _ramp(nodes, [(0.0, dark), (1.0, color + (1.0,))])
        links.new(cross.outputs["Value"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        height = cross.outputs["Value"]
    else:
        bsdf.inputs["Base Color"].default_value = color + (1.0,)
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = 400.0
        links.new(coords.outputs["Object"], tex.inputs["Vector"])
        height = tex.outputs["Fac"]
    bsdf.inputs["Roughness"].default_value = rough
    if "Sheen Weight" in bsdf.inputs:
        bsdf.inputs["Sheen Weight"].default_value = 0.3
    bmp = nodes.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = bump
    links.new(height, bmp.inputs["Height"])
    links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    _materials[key] = mat
    return mat


def _metal(name, color, rough=0.35):
    key = ("metal", name)
    if key not in _materials:
        mat = simple_material("openplan_%s" % name, color, roughness=rough)
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Metallic"].default_value = 1.0
        _materials[key] = mat
    return _materials[key]


def _lampshade():
    key = ("lampshade",)
    if key not in _materials:
        mat = simple_material("openplan_lampshade", (0.95, 0.90, 0.80), roughness=0.8)
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None and "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (1.0, 0.82, 0.58, 1.0)
            bsdf.inputs["Emission Strength"].default_value = 2.5
        _materials[key] = mat
    return _materials[key]


def kenney_material(name, rug, woods=None, tints=None):
    """Les couleurs plates de Kenney deviennent des matières (D4) : bois (texture installée ou
    bois dessiné), lin, jute pour les tapis, métal, abat-jour lumineux ; le reste est gardé.
    L'ambiance (V1) choisit le bois des meubles (`woods`) et la teinte du tissu et du métal
    (`tints`, linéaire 0–1)."""
    woods = woods or {}
    tints = tints or {}
    base = name.split(".")[0]
    if rug and base.startswith("carpet"):
        if _PALETTE["value"]:
            return _rug_material(base)
        tone = (0.36, 0.28, 0.18) if base == "carpetDarker" else (0.52, 0.41, 0.26)
        return _fabric("jute_%s" % base, tone, rough=1.0, bump=0.6, woven=True)
    if base == "wood":
        finish = woods.get("wood", "wood_boards")
        return pbr_material(finish, 0.7) or finish_material(_FALLBACK.get(finish, finish))
    if base == "woodDark":
        finish = woods.get("woodDark", "wood_dark")
        return pbr_material(finish, 0.7) or finish_material(_FALLBACK.get(finish, finish))
    if base == "carpet" and "carpet" in tints:
        tone = tuple(tints["carpet"])
        return _fabric("linen_%02x%02x%02x" % tuple(int(c * 255) for c in tone), tone)
    if base in _LINEN:
        return _fabric("linen_%s" % base, _LINEN[base])
    if base.startswith("metal"):
        tone = (0.18, 0.18, 0.18) if base == "metalDark" else (0.72, 0.72, 0.70)
        if "metal" in tints:
            tone = tuple(tints["metal"])
            return _metal("%s_%02x" % (base, int(tone[0] * 255)), tone)
        return _metal(base, tone)
    if base == "lamp":
        return _lampshade()
    if base == "plant":
        return _leaf()
    return None


def _leaf():
    """Feuillage (C3) : vert profond et varié au lieu du vert menthe uni de Kenney, un peu
    satiné, que la lumière traverse légèrement."""
    key = ("leaf",)
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_leaf")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    links.new(coords.outputs["Object"], noise.inputs["Vector"])
    ramp = _ramp(nodes, [(0.3, (0.05, 0.16, 0.05, 1.0)), (0.7, (0.16, 0.32, 0.10, 1.0))])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.5
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 0.1
    _materials[key] = mat
    return mat


def _mix(nodes, blend, factor, a, b):
    """Nœud Mix (couleurs) de Blender 4 : entrées 6 et 7, sortie 2."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = blend
    mix.inputs[0].default_value = factor
    return mix, mix.inputs[6], mix.inputs[7], mix.outputs[2]


def _rug_material(base):
    """Tapis de l'ambiance (C3) : fond et larges rayures contrastées, trame tissée par-dessus ;
    le contraste survit à la finition IA, qui effaçait la jute claire et unie."""
    pal = _PALETTE["value"]
    key = ("rug", pal["style"], base)
    if key in _materials:
        return _materials[key]
    mat = bpy.data.materials.new(name="openplan_rug_%s" % pal["style"])
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    stripes = nodes.new("ShaderNodeTexWave")
    stripes.wave_type = "BANDS"
    stripes.bands_direction = "X"
    stripes.inputs["Scale"].default_value = 2.2
    links.new(coords.outputs["Generated"], stripes.inputs["Vector"])
    ramp = _ramp(
        nodes,
        [(0.0, tuple(pal["rug"]) + (1.0,)), (0.78, tuple(pal["rug_stripe"]) + (1.0,))],
    )
    ramp.color_ramp.interpolation = "CONSTANT"
    links.new(stripes.outputs["Fac"], ramp.inputs["Fac"])
    warp = nodes.new("ShaderNodeTexWave")
    warp.wave_type = "BANDS"
    warp.bands_direction = "X"
    warp.inputs["Scale"].default_value = 60.0
    weft = nodes.new("ShaderNodeTexWave")
    weft.wave_type = "BANDS"
    weft.bands_direction = "Y"
    weft.inputs["Scale"].default_value = 60.0
    links.new(coords.outputs["Generated"], warp.inputs["Vector"])
    links.new(coords.outputs["Generated"], weft.inputs["Vector"])
    cross = nodes.new("ShaderNodeMath")
    cross.operation = "MULTIPLY"
    links.new(warp.outputs["Fac"], cross.inputs[0])
    links.new(weft.outputs["Fac"], cross.inputs[1])
    weave = _ramp(nodes, [(0.0, (0.72, 0.72, 0.72, 1.0)), (1.0, (1.0, 1.0, 1.0, 1.0))])
    links.new(cross.outputs["Value"], weave.inputs["Fac"])
    _mixer, a, b, out = _mix(nodes, "MULTIPLY", 1.0, None, None)
    links.new(ramp.outputs["Color"], a)
    links.new(weave.outputs["Color"], b)
    links.new(out, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 1.0
    bmp = nodes.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = 0.5
    links.new(cross.outputs["Value"], bmp.inputs["Height"])
    links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    _materials[key] = mat
    return mat


def _sheer(name, color):
    """Voilage (C3) : clair, un peu translucide, plis verticaux en relief."""
    key = ("sheer", name)
    if key in _materials:
        return _materials[key]
    mat = simple_material("openplan_%s" % name, color, roughness=0.85, transmission=0.3)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    folds = nodes.new("ShaderNodeTexWave")
    folds.wave_type = "BANDS"
    folds.bands_direction = "X"
    folds.inputs["Scale"].default_value = 9.0
    links.new(coords.outputs["Generated"], folds.inputs["Vector"])
    bmp = nodes.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = 0.6
    links.new(folds.outputs["Fac"], bmp.inputs["Height"])
    links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    _materials[key] = mat
    return mat


def _print_material(index, item):
    """Tirage abstrait (C3) : deux aplats de l'ambiance en blocs, arche ou bandes."""
    key = ("print", index)
    if key in _materials:
        return _materials[key]
    c1 = tuple(item.get("color") or _ART[index % len(_ART)]) + (1.0,)
    c2 = tuple(item.get("accent") or _ART[(index + 1) % len(_ART)]) + (1.0,)
    paper = (0.93, 0.91, 0.86, 1.0)
    mat = bpy.data.materials.new(name="openplan_print_%d" % index)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    pattern = item.get("pattern") or "blocks"
    if pattern == "arch":
        grad = nodes.new("ShaderNodeTexGradient")
        grad.gradient_type = "SPHERICAL"
        mapping = nodes.new("ShaderNodeMapping")
        mapping.inputs["Location"].default_value = (-0.5, 0.0, -0.15)
        mapping.inputs["Scale"].default_value = (2.4, 2.4, 2.4)
        links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], grad.inputs["Vector"])
        ramp = _ramp(nodes, [(0.0, paper), (0.05, c2), (0.45, c1)])
        ramp.color_ramp.interpolation = "CONSTANT"
        links.new(grad.outputs["Fac"], ramp.inputs["Fac"])
    elif pattern == "bands":
        wave = nodes.new("ShaderNodeTexWave")
        wave.wave_type = "BANDS"
        wave.bands_direction = "Z"
        wave.inputs["Scale"].default_value = 1.6
        links.new(coords.outputs["Generated"], wave.inputs["Vector"])
        ramp = _ramp(nodes, [(0.0, c1), (0.5, c2), (0.8, paper)])
        ramp.color_ramp.interpolation = "CONSTANT"
        links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    else:
        checker = nodes.new("ShaderNodeTexChecker")
        checker.inputs["Scale"].default_value = 1.5
        checker.inputs["Color1"].default_value = c1
        checker.inputs["Color2"].default_value = c2
        links.new(coords.outputs["Generated"], checker.inputs["Vector"])
        ramp = None
    color_out = ramp.outputs["Color"] if ramp is not None else checker.outputs["Color"]
    links.new(color_out, bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
    _materials[key] = mat
    return mat


def _pendant_material(finish):
    if finish == "rattan":
        return _fabric("rattan", (0.62, 0.46, 0.28), rough=0.9, bump=0.6, woven=True)
    if finish == "brass":
        return _metal("pendant_brass", (0.80, 0.60, 0.30), rough=0.3)
    if finish == "white":
        return simple_material("openplan_pendant_white", (0.92, 0.92, 0.90), roughness=0.6)
    return _metal("pendant_black", (0.05, 0.05, 0.05), rough=0.5)


def _pendant(index, x, y, z, w, drop, finish, collection):
    """Suspension (C3) : câble depuis le plafond, abat-jour conique ouvert vers le bas."""
    shade_h = 0.26 if finish != "rattan" else 0.3
    cable = simple_material("openplan_cable", (0.05, 0.05, 0.05), roughness=0.6)
    make_box(
        "decor-%d_cable" % index, (x, y, z + shade_h + (drop - shade_h) / 2), (1.0, 0.0),
        (0.0, 1.0), 0.012, 0.012, max(drop - shade_h, 0.01), cable, collection,
    )
    bpy.ops.mesh.primitive_cone_add(
        vertices=40, radius1=w / 2, radius2=0.05, depth=shade_h,
        end_fill_type="NOTHING", location=(x, y, z + shade_h / 2),
    )
    shade = bpy.context.view_layer.objects.active
    for c in list(shade.users_collection):
        c.objects.unlink(shade)
    collection.objects.link(shade)
    shade.name = "decor-%d" % index
    shade.data.materials.append(_pendant_material(finish))
    modifier = shade.modifiers.new("epaisseur", "SOLIDIFY")
    modifier.thickness = 0.006
    return shade


def sky_background(scene, strength, tint=None):
    """Ciel HDRI installé (D2) comme fond du monde : il entre par les fenêtres et les velux ;
    `tint` (heure dorée, H4) le réchauffe par multiplication."""
    path = _MATERIALS.get("sky")
    if not path or scene.world is None:
        return False
    nodes, links = scene.world.node_tree.nodes, scene.world.node_tree.links
    background = nodes.get("Background")
    if background is None:
        return False
    env = nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(path, check_existing=True)
    if tint:
        warm = nodes.new("ShaderNodeMixRGB")
        warm.blend_type = "MULTIPLY"
        warm.inputs["Fac"].default_value = 1.0
        warm.inputs["Color2"].default_value = (*(float(c) for c in tint), 1.0)
        links.new(env.outputs["Color"], warm.inputs["Color1"])
        links.new(warm.outputs["Color"], background.inputs["Color"])
    else:
        links.new(env.outputs["Color"], background.inputs["Color"])
    background.inputs["Strength"].default_value = strength
    return True


def make_model(prism, collection, warnings):
    """Meuble du catalogue : import du modèle GLB, mis à l'échelle de sa boîte et tourné de son
    lacet. L'import glTF de Blender passe Y-haut en Z-haut et la face avant +Z en −Y ; un demi-
    tour la ramène vers +y (convention du plan), puis le lacet du meuble s'applique."""
    import mathutils

    path = os.path.join(_ASSETS["dir"] or "", prism["model"])
    if not os.path.isfile(path):
        warnings.append("modèle absent pour %s : %s" % (prism["element_id"], prism["model"]))
        return None
    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o.name not in before]
    new_names = [o.name for o in new]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        warnings.append("modèle vide pour %s" % prism["element_id"])
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for name in new_names:
        other = bpy.data.objects.get(name)
        if other is not None and other.name != obj.name:
            bpy.data.objects.remove(other, do_unlink=True)
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    ext = prism["exterior"]
    cx = sum(float(p["x"]) for p in ext) / len(ext)
    cy = sum(float(p["y"]) for p in ext) / len(ext)
    yaw = float(prism.get("yaw", 0.0))
    p0, p1, p3 = ext[0], ext[1], ext[3]
    width = math.hypot(float(p1["x"]) - float(p0["x"]), float(p1["y"]) - float(p0["y"]))
    depth = math.hypot(float(p3["x"]) - float(p0["x"]), float(p3["y"]) - float(p0["y"]))
    if (
        abs(
            (float(p1["x"]) - float(p0["x"])) * math.cos(yaw)
            + (float(p1["y"]) - float(p0["y"])) * math.sin(yaw)
        )
        < 0.5 * width
    ):
        width, depth = depth, width
    height = float(prism["z1"]) - float(prism["z0"])
    size = (
        max(max(xs) - min(xs), 1e-6),
        max(max(ys) - min(ys), 1e-6),
        max(max(zs) - min(zs), 1e-6),
    )
    centre = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, min(zs))
    matrix = (
        mathutils.Matrix.Translation((cx, cy, float(prism["z0"])))
        @ mathutils.Matrix.Rotation(yaw + math.pi, 4, "Z")
        @ mathutils.Matrix.Diagonal((width / size[0], depth / size[1], height / size[2], 1.0))
        @ mathutils.Matrix.Translation((-centre[0], -centre[1], -centre[2]))
    )
    obj.data.transform(matrix)
    obj.matrix_world = mathutils.Matrix.Identity(4)
    if _STYLE["value"] == "realistic":
        rug = "rug" in os.path.basename(prism["model"]).lower()
        for i, mat in enumerate(list(obj.data.materials)):
            if mat is None:
                continue
            better = kenney_material(
                mat.name, rug, prism.get("woods"), prism.get("tints")
            )
            if better is not None:
                obj.data.materials[i] = better
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.name = prism["element_id"]
    obj.data.name = prism["element_id"]
    obj["openplan_id"] = prism["element_id"]
    obj["openplan_kind"] = prism["kind"]
    obj["openplan_source"] = prism["source"]
    return obj


_ART = [(0.55, 0.62, 0.66), (0.74, 0.63, 0.50), (0.60, 0.66, 0.55), (0.70, 0.58, 0.56)]


def make_decor(index, item, collection, warnings):
    """Habillage du rendu (D4) : modèle du kit posé sur un meuble (lampe, plante), ou cadre
    construit (baguette de bois et tirage) ; une lampe de chevet éclaire doucement."""
    x, y, z = float(item["x"]), float(item["y"]), float(item["z"])
    yaw = float(item.get("yaw", 0.0))
    w, d, h = float(item["width"]), float(item["depth"]), float(item["height"])
    ux, uy = math.cos(yaw), math.sin(yaw)
    nx, ny = -math.sin(yaw), math.cos(yaw)
    made = None
    if item.get("model") and _ASSETS["dir"]:
        corners = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
        prism = {
            "element_id": "decor-%d" % index,
            "kind": "fixture",
            "source": "generated",
            "exterior": [{"x": x + ux * a + nx * b, "y": y + uy * a + ny * b} for a, b in corners],
            "z0": z,
            "z1": z + h,
            "model": item["model"],
            "yaw": yaw,
        }
        made = make_model(prism, collection, warnings)
        if made is not None and item.get("color"):
            # Coussins (C3) : la toile du modèle prend la couleur de l'ambiance.
            cloth = _fabric("cushion_%d" % index, tuple(item["color"]), bump=0.3)
            for i in range(len(made.data.materials)):
                made.data.materials[i] = cloth
    elif item.get("kind") == "frame":
        frame_mat = simple_material("openplan_frame", (0.16, 0.11, 0.07), roughness=0.5)
        mat = simple_material("openplan_mat_board", (0.95, 0.94, 0.91), roughness=0.9)
        made = make_box("decor-%d" % index, (x, y, z + h / 2), (ux, uy), (nx, ny), w, d, h, frame_mat, collection)
        make_box(
            "decor-%d_mat" % index,
            (x + nx * 0.004, y + ny * 0.004, z + h / 2),
            (ux, uy), (nx, ny), w - 0.06, d, h - 0.06, mat, collection,
        )
        make_box(
            "decor-%d_art" % index,
            (x + nx * 0.007, y + ny * 0.007, z + h / 2),
            (ux, uy), (nx, ny), w - 0.2, d, h - 0.2, _print_material(index, item), collection,
        )
    elif item.get("kind") == "throw":
        color = tuple(item.get("color") or (0.6, 0.35, 0.25))
        cloth = _fabric("throw_%d" % index, color, rough=0.95, bump=0.25, woven=True)
        drape = item.get("drape")
        if drape:
            # Plaid tissé (H3) : retombe sur les côtés du lit, plis légers, franges aux bords.
            made = _cloth(
                "decor-%d" % index, (x, y, z), (ux, uy), (nx, ny), w, d, float(drape),
                0.012, cloth, collection, seed=index,
            )
            _fringes("decor-%d_fringe" % index, (x, y, z), (ux, uy), (nx, ny), w, d, float(drape), cloth, collection)
        else:
            made = make_box("decor-%d" % index, (x, y, z + h / 2), (ux, uy), (nx, ny), w, d, h, cloth, collection)
    elif item.get("kind") == "duvet":
        color = tuple(item.get("color") or (0.88, 0.85, 0.80))
        linen = _fabric("duvet_%d" % index, color, rough=0.95, bump=0.12)
        made = _cloth(
            "decor-%d" % index, (x, y, z), (ux, uy), (nx, ny), w, d, float(item.get("drape") or w / 2),
            0.012, linen, collection, seed=index, thickness=0.05,
            puff=0.035, bend_m=0.07, head_folds=0.05,
        )
    elif item.get("kind") == "jute_rug":
        color = tuple(item.get("color") or (0.62, 0.50, 0.33))
        woven = item.get("accent") is None
        rug = _fabric("bedside_rug_%d" % index, color, rough=1.0, bump=0.5 if woven else 0.2, woven=woven)
        made = make_box("decor-%d" % index, (x, y, z + h / 2), (ux, uy), (nx, ny), w, d, h, rug, collection)
    elif item.get("kind") == "curtain":
        color = tuple(item.get("color") or (0.93, 0.9, 0.84))
        made = make_box(
            "decor-%d" % index, (x, y, z + h / 2), (ux, uy), (nx, ny), w, d, h,
            _sheer("curtain_%d" % index, color), collection,
        )
    elif item.get("kind") == "pendant":
        made = _pendant(index, x, y, z, w, h, item.get("material") or "white", collection)
    light_w = float(item.get("light_w", 0.0))
    if light_w > 0.0:
        data = bpy.data.lights.new("decor-%d_light" % index, type="POINT")
        data.energy = light_w
        data.color = (1.0, 0.82, 0.6)
        data.shadow_soft_size = 0.08
        lamp = bpy.data.objects.new("decor-%d_light" % index, data)
        lamp.location = (x, y, z + 0.1) if item.get("kind") == "pendant" else (x, y, z + h * 0.7)
        collection.objects.link(lamp)
    return made


def _cloth(
    name, base, u, n, width, depth, half_flat, wrinkle, material, collection,
    seed=0, thickness=0.006, puff=0.0, bend_m=0.03, head_folds=0.0,
):
    """Tissu posé sur un lit (H3) : une grille qui reste à plat sur `half_flat` de part et
    d'autre de l'axe, puis retombe à la verticale le long du lit ; plis doux (bruit) et
    lissage, au lieu d'une plaque rigide."""
    import mathutils

    x0, y0, z0 = base
    nu, nv = 44, 18
    drop = max(0.0, width / 2 - half_flat)
    bm = bmesh.new()
    rows = []
    for j in range(nv + 1):
        b = -depth / 2 + depth * j / nv
        row = []
        for i in range(nu + 1):
            a = -width / 2 + width * i / nu
            side = 1.0 if a >= 0 else -1.0
            excess = abs(a) - half_flat
            if excess <= 0:
                along, z = a, 0.0
                # H5 : la couette gonfle un peu au milieu, retombe en douceur vers les bords.
                z += puff * (1.0 - (a / max(half_flat, 1e-3)) ** 2)
            else:
                bend = min(excess, bend_m)
                theta = bend / bend_m * math.pi / 2
                along = side * (half_flat + bend_m * math.sin(theta))
                # retour au bord : un quart de rond de rayon bend_m avant la chute verticale
                z = -max(0.0, excess - bend_m) - bend_m * (1.0 - math.cos(bend / bend_m * math.pi / 2))
            noise = mathutils.noise.noise(mathutils.Vector((a * 3.0 + seed, b * 3.0, seed * 0.7)))
            # plis plus marqués vers la tête (b < 0 : côté oreillers)
            head = max(0.0, -b / (depth / 2)) ** 2
            folds = head_folds * head * mathutils.noise.noise(
                mathutils.Vector((a * 9.0 + seed, b * 2.0, 1.3))
            )
            z += wrinkle * noise + folds + 0.004
            row.append(bm.verts.new((x0 + u[0] * along + n[0] * b, y0 + u[1] * along + n[1] * b, z0 + z)))
        rows.append(row)
    for j in range(nv):
        for i in range(nu):
            bm.faces.new([rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    solid = obj.modifiers.new("epaisseur", "SOLIDIFY")
    solid.thickness = thickness
    smooth = obj.modifiers.new("lissage", "SUBSURF")
    smooth.levels = 1
    smooth.render_levels = 2
    for poly in mesh.polygons:
        poly.use_smooth = True
    obj.data.materials.append(material)
    collection.objects.link(obj)
    return obj


def _fringes(name, base, u, n, width, depth, half_flat, material, collection):
    """Franges du plaid (H3) : brins de 7 cm sous les deux bords qui retombent."""
    x0, y0, z0 = base
    reach = width / 2
    drop = max(0.0, reach - half_flat - 0.03)
    count = int(depth / 0.018)
    for side in (-1.0, 1.0):
        along = side * (half_flat + 0.035)
        for k in range(count):
            b = -depth / 2 + (k + 0.5) * depth / count
            cx = x0 + u[0] * along + n[0] * b
            cy = y0 + u[1] * along + n[1] * b
            cz = z0 - drop - 0.035
            make_box("%s_%d_%d" % (name, side > 0, k), (cx, cy, cz), u, n, 0.006, 0.006, 0.07, material, collection)


def make_prism(prism, collection, warnings):
    """Face plane à z0 extrudée jusqu'à z1. Les trous ne sont pas gérés (avertissement)."""
    exterior = prism["exterior"]
    if prism.get("holes"):
        warnings.append("trous ignorés pour %s" % prism["element_id"])
    z0, z1 = float(prism["z0"]), float(prism["z1"])
    if z1 <= z0 or len(exterior) < 3:
        return None
    bm = bmesh.new()
    verts = [bm.verts.new((float(p["x"]), float(p["y"]), z0)) for p in exterior]
    try:
        face = bm.faces.new(verts)
    except ValueError:
        warnings.append("contour dégénéré pour %s" % prism["element_id"])
        bm.free()
        return None
    result = bmesh.ops.extrude_face_region(bm, geom=[face])
    top_verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top_verts, vec=(0.0, 0.0, z1 - z0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    name = (
        prism["element_id"]
        if int(prism.get("part", 0)) == 0
        else "%s#%d" % (prism["element_id"], prism["part"])
    )
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material_of(prism))
    obj["openplan_id"] = prism["element_id"]
    obj["openplan_kind"] = prism["kind"]
    obj["openplan_source"] = prism["source"]
    collection.objects.link(obj)
    return obj


def simple_material(name, rgb, roughness=0.5, transmission=0.0):
    """Matière unie Principled (chambranles, vantaux, vitres), créée une fois."""
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        principled.inputs["Roughness"].default_value = roughness
        if transmission > 0 and "Transmission Weight" in principled.inputs:
            principled.inputs["Transmission Weight"].default_value = transmission
            principled.inputs["IOR"].default_value = 1.45
    return mat


def make_box(name, center, u, n, du, dn, dz, material, collection):
    """Pavé centré en `center` (x, y, z), de côtés `du` le long de `u`, `dn` le long de `n`
    (vecteurs 2D unitaires du plan) et `dz` en hauteur."""
    cx, cy, cz = center
    bm = bmesh.new()
    corners = []
    for sz in (-0.5, 0.5):
        for sn in (-0.5, 0.5):
            for su in (-0.5, 0.5):
                x = cx + u[0] * su * du + n[0] * sn * dn
                y = cy + u[1] * su * du + n[1] * sn * dn
                corners.append(bm.verts.new((x, y, cz + sz * dz)))
    bm.verts.ensure_lookup_table()
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    for face in faces:
        bm.faces.new([corners[i] for i in face])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)
    obj["openplan_decor"] = True
    collection.objects.link(obj)
    return obj


SLOPE_UV = "UVMap"


def slope_uv(bm, planks):
    """Pente (V1) : coordonnées de texture (en mètres) dans le plan de la pente, planches dans
    le sens de la pente comme un lambris. La projection boîte mêle deux projections sur un plan
    incliné et dessinait un zigzag sur les planches. `planks` : sens des planches dans l'image
    (« u » largeur, « v » hauteur, None sans importance). Faux si rien d'incliné."""
    sloped = [f for f in bm.faces if 0.1 < abs(f.normal.z) < 0.995]
    if not sloped:
        return False
    n = max(sloped, key=lambda f: f.calc_area()).normal
    if n.z < 0:
        n = -n
    eave = Vector((n.y, -n.x, 0.0)).normalized()
    up = n.cross(eave).normalized()
    layer = bm.loops.layers.uv.new(SLOPE_UV)
    for face in bm.faces:
        for loop in face.loops:
            s, e = loop.vert.co.dot(up), loop.vert.co.dot(eave)
            loop[layer].uv = (e, s) if planks == "v" else (s, e)
    return True


def make_polyhedron(poly, collection, warnings):
    """Solide fermé donné par ses sommets et ses faces triangulaires (murs sous pente, pentes)."""
    vertices = poly["vertices"]
    faces = poly["faces"]
    if len(vertices) < 4 or len(faces) < 4:
        return None
    bm = bmesh.new()
    verts = [bm.verts.new((float(p["x"]), float(p["y"]), float(p["z"]))) for p in vertices]
    bm.verts.ensure_lookup_table()
    made = 0
    for face in faces:
        try:
            bm.faces.new([verts[int(i)] for i in face])
            made += 1
        except ValueError:
            continue  # face doublée : ignorée
    if made == 0:
        warnings.append("polyèdre sans face valide pour %s" % poly["element_id"])
        bm.free()
        return None
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    uv = False
    if poly.get("kind") == "roof":
        spec = _MATERIALS["surfaces"].get(poly.get("finish")) or {}
        uv = slope_uv(bm, spec.get("planks"))
    name = (
        poly["element_id"]
        if int(poly.get("part", 0)) == 0
        else "%s#%d" % (poly["element_id"], poly["part"])
    )
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material_of(poly, uv=uv))
    obj["openplan_id"] = poly["element_id"]
    obj["openplan_kind"] = poly["kind"]
    obj["openplan_source"] = poly["source"]
    collection.objects.link(obj)
    return obj


def add_daylight(scene, light, center, diag, hi, cam, camera_spec, background):
    """Éclairage « heure du jour » : soleil orienté d'après le nord du plan (direction vers le
    soleil dans le repère du plan), ciel teinté, lampes intérieures dosées par le préréglage."""
    sun = light.get("sun")
    if sun:
        sun_data = bpy.data.lights.new("openplan_sun", type="SUN")
        sun_data.energy = float(sun.get("energy", 3.0))
        sun_data.color = tuple(float(c) for c in sun.get("color", (1.0, 1.0, 1.0)))
        sun_data.angle = math.radians(float(sun.get("angle_deg", 1.5)))
        obj = bpy.data.objects.new("openplan_sun", sun_data)
        obj.location = center + Vector((0.0, 0.0, diag))
        towards = Vector(tuple(float(v) for v in sun["direction"]))
        # Une lampe SUN éclaire le long de son −Z local : son +Z doit pointer vers le soleil.
        obj.rotation_euler = towards.to_track_quat("Z", "Y").to_euler()
        scene.collection.objects.link(obj)
    # D3 : exposition d'intérieur (le jour seul, une pièce peu vitrée paraît sombre sinon).
    scene.view_settings.exposure = float(light.get("exposure", 0.0))
    if background is not None:
        sky = light.get("sky_color", (0.62, 0.74, 0.92))
        background.inputs["Color"].default_value = (*(float(c) for c in sky), 1.0)
        background.inputs["Strength"].default_value = float(light.get("sky_strength", 1.0))
    if camera_spec is not None:
        key_data = bpy.data.lights.new("openplan_key", type="POINT")
        key_data.energy = float(light.get("interior_key", 100.0))
        key_data.shadow_soft_size = 0.5
        key = bpy.data.objects.new("openplan_key", key_data)
        key.location = Vector((center.x, center.y, hi.z - 0.35))
        scene.collection.objects.link(key)
        fill_data = bpy.data.lights.new("openplan_fill", type="POINT")
        fill_data.energy = float(light.get("interior_fill", 30.0))
        if light.get("fill_color"):
            fill_data.color = tuple(float(c) for c in light["fill_color"])
        fill = bpy.data.objects.new("openplan_fill", fill_data)
        fill.location = cam.location
        scene.collection.objects.link(fill)


def add_camera_and_light(scene, bounds_min, bounds_max, camera_spec=None, light=None):
    lo = Vector((bounds_min["x"], bounds_min["y"], bounds_min["z"]))
    hi = Vector((bounds_max["x"], bounds_max["y"], bounds_max["z"]))
    center = (lo + hi) / 2.0
    diag = max((hi - lo).length, 1.0)
    cam_data = bpy.data.cameras.new("openplan_camera")
    cam = bpy.data.objects.new("openplan_camera", cam_data)
    if camera_spec is None:
        cam_data.lens = 28.0
        cam.location = center + Vector((1.1, -1.4, 0.9)) * diag
        direction = center - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    else:
        # Caméra imposée : sensor_fit horizontal, focale en pixels → mm sur un capteur de 36 mm.
        # Euler XYZ (π/2 + tangage, 0, lacet − π/2) : regarde +Y puis tourne vers l'azimut voulu.
        cam_data.sensor_fit = "HORIZONTAL"
        cam_data.sensor_width = 36.0
        cam_data.lens = float(camera_spec["focal_px"]) * 36.0 / float(camera_spec["width_px"])
        p = camera_spec["position"]
        cam.location = Vector((float(p["x"]), float(p["y"]), float(p["z"])))
        cam.rotation_mode = "XYZ"
        cam.rotation_euler = (
            math.pi / 2.0 + float(camera_spec["pitch"]),
            0.0,
            float(camera_spec["yaw"]) - math.pi / 2.0,
        )
        roll = float(camera_spec.get("roll", 0.0))
        if roll:
            # Roulis autour de l'axe de visée (Z local de la caméra Blender).
            cam.rotation_euler.rotate_axis("Z", roll)
        scene.render.resolution_x = int(camera_spec["width_px"])
        scene.render.resolution_y = int(camera_spec["height_px"])
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.world = bpy.data.worlds.new("openplan_world")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if light is not None:
        add_daylight(scene, light, center, diag, hi, cam, camera_spec, background)
        tint = light.get("sky_color") if light.get("sky_tint") else None
        sky_background(scene, float(light.get("sky_strength", 1.0)), tint)
    elif camera_spec is None:
        sun_data = bpy.data.lights.new("openplan_sun", type="SUN")
        sun_data.energy = 3.0
        sun = bpy.data.objects.new("openplan_sun", sun_data)
        sun.location = center + Vector((0.0, 0.0, diag))
        sun.rotation_euler = (math.radians(35.0), math.radians(10.0), math.radians(30.0))
        scene.collection.objects.link(sun)
        if background is not None:
            background.inputs["Color"].default_value = (0.9, 0.9, 0.92, 1.0)
            background.inputs["Strength"].default_value = 0.8
    else:
        # Caméra à l'intérieur d'une pièce fermée : éclairage intérieur, ciel presque noir.
        key_data = bpy.data.lights.new("openplan_key", type="POINT")
        key_data.energy = 400.0
        key_data.shadow_soft_size = 0.5
        key = bpy.data.objects.new("openplan_key", key_data)
        key.location = Vector((center.x, center.y, hi.z - 0.35))
        scene.collection.objects.link(key)
        fill_data = bpy.data.lights.new("openplan_fill", type="POINT")
        fill_data.energy = 60.0
        fill = bpy.data.objects.new("openplan_fill", fill_data)
        fill.location = cam.location
        scene.collection.objects.link(fill)
        if background is not None:
            # Ciel clair vu par les ouvertures : une fenêtre rendue ressemble à une fenêtre
            # (lumineuse), pas à un trou noir ; un peu de lumière du jour entre par les baies.
            background.inputs["Color"].default_value = (0.62, 0.74, 0.92, 1.0)
            background.inputs["Strength"].default_value = 1.0
        sky_background(scene, 1.0)


def checker_floor(collection, tile_m=0.4):
    """Fixtures : sol carrelé (deux tons), pour offrir au détecteur les lignes d'un vrai intérieur."""
    mat = bpy.data.materials.new(name="openplan_checker_floor")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    principled = nodes.get("Principled BSDF")
    coords = nodes.new("ShaderNodeTexCoord")
    checker = nodes.new("ShaderNodeTexChecker")
    checker.inputs["Scale"].default_value = 1.0 / tile_m
    checker.inputs["Color1"].default_value = (0.50, 0.54, 0.60, 1.0)
    checker.inputs["Color2"].default_value = (0.16, 0.18, 0.22, 1.0)
    links.new(coords.outputs["Object"], checker.inputs["Vector"])
    if principled is not None:
        links.new(checker.outputs["Color"], principled.inputs["Base Color"])
        principled.inputs["Roughness"].default_value = 0.6
    for obj in collection.objects:
        if obj.get("openplan_kind") == "floor":
            obj.data.materials.clear()
            obj.data.materials.append(mat)


def enable_gpu(scene):
    """Active Cycles sur GPU (OptiX puis CUDA) ; renvoie le type de périphérique retenu."""
    addon = bpy.context.preferences.addons.get("cycles")
    if addon is None:
        return "CPU"
    prefs = addon.preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        try:
            prefs.get_devices()
        except Exception:  # noqa: BLE001 - dépend du pilote
            pass
        gpus = [d for d in prefs.devices if d.type == kind]
        if not gpus:
            continue
        for d in prefs.devices:
            d.use = d.type == kind
        scene.cycles.device = "GPU"
        return kind
    return "CPU"


def enable_depth_output(scene, exr_dir):
    """Passe de profondeur Cycles (distance au plan de la caméra, en mètres) écrite en OpenEXR
    32 bits par le compositeur, à côté de l'aperçu. Vérité terrain pour juger un modèle de
    profondeur (docs/evaluation/protocol.md, critère 2.2)."""
    scene.view_layers[0].use_pass_z = True
    scene.use_nodes = True
    tree = scene.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    layers = tree.nodes.new("CompositorNodeRLayers")
    composite = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.base_path = exr_dir
    output.format.file_format = "OPEN_EXR"
    output.format.color_depth = "32"
    output.format.color_mode = "RGB"
    output.file_slots[0].path = "depth_z_"
    depth_socket = layers.outputs.get("Depth") or layers.outputs.get("Z")
    tree.links.new(depth_socket, output.inputs[0])


def save_depth_npy(exr_dir, npy_path):
    """Relit l'EXR écrit par le compositeur (dans Blender, sans dépendance) et l'écrit en
    .npy (H, W) float32, ligne 0 en haut."""
    import numpy as np

    candidates = sorted(p for p in os.listdir(exr_dir) if p.startswith("depth_z_"))
    if not candidates:
        raise RuntimeError("passe de profondeur non écrite par le compositeur")
    exr = os.path.join(exr_dir, candidates[-1])
    image = bpy.data.images.load(exr)
    width, height = image.size
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, image.channels)
    depth = np.flipud(pixels[..., 0]).copy()
    bpy.data.images.remove(image)
    os.remove(exr)
    np.save(npy_path, depth)
    return float(np.nanmedian(depth[np.isfinite(depth)]))


def render_preview(
    scene, path, size, samples, denoise=False, keep_resolution=False, gpu=False, depth_npy=None
):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    device = enable_gpu(scene) if gpu else "CPU"
    scene.cycles.samples = max(1, samples)
    scene.cycles.use_denoising = denoise
    if not keep_resolution:
        width, height = (int(v) for v in size.lower().split("x"))
        scene.render.resolution_x = width
        scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
    if depth_npy:
        enable_depth_output(scene, os.path.dirname(os.path.abspath(depth_npy)))
    started = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    if depth_npy:
        save_depth_npy(os.path.dirname(os.path.abspath(depth_npy)), depth_npy)
    return device, time.perf_counter() - started


def main():
    args = parse_args()
    started = time.perf_counter()
    report = {
        "blender_version": bpy.app.version_string,
        "objects": 0,
        "warnings": [],
        "artifacts": {},
        "error": None,
        "style": args.style,
        "device": "CPU",
        "render_s": None,
    }
    _STYLE["value"] = args.style
    try:
        with open(args.bake, encoding="utf-8") as fh:
            bake = json.load(fh)
        _PALETTE["value"] = bake.get("palette")
        scene = reset_scene()
        collection = bpy.data.collections.new("OpenPlan %s" % bake.get("level_name", ""))
        scene.collection.children.link(collection)
        _ASSETS["dir"] = args.assets
        if args.materials:
            with open(args.materials, encoding="utf-8") as fh:
                _MATERIALS.update(json.load(fh))
        for prism in bake.get("prisms", []):
            made = None
            if prism.get("model") and args.assets:
                made = make_model(prism, collection, report["warnings"])
            if made is None:
                made = make_prism(prism, collection, report["warnings"])
            if made is not None:
                report["objects"] += 1
        for poly in bake.get("polyhedra", []):
            if make_polyhedron(poly, collection, report["warnings"]) is not None:
                report["objects"] += 1
        for k, item in enumerate(bake.get("decor", [])):
            if make_decor(k, item, collection, report["warnings"]) is not None:
                report["objects"] += 1
            tag_decor(k, item, collection)
        if args.section:
            apply_section(collection, args.section)
        camera_spec = None
        if args.camera:
            with open(args.camera, encoding="utf-8") as fh:
                camera_spec = json.load(fh)
        light = None
        if args.light:
            with open(args.light, encoding="utf-8") as fh:
                light = json.load(fh)
            report["daylight"] = light.get("preset")
        if bake.get("bounds_min") and bake.get("bounds_max"):
            add_camera_and_light(scene, bake["bounds_min"], bake["bounds_max"], camera_spec, light)
        if args.backdrop:
            report["backdrop"] = neutral_backdrop(scene)
        if camera_spec is not None and camera_spec.get("checker_floor", True):
            checker_floor(collection)
        if args.out_blend:
            bpy.ops.wm.save_as_mainfile(filepath=args.out_blend)
            report["artifacts"]["blend"] = args.out_blend
        if args.out_glb:
            bpy.ops.export_scene.gltf(
                filepath=args.out_glb, export_format="GLB", export_apply=True, export_extras=True
            )
            report["artifacts"]["glb"] = args.out_glb
        if args.preview and report["objects"] > 0:
            device, render_s = render_preview(
                scene,
                args.preview,
                args.preview_size,
                args.samples,
                denoise=args.denoise,
                keep_resolution=camera_spec is not None,
                gpu=args.gpu,
                depth_npy=args.depth,
            )
            report["artifacts"]["preview"] = args.preview
            if args.depth:
                report["artifacts"]["depth"] = args.depth
            report["device"] = device
            report["render_s"] = render_s
            if args.idmap:
                # P4 : carte des éléments, après l'aperçu (environ 1 s de plus sur GPU).
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from idmap import render_idmap

                report["idmap_s"] = round(
                    render_idmap(scene, args.idmap, gpu_kind=device if device != "CPU" else None),
                    2,
                )
                report["artifacts"]["idmap"] = os.path.join(args.idmap, "idmap.npz")
    except Exception as exc:  # noqa: BLE001 - tout doit finir dans le rapport
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["traceback"] = traceback.format_exc()
    report["duration_s"] = time.perf_counter() - started
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    if report["error"]:
        print(report["traceback"], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
