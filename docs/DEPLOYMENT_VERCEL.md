# Deploying CivicWatch to Vercel (hybrid mode)

CivicWatch runs in two modes from one codebase:

| | **A. Standalone local** | **B. Cloud (hybrid)** |
| --- | --- | --- |
| Where it runs | The operator's Mac, `127.0.0.1:8000` | Vercel (dashboard + API) + a connector on a Mac |
| Inference | YOLO + ByteTrack + OpenCV + PyTorch, locally | YOLO + ByteTrack + OpenCV + PyTorch, **on the connector** |
| Database | SQLite in `data/` | PostgreSQL (Supabase, Neon, RDS, …) |
| Evidence | Local files in `data/evidence/` | Private S3-compatible bucket with presigned URLs |
| Live cameras | Webcam / RTSP, on the Mac | On the connector Mac; the dashboard never sees a stream |
| Who can open it | Anyone at the Mac | Operators with `CIVICWATCH_ADMIN_TOKEN` |

**Vercel never runs inference.** Serverless functions handle JSON only: the
dashboard API, incident management, settings, connectors and short-lived
presigned-URL issuance. Video is uploaded by the browser directly to object
storage, and processed locally by the connector.

The local macOS app is untouched by this deployment: `Setup CivicWatch.command`,
`Start CivicWatch.command` and `Stop CivicWatch.command` keep working exactly as
before, and the same React bundle detects which mode it is talking to.

---

## 1. What gets deployed

```
Vercel
├── Static site            frontend/dist  (Vite build, SPA)
└── Python function        api/index.py → civicwatch.cloud.app:create_cloud_app()
    └── /api/*             dashboard, incidents, settings, exports, connector protocol
Supabase / any Postgres    incidents, connectors, jobs, audit log
S3 / R2 / Supabase Storage private evidence + uploaded videos
Connector (your Mac)       Start Cloud Connector.command → YOLO/ByteTrack, outbox, uploads
```

The function bundle contains only `backend/civicwatch/**` plus the packages in
`backend/requirements-cloud.txt` (FastAPI, pg8000, boto3, reportlab, Pillow).
There is **no** `torch`, `torchvision`, `opencv-python`, `numpy`, `ultralytics`
or `lap` in the cloud environment — verified by installing
`backend/requirements-cloud.txt` into a clean virtualenv and importing the app
in isolation (`tests` note below). Bundle sizes measured in this repository:

| Item | Size |
| --- | --- |
| Application code (`backend/civicwatch/**`) | ~1.3 MB |
| Production Python packages (installed) | ~60 MB (botocore + reportlab + Pillow dominate) |
| Frontend build | ~418 kB JS / ~25 kB CSS (~124 kB / 6 kB gzipped) |

Both are far below Vercel's 250 MB unzipped function limit.

### Files that matter

| Path | Purpose |
| --- | --- |
| `vercel.json` | builds, routes, regions, headers |
| `api/index.py` | ASGI entry point (Vercel's Python runtime speaks ASGI; no adapter needed) |
| `api/requirements.txt` | re-exports `backend/requirements-cloud.txt` |
| `backend/requirements-cloud.txt` | cloud-only dependency set |
| `backend/civicwatch/cloud/` | auth, API, service layer, app factory |
| `backend/civicwatch/connector/` | local worker, outbox, cloud client, CLI |
| `tools/cloud_migrate.py` | database status + connector provisioning |
| `tools/connector.py` | connector entry point used by the launcher |
| `.env.example` | every variable, grouped by concern |
| `.vercelignore` | keeps models, footage, venvs and tests out of the upload |

---

## 2. Prerequisites

* A Vercel account and the `vercel` CLI (`npm i -g vercel`).
* A PostgreSQL database that accepts public TLS connections. Supabase's
  "Session pooler" or "Direct connection" string works; set
  `sslmode=require` (or `verify-full`).
* An S3-compatible bucket that supports **presigned PUT/GET**:
  AWS S3, Cloudflare R2, Backblaze B2, MinIO, or Supabase Storage (S3
  compatibility). The bucket stays **private** — no public read policy.
* A Mac with the local install (for the connector).
* An `CIVICWATCH_ADMIN_TOKEN` you generate yourself, e.g.
  `python3 -c "import secrets; print(secrets.token_urlsafe(36))"`.

> **Why not Vercel Blob?** Vercel Blob issues client upload tokens, not
> S3-signature URLs, and the connector (a plain Python process) needs uniform
> presign semantics for PUT and GET. Supporting one S3-compatible API keeps
> local, R2, S3 and Supabase interchangeable. Vercel Blob can be added later
> behind the same `storage.py` interface.

---

## 3. Database

1. Create the database and copy its connection string:
   `postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require`.
2. Nothing else is required: migrations run automatically on the first request
   (cold start) and are idempotent (`schema_migrations`).
3. Verify from your Mac:

```bash
DATABASE_URL='postgresql://…' PYTHONPATH=backend \
  .venv/bin/python tools/cloud_migrate.py --status
```

Expected output: `applied: [1, 2]` (or `cached`), the table list, and
`backend: postgres`. The Postgres schema mirrors SQLite exactly, including the
columns added in migration 2 (`client_incident_id`, `snapshot_key`, `clip_key`,
`preview_key`, `origin`, `connector_id`, connector tables).

---

## 4. Object storage

Create a private bucket, then configure CORS so the browser can PUT directly:

```json
[
  {
    "AllowedOrigins": ["https://your-project.vercel.app"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

Notes:

* Evidence and videos are **private**. The dashboard fetches them through the
  API, which answers `302` to a presigned URL valid for 15 minutes (snapshot,
  clip, preview) or 60 minutes (annotated video).
* The connector downloads assignments with a presigned GET. Its storage client
  deliberately carries **no** bearer token, so the connector token can never
  leak to a storage host.
* Rotation, lifecycle rules and versioning are up to your provider; CivicWatch
  writes under `evidence/`, `media/` and `processed/`.

---

## 5. Environment variables

Copy `.env.example` and fill it in. Minimum for a working deployment:

| Variable | Example | Required |
| --- | --- | --- |
| `CIVICWATCH_MODE` | `cloud` | yes |
| `DATABASE_URL` | `postgresql://…?sslmode=require` | yes |
| `CIVICWATCH_ADMIN_TOKEN` | random 40+ chars | yes |
| `CIVICWATCH_S3_BUCKET` | `civicwatch-evidence` | yes |
| `CIVICWATCH_S3_REGION` | `ap-south-1` | yes (or provider default) |
| `CIVICWATCH_S3_ACCESS_KEY_ID` / `…_SECRET_ACCESS_KEY` | provider keys | yes |
| `CIVICWATCH_S3_ENDPOINT` | `https://xxxx.r2.cloudflarestorage.com` | for R2/B2/MinIO/Supabase |
| `CIVICWATCH_S3_FORCE_PATH_STYLE` | `1` | for MinIO/some providers |
| `CIVICWATCH_CORS_ORIGINS` | `https://your-project.vercel.app` | if the API is called cross-origin |

Local connector variables (`CIVICWATCH_CLOUD_URL`, `CIVICWATCH_CONNECTOR_TOKEN`,
`CIVICWATCH_CONNECTOR_INTERVAL`) belong on the **Mac**, not on Vercel.

Set them with the CLI:

```bash
vercel env add CIVICWATCH_MODE production
vercel env add DATABASE_URL production
vercel env add CIVICWATCH_ADMIN_TOKEN production
# …repeat for each variable, then:
vercel env ls
```

Generate strong values:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(36))"   # admin token
```

---

## 6. Deploy

```bash
vercel link
vercel pull --yes --environment=production   # writes .vercel/.env.production.local
vercel build --prod
vercel deploy --prebuilt --prod
```

Or connect the Git repository and let Vercel build on push. `vercel.json` uses:

* `@vercel/python` on `api/index.py`, `maxDuration: 60`, `memory: 1024`,
  `includeFiles: ["backend/civicwatch/**"]`;
* `@vercel/static-build` on `frontend/package.json` with `distDir: dist`;
* routes `/api/*` → the function, `/assets/*` and static extensions served
  directly, everything else → `frontend/index.html` (SPA fallback);
* `regions: ["bom1"]` — change this to the region closest to your database;
* `Cache-Control: no-store` on `/api/*`, immutable caching on `/assets/*`.

The static routes follow Vercel's legacy monorepo pattern (`@vercel/static-build`
on a subdirectory project, `dest: /frontend/...`). If your project serves the
build from the root instead and assets 404, replace the last three `routes`
entries with `rewrites`: keep `{"source": "/api/(.*)", "destination": "/api/index.py"}`,
add `{"source": "/assets/(.*)", "destination": "/assets/$1"}`, and finish with
`{"source": "/(.*)", "destination": "/index.html"}`. Verify with
`curl -I https://your-project.vercel.app/assets/<hashed-file>.js`.

Sanity-check the mapping locally before deploying:

```bash
python3 -c "import json;json.load(open('vercel.json'));print('vercel.json ok')"
```

### Post-deploy checks

```bash
BASE=https://your-project.vercel.app
curl -s $BASE/api/health                 # {"status":"ok","database":"reachable",…}
curl -s $BASE/api/cloud/config | head -c 400
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/incidents        # 401 without a session
curl -s -X POST $BASE/api/auth/session \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$CIVICWATCH_ADMIN_TOKEN\"}" -c cookies.txt      # 200
curl -s -b cookies.txt $BASE/api/incidents                          # 200
```

Then open the site, sign in with the admin token, and confirm the sidebar says
**VERCEL** and **polling**.

---

## 7. Pair a connector

1. On the dashboard: **Settings → Local connectors → Create token**. Copy the
   token — it is shown exactly once (only its SHA-256 hash is stored).
2. On the Mac with the local install, either:
   * double-click **`Start Cloud Connector.command`** (prompts for the URL and
     token, saves them to `config/connector.env` mode 600 — git-ignored), or
   * run it manually:

```bash
cd /path/to/cautious-computing-machine
.venv/bin/python tools/connector.py \
  --cloud-url https://your-project.vercel.app \
  --token cwconn_… \
  --interval 20
```

Useful flags: `--status` (print outbox/queue state and exit), `--once`
(single tick, for cron), `--no-assignments`, `--test` (auth + health only),
`--verbose`.

What the connector does every tick:

1. heartbeat with device, capabilities and **installed** models only;
2. enqueue newly created local incidents into a durable SQLite outbox;
3. drain the outbox with exponential backoff (5s, 15s, 45s, 120s, 300s, 900s),
   marking sent/duplicate/failed and purging sent rows after 14 days;
4. claim at most a few queued cloud assignments, download the video, run the
   local pipeline, upload the annotated video and push the incidents.

The connector always keeps its own local SQLite store and refuses to start if
the configured backend is not SQLite — a connector is not a cloud process.

### Offline behaviour

* Network loss or 5xx → `CloudUnavailable`: the row stays queued, `attempts`
  increments, `next_attempt_at` backs off, and the process keeps running.
* 4xx / revoked token → `CloudRejected`: logged, marked failed, never retried
  blindly; the incident remains in the local database.
* The cloud deduplicates on `client_incident_id`, so a lost acknowledgement
  cannot create a second incident.

---

## 8. Uploading video in cloud mode

The dashboard's **Video uploads** page:

1. `POST /api/media/upload-url` → presigned PUT + `media_id` (function returns
   only JSON);
2. the browser PUTs the file **directly to storage** with XHR progress;
3. `POST /api/media/{id}/complete` verifies the object exists via HEAD;
4. `POST /api/jobs {media_id}` queues an assignment;
5. a connector claims it, processes locally, uploads `processed/{job_id}/annotated.mp4`
   via a presigned PUT and reports frame counts back.

Hard limits to design around (all enforced/documented in code):

| Limit | Value | Where |
| --- | --- | --- |
| JSON request body | 4 MB → HTTP 413 before parsing | `cloud/app.py` |
| Single upload | 4 GB | `service.create_upload` |
| Upload URL lifetime | 1 hour (PUT) | `create_upload` |
| Evidence URL lifetime | 15 minutes | `connector_config` / upload-url issues |
| Incidents per batch | 50 | `ingest_incidents` |
| Function timeout | 60 s | `vercel.json` |
| Connector poll / heartbeat | 20 s / 30 s | `connector_config` |
| Outbox retry cap | 15 min, stale alert at 8 attempts | `connector/outbox.py` |

---

## 9. Security model (cloud mode)

* **Dashboard auth**: `CIVICWATCH_ADMIN_TOKEN` is exchanged for an HMAC-signed,
  HttpOnly, `SameSite=Strict` cookie (12 h; `Secure` on HTTPS). Every
  cookie-authenticated write additionally requires the
  `X-CivicWatch-CSRF: 1` header, which cross-site forms cannot set. Bearer
  requests (scripts, connector) are exempt from the CSRF header.
* **Connector auth**: `Authorization: Bearer cwconn_…`, tokens stored as
  SHA-256 hashes, revocable, shown once.
* **Evidence**: private bucket, short-lived signed URLs, `no-referrer`,
  `nosniff`, `X-Frame-Options: DENY` on API responses.
* **Credentials**: RTSP URLs are encrypted at rest on the connector Mac. The
  ingestion API rejects payloads containing `password`, `rtsp_url`,
  `username`, `credentials`, … and rejects camera labels that embed
  `user:pass@` — a rejected item is reported, never stored.
* **No fabricated output**: pothole and garbage categories have no weights in
  the repository; the dashboard shows them as *model required*, and the
  connector only reports models whose weight files exist on disk.

---

## 10. Verification performed in this repository

Run the full suite:

```bash
cd backend && ../.venv/bin/python -m pytest -q      # 107 tests
cd frontend && npx vitest run && npx tsc -b && npx vite build
```

Cloud-specific coverage (`backend/tests/`):

| Test file | What it proves |
| --- | --- |
| `test_cloud_storage.py` | `CIVICWATCH_STORAGE=local|s3` selection, presigned PUT/GET roundtrip against a real S3 endpoint, private buckets, no unsigned URLs |
| `test_postgres_backend.py` | real PostgreSQL server: migrations 1–2, schema parity, ILIKE search, incident workflow, connector ingestion + dedupe, credential rejection, URL password masking |
| `test_cloud_api.py` | health/config, session cookie + CSRF, connector tokens shown once, model reporting (pothole stays unavailable), direct upload → queue → assignment claim, incident ingestion with evidence, 302 signed evidence access, CSV/PDF exports, 413 guard, security headers, corrupt-evidence PDF resilience |
| `test_connector_sync.py` | connector ↔ cloud over real HTTP: heartbeat, evidence upload, duplicate replay, offline buffering + backoff, revoked token, credentials never leaving the Mac, and a **full assignment processed by the local YOLO pipeline** |

Also verified manually in this repo:

* `backend/requirements-cloud.txt` installed into a clean venv → cloud app
  imports, `torch`/`cv2`/`numpy` absent, and the cloud + storage test suites
  (20 tests) pass there.
* The local app still constructs and serves `/api/cloud/config` (mode `local`),
  `/api/health`, `/api/overview`, `/api/settings`, `/api/jobs`, `/api/incidents`.

Not verified here (no network access to those providers): an actual Vercel
deployment, Supabase connectivity, and provider-specific S3 CORS. Follow the
checklist in §6 on first deployment.

---

## 11. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `/api/health` returns 503 `database: unreachable` | Wrong `DATABASE_URL`, missing `sslmode=require`, or the provider requires the pooler host/port. Test with `tools/cloud_migrate.py --status`. |
| Dashboard loads but every call is 401 | Session expired (12 h) or cookies blocked. Sign in again; ensure you open the `https://` URL, not a preview alias with a different host. |
| Write returns 403 `Missing X-CivicWatch-CSRF header` | You are calling the API with a cookie from a script. Send the header or use the bearer token. |
| Upload fails immediately with CORS error | Bucket CORS does not allow your Vercel origin / PUT method. Apply §4. |
| `complete` returns "file has not reached storage yet" | The PUT did not finish (or used a different content type). Retry the upload. |
| Connector logs `401` | Token revoked or copied incompletely. Create a new connector token; incidents already queued stay local and will sync. |
| Connector logs `CloudUnavailable` repeatedly | Wrong URL, offline, or the deployment is paused. Use `--test` for a minimal auth check. |
| Jobs stay `queued` | No connector is online. Start `Start Cloud Connector.command` and check **Settings → Local connectors**. |
| Pothole/waste never detects | Expected. Install real weights on the connector; they are never simulated. |
| `/api/*` returns 413 | Body over 4 MB. Use direct upload; do not POST video through functions. |
| Static site loads but `/assets/*.js` is 404 | Legacy `routes` mapping does not match your project layout. See the `rewrites` alternative in §6. |
| Dashboard shows "that page does not exist" after sign-in | The SPA fallback is not reached (a route intercepted `/`). Ensure the catch-all route/rewrite maps to the built `index.html`. |

---

## 12. Cost and scaling notes

* Cold starts dominate function latency; the app imports FastAPI, boto3 and
  reportlab only. Keep `regions` close to the database to avoid cross-region
  round-trips on every query.
* PostgreSQL: CivicWatch uses one connection per invocation (`pg8000`), no
  pooling. Use the provider's pooler (Supabase "Session pooler", Neon pooled
  URL) for traffic beyond a handful of users.
* Storage: evidence dominates. Tune `evidence_retention_days` and the
  connector's `store_clips`.
* Bandwidth: video goes browser → storage and storage → connector, never
  through Vercel.
* The connector Mac does the GPU/CPU work; adding a second Mac means creating a
  second connector token.
