"""Per-tenant object-storage registry (0002_tenant_storage): provisioning lifecycle against
a real Postgres (testcontainers) with a fake object-store admin, plus the RLS boundary —
a firm reads only its own registry row and can never touch the secrets table."""

import json
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, datalake, object_store, provisioning, tenant_storage
from app.config import settings
from app.tenant_storage import (
    get_tenant_store,
    provision_tenant_storage,
    resume_tenant_storage,
    suspend_tenant_storage,
)
from tests.conftest import principal_conn


class FakeAdmin:
    """In-memory ObjectStoreAdmin: records calls; failure modes are switchable."""

    def __init__(
        self,
        *,
        bucket_fails: bool = False,
        public_prefix_ok: bool = True,
        mints: bool = True,
        live_keys: set[str] | None = None,
    ) -> None:
        self.bucket_fails = bucket_fails
        self.public_prefix_ok = public_prefix_ok
        self.mints = mints
        # None => every stored credential probes as live. A set => only these keys do, which
        # is how a store that has forgotten a principal is simulated.
        self.live_keys = live_keys
        self.buckets: list[str] = []
        self.minted: list[str] = []

    def ensure_bucket(self, bucket: str) -> None:
        if self.bucket_fails:
            raise RuntimeError("object store down")
        self.buckets.append(bucket)

    def ensure_public_prefix(self, bucket: str, prefix: str) -> bool:
        return self.public_prefix_ok

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, object] | None = None
    ) -> tuple[str, str] | None:
        if not self.mints:
            return None
        # A distinct keypair per role, mirroring production's deterministic naming: the
        # registry's UNIQUE(access_key_id) would reject four identical keys.
        self.minted.append(name)
        return name, f"{name}-secret"

    def credential_works(self, bucket: str, access_key: str, secret_key: str) -> bool:
        return self.live_keys is None or access_key in self.live_keys


@pytest.fixture
def s3_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://rustfs:9000")
    monkeypatch.setattr(settings, "s3_access_key_id", "root")
    monkeypatch.setattr(settings, "s3_secret_access_key", "root")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    # provision_tenant's create-time hook builds its admin via admin_for — keep it off the
    # network (tests that exercise specific behaviours pass their own FakeAdmin explicitly).
    # principal=None so the hook never pre-mints a credential the tests then assert about.
    monkeypatch.setattr(
        object_store, "admin_for", lambda kind, endpoint, region: FakeAdmin(mints=False)
    )


async def _mk_tenant(session: AsyncSession) -> int:
    alias = f"store-{uuid.uuid4().hex[:10]}"
    row = await provisioning.provision_tenant(session, name=alias, alias=alias, from_model=False)
    return int(row["id"])


async def _bucket_of(session: AsyncSession, tenant_id: int) -> str:
    """The expected bucket name: tnt- + the tenant uid's hex (docs/storage.md §1)."""
    uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one()
    return f"tnt-{str(uid).removeprefix('ten_')}"


async def _row(session: AsyncSession, tenant_id: int) -> dict | None:
    return await tenant_storage.get_storage_row(session, tenant_id)


async def test_provision_happy_path(service_session: AsyncSession, s3_configured: None) -> None:
    """Bucket + public prefix + service user => active row, Fernet secret round-trips."""
    tid = await _mk_tenant(service_session)
    bucket = await _bucket_of(service_session, tid)
    admin = FakeAdmin()
    result = await provision_tenant_storage(service_session, tid, admin=admin)
    assert result["status"] == "active"
    assert result["bucket"] == bucket
    assert admin.buckets == [bucket]

    row = await _row(service_session, tid)
    assert row is not None
    assert row["status"] == "active"
    assert row["kind"] == "rustfs"  # inferred: custom endpoint
    assert row["public_base_url"] == f"http://rustfs:9000/{bucket}"
    # `base` is mirrored onto the legacy column so pre-0061 readers keep working.
    assert row["access_key_id"] == f"svc-{bucket}"
    ct = (
        await service_session.execute(
            text("SELECT secret_access_key_ct FROM storage.tenant_secrets WHERE tenant_id = :t"),
            {"t": tid},
        )
    ).scalar_one()
    assert crypto.decrypt(ct) == f"svc-{bucket}-secret"

    # All four scoped roles (0061), each with its own keypair.
    roles = dict(
        (
            await service_session.execute(
                text(
                    "SELECT role, access_key_id FROM storage.tenant_service_users "
                    "WHERE tenant_id = :t"
                ),
                {"t": tid},
            )
        ).all()
    )
    assert roles == {
        "base": f"svc-{bucket}",
        "datalake": f"svc-{bucket}-datalake",
        "documents": f"svc-{bucket}-documents",
        "readonly": f"svc-{bucket}-readonly",
    }
    assert result["steps"]["service_users"] == dict.fromkeys(roles, "ok")

    store = await get_tenant_store(service_session, tid)
    assert store is not None and store.bucket == bucket


async def test_bucket_failure_leaves_provisioning_and_rerun_repairs(
    service_session: AsyncSession, s3_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The create-time hook must fail too, so the row genuinely starts half-provisioned.
    monkeypatch.setattr(
        object_store,
        "admin_for",
        lambda kind, endpoint, region: FakeAdmin(bucket_fails=True, mints=False),
    )
    tid = await _mk_tenant(service_session)
    result = await provision_tenant_storage(
        service_session, tid, admin=FakeAdmin(bucket_fails=True)
    )
    assert result["status"] == "provisioning"
    row = await _row(service_session, tid)
    assert row is not None and row["status"] == "provisioning"
    assert await get_tenant_store(service_session, tid) is None  # never resolves half-provisioned

    # Re-run with the store back up: same call repairs to active (idempotent, ensure-style).
    result = await provision_tenant_storage(service_session, tid, admin=FakeAdmin())
    assert result["status"] == "active"

    # And the reverse: a failed repair of an ACTIVE tenant must NOT take it offline —
    # the row keeps resolving while the store is down.
    result = await provision_tenant_storage(
        service_session, tid, admin=FakeAdmin(bucket_fails=True)
    )
    assert result["status"] == "active"
    assert await get_tenant_store(service_session, tid) is not None


async def test_no_iam_backend_leaves_credential_null(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """A backend that can't mint service users (plain S3 / young RustFS IAM) still activates —
    access_key_id stays NULL (best-effort, docs/storage.md §7)."""
    tid = await _mk_tenant(service_session)
    result = await provision_tenant_storage(service_session, tid, admin=FakeAdmin(mints=False))
    assert result["status"] == "active"
    row = await _row(service_session, tid)
    assert row is not None and row["access_key_id"] is None


async def test_reprovision_remints_a_credential_the_store_has_forgotten(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """A repair must VERIFY each principal against the store, not trust the registry.

    Regression: provisioning reported `service_user: ok` whenever a key was recorded, so a
    store that had lost its IAM users (rebuilt volume) could never be repaired — every lake
    read 403'd with InvalidAccessKeyId while all four provisioning steps showed green, and
    the only way out was to NULL the column by hand.
    """
    tid = await _mk_tenant(service_session)
    bucket = await _bucket_of(service_session, tid)
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())

    # The store now recognises nothing (live_keys=set()), exactly as a rebuilt store behaves.
    forgetful = FakeAdmin(live_keys=set())
    result = await provision_tenant_storage(service_session, tid, admin=forgetful)
    assert result["status"] == "active"
    assert sorted(forgetful.minted) == sorted(
        [
            f"svc-{bucket}",
            f"svc-{bucket}-datalake",
            f"svc-{bucket}-documents",
            f"svc-{bucket}-readonly",
        ]
    ), "a forgotten principal must be re-minted, not reported ok"

    # ...and a healthy store must NOT churn working keys.
    healthy = FakeAdmin()
    await provision_tenant_storage(service_session, tid, admin=healthy)
    assert healthy.minted == [], "live credentials must be left alone"


async def test_no_public_prefix_leaves_base_url_null(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """If the prefix policy can't be applied, public_base_url stays NULL => branding keeps
    falling back to the legacy shared bucket."""
    tid = await _mk_tenant(service_session)
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin(public_prefix_ok=False))
    row = await _row(service_session, tid)
    assert row is not None and row["status"] == "active" and row["public_base_url"] is None


async def test_suspend_resume_guards(
    service_session: AsyncSession, s3_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await _mk_tenant(service_session)
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())

    await suspend_tenant_storage(service_session, tid)
    assert (await _row(service_session, tid))["status"] == "suspended"  # type: ignore[index]
    assert await get_tenant_store(service_session, tid) is None  # suspended stops resolving

    await resume_tenant_storage(service_session, tid)
    assert (await _row(service_session, tid))["status"] == "active"  # type: ignore[index]

    # A half-provisioned row must NOT resume to active (its bucket may not exist) — the
    # create-time hook fails too, so tid2 never activates in the first place.
    monkeypatch.setattr(
        object_store,
        "admin_for",
        lambda kind, endpoint, region: FakeAdmin(bucket_fails=True, mints=False),
    )
    tid2 = await _mk_tenant(service_session)
    await provision_tenant_storage(service_session, tid2, admin=FakeAdmin(bucket_fails=True))
    await resume_tenant_storage(service_session, tid2)
    assert (await _row(service_session, tid2))["status"] == "provisioning"  # type: ignore[index]


async def test_unconfigured_instance_writes_nothing(service_session: AsyncSession) -> None:
    """No S3 settings (fresh dev checkout) => no registry row at all."""
    tid = await _mk_tenant(service_session)  # autouse fixture keeps S3 blank
    result = await provision_tenant_storage(service_session, tid)
    assert result["status"] == "unconfigured"
    assert await _row(service_session, tid) is None


async def test_provision_tenant_survives_storage_failure(
    service_session: AsyncSession,
    s3_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The create-time hook is best-effort: storage blowing up must never fail tenant
    creation (retry via POST /admin/tenants/{id}/storage/provision)."""

    async def boom(session: AsyncSession, tenant_id: int, **kwargs: object) -> dict:
        raise RuntimeError("catastrophic storage failure")

    monkeypatch.setattr(tenant_storage, "provision_tenant_storage", boom)
    alias = f"store-{uuid.uuid4().hex[:10]}"
    row = await provisioning.provision_tenant(
        service_session, name=alias, alias=alias, from_model=False
    )
    assert row["alias"] == alias  # tenant created despite the storage failure


async def test_disable_tenant_suspends_storage(
    service_session: AsyncSession, s3_configured: None
) -> None:
    tid = await _mk_tenant(service_session)
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())
    await provisioning.update_tenant(service_session, tenant_id=tid, enabled=False)
    assert (await _row(service_session, tid))["status"] == "suspended"  # type: ignore[index]
    await provisioning.update_tenant(service_session, tenant_id=tid, enabled=True)
    assert (await _row(service_session, tid))["status"] == "active"  # type: ignore[index]


async def test_lake_catalog_provisioned_with_storage(
    service_session: AsyncSession,
    s3_configured: None,
    super_engine: Engine,
    db: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a datalakes DB configured, provisioning also ensures the tenant's DuckLake
    catalogue schema (named by the tenant uid) — idempotently."""
    with super_engine.connect() as c:
        c.exec_driver_sql(
            "DO $$ BEGIN CREATE ROLE lake_reader NOLOGIN; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
    # Point the catalogue at the same test container (schema creation is all we exercise).
    monkeypatch.setattr(settings, "datalake_database_url", db["super"])
    datalake.reset_engine()

    tid = await _mk_tenant(service_session)
    uid = (
        await service_session.execute(
            text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tid}
        )
    ).scalar_one()
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())
    assert await datalake.catalog_exists(str(uid))
    # Re-run repairs / is a no-op.
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())
    assert await datalake.catalog_exists(str(uid))
    datalake.reset_engine()


async def test_lake_schema_name_validated() -> None:
    with pytest.raises(ValueError):
        await datalake.ensure_tenant_catalog('ten_x"; DROP SCHEMA auth CASCADE; --')


async def test_get_lake_config(
    service_session: AsyncSession, s3_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The internal attach-config payload: tenant-scoped credential when minted (decrypted),
    platform fallback flagged scoped=false otherwise, None for unknown/inactive tenants."""
    monkeypatch.setattr(settings, "s3_access_key_id", "platform-root")
    monkeypatch.setattr(settings, "s3_secret_access_key", "platform-root-secret")

    tid = await _mk_tenant(service_session)
    uid = str(
        (
            await service_session.execute(
                text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tid}
            )
        ).scalar_one()
    )
    await provision_tenant_storage(service_session, tid, admin=FakeAdmin())
    bucket = await _bucket_of(service_session, tid)
    config = await tenant_storage.get_lake_config(service_session, uid)
    assert config is not None
    assert config["scoped"] is True
    # The lake attaches as the least-privileged role that can read it (0061), NOT the
    # whole-bucket `base` credential.
    assert config["access_key_id"] == f"svc-{bucket}-datalake"
    assert config["secret_access_key"] == f"svc-{bucket}-datalake-secret"
    assert config["data_path"] == f"s3://{bucket}/datalake/"
    assert config["lake_schema"] == uid

    # No minted credential => platform fallback, flagged unscoped.
    tid2 = await _mk_tenant(service_session)
    uid2 = str(
        (
            await service_session.execute(
                text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tid2}
            )
        ).scalar_one()
    )
    await provision_tenant_storage(service_session, tid2, admin=FakeAdmin(mints=False))
    config2 = await tenant_storage.get_lake_config(service_session, uid2)
    assert config2 is not None
    assert config2["scoped"] is False
    assert config2["access_key_id"] == "platform-root"

    # Suspended/unknown => None.
    await suspend_tenant_storage(service_session, tid)
    assert await tenant_storage.get_lake_config(service_session, uid) is None
    assert await tenant_storage.get_lake_config(service_session, "ten_nope") is None


# ---- RLS boundary (sync engines, mirroring test_rls.py) ----


def _seed_two_tenants_with_storage(super_engine: Engine) -> dict[str, object]:
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        tenant_a = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_sa','storage-a','Firm A') RETURNING id"
            )
        ).scalar_one()
        tenant_b = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_sb','storage-b','Firm B') RETURNING id"
            )
        ).scalar_one()
        for tid in (tenant_a, tenant_b):
            c.execute(
                text(
                    "INSERT INTO storage.tenants "
                    "  (tenant_id, kind, endpoint, bucket, status) "
                    "VALUES (:t, 'rustfs', 'http://rustfs:9000', :b, 'active')"
                ),
                {"t": tid, "b": f"t-{tid}"},
            )
            c.execute(
                text(
                    "INSERT INTO storage.tenant_secrets "
                    "  (tenant_id, secret_access_key_ct) VALUES (:t, 'ct')"
                ),
                {"t": tid},
            )
    return {"tenant_a": tenant_a, "tenant_b": tenant_b}


def _staff_claims(tenant_id: object) -> dict[str, object]:
    return {
        "sub": "wos_x",
        "role": "adviser",
        "permissions": [],
        "org_id": "org_x",
        "tenant_id": tenant_id,
        "tenant_ids": [tenant_id],
        "contact_ids": [],
        "account_ids": [],
    }


def test_registry_rows_are_tenant_isolated(super_engine: Engine, app_engine: Engine) -> None:
    seed = _seed_two_tenants_with_storage(super_engine)
    with principal_conn(app_engine, _staff_claims(seed["tenant_a"])) as conn:
        rows = conn.execute(text("SELECT tenant_id, bucket FROM storage.tenants")).all()
        assert [r.tenant_id for r in rows] == [seed["tenant_a"]]
        # writes are service_role-only: no INSERT/UPDATE grant to authenticated
        with pytest.raises(ProgrammingError):
            conn.execute(
                text("UPDATE storage.tenants SET status = 'suspended' WHERE tenant_id = :t"),
                {"t": seed["tenant_a"]},
            )


def _client_claims(tenant_id: object) -> dict[str, object]:
    """A client-portal login for `tenant_id`. `tenant_ids` is empty — a real client's JWT never
    carries the staff lineage array — but `tenant_id` (the raw claim, not the lineage) is set,
    same as a real login: `auth.tenant_id()` reads it directly regardless of portal."""
    return {
        "sub": "wos_client_x",
        "role": "org-advised-client",
        "portal": "client",
        "permissions": [],
        "org_id": "org_client_x",
        "tenant_id": tenant_id,
        "tenant_ids": [],
        "contact_ids": [],
        "account_ids": [],
    }


def test_a_client_can_read_their_own_tenants_storage_row(
    super_engine: Engine, app_engine: Engine
) -> None:
    """0053: tenant_lineage() is deliberately empty for a client-portal principal (the same
    mechanism the AML tipping-off control relies on), which used to leave a client's own
    session unable to resolve their own tenant's bucket at all — app.messaging's attachment
    send raised AttachmentStorageUnavailable for every client, unconditionally. auth.tenant_id()
    (the raw JWT claim, not the staff lineage array) is the fix: it's set for a client's login
    same as a staff one."""
    seed = _seed_two_tenants_with_storage(super_engine)
    with principal_conn(app_engine, _client_claims(seed["tenant_a"])) as conn:
        rows = conn.execute(text("SELECT tenant_id, bucket FROM storage.tenants")).all()
        assert [r.tenant_id for r in rows] == [seed["tenant_a"]]


def test_a_client_still_cannot_read_another_tenants_storage_row(
    super_engine: Engine, app_engine: Engine
) -> None:
    seed = _seed_two_tenants_with_storage(super_engine)
    with principal_conn(app_engine, _client_claims(seed["tenant_a"])) as conn:
        rows = conn.execute(
            text("SELECT tenant_id FROM storage.tenants WHERE tenant_id = :t"),
            {"t": seed["tenant_b"]},
        ).all()
        assert rows == []


def test_secrets_table_denies_authenticated_entirely(
    super_engine: Engine, app_engine: Engine
) -> None:
    """Mirrors auth.environment_secrets: not even the ciphertext is readable on the
    request path."""
    seed = _seed_two_tenants_with_storage(super_engine)
    with principal_conn(app_engine, _staff_claims(seed["tenant_a"])) as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(text("SELECT * FROM storage.tenant_secrets"))


def test_audit_redacts_storage_secret_ciphertext(super_engine: Engine) -> None:
    """0002 extends audit.record()'s redaction list: the Fernet ciphertext never lands in
    audit.log, but the secrets-table write itself is still recorded."""
    _seed_two_tenants_with_storage(super_engine)
    with super_engine.connect() as c:
        detail = c.execute(
            text(
                "SELECT detail::text FROM audit.log "
                "WHERE action = 'storage.tenant_secrets.insert' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).scalar_one()
    assert "secret_access_key_ct" not in detail
    assert json.loads(detail)["row"].get("tenant_id") is not None
