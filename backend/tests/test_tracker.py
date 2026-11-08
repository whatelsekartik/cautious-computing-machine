"""ByteTrack behaviour tests (synthetic detections, no model required)."""

from dataclasses import dataclass

from civicwatch.tracking import BYTETracker


@dataclass
class Det:
    xyxy: tuple[float, float, float, float]
    score: float
    class_id: int
    class_name: str


def det(x: float, y: float, w: float = 60, h: float = 50, score: float = 0.9, name: str = "car") -> Det:
    return Det((x, y, x + w, y + h), score, 2, name)


def test_identities_are_stable_for_moving_and_static_objects():
    tracker = BYTETracker()
    moving_ids, static_ids = set(), set()
    for frame in range(1, 41):
        tracks = tracker.update([det(50 + frame * 8, 200), det(600, 300)])
        for track in tracks:
            if track.box[0] < 500:
                moving_ids.add(track.track_id)
            else:
                static_ids.add(track.track_id)
    assert len(moving_ids) == 1
    assert len(static_ids) == 1
    assert moving_ids != static_ids


def test_track_survives_short_occlusion():
    tracker = BYTETracker(track_buffer=30)
    seen: set[int] = set()
    for frame in range(1, 31):
        detections = [] if 12 <= frame <= 16 else [det(100 + frame * 5, 100)]
        for track in tracker.update(detections):
            seen.add(track.track_id)
    assert seen == {1}


def test_low_confidence_detections_keep_the_track_alive():
    """ByteTrack's second association stage is what rescues flickering scores."""
    tracker = BYTETracker()
    ids = set()
    for frame in range(1, 21):
        score = 0.9 if frame % 2 else 0.12
        for track in tracker.update([det(200, 150, score=score)]):
            ids.add(track.track_id)
    assert ids == {1}


def test_class_aware_tracking_does_not_reuse_vehicle_ids_for_people():
    tracker = BYTETracker(class_aware=True)
    tracker.update([det(100, 100, name="car")])
    second = tracker.update([det(105, 100, name="person")])
    assert all(track.class_name == "car" for track in second)
    for _ in range(3):
        tracks = tracker.update([det(110, 100, name="person")])
    assert any(track.class_name == "person" for track in tracks)


def test_new_track_needs_confirmation_before_being_reported():
    tracker = BYTETracker()
    tracker.update([])  # frame 1
    assert tracker.update([det(50, 60)]) == []  # frame 2: created but unconfirmed
    assert tracker.update([det(50, 60)]) != []  # frame 3: confirmed


def test_reset_clears_state():
    tracker = BYTETracker()
    tracker.update([det(10, 10)])
    tracker.reset()
    assert tracker.tracked_stracks == []
    assert tracker.update([det(10, 10), det(80, 80)]) != []
