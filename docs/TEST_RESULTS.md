# Test results

Recorded **2026-11-08**, from an actual run in the delivery environment. Nothing
below is estimated: commands, counts and timings are copied from the outputs.

## Environment

| | |
| --- | --- |
| Host | Linux 6.1 x86_64 (build sandbox) |
| Python | 3.11.2 (venv at `.venv`) |
| Node / npm | 22.22.3 / 10.9.8 |
| PyTorch | 2.15.0 (CPU build, `mps_available=False`) |
| Ultralytics | 8.4.208 |
| OpenCV | opencv-python-headless 5.0.0 |
| FastAPI / Uvicorn | 0.162.2 / 0.54.0 |
| Vitest / Vite | 3.2.7 / 6.4.5 |
| Hardware | 2 vCPU, no camera, no Apple GPU |

The sandbox is **not** macOS and has no camera, RTSP access or Metal GPU, so
Apple-specific behaviour is verified by unit tests, by static/shell checks and
by `tools/diagnostics.py` on the target machine — never assumed. See
[Limitations](#what-could-not-be-verified-here).

## Backend — `pytest`

```
cd backend && ../.venv/bin/python -m pytest
...
70 passed, 1 warning in 10.57s
```

The single warning is Starlette's deprecation notice about `httpx` in
`TestClient` (third-party), not a CivicWatch defect.

| Module | Tests | Covers |
| --- | --- | --- |
| `test_geometry.py` | 5 | polygon validation, normalised masks, point-in-polygon, box anchor/scale |
| `test_tracker.py` | 6 | Kalman predict/update, ID continuity, lost-track buffer, IoU distance, class-aware association |
| `test_rules.py` | 9 | dwell accumulation, stationary test, overlap vs anchor containment, double parking, footpath, restricted entry, cooldown, severity |
| `test_evidence.py` | 3 | snapshot/clip/preview writing, JPEG validity, path layout, person blur path |
| `test_incident_workflow.py` | 8 | status transitions + validation, notes, audit trail, filters/search, CSV/PDF export, delete cleanup |
| `test_jobs_pipeline.py` | 9 | upload → zone → job → evidence → export → delete, progress/frame counters, stub detector, annotated output, deletion of a running job |
| `test_cameras_security.py` | 10 | Fernet round-trip, key permissions (600), redaction filter, loopback middleware, credential masking, authorisation requirement |
| `test_settings_zones_system.py` | 10 | settings validation/coercion/reset, zone CRUD and defaults, overview counters, storage report, diagnostics, model catalogue, forced weight re-download |
| `test_launchers.py` | 10 | every `.command` parses (`bash -n`) and is executable, shared helpers present, Setup covers all prerequisites, browser opens only after health, diagnostics JSON structure, model check fails cleanly |

Test design notes:

* `conftest.py` points `CIVICWATCH_DATA_DIR` at a session temp directory and
  truncates every table between tests, so the suite never touches `data/`.
* `test_jobs_pipeline.py` monkeypatches `civicwatch.detect.registry.get_detector`
  with a deterministic stub, so the API/pipeline are tested without a GPU or a
  model download; the real model path is exercised separately (below).
* `test_launchers.py` runs `tools/diagnostics.py --json --quick` as a subprocess
  against a temp data dir, proving the tooling the installer depends on works.

## Frontend — `vitest`

```
cd frontend && npx vitest run
Test Files  3 passed (3)
     Tests  14 passed (14)
  Duration  2.59s
```

| Spec | Tests | Covers |
| --- | --- | --- |
| `format.test.ts` | 4 | durations (short/long), byte sizes, status/category label mapping, percentages and timestamps |
| `api.test.ts` | 4 | export URLs with filters, zone/stream URL construction, backend error surfacing, JSON bodies |
| `ui.test.tsx` | 6 | status badges and tones, progress bar width, toggle interaction, modal visibility, toasts |

## Type check and production build

```
cd frontend && npx tsc -b          # exit 0, no diagnostics
cd frontend && npx vite build
dist/index.html                   0.78 kB │ gzip:   0.48 kB
dist/assets/index-CZPid2PV.css   24.05 kB │ gzip:   5.79 kB
dist/assets/index-cz6N8CzW.js   397.89 kB │ gzip: 118.33 kB
```

The build is then served by FastAPI; the running instance returned `200` for
`/`, `/incidents` (SPA fallback), `/api/overview`, `/api/incidents` and the
hashed JS/CSS assets.

## Real end-to-end inference (no stubs)

Executed against `samples/demo-street-traffic.avi` (the OpenCV `vtest.avi` clip:
79.5 s, 768×576, 10 fps, 795 frames) with the real `yolov8n.pt` weights:

| Result | Value |
| --- | --- |
| Job | completed in ~14 s, 12 fps on 2 CPU cores with `sample_every=12` |
| Frames | 67 of 795 processed |
| Detections | 524 |
| Incidents | 4 (`INC-20261108-0001`…`0004`): 2 illegal parking (car, truck), 2 restricted-entry (person) |
| Annotated video | `data/processed/<job>.mp4`, duration 44.67 s |
| Evidence | snapshot 108 KB JPEG, preview GIF 793 KB, evidence clip 191 KB MP4 |
| Exports | CSV 2,037 B, PDF 5,938 B — both downloaded through the API |
| Visual check | an annotated frame was extracted and inspected: boxes, track IDs and zone outlines render correctly |

These artefacts are still present in `data/` for inspection; they are excluded
from version control.

## Diagnostics

`tools/diagnostics.py` on the sandbox reports:

```
platform  Linux 6.1 x86_64 (launchers target macOS)      warn
python    3.11.2, virtualenv                             ok
packages  torch 2.15.0, ultralytics 8.4.208, cv2 5.0.0 … ok
hardware  resolved=cpu, mps_available=False, threads=1   ok
models    installed: yolov8n (validated, 80 classes)     ok
database  11 tables, migrations applied                  ok
storage   9.0 GB free, writable                          ok
frontend  dist/index.html + bundle present               ok
samples   1 sample clip                                  ok
port/server running CivicWatch AI Studio 1.0.0           ok
inference yolov8n on cpu · 110 ms/frame · JPEG encoded   ok
```

## Shell launchers

* `bash -n` passes for all four `.command` files and `launchers/lib/common.sh`
  (also enforced by `test_launchers.py`).
* `chmod +x` verified by test (`os.access(..., os.X_OK)`).
* `tools/dev.sh` syntax-checked; it is not executed here because it would
  duplicate the running server.

## What could not be verified here

| Item | Why | How to verify on your Mac |
| --- | --- | --- |
| Apple MPS acceleration | sandbox is Intel/Linux, `torch.backends.mps.is_built()` is false | `.venv/bin/python tools/diagnostics.py` → `hardware` line must say `mps_available=True`; Settings → Device shows `mps`; Overview reports the same device during a job |
| Double-click launcher flow | requires Finder/Terminal on macOS | run `./Setup CivicWatch.command` once, then `./Start CivicWatch.command`; both print each step and write `data/logs/*.log` |
| Webcam capture | no camera device | Live Monitoring → add webcam → **Test connection** → Start; macOS asks for Camera permission |
| RTSP ingestion | no camera on the network | add the authorised URL, **Test connection**, then start; see TROUBLESHOOTING for decoder caveats |
| H.264 annotated export | no H.264 encoder in this image (`mp4v` used) | on macOS, replace `mp4v` with `avc1` in `pipeline/annotate.py` if `cv2.VideoWriter` supports it, or transcode with ffmpeg |
| macOS `.app` bundle | requires `sips`/`iconutil`/`codesign` | run `Create CivicWatch App.command`, then double-click `CivicWatch.app` |

## Reproducing

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest

# frontend
cd frontend && npm ci && npx vitest run && npx tsc -b && npx vite build

# environment
.venv/bin/python tools/diagnostics.py
```
