"""Evidence capture: annotated snapshots plus short clips with pre/post roll."""

from __future__ import annotations

import logging
import math
from collections import deque
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("civicwatch.evidence")


class _ClipWriter:
    def __init__(self, path: Path, writer: cv2.VideoWriter, post_frames: int) -> None:
        self.path = path
        self.writer = writer
        self.remaining = post_frames
        self.frames: list[np.ndarray] = []
        self.preview_path = path.with_name("preview.gif")


class EvidenceRecorder:
    """Keeps a short ring buffer so incidents have footage from *before* the event.

    Processing is sequential per source, so a single recorder is safe to drive
    from the frame loop. Clips are written with the universal ``mp4v`` codec so
    they play in Safari/QuickTime without FFmpeg CLI helpers.
    """

    def __init__(
        self,
        base_dir: Path,
        clip_fps: float = 4.0,
        pre_seconds: float = 3.0,
        post_seconds: float = 3.0,
        clip_width: int = 640,
        store_snapshots: bool = True,
        store_clips: bool = True,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.clip_fps = max(1.0, min(30.0, float(clip_fps)))
        self.pre_seconds = max(0.0, float(pre_seconds))
        self.post_seconds = max(0.0, float(post_seconds))
        self.clip_width = int(clip_width)
        self.store_snapshots = store_snapshots
        self.store_clips = store_clips
        capacity = max(2, int(math.ceil(self.pre_seconds * self.clip_fps)) + 2)
        self._buffer: deque[np.ndarray] = deque(maxlen=capacity)
        self._active: dict[str, _ClipWriter] = {}
        self.frames_written = 0

    # -- capture ---------------------------------------------------------
    def push(self, frame_bgr: np.ndarray, _t_seconds: float = 0.0) -> None:
        clip_frame = self._resize(frame_bgr)
        if self.store_clips:
            self._buffer.append(clip_frame)
        finished: list[str] = []
        for incident_id, clip in self._active.items():
            if clip.remaining <= 0:
                finished.append(incident_id)
                continue
            self._write(clip, clip_frame)
            clip.remaining -= 1
            if clip.remaining <= 0:
                finished.append(incident_id)
        for incident_id in finished:
            self._close(incident_id)

    def snapshot(self, frame_bgr: np.ndarray, incident_id: str) -> str | None:
        if not self.store_snapshots:
            return None
        directory = self.base_dir / incident_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "snapshot.jpg"
        ok, buffer = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            log.warning("Could not encode snapshot for %s", incident_id)
            return None
        path.write_bytes(buffer.tobytes())
        return str(path)

    def start_clip(self, incident_id: str) -> str | None:
        if not self.store_clips or not self._buffer:
            return None
        height, width = self._buffer[-1].shape[:2]
        directory = self.base_dir / incident_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "clip.mp4"
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.clip_fps, (width, height)
        )
        if not writer.isOpened():
            log.warning("Video encoder unavailable; storing snapshot only for %s", incident_id)
            return None
        frames: list[np.ndarray] = []
        for frame in self._buffer:
            writer.write(frame)
            frames.append(frame)
            self.frames_written += 1
        post_frames = int(round(self.post_seconds * self.clip_fps))
        clip = _ClipWriter(path=path, writer=writer, post_frames=post_frames)
        clip.frames = frames
        self._active[incident_id] = clip
        if post_frames <= 0:
            self._close(incident_id)
        return str(path)

    def close(self) -> None:
        for incident_id in list(self._active):
            self._close(incident_id)
        self._buffer.clear()

    # -- internals -------------------------------------------------------
    def _resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w <= self.clip_width:
            return frame
        scale = self.clip_width / float(w)
        return cv2.resize(
            frame, (self.clip_width, int(round(h * scale))), interpolation=cv2.INTER_AREA
        )

    @staticmethod
    def _write(clip: _ClipWriter, frame: np.ndarray) -> None:
        try:
            clip.writer.write(frame)
            if len(clip.frames) < 90:
                clip.frames.append(frame)
        except Exception:  # pragma: no cover - codec edge case
            return

    def _close(self, incident_id: str) -> None:
        clip = self._active.pop(incident_id, None)
        if clip is None:
            return
        try:
            clip.writer.release()
        except Exception:  # pragma: no cover
            pass
        _write_preview_gif(clip.frames, clip.preview_path, self.clip_fps)


def _write_preview_gif(frames: list[np.ndarray], path: Path, fps: float) -> None:
    """Animated GIF preview so clips always play in a browser.

    OpenCV's bundled FFmpeg can encode H.264 only on some builds, while every
    browser plays GIF. The MP4 remains the downloadable record; the GIF is a
    lightweight, universally playable preview of the same frames.
    """
    if not frames:
        return
    try:
        from PIL import Image
    except Exception:  # pragma: no cover - pillow ships with ultralytics
        return
    try:
        images = []
        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            if image.width > 480:
                height = int(image.height * 480 / image.width)
                image = image.resize((480, height), Image.BILINEAR)
            images.append(image.convert("P", palette=Image.ADAPTIVE, colors=128))
        if not images:
            return
        duration = max(60, int(1000 / max(1.0, fps)))
        images[0].save(
            path,
            save_all=True,
            append_images=images[1:],
            duration=duration,
            loop=0,
            optimize=True,
        )
    except Exception:  # pragma: no cover - never fail an incident over a preview
        return
