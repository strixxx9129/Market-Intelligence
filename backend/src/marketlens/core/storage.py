"""Object storage behind a tiny interface: S3-compatible in the stack (SeaweedFS locally, any
S3 in production), the local filesystem in tests.

Large artefacts (equity curves, later raw files) go here; PostgreSQL keeps only their URI.
"""

from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from marketlens.core.config import Settings, get_settings


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Store `data` under `key` and return its URI."""

    def get_bytes(self, uri: str) -> bytes: ...


class S3ObjectStore:
    def __init__(self, bucket: str, settings: Settings | None = None) -> None:
        import boto3  # imported lazily: only needed where objects are actually written

        s = settings or get_settings()
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        body = self.client.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))["Body"]
        return bytes(body.read())


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.resolve().as_uri()

    def get_bytes(self, uri: str) -> bytes:
        return Path(urlparse(uri).path).read_bytes()
