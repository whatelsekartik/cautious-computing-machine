"""Hardware selection: Apple MPS when usable, CPU everywhere else."""

from __future__ import annotations

import logging
import os
import platform
import sys
from dataclasses import dataclass

log = logging.getLogger("civicwatch.device")


@dataclass
class DeviceInfo:
    device: str
    reason: str
    mps_available: bool
    mps_built: bool
    cuda_available: bool
    torch_version: str | None
    machine: str
    arm64: bool

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "reason": self.reason,
            "mps_available": self.mps_available,
            "mps_built": self.mps_built,
            "cuda_available": self.cuda_available,
            "torch_version": self.torch_version,
            "machine": self.machine,
            "arm64": self.arm64,
        }


def torch_status() -> dict:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - torch missing
        return {"installed": False, "error": str(exc)}
    mps = torch.backends.mps
    return {
        "installed": True,
        "version": torch.__version__,
        "mps_built": bool(mps.is_built()),
        "mps_available": bool(mps.is_available()),
        "cuda_available": bool(torch.cuda.is_available()),
        "threads": torch.get_num_threads(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }


def resolve_device(preference: str = "auto") -> DeviceInfo:
    """Resolve the inference device.

    ``auto`` prefers MPS, then CUDA (dev machines), then CPU. ``mps`` requests
    MPS explicitly but falls back to CPU with a logged reason rather than
    failing the whole pipeline — an operator should still get results.
    """
    status = torch_status()
    machine = platform.machine()
    arm64 = machine.lower() in {"arm64", "aarch64"}
    if not status.get("installed"):
        return DeviceInfo(
            device="cpu",
            reason="PyTorch is not installed; detection is unavailable",
            mps_available=False,
            mps_built=False,
            cuda_available=False,
            torch_version=None,
            machine=machine,
            arm64=arm64,
        )

    mps_available = bool(status["mps_available"])
    mps_built = bool(status["mps_built"])
    cuda = bool(status["cuda_available"])

    if preference == "cpu":
        return DeviceInfo("cpu", "CPU selected in Settings", mps_available, mps_built, cuda,
                          status["version"], machine, arm64)
    if preference == "mps":
        if mps_available:
            return DeviceInfo("mps", "Apple Silicon GPU (MPS) selected in Settings", mps_available,
                              mps_built, cuda, status["version"], machine, arm64)
        reason = (
            "MPS was requested but is unavailable"
            + ("" if arm64 else " (this machine is not Apple Silicon)")
            + f"; fell back to CPU. torch={status['version']}, mps_built={mps_built}"
        )
        log.warning(reason)
        return DeviceInfo("cpu", reason, mps_available, mps_built, cuda, status["version"],
                          machine, arm64)
    # auto
    if mps_available:
        return DeviceInfo("mps", "Auto: Apple MPS acceleration available", mps_available,
                          mps_built, cuda, status["version"], machine, arm64)
    if cuda:
        return DeviceInfo("cuda:0", "Auto: CUDA GPU available", mps_available, mps_built, cuda,
                          status["version"], machine, arm64)
    return DeviceInfo(
        "cpu",
        "Auto: no GPU acceleration available (CPU mode)",
        mps_available,
        mps_built,
        cuda,
        status["version"],
        machine,
        arm64,
    )


def configure_threads() -> None:
    """Keep the CPU backend responsive while a job runs."""
    try:
        import torch

        if torch.cuda.is_available() or torch.backends.mps.is_available():
            return
        cores = os.cpu_count() or 1
        torch.set_num_threads(max(1, min(4, cores)))
    except Exception:
        return
