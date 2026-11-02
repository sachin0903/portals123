from typing import Any

import pytest

from app import environments, storage
from app.routers import internal


@pytest.mark.asyncio
async def test_public_asset_resolves_tenant_from_host_and_returns_hardened_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def env_for_host(_session: Any, host: str) -> dict[str, object]:
        assert host == "client.example.test"
        return {"tenant_id": 42}

    async def asset(_session: Any, tenant_id: int, name: str) -> tuple[bytes, str]:
        assert (tenant_id, name) == (42, "logo-0123456789abcdef.svg")
        return b"<svg/>", "image/svg+xml"

    monkeypatch.setattr(environments, "env_config_by_host", env_for_host)
    monkeypatch.setattr(storage, "get_public_asset", asset)

    response = await internal.public_asset(
        "logo-0123456789abcdef.svg",
        host="client.example.test",
        operations=False,
        session=None,
    )
    assert response.body == b"<svg/>"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
