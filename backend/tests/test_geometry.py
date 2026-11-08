import pytest

from civicwatch.geometry import (
    ZoneMask,
    box_iou,
    point_in_polygon,
    polygon_area,
    validate_polygon,
)

SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]


def test_validate_polygon_rejects_degenerate_shapes():
    with pytest.raises(ValueError):
        validate_polygon([[0.1, 0.1], [0.2, 0.2]])
    with pytest.raises(ValueError):
        validate_polygon([[0.1, 0.1], [0.1, 0.1], [0.1, 0.1]])


def test_validate_polygon_clamps_and_deduplicates():
    polygon = validate_polygon([[-0.5, 0.2], [-0.5, 0.2], [0.8, 0.2], [1.4, 0.9], [0.2, 0.9]])
    assert polygon[0] == [0.0, 0.2]
    assert polygon[2] == [1.0, 0.9]
    assert len(polygon) == 4  # duplicate consecutive vertex removed


def test_polygon_area_and_point_inside():
    assert polygon_area(SQUARE) == pytest.approx(0.36)
    assert point_in_polygon((0.5, 0.5), SQUARE)
    assert not point_in_polygon((0.1, 0.5), SQUARE)


def test_zone_mask_coverage_measures_overlap():
    mask = ZoneMask.build(SQUARE, width=100, height=100)
    inside = mask.coverage((300, 300, 400, 400), 1000, 1000)  # fully inside the square
    outside = mask.coverage((0, 0, 100, 100), 1000, 1000)
    partial = mask.coverage((150, 150, 350, 350), 1000, 1000)
    assert inside > 0.95
    assert outside == 0.0
    assert 0.2 < partial < 0.9


def test_box_iou():
    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert box_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert box_iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)
