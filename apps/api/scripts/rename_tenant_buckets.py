"""Move existing tenant buckets onto the current S3_TENANT_BUCKET_PREFIX (e.g. t- -> tnt-).

`storage.tenants.bucket` is written once and then treated as immutable, because the bucket
exists remotely AND a DuckLake catalogue's `data_path` metadata names it. So changing the
prefix setting renames nothing by itself — this script performs the move:

  1. create the new bucket and copy every object across (server-side copy, no download)
  2. verify the object count matches before anything is repointed
  3. update `storage.tenants.bucket` + `public_base_url`
  4. rewrite the ONE `ducklake_metadata` row that names the bucket, per tenant catalogue
  5. drop the tenant's service users so the next provision mints them against the new name

Step 4 is why this is cheap: DuckLake stores data-file paths RELATIVE to the catalogue's
`data_path`, so the bucket appears in exactly one row per catalogue. Renaming does not
invalidate a single snapshot.

Step 5 matters more than it looks. Service-user policies are bucket-scoped and their access
keys are named after the bucket (`svc-{bucket}-{role}`), so after a rename the old keys are
both wrongly named and scoped to a bucket the tenant no longer uses. They must be re-minted,
not probed: `credential_works` would see AccessDenied on the new bucket and correctly report
the principal as *alive*, which is true and useless — it is alive and pointing at the wrong
bucket. Deleting the rows makes the next provision mint fresh ones.

The OLD bucket is left in place. Buckets are never deleted automatically (docs/storage.md);
delete it by hand once you are satisfied, which also keeps this script re-runnable.

Run (dry run by default — prints the plan and touches nothing):
    uv run --directory apps/api python scripts/rename_tenant_buckets.py
    uv run --directory apps/api python scripts/rename_tenant_buckets.py --apply
    uv run --directory apps/api python scripts/rename_tenant_buckets.py --apply --tenant 1
"""

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import datalake
from app.config import settings


def _new_bucket(uid: str) -> str:
    return f"{settings.s3_tenant_bucket_prefix}{uid.removeprefix('ten_')}"


def _client() -> Any:
    import boto3
    from botocore.config import Config

    endpoint = settings.s3_endpoint_url
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=settings.s3_region or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        config=Config(s3={"addressing_style": "path" if endpoint else "virtual"}),
    )


def _keys(client: Any, bucket: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        keys += [str(o["Key"]) for o in page.get("Contents", ())]
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")
        if not token:
            return keys


def _copy_bucket(client: Any, old: str, new: str) -> tuple[int, int]:
    """Server-side copy every object old -> new. Returns (copied, already_present)."""
    from botocore.exceptions import ClientError

    try:
        client.create_bucket(Bucket=new)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") not in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            raise
    existing = set(_keys(client, new))
    copied = 0
    for key in _keys(client, old):
        if key in existing:
            continue
        client.copy_object(Bucket=new, Key=key, CopySource={"Bucket": old, "Key": key})
        copied += 1
    return copied, len(existing)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the move (default: dry run)")
    parser.add_argument("--tenant", type=int, default=None, help="limit to one tenant id")
    args = parser.parse_args()

    engine = create_async_engine(settings.migration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = _client()
    moved = 0

    async with sessions() as session:
        await session.execute(text("SET ROLE service_role"))
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT ts.tenant_id, t.uid, ts.bucket "
                        "FROM storage.tenants ts JOIN auth.tenants t ON t.id = ts.tenant_id "
                        "WHERE (:only IS NULL OR ts.tenant_id = :only) ORDER BY ts.tenant_id"
                    ),
                    {"only": args.tenant},
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            uid, old = str(row["uid"]), str(row["bucket"])
            new = _new_bucket(uid)
            if new == old:
                print(f"  tenant {row['tenant_id']:>4}  {old}  already current")
                continue
            objects = len(_keys(client, old))
            print(f"  tenant {row['tenant_id']:>4}  {old} -> {new}  ({objects} objects)")
            if not args.apply:
                moved += 1
                continue

            copied, present = _copy_bucket(client, old, new)
            after = len(_keys(client, new))
            if after < objects:
                print(f"      ABORT: {new} holds {after} of {objects} objects — not repointed")
                continue
            print(f"      copied {copied} (+{present} already there), {after} total")

            await session.execute(
                text(
                    "UPDATE storage.tenants SET bucket = :new, "
                    "  public_base_url = CASE WHEN public_base_url IS NULL THEN NULL "
                    "    ELSE replace(public_base_url, :old, :new) END "
                    "WHERE tenant_id = :t"
                ),
                {"t": row["tenant_id"], "old": old, "new": new},
            )
            # Bucket-scoped policies + bucket-derived key names: re-mint, never re-probe.
            await session.execute(
                text("DELETE FROM storage.tenant_service_users WHERE tenant_id = :t"),
                {"t": row["tenant_id"]},
            )
            await session.execute(
                text("UPDATE storage.tenants SET access_key_id = NULL WHERE tenant_id = :t"),
                {"t": row["tenant_id"]},
            )
            await session.commit()

            if datalake.configured():
                await datalake.repoint_catalogue(uid, old, new)
                print(f"      catalogue data_path -> s3://{new}/datalake/")
            moved += 1

    await engine.dispose()
    verb = "would move" if not args.apply else "moved"
    print(f"\n{verb} {moved} tenant bucket(s).")
    if args.apply and moved:
        print(
            "Next: POST /admin/storage/provision-all to re-mint the four service users "
            "against the new bucket names, then delete the old buckets by hand."
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
