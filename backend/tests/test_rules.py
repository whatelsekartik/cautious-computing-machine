"""Zone rule engine tests: dwell timing, categories, de-duplication."""

from dataclasses import dataclass

from civicwatch.detect.base import Detection
from civicwatch.pipeline.rules import RuleEngine, ZoneSpec

ZONE_POLYGON = [[0.6, 0.05], [0.99, 0.05], [0.99, 0.5], [0.6, 0.5]]
FOOTPATH_POLYGON = [[0.05, 0.55], [0.95, 0.55], [0.95, 0.95], [0.05, 0.95]]


@dataclass
class Track:
    track_id: int
    box: tuple[float, float, float, float]
    score: float
    class_id: int
    class_name: str
    age: int = 30
    hits: int = 30
    time_since_update: int = 0
    is_new: bool = False


def make_zone(kind: str = "no_parking", rules: dict | None = None, polygon=None) -> ZoneSpec:
    return ZoneSpec(
        id="zone-1",
        name="Test zone",
        kind=kind,
        polygon=polygon or ZONE_POLYGON,
        rules=rules if rules is not None else {"dwell_seconds": 6, "classes": ["car", "truck"]},
    )


def test_stationary_vehicle_raises_candidate_after_dwell():
    engine = RuleEngine([make_zone()], {"incident_cooldown_minutes": 10})
    car = Track(7, (620, 160, 720, 240), 0.82, 2, "car")
    fired_at = None
    for step in range(1, 12):
        now = step * 1.2
        violations = engine.evaluate([car], now, 1000, 600)
        if violations and fired_at is None:
            fired_at = now
            assert violations[0].category == "illegal_parking"
            assert violations[0].dwell_seconds >= 6.0 - 1e-6  # float accumulation
            assert violations[0].track_id == 7
    assert fired_at is not None
    assert fired_at >= 6.0


def test_moving_vehicle_is_not_flagged():
    engine = RuleEngine([make_zone()], {"incident_cooldown_minutes": 10})
    for step in range(1, 15):
        box = (200 + step * 40, 160, 300 + step * 40, 240)
        assert engine.evaluate([Track(3, box, 0.8, 2, "car")], step * 1.2, 1000, 600) == []


def test_cooldown_prevents_repeated_incidents_for_same_track():
    zone = make_zone(rules={"dwell_seconds": 3, "classes": ["car"]})
    engine = RuleEngine([zone], {"incident_cooldown_minutes": 10})
    car = Track(1, (650, 150, 740, 240), 0.9, 2, "car")
    fired = 0
    for step in range(1, 30):
        if engine.evaluate([car], step * 1.0, 1000, 600):
            fired += 1
    assert fired == 1


def test_zone_outside_frame_is_ignored():
    engine = RuleEngine([make_zone()], {})
    car = Track(2, (10, 10, 60, 60), 0.9, 2, "car")
    for step in range(1, 15):
        assert engine.evaluate([car], step, 1000, 600) == []


def test_overlapping_vehicles_are_labelled_double_parking():
    zone = make_zone(rules={"dwell_seconds": 3, "classes": ["car"], "double_parking": True})
    engine = RuleEngine([zone], {"incident_cooldown_minutes": 10})
    first = Track(1, (620, 160, 700, 240), 0.9, 2, "car")
    second = Track(2, (710, 160, 790, 240), 0.9, 2, "car")
    categories = []
    for step in range(1, 12):
        categories += [v.category for v in engine.evaluate([first, second], step, 1000, 600)]
    assert "double_parking" in categories


def test_footpath_obstruction_ignores_pedestrians():
    zone = make_zone(
        kind="footpath",
        rules={"dwell_seconds": 3, "classes": ["car", "motorcycle"]},
        polygon=FOOTPATH_POLYGON,
    )
    engine = RuleEngine([zone], {})
    person = Track(9, (300, 450, 340, 560), 0.9, 0, "person")
    for step in range(1, 12):
        assert engine.evaluate([person], step, 1000, 600) == []
    car = Track(10, (300, 450, 400, 550), 0.9, 2, "car")
    categories = []
    for step in range(1, 12):
        categories += [v.category for v in engine.evaluate([car], step, 1000, 600)]
    assert "footpath_obstruction" in categories


def test_restricted_zone_flags_people_when_configured():
    zone = make_zone(
        kind="restricted",
        rules={"dwell_seconds": 2, "classes": ["person"]},
        polygon=[[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]],
    )
    engine = RuleEngine([zone], {})
    person = Track(4, (400, 250, 440, 400), 0.8, 0, "person")
    categories = []
    for step in range(1, 8):
        categories += [v.category for v in engine.evaluate([person], step, 1000, 600)]
    assert categories == ["restricted_entry"]


def test_disabled_zone_is_not_evaluated():
    zone = make_zone()
    zone.enabled = False
    engine = RuleEngine([zone], {})
    car = Track(1, (650, 150, 740, 240), 0.9, 2, "car")
    for step in range(1, 15):
        assert engine.evaluate([car], step, 1000, 600) == []


def test_custom_model_detections_are_deduplicated_spatially():
    engine = RuleEngine([], {})
    detection = Detection((100, 100, 180, 160), 0.7, 0, "pothole")
    first = engine.evaluate_custom([("pothole", detection)], 1.0, 1000, 600)
    second = engine.evaluate_custom([("pothole", detection)], 5.0, 1000, 600)
    later = engine.evaluate_custom([("pothole", detection)], 400.0, 1000, 600)
    assert len(first) == 1
    assert second == []
    assert len(later) == 1
    assert first[0].category == "pothole"
    assert first[0].label.startswith("Potential pothole")
