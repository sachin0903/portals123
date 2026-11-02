"""Object-store admin plane: idempotent bucket creation, the exact public-prefix policy,
and the best-effort (degrade-to-None) service-user mint (docs/storage.md §7)."""

import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app import object_store
from app.object_store import RustFSAdmin, S3CompatAdmin, admin_for, public_read_policy


class FakeS3Client:
    def __init__(self, *, create_error: str | None = None, policy_error: bool = False) -> None:
        self.create_error = create_error
        self.policy_error = policy_error
        self.policies: list[tuple[str, str]] = []
        self.created: list[str] = []

    def create_bucket(self, Bucket: str) -> None:  # noqa: N803 — boto3 API shape
        if self.create_error:
            raise ClientError({"Error": {"Code": self.create_error}}, "CreateBucket")
        self.created.append(Bucket)

    def put_bucket_policy(self, Bucket: str, Policy: str) -> None:  # noqa: N803
        if self.policy_error:
            raise ClientError({"Error": {"Code": "NotImplemented"}}, "PutBucketPolicy")
        self.policies.append((Bucket, Policy))


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client()
    monkeypatch.setattr(object_store, "_s3_client", lambda endpoint, region: client)
    return client


def test_ensure_bucket_swallows_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    for code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
        client = FakeS3Client(create_error=code)
        monkeypatch.setattr(object_store, "_s3_client", lambda endpoint, region, c=client: c)
        S3CompatAdmin("http://x:9000", None).ensure_bucket("t-1")  # no raise


def test_ensure_bucket_raises_on_real_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeS3Client(create_error="AccessDenied")
    monkeypatch.setattr(object_store, "_s3_client", lambda endpoint, region: client)
    with pytest.raises(ClientError):
        S3CompatAdmin("http://x:9000", None).ensure_bucket("t-1")


def test_public_prefix_policy_shape(fake_client: FakeS3Client) -> None:
    """The ONLY public surface of a tenant bucket: anonymous GetObject on files/tenant/*."""
    assert S3CompatAdmin("http://x:9000", None).ensure_public_prefix("t-1", "files/tenant")
    bucket, policy_json = fake_client.policies[0]
    assert bucket == "t-1"
    assert json.loads(policy_json) == {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::t-1/files/tenant/*"],
            }
        ],
    }
    assert public_read_policy("b", "/files/tenant/") == public_read_policy("b", "files/tenant")


def test_public_prefix_degrades_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend that rejects bucket policies must NOT fail provisioning — the caller leaves
    public_base_url NULL and branding falls back to the shared bucket."""
    client = FakeS3Client(policy_error=True)
    monkeypatch.setattr(object_store, "_s3_client", lambda endpoint, region: client)
    admin = S3CompatAdmin("http://x:9000", None)
    assert admin.ensure_public_prefix("t-1", "files/tenant") is False


def test_plain_s3_has_no_iam() -> None:
    assert S3CompatAdmin("", None).ensure_tenant_principal("t-1", "svc-t-1") is None


def test_rustfs_principal_mint_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    admin = RustFSAdmin("http://rustfs:9000", None)
    monkeypatch.setattr(
        admin.__class__, "_admin_call", lambda self, method, path, body: calls.append(path)
    )
    principal = admin.ensure_tenant_principal("t-1", "svc-t-1")
    assert principal is not None
    access_key_id, secret = principal
    assert access_key_id == "svc-t-1"
    assert len(secret) >= 30
    assert any("add-user" in c for c in calls)
    assert any("add-canned-policy" in c for c in calls)
    assert any("set-user-or-group-policy" in c for c in calls)


def test_rustfs_principal_mint_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """RustFS's admin API is young — ANY failure must degrade to None (registry records the
    missing credential; a re-provision retries), never break tenant provisioning."""

    def boom(self: Any, method: str, path: str, body: bytes) -> None:
        raise RuntimeError("admin API not there yet")

    admin = RustFSAdmin("http://rustfs:9000", None)
    monkeypatch.setattr(admin.__class__, "_admin_call", boom)
    assert admin.ensure_tenant_principal("t-1", "svc-t-1") is None


def test_admin_for_mapping() -> None:
    assert isinstance(admin_for("rustfs", "http://x:9000", None), RustFSAdmin)
    assert type(admin_for("s3", "", None)) is S3CompatAdmin
    assert type(admin_for("minio", "http://x:9000", None)) is S3CompatAdmin


def test_user_files_policy_read_all_with_shared() -> None:
    """The storage:read_all + storage:shared policy: list+read over files/ and exports/,
    write only on files/shared/. lake/ never appears; everything is arrays."""
    policy = object_store.user_files_policy(
        "t-7", read_prefixes=["files/", "exports/"], shared=True
    )
    listing, read, write = policy["Statement"]
    assert listing["Condition"]["StringLike"]["s3:prefix"] == [
        "files/",
        "files/*",
        "exports/",
        "exports/*",
    ]
    assert listing["Resource"] == ["arn:aws:s3:::t-7"]
    assert read["Resource"] == ["arn:aws:s3:::t-7/files/*", "arn:aws:s3:::t-7/exports/*"]
    assert write["Action"] == ["s3:PutObject", "s3:DeleteObject"]
    assert write["Resource"] == ["arn:aws:s3:::t-7/files/shared/*"]
    assert "lake" not in json.dumps(policy)


def test_user_files_policy_reports_tier_read_only() -> None:
    """The storage:read tier: reports + exports only, no write statement."""
    policy = object_store.user_files_policy(
        "t-7", read_prefixes=["files/reports/", "exports/"], shared=False
    )
    assert len(policy["Statement"]) == 2  # list + read, no write
    listing, read = policy["Statement"]
    assert "files/reports/*" in listing["Condition"]["StringLike"]["s3:prefix"]
    assert read["Resource"] == [
        "arn:aws:s3:::t-7/files/reports/*",
        "arn:aws:s3:::t-7/exports/*",
    ]


def test_user_files_policy_shared_only() -> None:
    """storage:shared alone: read+write on files/shared/ and nothing else."""
    policy = object_store.user_files_policy("t-7", read_prefixes=[], shared=True)
    listing, read, write = policy["Statement"]
    assert listing["Condition"]["StringLike"]["s3:prefix"] == ["files/shared/", "files/shared/*"]
    assert read["Resource"] == ["arn:aws:s3:::t-7/files/shared/*"]
    assert write["Resource"] == ["arn:aws:s3:::t-7/files/shared/*"]


def test_rustfs_user_credential_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """create = add-user + add-canned-policy + attach; delete tolerates an already-gone
    user (404) but raises otherwise."""
    from urllib.error import HTTPError

    calls: list[str] = []
    admin = RustFSAdmin("http://rustfs:9000", None)
    monkeypatch.setattr(
        admin.__class__, "_admin_call", lambda self, method, path, body: calls.append(path)
    )
    admin.create_user_credential("svc-u-7-abc", "s3cret", {"Version": "2012-10-17"})
    assert [c.split("?")[0] for c in calls] == [
        "/rustfs/admin/v3/add-user",
        "/rustfs/admin/v3/add-canned-policy",
        "/rustfs/admin/v3/set-user-or-group-policy",
    ]

    def gone(self: RustFSAdmin, method: str, path: str, body: bytes) -> None:
        raise HTTPError(path, 404, "not found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(admin.__class__, "_admin_call", gone)
    admin.delete_user_credential("svc-u-7-abc")  # 404 => already revoked, no raise

    def denied(self: RustFSAdmin, method: str, path: str, body: bytes) -> None:
        raise HTTPError(path, 403, "denied", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(admin.__class__, "_admin_call", denied)
    with pytest.raises(HTTPError):
        admin.delete_user_credential("svc-u-7-abc")


def test_plain_s3_cannot_issue_user_credentials() -> None:
    admin = S3CompatAdmin("", None)
    with pytest.raises(object_store.CredentialsUnsupported):
        admin.create_user_credential("k", "s", {})
    with pytest.raises(object_store.CredentialsUnsupported):
        admin.delete_user_credential("k")


# ---- 0061: four scoped service users ---------------------------------------------------


@pytest.mark.parametrize(
    ("role", "prefixes"),
    [("datalake", ["datalake/*", "files/platforms/*"]), ("documents", ["files/*"])],
)
def test_role_policies_are_scoped_to_their_prefixes(role: str, prefixes: list[str]) -> None:
    policy = object_store.service_user_policy("tnt-abc", role)
    resources = {r for s in policy["Statement"] for r in s["Resource"]}
    assert resources == {f"arn:aws:s3:::tnt-abc/{p}" for p in prefixes} | {"arn:aws:s3:::tnt-abc"}
    # ListBucket is a BUCKET-level action, so it must carry the prefix condition — without it
    # a role scoped to files/ could enumerate the tenant's provider data under datalake/.
    listing = next(s for s in policy["Statement"] if "s3:ListBucket" in s["Action"])
    assert listing["Condition"]["StringLike"]["s3:prefix"]
    assert all("datalake" not in r or role == "datalake" for r in resources)


def test_readonly_role_cannot_write_and_base_owns_the_bucket() -> None:
    readonly = object_store.service_user_policy("tnt-abc", "readonly")
    actions = {a for s in readonly["Statement"] for a in s["Action"]}
    assert "s3:PutObject" not in actions and "s3:DeleteObject" not in actions
    assert "s3:GetObject" in actions

    base = object_store.service_user_policy("tnt-abc", "base")
    assert base == {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": ["arn:aws:s3:::tnt-abc", "arn:aws:s3:::tnt-abc/*"],
            }
        ],
    }


def test_role_key_names_are_deterministic() -> None:
    # A repair asks the store about THIS principal, so the name must be derivable.
    assert object_store.service_user_role_name("tnt-abc", "base") == "svc-tnt-abc"
    assert object_store.service_user_role_name("tnt-abc", "datalake") == "svc-tnt-abc-datalake"


@pytest.mark.parametrize(
    ("code", "alive"),
    [
        ("InvalidAccessKeyId", False),
        ("SignatureDoesNotMatch", False),
        # The principal exists and is merely not permitted — correct for a scoped role, and
        # re-minting on it would rotate a perfectly good key on every repair.
        ("AccessDenied", True),
        ("NoSuchBucket", True),
    ],
)
def test_credential_probe_separates_authentication_from_authorisation(
    monkeypatch: pytest.MonkeyPatch, code: str, alive: bool
) -> None:
    class Probe:
        def list_objects_v2(self, **kwargs: Any) -> None:
            raise ClientError({"Error": {"Code": code}}, "ListObjectsV2")

    monkeypatch.setattr(object_store, "_scoped_client", lambda *a, **k: Probe())
    assert S3CompatAdmin("http://store:9000", None).credential_works("b", "ak", "sk") is alive
