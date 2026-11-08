# CivicWatch AI Studio

**Local-first civic monitoring for a municipality's own cameras and video files.**
Upload or point CivicWatch at footage, and it finds *potential* parking and
obstruction violations, packages each one with evidence, and puts it in front of
a human for a decision. Nothing is uploaded, nothing is auto-fined, and no cloud
service is involved — every frame is processed on the Mac it runs on.

> Automated output is a **signal for human review**, not a legal finding.
> CivicWatch never issues challans or penalties, performs no face recognition,
> and every incident record is labelled a *potential* violation.

---

## Quick start (macOS)

1. **Double-click `Setup CivicWatch.command`.**
   It detects or installs Python and Node, builds an isolated environment,
   downloads and validates the AI model, creates the database and runs a
   full self-check. First run takes roughly 5–15 minutes (mostly PyTorch);
   re-running is safe and skips finished work.
2. **Double-click `Start CivicWatch.command`.**
   It starts the local server, waits until it is genuinely ready, and opens
   <http://127.0.0.1:8000> in your browser.
3. **Stop with `Stop CivicWatch.command`.** Your data stays in `data/`.

No Terminal commands, no config files, no cloud account, no Docker. After
setup, CivicWatch works fully offline.

*Optional:* run `Create CivicWatch App.command` once to get a clickable
`CivicWatch.app` you can drag into `/Applications` or Launchpad.

### Your first analysis (about two minutes)

1. **Video Analysis** → drop `samples/demo-street-traffic.avi` (a short street
   clip shipped with the app) or pick your own file.
2. **Zone Editor** → choose that upload, scrub to a representative frame, and
   draw a *No parking* polygon over the kerb; repeat for any footpath or
   restricted areas. Each zone has its own dwell time and vehicle classes.
3. **Video Analysis** → choose sampling and resolution, then **Start analysis**.
   Boxes and ByteTrack IDs appear on the annotated preview; progress, frame
   counts and detection totals update live.
4. **Incidents** → open a candidate, look at the snapshot, clip and tracking ID,
   then **Confirm**, **Dismiss**, **Forward** or **Resolve** with a note.
5. **Export** → filtered CSV for your register, or a signed-off PDF dossier.

---

## What is in the box

| Page | What it does |
| --- | --- |
| **Overview** | Processing status, incident counts by state, recent detections, throughput and hardware. All numbers come from the database — no sample data. |
| **Video Analysis** | Drag-and-drop or file picker, preview, start/pause/resume/stop, frame-sampling and resolution controls, annotated footage with boxes + track IDs, live progress. |
| **Live Monitoring** | Webcam or authorised RTSP/HTTP camera, connection test, start/stop, sampling, live detection feed. Credentials are encrypted at rest and never displayed or logged. |
| **Zone Editor** | Load a real frame, draw polygons for no-parking, footpath, road boundary and restricted areas, set per-zone rules, edit or delete later. |
| **Incidents** | Search and filter by category, status, camera, zone and date; review evidence (snapshot, clip, GIF preview, tracking ID, confidence, timestamps); confirm/dismiss/forward/resolve; export CSV/PDF. |
| **Settings** | Hardware and device selection (MPS/CPU), model catalogue with download/progress/validation, custom model installation, confidence and IoU, sampling, retention, storage usage and cleanup, diagnostics and model updates, privacy controls. |

Design: warm off-white surfaces, muted teal accents, thin borders, rounded
cards, system fonts, a real dark mode (toggle in the header, remembered per
browser). It looks like municipal operations software, not a demo.

### Detection

* **Vehicles and people** — Ultralytics YOLO (YOLOv8n by default, YOLOv8s and
  YOLO11n selectable) detecting `car`, `motorcycle`, `bus`, `truck`, `bicycle`
  and `person`. Inference runs on Apple Silicon's GPU via Metal (MPS) when
  available, otherwise on the CPU.
* **Tracking** — an in-repo ByteTrack implementation assigns stable track IDs,
  so a parked car keeps the same ID across frames.
* **Zones and rules** — polygons are rasterised once per resolution and each
  track is tested for overlap/containment. A candidate incident is emitted only
  when the object has been *stationary* for the zone's dwell time:

  | Zone kind | Default dwell | Categories |
  | --- | --- | --- |
  | No parking | 20 s | illegal parking, double parking |
  | Footpath | 5 s | footpath obstruction |
  | Road boundary | 10 s | stopped in carriageway |
  | Restricted | 2 s | restricted entry |

  Double parking is geometry-based: two stationary vehicles side by side inside
  the same zone. All thresholds are editable per zone.
* **Pothole and garbage** — these need their own trained models. CivicWatch
  ships a modular interface (install any Ultralytics-format `.pt` in Settings)
  and says **“model not installed”** until you supply one. It never pretends a
  generic detector can see a pothole.
* **Evidence** — per incident: annotated snapshot, short clip with pre/post
  roll, a GIF preview that plays everywhere, the tracking ID, confidence,
  camera/location, timestamps and the zone rule that fired.

### Human-in-the-loop workflow

`Detected → Pending review → Verified → Forwarded → Resolved`, plus `Dismissed`.
Every transition is written to `incident_events` and the audit log with the
operator name, timestamp and note. Deleting or exporting never bypasses review.

---

## Requirements

* macOS 12 Monterey or newer (Apple Silicon or Intel; Apple Silicon gets GPU
  inference via MPS, Intel uses the CPU).
* ~6 GB free disk (PyTorch is the bulk), 8 GB RAM recommended.
* Internet **once**, during setup. No account, no API key, no paid service.
* A browser (Safari, Chrome, Firefox, Edge).

Python 3.10–3.13 and Node 20+ are used if present. If they are missing, Setup
downloads private, checksum-verified copies into `tools/runtime/` — no admin
password, no Homebrew, nothing added to your PATH.

---

## Privacy and security by design

* The API binds to **127.0.0.1** and a middleware rejects any request whose
  client address is not loopback, so footage never leaves the machine.
* No face recognition, no re-identification, no analytics SDK, no telemetry.
  Optional person blurring is available for evidence frames.
* Camera credentials are encrypted with a locally generated Fernet key
  (`data/secrets/credentials.key`, mode 600) and masked in every response and
  log line.
* Retention is enforced: media and evidence are purged after the configured
  window (14 and 30 days by default), with a manual “purge evidence” control.
* The audit log records review decisions, exports, model installs and settings
  changes.

Read **[docs/PRIVACY_AND_LEGAL.md](docs/PRIVACY_AND_LEGAL.md)** before pointing
CivicWatch at real public space footage — it covers CCTV authorisation, DPDP
Act 2023 considerations, signage, retention and the AGPL-3.0 licence that
applies because of the YOLO weights.

---

## Project layout

```
.
├── Setup CivicWatch.command        one-time installer + verifier
├── Start CivicWatch.command        start server, wait for health, open browser
├── Stop CivicWatch.command         graceful shutdown
├── Create CivicWatch App.command   optional CivicWatch.app builder
├── launchers/lib/common.sh         shared shell helpers (runtime discovery, health)
├── config/civicwatch.example.env   optional environment overrides
├── samples/demo-street-traffic.avi bundled demo clip (OpenCV sample data)
├── assets/icon.png                 app icon source
├── tools/
│   ├── diagnostics.py              full self-check + inference smoke test
│   ├── download_models.py          download/validate model weights
│   └── dev.sh                      hot-reload developer launcher
├── backend/
│   ├── requirements.txt            runtime dependencies
│   ├── requirements-dev.txt        test dependencies
│   ├── civicwatch/
│   │   ├── main.py                 FastAPI app, redaction, loopback guard, SPA mount
│   │   ├── config.py               paths, version, env overrides
│   │   ├── db.py                   SQLite connection + migration runner
│   │   ├── migrations/001_init.sql schema (9 tables + indexes)
│   │   ├── security.py             Fernet credential vault, redaction, masking
│   │   ├── settings_store.py       typed settings with validation
│   │   ├── geometry.py             polygons, masks, IoU, containment
│   │   ├── detect/                 base, device (MPS/CPU), yolo, registry
│   │   ├── tracking/byte_track.py  ByteTrack tracker + Kalman filter
│   │   ├── pipeline/               engine, rules, annotate, evidence
│   │   ├── services/               jobs, incidents, zones, cameras, reports, events, system
│   │   └── api/                    system, sources, incidents routers
│   └── tests/                      9 test modules (69 tests)
├── frontend/
│   ├── src/pages/                  Overview, VideoAnalysis, LiveMonitoring,
│   │                               ZoneEditor, Incidents, Settings
│   ├── src/components/             AppShell, ui kit, drawer, toasts, accents
│   ├── src/lib/                    API client, types, formatting, hooks
│   └── src/test/                   vitest specs (14 tests)
├── docs/                           architecture, security/legal, troubleshooting
└── data/                           created at setup: db, media, evidence, models, logs
```

Runtime data (`data/`) is deliberately **not** committed: it contains footage,
evidence frames and the credential key.

---

## Configuration

Normal use needs none. Settings page → model, confidence, IoU, image size,
frame sampling, tracker thresholds, evidence clips, retention, theme, operator
name, storage cleanup, diagnostics.

Advanced/portable setups can use the environment variables documented in
[`config/civicwatch.example.env`](config/civicwatch.example.env):
`CIVICWATCH_DATA_DIR`, `CIVICWATCH_HOST`, `CIVICWATCH_PORT`,
`CIVICWATCH_ALLOW_REMOTE` (sandbox only; the launchers never set it), plus
`CIVICWATCH_VENV` for an externally managed virtual environment.

The API schema is served at `/docs` and summarised in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#http-api).

---

## Development and tests

```bash
tools/dev.sh                               # backend :8000 + Vite :5173 with reload
cd backend && ../.venv/bin/python -m pytest        # API, rules, tracker, evidence, launchers
cd frontend && npx vitest run                       # formatting, API client, UI kit
cd frontend && npx tsc -b && npx vite build         # typecheck + production build
.venv/bin/python tools/diagnostics.py               # full environment + inference check
```

Latest results are recorded in [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md),
including which checks could not run in the Linux build sandbox.

---

## Known limitations (please read)

* **Not a certified enforcement system.** Human review is mandatory; outputs are
  evidence packages for your officers, not offences.
* **Pothole/garbage detection is not included.** The interface exists; the
  models do not. The UI says so explicitly.
* **Annotated video export uses the `mp4v` codec** because the packaged build
  environment had no H.264 encoder. macOS QuickTime may refuse `mp4v`; the
  bundled GIF preview and snapshots always play, and the annotated MP4 opens in
  VLC/Chrome. Install an H.264 encoder (or open the file in a converter) if you
  need QuickTime playback of the annotation overlay.
* **RTSP/H.264 live cameras** are supported through OpenCV/FFmpeg, but could not
  be exercised in the delivery sandbox (no camera, no network access). Test with
  your own authorised camera; see the troubleshooting guide.
* **MPS acceleration** is implemented and selected automatically, but the build
  sandbox is Intel/Linux, so MPS was verified through code paths and a CPU
  fallback, not on an Apple GPU. `tools/diagnostics.py` reports the real device
  on your Mac in one line.
* **Single-operator by design.** One local SQLite database, no accounts, no
  multi-user sync. Share exports, not the database.
* **Very long recordings** are processed as fast as the CPU/GPU allows; a
  3-minute 1080p clip takes minutes, not seconds. Sampling (every 5th frame by
  default) is the main speed lever.
* The bundled `samples/demo-street-traffic.avi` is sample data from the OpenCV
  project, suitable for a demo only — not for any enforcement decision.

---

## Troubleshooting

Start here: **Settings → Diagnostics**, or run

```bash
.venv/bin/python tools/diagnostics.py
```

It checks platform, Python, packages, device, models, database, storage,
frontend build and runs a real inference. The full guide, with fixes for the
common failures (port in use, Gatekeeper, model download, webcam permission,
RTSP timeouts, MPS falling back to CPU), is in
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

Logs: `data/logs/{setup,pip,npm,backend}.log`.

### Uninstall

Quit with `Stop CivicWatch.command`, then delete the project folder, or keep
your records and delete only `data/` (footage, evidence, database, credentials).
Nothing is written outside the project directory except the optional
`CivicWatch.app`.

---

## Licence

CivicWatch AI Studio is distributed under **AGPL-3.0**, because it uses
Ultralytics YOLO and its pretrained weights, which are AGPL-3.0. Municipalities
and integrators are free to use, modify and self-host it; if you offer it as a
network service, the AGPL requires you to publish your modifications.
Commercial closed-source redistribution requires an Ultralytics Enterprise
licence. Details and third-party attributions: `docs/PRIVACY_AND_LEGAL.md`.
