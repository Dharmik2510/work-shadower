"""Content-addressed screenshot storage: S3-compatible or local filesystem (dev)."""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Protocol

from .config import Settings


class Storage(Protocol):
    kind: str

    def exists(self, sha256: str) -> bool: ...
    def presign_put(self, sha256: str, asset_id: str, content_type: str, base_url: str) -> dict: ...
    def presign_get(self, sha256: str, content_type: str) -> str | None: ...
    def read(self, sha256: str) -> bytes: ...
    def write(self, sha256: str, data: bytes, content_type: str) -> None: ...
    def health(self) -> bool: ...


def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class LocalStorage:
    """Files under LOCAL_STORAGE_DIR/<aa>/<sha256>. Upload URLs are HMAC-signed and
    point at PUT /api/v1/assets/upload/{asset_id}, so (like S3) they need no bearer token."""

    kind = "local"

    def __init__(self, settings: Settings):
        self.root = Path(settings.local_storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret = settings.secret_key
        self.ttl = settings.presign_ttl_seconds

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).is_file()

    def upload_signature(self, asset_id: str, expires: int) -> str:
        return _sign(self.secret, f"put:{asset_id}:{expires}")

    def verify_upload(self, asset_id: str, expires: int, sig: str) -> bool:
        return expires >= int(time.time()) and hmac.compare_digest(self.upload_signature(asset_id, expires), sig)

    def presign_put(self, sha256: str, asset_id: str, content_type: str, base_url: str) -> dict:
        expires = int(time.time()) + self.ttl
        sig = self.upload_signature(asset_id, expires)
        url = f"{base_url.rstrip('/')}/api/v1/assets/upload/{asset_id}?expires={expires}&sig={sig}"
        return {"method": "PUT", "url": url, "headers": {"Content-Type": content_type}}

    def presign_get(self, sha256: str, content_type: str) -> str | None:
        return None  # API streams the bytes itself

    def read(self, sha256: str) -> bytes:
        return self._path(sha256).read_bytes()

    def write(self, sha256: str, data: bytes, content_type: str) -> None:
        p = self._path(sha256)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def health(self) -> bool:
        try:
            probe = self.root / ".health"
            probe.write_text(str(time.time()))
            return True
        except OSError:
            return False


class S3Storage:
    kind = "s3"

    def __init__(self, settings: Settings):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self.ttl = settings.presign_ttl_seconds
        cfg = Config(signature_version="s3v4", s3={"addressing_style": "path" if settings.s3_endpoint_url else "auto"},
                     retries={"max_attempts": 3, "mode": "standard"})
        common = {"region_name": settings.s3_region, "config": cfg}
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, **common)
        public = settings.s3_public_endpoint_url or settings.s3_endpoint_url or None
        self.presigner = boto3.client("s3", endpoint_url=public, **common)

    def _key(self, sha256: str) -> str:
        return f"{self.prefix}{sha256}"

    def exists(self, sha256: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(sha256))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def presign_put(self, sha256: str, asset_id: str, content_type: str, base_url: str) -> dict:
        url = self.presigner.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": self._key(sha256), "ContentType": content_type},
            ExpiresIn=self.ttl,
        )
        return {"method": "PUT", "url": url, "headers": {"Content-Type": content_type}}

    def presign_get(self, sha256: str, content_type: str) -> str | None:
        return self.presigner.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._key(sha256), "ResponseContentType": content_type},
            ExpiresIn=self.ttl,
        )

    def read(self, sha256: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(sha256))["Body"].read()

    def write(self, sha256: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(sha256), Body=data, ContentType=content_type)

    def health(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


def make_storage(settings: Settings) -> Storage:
    if settings.storage_driver == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_DRIVER=s3 requires S3_BUCKET")
        return S3Storage(settings)
    return LocalStorage(settings)
