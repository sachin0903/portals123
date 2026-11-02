"""The pipeline's landing zone and reject ledger (0062), against a real Postgres.

Two properties are load-bearing and neither is visible from reading the migration alone: the
reject ledger must be genuinely append-only (enforced by withheld privilege, not by a trigger
somebody can drop), and `book_writer` must be narrow — a pipeline credential that could read
identity documents or screening outcomes would make the whole scoped-role exercise decorative.
"""

import pytest
from sqlalchemy import Engine, text

# What the merge UPSERTS, and therefore what book_writer may select/insert/update.
# `platform.merge_rejects` is deliberately absent: it is append-only, so UPDATE is withheld from
# every role including this one — see test_reject_ledger_is_append_only_even_for_service_role.
MERGE_TABLES = [
    ("platform", "nodes"),
    ("platform", "instruments"),
    ("platform", "accounts"),
    ("platform", "models"),
    ("platform", "model_platforms"),
    ("platform", "valuations"),
    ("platform", "transactions"),
    ("platform", "products"),
    ("platform", "links"),
    ("crm", "contacts"),
]

# What it must NOT reach. service_role has ALL on every one of these.
FORBIDDEN_TABLES = [
    ("auth", "users"),
    ("auth", "organisation_memberships"),
    ("documents", "files"),
    ("documents", "documents"),
    ("storage", "tenant_service_user_secrets"),
    ("flows", "cases"),
]


def _priv(conn, schema: str, table: str, privilege: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT has_table_privilege('book_writer', :rel, :priv)"),
            {"rel": f"{schema}.{table}", "priv": privilege},
        ).scalar_one()
    )


def test_book_writer_reaches_the_merged_relations(super_engine: Engine) -> None:
    with super_engine.connect() as conn:
        for schema, table in MERGE_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                assert _priv(conn, schema, table, privilege), f"{schema}.{table} {privilege}"


def test_book_writer_reaches_nothing_else(super_engine: Engine) -> None:
    """The whole of the blast-radius reduction. BYPASSRLS is unavoidable for a cross-tenant
    writer, so grant scope is the only thing separating this credential from service_role."""
    with super_engine.connect() as conn:
        for schema, table in FORBIDDEN_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not _priv(conn, schema, table, privilege), (
                    f"book_writer must not have {privilege} on {schema}.{table}"
                )


def test_book_writer_can_append_to_the_reject_ledger(super_engine: Engine) -> None:
    """It must be able to record its own refusals — a reject that cannot be written is a reject
    nobody ever sees, which is the state this whole ledger exists to end."""
    with super_engine.connect() as conn:
        assert _priv(conn, "platform", "merge_rejects", "SELECT")
        assert _priv(conn, "platform", "merge_rejects", "INSERT")


def test_book_writer_cannot_delete_the_book(super_engine: Engine) -> None:
    """It upserts; it never removes. A provider dropping a row from its feed is not authority to
    delete a firm's account — that is an ops decision with its own audit trail."""
    with super_engine.connect() as conn:
        for schema, table in MERGE_TABLES:
            assert not _priv(conn, schema, table, "DELETE"), f"{schema}.{table} DELETE"


def test_book_writer_is_not_a_member_of_service_role(super_engine: Engine) -> None:
    """Otherwise every grant above is theatre: it could SET ROLE service_role and have ALL."""
    with super_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT pg_has_role('book_writer', 'service_role', 'USAGE')")
            ).scalar_one()
            is False
        )


@pytest.mark.parametrize("table", ["platform.merge_rejects", "platform.merge_runs"])
def test_the_ledgers_are_append_only_even_for_service_role(
    super_engine: Engine, table: str
) -> None:
    """Enforced by withheld privilege rather than a trigger, so it cannot be dropped. A reject is
    a statement about one run; a correction is a new row, never an edit.

    BOTH ledgers, because they answer halves of one question and an editable half is no answer:
    `merge_rejects` records what was refused, `merge_runs` (0076) records what the merge did — the
    latter being the only thing that separates "this firm has no data with this custodian" from
    "the chain never ran".
    """
    with super_engine.connect() as conn:
        for role in ("service_role", "book_writer"):
            for privilege in ("UPDATE", "DELETE"):
                granted = conn.execute(
                    text("SELECT has_table_privilege(:role, :table, :priv)"),
                    {"role": role, "table": table, "priv": privilege},
                ).scalar_one()
                assert granted is False, f"{role} must not {privilege} {table}"
            appends = conn.execute(
                text("SELECT has_table_privilege(:role, :table, 'INSERT')"),
                {"role": role, "table": table},
            ).scalar_one()
            assert appends is True, f"{role} must be able to append to {table}"


def test_inbound_is_unreachable_from_the_request_path(super_engine: Engine) -> None:
    """`authenticated` is granted nothing on the landing zone — a stronger statement than a
    policy, since there is no policy to get wrong."""
    with super_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT has_schema_privilege('authenticated', 'inbound', 'USAGE')")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(
                text("SELECT has_schema_privilege('book_writer', 'inbound', 'CREATE')")
            ).scalar_one()
            is True
        )


@pytest.mark.asyncio
async def test_a_reject_records_the_source_key_not_the_row(service_session) -> None:
    """The ledger chases a refusal back to source without becoming a quarantine: a rejected
    transaction is still client data, and `row_key` is the provider's identifier, not the row."""
    tenant_id = (
        await service_session.execute(text("SELECT id FROM auth.tenants ORDER BY id LIMIT 1"))
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO platform.merge_rejects (tenant_id, provider, entity, reason, row_key) "
            "VALUES (:t, 'seccl', 'transactions', "
            "        'unmapped type: Expectation/FAD Gross Withdrawal', 'txn_0001')"
        ),
        {"t": tenant_id},
    )
    row = (
        (
            await service_session.execute(
                text(
                    "SELECT provider, entity, reason, row_key FROM platform.merge_rejects "
                    "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["entity"] == "transactions"
    assert row["reason"].startswith("unmapped type:")
    assert row["row_key"] == "txn_0001"
    # The columns a quarantine would have, and this deliberately does not.
    cols = {
        r[0]
        for r in (
            await service_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'platform' AND table_name = 'merge_rejects'"
                )
            )
        ).all()
    }
    assert "payload" not in cols and "data" not in cols and "row" not in cols
