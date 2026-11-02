"""The core object store (`platforms`), via boto3.

Thin on purpose: put, exists, and a key listing. The bucket itself is created by
`rustfs-init` locally and by storage provisioning in deployed environments — this
module never creates buckets, so a typo'd bucket name fails loudly instead of
silently inventing a new one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sei_sync.settings import SeiSettings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)


def client(settings: SeiSettings) -> S3Client:
    """An S3 client for the core store. Endpoint blank = real AWS."""
    import boto3

    kwargs: dict[str, Any] = {"region_name": settings.s3_region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.s3_access_key_id:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client("s3", **kwargs)


def exists(s3: S3Client, bucket: str, key: str) -> bool:
    """Whether the object is already there — what makes the pull idempotent."""
    from botocore.exceptions import ClientError

    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def put(s3: S3Client, bucket: str, key: str, body: bytes, *, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


def copy(s3: S3Client, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
    """Server-side copy. The core credential writes tenant buckets the same way the lake does.

    Content-Type travels with the source object (``MetadataDirective`` defaults to COPY).
    A destination that already holds the key is the caller's problem to skip: this does not
    check, so a retry that forgot the existence check would overwrite an immutable object.
    """
    s3.copy_object(
        Bucket=dst_bucket,
        Key=dst_key,
        CopySource={"Bucket": src_bucket, "Key": src_key},
    )


def get(s3: S3Client, bucket: str, key: str) -> bytes:
    response = s3.get_object(Bucket=bucket, Key=key)
    data: bytes = response["Body"].read()
    return data


def list_keys(s3: S3Client, bucket: str, prefix: str) -> list[str]:
    """Every key under a prefix, paginating past the 1000-key response cap."""
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")
