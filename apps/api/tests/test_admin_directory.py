"""The admin surface's paged/filtered read queries, and the permission gates on them.

These shipped untested, and two of them are contract-bearing: `GET /admin/users` changed from a
bare array to a `{users, total, limit, offset}` envelope (a breaking response shape for the
generated client), and `GET /admin/audit` grew server-side `tenant_id`/`actor`/`since`/`until`
filters whose whole point is that a search covers the trail rather than one page of it.

The endpoint functions are called directly with a `service_role` session — the same session
`admin_session` yields — so the SQL and the response shape are exercised without a live WorkOS or a
signed token.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.permissions import AUDIT_READ, MODEL_READ, OPERATIONS_READ, TENANTS_READ, TENANTS_WRITE
from app.provisioning import provision_tenant
from app.routers import admin as admin_routes
from app.routers.admin import (
    get_user_by_uid,
    list_audit,
    list_permissions,
    list_tenants,
    list_users_directory,
)

SYSTEM = Principal(user_id="user_ops", permissions=["system"])


async def _firm_with_logins(
    session: AsyncSession, *, alias: str, name: str, emails: list[str]
) -> int:
    tenant = await provision_tenant(session, name=name, alias=alias, from_model=False)
    tid = int(tenant["id"])
    for i, email in enumerate(emails):
        await session.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, email, first_name, last_name, enabled) "
                "VALUES (:uid, :t, :email, :fn, 'Person', true)"
            ),
            {"uid": f"usr_{alias}_{i}", "t": tid, "email": email, "fn": f"Given{i}"},
        )
    return tid


# ---- GET /admin/users: the paged envelope ----


@pytest.mark.asyncio
async def test_users_directory_returns_a_paged_envelope(service_session: AsyncSession) -> None:
    """BREAKING SHAPE, pinned here on purpose: the endpoint returns
    `{users, total, limit, offset}`, not a bare list. `packages/api-client` has to be regenerated
    against this or every admin directory call type-errors."""
    await _firm_with_logins(
        service_session,
        alias="paged-co",
        name="Paged Co",
        emails=[f"p{i}@paged.test" for i in range(5)],
    )
    page = await list_users_directory(
        session=service_session, _p=SYSTEM, q="@paged.test", limit=2, offset=0
    )
    assert set(page.keys()) == {"users", "total", "limit", "offset"}
    assert page["total"] == 5  # the whole matching set, not the page
    assert page["limit"] == 2
    assert page["offset"] == 0
    users = page["users"]
    assert isinstance(users, list)
    assert len(users) == 2
    # Each row carries the firm, because per-tenant email uniqueness means the same address
    # legitimately appears once per firm and a bare list cannot tell them apart.
    assert users[0]["tenant_name"] == "Paged Co"
    assert users[0]["tenant_alias"] == "paged-co"
    # And its UID, because the admin portal's firm link is `/tenants/{uid}` — the row used to
    # carry only the serial id, so the directory's firm link 404'd. It is in the GROUP BY too
    # (this query aggregates the membership count), which is what a bare SELECT addition misses.
    assert isinstance(users[0]["tenant_uid"], str)
    assert users[0]["tenant_uid"]
    # The LOGIN's uid for the same reason: the record link is `/users/{uid}`, and the row used to
    # carry only `auth.users.id`. Also in the GROUP BY.
    assert users[0]["user_uid"] == "usr_paged-co_0"


# ---- GET /admin/users/by-uid/{uid}: addressing a login ----


class TestAddressingAUserByUid:
    """`GET /admin/users/by-uid/{uid}` -- what the admin portal's `/users/{uid}` route resolves.

    `auth.users.id` is a uuid, which leaks no row ordering, but it is still the database's own key
    and `auth.users.uid` exists as the public handle. The resolve delegates to `get_user`, so the
    two addresses cannot answer with two record shapes; it is declared before `/users/{user_id}`,
    whose param is typed UUID and would answer "by-uid" with a 422.
    """

    @pytest.mark.asyncio
    async def test_it_answers_with_the_uuid_route_s_payload(
        self, service_session: AsyncSession
    ) -> None:
        await _firm_with_logins(
            service_session, alias="uid-co", name="Uid Co", emails=["one@uid.test"]
        )
        record = await get_user_by_uid(uid="usr_uid-co_0", session=service_session, _p=SYSTEM)
        user = record["user"]
        assert user["uid"] == "usr_uid-co_0"  # type: ignore[index]
        assert str(user["email"]) == "one@uid.test"  # type: ignore[index]
        # The uuid rides along, because the member endpoints are typed on it and the page's write
        # actions have nothing else to resolve from.
        assert user["id"]  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_an_unknown_uid_is_a_404(self, service_session: AsyncSession) -> None:
        with pytest.raises(HTTPException) as raised:
            await get_user_by_uid(uid="usr_nosuch", session=service_session, _p=SYSTEM)
        assert raised.value.status_code == 404

    def test_it_is_declared_before_the_uuid_route(self) -> None:
        paths = [getattr(r, "path", "") for r in admin_routes.router.routes]
        assert paths.index("/admin/users/by-uid/{uid}") < paths.index("/admin/users/{user_id}")


@pytest.mark.asyncio
async def test_users_directory_pages_without_gaps_or_repeats(
    service_session: AsyncSession,
) -> None:
    """Ordering is stable (email, firm), so walking offsets yields every row exactly once."""
    await _firm_with_logins(
        service_session,
        alias="walk-co",
        name="Walk Co",
        emails=[f"w{i}@walk.test" for i in range(5)],
    )
    seen: list[str] = []
    for offset in (0, 2, 4):
        page = await list_users_directory(
            session=service_session, _p=SYSTEM, q="@walk.test", limit=2, offset=offset
        )
        seen.extend(str(u["email"]) for u in page["users"])  # type: ignore[union-attr]
    assert seen == sorted(seen)
    assert len(set(seen)) == 5


@pytest.mark.asyncio
async def test_users_directory_searches_email_name_and_firm(service_session: AsyncSession) -> None:
    """Search is server-side over the whole estate: the person's email or name, or their firm's
    name — a client-side filter over one page read as a search and covered a sliver of it."""
    await _firm_with_logins(
        service_session,
        alias="searchable-co",
        name="Searchable Advisers",
        emails=["findme@searchable.test"],
    )
    by_email = await list_users_directory(session=service_session, _p=SYSTEM, q="findme")
    assert [u["email"] for u in by_email["users"]] == ["findme@searchable.test"]  # type: ignore[union-attr]

    by_firm = await list_users_directory(session=service_session, _p=SYSTEM, q="Searchable Advis")
    assert "findme@searchable.test" in [u["email"] for u in by_firm["users"]]  # type: ignore[union-attr]

    by_name = await list_users_directory(session=service_session, _p=SYSTEM, q="Given0 Person")
    assert "findme@searchable.test" in [u["email"] for u in by_name["users"]]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_users_directory_excludes_soft_deleted_logins(service_session: AsyncSession) -> None:
    """A soft-deleted login is gone, not merely hidden from its firm — it must not haunt the
    cross-tenant directory (and it must not be counted in `total` either)."""
    tid = await _firm_with_logins(
        service_session,
        alias="deleted-co",
        name="Deleted Co",
        emails=["alive@deleted.test", "gone@deleted.test"],
    )
    await service_session.execute(
        text("UPDATE auth.users SET deleted_at = now() WHERE tenant_id = :t AND email = :e"),
        {"t": tid, "e": "gone@deleted.test"},
    )
    page = await list_users_directory(session=service_session, _p=SYSTEM, q="@deleted.test")
    assert [u["email"] for u in page["users"]] == ["alive@deleted.test"]  # type: ignore[union-attr]
    assert page["total"] == 1


@pytest.mark.asyncio
async def test_users_directory_clamps_the_page_size(service_session: AsyncSession) -> None:
    """`limit` is caller-supplied; unclamped it is a whole-estate dump in one request."""
    await _firm_with_logins(
        service_session, alias="clamp-co", name="Clamp Co", emails=["c@clamp.test"]
    )
    assert (await list_users_directory(session=service_session, _p=SYSTEM, limit=10_000))[
        "limit"
    ] == 200
    assert (await list_users_directory(session=service_session, _p=SYSTEM, limit=0))["limit"] == 1
    assert (await list_users_directory(session=service_session, _p=SYSTEM, offset=-5))[
        "offset"
    ] == 0


# ---- GET /admin/audit: the server-side filters ----


async def _audit_row(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    action: str,
    when: str | None = None,
    user_id: object | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit.log (tenant_id, action, entity_type, detail, created_at, user_id) "
            "VALUES (:t, :a, 'test', '{}'::jsonb, COALESCE(CAST(:w AS timestamptz), now()), :u)"
        ),
        {"t": tenant_id, "a": action, "w": when, "u": user_id},
    )


@pytest.mark.asyncio
async def test_audit_actor_filter_does_not_read_a_wildcard_as_a_wildcard(
    service_session: AsyncSession,
) -> None:
    """`?actor=%` used to hand back the WHOLE trail: the filter builds `%<actor>%` and compares
    with ILIKE, so an operator who had narrowed the log to one person was shown everyone."""
    emails = ["rowan@like.test", "a_b@like.test", "axb@like.test"]
    tid = await _firm_with_logins(service_session, alias="aud-like", name="Aud Like", emails=emails)
    ids = dict(
        (
            await service_session.execute(
                text("SELECT email, id FROM auth.users WHERE tenant_id = :t"), {"t": tid}
            )
        ).all()
    )
    for email in ids:
        await _audit_row(
            service_session, tenant_id=tid, action=f"test.like.{email[:3]}", user_id=ids[email]
        )

    async def actions(actor: list[str] | None = None) -> set[str]:
        # Scoped to this firm AND these rows: the trail is estate-wide and shared.
        page = await list_audit(
            session=service_session, _p=SYSTEM, actor=actor, tenant_id=[tid], limit=200
        )
        return {str(r["action"]) for r in page["items"] if str(r["action"]).startswith("test.like")}

    assert await actions() == {"test.like.row", "test.like.a_b", "test.like.axb"}
    # Unescaped, `%` returned every audit row in the estate. And `_` is a literal underscore only
    # if the escaping AND the ESCAPE clause are both there: `a_b` must not find `axb@...`.
    assert await actions(["%"]) == set()
    assert await actions(["a_b"]) == {"test.like.a_b"}
    # Several actors still OR.
    assert await actions(["rowan", "a_b"]) == {"test.like.row", "test.like.a_b"}


@pytest.mark.asyncio
async def test_audit_filters_by_firm_action_and_date_range(service_session: AsyncSession) -> None:
    """All four filters are applied in SQL. `since` is inclusive, `until` exclusive."""
    a = await _firm_with_logins(service_session, alias="aud-a", name="Aud A", emails=[])
    b = await _firm_with_logins(service_session, alias="aud-b", name="Aud B", emails=[])
    await _audit_row(service_session, tenant_id=a, action="test.old", when="2026-01-01T00:00:00Z")
    await _audit_row(service_session, tenant_id=a, action="test.mid", when="2026-06-15T12:00:00Z")
    await _audit_row(service_session, tenant_id=b, action="test.mid", when="2026-06-15T12:00:00Z")

    # `list_audit` returns {items, tenants} (the tenants lookup feeds the firm-filter dropdown);
    # the rows are under "items".
    only_a = (await list_audit(session=service_session, _p=SYSTEM, tenant_id=a, limit=200))["items"]
    assert {r["action"] for r in only_a} == {"test.old", "test.mid"}
    assert all(r["tenant_id"] == a for r in only_a)
    # The firm's name is joined in, so the operator sees a name rather than an id.
    assert {r["tenant_name"] for r in only_a} == {"Aud A"}

    by_action = (
        await list_audit(
            session=service_session, _p=SYSTEM, tenant_id=a, action="test.mid", limit=200
        )
    )["items"]
    assert [r["action"] for r in by_action] == ["test.mid"]

    windowed = (
        await list_audit(
            session=service_session,
            _p=SYSTEM,
            tenant_id=a,
            since="2026-06-01",
            until="2026-06-16",
            limit=200,
        )
    )["items"]
    assert [r["action"] for r in windowed] == ["test.mid"]

    # `until` is exclusive: the day the event happened on excludes it.
    excluded = (
        await list_audit(
            session=service_session, _p=SYSTEM, tenant_id=a, until="2026-06-15", limit=200
        )
    )["items"]
    assert [r["action"] for r in excluded] == ["test.old"]


@pytest.mark.asyncio
async def test_audit_rejects_an_unparseable_date_instead_of_500ing(
    service_session: AsyncSession,
) -> None:
    """`since`/`until` used to be handed to Postgres as `CAST(:since AS timestamptz)`, so
    `?since=yesterday` raised a psycopg DataError and reached the caller as a 500 — for what is
    plainly a bad request."""
    for field in ("since", "until"):
        with pytest.raises(HTTPException) as exc:
            await list_audit(session=service_session, _p=SYSTEM, **{field: "yesterday"})
        assert exc.value.status_code == 422
        assert field in str(exc.value.detail)

    # An empty string is "no filter", not an error (the date inputs submit empty when cleared).
    cleared = await list_audit(session=service_session, _p=SYSTEM, since="", until="")
    assert isinstance(cleared["items"], list)


@pytest.mark.asyncio
async def test_audit_keyset_pagination_walks_backwards(service_session: AsyncSession) -> None:
    """`before_id` is the paging key; the trail is returned newest-first."""
    tid = await _firm_with_logins(service_session, alias="aud-page", name="Aud Page", emails=[])
    for i in range(4):
        await _audit_row(service_session, tenant_id=tid, action=f"test.p{i}")
    first = (await list_audit(session=service_session, _p=SYSTEM, tenant_id=tid, limit=2))["items"]
    assert [r["action"] for r in first] == ["test.p3", "test.p2"]
    second = (
        await list_audit(
            session=service_session,
            _p=SYSTEM,
            tenant_id=tid,
            limit=2,
            before_id=int(str(first[-1]["id"])),
        )
    )["items"]
    assert [r["action"] for r in second] == ["test.p1", "test.p0"]


@pytest.mark.asyncio
async def test_audit_clamps_the_page_size(service_session: AsyncSession) -> None:
    tid = await _firm_with_logins(service_session, alias="aud-clamp", name="Aud Clamp", emails=[])
    for i in range(3):
        await _audit_row(service_session, tenant_id=tid, action=f"test.c{i}")
    page = await list_audit(session=service_session, _p=SYSTEM, tenant_id=tid, limit=1)
    assert len(page["items"]) == 1


# ---- the permission gates ----


@pytest.mark.asyncio
async def test_permissions_catalogue_needs_one_of_the_three_read_permissions(
    service_session: AsyncSession,
) -> None:
    """The catalogue is read by all three role editors, so any of their read permissions unlocks it
    — but "any" is not "none"."""
    for slug in (TENANTS_READ.slug, MODEL_READ.slug, OPERATIONS_READ.slug):
        holder = Principal(user_id="u", permissions=[slug])
        assert isinstance(await list_permissions(session=service_session, principal=holder), list)
    with pytest.raises(HTTPException) as exc:
        await list_permissions(
            session=service_session, principal=Principal(user_id="u", permissions=["audit:read"])
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_tenant_list_reports_ready_only_when_both_secrets_are_set(
    service_session: AsyncSession,
) -> None:
    """`has_keys` must mean BOTH the WorkOS api key and the cookie password. Checking only the api
    key made a half-configured firm read "Ready" on this list while its own page said its keys were
    unset — the operator then wondered why nobody could sign in."""
    tenant = await provision_tenant(
        service_session, name="Half Co", alias="half-co", from_model=False
    )
    tid = int(tenant["id"])
    env_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id) "
                "VALUES (:t, 'production', 'c') RETURNING id"
            ),
            {"t": tid},
        )
    ).scalar_one()

    def _row(rows: list[dict[str, object]]) -> dict[str, object]:
        return next(r for r in rows if r["id"] == tid)

    listed = _row(await list_tenants(session=service_session, _p=SYSTEM))
    assert listed["has_environment"] is True
    assert listed["has_keys"] is False
    assert listed["has_orgs"] is False

    # Only the api key -> still not ready.
    await service_session.execute(
        text(
            "INSERT INTO auth.environment_secrets "
            "  (tenant_environment_id, workos_api_key_ct) VALUES (:e, 'ct')"
        ),
        {"e": env_id},
    )
    assert _row(await list_tenants(session=service_session, _p=SYSTEM))["has_keys"] is False

    await service_session.execute(
        text(
            "UPDATE auth.environment_secrets SET cookie_password_ct = 'ct' "
            "WHERE tenant_environment_id = :e"
        ),
        {"e": env_id},
    )
    assert _row(await list_tenants(session=service_session, _p=SYSTEM))["has_keys"] is True


@pytest.mark.asyncio
async def test_tenant_list_hides_the_model_tenant(service_session: AsyncSession) -> None:
    """The model tenant is a role preset, not a firm; it has its own /model page."""
    rows = await list_tenants(session=service_session, _p=SYSTEM)
    model_ids = list(
        (await service_session.execute(text("SELECT id FROM auth.tenants WHERE is_model")))
        .scalars()
        .all()
    )
    assert model_ids, "the model tenant should be seeded"
    assert not [r for r in rows if r["id"] in model_ids]


# ---- branding asset uploads ----


class _Upload:
    """The slice of `UploadFile` the handler uses. A REAL (1×1) PNG, not just the magic bytes: a
    shell-kind upload is now decoded to be downscaled (`shell_images.fit_shell_upload`), and
    bytes that do not decode are a 422 by design."""

    def __init__(self, content_type: str = "image/png") -> None:
        self.content_type = content_type

    async def read(self) -> bytes:
        from io import BytesIO

        from PIL import Image

        out = BytesIO()
        Image.new("RGBA", (1, 1)).save(out, "PNG")
        return out.getvalue()


@pytest.mark.asyncio
async def test_branding_asset_kinds_are_an_allowlist_including_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`icon` was missing from the allowlist, so the home-screen icon upload 400'd — while an
    unrecognised kind must still be refused rather than writing an arbitrary storage key."""
    from app import storage
    from app.routers.admin import _ASSET_KINDS, _store_branding_asset

    stored: list[tuple[int, str]] = []

    async def _put(
        _session: object,
        tenant_id: int,
        kind: str,
        data: bytes,
        content_type: str,
        **_kwargs: object,
    ) -> str:
        assert _kwargs == {"proxy": True}
        stored.append((tenant_id, kind))
        return f"https://cdn.test/{tenant_id}/{kind}.png"

    monkeypatch.setattr(storage, "put_branding_asset", _put)

    assert _ASSET_KINDS == {"logo", "logo-dark", "icon", "favicon", "card-art"}
    for kind in sorted(_ASSET_KINDS):
        result = await _store_branding_asset(None, 7, kind, _Upload())  # type: ignore[arg-type]
        assert result["src"].endswith(f"/{kind}.png")
    assert [k for _t, k in stored] == sorted(_ASSET_KINDS)

    with pytest.raises(HTTPException) as exc:
        await _store_branding_asset(None, 7, "banner", _Upload())  # type: ignore[arg-type]
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_branding_asset_upload_reports_unconfigured_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No object storage is a deployment state, not a bad request: the operator is told to paste a
    hosted URL instead of getting an opaque 500."""
    from app import storage
    from app.routers.admin import _store_branding_asset

    async def _put(*_a: object, **_k: object) -> str:
        raise storage.StorageUnavailable("no bucket")

    monkeypatch.setattr(storage, "put_branding_asset", _put)
    with pytest.raises(HTTPException) as exc:
        await _store_branding_asset(None, 7, "logo", _Upload())  # type: ignore[arg-type]
    assert exc.value.status_code == 409
    assert "paste a hosted" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_audit_read_gate_is_distinct_from_tenants_read(service_session: AsyncSession) -> None:
    """Reading the whole estate's audit trail is its own permission — `tenants:read` must not
    confer it (nor `audit:read` confer the tenant directory)."""
    from app.routers.admin import _audit_read, _tenants_read

    auditor = Principal(user_id="u", permissions=[AUDIT_READ.slug])
    assert _audit_read(principal=auditor) is auditor
    with pytest.raises(HTTPException):
        _tenants_read(principal=auditor)

    manager = Principal(user_id="u", permissions=[TENANTS_WRITE.slug])
    assert _tenants_read(principal=manager) is manager  # write implies read
    with pytest.raises(HTTPException):
        _audit_read(principal=manager)


@pytest.mark.asyncio
async def test_the_adviser_directory_does_not_cross_firms_without_rls(
    service_session: AsyncSession,
) -> None:
    """`service_role` bypasses `auth_users_scoped_read`. The directory still has to."""
    import json
    from uuid import uuid4

    from app.routers.crm import adviser_directory

    nonce = uuid4().hex[:8]
    home = await provision_tenant(
        service_session, name=f"Home {nonce}", alias=f"home-{nonce}", from_model=False
    )
    other = await provision_tenant(
        service_session, name=f"Other {nonce}", alias=f"other-{nonce}", from_model=False
    )
    for tenant_id, first in ((int(home["id"]), "Ada"), (int(other["id"]), "Bob")):
        user_id = (
            await service_session.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, "
                    "  first_name, last_name, type, is_adviser) "
                    "VALUES (:uid, :t, :w, :e, :first, 'Adviser', ARRAY['staff'], true) "
                    "RETURNING id"
                ),
                {
                    "uid": f"usr_{first.lower()}_{nonce}",
                    "t": tenant_id,
                    "w": f"user_{first.lower()}_{nonce}",
                    "e": f"{first.lower()}.{nonce}@example.test",
                    "first": first,
                },
            )
        ).scalar_one()
        await service_session.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user_id, "t": tenant_id},
        )
    await service_session.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {
            "c": json.dumps(
                {
                    "role": "admin",
                    "portal": "platform",
                    "tenant_id": int(home["id"]),
                    "tenant_ids": [int(home["id"])],
                }
            )
        },
    )
    names = await adviser_directory(service_session)
    assert "Ada Adviser" in names
    assert "Bob Adviser" not in names
