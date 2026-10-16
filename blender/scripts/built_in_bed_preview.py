"""Vignette de bibliothèque du lit encastré (H1b) : les morceaux du lit, vue isométrique, fond
transparent, 256 × 256, dans l'esprit des vignettes Kenney. Usage :
blender -b --python thumb_blender.py -- parts.json sortie.png"""

import json
import math
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1 :]
parts_path, out_path = argv[0], argv[1]
boxes = json.load(open(parts_path))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 64
scene.cycles.device = "CPU"
scene.render.resolution_x = scene.render.resolution_y = 256
scene.render.film_transparent = True
scene.view_settings.view_transform = "Standard"


def material(name, color, rough):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    return mat


wood = material("wood", (0.55, 0.38, 0.22), 0.5)
dark = material("dark", (0.36, 0.24, 0.14), 0.5)
linen = material("linen", (0.86, 0.83, 0.77), 0.95)
pillow = material("pillow", (0.93, 0.92, 0.89), 0.95)
for b in boxes:
    sx, sy, sz = b["u1"] - b["u0"], b["v1"] - b["v0"], b["z1"] - b["z0"]
    bpy.ops.mesh.primitive_cube_add(
        size=1, location=((b["u0"] + b["u1"]) / 2, (b["v0"] + b["v1"]) / 2, (b["z0"] + b["z1"]) / 2)
    )
    obj = bpy.context.active_object
    obj.scale = (sx, sy, sz)
    bevel = obj.modifiers.new("bevel", "BEVEL")
    bevel.width = 0.012 if b["part"] != "mattress" else 0.04
    bevel.segments = 3
    obj.data.materials.append(
        {"mattress": linen, "drawer": dark}.get(b["part"], wood)
    )
# deux oreillers contre la planche de tête
for u in (-0.33, 0.33):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(u, -0.72, 0.62))
    obj = bpy.context.active_object
    obj.scale = (0.55, 0.32, 0.13)
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = 0.05
    mod.segments = 4
    obj.data.materials.append(pillow)

cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 3.1
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
cam.location = (3.2, 2.6, 2.4)
direction = (-3.2, -2.6, -2.0)
pitch = math.atan2(-direction[2], math.hypot(direction[0], direction[1]))
cam.rotation_euler = (math.pi / 2 - pitch, 0.0, math.atan2(direction[1], direction[0]) - math.pi / 2)
scene.camera = cam
sun = bpy.data.lights.new("sun", "SUN")
sun.energy = 3.5
sun_obj = bpy.data.objects.new("sun", sun)
sun_obj.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun_obj)
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
scene.world = world
scene.render.filepath = out_path
bpy.ops.render.render(write_still=True)
