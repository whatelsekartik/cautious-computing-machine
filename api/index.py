"""Vercel serverless entry point for the CivicWatch cloud API.

Only metadata, incidents, settings and evidence URLs are handled here. There is
no PyTorch/OpenCV import in this bundle — inference runs on the local
connector. The dashboard itself is built from ``frontend/`` as static assets.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Cloud behaviour by default; explicit env vars still win on Vercel.
os.environ.setdefault("CIVICWATCH_MODE", "cloud")

from civicwatch.cloud.app import create_cloud_app  # noqa: E402  (path set above)

app = create_cloud_app()
