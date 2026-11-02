"""Which tenants a provider is enabled for, read from the book when the project loads.

`serve` compiles the project at startup and again when a model file changes. A firm enabled
after that would be absent until the next compile, and Refresh posts `tag:tenant:{uid}` — a
tag interlace rejects with 400 when it matches nothing. `tenant_reload` compares this query
with the compiled tags on each request and recompiles when they differ, so the next Refresh
sees the firm. A failed read is `None`, not an empty list: a blip must not look like every
firm was disabled.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from book_sink.config import SinkConfig, from_env

logger = logging.getLogger("book_sink.tenants")

_QUERY = """
SELECT tp.id            AS enablement_id,
       tp.tenant_id     AS tenant_id,
       t.uid            AS uid,
       t.name           AS name,
       tp.provider      AS provider,
       tp.config        AS config,
       ts.bucket        AS bucket
  FROM platform.tenant_platforms tp
  JOIN auth.tenants t ON t.id = tp.tenant_id
  -- INNER JOIN on purpose: a tenant with no active bucket has nowhere to put its own lake, and
  -- projecting it into the book while its parquet goes nowhere is the half-landed state the
  -- storage registry exists to prevent. Provision the bucket, restart, and it appears.
  JOIN storage.tenants ts ON ts.tenant_id = tp.tenant_id AND ts.status = 'active'
 WHERE tp.enabled
   AND tp.refresh_paused_at IS NULL
   AND tp.provider = %s
 ORDER BY tp.tenant_id
"""


@dataclass(frozen=True)
class TenantSlice:
    """One enabled (tenant, provider): everything the models need to name themselves."""

    enablement_id: int
    tenant_id: int
    uid: str
    name: str
    provider: str
    config: dict[str, Any]
    bucket: str

    @property
    def tag(self) -> str:
        """The selector the admin Refresh action posts: `tag:tenant:{uid}`."""
        return f"tenant:{self.uid}"


def read_enabled(provider: str, config: SinkConfig | None = None) -> list[TenantSlice] | None:
    """The provider's enabled tenants, or None when the book cannot be read.

    None is not an empty book. `interlace plan` with no DSN still has to show the provider's
    own models, and a reload must not drop every firm because one read failed. The reason is
    logged at WARNING — the same distinction the materialise ledger draws between "no data"
    and "could not read".
    """
    settings = config or from_env()
    if not settings.book_dsn:
        logger.warning("book_sink: BOOK_WRITER_DSN unset, so no tenant models are registered")
        return None
    try:
        import psycopg

        with psycopg.connect(settings.book_dsn) as conn, conn.cursor() as cur:
            cur.execute(_QUERY, (provider,))
            rows = cur.fetchall()
            columns = [column.name for column in cur.description or []]
    except Exception:
        logger.warning("book_sink: could not read enabled tenants for %s", provider, exc_info=True)
        return None

    slices: list[TenantSlice] = []
    for row in rows:
        record = dict(zip(columns, row, strict=True))
        raw_config = record["config"]
        slices.append(
            TenantSlice(
                enablement_id=int(record["enablement_id"]),
                tenant_id=int(record["tenant_id"]),
                uid=str(record["uid"]),
                name=str(record["name"] or record["uid"]),
                provider=str(record["provider"]),
                config=json.loads(raw_config) if isinstance(raw_config, str) else dict(raw_config),
                bucket=str(record["bucket"]),
            )
        )
    return slices


def enabled(provider: str, config: SinkConfig | None = None) -> list[TenantSlice]:
    """The provider's enabled tenants, or an empty list when the book is unreachable.

    Callers that would mistake a failed read for "no firms" want `read_enabled` instead.
    """
    found = read_enabled(provider, config)
    return [] if found is None else found
