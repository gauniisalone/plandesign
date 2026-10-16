"""C1 : charpente apparente sous les pentes (chevrons, pannes, faîtière), réglable."""

from __future__ import annotations

from openplan.pipeline.scene3d import RAFTER, bake_level, timber_rects, timber_settings
from openplan.scene.ops import AddRoofWindow, UpdateElement, apply_op
from openplan.schemas.elements import RoofPlane
from tests.factories import ATTIC, attic_project, only_level


def _timber(project):  # type: ignore[no-untyped-def]
    level = only_level(project)
    return [
        p for p in bake_level(project, level.id, style="realistic").polyhedra if p.kind == "timber"
    ]


def test_rafters_every_60_cm_with_purlins_and_ridge() -> None:
    rects = timber_rects(6.0, 3.0, 0.6, [])
    rafters = [r for r, t0, _ in rects if t0 == -RAFTER[1]]
    assert len(rafters) == 10, "6 m d'égout, entraxe 60 cm"
    widths = {round(r[1] - r[0], 3) for r in rafters}
    assert widths == {RAFTER[0]}
    assert len(rects) == 10 + 1, "une panne pour 3 m de pente"


def test_rafters_and_purlins_stop_at_a_roof_window() -> None:
    hole = (2.0, 2.78, 1.0, 1.98)
    rects = timber_rects(6.0, 3.0, 0.6, [hole])
    for (u0, u1, v0, v1), _t0, _t1 in rects:
        inside = u0 < hole[1] and u1 > hole[0] and v0 < hole[3] and v1 > hole[2]
        assert not inside, "aucune pièce de charpente à travers le velux"


def test_attic_has_timber_and_the_roof_sheet_can_hide_it() -> None:
    project = attic_project()
    timber = _timber(project)
    assert len(timber) >= 2 * 10
    ridges = [p for p in timber if p.part == 1999]
    assert len(ridges) == 2 and all(p.finish == "timber" for p in timber)
    # Les deux moitiés de faîtière se touchent sous le faîtage : pas de fente.
    xs = [[v.x for v in p.vertices] for p in ridges]
    ys = [[v.y for v in p.vertices] for p in ridges]
    spans = [(min(c), max(c)) for c in (xs if max(xs[0]) - min(xs[0]) < 1.0 else ys)]
    assert abs(spans[0][1] - spans[1][0]) < 1e-6 or abs(spans[1][1] - spans[0][0]) < 1e-6
    assert all(v.z < ATTIC["ridge_height"] for p in ridges for v in p.vertices)
    level = only_level(project)
    roofs = [e for e in level.elements.values() if isinstance(e, RoofPlane)]
    for roof in roofs:
        project = apply_op(
            project,
            level.id,
            UpdateElement(element_id=roof.id, fields={"metadata": {"timber": {"enabled": False}}}),
        )
    assert _timber(project) == []
    assert timber_settings(
        roofs[0].model_copy(update={"metadata": {"timber": {"spacing": 9}}})
    ) == (True, 1.2)


def test_denser_rafters_and_skylights_change_the_count() -> None:
    project = attic_project()
    level = only_level(project)
    base = len(_timber(project))
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    dense = apply_op(
        project,
        level.id,
        UpdateElement(element_id=roof.id, fields={"metadata": {"timber": {"spacing": 0.45}}}),
    )
    assert len(_timber(dense)) > base
    pierced = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=2.0, distance=1.0))
    assert len(_timber(pierced)) > base, "chevrons coupés en deux autour du velux"
