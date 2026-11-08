"""Frame annotation: zones, tracked boxes, HUD and optional privacy blur."""

from __future__ import annotations

from typing import Iterable, Sequence

import cv2
import numpy as np

from ..geometry import polygon_centroid

# BGR colours chosen to match the dashboard palette.
COLORS = {
    "car": (168, 111, 61),
    "truck": (168, 111, 61),
    "bus": (168, 111, 61),
    "motorcycle": (60, 160, 90),
    "bicycle": (60, 160, 90),
    "person": (140, 90, 190),
    "default": (100, 100, 100),
}

ZONE_COLORS = {
    "no_parking": (40, 165, 240),  # amber
    "footpath": (115, 122, 47),  # teal
    "road": (168, 111, 61),  # blue
    "restricted": (60, 69, 196),  # red
    "pothole": (40, 165, 240),
    "garbage": (115, 122, 47),
}

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text_size(text: str, scale: float, thickness: int) -> tuple[int, int, int]:
    (w, h), base = cv2.getTextSize(text, FONT, scale, thickness)
    return w, h, base


def draw_zone(
    frame: np.ndarray,
    polygon: Sequence[Sequence[float]],
    kind: str,
    name: str,
    alpha: float = 0.16,
) -> None:
    h, w = frame.shape[:2]
    pts = np.array([[int(x * w), int(y * h)] for x, y in polygon], dtype=np.int32)
    if len(pts) < 3:
        return
    color = ZONE_COLORS.get(kind, (150, 150, 150))
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
    thickness = 2
    cv2.polylines(frame, [pts], True, color, thickness, lineType=cv2.LINE_AA)
    if name:
        cx, cy = polygon_centroid(polygon)
        label = name.upper()
        scale = 0.42
        tw, th, _ = _text_size(label, scale, 1)
        x = int(cx * w - tw / 2)
        y = int(cy * h)
        cv2.rectangle(frame, (x - 4, y - th - 6), (x + tw + 4, y + 6), color, -1)
        cv2.putText(frame, label, (x, y), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def blur_box(frame: np.ndarray, box: Sequence[float], strength: int = 25) -> None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return
    region = frame[y1:y2, x1:x2]
    k = max(3, strength | 1)
    frame[y1:y2, x1:x2] = cv2.GaussianBlur(region, (k, k), 0)


def draw_track(
    frame: np.ndarray,
    box: Sequence[float],
    class_name: str,
    track_id: int,
    score: float,
    stationary: bool = False,
) -> None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return
    color = COLORS.get(class_name, COLORS["default"])
    thickness = 2
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, lineType=cv2.LINE_AA)
    if stationary:
        # Small corner ticks mark objects the rules engine considers stationary.
        tick = max(6, min(14, (x2 - x1) // 6))
        for (cx, cy, dx, dy) in (
            (x1, y1, 1, 1),
            (x2, y1, -1, 1),
            (x1, y2, 1, -1),
            (x2, y2, -1, -1),
        ):
            cv2.line(frame, (cx, cy), (cx + dx * tick, cy), (255, 255, 255), 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy * tick), (255, 255, 255), 2)
    label = f"#{track_id} {class_name.upper()} {score:.2f}"
    scale = 0.4
    tw, th, _ = _text_size(label, scale, 1)
    top = y1 - th - 8
    if top < 0:
        top = y1 + 1
    cv2.rectangle(frame, (x1, top), (x1 + tw + 8, top + th + 8), color, -1)
    cv2.putText(
        frame, label, (x1 + 4, top + th + 4), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA
    )


def draw_detection(frame: np.ndarray, box: Sequence[float], label: str, category: str) -> None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w - 1, x2), min(h - 1, y2)
    color = ZONE_COLORS.get(category, (40, 165, 240))
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, lineType=cv2.LINE_AA)
    scale = 0.42
    tw, th, _ = _text_size(label, scale, 1)
    top = max(0, y1 - th - 8)
    cv2.rectangle(frame, (x1, top), (x1 + tw + 8, top + th + 8), color, -1)
    cv2.putText(frame, label, (x1 + 4, top + th + 4), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def draw_hud(frame: np.ndarray, lines: Iterable[str]) -> None:
    lines = [line for line in lines if line]
    if not lines:
        return
    pad = 8
    scale = 0.45
    sizes = [_text_size(line, scale, 1) for line in lines]
    width = max(s[0] for s in sizes) + pad * 2
    line_h = max(s[1] for s in sizes) + 8
    height = line_h * len(lines) + pad * 2
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, height), (28, 28, 26), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    y = pad + line_h - 8
    for line in lines:
        cv2.putText(frame, line, (pad, y), FONT, scale, (240, 240, 235), 1, cv2.LINE_AA)
        y += line_h


def letterbox(frame: np.ndarray, max_width: int) -> tuple[np.ndarray, float]:
    """Downscale a frame, returning the scale factor for coordinate mapping."""
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame, 1.0
    scale = max_width / float(w)
    resized = cv2.resize(
        frame, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA
    )
    return resized, scale


def encode_jpeg(frame: np.ndarray, quality: int = 78) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:  # pragma: no cover - encoder failure
        raise RuntimeError("Could not encode frame")
    return buffer.tobytes()
