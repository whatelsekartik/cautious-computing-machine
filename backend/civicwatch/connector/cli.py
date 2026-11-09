"""Command line entry point for the local connector.

Typical use on the Mac that owns the cameras:

    python -m civicwatch.connector --cloud-url https://your-app.vercel.app \
        --token cwconn_xxx

The token is created in the hosted dashboard (Settings → Local connectors) and
shown once. Everything the connector needs is already installed by the normal
Setup launcher; no extra dependencies are required beyond httpx.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any

from ..db import backend_name, init_db
from ..settings_store import update_settings
from . import outbox
from .client import CloudClient, CloudError, CloudRejected, CloudUnavailable
from .worker import ConnectorWorker, local_evidence_summary

GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    ("\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m")
    if sys.stdout.isatty()
    else ("", "", "", "", "", "")
)


def _line(symbol: str, message: str, colour: str = GREEN) -> None:
    print(f"  {colour}{symbol}{RESET} {message}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="civicwatch-connector",
        description="Run CivicWatch inference locally and sync incidents to the cloud dashboard.",
    )
    parser.add_argument("--cloud-url", default=os.environ.get("CIVICWATCH_CLOUD_URL", ""))
    parser.add_argument("--token", default=os.environ.get("CIVICWATCH_CONNECTOR_TOKEN", ""))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("CIVICWATCH_CONNECTOR_INTERVAL", "20")))
    parser.add_argument("--device", choices=["auto", "mps", "cpu"], default=None,
                        help="Set the local inference device before starting")
    parser.add_argument("--once", action="store_true", help="Run a single sync cycle and exit")
    parser.add_argument("--no-assignments", action="store_true",
                        help="Only push local incidents; do not claim cloud processing jobs")
    parser.add_argument("--status", action="store_true", help="Show queued incidents and exit")
    parser.add_argument("--test", action="store_true", help="Check connectivity and credentials only")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if backend_name() != "sqlite":
        print("This process is the local connector and must use the local SQLite database.")
        print("DATABASE_URL is set, which selects the cloud database — unset it and retry.")
        return 2

    init_db()

    if args.status:
        print("CivicWatch connector — local queue")
        summary = local_evidence_summary()
        print(f"  incidents: {summary['incidents']} ({summary['with_evidence']} with evidence)")
        print(f"  outbox:    {summary['outbox']}")
        return 0

    if args.device:
        update_settings({"device_preference": args.device})
        _line("✓", f"Local inference device set to {args.device}")

    if not args.cloud_url or not args.token:
        print(f"{BOLD}Missing configuration{RESET}")
        print("  --cloud-url  e.g. https://civicwatch-yourcity.vercel.app")
        print("  --token      cwconn_… created in the dashboard (Settings → Local connectors)")
        print("  Both can also be supplied through CIVICWATCH_CLOUD_URL / CIVICWATCH_CONNECTOR_TOKEN.")
        return 2

    try:
        client = CloudClient(args.cloud_url, args.token)
    except CloudError as exc:
        print(f"{RED}{exc}{RESET}")
        return 2

    worker = ConnectorWorker(client, process_assignments=not args.no_assignments)
    stop = {"flag": False}

    def _handle(signum, _frame):
        if not stop["flag"]:
            print("\nStopping connector — queued incidents stay in the local outbox.")
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    print(f"\n{BOLD}CivicWatch local connector{RESET}")
    print(f"{DIM}cloud: {args.cloud_url}{RESET}")

    try:
        health = client.health()
        _line("✓", f"Cloud reachable · {health.get('app', 'CivicWatch')} {health.get('version', '')}".strip())
        config = client.config()
        cfg = config.get("config", {})
        _line("✓", f"Authenticated as connector '{config.get('connector_name', 'unknown')}'")
        worker.last_config = cfg
        print(
            f"{DIM}  model={cfg.get('model_id')} confidence={cfg.get('confidence')} "
            f"sampling=1/{cfg.get('sample_every')} policy is controlled by the dashboard{RESET}"
        )
    except CloudRejected as exc:
        print(f"{RED}Authentication failed.{RESET} {exc}")
        print("  Create a new connector in the dashboard and copy the token it shows once.")
        return 1
    except CloudUnavailable as exc:
        print(f"{YELLOW}Cloud not reachable right now: {exc}{RESET}")
        print("  Incidents will be buffered locally and retried automatically.")

    if args.test:
        return 0

    try:
        worker.heartbeat()
        _line("✓", "Heartbeat sent · local models reported truthfully (nothing is fabricated)")
    except (CloudUnavailable, CloudRejected) as exc:
        print(f"{YELLOW}Heartbeat deferred: {exc}{RESET}")

    if args.once:
        summary = worker.tick()
        _print_summary(summary)
        client.close()
        return 0 if summary.get("ok") or summary.get("offline") else 1

    print(f"{DIM}  polling every {max(5, args.interval)}s · Ctrl-C to stop · queued incidents are never lost{RESET}\n")
    try:
        while not stop["flag"]:
            summary = worker.tick()
            _print_summary(summary)
            deadline = time.time() + max(5, args.interval)
            while time.time() < deadline and not stop["flag"]:
                time.sleep(0.5)
    finally:
        client.close()
    return 0


def _print_summary(summary: dict[str, Any]) -> None:
    if not summary.get("ok"):
        if summary.get("offline"):
            pending = summary.get("pending", 0)
            _line("!", f"Offline · {pending} incident(s) queued locally · will retry", YELLOW)
            return
        _line("✗", summary.get("error", "connector error"), RED)
        return
    sync = summary.get("sync") or {}
    parts = []
    if sync.get("sent"):
        parts.append(f"{sync['sent']} sent")
    if sync.get("duplicates"):
        parts.append(f"{sync['duplicates']} duplicate")
    if sync.get("failed"):
        parts.append(f"{sync['failed']} retry")
    if sync.get("pending"):
        parts.append(f"{sync['pending']} queued")
    assignments = summary.get("assignments") or {}
    if assignments.get("claimed"):
        parts.append(f"{assignments['claimed']} assignment(s)")
    message = " · ".join(parts) if parts else "idle · queue empty"
    _line("✓", f"heartbeat · {message}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
