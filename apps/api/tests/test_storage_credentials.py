"""Per-user storage credentials (0003_storage_credentials): scope mapping, the
issue/rotate/revoke lifecycle against real Postgres with a fake store admin, mirror-based
scoping for issue-for-other, and the RLS/audit posture of the new tables."""

import json
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto, object_store, provisioning, storage_credentials
from app.config import settings
from app.storage_credentials import Scope, scope_for
from app.tenant_storage import provision_tenant_storage
from tests.conftest import principal_conn


class FakeCredAdmin:
    """ObjectStoreAdmin double covering both provisioning and the credential plane."""

    def __init__(self, *, create_fails: bool = False) -> None:
        self.create_fails = create_fails
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def ensure_bucket(self, bucket: str) -> None:
        pass

    def ensure_public_prefix(self, bucket: str, prefix: str) -> bool:
        return True

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, Any] | None = None
    ) -> tuple[str, str] | None:
        return None

    def credential_works(self, bucket: str, access_key: str, secret_key: str) -> bool:
        return True

    def create_user_credential(
        self, access_key: str, secret_key: str, policy: dict[str, Any]
    ) -> None:
        if self.create_fails:
            raise RuntimeError("store down")
        self.created.append((access_key, policy))

    def delete_user_credential(self, access_key: str) -> None:
        self.deleted.append(access_key)


@pytest.fixture
def s3_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://rustfs:9000")
    monkeypatch.setattr(settings, "s3_access_key_id", "root")
    monkeypatch.setattr(settings, "s3_secret_access_key", "root")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    monkeypatch.setattr(object_store, "admin_for", lambda kind, endpoint, region: FakeCredAdmin())


async def _mk_tenant_with_storage(session: AsyncSession) -> int:
    alias = f"cred-{uuid.uuid4().hex[:10]}"
    row = await provisioning.provision_tenant(session, name=alias, alias=alias, from_model=False)
    tid = int(row["id"])
    await provision_tenant_storage(session, tid, admin=FakeCredAdmin())
    return tid


async def _mk_user(session: AsyncSession, tenant_id: int, slugs: list[str] | None = None) -> str:
    """A mirror user, optionally with an active platform membership whose role grants
    the given permission slugs."""
    uid = str(uuid.uuid4())
    email = f"{uuid.uuid4().hex[:8]}@x.com"
    await session.execute(
        text(
            "INSERT INTO auth.users (id, uid, tenant_id, email, first_name, last_name) "
            "VALUES (:id, :uid, :t, :email, 'Test', 'User')"
        ),
        {"id": uid, "uid": f"usr_{uuid.uuid4().hex[:12]}", "t": tenant_id, "email": email},
    )
    if slugs is not None:
        role_id = (
            await session.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                    "VALUES (:t, :slug, 'Cred Role', 'organisation', 'platform') RETURNING id"
                ),
                {"t": tenant_id, "slug": f"cred-role-{uuid.uuid4().hex[:6]}"},
            )
        ).scalar_one()
        for slug in slugs:
            await session.execute(
                text(
                    "INSERT INTO auth.role_permissions (role_id, permission_id) "
                    "SELECT :r, id FROM auth.permissions WHERE slug = :s"
                ),
                {"r": role_id, "s": slug},
            )
        await session.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, role_id, portal, status) "
                "VALUES (:u, :t, :r, 'platform', 'active')"
            ),
            {"u": uid, "t": tenant_id, "r": role_id},
        )
    return uid


# ---- scope mapping (pure) ----


def test_scope_for_tiers() -> None:
    assert scope_for({"storage:read_all", "storage:shared"}) == Scope(
        read_prefixes=["files/", "exports/"], shared=True
    )
    assert scope_for({"storage:read"}) == Scope(
        read_prefixes=["files/reports/", "exports/"], shared=False
    )
    assert scope_for({"storage:read_all", "storage:read"}) == Scope(
        read_prefixes=["files/", "exports/"],
        shared=False,  # read_all wins
    )
    assert scope_for({"storage:shared"}) == Scope(read_prefixes=[], shared=True)
    assert scope_for({"system"}) == Scope(read_prefixes=["files/", "exports/"], shared=True)
    assert scope_for({"members:read"}) is None
    assert scope_for(set()) is None


def test_can_issue_and_manage() -> None:
    assert storage_credentials.can_issue_own({"storage:credentials_own"})
    assert storage_credentials.can_issue_own({"storage:credentials"})  # manage implies own
    assert not storage_credentials.can_issue_own({"storage:read"})
    assert storage_credentials.can_manage({"storage:credentials"})
    assert not storage_credentials.can_manage({"storage:credentials_own"})


# ---- lifecycle (real Postgres, fake store) ----


async def test_issue_rotate_revoke(service_session: AsyncSession, s3_configured: None) -> None:
    tid = await _mk_tenant_with_storage(service_session)
    user = await _mk_user(service_session, tid)
    admin = FakeCredAdmin()
    scope = Scope(read_prefixes=["files/", "exports/"], shared=True)

    issued = await storage_credentials.issue(service_session, tid, user, scope, admin=admin)
    assert issued["access_key_id"].startswith(f"svc-u-{tid}-")
    # uid-named (docs/storage.md §1); the prefix is configurable, so read it rather than
    # hardcoding it a second time.
    assert issued["bucket"].startswith(settings.s3_tenant_bucket_prefix)
    assert issued["shared"] is True
    assert len(admin.created) == 1
    assert "files/shared" in json.dumps(admin.created[0][1])

    # The secret round-trips from the ciphertext (repair path) and matches the response.
    ct = (
        await service_session.execute(
            text(
                "SELECT s.secret_access_key_ct FROM storage.user_credential_secrets s "
                "WHERE s.credential_id = :id"
            ),
            {"id": issued["id"]},
        )
    ).scalar_one()
    assert crypto.decrypt(ct) == issued["secret_access_key"]

    own = await storage_credentials.get_own(service_session, tid, user)
    assert own is not None and own["access_key_id"] == issued["access_key_id"]

    # Re-issue rotates: old store user deleted, old row revoked, exactly one active.
    second = await storage_credentials.issue(service_session, tid, user, scope, admin=admin)
    assert admin.deleted == [issued["access_key_id"]]
    rows = await storage_credentials.list_for_tenant(service_session, tid)
    statuses = {r["access_key_id"]: r["status"] for r in rows}
    assert statuses[issued["access_key_id"]] == "revoked"
    assert statuses[second["access_key_id"]] == "active"

    # Revoke by id.
    result = await storage_credentials.revoke(service_session, tid, int(second["id"]), admin=admin)
    assert result is not None and result["status"] == "revoked"
    assert await storage_credentials.get_own(service_session, tid, user) is None
    assert await storage_credentials.revoke(service_session, tid, 999999, admin=admin) is None


async def test_issue_requires_active_storage(
    service_session: AsyncSession, s3_configured: None
) -> None:
    alias = f"cred-{uuid.uuid4().hex[:10]}"
    row = await provisioning.provision_tenant(
        service_session, name=alias, alias=alias, from_model=False
    )
    tid = int(row["id"])
    # storage row exists (create-time hook) but suspend it => inactive.
    from app.tenant_storage import suspend_tenant_storage

    await suspend_tenant_storage(service_session, tid)
    user = await _mk_user(service_session, tid)
    with pytest.raises(storage_credentials.StorageInactive):
        await storage_credentials.issue(
            service_session,
            tid,
            user,
            Scope(read_prefixes=["files/"], shared=False),
            admin=FakeCredAdmin(),
        )


async def test_store_failure_leaves_no_row(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """If the store-side create fails, no registry row appears (no phantom credentials)."""
    tid = await _mk_tenant_with_storage(service_session)
    user = await _mk_user(service_session, tid)
    with pytest.raises(RuntimeError):
        await storage_credentials.issue(
            service_session,
            tid,
            user,
            Scope(read_prefixes=["files/"], shared=False),
            admin=FakeCredAdmin(create_fails=True),
        )
    assert await storage_credentials.get_own(service_session, tid, user) is None


async def test_member_permission_slugs(service_session: AsyncSession, s3_configured: None) -> None:
    tid = await _mk_tenant_with_storage(service_session)
    member = await _mk_user(service_session, tid, slugs=["storage:read", "storage:shared"])
    slugs = await storage_credentials.member_permission_slugs(service_session, tid, member)
    assert slugs == {"storage:read", "storage:shared"}
    assert scope_for(slugs) == Scope(read_prefixes=["files/reports/", "exports/"], shared=True)

    nobody = await _mk_user(service_session, tid)  # no membership
    assert await storage_credentials.member_permission_slugs(service_session, tid, nobody) == set()


# ---- RLS + audit posture (sync engines, mirroring test_rls.py) ----


def test_credential_rows_rls_and_secret_redaction(super_engine: Engine, app_engine: Engine) -> None:
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        tenant_a = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_ca','cred-a','A') RETURNING id"
            )
        ).scalar_one()
        tenant_b = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_cb','cred-b','B') RETURNING id"
            )
        ).scalar_one()
        for tid, label in ((tenant_a, "a"), (tenant_b, "b")):
            user = c.execute(
                text(
                    "INSERT INTO auth.users (id, uid, tenant_id, email, first_name) "
                    "VALUES (gen_random_uuid(), :uid, :t, :email, 'U') RETURNING id"
                ),
                {"uid": f"usr_{label}", "t": tid, "email": f"{label}@x.com"},
            ).scalar_one()
            cred = c.execute(
                text(
                    "INSERT INTO storage.user_credentials "
                    "  (tenant_id, user_id, access_key_id, read_prefixes, shared) "
                    "VALUES (:t, :u, :ak, ARRAY['files/'], false) RETURNING id"
                ),
                {"t": tid, "u": user, "ak": f"svc-u-{tid}-{label}"},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO storage.user_credential_secrets "
                    "  (credential_id, secret_access_key_ct) VALUES (:id, 'ct')"
                ),
                {"id": cred},
            )

    claims = {
        "sub": "wos_x",
        "role": "adviser",
        # 0107: lineage is a whitelist on the portal claim — no portal, no reach.
        "portal": "platform",
        "permissions": [],
        "org_id": "org_x",
        "tenant_id": tenant_a,
        "tenant_ids": [tenant_a],
        "contact_ids": [],
        "account_ids": [],
    }
    with principal_conn(app_engine, claims) as conn:
        rows = conn.execute(text("SELECT tenant_id FROM storage.user_credentials")).all()
        assert [r.tenant_id for r in rows] == [tenant_a]  # B's row invisible
        with pytest.raises(ProgrammingError):
            conn.execute(text("SELECT * FROM storage.user_credential_secrets"))

    with super_engine.connect() as c:
        detail = c.execute(
            text(
                "SELECT detail::text FROM audit.log "
                "WHERE action = 'storage.user_credential_secrets.insert' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).scalar_one()
        assert "secret_access_key_ct" not in detail  # ciphertext never in the trail
