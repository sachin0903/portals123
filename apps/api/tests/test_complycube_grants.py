"""The tipping-off grants on ``compliance.*`` (0221), against a real Postgres.

A firm's staff read a check's status-level columns and nothing of its result. Matches, the firm
webhook outbox and the provider inbox are not readable at all, and the DML trail of any
``compliance.*`` row is withheld from a firm (0095's carve-out, extended).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> Iterator[dict[str, object]]:
    """One firm with a client, a referred check with a match, a delivery and an inbox row."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"ccg_{s}", "a": f"ccg-{s}", "n": f"Grants {s}"},
            ).scalar_one()
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :u, 'individual', 'Okafor', 'advised') RETURNING id"
                ),
                {"t": tenant, "u": f"ccg{s}"},
            ).scalar_one()
        )
        client = int(
            c.execute(
                text(
                    "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id) "
                    "VALUES (:t, :c, :p) RETURNING id"
                ),
                {"t": tenant, "c": contact, "p": f"cli_ccg_{s}"},
            ).scalar_one()
        )
        check = c.execute(
            text(
                "INSERT INTO compliance.checks (tenant_id, client_id, kind, status, "
                "  provider_check_id, outcome, result, body, decided_by, decision_comment) "
                "VALUES (:t, :c, 'aml', 'refer', :p, 'attention', "
                "  '{\"breakdown\": {\"matches\": [{\"name\": \"PEP\"}]}}', '{}', 'ops', 'x') "
                "RETURNING id, uid"
            ),
            {"t": tenant, "c": client, "p": f"chk_ccg_{s}"},
        ).one()
        c.execute(
            text(
                "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, name) "
                "VALUES (:t, :c, 'm_1', 'A Politically Exposed Person')"
            ),
            {"t": tenant, "c": check.id},
        )
        webhook = int(
            c.execute(
                text(
                    "INSERT INTO compliance.webhooks (tenant_id, url, secret_ct) "
                    "VALUES (:t, 'https://firm.example', 'ct') RETURNING id"
                ),
                {"t": tenant},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO compliance.webhook_deliveries (tenant_id, webhook_id, event, payload) "
                "VALUES (:t, :w, 'check.updated', '{}')"
            ),
            {"t": tenant, "w": webhook},
        )
        c.execute(
            text(
                "INSERT INTO compliance.events (tenant_id, provider_event_id, event_type, payload) "
                "VALUES (:t, :e, 'check.completed', '{\"outcome\": \"attention\"}')"
            ),
            {"t": tenant, "e": f"evt_ccg_{s}"},
        )
    yield {"tenant": tenant, "check_uid": check.uid, "nonce": s}
    with super_engine.connect() as c:
        for stmt in (
            "DELETE FROM compliance.webhook_deliveries WHERE tenant_id = :t",
            "DELETE FROM compliance.matches WHERE tenant_id = :t",
            "DELETE FROM compliance.checks WHERE tenant_id = :t",
            "DELETE FROM compliance.events WHERE tenant_id = :t",
            "DELETE FROM compliance.webhooks WHERE tenant_id = :t",
            "DELETE FROM compliance.clients WHERE tenant_id = :t",
            "DELETE FROM crm.contacts WHERE tenant_id = :t",
            "DELETE FROM auth.tenants WHERE id = :t",
        ):
            c.execute(text(stmt), {"t": tenant})


def _staff(seed: dict[str, object]) -> dict[str, object]:
    return {
        "sub": f"wos_ccg_{seed['nonce']}",
        "role": "adviser",
        "portal": "platform",
        "permissions": ["contacts:read_all"],
        "user_uuid": f"usr_ccg_{seed['nonce']}",
        "tenant_id": seed["tenant"],
        "tenant_ids": [seed["tenant"]],
        "contact_ids": [],
        "account_ids": [],
    }


def _denied(app_engine: Engine, claims: dict[str, object], sql: str) -> bool:
    with principal_conn(app_engine, claims) as conn:
        try:
            conn.execute(text(sql)).all()
        except ProgrammingError as exc:
            return "permission denied" in str(exc)
    return False


def test_a_firm_reads_its_checks_status_and_not_the_result(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff(seed)) as conn:
        rows = conn.execute(
            text(
                "SELECT uid, kind, status, origin, attempt, stale_at, created_at, updated_at "
                "FROM compliance.checks"
            )
        ).all()
    assert [(row.uid, row.status) for row in rows] == [(seed["check_uid"], "refer")]
    for column in (
        "result",
        "body",
        "outcome",
        "decided_by",
        "decision_comment",
        "monitoring",
        "alerted_at",
    ):
        sql = f"SELECT {column} FROM compliance.checks"  # noqa: S608 - a fixed column list
        assert _denied(app_engine, _staff(seed), sql), column
    assert _denied(app_engine, _staff(seed), "SELECT * FROM compliance.checks")


@pytest.mark.parametrize(
    "table", ["compliance.matches", "compliance.webhook_deliveries", "compliance.events"]
)
def test_a_firm_cannot_read_matches_deliveries_or_the_inbox(
    app_engine: Engine, seed: dict[str, object], table: str
) -> None:
    assert _denied(app_engine, _staff(seed), f"SELECT count(*) FROM {table}")  # noqa: S608


def test_a_firm_cannot_read_the_compliance_dml_trail(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    with principal_conn(app_engine, _staff(seed)) as conn:
        compliance_rows = conn.execute(
            text("SELECT count(*) FROM audit.log WHERE entity_type LIKE 'compliance.%'")
        ).scalar_one()
        own_contacts = conn.execute(
            text("SELECT count(*) FROM audit.log WHERE entity_type = 'crm.contacts'")
        ).scalar_one()
    # The positive control: the firm still reads its own non-screening trail.
    assert own_contacts > 0
    assert compliance_rows == 0


def test_the_audit_read_policy_carves_out_compliance_and_aml(super_engine: Engine) -> None:
    with super_engine.connect() as c:
        qual = c.execute(
            text(
                "SELECT qual FROM pg_policies WHERE schemaname = 'audit' "
                " AND tablename = 'log' AND policyname = 'audit_log_read'"
            )
        ).scalar_one()
    assert "compliance.%" in qual
    assert "aml.%" in qual
    assert "tenant_lineage" in qual


def test_the_new_tables_are_forced_and_have_no_authenticated_grant(super_engine: Engine) -> None:
    with super_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "  has_table_privilege('authenticated', c.oid, 'SELECT') AS readable "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'compliance' AND c.relname IN ('matches', 'webhook_deliveries')"
            )
        ).all()
    assert {row.relname: (row[1], row[2], row[3]) for row in rows} == {
        "matches": (True, True, False),
        "webhook_deliveries": (True, True, False),
    }
