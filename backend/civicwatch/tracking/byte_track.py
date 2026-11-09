"""ByteTrack multi-object tracker (dependency-light re-implementation).

Algorithm (Zhang et al., "ByteTrack: Multi-Object Tracking by Associating
Every Detection Box", ECCV 2022, MIT licensed reference implementation):

1. Kalman motion model with a constant velocity state.
2. First association of high-confidence detections against active tracks.
3. Second association of *low*-confidence detections against remaining tracks,
   which is what keeps occluded vehicles alive and preserves their identity.
4. Unconfirmed (new) tracks are matched against the remaining detections.
5. Tracks not seen for ``track_buffer`` frames are dropped.

Deliberate engineering choices for CivicWatch:

* Assignment uses Hungarian matching via SciPy when it is available (SciPy is
  already installed as an Ultralytics dependency) and falls back to a greedy
  IoU assignment otherwise.
* ``class_aware=True`` stops a car track from being re-used for a pedestrian,
  which matters when evidence is attached to a specific vehicle. Set it to
  False for stock ByteTrack behaviour.
* The tracker keeps no global state, so every video job and every camera
  session gets its own instance. This is required for pause/resume and for
  processing multiple sources at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

try:  # SciPy ships with Ultralytics; keep a graceful fallback.
    from scipy.optimize import linear_sum_assignment as _lsa

    _HAS_SCIPY = True
except Exception:  # pragma: no cover - fallback path
    _lsa = None
    _HAS_SCIPY = False


# --------------------------------------------------------------------------
# Kalman filter (constant velocity model on [cx, cy, aspect, height])
# --------------------------------------------------------------------------


class KalmanFilter:
    def __init__(self) -> None:
        ndim, dt = 4, 1.0
        self._motion_mat = np.eye(2 * ndim)
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt
        self._update_mat = np.eye(ndim, 2 * ndim)
        self._std_weight_position = 1.0 / 20
        self._std_weight_velocity = 1.0 / 160

    def initiate(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mean_pos = measurement
        mean_vel = np.zeros_like(mean_pos)
        mean = np.r_[mean_pos, mean_vel]
        std = [
            2 * self._std_weight_position * measurement[3],
            2 * self._std_weight_position * measurement[3],
            1e-2,
            2 * self._std_weight_position * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            1e-5,
            10 * self._std_weight_velocity * measurement[3],
        ]
        covariance = np.diag(np.square(std))
        return mean, covariance

    def predict(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        std_pos = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-2,
            self._std_weight_position * mean[3],
        ]
        std_vel = [
            self._std_weight_velocity * mean[3],
            self._std_weight_velocity * mean[3],
            1e-5,
            self._std_weight_velocity * mean[3],
        ]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel]))
        mean = np.dot(self._motion_mat, mean)
        covariance = (
            np.linalg.multi_dot((self._motion_mat, covariance, self._motion_mat.T))
            + motion_cov
        )
        return mean, covariance

    def multi_predict(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Predict a batch of states. A loop is plenty for civic-scale counts."""
        if mean.ndim == 1:
            return self.predict(mean, covariance)
        means = np.empty_like(mean)
        covs = np.empty_like(covariance)
        for i in range(mean.shape[0]):
            means[i], covs[i] = self.predict(mean[i], covariance[i])
        return means, covs

    def project(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        std = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-1,
            self._std_weight_position * mean[3],
        ]
        innovation_cov = np.diag(np.square(std))
        mean = np.dot(self._update_mat, mean)
        covariance = np.linalg.multi_dot(
            (self._update_mat, covariance, self._update_mat.T)
        )
        return mean, covariance + innovation_cov

    def multi_project(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        projected_means, projected_covs = [], []
        for m, c in zip(mean, covariance):
            pm, pc = self.project(m, c)
            projected_means.append(pm)
            projected_covs.append(pc)
        return np.array(projected_means), np.array(projected_covs)

    def update(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurement: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        projected_mean, projected_cov = self.project(mean, covariance)
        # Kalman gain via Cholesky solves: K = P H^T (H P H^T + R)^-1
        chol = np.linalg.cholesky(projected_cov)
        rhs = np.dot(covariance, self._update_mat.T).T  # shape (4, 8)
        kalman_gain = np.linalg.solve(chol.T, np.linalg.solve(chol, rhs)).T
        innovation = measurement - projected_mean
        new_mean = mean + np.dot(innovation, kalman_gain.T)
        new_covariance = covariance - np.linalg.multi_dot(
            (kalman_gain, projected_cov, kalman_gain.T)
        )
        return new_mean, new_covariance


# --------------------------------------------------------------------------
# Track state
# --------------------------------------------------------------------------


class TrackState:
    NEW = 0
    TRACKED = 1
    LOST = 2
    REMOVED = 3


def _tlwh_to_xyxy(tlwh: Sequence[float]) -> np.ndarray:
    x, y, w, h = tlwh
    return np.array([x, y, x + w, y + h], dtype=np.float32)


@dataclass
class Track:
    """Public, immutable view of a tracked object for the rules engine."""

    track_id: int
    box: tuple[float, float, float, float]
    score: float
    class_id: int
    class_name: str
    age: int
    hits: int
    time_since_update: int
    is_new: bool


class STrack:
    _count = 0

    def __init__(self, box: Sequence[float], score: float, class_id: int, class_name: str):
        x1, y1, x2, y2 = [float(v) for v in box]
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        self._tlwh = np.array([x1, y1, w, h], dtype=np.float64)
        self.score = float(score)
        self.class_id = int(class_id)
        self.class_name = class_name
        self.mean: np.ndarray | None = None
        self.covariance: np.ndarray | None = None
        self.state = TrackState.NEW
        self.is_activated = False
        self.track_id = 0
        self.start_frame = 0
        self.frame_id = 0
        self.tracklet_len = 0
        self.hits = 0
        self.time_since_update = 0

    def activate(self, kalman: KalmanFilter, frame_id: int) -> None:
        self.kalman_filter = kalman
        self.mean, self.covariance = kalman.initiate(self._xyah())
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        self.is_activated = frame_id == 1
        self.frame_id = frame_id
        self.start_frame = frame_id

    def predict(self) -> None:
        assert self.mean is not None and self.covariance is not None
        mean_state = self.mean.copy()
        if self.state != TrackState.TRACKED:
            mean_state[7] = 0
        self.mean, self.covariance = self.kalman_filter.predict(mean_state, self.covariance)

    def update(self, track: "STrack", frame_id: int) -> None:
        assert self.mean is not None and self.covariance is not None
        self.frame_id = frame_id
        self.tracklet_len += 1
        self._tlwh = track._tlwh.copy()
        self.score = track.score
        self.class_id = track.class_id
        self.class_name = track.class_name
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.hits += 1
        self.time_since_update = 0
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, track._xyah()
        )

    def re_activate(self, new_track: "STrack", frame_id: int, new_id: bool = False) -> None:
        assert self.mean is not None and self.covariance is not None
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, new_track._xyah()
        )
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.frame_id = frame_id
        self.score = new_track.score
        self.class_id = new_track.class_id
        self.class_name = new_track.class_name
        self.time_since_update = 0
        self.hits += 1
        if new_id:
            STrack._count += 1
            self.track_id = STrack._count

    def mark_lost(self) -> None:
        self.state = TrackState.LOST

    def mark_removed(self) -> None:
        self.state = TrackState.REMOVED

    def _xyah(self) -> np.ndarray:
        x, y, w, h = self._tlwh
        return np.array([x + w / 2.0, y + h / 2.0, w / max(h, 1e-6), h], dtype=np.float64)

    @property
    def tlwh(self) -> np.ndarray:
        if self.mean is None:
            return self._tlwh.copy()
        ret = self.mean[:4].copy()
        ret[2] *= ret[3]
        ret[:2] -= ret[2:] / 2.0
        return ret

    @property
    def xyxy(self) -> np.ndarray:
        ret = self.tlwh
        ret[2:] += ret[:2]
        return ret

    def to_track(self, frame_id: int) -> Track:
        x1, y1, x2, y2 = [float(v) for v in self.xyxy]
        return Track(
            track_id=self.track_id,
            box=(x1, y1, x2, y2),
            score=float(self.score),
            class_id=self.class_id,
            class_name=self.class_name,
            age=frame_id - self.start_frame,
            hits=self.hits,
            time_since_update=self.time_since_update,
            is_new=(frame_id - self.start_frame) <= 1,
        )


def _greedy_assignment(cost: np.ndarray, thresh: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    matches: list[tuple[int, int]] = []
    if cost.size == 0:
        return matches, list(range(cost.shape[0])), list(range(cost.shape[1]))
    candidates = [
        (cost[i, j], i, j)
        for i in range(cost.shape[0])
        for j in range(cost.shape[1])
        if cost[i, j] <= thresh
    ]
    candidates.sort(key=lambda c: c[0])
    used_rows: set[int] = set()
    used_cols: set[int] = set()
    for value, i, j in candidates:
        if i in used_rows or j in used_cols:
            continue
        used_rows.add(i)
        used_cols.add(j)
        matches.append((i, j))
    unmatched_rows = [i for i in range(cost.shape[0]) if i not in used_rows]
    unmatched_cols = [j for j in range(cost.shape[1]) if j not in used_cols]
    return matches, unmatched_rows, unmatched_cols


def linear_assignment(
    cost_matrix: np.ndarray, thresh: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    if cost_matrix.size == 0:
        return [], list(range(cost_matrix.shape[0])), list(range(cost_matrix.shape[1]))
    if not _HAS_SCIPY:
        return _greedy_assignment(cost_matrix, thresh)
    rows, cols = _lsa(cost_matrix)
    matches = [(int(r), int(c)) for r, c in zip(rows, cols) if cost_matrix[r, c] <= thresh]
    matched_rows = {m[0] for m in matches}
    matched_cols = {m[1] for m in matches}
    unmatched_rows = [i for i in range(cost_matrix.shape[0]) if i not in matched_rows]
    unmatched_cols = [j for j in range(cost_matrix.shape[1]) if j not in matched_cols]
    return matches, unmatched_rows, unmatched_cols


def iou_distance(tracks: Sequence[STrack], detections: Sequence[Any]) -> np.ndarray:
    """ByteTrack's IoU cost (1 - IoU), computed in one vectorised pass."""
    if not tracks or not detections:
        return np.zeros((len(tracks), len(detections)), dtype=np.float32)
    track_boxes = [tuple(float(v) for v in t.xyxy) for t in tracks]
    det_boxes = [tuple(float(v) for v in d.xyxy) for d in detections]
    a = np.asarray(track_boxes, dtype=np.float32)
    b = np.asarray(det_boxes, dtype=np.float32)
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    iw = np.clip(ix2 - ix1, 0, None)
    ih = np.clip(iy2 - iy1, 0, None)
    inter = iw * ih
    area_a = np.clip(a[:, 2] - a[:, 0], 0, None) * np.clip(a[:, 3] - a[:, 1], 0, None)
    area_b = np.clip(b[:, 2] - b[:, 0], 0, None) * np.clip(b[:, 3] - b[:, 1], 0, None)
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        iou = np.where(union > 0, inter / union, 0.0)
    return (1.0 - iou).astype(np.float32)


class BYTETracker:
    def __init__(
        self,
        track_thresh: float = 0.4,
        match_thresh: float = 0.8,
        track_buffer: int = 30,
        class_aware: bool = True,
        min_box_area: float = 64.0,
    ) -> None:
        self.tracked_stracks: list[STrack] = []
        self.lost_stracks: list[STrack] = []
        self.removed_stracks: list[STrack] = []
        self.kalman_filter = KalmanFilter()
        self.track_thresh = float(track_thresh)
        # ByteTrack keeps a second, lower confidence gate for rescuing tracks.
        self.low_thresh = max(0.05, min(0.1, self.track_thresh * 0.25))
        self.match_thresh = float(match_thresh)
        self.buffer_size = int(track_buffer)
        self.max_time_lost = self.buffer_size
        self.class_aware = bool(class_aware)
        self.min_box_area = float(min_box_area)
        self.frame_id = 0
        self._reset_id()

    def _reset_id(self) -> None:
        # Per-instance ids are stable and small, which reads much better in the
        # UI and in exported reports than a global counter.
        self._next_id = 0

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    # -- public API ------------------------------------------------------
    def update(self, detections: Sequence[Any], frame_id: int | None = None) -> list[Track]:
        """Advance the tracker by one frame and return the currently active tracks."""
        self.frame_id = frame_id if frame_id is not None else self.frame_id + 1
        frame_id = self.frame_id

        dets = [
            d
            for d in detections
            if (d.xyxy[2] - d.xyxy[0]) * (d.xyxy[3] - d.xyxy[1]) >= self.min_box_area
        ]
        high = [d for d in dets if d.score >= self.track_thresh]
        second = [d for d in dets if self.low_thresh <= d.score < self.track_thresh]

        activated: list[STrack] = []
        lost_now: list[STrack] = []

        unconfirmed = [t for t in self.tracked_stracks if not t.is_activated]
        tracked = [t for t in self.tracked_stracks if t.is_activated]
        strack_pool = _joint(tracked, self.lost_stracks)

        for track in strack_pool:
            track.time_since_update += 1
        _multi_predict(self.kalman_filter, strack_pool)

        # ---- stage 1: high confidence detections vs active + lost tracks
        dists = iou_distance(strack_pool, high)
        if self.class_aware:
            _mask_class_mismatch(dists, strack_pool, high)
        matches, u_track, u_det = linear_assignment(dists, self.match_thresh)
        for i, j in matches:
            track, det = strack_pool[i], high[j]
            candidate = STrack(det.xyxy, det.score, det.class_id, det.class_name)
            if track.state == TrackState.TRACKED:
                track.update(candidate, frame_id)
                activated.append(track)
            else:  # a previously lost track was re-acquired
                track.re_activate(candidate, frame_id)
                activated.append(track)

        # ---- stage 2: low confidence detections rescue recently lost tracks
        r_tracked = [strack_pool[i] for i in u_track if strack_pool[i].state == TrackState.TRACKED]
        dists = iou_distance(r_tracked, second)
        if self.class_aware:
            _mask_class_mismatch(dists, r_tracked, second)
        matches, u_track_2, _ = linear_assignment(dists, 0.5)
        for i, j in matches:
            track, det = r_tracked[i], second[j]
            candidate = STrack(det.xyxy, det.score, det.class_id, det.class_name)
            if track.state == TrackState.TRACKED:
                track.update(candidate, frame_id)
                activated.append(track)
            else:  # a previously lost track was re-acquired
                track.re_activate(candidate, frame_id)
                activated.append(track)
        for i in u_track_2:
            track = r_tracked[i]
            if track.state != TrackState.LOST:
                track.mark_lost()
                lost_now.append(track)

        # Tracks that were already lost and still unmatched are dropped below.
        # ---- stage 3: unconfirmed tracks vs the remaining high detections
        remaining = [high[j] for j in u_det]
        dists = iou_distance(unconfirmed, remaining)
        if self.class_aware:
            _mask_class_mismatch(dists, unconfirmed, remaining)
        matches, u_unconfirmed, u_det = linear_assignment(dists, 0.7)
        for i, j in matches:
            det = remaining[j]
            unconfirmed[i].update(
                STrack(det.xyxy, det.score, det.class_id, det.class_name), frame_id
            )
            activated.append(unconfirmed[i])
        removed: list[STrack] = []
        for i in u_unconfirmed:
            track = unconfirmed[i]
            track.mark_removed()
            removed.append(track)

        # ---- stage 4: activate brand new tracks from leftover detections
        for j in u_det:
            det = remaining[j]
            if det.score < self.track_thresh:
                continue
            track = STrack(det.xyxy, det.score, det.class_id, det.class_name)
            track.activate(self.kalman_filter, frame_id)
            track.track_id = self._new_id()
            if track.is_activated:
                activated.append(track)
            else:
                unconfirmed.append(track)

        # ---- housekeeping: expire tracks that stayed lost for too long
        still_lost: list[STrack] = []
        for track in _joint(self.lost_stracks, lost_now):
            if track.state != TrackState.LOST:
                continue
            if frame_id - track.frame_id > self.max_time_lost:
                track.mark_removed()
                removed.append(track)
            else:
                still_lost.append(track)

        self.tracked_stracks = [
            t for t in _joint(activated, unconfirmed) if t.state == TrackState.TRACKED
        ]
        self.lost_stracks = still_lost
        self.removed_stracks.extend(removed)
        if len(self.removed_stracks) > 1000:
            self.removed_stracks = self.removed_stracks[-999:]

        return [t.to_track(frame_id) for t in self.tracked_stracks if t.is_activated]

    def reset(self) -> None:
        self.tracked_stracks = []
        self.lost_stracks = []
        self.removed_stracks = []
        self.frame_id = 0
        self._reset_id()


def _joint(a: Sequence[STrack], b: Sequence[STrack]) -> list[STrack]:
    """Union of two track lists with stable ordering (by object identity)."""
    seen: set[int] = set()
    out: list[STrack] = []
    for track in list(a) + list(b):
        key = id(track)
        if key not in seen:
            seen.add(key)
            out.append(track)
    return out


def _sub(a: Sequence[STrack], b: Sequence[STrack]) -> list[STrack]:
    excluded = {id(t) for t in b}
    return [t for t in a if id(t) not in excluded]


def _mask_class_mismatch(dists: np.ndarray, tracks: Sequence[STrack], detections: Sequence[Any]) -> None:
    for i, track in enumerate(tracks):
        for j, det in enumerate(detections):
            if track.class_name != det.class_name:
                dists[i, j] = 1.0


def _multi_predict(kalman: KalmanFilter, tracks: Sequence[STrack]) -> None:
    """Vectorised Kalman prediction for all live tracks in one call."""
    if not tracks:
        return
    means = np.array([t.mean for t in tracks], dtype=np.float64)
    covs = np.array([t.covariance for t in tracks], dtype=np.float64)
    means, covs = kalman.multi_predict(means, covs)
    for track, mean, cov in zip(tracks, means, covs):
        track.mean, track.covariance = mean, cov
