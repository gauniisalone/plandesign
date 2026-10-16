"""Carte des éléments d'un rendu (P4) : une passe « identifiant d'objet » Cycles, 1 échantillon
sans filtre, juste après le rendu (environ 1 s sur GPU). Chaque pixel donne l'élément du projet
qui s'y voit ; le décor rattaché à un meuble (linge, lampe de chevet, livres) donne ce meuble,
le décor sans porteur garde son nom (« decor-n »).

Écrit `idmap.npz` (tableau `index`, uint16, H × W, ligne 0 en haut ; 0 = rien) et
`idmap.json` (`ids` : identifiant de l'index 1, 2… ; `size` [W, H] ; `render_s`).

Deux usages :
- importé par build_scene.py (`render_idmap`) après le rendu ;
- seul, pour un rendu plus ancien : `blender -b --python idmap.py -- <scene.blend> <dossier>`.
"""

import json
import os
import sys
import tempfile
import time

import bpy


def element_key(obj):
    """L'élément que désigne un objet : `openplan_id`, sinon son nom (décor sans porteur)."""
    return obj.get("openplan_id") or obj.name


def render_idmap(scene, out_dir, gpu_kind=None):
    import numpy as np

    started = time.perf_counter()
    ids = []
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        key = element_key(obj)
        if key not in ids:
            ids.append(key)
        obj.pass_index = ids.index(key) + 1
    scene.render.engine = "CYCLES"
    if gpu_kind:
        scene.cycles.device = "GPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.filter_width = 0.01
    scene.cycles.max_bounces = 0
    layer = scene.view_layers[0]
    layer.use_pass_object_index = True
    layer.use_pass_z = False
    scene.use_nodes = True
    tree = scene.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    layers = tree.nodes.new("CompositorNodeRLayers")
    composite = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
    tmp = tempfile.mkdtemp(prefix="idmap-")
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.base_path = tmp
    output.format.file_format = "OPEN_EXR"
    output.format.color_depth = "32"
    output.format.color_mode = "RGB"
    output.file_slots[0].path = "index_"
    tree.links.new(layers.outputs["IndexOB"], output.inputs[0])
    scene.render.filepath = os.path.join(tmp, "flat.png")
    bpy.ops.render.render(write_still=False)
    exr = sorted(p for p in os.listdir(tmp) if p.startswith("index_"))
    if not exr:
        raise RuntimeError("passe d'identifiants non écrite par le compositeur")
    image = bpy.data.images.load(os.path.join(tmp, exr[-1]))
    width, height = image.size
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, image.channels)
    index = np.flipud(np.rint(pixels[..., 0])).astype(np.uint16)
    bpy.data.images.remove(image)
    for name in os.listdir(tmp):
        os.remove(os.path.join(tmp, name))
    os.rmdir(tmp)
    os.makedirs(out_dir, exist_ok=True)
    np.savez_compressed(os.path.join(out_dir, "idmap.npz"), index=index)
    seconds = time.perf_counter() - started
    with open(os.path.join(out_dir, "idmap.json"), "w", encoding="utf-8") as fh:
        json.dump({"ids": ids, "size": [width, height], "render_s": round(seconds, 2)}, fh)
    return seconds


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :]
    blend, out_dir = argv[0], argv[1]
    bpy.ops.wm.open_mainfile(filepath=blend)
    render_idmap(bpy.context.scene, out_dir)


if __name__ == "__main__":
    main()
