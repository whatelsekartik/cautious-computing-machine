#!/usr/bin/env python3
"""Download and validate CivicWatch model weights during setup.

Usage:
    python tools/download_models.py                 # default model only
    python tools/download_models.py --model yolo11n
    python tools/download_models.py --all           # every built-in model
    python tools/download_models.py --check         # validate, download nothing

Downloads go through the same catalogue and validator the app uses, so a
successful run here guarantees Settings → AI models shows "installed".
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from civicwatch.detect import registry  # noqa: E402


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def download(entry: dict, target: Path, attempts: int = 3) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                entry["url"], headers={"User-Agent": "CivicWatch-Setup/1.0"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                started = time.time()
                with open(part, "wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done / total * 100
                            sys.stdout.write(f"\r    {pct:5.1f}%  {human(done)} / {human(total)}")
                            sys.stdout.flush()
                        elif time.time() - started > 5:
                            sys.stdout.write(f"\r    {human(done)}")
                            sys.stdout.flush()
            sys.stdout.write("\r" + " " * 60 + "\r")
            part.replace(target)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # noqa: PERF203
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < attempts:
                print(f"    attempt {attempt} failed ({exc}); retrying…")
                time.sleep(2 * attempt)
    raise SystemExit(f"Could not download {entry['name']}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download CivicWatch model weights")
    parser.add_argument("--model", default=os.environ.get("CIVICWATCH_DEFAULT_MODEL", "yolov8n"))
    parser.add_argument("--all", action="store_true", help="download every built-in model")
    parser.add_argument("--check", action="store_true", help="validate existing files only")
    parser.add_argument("--force", action="store_true", help="re-download even if a valid file exists")
    args = parser.parse_args()

    ids = [m["id"] for m in registry.list_builtin_models()] if args.all else [args.model]
    failures = 0

    print(f"CivicWatch models · licence of these weights: {registry.ULTRALYTICS_LICENSE}")
    print(f"Model directory: {registry.model_dir()}")

    for model_id in ids:
        entry = registry.builtin_entry(model_id)
        if entry is None:
            print(f"  ✗ unknown model '{model_id}'")
            failures += 1
            continue
        target = registry.weights_path(model_id)

        if target.exists() and not args.force:
            try:
                info = registry.validate_weights(target)
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                print(f"  ! {entry['name']}: existing file is unusable ({exc}); replacing")
                target.unlink(missing_ok=True)
            else:
                print(
                    f"  ✓ {entry['name']} already installed "
                    f"({human(info['bytes'])}, {len(info['classes'])} classes)"
                )
                continue

        if args.check:
            print(f"  ✗ {entry['name']} is not installed ({target.name})")
            failures += 1
            continue

        print(f"  ▸ {entry['name']} ({entry['size_mb']} MB) from {entry['url']}")
        try:
            download(entry, target)
            info = registry.validate_weights(target)
        except SystemExit as exc:
            print(f"  ✗ {exc}")
            failures += 1
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {entry['name']} failed validation: {exc}")
            target.unlink(missing_ok=True)
            failures += 1
            continue
        missing = {"car", "truck", "person"} - set(info["classes"].values())
        if missing:
            print(f"  ✗ {entry['name']} lacks required classes: {sorted(missing)}")
            failures += 1
            continue
        print(f"  ✓ {entry['name']} validated ({human(info['bytes'])}, {len(info['classes'])} classes)")
        print(f"    sha256 {info['sha256']}")

    if failures:
        print(f"\n{failures} model(s) could not be prepared. Re-run Setup once the network is stable.")
        return 1
    print("\nAll requested models are installed and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
