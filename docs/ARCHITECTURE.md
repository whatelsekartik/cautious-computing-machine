# Architecture

CivicWatch is a single-process, local-first application: one FastAPI server owns
the database, the inference pipeline and the built React frontend. There is no
message broker, no worker fleet, no cloud storage — deliberately, because
municipal footage should not leave the machine that analyses it.

```
Browser (React + Vite build)
   │  fetch /api/... , EventSource /api/events
   ├──────────────────────────────────────────────┐
   │                                              │
FastAPI app (uvicorn, 127.0.0.1:8000)             │
   ├── api/system    settings, models, storage, diagnostics, overview
   ├── api/sources   media upload, jobs, zones, cameras, streams
   ├── api/incidents review workflow, evidence, CSV/PDF export
   │
   ├── services/     business logic + background threads
   │     jobs      one thread per analysis job, progress in memory + SQLite
   │     cameras   live capture thread per camera, sampling, live detections
   │     incidents status machine, notes, audit trail
   │     zones     polygons + per-zone rules
   │     reports   ReportLab PDF, streaming CSV
   │     events    in-process event bus → SSE
   │
   ├── pipeline/     Detection → ByteTrack → zone rules → incidents → evidence
   ├── detect/       YOLO wrapper, model registry, MPS/CPU resolution
   ├── tracking/     ByteTrack (Kalman + greedy IoU assignment)
   └── db            SQLite (WAL) + forward-only migrations
```

## Request path and security

* `LocalhostOnlyMiddleware` rejects any request whose socket peer is not
  loopback (unless `CIVICWATCH_ALLOW_REMOTE=1`, used only by delivery previews).
* `security.redact()` runs as a logging filter, so RTSP URLs with credentials
  never reach a log line; API responses return `rtsp://host/***`.
* Camera URLs are encrypted at rest with Fernet. The key lives in
  `data/secrets/credentials.key` with mode 600 and is generated on first use.
* The SPA build is mounted at `/` with history fallback, so deep links such as
  `/incidents` work; `/api/**` is always matched first.

## Database schema (migration 1)

| Table | Purpose | Notable columns |
| --- | --- | --- |
| `settings` | one row per key, validated on write | `key`, `value` (JSON), `updated_at` |
| `media` | uploaded source files | `id`, `original_name`, `size_bytes`, `duration_s`, `width`, `height`, `fps`, `frame_count` |
| `jobs` | analysis runs | `status`, `device`, `model_id`, `sample_every`, `imgsz`, `frames_total`, `frames_processed`, `progress`, `detections`, `incidents`, `runtime_s` |
| `cameras` | live sources | `source_type` (webcam/rtsp/http), `source_value` (index or encrypted URL), `authorised`, `last_seen_at` |
| `zones` | polygons | `kind` (no_parking/footpath/road/restricted), `polygon` (normalised JSON), `rules` (JSON), `source_ref` (media or camera), `enabled` |
| `incidents` | candidate violations | `id` (`INC-YYYYMMDD-NNNN`), `category`, `status`, `confidence`, `track_id`, `captured_at`, `camera`, `location`, `snapshot_path`, `clip_path`, `preview_path`, `review_note`, `reviewed_by` |
| `incident_events` | immutable workflow trail | `incident_id`, `action`, `from_status`, `to_status`, `note`, `actor`, `created_at` |
| `audit_log` | system-level actions | `action`, `detail` (redacted), `actor`, `created_at` |
| `custom_models` | installed third-party weights | `id`, `name`, `family` (pothole/garbage/…), `path`, `enabled`, `classes` |

Migrations are forward-only: `db.init_db()` creates `schema_migrations`, applies
each `NNN_*.sql` in order inside a transaction and records the version. Running
it repeatedly is a no-op.

## Detection pipeline

1. **Ingest** — an upload is copied to `data/media/<id>.<ext>`; OpenCV probes
   duration, size, fps and frame count. Rejections are explicit (unreadable
   codec, zero frames).
2. **Resolve runtime** — `device.resolve_device()` prefers MPS on Apple Silicon,
   falls back to CUDA then CPU; the detector is cached per `model:device` and
   warmed up once so the first real frame is not slow.
3. **Sample frames** — every *n*-th frame (`sample_every`, default 5). Frames are
   downscaled to `max_processing_width` before inference; coordinates are
   mapped back to source space for evidence.
4. **Detect** — YOLO returns boxes for vehicles/pedestrians with confidences.
   Empty frames are cheap and common.
5. **Track** — ByteTrack predicts, associates by IoU, keeps lost tracks for a
   buffer of frames and assigns monotonically increasing IDs. Stationarity is a
   bounding-box spread test over an 8-second window.
6. **Evaluate rules** — for each active track, for each enabled zone:
   point-in-polygon on the box anchor or mask overlap ≥ threshold; accumulate
   dwell time only while stationary; emit a candidate once
   `dwell ≥ zone.dwell_seconds`. Cooldown (default 10 min) prevents duplicate
   incidents for the same track/zone.
7. **Evidence** — snapshot (annotated JPEG), clip (pre/post roll, `mp4v`),
   GIF preview, and an `incidents` row + `incident_events` entry.
8. **Annotate** — when `save_annotated_video` is on, every processed frame is
   drawn with boxes, track IDs, zone outlines and a HUD, and written to
   `data/processed/<job_id>.mp4`.
9. **Report progress** — counters are persisted at most a few times per second
   and pushed over SSE; the UI polls only while a job is active.

Rules are pure functions over `(track, zone, time)`; the engine owns state. That
keeps `pipeline/rules.py` unit-testable without video files.

## Live monitoring

Each camera gets a capture thread (webcam index or OpenCV RTSP/HTTP URL). It
runs the same detector/tracker/rules path with `live_sample_every`, publishes
annotated frames as MJPEG at `/api/cameras/{id}/stream`, and records live
incidents exactly like file jobs. A connection test opens the source, grabs one
frame and reports resolution/fps without starting a session. Credentials are
never echoed back to the client.

## Model management

`detect/registry.py` is the single catalogue:

* **Built-in** — YOLOv8n/s, YOLO11n. Downloads from the official Ultralytics
  release URL, progress-tracked, then validated by loading the checkpoint
  (`torch.load`) and asserting COCO class names before the file is moved into
  place. A partial `.part` file never becomes a usable model.
* **Custom** — any Ultralytics `.pt` uploaded in Settings. The registry records
  its classes and only runs it for categories it can actually emit
  (`pothole`, `garbage`, …). Until installed, `category_status()` returns
  `installed: false` and the UI says the category is unavailable.

## Frontend

React 19 + TypeScript + Tailwind 3 + Lucide, built by Vite into
`frontend/dist` and served by FastAPI.

* `lib/api.ts` is the only place that knows URLs; every call is relative
  (`/api/...`) so the app works at any loopback port.
* `lib/hooks.ts` provides SSE subscription with debounced refresh; job status
  polling is 1.2 s only while a job is active, and stops otherwise.
* Theme is a class on `<html>` persisted in `localStorage`
  (`civicwatch-theme`), initialised before first paint to avoid a flash.
* The UI never invents data: loading, empty and error states are explicit, and
  “not installed” categories are rendered as disabled with a reason.

## HTTP API

Generated from the running app (`GET /docs` is interactive). Summary:

| Area | Endpoints |
| --- | --- |
| System | `GET /api/health`, `/api/overview`, `/api/system/info`, `/api/system/diagnostics`, `/api/audit`, `GET/PATCH /api/settings`, `POST /api/settings/reset/{key}`, `GET /api/storage`, `POST /api/storage/cleanup`, `POST /api/privacy/purge-evidence` |
| Models | `GET /api/models`, `POST /api/models/{id}/download`, `DELETE /api/models/{id}`, `POST/PATCH/DELETE /api/models/custom[/{id}]` |
| Media | `GET/POST /api/media`, `GET/DELETE /api/media/{id}`, `GET /api/media/{id}/frame` |
| Jobs | `GET/POST /api/jobs`, `GET/DELETE /api/jobs/{id}`, `POST /api/jobs/{id}/{action}` (start/pause/resume/stop), `GET /api/jobs/{id}/frame`, `/stream`, `/video` |
| Zones | `GET/POST /api/zones`, `PATCH/DELETE /api/zones/{id}`, `GET /api/zones/kinds` |
| Cameras | `GET/POST /api/cameras`, `PATCH/DELETE /api/cameras/{id}`, `POST /api/cameras/{id}/{test\|start\|stop}`, `GET /api/cameras/{id}/{frame\|stream}`, `GET /api/cameras/webcams/discover` |
| Incidents | `GET /api/incidents` (filters incl. `source_ref`), `GET/PATCH/DELETE /api/incidents/{id}`, `POST /api/incidents/{id}/notes`, `GET …/{snapshot\|preview\|clip}`, `GET /api/incidents/{categories\|stats\|export.csv\|export.pdf}`, `GET /api/events` (SSE) |
| Files | `GET /api/files/{kind}/{name}` for processed artefacts |

## Concurrency and failure handling

* FastAPI is single-process; SQLite runs in WAL mode with a process-wide
  connection guarded by a lock, so API reads and job writes coexist.
* Job runners live in a dict guarded by `JobService._lock`; deletion pops the
  runner under the lock and prunes old entries (`_prune_runners(keep=20)`) so
  memory stays bounded during long sessions.
* A job that crashes records `status=error` with the message, keeps completed
  evidence and can be deleted cleanly.
* Shutdown stops camera capture threads and signals job runners to stop before
  the process exits.
* Every evidence path is resolved through the service layer; the `/api/files`
  route only serves names inside the configured data directory.

## Extension points

* **New violation type** — add a `KIND_DEFAULTS` entry in `pipeline/rules.py`,
  a category label in `detect/base.py`, and expose it in the Zone Editor.
* **New detector family** — implement the `Detector` protocol
  (`detect/base.py`) and register it; the pipeline only calls `.predict()`.
* **New source** — cameras are rows with a `source_type`; a future CivicBot
  robot camera becomes another capture adapter without touching the pipeline.
* **New export** — `services/reports.py` builds PDFs from incident rows; add
  another formatter alongside CSV.
