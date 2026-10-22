"""Where generated files live: AWS S3 in real use, the local disk for development and tests.

A stored file is identified by a URI that is saved on submission_request:
  s3://<bucket>/<prefix>/<tenant>/<batch>/<file>      (S3FileStore)
  <absolute local path>                               (LocalFileStore)
Files are written once and never overwritten. The UI streams downloads through the app, so the bucket stays private.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

CHUNK = 64 * 1024


class StoredFileExists(FileExistsError):
    pass


class StoredFileNotFound(FileNotFoundError):
    pass


class FileStore(Protocol):
    def put(self, relative_key: str, data: bytes, content_type: str) -> str:
        """Write a new file (never overwrites). Returns its URI."""

    def read_chunks(self, uri: str) -> Iterator[bytes]:
        """Stream a file this store owns. Raises StoredFileNotFound."""

    def delete(self, uri: str) -> None:
        """Delete a file this store owns (no error if already gone)."""

    def owns(self, uri: str) -> bool:
        """True if `uri` points inside this store (downloads and deletes are limited to these)."""

    def describe(self) -> str:
        """Human-readable location, for error messages."""


def _safe_key(relative_key: str) -> str:
    parts = relative_key.replace("\\", "/").split("/")
    if not relative_key or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Invalid storage key {relative_key!r}")
    return "/".join(parts)


# ---------------------------------------------------------------- local disk
class LocalFileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def put(self, relative_key: str, data: bytes, content_type: str) -> str:
        path = self.root / _safe_key(relative_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as f:
                f.write(data)
        except FileExistsError as exc:
            raise StoredFileExists(str(path)) from exc
        return str(path)

    def owns(self, uri: str) -> bool:
        return not uri.startswith("s3://") and Path(uri).resolve().is_relative_to(self.root)

    def describe(self) -> str:
        return f"local folder {self.root}"

    def read_chunks(self, uri: str) -> Iterator[bytes]:
        path = Path(uri).resolve()
        if not self.owns(uri) or not path.is_file():
            raise StoredFileNotFound(uri)
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                yield chunk

    def delete(self, uri: str) -> None:
        if self.owns(uri):
            Path(uri).unlink(missing_ok=True)


# ---------------------------------------------------------------- AWS S3
@dataclass(frozen=True)
class S3Location:
    bucket: str
    key: str

    @classmethod
    def parse(cls, uri: str) -> "S3Location":
        if not uri.startswith("s3://") or "/" not in uri[5:]:
            raise ValueError(f"Not an S3 URI: {uri!r}")
        bucket, key = uri[5:].split("/", 1)
        return cls(bucket, key)

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


class S3FileStore:
    """Private bucket, server-side encryption (SSE-S3, or SSE-KMS when a key id is given)."""

    def __init__(self, client, bucket: str, prefix: str = "bureau-submissions", kms_key_id: str | None = None):
        self.client, self.bucket, self.prefix, self.kms_key_id = client, bucket, prefix.strip("/"), kms_key_id

    def _location(self, relative_key: str) -> S3Location:
        key = _safe_key(relative_key)
        return S3Location(self.bucket, f"{self.prefix}/{key}" if self.prefix else key)

    def put(self, relative_key: str, data: bytes, content_type: str) -> str:
        loc = self._location(relative_key)
        encryption = ({"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id} if self.kms_key_id
                      else {"ServerSideEncryption": "AES256"})
        try:
            self.client.put_object(Bucket=loc.bucket, Key=loc.key, Body=data, ContentType=content_type,
                                   IfNoneMatch="*", **encryption)     # conditional write: never overwrite
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise StoredFileExists(loc.uri) from exc
            raise
        return loc.uri

    def owns(self, uri: str) -> bool:
        try:
            loc = S3Location.parse(uri)
        except ValueError:
            return False
        return loc.bucket == self.bucket and (not self.prefix or loc.key.startswith(self.prefix + "/"))

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}/" if self.prefix else f"s3://{self.bucket}/"

    def read_chunks(self, uri: str) -> Iterator[bytes]:
        if not self.owns(uri):
            raise StoredFileNotFound(uri)
        loc = S3Location.parse(uri)
        try:
            response = self.client.get_object(Bucket=loc.bucket, Key=loc.key)
        except self.client.exceptions.NoSuchKey as exc:
            raise StoredFileNotFound(uri) from exc
        body = response["Body"]          # StreamingBody; its __enter__ returns the raw urllib3 response, so no `with ... as`
        try:
            yield from body.iter_chunks(chunk_size=CHUNK)
        finally:
            body.close()                                        # always release the connection

    def delete(self, uri: str) -> None:
        if self.owns(uri):
            loc = S3Location.parse(uri)
            self.client.delete_object(Bucket=loc.bucket, Key=loc.key)


class ReadableStores:
    """Read/delete across the app's own locations: the configured store first, then the local output folder.

    New files are always written to the configured store; this only keeps older or locally written files
    downloadable (e.g. files generated before switching to S3, or by a worker started with other settings).
    """

    def __init__(self, primary: FileStore, *others: FileStore):
        self.primary, self.stores = primary, (primary, *others)

    def put(self, relative_key: str, data: bytes, content_type: str) -> str:
        return self.primary.put(relative_key, data, content_type)

    def _owner(self, uri: str) -> FileStore | None:
        return next((s for s in self.stores if s.owns(uri)), None)

    def owns(self, uri: str) -> bool:
        return self._owner(uri) is not None

    def read_chunks(self, uri: str) -> Iterator[bytes]:
        owner = self._owner(uri)
        if owner is None:
            raise StoredFileNotFound(uri)
        return owner.read_chunks(uri)

    def delete(self, uri: str) -> None:
        owner = self._owner(uri)
        if owner is not None:
            owner.delete(uri)

    def describe(self) -> str:
        return " or ".join(s.describe() for s in self.stores)


def make_store(settings) -> FileStore:
    """The store new files are written to, able to read the app's other own locations too."""
    primary = _primary_store(settings)
    if isinstance(primary, LocalFileStore):
        return primary
    return ReadableStores(primary, LocalFileStore(settings.output_dir))


def _primary_store(settings) -> FileStore:
    """Build the configured store. S3 uses the standard AWS credential chain (IAM role, env, profile)."""
    if settings.storage == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("SUBMISSIONS_S3_BUCKET is required when SUBMISSIONS_STORAGE=s3")
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", region_name=settings.s3_region,
                              config=Config(retries={"mode": "standard", "total_max_attempts": 5},
                                            connect_timeout=10, read_timeout=60))
        return S3FileStore(client, settings.s3_bucket, settings.s3_prefix, settings.s3_kms_key_id)
    return LocalFileStore(settings.output_dir)
