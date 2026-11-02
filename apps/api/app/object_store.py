"""Object-store admin plane: bucket + policy + service-user provisioning (docs/storage.md §7).

The request path (``app/storage.py``) only ever PUTs objects; this module is the control
plane used at tenant creation to mint the per-tenant bucket. Bucket creation and bucket
policies are plain S3 API calls (work everywhere, including RustFS — root credential).
Minting a tenant-scoped service user is IAM, which plain S3 does not expose and RustFS's
admin API only partially supports — so ``ensure_tenant_principal`` is **best-effort**:
it returns the credential when the backend can mint one and ``None`` otherwise, and the
caller records that in the registry (``access_key_id`` nullable). Nothing consumes tenant
credentials yet (the ETL/datalake plane does, later).

All methods are sync (boto3); callers run them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets as _secrets
from functools import lru_cache
from typing import Any, Protocol
from urllib import request as _urlrequest
from urllib.parse import quote as _urlquote

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["ObjectStoreAdmin", "RustFSAdmin", "S3CompatAdmin", "admin_for", "public_read_policy"]


class CredentialsUnsupported(RuntimeError):
    """This backend has no IAM API — per-user credentials can't be issued against it."""


class ObjectStoreAdmin(Protocol):
    """Provisioning operations against one object-store endpoint. The ``ensure_*``
    methods are idempotent (safe to re-run to repair a partial provision); the
    credential methods raise — the credential IS the product."""

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't exist. Raises on failure."""
        ...

    def ensure_public_prefix(self, bucket: str, prefix: str) -> bool:
        """Apply an anonymous-read bucket policy scoped to ``{prefix}/*``. Returns False
        (rather than raising) when the backend can't — the caller then leaves the
        registry's ``public_base_url`` NULL and branding falls back (docs/storage.md §8)."""
        ...

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, Any] | None = None
    ) -> tuple[str, str] | None:
        """Best-effort: mint a service user with ``policy`` (default: full access to exactly
        this bucket). Returns ``(access_key_id, secret_access_key)`` or ``None`` when the
        backend has no (working) IAM API."""
        ...

    def credential_works(self, bucket: str, access_key: str, secret_key: str) -> bool:
        """Whether this keypair still AUTHENTICATES against the store.

        The distinction that matters: ``InvalidAccessKeyId``/``SignatureDoesNotMatch`` mean
        the principal is gone or the secret is stale — it must be re-minted. ``AccessDenied``
        means the principal is alive and merely not permitted to do this, which is the
        correct answer for a prefix-scoped role and must NOT trigger a re-mint. Without this
        check a repair can only trust the registry, which is how provisioning came to report
        ``service_user: ok`` for a credential the store had forgotten."""
        ...

    def create_user_credential(
        self, access_key: str, secret_key: str, policy: dict[str, Any]
    ) -> None:
        """Create an IAM user with this exact keypair and attach the literal policy.
        Raises ``CredentialsUnsupported`` on backends without IAM, or on failure."""
        ...

    def delete_user_credential(self, access_key: str) -> None:
        """Delete the IAM user (revocation) and its policy. Raises on failure."""
        ...


def public_read_policy(bucket: str, prefix: str) -> dict[str, Any]:
    """Anonymous ``s3:GetObject`` on ``{bucket}/{prefix}/*`` — the ONLY public surface of a
    tenant bucket (branding assets, docs/storage.md §4)."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/{prefix.strip('/')}/*"],
            }
        ],
    }


def user_files_policy(bucket: str, *, read_prefixes: list[str], shared: bool) -> dict[str, Any]:
    """The literal per-user policy (docs/storage.md §4): list+read over the granted
    prefixes, plus read/write on ``files/shared/`` when ``shared``. `lake/` never appears.
    All Action/Resource values are arrays (the store rejects scalar JSON strings)."""
    list_prefixes: list[str] = []
    read_resources: list[str] = []
    for prefix in read_prefixes:
        clean = prefix.strip("/") + "/"
        list_prefixes += [clean, f"{clean}*"]
        read_resources.append(f"arn:aws:s3:::{bucket}/{clean}*")
    statements: list[dict[str, Any]] = []
    shared_covered = any("files/shared/".startswith(p.strip("/") + "/") for p in read_prefixes)
    if shared and not shared_covered:
        list_prefixes += ["files/shared/", "files/shared/*"]
        read_resources.append(f"arn:aws:s3:::{bucket}/files/shared/*")
    if list_prefixes:
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
                "Condition": {"StringLike": {"s3:prefix": list_prefixes}},
            }
        )
    if read_resources:
        statements.append(
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": read_resources}
        )
    if shared:
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/files/shared/*"],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def _tenant_bucket_policy(bucket: str) -> dict[str, Any]:
    """Full access to exactly one bucket — the tenant service user's policy document."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }


#: The service users minted per tenant bucket (0061). Order matters only for reporting.
SERVICE_USER_ROLES: tuple[str, ...] = ("base", "datalake", "documents", "readonly")

#: What each role may touch, as prefix globs. `base` is deliberately absent — it is the
#: whole-bucket credential and gets `_tenant_bucket_policy`.
_ROLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "datalake": ("datalake/", "files/platforms/"),
    "documents": ("files/",),
    "readonly": ("files/",),
}


def service_user_role_name(bucket: str, role: str) -> str:
    """The deterministic access key id for a (bucket, role). Deterministic on purpose: a
    repair can ask the store about THIS principal rather than trusting the registry."""
    return f"svc-{bucket}" if role == "base" else f"svc-{bucket}-{role}"


def service_user_policy(bucket: str, role: str) -> dict[str, Any]:
    """The literal policy for one tenant service user (0061 carries the reasoning).

    `readonly` gets GetObject only; the other prefix-scoped roles also get Put/Delete.
    List is granted on the bucket with an `s3:prefix` condition, because ListBucket is a
    bucket-level action — without the condition a reader scoped to `files/` could enumerate
    `datalake/`, which is the tenant's provider data rather than their files."""
    if role == "base":
        return _tenant_bucket_policy(bucket)
    prefixes = _ROLE_PREFIXES[role]
    list_prefixes: list[str] = []
    resources: list[str] = []
    for prefix in prefixes:
        clean = prefix.strip("/") + "/"
        list_prefixes += [clean, f"{clean}*"]
        resources.append(f"arn:aws:s3:::{bucket}/{clean}*")
    statements: list[dict[str, Any]] = [
        {
            "Effect": "Allow",
            "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
            "Resource": [f"arn:aws:s3:::{bucket}"],
            "Condition": {"StringLike": {"s3:prefix": list_prefixes}},
        },
        {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": resources},
    ]
    if role != "readonly":
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
                "Resource": resources,
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


class S3CompatAdmin:
    """Plain S3-API admin ops with the platform root credential. Works against anything
    S3-compatible; has no IAM surface (``ensure_tenant_principal`` -> None)."""

    kind = "s3"

    def __init__(self, endpoint: str, region: str | None) -> None:
        self.endpoint = endpoint
        self.region = region

    @property
    def _client(self) -> Any:  # boto3 lazily, matching app/storage.py
        return _s3_client(self.endpoint, self.region)

    def ensure_bucket(self, bucket: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.create_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise

    def ensure_public_prefix(self, bucket: str, prefix: str) -> bool:
        try:
            self._client.put_bucket_policy(
                Bucket=bucket, Policy=json.dumps(public_read_policy(bucket, prefix))
            )
            return True
        except Exception:
            logger.warning("put_bucket_policy failed for %s (public prefix %s)", bucket, prefix)
            return False

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, Any] | None = None
    ) -> tuple[str, str] | None:
        return None  # plain S3 has no IAM API; hosted deployments use real IAM out-of-band

    def credential_works(self, bucket: str, access_key: str, secret_key: str) -> bool:
        """Plain S3, so this works on every backend: make one cheap authenticated call and
        read the error CODE rather than the status. See the protocol for why AccessDenied is
        a pass."""
        from botocore.exceptions import ClientError

        client = _scoped_client(self.endpoint, self.region, access_key, secret_key)
        try:
            client.list_objects_v2(Bucket=bucket, MaxKeys=1)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidSecurity"}:
                return False
            # AccessDenied (and anything else the store chooses to say) means it recognised
            # the caller. A prefix-scoped role legitimately cannot list the bucket root.
            return True
        except Exception:
            # A transport failure says nothing about the credential; treat it as alive so a
            # blip never causes a needless rotation of a working key.
            logger.warning(
                "credential probe failed for %s on %s (assuming live)", access_key, bucket
            )
            return True
        return True

    def create_user_credential(
        self, access_key: str, secret_key: str, policy: dict[str, Any]
    ) -> None:
        raise CredentialsUnsupported("this storage backend cannot issue user credentials")

    def delete_user_credential(self, access_key: str) -> None:
        raise CredentialsUnsupported("this storage backend cannot issue user credentials")


class RustFSAdmin(S3CompatAdmin):
    """S3CompatAdmin plus a best-effort service-user mint via RustFS's native admin REST
    API (``/rustfs/admin/v3/...``). That API is young (mc-admin compat is incomplete —
    rustfs#1571), so ANY failure degrades to None rather than failing provisioning; the
    registry records the missing credential and a re-provision retries."""

    kind = "rustfs"

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, Any] | None = None
    ) -> tuple[str, str] | None:
        secret_key = _secrets.token_urlsafe(30)
        try:
            self.create_user_credential(name, secret_key, policy or _tenant_bucket_policy(bucket))
            return name, secret_key
        except Exception as exc:
            logger.warning("RustFS service-user mint failed for %s (degrading): %s", name, exc)
            return None

    def create_user_credential(
        self, access_key: str, secret_key: str, policy: dict[str, Any]
    ) -> None:
        """IAM user + literal policy + attachment, via the native admin API (wire format
        per the official `rc` CLI). Raises on any failure."""
        key = _urlquote(access_key)
        self._admin_call(
            "PUT",
            f"/rustfs/admin/v3/add-user?accessKey={key}",
            json.dumps({"secretKey": secret_key, "status": "enabled"}).encode(),
        )
        self._admin_call(
            "PUT",
            f"/rustfs/admin/v3/add-canned-policy?name={key}-policy",
            json.dumps(policy).encode(),
        )
        self._admin_call(
            "PUT",
            f"/rustfs/admin/v3/set-user-or-group-policy"
            f"?policyName={key}-policy&userOrGroup={key}&isGroup=false",
            b"",
        )

    def delete_user_credential(self, access_key: str) -> None:
        """Delete the IAM user (access dies immediately) + its policy. A user already
        gone counts as revoked; other failures raise (revocation must not silently fail)."""
        from urllib.error import HTTPError

        key = _urlquote(access_key)
        try:
            self._admin_call("DELETE", f"/rustfs/admin/v3/remove-user?accessKey={key}", b"")
        except HTTPError as exc:
            if exc.code != 404:
                raise
        with contextlib.suppress(Exception):  # policy cleanup is cosmetic
            self._admin_call(
                "DELETE", f"/rustfs/admin/v3/remove-canned-policy?name={key}-policy", b""
            )

    def _admin_call(self, method: str, path_qs: str, body: bytes) -> None:
        """SigV4-signed call to the RustFS admin API (`/rustfs/admin/v3/…`). Wire format
        matches the official `rc` CLI: plain JSON body, `Content-Type: application/json`,
        `x-amz-content-sha256` payload hash, SigV4 service `s3` — all signed with the
        platform root credential."""
        import hashlib

        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials

        url = f"{self.endpoint.rstrip('/')}{path_qs}"
        headers = {"x-amz-content-sha256": hashlib.sha256(body).hexdigest()}
        if body:
            headers["Content-Type"] = "application/json"
        aws_request = AWSRequest(method=method, url=url, data=body, headers=headers)
        credentials = Credentials(settings.s3_access_key_id, settings.s3_secret_access_key)
        SigV4Auth(credentials, "s3", self.region or "us-east-1").add_auth(aws_request)
        request = _urlrequest.Request(  # noqa: S310 — endpoint comes from settings, http(s) only
            url, data=body, method=method, headers=dict(aws_request.headers)
        )
        with _urlrequest.urlopen(request, timeout=10) as response:  # noqa: S310
            if response.status >= 300:
                raise RuntimeError(f"admin call {path_qs} -> HTTP {response.status}")


@lru_cache
def _scoped_client(endpoint: str, region: str | None, access_key: str, secret_key: str) -> Any:
    """A client bound to a specific keypair — used only to probe whether that credential
    still authenticates. One retry: a probe that hangs is worse than a probe that fails."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=region or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            s3={"addressing_style": "path" if endpoint else "virtual"},
            retries={"max_attempts": 1},
            connect_timeout=5,
            read_timeout=5,
        ),
    )


@lru_cache
def _s3_client(endpoint: str, region: str | None) -> Any:
    import boto3
    from botocore.config import Config

    # Custom endpoints (RustFS/MinIO/R2) are path-style; plain AWS is virtual-hosted.
    config = Config(s3={"addressing_style": "path" if endpoint else "virtual"})
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=region or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        config=config,
    )


def admin_for(kind: str, endpoint: str, region: str | None) -> ObjectStoreAdmin:
    if kind == "rustfs":
        return RustFSAdmin(endpoint, region)
    return S3CompatAdmin(endpoint, region)
