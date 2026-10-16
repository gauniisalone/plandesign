"""D3 : la lumière du jour prime sur les lampes, l'exposition suit le vitrage, et la vue
d'architecte cadre la pièce de face, au centre."""

from __future__ import annotations

import math

import pytest

from openplan.pipeline.render import (
    ARCHITECT_NAME,
    architect_view,
    balanced_light,
    daylight_apertures,
    daylight_spec,
    default_light,
)
from openplan.scene.ops import AddRoofWindow, apply_op
from openplan.schemas.elements import Camera, RoofPlane
from tests.factories import attic_project, only_level, rect_room_project


def _attic_with_skylights():  # type: ignore[no-untyped-def]
    project = attic_project()
    level_id = only_level(project).id
    for roof in [e for e in only_level(project).elements.values() if isinstance(e, RoofPlane)]:
        project = apply_op(project, level_id, AddRoofWindow(roof=roof.id, offset=1.0, distance=0.8))
    return project


def test_daylight_wins_over_lamps_and_exposure_follows_glazing() -> None:
    project = _attic_with_skylights()
    level = only_level(project)
    assert daylight_apertures(level) == pytest.approx(2 * 1.5 * 0.78 * 0.98)
    noon = daylight_spec("midi", level.north_deg)
    light = balanced_light(noon, level)
    assert light["balanced"] and light["interior_key"] == 0.0
    assert 1.0 < light["exposure"] <= 2.5, light
    night = balanced_light(daylight_spec("nuit", level.north_deg), level)
    assert not night["balanced"] and night["exposure"] == 0.0
    # Sans heure demandée : le jour si la pièce a des ouvertures, l'atelier sinon.
    assert default_light(level, None, level.north_deg)["balanced"]  # type: ignore[index]
    blind = only_level(attic_project())
    assert daylight_apertures(blind) == 0.0 and default_light(blind, None, 90.0) is None


def test_more_glazing_means_less_exposure() -> None:
    small = only_level(_attic_with_skylights())
    rect = only_level(rect_room_project())  # une fenêtre de 1,20 × 1,25 pour 11 m²
    e_small = balanced_light(daylight_spec("midi", 90.0), small)["exposure"]
    e_rect = balanced_light(daylight_spec("midi", 90.0), rect)["exposure"]
    assert e_rect < e_small


def test_architect_view_is_frontal_centred_and_wide() -> None:
    project = attic_project()  # pièce 6 × 5, longueur le long de x
    level_id = only_level(project).id
    op = architect_view(project, level_id)
    assert op.name == ARCHITECT_NAME and op.pitch > 0, "un peu relevée sous la toiture"
    assert op.position.y == pytest.approx(2.5, abs=0.2), "dans l'axe de la pièce"
    assert abs(math.cos(op.yaw)) == pytest.approx(1.0, abs=1e-3), "regarde le long de la longueur"
    assert op.focal_px == pytest.approx(1280 / 2 / math.tan(math.radians(40)), rel=1e-3)
    after = apply_op(project, level_id, op)
    camera = next(
        e
        for e in only_level(after).elements.values()
        if isinstance(e, Camera) and e.name == ARCHITECT_NAME
    )
    assert camera.pitch == op.pitch and camera.focal_px == op.focal_px
    # Pièce avec porte (mur du bas, à gauche) : on entre par le côté de la porte.
    rect = rect_room_project()
    rop = architect_view(rect, only_level(rect).id)
    assert rop.position.x < 2.0 and math.cos(rop.yaw) > 0.99 and rop.pitch == 0.0


def test_architect_view_stays_inside_an_l_shaped_room() -> None:
    from shapely.geometry import Point

    from openplan.geom.wallgraph import WallGraph
    from openplan.schemas.elements import Room
    from tests.factories import l_room_project

    project = l_room_project()
    level = only_level(project)
    op = architect_view(project, level.id)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    polygon = WallGraph.from_level(level).room_polygon(room.id)
    assert polygon.buffer(-0.29).contains(Point(op.position.x, op.position.y))


def test_exposure_correction_moves_towards_a_bright_interior() -> None:
    from openplan.pipeline.render import TARGET_MEAN, exposure_correction

    assert exposure_correction(TARGET_MEAN) == pytest.approx(0.0)
    assert exposure_correction(TARGET_MEAN / 2) == pytest.approx(2.2)
    assert exposure_correction(TARGET_MEAN * 2) == pytest.approx(-2.0)
    assert exposure_correction(1.0) == 4.0, "borné : une image noire n'explose pas"


def test_auto_exposure_uses_a_tiny_probe(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    from openplan.pipeline import render

    seen = {}

    def fake_blender(project, level_id, out, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        out.mkdir(parents=True, exist_ok=True)
        Image.new("L", (kw["camera"].width_px, kw["camera"].height_px), 55).save(
            out / "preview.png"
        )

    monkeypatch.setattr(render, "run_blender", fake_blender)
    project = _attic_with_skylights()
    level = only_level(project)
    _, cam = render.photo_camera(project, level.id)
    light = render.balanced_light(daylight_spec("midi", 90.0), level)
    exposed = render.auto_exposed(project, level.id, cam, light, tmp_path)
    assert (
        seen["camera"].width_px == render.PROBE_WIDTH_PX and seen["samples"] == render.PROBE_SAMPLES
    )
    assert exposed["exposure"] == pytest.approx(min(5.0, light["exposure"] + 2.2), abs=0.02)
    night = render.balanced_light(daylight_spec("nuit", 90.0), level)
    assert render.auto_exposed(project, level.id, cam, night, tmp_path) is night


def test_attic_architect_view_follows_the_ridge_under_full_headroom() -> None:
    project = attic_project()  # égouts le long de x, faîtage à y = 2,5
    op = architect_view(project, only_level(project).id)
    assert abs(math.cos(op.yaw)) == pytest.approx(1.0, abs=1e-3)
    assert op.position.y == pytest.approx(2.5, abs=0.15)
