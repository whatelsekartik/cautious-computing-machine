"""The macOS launchers and setup tooling are part of the product surface.

These tests keep them honest: they must parse as bash, reference the files the
repo actually ships, and their diagnostics must run without crashing in an
environment where nothing is installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LAUNCHERS = [
    "Setup CivicWatch.command",
    "Start CivicWatch.command",
    "Stop CivicWatch.command",
    "Create CivicWatch App.command",
    "Start Cloud Connector.command",
]


@pytest.mark.parametrize("name", LAUNCHERS)
def test_launcher_parses_as_bash(name: str) -> None:
    path = REPO / name
    assert path.exists(), f"{name} is missing from the repository root"
    result = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_launchers_are_executable() -> None:
    for name in LAUNCHERS:
        path = REPO / name
        assert os.access(path, os.X_OK), f"{name} is not executable (chmod +x)"


def test_common_library_exposes_shared_helpers() -> None:
    library = REPO / "launchers" / "lib" / "common.sh"
    assert library.exists()
    text = library.read_text()
    for function in (
        "find_system_python",
        "download_standalone_python",
        "ensure_node",
        "wait_for_health",
        "civicwatch_running",
        "port_in_use",
    ):
        assert f"{function}()" in text, f"{function} is missing from common.sh"


def test_setup_checks_every_essential_prerequisite() -> None:
    setup = (REPO / "Setup CivicWatch.command").read_text()
    for needle in (
        "requirements.txt",
        "npm run build",
        "download_models.py",
        "diagnostics.py",
        "init_db",
        "mps_available",
        "check_network",
    ):
        assert needle in setup, f"Setup no longer handles {needle}"


def test_start_script_opens_the_browser_only_after_health() -> None:
    text = (REPO / "Start CivicWatch.command").read_text()
    health = text.index("wait_for_health 90")
    browser = text.rindex('open "$BASE_URL"')
    assert health < browser, "the browser must open only after the server is healthy"
    assert '"$BASE_URL"' in text


def _run_tool(script: str, args: list[str], data_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CIVICWATCH_DATA_DIR=str(data_dir), PYTHONPATH=str(REPO / "backend"))
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / script), *args],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def test_diagnostics_reports_structured_results(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = _run_tool("diagnostics.py", ["--json", "--quick"], data_dir)
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    names = {check["name"] for check in payload["checks"]}
    assert {"platform", "python", "packages", "hardware", "models", "database", "storage"} <= names
    assert all(check["status"] in {"ok", "warn", "fail"} for check in payload["checks"])
    assert isinstance(payload["failures"], list)
    # The database check must have created and validated a fresh schema.
    assert (data_dir / "civicwatch.db").exists()


def test_model_check_fails_cleanly_when_nothing_is_installed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = _run_tool("download_models.py", ["--check"], data_dir)
    assert result.returncode == 1
    assert "not installed" in result.stdout
    assert "Traceback" not in result.stderr


def test_cloud_connector_launcher_uses_the_repo_tool_and_keeps_secrets_out_of_git() -> None:
    launcher = (REPO / "Start Cloud Connector.command").read_text(encoding="utf-8")
    assert "tools/connector.py" in launcher
    assert "CIVICWATCH_CLOUD_URL" in launcher
    assert "CIVICWATCH_CONNECTOR_TOKEN" in launcher
    # The token is read silently and saved with 0600, never echoed in full.
    assert "read -r -s TOKEN" in launcher
    assert "chmod 600" in launcher
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "config/connector.env" in ignore


def test_connector_env_file_is_never_committed() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "config/connector.env"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode == 128:  # not a git checkout (tarball install)
        pytest.skip("not running inside a git checkout")
    assert result.returncode == 0, "config/connector.env must be git-ignored"
