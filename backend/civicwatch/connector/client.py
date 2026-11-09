"""HTTPS client for the CivicWatch cloud API.

Design rules that matter for security:

* the connector token is only ever sent to the configured cloud origin — the
  storage client used for presigned URLs has **no** default headers, so a
  presigned S3/R2 URL can never receive the token;
* 4xx responses are surfaced as ``CloudRejected`` (the payload is invalid and
  will not become valid by retrying);
* timeouts, connection errors and 5xx are surfaced as ``CloudUnavailable`` so
  the outbox keeps the item queued and retries later.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "CivicWatch-Connector/1.0"


class CloudError(RuntimeError):
    pass


class CloudUnavailable(CloudError):
    """Transient: offline, timeout, 5xx or rate limited."""


class CloudRejected(CloudError):
    """Permanent for this payload: authentication or validation failure."""


def _origin_ok(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("https://"):
        return True
    if lowered.startswith("http://"):
        host = lowered.split("//", 1)[1].split("/", 1)[0].split(":")[0]
        return host in {"127.0.0.1", "localhost", "::1"} or os.environ.get(
            "CIVICWATCH_ALLOW_INSECURE_CONNECTOR"
        ) == "1"
    return False


class CloudClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        if not base_url:
            raise CloudError("Cloud URL is required")
        if not _origin_ok(base_url):
            raise CloudError(
                "The cloud URL must use HTTPS (plain HTTP is allowed only for localhost testing)"
            )
        if not token:
            raise CloudError("Connector token is required")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._api = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        # No default headers: presigned storage URLs are signed, not authorised.
        self._storage = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=30.0),
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._api.close()
        self._storage.close()

    def __enter__(self) -> "CloudClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- plumbing --------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = self._api.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CloudUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise CloudUnavailable(
                f"Cloud returned {response.status_code}: {_detail(response)}"
            )
        if response.status_code >= 400:
            raise CloudRejected(f"Cloud rejected the request ({response.status_code}): {_detail(response)}")
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text[:200]}

    # -- endpoints -------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            response = self._api.get("/api/health")
        except httpx.HTTPError as exc:
            raise CloudUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise CloudUnavailable(f"Health check failed with {response.status_code}")
        return response.json()

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/connector/heartbeat", json=payload)

    def config(self) -> dict[str, Any]:
        return self._request("GET", "/api/connector/config")

    def claim_assignments(self, limit: int = 3) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/connector/assignments", params={"limit": limit})
        return payload.get("items", [])

    def update_assignment(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/connector/assignments/{job_id}", json=payload)

    def evidence_urls(self, client_incident_id: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = self._request(
            "POST",
            "/api/connector/evidence/upload-urls",
            json={"client_incident_id": client_incident_id, "files": files},
        )
        return payload.get("files", [])

    def request_processed_url(self, job_id: str) -> dict[str, Any] | None:
        try:
            return self._request("POST", "/api/connector/processed/upload-url", json={"job_id": job_id})
        except CloudRejected:
            return None

    def push_incidents(self, incidents: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/connector/incidents", json={"incidents": incidents})

    def report_models(self, models: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/connector/models", json=models)

    # -- storage transfers ----------------------------------------------
    def upload_file(self, url: str, path: Path, headers: dict[str, str] | None = None) -> int:
        if not _origin_ok(url):
            raise CloudError("Refusing to upload to a non-HTTPS URL")
        try:
            with open(path, "rb") as handle:
                response = self._storage.put(url, content=handle, headers=headers or {})
        except httpx.HTTPError as exc:
            raise CloudUnavailable(f"Upload failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            if response.status_code < 500 and response.status_code not in {408, 429}:
                raise CloudRejected(
                    f"Storage rejected the upload ({response.status_code}): {response.text[:160]}"
                )
            raise CloudUnavailable(f"Storage error {response.status_code}")
        return Path(path).stat().st_size

    def download_to(self, url: str, destination: Path, max_bytes: int = 4 * 1024**3) -> int:
        if not _origin_ok(url):
            raise CloudError("Refusing to download from a non-HTTPS URL")
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._storage.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise CloudUnavailable(f"Download failed with {response.status_code}")
                with open(destination, "wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            handle.close()
                            destination.unlink(missing_ok=True)
                            raise CloudRejected("Video exceeds the configured size limit")
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise CloudUnavailable(f"Download failed: {type(exc).__name__}: {exc}") from exc
        return written


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or "no detail"
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            return "; ".join(str(item.get("msg", item)) for item in detail)[:300]
    return str(payload)[:200]
