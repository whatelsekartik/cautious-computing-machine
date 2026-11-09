"""Storage adapter tests against a real S3-compatible endpoint (moto)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from civicwatch.storage import (
    LocalStorage,
    S3Storage,
    StorageError,
    UnsupportedStorageOperation,
    evidence_keys,
    get_storage,
    ref_available,
    reset_storage,
)


def test_local_storage_is_the_standalone_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CIVICWATCH_S3_BUCKET", raising=False)
    monkeypatch.delenv("CIVICWATCH_STORAGE", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    reset_storage()
    storage = get_storage()
    assert isinstance(storage, LocalStorage)
    assert storage.remote is False

    ref = storage.save_bytes("evidence/INC-1/snapshot.jpg", b"jpeg-bytes", "image/jpeg")
    assert ref_available(ref) is True
    assert storage.read_bytes(ref) == b"jpeg-bytes"
    storage.delete(ref)
    assert ref_available(ref) is False
    with pytest.raises(UnsupportedStorageOperation):
        storage.presign_put("evidence/INC-1/snapshot.jpg", "image/jpeg")


def test_s3_selection_requires_a_bucket(monkeypatch):
    monkeypatch.setenv("CIVICWATCH_STORAGE", "s3")
    monkeypatch.delenv("CIVICWATCH_S3_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    reset_storage()
    with pytest.raises(StorageError):
        get_storage()
    reset_storage()


def test_s3_roundtrip_and_private_presigned_access(s3_bucket):
    storage = get_storage()
    assert isinstance(storage, S3Storage)
    assert storage.remote is True
    assert storage.info()["private"] is True

    key = storage.save_bytes("evidence/INC-9/snapshot.jpg", b"frame-bytes", "image/jpeg")
    assert key == "evidence/INC-9/snapshot.jpg"
    assert storage.exists(key) is True
    assert storage.read_bytes(key) == b"frame-bytes"

    # Direct browser upload path: presigned PUT, no credentials in the request.
    issued = storage.presign_put("media/abc/source.mp4", "video/mp4", expires=300)
    assert issued["method"] == "PUT"
    assert issued["headers"]["Content-Type"] == "video/mp4"
    put = httpx.put(issued["url"], content=b"video-bytes", headers=issued["headers"])
    assert put.status_code == 200, put.text
    assert storage.read_bytes("media/abc/source.mp4") == b"video-bytes"

    # Presigned GET works without any CivicWatch credential and is always
    # signed and time-limited. (moto's server mode accepts unsigned requests,
    # so *enforcement* is a bucket setting on the real provider; the assertion
    # here is that CivicWatch never issues unsigned or long-lived URLs.)
    url = storage.presign_get("media/abc/source.mp4", filename="clip.mp4", disposition="attachment")
    query = parse_qs(urlparse(url).query)
    assert "X-Amz-Signature" in query
    assert int(query["X-Amz-Expires"][0]) <= 900
    assert query["response-content-disposition"][0].startswith("attachment")
    fetched = httpx.get(url)
    assert fetched.status_code == 200
    assert fetched.content == b"video-bytes"

    inline = storage.presign_get("media/abc/source.mp4", filename="snapshot.jpg")
    assert parse_qs(urlparse(inline).query)["response-content-disposition"][0].startswith("inline")

    # Existence checks use HEAD, and deletions actually remove the object.
    assert storage.exists("media/abc/source.mp4") is True
    storage.delete("media/abc/source.mp4")
    assert storage.exists("media/abc/source.mp4") is False
    assert storage.exists("media/never-uploaded.mp4") is False


def test_evidence_keys_prefer_cloud_keys_and_derive_preview(tmp_path):
    local = {"snapshot_path": str(tmp_path / "snapshot.jpg"), "clip_path": str(tmp_path / "clip.mp4")}
    refs = evidence_keys(local)
    assert refs["snapshot"].endswith("snapshot.jpg")
    assert refs["preview"].endswith("preview.gif")

    cloud = {
        "snapshot_key": "evidence/INC-1/snapshot.jpg",
        "clip_key": "evidence/INC-1/clip.mp4",
        "preview_key": "evidence/INC-1/preview.gif",
        "snapshot_path": "/stale/local/path.jpg",
    }
    refs = evidence_keys(cloud)
    assert refs == {
        "snapshot": "evidence/INC-1/snapshot.jpg",
        "clip": "evidence/INC-1/clip.mp4",
        "preview": "evidence/INC-1/preview.gif",
    }


def test_remote_refs_are_trusted_without_a_head_per_row(s3_bucket):
    # Listing 50 incidents must not perform 100 HEAD requests:
    # remote keys are only written after a successful upload.
    assert ref_available("evidence/INC-1/snapshot.jpg") is True
    assert ref_available(None) is False
