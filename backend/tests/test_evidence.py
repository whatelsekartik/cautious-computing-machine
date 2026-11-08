import numpy as np
from pathlib import Path

from civicwatch.pipeline.evidence import EvidenceRecorder


def test_snapshot_clip_and_preview_are_written(tmp_path: Path):
    recorder = EvidenceRecorder(
        base_dir=tmp_path,
        clip_fps=5,
        pre_seconds=1.0,
        post_seconds=1.0,
        clip_width=320,
    )
    for index in range(12):
        recorder.push(np.full((240, 320, 3), index * 12, dtype=np.uint8), index * 0.2)

    snapshot = recorder.snapshot(np.full((240, 320, 3), 90, dtype=np.uint8), "INC-TEST-0001")
    clip = recorder.start_clip("INC-TEST-0001")
    assert snapshot and Path(snapshot).exists()
    assert clip and Path(clip).exists()

    for index in range(10):
        recorder.push(np.full((240, 320, 3), 120, dtype=np.uint8), 2 + index * 0.2)
    recorder.close()

    folder = Path(snapshot).parent
    assert (folder / "snapshot.jpg").exists()
    assert (folder / "clip.mp4").exists()
    assert (folder / "preview.gif").exists()
    assert (folder / "clip.mp4").stat().st_size > 0
    assert (folder / "preview.gif").read_bytes()[:3] == b"GIF"


def test_evidence_can_be_disabled(tmp_path: Path):
    recorder = EvidenceRecorder(
        base_dir=tmp_path,
        clip_fps=4,
        pre_seconds=1,
        post_seconds=1,
        store_snapshots=False,
        store_clips=False,
    )
    recorder.push(np.zeros((120, 160, 3), dtype=np.uint8))
    assert recorder.snapshot(np.zeros((120, 160, 3), dtype=np.uint8), "INC-X") is None
    assert recorder.start_clip("INC-X") is None
    assert list(tmp_path.glob("*")) == []


def test_clip_is_downscaled_to_configured_width(tmp_path: Path):
    import cv2

    recorder = EvidenceRecorder(
        base_dir=tmp_path, clip_fps=4, pre_seconds=1, post_seconds=0.2, clip_width=160
    )
    for _ in range(6):
        recorder.push(np.zeros((480, 640, 3), dtype=np.uint8))
    clip = recorder.start_clip("INC-Y")
    recorder.close()
    capture = cv2.VideoCapture(clip)
    assert capture.isOpened()
    assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == 160
    capture.release()
