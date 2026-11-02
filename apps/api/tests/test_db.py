"""Tests for the tenant RLS-context dependency (app.db.tenant_session)."""

import pytest
from fastapi import HTTPException

from app.auth import Principal
from app.db import tenant_session
from tests.conftest import fake_request


async def test_tenant_session_fails_closed_without_org() -> None:
    """A principal with no organisation must be refused, not handed an
    unscoped session that would bypass RLS. The 403 is raised before any DB
    connection is opened, so this needs no Postgres."""
    principal = Principal(user_id="user_01", organisation_id=None)
    gen = tenant_session(fake_request(), principal=principal)
    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403
    await gen.aclose()


# NOTE: the happy path (org context is set and does not leak across pooled
# connections) needs a real Postgres fixture; add it alongside the first
# RLS-backed migration.
