"""The tenant slice, as SQL. Ported from app/fanout.py, and these are its scars.

Every case here is a slice that came out silently wrong once: over-projection reads as one firm
seeing another's rows, under-projection reads as "this firm has no data", and neither raises.
"""

from __future__ import annotations

import pytest

from book_sink import predicate


def test_a_conformed_slice_is_one_containment_test() -> None:
    where = predicate.conformed(["1", "9"])
    assert where == "(len(lineage) = 0 OR list_has_any(lineage, ['1', '9']))"


def test_global_reference_data_projects_whole() -> None:
    """An empty lineage means the row belongs to no firm -- an instrument, a firm-wide fee
    register. Scoping it to the firm ROOT instead looks tighter and is wrong: containment tests
    ancestry, so a root-owned row matches only a tenant holding the root, and
    platform.third_party_providers projected 0 of 7 rows for a tenant on a sub-node."""
    assert "len(lineage) = 0 OR" in predicate.conformed(["7"])


def test_a_quote_in_an_key_cannot_close_the_predicate() -> None:
    assert predicate.conformed(["a'b"]) == "(len(lineage) = 0 OR list_has_any(lineage, ['a''b']))"


def test_an_enablement_with_no_keys_is_refused() -> None:
    """`true` would hand this tenant the provider's whole book and `false` would look like a firm
    with no data. Neither is a slice, so neither is offered."""
    with pytest.raises(predicate.PredicateUnavailable):
        predicate.conformed([])


def test_sei_narrows_a_dimension_only_on_the_axes_it_carries() -> None:
    """platform.firms has no subfirm_id and platform.dsr_teams holds one scalar team, so the
    entity shape raised `Binder Error: Referenced column "subfirm_id" not found` and aborted the
    whole tenant's projection the moment an operator picked a subfirm -- which the pickers exist
    to do."""
    config = {"firm_id": "F1", "subfirm_ids": ["S1"], "dsr_ids": ["D1", "D2"]}

    entity = predicate.sei(config, "accounts")
    assert "subfirm_id" in entity and "list_has_any(dsr_team_ids" in entity

    firms = predicate.sei(config, "firms")
    assert "subfirm_id" not in firms and "dsr_team" not in firms

    teams = predicate.sei(config, "dsr_teams")
    assert "CAST(dsr_team_id AS VARCHAR) IN ('D1', 'D2')" in teams
    assert "list_has_any" not in teams, "one team per row is equality, not containment"


def test_sei_reference_data_is_not_narrowed_at_all() -> None:
    # `instruments` is the conformed §3 name; SEI's own report is Assets and `raw.assets` keeps it.
    assert predicate.sei({"firm_id": "F1"}, "instruments") == "true"
    # The ceding-counterparty register is the same shape: firm_id is null on every code.
    assert predicate.sei({"firm_id": "F1"}, "transfer_providers") == "true"


def test_the_sei_org_dimension_is_narrowed_by_containment_not_by_the_triple() -> None:
    """`platform.nodes` supersedes firms/subfirms/dsr_teams and carries `lineage`, NOT `firm_id`.

    Third time this shape has bitten: the triple against a table that does not carry it is a Binder
    Error which aborts the tenant's whole projection, and the ledger records that as "unavailable" —
    the same reading as "this firm has no data with this provider".
    """
    where = predicate.sei({"firm_id": "F1"}, "nodes")
    assert "list_has_any(lineage" in where
    assert "firm_id" not in where, "the org dimension has no firm_id column to narrow on"


def test_narrowing_within_a_firm_does_not_narrow_its_org_TREE() -> None:
    """Deliberate, and the reason is resolution rather than tidiness: an account's `key` can
    name any node of its firm's tree, and an org that is not projected is an unresolvable parent —
    a counted reject that reads as missing data. It is all one firm's own structure either way."""
    firm_only = predicate.sei({"firm_id": "F1"}, "nodes")
    narrowed = predicate.sei({"firm_id": "F1", "subfirm_ids": ["S1"], "dsr_ids": ["D1"]}, "nodes")
    assert firm_only == narrowed
    # …while the ENTITY tables still narrow on every axis the enablement names.
    accounts = predicate.sei({"firm_id": "F1", "subfirm_ids": ["S1"]}, "accounts")
    assert "subfirm_id" in accounts


def test_sei_clients_match_a_subfirm_through_the_accounts_they_hold() -> None:
    """A scalar `subfirm_id IN (...)` matches nobody: the column is null on the whole drop.

    `list_has_any` over `subfirm_ids` is the account's subfirm, and it has to be a list — one
    client in the dev book holds accounts at two subfirms, and a scalar pick would hide one.
    """
    where = predicate.sei({"firm_id": "3106", "subfirm_ids": ["704254", "704766"]}, "clients")
    assert "list_has_any(subfirm_ids, ['704254', '704766'])" in where
    assert "CAST(subfirm_id AS VARCHAR)" not in where
    accounts = predicate.sei({"firm_id": "3106", "subfirm_ids": ["704254"]}, "accounts")
    assert "CAST(subfirm_id AS VARCHAR) IN ('704254')" in accounts


def test_an_absent_sei_axis_does_not_narrow() -> None:
    """An absent key means "the whole firm", not "match nothing" -- the difference between a
    tenant seeing its book and a tenant seeing an empty one."""
    where = predicate.sei({"firm_id": "F1"}, "accounts")
    assert where == "CAST(firm_id AS VARCHAR) IN ('F1')"


def test_sei_without_a_firm_is_refused() -> None:
    with pytest.raises(predicate.PredicateUnavailable):
        predicate.sei({}, "accounts")


def test_morgan_lloyd_filters_by_containment_like_any_conformed_provider() -> None:
    """ML's org axis is the single Microsoft tenant, so its conformed rows carry a one-element
    lineage and there is no ML-specific branch to keep in step."""
    where = predicate.for_provider("morgan-lloyd", {"tenant_ids": ["guid-1"]}, "clients")
    assert where == "(len(lineage) = 0 OR list_has_any(lineage, ['guid-1']))"


def test_an_unknown_provider_is_refused_not_projected() -> None:
    with pytest.raises(predicate.PredicateUnavailable):
        predicate.for_provider("nobody", {}, "accounts")


# The columns each SEI conformed relation actually publishes
# (`services/sei/models/platform/*.sql`). Ported from apps/api/tests/test_providers.py when
# app/fanout.py was deleted, retargeted at `predicate.sei` — the predicate moved here and the shape
# it has to bind against is a fact about the models, not about the module that reads them.
#
# `orgs` supersedes firms/subfirms/dsr_teams (docs/platform-model.md §3) and carries NO `firm_id`,
# which is the third outing of this bug: the triple against a relation that lacks a column is a
# Binder Error that aborts the tenant's whole projection, recorded as "unavailable" and read as
# "this firm has no data". The old dimension names are kept below because the predicate still names
# them and a provider mid-rename would otherwise lose its narrowing silently.
_SEI_COLUMNS: dict[str, list[str]] = {
    "nodes": ["key", "lineage", "name", "type"],
    "firms": ["firm_id", "firm_name", "as_of_date"],
    "subfirms": ["subfirm_id", "subfirm_name", "firm_id", "firm_name", "as_of_date"],
    "dsr_teams": ["dsr_team_id", "dsr_team_name", "firm_id", "firm_name", "as_of_date"],
    "tenancy": ["account_number", "firm_id", "subfirm_id", "dsr_team_ids"],
    "accounts": ["account_number", "firm_id", "subfirm_id", "dsr_team_ids"],
    # `subfirm_ids`, not a scalar: the Clients report leaves RESIDING_SUBFIRM_ID blank, and the
    # list is the subfirms of the accounts the client holds (platform.tenancy).
    "clients": ["client_id", "firm_id", "subfirm_id", "subfirm_ids", "dsr_team_ids"],
    "positions": ["account_number", "firm_id", "subfirm_id", "dsr_team_ids"],
    # The container below an account. Its tenancy comes from the ACCOUNT (a portfolio has no grant
    # of its own), so it carries the same triple as everything else that hangs off one.
    "portfolios": ["portfolio_id", "account_external_id", "firm_id", "subfirm_id", "dsr_team_ids"],
    "transactions": ["transaction_id", "firm_id", "subfirm_id", "dsr_team_ids"],
    "valuations": ["account_number", "firm_id", "subfirm_id", "dsr_team_ids"],
    # THE FIRM AXIS ONLY, and stated here because this fixture is what proves it binds. A SEI
    # employee user has a `FIRM_ID` and no subfirm and no DSR team — deliberately NO
    # `subfirm_id`/`dsr_team_ids` in this list — so a predicate that applied the entity triple
    # would raise a Binder Error here rather than in a nightly run against a real firm.
    "users": ["external_id", "email", "firm_id"],
    # Discovered from the accounts that name a model, published per (model, firm) — so `firm_id`
    # and deliberately NO finer axis, for the same reason `users` has none.
    "models": ["external_id", "name", "firm_id"],
    # Periodic fee packages, priced by the firm — `firm_id` and deliberately NO finer axis.
    "fee_packages": ["external_id", "name", "firm_id"],
    # Reference data: no tenancy columns at all, so it projects whole.
    "instruments": ["instrument_id", "isin"],
    # The ceding-counterparty register. firm_id is null on every Reference code, so the
    # predicate does not read a tenancy column.
    "transfer_providers": ["external_id", "name"],
}


def test_the_column_fixture_covers_every_relation_sei_projects() -> None:
    """A relation added to SEI's register with no column list here would skip the binding test
    below in silence, which is the whole failure this file exists to catch."""
    from book_sink.entities import PROVIDER_ENTITIES, lake_relations

    projected = set(lake_relations("sei")) if PROVIDER_ENTITIES.get("sei") else set()
    assert projected <= set(_SEI_COLUMNS), sorted(projected - set(_SEI_COLUMNS))


@pytest.mark.parametrize("table", sorted(_SEI_COLUMNS))
def test_the_sei_predicate_binds_against_every_relation(table: str) -> None:
    """Every narrowing axis populated — the case that used to fail. The predicate either binds
    against that relation's real columns or raises, which IS the assertion."""
    duckdb = pytest.importorskip("duckdb")
    columns = _SEI_COLUMNS[table]
    projection = ", ".join(
        f"['x'] AS {c}" if c.endswith("_ids") or c == "lineage" else f"'x' AS {c}" for c in columns
    )
    config = {"firm_id": "3106", "subfirm_ids": ["704254"], "dsr_ids": ["200119"]}
    where = predicate.sei(config, table)
    conn = duckdb.connect(":memory:")
    conn.execute(f"CREATE TABLE t AS SELECT {projection}")
    conn.execute(f"SELECT count(*) FROM t WHERE {where}")  # noqa: S608


def test_a_dsr_grant_is_containment_because_an_account_holds_several() -> None:
    """SEI grants an account ~3.5 DSR teams at once, so the column is a LIST and the test has to be
    containment. An `IN` against it would never match — an empty slice, reported as no data."""
    where = predicate.sei({"firm_id": "3106", "dsr_ids": ["a", "b"]}, "positions")
    assert "list_has_any(dsr_team_ids, ['a', 'b'])" in where
    assert "dsr_team_ids IN" not in where


class TestTheStaffRelationIsNarrowedOnTheFirmAlone:
    """`users` is the FOURTH relation to hit SEI's tenancy-shape trap, so it is pinned twice.

    `UserDetail` is twelve columns and neither a subfirm nor a DSR team is among them. Applying
    the entity triple would raise `Binder Error: Referenced column "subfirm_id" not found` and
    abort the whole tenant's projection — and the trigger is an operator picking a subfirm in the
    admin enablement picker, which is what that picker is for. Getting it wrong the other way is
    worse than an error: over-projection would show one firm another firm's employees.
    """

    CONFIG = {"firm_id": "3102", "subfirm_ids": ["9001"], "dsr_ids": ["200119"]}

    def test_a_subfirm_or_dsr_choice_does_not_reach_the_staff_relation(self) -> None:
        clause = predicate.for_provider("sei", self.CONFIG, "users")
        assert "subfirm_id" not in clause, clause
        assert "dsr_team_ids" not in clause, clause
        assert "dsr_team_id" not in clause, clause
        assert "'3102'" in clause

    def test_the_firm_is_still_applied_so_staff_do_not_cross_firms(self) -> None:
        """The failure this half guards is silent rather than loud: a missing clause is one
        firm reading another firm's employees."""
        clause = predicate.for_provider("sei", {"firm_id": "3106"}, "users")
        assert "firm_id" in clause
        assert "'3106'" in clause
        assert clause != "true"

    def test_an_enablement_with_no_firm_refuses_rather_than_widening(self) -> None:
        with pytest.raises(predicate.PredicateUnavailable):
            predicate.for_provider("sei", {"subfirm_ids": ["9001"]}, "users")
