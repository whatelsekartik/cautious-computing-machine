# Privacy, security and licensing

This document describes what CivicWatch actually does with data, what it cannot
do, and the licences attached to it. It is engineering documentation, **not legal
advice**; deployments handling public space footage need sign-off from the
municipality's legal officer.

## 1. What stays on the machine

| Data | Where it lives | Leaves the Mac? |
| --- | --- | --- |
| Uploaded video files | `data/media/` | No |
| Annotated output video | `data/processed/` | No |
| Incident snapshots, clips, GIF previews | `data/evidence/<INCIDENT-ID>/` | No |
| SQLite database (incidents, zones, settings, audit log) | `data/civicwatch.db` | No |
| Camera credentials (encrypted) | `data/secrets/credentials.key` + DB | No |
| Exports (CSV/PDF) | `data/exports/` | Only when you send them |
| Model weights | `data/models/` | Downloaded once from Ultralytics' official release |

There is no analytics, no crash reporting, no update ping, no account, no API
key and no cloud inference. The only network traffic CivicWatch generates is
(a) setup downloads (Python/Node packages, model weights) and (b) the RTSP/HTTP
streams **you** configure. Both stop being necessary after setup.

The server binds to `127.0.0.1`. `LocalhostOnlyMiddleware` independently rejects
requests from non-loopback peers, so binding is not the only control.

## 2. Personal data safeguards built in

* **No face recognition, no gait or licence-plate recognition, no biometric
  templates.** Detections are limited to vehicle classes and “person” boxes.
  CivicWatch does not identify individuals.
* **Optional person blurring** (`blur_persons`) softens person regions in
  evidence frames before they are written.
* **Evidence minimisation** — only the snapshot, a short pre/post clip and a
  preview GIF are stored, not the whole source video by default; annotated
  processing output can be disabled entirely.
* **Retention enforcement** — `media_retention_days` (14) and
  `evidence_retention_days` (30) purge old artefacts; Settings exposes a manual
  “purge evidence” action and a storage breakdown.
* **Human decision required** — automated output is labelled
  *potential violation*; nothing is auto-issued. Status transitions
  (`Detected → Pending review → Verified → Forwarded → Resolved`, or
  `Dismissed`) are recorded with actor, timestamp and note in
  `incident_events`, and mirrored in `audit_log`.
* **Credential hygiene** — camera URLs are Fernet-encrypted with a locally
  generated key (mode 600); API responses and logs show `rtsp://host/***`. A
  logging filter redacts URL userinfo even if a library logs it directly.
* **No credential input from code** — nothing is read from committed files;
  `.env`, `*.key`, `data/` and `*.db` are git-ignored.

## 3. Deploying on public roads — what the operator must do

Municipal CCTV is not automatically lawful to process just because the cameras
are public. Before using CivicWatch on live footage, the deploying body should:

1. **Confirm authority** — a documented purpose, legal basis and owner for each
   camera, plus written authorisation for the analyst accounts using the tool.
2. **Signal the area** — visible signage that automated video analytics may
   produce potential violation records, and who to contact.
3. **Data-minimise** — restrict traffic monitoring to road-facing views, mask
   adjacent private property where possible, and keep retention at the minimum
   that supports the review process.
4. **Limit access** — one local machine, disk encryption (FileVault) on, a
   screen lock, and exports stored in access-controlled folders.
5. **Review before action** — a human must open the evidence, confirm vehicle
   and location, and only then forward a case. Dismiss false positives with a
   note; that record is part of your accountability trail.
6. **Handle requests** — keep a contact for access/correction requests and an
   erasure path (delete incident + evidence + media). CivicWatch supports
   deleting incidents and purging evidence, media and processed video.
7. **Check local law** — in India, for example, the Digital Personal Data
   Protection Act 2023 treats video that identifies individuals as personal
   data, with obligations around purpose limitation, notice, security and
   retention; other jurisdictions add CCTV-specific rules. Confirm requirements
   with your legal officer and adapt retention/settings accordingly.

CivicWatch's own defaults (potential-violation labelling, 30-day evidence
retention, loopback-only API, credential encryption) are conservative starting
points, not a compliance certificate.

## 4. Threat model and non-goals

Covered:

* Remote access to the API — blocked by loopback middleware.
* Credential leakage in API responses and logs — masked and filtered.
* Path traversal through evidence endpoints — names resolved inside the data
  directory only.
* Silent model substitution — downloads validated by loading the checkpoint and
  checking class names; custom models are explicit operator actions and logged.
* Unbounded memory growth from job runners — runners pruned after completion.

Explicit non-goals:

* Protection against a local administrator who can read the disk (encrypt the
  disk instead).
* Multi-tenant access control, role separation or officer accounts — the tool is
  single-operator by design.
* Tamper-evident evidence (no block-chain/hashing chain). If you need forensic
  provenance, hash the exported PDF/CSV and store the hash in your register.
* Replacing a formal evidence-management or challan system. CivicWatch prepares
  a package; it does not adjudicate.

## 5. Licensing

### CivicWatch AI Studio

Distributed under **GNU Affero General Public License v3.0** (see `LICENSE`),
because the project depends on AGPL-licensed components (below). In practice:

* You may use, modify and self-host it, including commercially, without paying.
* If you distribute it, or offer its functionality to others over a network, you
  must make the corresponding source available under the AGPL.
* Closed-source redistribution or embedding in a proprietary product requires
  either compliance with the AGPL or a commercial licence for the underlying
  Ultralytics components.

### Ultralytics YOLO (package and pretrained weights)

* Licence: **AGPL-3.0** for both the `ultralytics` Python package and the
  `yolov8n.pt` / `yolov8s.pt` / `yolo11n.pt` weights downloaded at setup.
* Source: <https://github.com/ultralytics/ultralytics>
* Commercial closed-source use requires an Ultralytics Enterprise Licence.
* Model downloads come from the official release host
  (`github.com/ultralytics/assets/releases`), and each file is validated locally
  before use. CivicWatch stores the SHA-256 in diagnostics output so you can
  record exactly which weights were used.

### Third-party libraries

| Component | Licence |
| --- | --- |
| PyTorch, torchvision | BSD-3-Clause |
| OpenCV (`opencv-python-headless`) | Apache-2.0 |
| FastAPI, Uvicorn, Starlette | MIT / BSD-3-Clause |
| NumPy | BSD-3-Clause |
| ReportLab | BSD-3-Clause |
| cryptography | Apache-2.0 / BSD-3-Clause |
| psutil | BSD-3-Clause |
| React, React Router, Vite, Tailwind CSS, Lucide | MIT / ISC / Apache-2.0 (Lucide: ISC) |
| ByteTrack algorithm | implementation written for this project, following the published method (MIT-licensed reference implementation); no code copied |

Check each project's repository for the authoritative licence text before
redistribution.

### Sample media

`samples/demo-street-traffic.avi` is `vtest.avi` from the OpenCV project's
sample data (the `opencv_extra` repository, distributed under Apache-2.0),
originally captured for pedestrian-tracking research. It contains members of the
public; treat it as demonstration material only — do not use it for enforcement,
training a commercial model, or as a template for consent. Replace it with your
own authorised footage for operational demos.

## 6. Reporting a concern

If you find a security or privacy defect in CivicWatch, do not attach footage.
Describe the issue, the version (`GET /api/health`) and the affected endpoint,
and report it to whoever maintains your deployment.
