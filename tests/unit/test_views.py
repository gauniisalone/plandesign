"""V1 (note 10, point 4) : trois vues automatiques (ensemble, zone, détail), format 4:5,
« rendre toutes les vues » ; caméra de vignette sous un égout bas ; garde-corps et planches
des pentes selon le style."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Point, Polygon

from openplan.adapters.materials import load_materials
from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.layouts import (
    ZONE_KEY,
    LayoutBody,
    ZoneBriefBody,
    apply_layout,
    brief_from_intent,
    generate_layouts,
)
from openplan.pipeline.render import _room_shape, camera_view, overview_camera
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.views import (
    AUTO_KEY,
    DETAIL_FOCAL_MM,
    ZONE_FOCAL_MM,
    ZONE_NAMES,
    _footprint,
    _head,
    apply_auto_views,
    focal_px,
    views_to_render,
)
from openplan.scene.ops import (
    AddCamera,
    AddFurniture,
    AddOpening,
    AddStairs,
    SetIntent,
    UpdateElement,
    apply_op,
)
from openplan.schemas.bake import Prism
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Camera, Furniture, OpeningKind, Room, Wall
from openplan.schemas.scene import Project
from openplan.validation.layout import clearance_at
from tests.factories import attic_project, only_level


def _axes(project: Project) -> tuple[tuple[float, float], tuple[float, float]]:
    """Centre de la pièce et direction du faîtage."""
    level = only_level(project)
    polygon, _ = _room_shape(level)
    assert polygon is not None
    roof = next(iter(derived_roofs(level, WallGraph.from_level(level)).values()))
    a, b = roof.eave
    n = math.hypot(b.x - a.x, b.y - a.y)
    return (polygon.centroid.x, polygon.centroid.y), ((b.x - a.x) / n, (b.y - a.y) / n)


def _furnished() -> Project:
    """Combles 6 × 5 m : un lit tête vers un pignon avec son chevet, un canapé vers l'autre."""
    project = attic_project(camera=False)
    level = only_level(project)
    (cx, cy), (ux, uy) = _axes(project)
    bed_yaw = math.atan2(ux, -uy)  # face avant vers le centre
    lateral = (math.cos(bed_yaw), math.sin(bed_yaw))
    ops = [
        AddFurniture(
            asset_id="bed_double_160",
            position=Point2(x=cx + ux * 1.6, y=cy + uy * 1.6),
            yaw=bed_yaw,
        ),
        AddFurniture(
            asset_id="nightstand",
            position=Point2(
                x=cx + ux * 2.3 + lateral[0] * 1.15, y=cy + uy * 2.3 + lateral[1] * 1.15
            ),
            yaw=bed_yaw,
        ),
        AddFurniture(
            asset_id="sofa_2",
            position=Point2(x=cx - ux * 2.2, y=cy - uy * 2.2),
            yaw=math.atan2(-ux, uy),
        ),
        AddFurniture(asset_id="coffee_table", position=Point2(x=cx - ux * 1.2, y=cy - uy * 1.2)),
    ]
    for op in ops:
        project = apply_op(project, level.id, op)
    return project


def _auto(project: Project) -> list[Camera]:
    return [
        e
        for e in only_level(project).elements.values()
        if isinstance(e, Camera) and AUTO_KEY in e.metadata
    ]


def test_three_views_are_framed_inside_the_room_under_the_slopes() -> None:
    project = _furnished()
    level_id = only_level(project).id
    after, report = apply_auto_views(project, level_id, actor="user")
    cams = {c.metadata[AUTO_KEY]: c for c in _auto(after)}
    assert set(cams) == {"overall", "zone", "detail"}, report
    assert len(report.added) == 3 and not report.skipped
    level = only_level(after)
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    polygon, _ = _room_shape(level)
    assert polygon is not None
    for cam in cams.values():
        p = (cam.position.x, cam.position.y)
        assert polygon.buffer(-0.25).contains(Point(p))
        assert cam.position.z <= clearance_at(p, roofs, level) - 0.25 + 1e-6, cam.name
        assert cam.source is Source.GENERATED
    zone, detail = cams["zone"], cams["detail"]
    assert zone.name == "Vue de zone : détente"
    assert zone.focal_px == pytest.approx(focal_px(ZONE_FOCAL_MM), abs=0.1)
    assert detail.name == "Détail : lit et chevet"
    assert detail.focal_px == pytest.approx(focal_px(DETAIL_FOCAL_MM), abs=0.1)
    assert detail.pitch < 0 and zone.pitch < 0, "on regarde les meubles, vers le bas"
    # Le détail regarde vers le lit, à 2 m environ.
    bed = next(
        e
        for e in level.elements.values()
        if isinstance(e, Furniture) and e.category == "bed_double"
    )
    d = math.hypot(
        bed.transform.position.x - detail.position.x, bed.transform.position.y - detail.position.y
    )
    assert 1.0 < d < 3.2
    # Sans escalier ni porte : la vue d'ensemble est la vue d'architecte, dans l'axe.
    assert "axe" in str(cams["overall"].metadata["note"])


def test_the_overall_view_starts_at_the_door() -> None:
    project = _furnished()
    level = only_level(project)
    graph = WallGraph.from_level(level)
    gable = next(
        e for e in level.elements.values() if isinstance(e, Wall) and "Pignon" in (e.name or "")
    )
    length = graph.wall_length(gable.id)
    project = apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=gable.id,
            kind=OpeningKind.DOOR,
            offset=length / 2 - 0.45,
            width=0.9,
            height=2.0,
        ),
    )
    after, _ = apply_auto_views(project, level.id, actor="user")
    overall = next(c for c in _auto(after) if c.metadata[AUTO_KEY] == "overall")
    assert "porte" in str(overall.metadata["note"])
    a, b = graph.wall_axis(gable.id)
    door = ((a.x + b.x) / 2, (a.y + b.y) / 2)
    assert math.hypot(overall.position.x - door[0], overall.position.y - door[1]) < 2.8


def test_rerun_replaces_automatic_views_but_keeps_the_one_moved_by_hand() -> None:
    project = _furnished()
    level_id = only_level(project).id
    project, _ = apply_auto_views(project, level_id, actor="user")
    zone = next(c for c in _auto(project) if c.metadata[AUTO_KEY] == "zone")
    moved = zone.position.model_copy(update={"x": zone.position.x + 0.2})
    project = apply_op(
        project,
        level_id,
        UpdateElement(element_id=zone.id, fields={"position": moved.model_dump()}),
    )
    assert only_level(project).elements[zone.id].source is Source.USER_DEFINED
    again, report = apply_auto_views(project, level_id, actor="user")
    cams = _auto(again)
    assert len(cams) == 3, "pas de doublon"
    assert zone.id in {c.id for c in cams}, "la vue modifiée à la main reste"
    assert report.kept and len(report.added) == 2
    # Annulable d'un geste : l'historique a un pas « Vues automatiques ».
    assert again.history[-1].label.startswith("Vues automatiques")


def test_portrait_format_goes_into_the_intent_and_the_render_size() -> None:
    project = _furnished()
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    project = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "naturel"}))
    after, report = apply_auto_views(project, level.id, actor="user", aspect="4:5")
    intent = only_level(after).elements[room.id].intent  # type: ignore[union-attr]
    assert intent is not None and intent.views.aspect == "4:5"
    assert intent.chosen_by_user("views")
    assert report.aspect == "4:5"
    cam = _auto(after)[0]
    _, spec = camera_view(after, level.id, cam.id)
    assert (spec.width_px, spec.height_px) == (1024, 1280)


def test_portrait_without_intent_does_not_create_one() -> None:
    project = _furnished()
    level = only_level(project)
    after, _ = apply_auto_views(project, level.id, actor="user", aspect="4:5")
    room = next(e for e in only_level(after).elements.values() if isinstance(e, Room))
    assert room.intent is None, "les matières d'avant V1 restent"
    assert all(c.metadata["aspect"] == "4:5" for c in _auto(after))


def test_render_all_takes_automatic_views_first_and_no_photo_camera() -> None:
    project = attic_project(camera=True)
    level_id = only_level(project).id
    project = apply_op(project, level_id, AddCamera(position=Point2(x=1.0, y=1.0), name="Mienne"))
    project, _ = apply_auto_views(_with_bed(project), level_id, actor="user")
    names = [c.name for c in views_to_render(only_level(project))]
    assert names[0] == "Vue d'ensemble"
    assert names[-1] == "Mienne"
    assert all(c.image_id is None for c in views_to_render(only_level(project)))


def _with_bed(project: Project) -> Project:
    (cx, cy), (ux, uy) = _axes(project)
    return apply_op(
        project,
        only_level(project).id,
        AddFurniture(
            asset_id="bed_double_160", position=Point2(x=cx, y=cy), yaw=math.atan2(ux, -uy)
        ),
    )


def test_variant_thumbnail_camera_stays_under_a_low_eave() -> None:
    project = _furnished()
    level = only_level(project)
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture)]
    cam = overview_camera(level, furniture)
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    room = clearance_at((cam.position.x, cam.position.y), roofs, level)
    assert cam.position.z <= room - 0.3 + 1e-6, "sous la pente, pas au-dessus du toit"


def test_the_railing_follows_the_style() -> None:
    project = attic_project(camera=False)
    level = only_level(project)
    (cx, cy), (ux, uy) = _axes(project)
    project = apply_op(
        project,
        level.id,
        AddStairs(position=Point2(x=cx, y=cy), yaw=math.atan2(uy, ux)),
    )

    def rails(p: Project) -> set[str]:
        bake = bake_level(p, level.id, style="realistic")
        stairs = [x for x in bake.prisms if x.kind == "stairs"]
        return {x.finish for x in stairs if x.z1 - x.z0 < 0.06 and x.z0 > level.elevation}

    assert rails(project) == {"timber"}, "sans ambiance : comme avant"
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    scandi = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "scandinave"}))
    assert rails(scandi) == {"painted_wood"}
    indus = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "industriel"}))
    assert rails(indus) == {"metal"}


def test_the_detail_frames_the_whole_bed_head_and_the_nightstand() -> None:
    for aspect, half_px in (("4:3", 640.0), ("4:5", 512.0)):
        project = _furnished()
        level_id = only_level(project).id
        after, _ = apply_auto_views(project, level_id, actor="user", aspect=aspect)
        level = only_level(after)
        cam = next(c for c in _auto(after) if c.metadata[AUTO_KEY] == "detail")
        bed = next(
            e
            for e in level.elements.values()
            if isinstance(e, Furniture) and e.category == "bed_double"
        )
        stand = next(
            e
            for e in level.elements.values()
            if isinstance(e, Furniture) and e.category == "nightstand"
        )
        half = math.atan(half_px / (cam.focal_px or 1.0))
        corners = [*_head(bed).exterior.coords, *_footprint(stand).exterior.coords]
        for x, y in corners:
            angle = math.atan2(y - cam.position.y, x - cam.position.x) - cam.yaw
            angle = math.atan2(math.sin(angle), math.cos(angle))
            assert abs(angle) <= half + 1e-6, f"{aspect} : tête du lit coupée"


def test_layout_writes_the_zone_into_placed_furniture_and_the_zone_view_reads_it() -> None:
    project = attic_project(camera=False)
    level_id = only_level(project).id
    run = generate_layouts(
        project,
        level_id,
        LayoutBody(
            brief=brief_from_intent(project, level_id, ZoneBriefBody()), variants=1, budget_s=10
        ),
    )
    furniture = run.proposals[0].furniture
    zones = {f.metadata.get(ZONE_KEY) for f in furniture}
    assert zones - {None}, "les meubles du brief par zones gardent leur zone"
    project = apply_layout(project, level_id, run.proposals[0])
    after, _ = apply_auto_views(project, level_id, actor="user")
    zone = next((c for c in _auto(after) if c.metadata[AUTO_KEY] == "zone"), None)
    if zone is not None:
        label = str(zone.name).split(" : ")[1]
        assert label in {ZONE_NAMES[z] for z in zones if z in ZONE_NAMES}


def test_door_leaves_follow_the_style_and_double_leaves_leave_no_gap() -> None:
    project = attic_project(camera=False)
    level = only_level(project)
    graph = WallGraph.from_level(level)
    gable = next(
        e for e in level.elements.values() if isinstance(e, Wall) and "Pignon" in (e.name or "")
    )
    length = graph.wall_length(gable.id)
    project = apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=gable.id,
            kind=OpeningKind.DOUBLE_DOOR,
            offset=length / 2 - 0.7,
            width=1.4,
            height=2.0,
        ),
    )

    def leaves(p: Project) -> list[Prism]:
        bake = bake_level(p, level.id, style="realistic")
        return [
            x
            for x in bake.prisms
            if x.kind == "joinery" and x.finish not in ("plaster", "glass", "metal")
        ]

    sills = [
        x
        for x in bake_level(project, level.id, style="realistic").prisms
        if x.kind == "joinery" and x.z1 <= level.elevation + 1e-6
    ]
    assert sills, "un seuil sous la porte : pas de vide (ciel) au pied du vantail"
    before = [x for x in leaves(project) if x.z1 > level.elevation + 1e-6]
    assert {x.finish for x in before} == {"wood_dark"}, "sans ambiance : comme avant"
    a, b = (Polygon([(q.x, q.y) for q in x.exterior]) for x in before)
    assert a.intersects(b), "battement : pas de jour entre les deux vantaux"
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    scandi = apply_op(project, level.id, SetIntent(room_id=room.id, fields={"style": "scandinave"}))
    assert {x.finish for x in leaves(scandi) if x.z1 > level.elevation + 1e-6} == {"painted_wood"}


def test_plank_textures_say_which_way_their_planks_run() -> None:
    by_finish = {m.finish: m for m in load_materials().materials if m.kind == "surface"}
    assert by_finish["painted_wood"].planks == "v"
    assert by_finish["wood_boards"].planks == "u"
    assert by_finish["limewash"].planks is None
    assert by_finish["oak_dark"].planks == "u"
