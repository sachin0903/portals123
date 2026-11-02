"""Cached object-storage totals for a tenant's bucket.

WHY THIS IS CACHED (0045 carries the long version). S3 exposes no "size of this prefix" call,
so a total means paging ListObjectsV2 and summing every object's Size -- 1,000 keys per round
trip, and a tenant's datalake is thousands of parquet files that grow every night. There is no
portable admin API to ask instead: RustFS's needed its wire format reverse-engineered
(app/object_store.py) and production runs a different store entirely.

``app/storage.py::prefix_stats`` solves the adjacent problem for the object browser and is not
reusable here for two reasons: it caps its scan at 5,000 objects (right for a page, wrong for a
total -- a capped total is a wrong number), and it deliberately skips datalake keys, which are
exactly what we are measuring.

So: scan on demand, write the result to ``storage.tenant_usage``, and always hand the reader
``computed_at`` so the UI can say "as at" instead of implying live figures.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage

logger = logging.getLogger(__name__)

# The two prefixes worth separating: the datalake is what our ETL costs, files/ is what the firm
# and its clients put there. Anything outside both (a stray key at the root) is counted in
# neither, deliberately -- a total nobody can attribute is not a useful number.
DATALAKE_PREFIX = "datalake/"
FILES_PREFIX = "files/"

# A ceiling, not a target. Reaching it means the answer is a lower bound, which is recorded
# rather than hidden: an unbounded scan on a pathological bucket would hold a worker for
# minutes. 500k objects is ~500 round trips.
MAX_OBJECTS = 500_000


@dataclass(frozen=True)
class PrefixUsage:
    bytes: int = 0
    objects: int = 0
    truncated: bool = False


def _scan(client: Any, bucket: str, prefix: str) -> PrefixUsage:
    """Sum every object under a prefix. No delimiter: we want the recursive total, not folders."""
    total = 0
    count = 0
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents", ()):
            total += int(item.get("Size") or 0)
            count += 1
        if count >= MAX_OBJECTS:
            return PrefixUsage(total, count, True)
        if not response.get("IsTruncated"):
            return PrefixUsage(total, count, False)
        token = response.get("NextContinuationToken")
        if not token:  # a truncated response with no token is the store misbehaving
            return PrefixUsage(total, count, True)


def _scan_bucket(bucket: str, endpoint: str, region: str | None) -> tuple[PrefixUsage, PrefixUsage]:
    client = storage.client_for(endpoint, region)
    return _scan(client, bucket, DATALAKE_PREFIX), _scan(client, bucket, FILES_PREFIX)


async def refresh(session: AsyncSession, tenant_id: int) -> dict[str, Any]:
    """Rescan the tenant's bucket and write the totals. Returns the fresh row.

    A tenant with no bucket yet is not an error: storage provisioning is best-effort by design
    (it must never block tenant creation), so "not provisioned" is a real state the overview has
    to be able to show. It is recorded as an error string rather than raising, so one
    unprovisioned tenant cannot fail a refresh-all.
    """
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    started = time.monotonic()
    error: str | None = None
    lake = PrefixUsage()
    files = PrefixUsage()

    if store is None:
        error = "no tenant bucket provisioned"
    else:
        try:
            lake, files = await asyncio.to_thread(
                _scan_bucket, store.bucket, store.endpoint, store.region
            )
        except Exception as exc:  # noqa: BLE001 -- the failure is the datum here
            logger.warning("usage scan failed for tenant %s: %s", tenant_id, exc)
            error = f"{type(exc).__name__}: {exc}"

    await session.execute(
        text(
            """
            INSERT INTO storage.tenant_usage
                (tenant_id, datalake_bytes, datalake_objects, files_bytes, files_objects,
                 objects_truncated, scan_ms, error, computed_at)
            VALUES (:tenant_id, :lake_bytes, :lake_objects, :files_bytes, :files_objects,
                    :truncated, :scan_ms, :error, now())
            ON CONFLICT (tenant_id) DO UPDATE SET
                datalake_bytes = excluded.datalake_bytes,
                datalake_objects = excluded.datalake_objects,
                files_bytes = excluded.files_bytes,
                files_objects = excluded.files_objects,
                objects_truncated = excluded.objects_truncated,
                scan_ms = excluded.scan_ms,
                error = excluded.error,
                computed_at = excluded.computed_at
            """
        ),
        {
            "tenant_id": tenant_id,
            "lake_bytes": lake.bytes,
            "lake_objects": lake.objects,
            "files_bytes": files.bytes,
            "files_objects": files.objects,
            "truncated": lake.truncated or files.truncated,
            "scan_ms": int((time.monotonic() - started) * 1000),
            "error": error,
        },
    )
    usage = await cached(session, tenant_id)
    return usage or {}


async def cached(session: AsyncSession, tenant_id: int) -> dict[str, Any] | None:
    """The stored totals, or None if this tenant has never been scanned.

    None is not zero and the caller must not render it as such: "no figures yet" and "an empty
    bucket" are different facts about a firm.
    """
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT datalake_bytes, datalake_objects, files_bytes, files_objects,
                           objects_truncated, scan_ms, error, computed_at
                      FROM storage.tenant_usage WHERE tenant_id = :tenant_id
                    """
                ),
                {"tenant_id": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None
