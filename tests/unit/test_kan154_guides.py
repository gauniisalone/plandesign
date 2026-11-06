"""KAN-154 (étape 1) : les guides d'une vue gardent la profondeur exacte, l'élément et sa classe
par pixel (masques murs, ouvertures, poteaux), à la taille du rendu, et se relisent."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openplan.pipeline.guides import OPENING_KINDS, load_guides, scene_mesh, view_guides
from openplan.pipeline.scene3d import bake_level
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import only_level, rect_room_project

DEPTH_TOLERANCE = 0.005
"""Profondeur exacte contre la géométrie connue : ±0,5 % (la rastérisation interpole 1/z)."""


def _bake():  # type: ignore[no-untyped-def]
    project = rect_room_project()
    return bake_level(project, only_level(project).id, "realistic")


def _facing_east(width: int = 320, height: int = 240) -> RenderCamera:
    """Au milieu de la pièce (4 × 3 m), face au mur x = 4, regard horizontal."""
    return RenderCamera(
        position=Point3(x=1.0, y=1.5, z=1.2),
        yaw=0.0,
        pitch=0.0,
        focal_px=0.8 * width,
        width_px=width,
        height_px=height,
    )


def test_the_depth_is_the_exact_distance_to_the_wall() -> None:
    bake = _bake()
    guides = view_guides(bake, _facing_east(), (320, 240))
    assert guides.depth.shape == (240, 320) and guides.depth.dtype == np.float32
    # Face intérieure du mur x = 4 (épaisseur 0,15 m, axe sur le sommet) : x = 3,925 ; la
    # caméra est en x = 1 : 2,925 m le long de l'axe.
    centre = float(guides.depth[120, 160])
    assert centre == pytest.approx(2.925, rel=DEPTH_TOLERANCE)
    assert guides.kinds[guides.element[120, 160]] == "wall"
    assert guides.mask({"wall"})[120, 160]


def test_every_element_has_its_class_and_openings_are_masked() -> None:
    bake = _bake()
    mesh = scene_mesh(bake)
    assert len(mesh.kinds) == len(mesh.labels)
    by_label = dict(zip(mesh.labels, mesh.kinds, strict=True))
    openings = {str(o.element_id): o.kind for o in bake.openings}
    assert openings and all(by_label[i] == k for i, k in openings.items())
    assert {"wall", "floor"} <= set(mesh.kinds)
    # Une vue qui voit toute la pièce voit ses ouvertures : leur masque n'est pas vide.
    wide = RenderCamera(
        position=Point3(x=0.3, y=0.3, z=1.5),
        yaw=0.64,
        pitch=-0.2,
        focal_px=0.5 * 320,
        width_px=320,
        height_px=240,
    )
    guides = view_guides(bake, wide, (320, 240))
    assert guides.mask(OPENING_KINDS).any()
    assert not (guides.mask(OPENING_KINDS) & guides.mask({"wall"})).any()


def test_the_guides_are_kept_at_the_render_size_and_read_back(tmp_path: Path) -> None:
    bake = _bake()
    guides = view_guides(bake, _facing_east(1280, 960), (1024, 768))
    assert guides.depth.shape == guides.element.shape == guides.edges.shape == (768, 1024)
    guides.save(tmp_path / "r.guides.npz")
    back = load_guides(tmp_path / "r.guides.npz")
    assert np.array_equal(back.depth, guides.depth)
    assert np.array_equal(back.element, guides.element)
    assert back.labels == guides.labels and back.kinds == guides.kinds
