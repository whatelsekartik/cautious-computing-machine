#!/usr/bin/env python3
"""CivicWatch installation diagnostics.

Checks every prerequisite the app needs at runtime, runs a real inference
smoke test, and exits non-zero if a required check fails. Setup runs this
before declaring success; Start runs the fast subset; support can run it by
hand from Terminal.

    python tools/diagnostics.py            # full report
    python tools/diagnostics.py --json     # machine-readable
    python tools/diagnostics.py --quick    # skip the inference smoke test
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    required: bool = True
    data: dict = field(default_factory=dict)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    started: str = ""

    def add(self, name: str, status: str, detail: str, required: bool = True, **data) -> None:
        self.checks.append(Check(name, status, detail, required, data))

    def run(self, name: str, fn, required: bool = True) -> None:
        try:
            detail, status, data = fn()
        except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
            self.add(name, FAIL if required else WARN, f"{type(exc).__name__}: {exc}", required)
            return
        self.add(name, status, detail, required, **(data or {}))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL and c.required]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    def ok(self) -> bool:
        return not self.failures


SYMBOL = {OK: "✓", WARN: "!", FAIL: "✗"}
STYLE = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", "": ""}
RESET = "\033[0m"


def styled(symbol: str, status: str) -> str:
    if not sys.stdout.isatty():
        return symbol
    return f"{STYLE.get(status, '')}{symbol}{RESET}"


# ---------------------------------------------------------------------------
# Individual checks. Each returns (detail, status, extra-data).
# ---------------------------------------------------------------------------


def check_platform() -> tuple[str, str, dict]:
    system = platform.system()
    release = platform.release()
    machine = platform.machine()
    detail = f"{system} {release} · {machine} · Python {platform.python_version()}"
    data = {"system": system, "release": release, "machine": machine}
    if system != "Darwin":
        return detail + " (launchers target macOS)", WARN, data
    try:
        major = int(platform.mac_ver()[0].split(".")[0]) if platform.mac_ver()[0] else 0
    except ValueError:
        major = 0
    data["macos_major"] = major
    if major and major < 12:
        return detail + " — macOS 12 or newer is required", FAIL, data
    if machine not in {"arm64", "x86_64"}:
        return detail + " — unsupported architecture", FAIL, data
    return detail, OK, data


def check_python() -> tuple[str, str, dict]:
    info = sys.version_info
    detail = f"Python {info.major}.{info.minor}.{info.micro} at {sys.executable}"
    if not (3, 10) <= (info.major, info.minor) <= (3, 13):
        return detail + " — version 3.10–3.13 is required", FAIL, {}
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if not in_venv:
        return detail + " (not a virtual environment)", WARN, {"executable": sys.executable}
    return detail, OK, {"executable": sys.executable}


def check_packages() -> tuple[str, str, dict]:
    required = {
        "torch": "torch",
        "torchvision": "torchvision",
        "ultralytics": "ultralytics",
        "cv2": "opencv-python-headless",
        "numpy": "numpy",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "reportlab": "reportlab",
        "cryptography": "cryptography",
        "psutil": "psutil",
        "PIL": "pillow",
        "httpx": "httpx",
    }
    missing, versions = [], {}
    for module, package in required.items():
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{package} ({type(exc).__name__})")
            continue
        versions[package] = getattr(mod, "__version__", "unknown")
    if missing:
        return "missing: " + ", ".join(missing), FAIL, {"versions": versions}
    detail = ", ".join(f"{k} {v}" for k, v in versions.items())
    return detail, OK, {"versions": versions}


def check_device() -> tuple[str, str, dict]:
    import torch

    from civicwatch.detect.device import resolve_device, torch_status

    device = resolve_device("auto")
    status = torch_status()
    data = {"resolved": device.as_dict(), "torch": status}
    detail = (
        f"resolved={device.device} · mps_built={status.get('mps_built')} · "
        f"mps_available={status.get('mps_available')} · threads={torch.get_num_threads()}"
    )
    if status.get("mps_available"):
        return detail + " — Apple GPU acceleration active", OK, data
    if platform.machine() == "arm64" and platform.system() == "Darwin":
        return detail + " — MPS is unavailable; running on CPU", WARN, data
    return detail + " — CPU inference", OK, data


def _settings():
    from civicwatch import settings_store

    return settings_store.get_settings()


def _default_model_id() -> str:
    try:
        value = _settings()
        model_id = value.get("model_id") if isinstance(value, dict) else None
        return model_id or os.environ.get("CIVICWATCH_DEFAULT_MODEL", "yolov8n")
    except Exception:  # noqa: BLE001 - fall back to the shipped default
        return "yolov8n"


def check_models() -> tuple[str, str, dict]:
    from civicwatch.detect import registry

    installed, invalid = [], []
    for entry in registry.list_builtin_models():
        path = registry.weights_path(entry["id"])
        if not path.exists():
            continue
        try:
            registry.validate_weights(path)
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{entry['id']}: {exc}")
        else:
            installed.append(entry["id"])
    data = {"installed": installed, "invalid": invalid, "model_dir": str(registry.model_dir())}
    if invalid:
        return "invalid weights — " + "; ".join(invalid), FAIL, data
    if not installed:
        return f"no weights in {registry.model_dir()} — run Setup or Settings → AI models", FAIL, data
    default = _default_model_id()
    if default not in installed:
        return f"installed: {', '.join(installed)} (configured model '{default}' is missing)", WARN, data
    return f"installed: {', '.join(installed)} · default={default}", OK, data


def check_database() -> tuple[str, str, dict]:
    from civicwatch import db
    from civicwatch.config import get_paths

    applied = db.init_db()
    path = get_paths().db
    tables = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    expected = {"incidents", "jobs", "zones", "media", "settings", "cameras"}
    missing = expected - tables
    data = {"path": str(path), "tables": sorted(tables), "newly_applied": applied}
    if missing:
        return f"database at {path} is missing tables: {sorted(missing)}", FAIL, data
    incidents = db.query_one("SELECT COUNT(*) AS n FROM incidents")["n"]
    detail = f"{path} · {len(tables)} tables · {incidents} incidents stored"
    if applied:
        detail += f" · migrations applied: {applied}"
    return detail, OK, data


def check_storage() -> tuple[str, str, dict]:
    from civicwatch.config import get_paths

    paths = get_paths()
    paths.ensure()
    probe = paths.data / ".write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    usage = shutil.disk_usage(paths.data)
    free_gb = usage.free / 1024**3
    data = {"data_dir": str(paths.data), "free_gb": round(free_gb, 1)}
    if free_gb < 2:
        return f"{paths.data} has only {free_gb:.1f} GB free", FAIL, data
    return f"{paths.data} · {free_gb:.1f} GB free", OK, data


def check_frontend() -> tuple[str, str, dict]:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.exists():
        return "frontend/dist is missing — run Setup (it builds the interface)", FAIL, {}
    html = dist.read_text(encoding="utf-8", errors="replace")
    assets = dist.parent / "assets"
    scripts = [a for a in assets.glob("*.js")] if assets.exists() else []
    if not scripts:
        return "frontend build has no JavaScript bundle — re-run Setup", FAIL, {}
    size_kb = sum(p.stat().st_size for p in assets.iterdir()) / 1024
    return f"{dist} · {len(scripts)} bundle(s) · {size_kb:.0f} KB assets", OK, {
        "dist": str(dist),
        "mentions_app": "<div id=\"root\">" in html,
    }


def check_sample_media() -> tuple[str, str, dict]:
    samples = sorted((ROOT / "samples").glob("*"))
    videos = [p for p in samples if p.suffix.lower() in {".avi", ".mp4", ".mov", ".mkv"}]
    if not videos:
        return "no sample footage (upload your own video to analyse)", WARN, {}
    total_mb = sum(p.stat().st_size for p in videos) / 1024**2
    return f"{len(videos)} sample clip(s) · {total_mb:.1f} MB", OK, {
        "files": [p.name for p in videos]
    }


def check_port() -> tuple[str, str, dict]:
    from civicwatch.config import port

    target = port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        in_use = sock.connect_ex(("127.0.0.1", target)) == 0
    if in_use:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{target}/api/health", timeout=2
            ) as response:
                payload = json.load(response)
        except Exception:  # noqa: BLE001 - occupied by an unrelated process
            return (
                f"port {target} is in use by another app — stop it or set CIVICWATCH_PORT"
            ), WARN, {"port": target, "in_use": True}
        if str(payload.get("app", "")).startswith("CivicWatch"):
            return f"port {target} is in use by the running CivicWatch app", OK, {
                "port": target, "in_use": True, "civicwatch": True
            }
        return f"port {target} is serving a different app", WARN, {"port": target}
    return f"port {target} is free", OK, {"port": target}


def check_server() -> tuple[str, str, dict]:
    from civicwatch.config import port

    url = f"http://127.0.0.1:{port()}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return "not running (this is fine when the app is closed)", OK, {"running": False}
    detail = (
        f"running · {payload.get('app', 'CivicWatch')} {payload.get('version', '?')} "
        f"on port {port()}"
    )
    return detail, OK, {"running": True, "payload": payload}


def check_smoke() -> tuple[str, str, dict]:
    """Run a real inference, a tracker step and an annotation on a synthetic frame."""
    import numpy as np

    from civicwatch.detect import registry
    from civicwatch.pipeline import annotate
    from civicwatch.tracking.byte_track import BYTETracker

    model_id = _default_model_id()
    if model_id not in [m["id"] for m in registry.list_builtin_models()]:
        model_id = "yolov8n"
    from civicwatch.config import get_paths

    if not (get_paths().models / f"{model_id}.pt").exists():
        candidates = [m["id"] for m in registry.list_builtin_models()
                      if registry.weights_path(m["id"]).exists()]
        if not candidates:
            return "no weights available for the smoke test", FAIL, {}
        model_id = candidates[0]

    from civicwatch.detect.device import resolve_device

    device = resolve_device("auto")
    detector = registry.get_detector(model_id, device.device, 0.25, 0.7, 640)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[200:400, 200:420] = (60, 60, 62)
    started = time.perf_counter()
    detections = detector.predict(frame)
    inference_ms = (time.perf_counter() - started) * 1000

    tracker = BYTETracker()
    tracks = tracker.update(detections, 0)
    annotated = frame.copy()
    annotate.draw_hud(annotated, ["CivicWatch diagnostics", f"{len(detections)} detections"])
    jpeg = annotate.encode_jpeg(annotated)
    if not jpeg.startswith(b"\xff\xd8"):
        return "frame encoder did not produce a JPEG", FAIL, {}
    detail = (
        f"{model_id} on {device.device} · {inference_ms:.0f} ms/frame · "
        f"{len(detections)} detections · {len(tracks)} tracks · {len(jpeg) / 1024:.0f} KB jpeg"
    )
    return detail, OK, {
        "model_id": model_id,
        "device": device.device,
        "inference_ms": round(inference_ms, 1),
        "detections": len(detections),
        "tracks": len(tracks),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

CHECK_ORDER = [
    ("platform", check_platform, True),
    ("python", check_python, True),
    ("packages", check_packages, True),
    ("hardware", check_device, True),
    ("models", check_models, True),
    ("database", check_database, True),
    ("storage", check_storage, True),
    ("frontend", check_frontend, True),
    ("samples", check_sample_media, False),
    ("port", check_port, False),
    ("server", check_server, False),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CivicWatch installation diagnostics")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--quick", action="store_true", help="skip the inference smoke test")
    parser.add_argument("--no-smoke", action="store_true", help="alias for --quick")
    args = parser.parse_args(argv)

    report = Report(started=time.strftime("%Y-%m-%dT%H:%M:%S"))
    for name, fn, required in CHECK_ORDER:
        report.run(name, fn, required)
    if not (args.quick or args.no_smoke):
        report.run("inference", check_smoke, True)

    if args.json:
        print(json.dumps(
            {
                "ok": report.ok(),
                "started": report.started,
                "checks": [asdict(c) for c in report.checks],
                "failures": [c.name for c in report.failures],
                "warnings": [c.name for c in report.warnings],
            },
            indent=2,
        ))
        return 0 if report.ok() else 1

    print()
    print("CivicWatch diagnostics")
    print("=" * 60)
    for check in report.checks:
        print(f" {styled(SYMBOL[check.status], check.status)} {check.name:<11} {check.detail}")
    print("=" * 60)
    passed = sum(1 for c in report.checks if c.status == OK)
    print(
        f" {passed} ok · {len(report.warnings)} warning(s) · {len(report.failures)} failure(s)"
    )
    if report.failures:
        print("\nFix these before using CivicWatch:")
        for check in report.failures:
            print(f"  ✗ {check.name}: {check.detail}")
        print("\nSetup CivicWatch.command usually resolves these automatically.")
        return 1
    print("\nEverything CivicWatch needs is in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
