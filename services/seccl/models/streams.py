"""Interlace streams fed by the Seccl webhook gateway.

One stream per Seccl outgoing webhook. The gateway enriches thin webhook events
(id + status) into full records before publishing, so these tables hold current
entity state, deduplicated by ``_event_key`` (entity id + updateDate). Schemas
declare only the stable spine; ``evolve`` turns any further field Seccl sends
into a real column. Staging models merge these deltas onto the raw baselines
and are re-run automatically on every flush.
"""

from interlace import stream


def _spine(**columns: str) -> dict[str, str]:
    return {"_event_key": "text", "firm_id": "text", **columns}


@stream(
    name="transactions",
    schema=_spine(id="text", transaction_type="text", status="text", update_date="timestamp"),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def transactions() -> None:
    """Transaction created / status changed / deleted (gateway enriches by id)."""


@stream(
    name="positions",
    schema=_spine(id="text", account_id="text", position_type="text", update_date="timestamp"),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def positions() -> None:
    """Position updated (gateway enriches by position id)."""


@stream(
    name="clients",
    schema=_spine(client_id="text", update_date="timestamp"),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def clients() -> None:
    """Client created or updated (full entity on create; gateway fetches on update)."""


@stream(
    name="exceptions",
    schema=_spine(id="text"),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def exceptions() -> None:
    """Exception notification created (full notification entity embedded)."""


@stream(
    name="messages",
    schema=_spine(client_id="text", message_type="text", update_date="timestamp"),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def messages() -> None:
    """Message sent to a client."""


@stream(
    name="transfers",
    schema=_spine(
        transaction_id="text", account_id="text", transfer_status="text", update_date="timestamp"
    ),
    idempotency_key="_event_key",
    on_schema_drift="evolve",
    retention="30d",
)
def transfers() -> None:
    """Transfer rejected (the only transfer event Seccl emits)."""
