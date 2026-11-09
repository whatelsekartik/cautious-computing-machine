"""Evidence and media storage: local filesystem or S3-compatible object store.

Standalone macOS installs keep writing files under ``data/`` exactly as before.
The cloud deployment stores evidence in a private bucket and hands out
short-lived presigned URLs, so large videos never pass through a serverless
function and evidence is never public.

Selection (first match wins):

1. ``CIVICWATCH_STORAGE=local|s3``
2. ``CIVICWATCH_S3_BUCKET`` set → S3
3. otherwise local filesystem (standalone default)

The S3 adapter works with AWS S3, Cloudflare R2, Backblaze B2, MinIO and any
other S3-compatible endpoint; configure ``CIVICWATCH_S3_ENDPOINT`` for
non-AWS services. Vercel Blob is intentionally *not* used: its server-side
token model cannot issue the presigned direct upload this design needs, whereas
S3-compatible stores can (see docs/CLOUD_ARCHITECTURE.md).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_PREFIX = "civicwatch"
DEFAULT_EXPIRY = 900  # 15 minutes is enough for an upload or a page view


class StorageError(RuntimeError):
    pass


class UnsupportedStorageOperation(StorageError):
    pass


class Storage:
    kind = "unknown"
    remote = False

    # -- reads -----------------------------------------------------------
    def exists(self, ref: str | None) -> bool:
        raise NotImplementedError

    def read_bytes(self, ref: str) -> bytes:
        raise NotImplementedError

    def materialize(self, ref: str) -> Path:
        """Return a local path for a reference (downloading when remote)."""
        raise NotImplementedError

    # -- writes ----------------------------------------------------------
    def save_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def save_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def delete(self, ref: str | None) -> None:
        raise NotImplementedError

    # -- direct uploads --------------------------------------------------
    def presign_put(self, key: str, content_type: str, expires: int = DEFAULT_EXPIRY) -> dict[str, Any]:
        raise UnsupportedStorageOperation(
            "Direct-to-storage uploads are only available when CIVICWATCH_S3_BUCKET is configured"
        )

    def presign_get(
        self,
        ref: str,
        expires: int = DEFAULT_EXPIRY,
        filename: str | None = None,
        disposition: str = "inline",
    ) -> str:
        raise UnsupportedStorageOperation("This storage backend cannot sign URLs")

    def info(self) -> dict[str, Any]:
        return {"kind": self.kind, "remote": self.remote}


class LocalStorage(Storage):
    """Files on this machine — the standalone installation's behaviour."""

    kind = "local"
    remote = False

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, ref: str) -> Path:
        path = Path(ref)
        if not path.is_absolute():
            path = self.root / path
        return path

    def exists(self, ref: str | None) -> bool:
        if not ref:
            return False
        try:
            return self._path(ref).is_file()
        except OSError:  # pragma: no cover - defensive
            return False

    def read_bytes(self, ref: str) -> bytes:
        return self._path(ref).read_bytes()

    def materialize(self, ref: str) -> Path:
        path = self._path(ref)
        if not path.is_file():
            raise StorageError(f"Evidence file is missing: {path.name}")
        return path

    def save_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def save_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != Path(path).resolve():
            target.write_bytes(Path(path).read_bytes())
        return str(target)

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        try:
            self._path(ref).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive
            pass

    def presign_put(self, key: str, content_type: str, expires: int = DEFAULT_EXPIRY) -> dict[str, Any]:
        """Standalone mode accepts uploads through the normal multipart API."""
        raise UnsupportedStorageOperation(
            "Standalone mode uploads through /api/media; direct presigned uploads need S3"
        )


class S3Storage(Storage):
    """Private bucket access with presigned GET/PUT URLs."""

    kind = "s3"
    remote = True

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        prefix: str = DEFAULT_PREFIX,
        force_path_style: bool = False,
        client: Any | None = None,
    ):
        self.bucket = bucket
        self.region = region
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")
        self.force_path_style = force_path_style
        self._client = client
        self._tempdir = Path(tempfile.mkdtemp(prefix="civicwatch-evidence-"))

    # -- plumbing --------------------------------------------------------
    def _build_client(self):
        try:
            import boto3
        except Exception as exc:  # pragma: no cover - dependency guard
            raise StorageError(
                "boto3 is not installed. Install the cloud requirements: "
                "pip install -r backend/requirements-cloud.txt"
            ) from exc
        from botocore.config import Config

        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if self.force_path_style else "virtual"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=20,
        )
        return boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint or None,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            config=config,
        )

    @property
    def client(self):
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def key(self, ref: str) -> str:
        """Normalise a stored reference to an object key."""
        if not ref:
            raise StorageError("Empty storage reference")
        if ref.startswith("s3://"):
            _, _, rest = ref.partition("s3://")
            _, _, key = rest.partition("/")
            return key
        return ref

    def ref(self, key: str) -> str:
        return key

    # -- operations ------------------------------------------------------
    def exists(self, ref: str | None) -> bool:
        if not ref:
            return False
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(ref))
            return True
        except Exception as exc:  # noqa: BLE001 - any miss is "not there"
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def read_bytes(self, ref: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self.key(ref))
        return response["Body"].read()

    def materialize(self, ref: str) -> Path:
        key = self.key(ref)
        target = self._tempdir / key.rsplit("/", 1)[-1]
        target.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(target))
        return target

    def save_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        key = self.key(key)
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return key

    def save_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> str:
        key = self.key(key)
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )
        return key

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.key(ref))
        except Exception:  # noqa: BLE001 - deleting already-missing evidence is fine
            pass

    def presign_put(self, key: str, content_type: str, expires: int = DEFAULT_EXPIRY) -> dict[str, Any]:
        key = self.key(key)
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
        )
        return {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "key": key,
            "expires_in": expires,
        }

    def presign_get(
        self,
        ref: str,
        expires: int = DEFAULT_EXPIRY,
        filename: str | None = None,
        disposition: str = "inline",
    ) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self.key(ref)}
        if filename:
            kind = "attachment" if disposition == "attachment" else "inline"
            params["ResponseContentDisposition"] = f'{kind}; filename="{filename}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)

    def info(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "remote": True,
            "bucket": self.bucket,
            "region": self.region,
            "endpoint": self.endpoint or "aws",
            "prefix": self.prefix,
            "private": True,
        }


_storage: Storage | None = None
_storage_key: tuple[Any, ...] | None = None


def _config() -> dict[str, Any]:
    return {
        "mode": (os.environ.get("CIVICWATCH_STORAGE") or "").strip().lower(),
        "bucket": os.environ.get("CIVICWATCH_S3_BUCKET") or os.environ.get("S3_BUCKET") or "",
        "region": os.environ.get("CIVICWATCH_S3_REGION") or os.environ.get("AWS_REGION") or "us-east-1",
        "endpoint": os.environ.get("CIVICWATCH_S3_ENDPOINT") or "",
        "access_key": os.environ.get("CIVICWATCH_S3_ACCESS_KEY_ID")
        or os.environ.get("AWS_ACCESS_KEY_ID")
        or "",
        "secret_key": os.environ.get("CIVICWATCH_S3_SECRET_ACCESS_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or "",
        "prefix": os.environ.get("CIVICWATCH_S3_PREFIX") or DEFAULT_PREFIX,
        "force_path_style": (os.environ.get("CIVICWATCH_S3_FORCE_PATH_STYLE") or "0") == "1",
    }


def get_storage() -> Storage:
    global _storage, _storage_key
    from .config import get_paths

    cfg = _config()
    key = tuple(sorted(cfg.items()))
    if _storage is not None and _storage_key == key:
        return _storage
    if cfg["mode"] == "s3" or (cfg["mode"] != "local" and cfg["bucket"]):
        if not cfg["bucket"]:
            raise StorageError(
                "CIVICWATCH_STORAGE=s3 requires CIVICWATCH_S3_BUCKET to be set"
            )
        _storage = S3Storage(
            bucket=cfg["bucket"],
            region=cfg["region"],
            endpoint=cfg["endpoint"] or None,
            access_key=cfg["access_key"] or None,
            secret_key=cfg["secret_key"] or None,
            prefix=cfg["prefix"],
            force_path_style=cfg["force_path_style"],
        )
    else:
        _storage = LocalStorage(get_paths().data)
    _storage_key = key
    return _storage


def reset_storage() -> None:
    global _storage, _storage_key
    _storage = None
    _storage_key = None


def storage_info() -> dict[str, Any]:
    try:
        storage = get_storage()
        info = storage.info()
        info["configured"] = True
        return info
    except Exception as exc:  # noqa: BLE001 - surfaced to diagnostics
        return {"kind": _config().get("mode") or "local", "configured": False, "error": str(exc)}


def ref_available(ref: str | None) -> bool:
    """Cheap availability check.

    Local files are stat'ed. Remote keys are trusted: they are only written
    after a successful upload, and checking every row would cost one HEAD
    request per incident in a list response.
    """
    if not ref:
        return False
    try:
        storage = get_storage()
    except StorageError:
        return False
    if storage.remote:
        return True
    return storage.exists(ref)


def evidence_keys(incident: dict[str, Any]) -> dict[str, str | None]:
    """Resolve stored evidence references from an incident row.

    Cloud rows use ``*_key`` (private object keys); standalone rows use
    ``*_path`` (absolute file paths). Keys win when both exist.
    """
    snapshot = incident.get("snapshot_key") or incident.get("snapshot_path")
    clip = incident.get("clip_key") or incident.get("clip_path")
    preview = incident.get("preview_key")
    if not preview and clip:
        preview = str(Path(str(clip)).with_name("preview.gif"))
    return {"snapshot": snapshot, "clip": clip, "preview": preview}
