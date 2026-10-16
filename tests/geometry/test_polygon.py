"""Aires signées, orientation, conversions Shapely."""

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon

from openplan.geom.polygon import (
    ensure_ccw,
    is_ccw,
    largest_polygon,
    signed_area,
    to_points,
    to_polygon,
)
from openplan.schemas import Point2

SQUARE = [Point2(x=0, y=0), Point2(x=2, y=0), Point2(x=2, y=2), Point2(x=0, y=2)]


def test_signed_area_is_positive_for_ccw() -> None:
    assert signed_area(SQUARE) == 4.0
    assert signed_area(list(reversed(SQUARE))) == -4.0
    assert signed_area(SQUARE[:2]) == 0.0


def test_ensure_ccw_reverses_clockwise_input() -> None:
    assert not is_ccw(list(reversed(SQUARE)))
    assert ensure_ccw(list(reversed(SQUARE))) == SQUARE
    assert ensure_ccw(SQUARE) == SQUARE


def test_to_polygon_orients_and_round_trips() -> None:
    poly = to_polygon(list(reversed(SQUARE)))
    assert poly.area == 4.0
    assert poly.exterior.is_ccw
    assert signed_area(to_points(poly)) == 4.0


def test_to_polygon_needs_three_points() -> None:
    with pytest.raises(ValueError, match="trois"):
        to_polygon(SQUARE[:2])


def test_largest_polygon_picks_biggest_part() -> None:
    small = Polygon([(0, 0), (1, 0), (1, 1)])
    big = Polygon([(5, 5), (9, 5), (9, 9), (5, 9)])
    assert largest_polygon(MultiPolygon([small, big])).area == 16.0
    assert largest_polygon(big) is big
    assert largest_polygon(Point(0, 0)).is_empty
