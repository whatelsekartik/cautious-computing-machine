# Troubleshooting

Start with the built-in check — it inspects the real machine, not a guess:

```bash
.venv/bin/python tools/diagnostics.py
```

Or open the app and use **Settings → Diagnostics** (same checks, no Terminal).
Logs live in `data/logs/`:

| Log | Contents |
| --- | --- |
| `setup.log` | the full Setup run |
| `pip.log` | package installation |
| `npm.log` | frontend install/build |
| `backend.log` | server, jobs, camera sessions |
| `app.log` | output of the optional `CivicWatch.app` launcher |

Every launcher mirrors its output to a log, so a failed run can be diagnosed
after the window closes.

---

## Setup

### “Setup stopped early”

The window shows the failing step and the reason. The same text is in
`data/logs/setup.log`. Re-run Setup — completed steps (virtual environment,
packages, model, build) are skipped, so a retry costs seconds, not minutes.

### “Cannot reach pypi.org”

Setup needs the internet once. Check Wi-Fi/VPN/proxy, corporate TLS
interception (some proxies break `pip`) and retry. If your organisation requires
a proxy, export it before running Setup:

```bash
HTTPS_PROXY=http://proxy.local:8080 ./Setup\ CivicWatch.command
```

If the packages are already installed (for example on an air-gapped machine
prepared elsewhere), run `./Setup\ CivicWatch.command --offline` to verify
without touching the network.

### “Only N GB free”

PyTorch and the model need ~6 GB of headroom. Free space, or move the project to
a larger volume and re-run Setup.

### Python or Node problems

Setup prefers Python 3.10–3.13 and Node 20+ if they exist. If not, it downloads
private, checksum-verified builds into `tools/runtime/` — no admin password and
no PATH changes. To force a completely fresh environment:

```bash
rm -rf .venv tools/runtime
./Setup\ CivicWatch.command --force
```

`--force` also reinstalls Python packages and rebuilds the frontend.

### “Setup CivicWatch.command cannot be opened because it is from an unidentified developer”

macOS quarantines downloaded `.command` files. Either right-click the file →
**Open** → **Open**, or run:

```bash
xattr -d com.apple.quarantine "Setup CivicWatch.command"
chmod +x *.command
```

### Model download fails

`tools/download_models.py` retries three times, validates the checkpoint by
loading it, and refuses anything without COCO class names. If GitHub is blocked
by your network, download `yolov8n.pt` on a connected machine and copy it to
`data/models/yolov8n.pt`, then re-run Setup; the file will be validated in
place.

### Frontend build fails

Check `data/logs/npm.log`. Usually it is a stale `node_modules` or a Node
version below 20. Fix:

```bash
rm -rf frontend/node_modules frontend/dist
./Setup\ CivicWatch.command
```

---

## Starting and stopping

### “Port 8000 is already used by another application”

Something else is listening. Either quit it, or use another port:

```bash
CIVICWATCH_PORT=8010 ./Start\ CivicWatch.command
```

The launcher prints the process holding the port. Note that the developer Vite
server also expects the API on 8000 (`frontend/vite.config.ts`).

### “The server did not become ready within 90 seconds”

The launcher tails `backend.log`. Common causes:

* A stale process from a crashed run — `Stop CivicWatch.command`, then start.
* Insufficient memory — close other heavy apps; PyTorch needs ~1 GB resident.
* A half-installed environment — re-run Setup (it is idempotent).
* A firewall or end-point security tool blocking loopback connections on that
  port (rare; try another `CIVICWATCH_PORT`).

### The browser did not open

Open <http://127.0.0.1:8000> yourself; the server is running if
`Start CivicWatch.command` printed “CivicWatch is running”. `open` can be
blocked by some managed Macs — the launcher's health check already passed.

### “CivicWatch is not running”, but a process exists

The PID file may be stale. `Stop CivicWatch.command` also matches
`python … -m civicwatch` processes and whoever owns the loopback port, so run it
once and start again.

### Stopping takes about 15 seconds

The stop script sends SIGTERM, waits for jobs to write their final state, then
forces SIGKILL. Long recordings finish the current frame group before exiting;
your data is already committed.

---

## Inference

### MPS is available but CPU is used

Settings → Device should say `mps`. If not:

1. `tools/diagnostics.py` prints `mps_built` / `mps_available`. Both must be true.
2. A CPU-only PyTorch wheel was installed (common when reusing a Linux venv).
   Re-run `Setup CivicWatch.command --force`, which installs from macOS wheels.
3. macOS must be 12.3 or newer for Metal acceleration.
4. Set device preference to **Auto** (Settings) — it prefers MPS but will not
   fail if MPS cannot allocate memory.

CPU-only operation is fully supported; expect roughly 3–10× slower analysis on
the same clip.

### Analysis is slow

* Raise **frame sampling** (every 10th or 15th frame) — the single biggest lever.
* Lower **processing width** / image size to 640 or 512.
* Choose the nano model (`yolov8n`) instead of `yolov8s`.
* Close other GPU/CPU-heavy applications; look at Overview's fps figure to see
  what the current setting achieves.

### A job stays at 0% or “queued”

The runner starts within a second or two. If it does not, check
`backend.log` for a model loading error (for example a deleted weights file) and
re-run `tools/download_models.py`. Deleting a job while it runs is supported and
will stop its runner.

### Model says “not installed”

Settings → AI models → **Download**. A `pothole` or `garbage` category will
*always* say not installed until you add a model trained for it
(Settings → AI models → Install custom model). This is intentional: CivicWatch
does not pretend a vehicle detector can see potholes.

---

## Live monitoring

### Webcam shows nothing / permission prompt

macOS asks for camera permission for the process that opens the camera
(Terminal, or `CivicWatch` if launched from the app). Approve it in
**System Settings → Privacy & Security → Camera**. If you denied it, reset with
`tccutil reset Camera` and restart the launcher.

### RTSP camera fails to connect

* Confirm the camera is authorised for this use, and reachable:
  `ping <host>`; the stream URL usually looks like
  `rtsp://user:pass@192.168.1.20:554/Streaming/Channels/101`.
* Use **Test connection**; it reports resolution/fps or the decoder error
  without starting a session.
* Camera credentials are encrypted on save and never displayed again — if a
  password changed, edit the camera and re-enter it.
* Many cheap cameras allow only 1–2 concurrent streams; stop other viewers.
* H.265-only cameras may not decode with the bundled FFmpeg. Switch the camera
  to H.264 if possible.
* Live incidents need a zone whose `source_ref` matches that camera; zones drawn
  for an uploaded video do not apply to a live camera.

### The live view freezes after a while

The capture thread reconnects on read failure, but some cameras need a lower
sampling rate or a shorter TCP timeout. Stop and restart the session; check
`backend.log` for repeated `read failed` lines to confirm it is the camera.

---

## Evidence and exports

### QuickTime will not open the annotated video

The build environment could not encode H.264, so annotated output uses `mp4v`.
VLC, Chrome and Firefox play it; QuickTime may not. The evidence snapshot and
preview GIF always work. To get a QuickTime-compatible file:

```bash
# if you have ffmpeg installed
ffmpeg -i data/processed/<job>.mp4 -c:v libx264 -pix_fmt yuv420p out.mp4
```

### PDF export is empty or missing rows

The PDF mirrors the **active filters** in the Incidents page. Clear filters,
check the date range, then export again. With no incidents the export returns an
explanatory PDF rather than a blank one.

### Retention deleted something I needed

Retention is enforced at startup and through Settings → storage cleanup.
Defaults are 14 days for media and 30 days for evidence. Export or copy the
evidence you need; increase `evidence_retention_days` if your process requires a
longer window, and record that decision.

---

## Data, backups and reset

| Goal | How |
| --- | --- |
| Back up everything | Quit CivicWatch, copy `data/` (contains the DB, footage, evidence, key) |
| Keep records, drop footage | `Stop CivicWatch.command`, then delete `data/media/` and `data/processed/` |
| Start clean | Delete `data/`, then start CivicWatch — schema and defaults are recreated |
| Move storage elsewhere | Set `CIVICWATCH_DATA_DIR` (see `config/civicwatch.example.env`) |

Deleting `data/secrets/credentials.key` invalidates saved camera passwords: they
must be re-entered. The database itself is fine.

---

## Verification notes for this build

The delivery sandbox is Linux x86_64 without a camera, RTSP network access or
Apple GPU. Consequently:

* MPS selection, webcam capture and RTSP ingestion were **not** executed here.
  The code paths are covered by unit tests (device resolution, camera
  credential handling, capture error handling) and are verified on a Mac by
  `tools/diagnostics.py` (device line) and by **Test connection** in Live
  Monitoring.
* The macOS launchers were validated with `bash -n` (syntax), an automated
  launcher test suite, and the shared Python tooling was executed end to end. A
  real double-click run needs a Mac.
* No H.264 encoder was available, hence the documented `mp4v` limitation for
  annotated video.


---

## Cloud (Vercel) mode

The full guide is [DEPLOYMENT_VERCEL.md](DEPLOYMENT_VERCEL.md). The three failures
seen most often:

| Symptom | Fix |
| --- | --- |
| `/api/health` → 503 `database: unreachable` | `DATABASE_URL` is wrong or TLS is not enabled. Try `tools/cloud_migrate.py --status` locally with the same URL; Supabase URLs usually need `?sslmode=require` and the pooler host/port. |
| Upload fails instantly in the browser with a CORS error | The bucket's CORS policy does not permit your Vercel origin and `PUT`. Apply the policy in the deployment guide; no redeploy is needed. |
| Jobs stay `queued` and no incidents arrive | No connector is online. Run `Start Cloud Connector.command` on the Mac, or `tools/connector.py --test` to check the URL and token. Check **Settings → Local connectors** for `online`. |

Remember that camera capture, the zone editor and model installation are
local-only; in cloud mode those pages explain the split instead of failing.
