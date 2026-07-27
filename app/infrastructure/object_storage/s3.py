from __future__ import annotations

import asyncio
from typing import Any

import boto3

from .base import StoredObject


class S3CompatibleObjectStore:
    """Async facade over boto3's blocking S3-compatible client."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str = "",
        region: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
    ) -> None:
        if not bucket.strip():
            raise ValueError("Object store bucket is required.")
        self.bucket = bucket.strip()
        self.prefix = prefix.strip().strip("/")
        client_options: dict[str, Any] = {}
        if endpoint_url:
            client_options["endpoint_url"] = endpoint_url
        if region:
            client_options["region_name"] = region
        if access_key_id:
            client_options["aws_access_key_id"] = access_key_id
        if secret_access_key:
            client_options["aws_secret_access_key"] = secret_access_key
        self.client = boto3.client("s3", **client_options)

    async def put_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
    ) -> StoredObject:
        resolved_key = "/".join(
            part for part in (self.prefix, key.lstrip("/")) if part
        )
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=resolved_key,
            Body=body,
            ContentType=content_type,
        )
        return StoredObject(
            uri=f"s3://{self.bucket}/{resolved_key}",
            size_bytes=len(body),
            content_type=content_type,
        )
