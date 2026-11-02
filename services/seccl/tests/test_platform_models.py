"""Every conformed model binds against the real column shapes, and publishes the contract.

The warehouse needs credentials and a daemon, so `interlace plan` cannot run here. What can
run is DuckDB itself: stub the raw/staging tables with the columns and types the extractors
actually land, execute each `models/platform/*.sql`, and check what comes back. That is the
class of failure worth catching early — SEI's fan-out once aborted a whole tenant on a
`Binder Error: Referenced column "subfirm_id" not found`, which is exactly this test.

The stubs are empty. A model that binds against zero rows can still be wrong about the data,
but it cannot be wrong about a column name, a type mix, or a struct field — and those are what
break a deploy.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

# The spine every conformed entity publishes (docs/platform-model.md §3). The materialiser
# reads these by name for every provider, so a missing one is a broken sync, not a lint.
CONFORMED_SPINE = {"external_id", "key", "lineage", "as_of_date", "data"}

# Per-entity conformed fields the materialiser maps into OLTP. Extra columns are fine --
# providers publish more than the canonical model stores -- but these must be present.
CONTRACT: dict[str, set[str]] = {
    "nodes": {"name", "type_raw", "parent_key"},
    "instruments": {"name", "asset_type", "isin", "sedol", "cusip", "currency"},
    "clients": {
        "client_type",
        "first_name",
        "surname",
        "title",
        "date_of_birth",
        "nationality",
        "country_of_domicile",
        "national_identifier",
        "email",
        "phone",
        "address",
        "status",
    },
    "accounts": {
        "account_name",
        "product_key",
        "status",
        "base_currency",
        "model_key",
        "owner_external_ids",
        "is_discretionary",
        # The account's own terms (`derived/account_terms.sql`). 0002 created all three book
        # columns and nothing ever wrote to any of them, so the record's Fees and Regular-payments
        # cards read "none recorded" for every account in every book.
        "fees",
        "payments",
        "withdrawals",
        "fee_package_key",
    },
    "valuations": {
        "account_external_id",
        "market_value",
        "cash_value",
        "book_cost",
        "unrealised_gain",
        "opening_value",
        "currency",
    },
    "positions": {
        "account_external_id",
        "instrument_key",
        "isin",
        "quantity",
        "price",
        "market_value",
        "book_cost",
        "unrealised_gain",
        "currency",
    },
    "transactions": {
        "account_external_id",
        "type",
        "source_type",
        "source_subtype",
        "instrument_key",
        "isin",
        "quantity",
        "unit_price",
        "gross_amount",
        "net_amount",
        "fee_amount",
        "tax_amount",
        "currency",
        "transaction_date",
        "settlement_date",
        "status",
        "is_reversal",
        "order_key",
        # The statement layer. `report_group_kind` is the custodian's own bucket; the rest
        # are the per-movement facts a ledger row is read to answer: what it cost, what it
        # realised, when the entitlement was struck, what it did to the wrapper's allowance.
        "report_group_kind",
        "dealing_charges",
        "other_charges",
        "fee_type",
        "book_cost",
        "trade_date",
        "equalisation_payment",
        "subscription_amount",
        "realised_gain",
    },
    # The version-shaped payload the materialiser expands into model_versions /
    # model_allocations / model_commentary. `target_return` is deliberately absent: Seccl has no
    # field for it and the sync must leave the Graphene-side value alone.
    "models": {"name", "aim", "product_key", "risk_rating", "allocations", "commentary"},
    # Both transfer directions off the same master party (there is no second endpoint), so the
    # direction booleans are part of the contract, not an extra.
    "transfer_providers": {
        "name",
        "status",
        "is_active",
        "origo_id",
        "atg_reference",
        "products",
        "rails",
        "transfers_in",
        "transfers_out",
    },
    "third_party_providers": {"name", "product_key", "scheme_name"},
    # The firm's priced charges. `rule` is the fixed | percentage | tiered payload
    # platform.fee_packages stores; `excluded_products` is the exclusion Seccl states, which the
    # materialiser inverts into `applies_to` because it owns the canonical wrapper set.
    "fee_packages": {
        "firm_key",
        "name",
        "category",
        "fee_category_raw",
        "fee_type",
        "frequency",
        "rule",
        "charge_basis",
        "payer",
        "currency",
        "excluded_products",
    },
}

# What the extractors land, as they land it. Anything Seccl nests is a JSON string
# (seccl_sync.records: "nested structures become JSON strings"), and anything whose scalar
# types disagree across records widens to VARCHAR -- both reproduced here, because both are
# what the SQL has to survive.
RAW_TABLES: dict[str, str] = {
    "raw.nodes": """
        id bigint, parent_node_id bigint, firm_id varchar, name varchar, node_type varchar
    """,
    "raw.assets": """
        id varchar, isin varchar, currency varchar, instrument_type varchar, type varchar,
        long_name varchar, short_name varchar, custom_name varchar, mic varchar,
        primary_market varchar, status varchar, country_of_issue varchar,
        audit_details json, update_id varchar
    """,
    "raw.clients": """
        id varchar, node_id varchar, firm_id varchar, client_type varchar, first_name varchar,
        middle_name varchar, surname varchar,
        -- Carried by an `Entity` client (a trust, a company) in place of the name parts.
        entity_name varchar,
        title varchar, date_of_birth varchar, nationality varchar,
        nationalities json, tax_domicile varchar, national_insurance_no varchar, email varchar,
        -- The number is an object -- {"number", "isMobile", "locale"} -- per the convention
        -- above, and `mobile` was declared varchar here for as long as this file existed. That
        -- single wrong word is why the stub could not catch the model handing the whole object
        -- through as `phone`: the SQL bound against a string, so nothing failed, and a client's
        -- record showed the JSON. A stub that disagrees with the warehouse tests nothing.
        --
        -- `phone` is the landline, and it is the column that taught this file the OTHER half of
        -- the rule. It was declared here and read by the model, and it aborted the real warehouse
        -- (`Values list "c" does not have a column named "phone"`) because no client in the book
        -- carries one, so `records_to_table` never landed the column. A stub cannot promise what
        -- the extractor does not: it is safe to read here only now that `_CLIENT_SPINE` declares
        -- it. The two must be added together, which is what the spine tripwire below enforces.
        --
        -- The real payload is far wider than this (gender, employers, kyc_data, vulnerability,
        -- marital_status and some thirty more). Stub a field when a model reads it, and put it in
        -- the spine in the same change.
        mobile json, phone json,
        address_detail json, status varchar, record_creation_date varchar,
        bank_details json, audit_details json, update_id varchar
    """,
    "raw.accounts": """
        id varchar, node_id varchar, firm_id varchar, name varchar, account_type varchar,
        status varchar, currency varchar, positions json, current_value double,
        uninvested_cash double, book_value varchar, opening_value double, growth double,
        growth_percent double, audit_details json, client_id varchar, wrapper_detail json,
        -- The owners. `clients` is the nested list of {id: ...} (so a JSON string, per the
        -- convention above) and `lead_client_id` the scalar the model prefers when present --
        -- an account can be jointly held, and the lead is who it is reported to.
        clients json, lead_client_id varchar, portfolio_groups varchar
    """,
    # The terms columns are the extractor's declared SPINE, not inferred from the batch: every one
    # is genuinely optional in the payload (an account with no standing order has no
    # `recurringPayments`, a firm pricing by hand has no `adviserCharge`), so without the spine
    # `derived.account_terms` would fail to bind on exactly the firm with least to say. Nested
    # values are JSON strings.
    "raw.account_details": """
        id varchar, client_id varchar, client_name varchar, clients json,
        wrapper_detail json, audit_details json, portfolio_groups varchar,
        adviser_charge double, adviser_tiered_charges varchar, recurring_payments varchar,
        auto_disinvest varchar, fee_structures varchar
    """,
    "raw.account_summaries": """
        id varchar, currency varchar, current_value double, uninvested_cash double,
        book_value varchar, opening_value double, growth double, audit_details json
    """,
    "raw.transactions": """
        id varchar, node_id varchar, firm_id varchar, account_id varchar, account_name varchar,
        account_type varchar, status varchar, transaction_type varchar,
        transaction_sub_type varchar, movement_type varchar, method varchar, currency varchar,
        transaction_date varchar, created_date varchar, completed_date varchar,
        intended_settlement_date varchar, asset_id varchar, asset_name varchar, isin varchar,
        quantity double, executed_quantity varchar, amount double, executed_amount varchar,
        consideration double, book_value varchar, transfer_value double, charges json,
        information json, execution_details json, audit_details json, link_id varchar,
        core_id varchar, event_id varchar, destination_account varchar,
        is_pension_transfer boolean, transfer_status varchar, previous_provider varchar,
        previous_provider_name varchar, status_changes json, external_ids json,
        subscription_amount varchar
    """,
    "staging.clients": """
        client_id varchar, id varchar, node_id varchar, client_type varchar,
        first_name varchar, surname varchar, title varchar, date_of_birth varchar,
        nationality varchar, nationalities json, tax_domicile varchar,
        national_insurance_no varchar, email varchar, mobile varchar, address_detail json,
        status varchar, record_creation_date varchar, audit_details json
    """,
    "staging.positions": """
        id varchar, account_id varchar, node_id varchar, position_type varchar,
        asset_id varchar, asset_name varchar, isin varchar, currency varchar, quantity double,
        book_value varchar, transfer_book_value double, current_price double,
        current_price_date varchar, current_value double, opening_value double, growth double,
        growth_percent double, adjusted_growth double, adjusted_growth_percent double,
        allocation double
    """,
    "staging.transactions": """
        id varchar, node_id varchar, firm_id varchar, account_id varchar, account_name varchar,
        account_type varchar, status varchar, transaction_type varchar,
        transaction_sub_type varchar, movement_type varchar, method varchar, currency varchar,
        transaction_date varchar, created_date varchar, completed_date varchar,
        intended_settlement_date varchar, asset_id varchar, asset_name varchar, isin varchar,
        quantity double, executed_quantity varchar, amount double, executed_amount varchar,
        consideration double, book_value varchar, transfer_value double, charges json,
        information json, execution_details json, audit_details json, link_id varchar,
        core_id varchar, event_id varchar, destination_account varchar,
        is_pension_transfer boolean, transfer_status varchar, previous_provider varchar,
        previous_provider_name varchar, status_changes json, external_ids json
    """,
    # node_id is the scalar-or-list quirk: Seccl sends `["9"]` on some resources and `41` on
    # others, so it widens to VARCHAR and every model has to handle both.
    #
    # `commentary` is VARCHAR, not JSON: Seccl sends the firm's note as plain free text
    # (`This is a sample commentary about the ETC` on the dev book). It was declared `json` here on
    # the assumption it matched the other nested fields, and a reader that trusted that raised
    # "Malformed JSON at byte 0" against the real warehouse while every test passed.
    "raw.models": """
        id varchar, node_id varchar, name varchar, aim varchar, model_type varchar,
        investment_decision json, product_type varchar, risk_rating varchar,
        model_charge varchar, vatable varchar, suspend_rebalances varchar,
        cash_allocation varchar, status varchar, allocations json, asset_mix json,
        commentary varchar, available_to_node_id varchar, available_to_node_name varchar,
        audit_details json
    """,
    # Both transfer directions live here. `transfer_providers` is the inbound rails list and
    # `transfer_out_provider` the outbound {cashSupported, inSpecieSupported} object -- 661 and
    # 7 of 1,612 parties on staging respectively, which is why the model must survive either
    # being NULL.
    "raw.master_parties": """
        id varchar, name varchar, status varchar, external_ids json, audit_details json,
        wrapper_types_provided json, transfer_providers json, transfer_out_provider json
    """,
    "raw.third_party_products": """
        id varchar, available_to_node_id varchar, product_name varchar, scheme_name varchar,
        wrapper_type varchar, contact_name varchar, contact_number varchar,
        tax_wrapper_administrator varchar, pension_scheme_tax_reference varchar,
        scheme_fca_reference_number varchar, relief_at_source_reference_number varchar,
        origo_identifiers json, external_system varchar, audit_details json
    """,
    # Every column here is the extractor's SPINE, not columns inferred from data -- verified
    # live (04 Aug 2026) that staging's register is empty (meta.count 0), so without the spine
    # `type`/`tiers` would not exist and this model would fail to bind on precisely the firm
    # that has no fee structures. `firm_id` is stamped by the extractor; the payload has no
    # firm of its own.
    "raw.fee_structures": """
        id varchar, firm_id varchar, type varchar, name varchar,
        tiers varchar, excluded_wrapper_types varchar
    """,
    # `products` is the extractor spine: ISA/JISA remaining subscription lives in that array,
    # and a firm that omits it must still bind platform.accounts.
    "raw.portfolio_summaries": """
        id varchar, products varchar
    """,
    # `/static/transactiontype` -- the reference list `platform/transactions.sql` reads its
    # statement bucketing out of. `account_report_group` is VARCHAR and not a list:
    # `records_to_table` serialises a nested value to JSON and widens a column whose scalar types
    # disagree, so this one column holds `Payments In` on 75 codes and a JSON array of the two
    # internal-transfer directions on two. Typing it as a real list here would let a model bind in
    # the test and fail on the wire.
    "raw.transaction_codes": """
        id varchar, name varchar, transaction_type varchar, transaction_sub_type varchar,
        movement_type varchar, account_report_group varchar, products varchar,
        cgt boolean, subscription boolean
    """,
    # A Python model: a running average is a recurrence and not an aggregation
    # (`models/derived/book_costs.py`). It is a dependency of the conformed transactions model, so
    # it needs a stub here; its own arithmetic is tested in `test_book_costs.py`.
    "derived.book_costs": """
        transaction_id varchar, account_id varchar, isin varchar, book_cost double,
        total_book_cost double, total_quantity double, book_price double, realised_gain double,
        matched_quantity double, current boolean
    """,
}

# Models are built in dependency order: each one's output becomes a real table the next can
# bind against, which is also how interlace runs them.
BUILD_ORDER = (
    "nodes",
    "instruments",
    "clients",
    "models",
    "accounts",
    "valuations",
    "positions",
    "transactions",
    # The two provider registers. transfer_providers is Seccl-wide reference data and joins
    # nothing; third_party_providers is firm data and joins platform.nodes.
    "transfer_providers",
    "third_party_providers",
    # Joins platform.nodes for the firm ROOT, so it builds after it.
    "fee_packages",
)


def _strip_front_matter(sql: str) -> str:
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


def _model_sql(name: str) -> str:
    """A conformed model's body, with the interlace front-matter comment stripped."""
    return _strip_front_matter((MODELS / f"{name}.sql").read_text())


def _derived_sql(name: str) -> str:
    """A `derived` SQL model's body. Built, not stubbed: the conformed layer joins it, so a change
    that breaks its shape has to fail here rather than at apply time."""
    return _strip_front_matter((MODELS.parent / "derived" / f"{name}.sql").read_text())


#: The `derived` schema's SQL models, built before the conformed layer that joins them.
#: (`book_costs` is a PYTHON model and is stubbed in `RAW_TABLES` instead.)
DERIVED_BUILD_ORDER = ("account_terms",)


@pytest.fixture(scope="module")
def warehouse() -> object:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "staging", "derived", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    for table, columns in RAW_TABLES.items():
        connection.execute(f"CREATE TABLE {table} ({columns})")
    for name in DERIVED_BUILD_ORDER:
        connection.execute(f"CREATE TABLE derived.{name} AS {_derived_sql(name)}")
    for name in BUILD_ORDER:
        connection.execute(f"CREATE TABLE platform.{name} AS {_model_sql(name)}")
    return connection


def _columns(connection: object, relation: str) -> dict[str, str]:
    rows = connection.execute(f"DESCRIBE {relation}").fetchall()  # type: ignore[attr-defined]
    return {row[0]: row[1] for row in rows}


def test_every_conformed_model_is_covered() -> None:
    """A new model without a contract entry would never be checked."""
    on_disk = {path.stem for path in MODELS.glob("*.sql")}
    assert on_disk == set(BUILD_ORDER) == set(CONTRACT)


@pytest.mark.parametrize("name", BUILD_ORDER)
def test_model_publishes_the_conformed_spine(warehouse: object, name: str) -> None:
    columns = set(_columns(warehouse, f"platform.{name}"))
    missing = CONFORMED_SPINE - columns
    # orgs is the org itself, so its key IS its external_id and it has no owner above it.
    assert not missing, f"platform.{name} is missing {sorted(missing)}"


@pytest.mark.parametrize("name", BUILD_ORDER)
def test_model_publishes_its_contract(warehouse: object, name: str) -> None:
    columns = set(_columns(warehouse, f"platform.{name}"))
    missing = CONTRACT[name] - columns
    assert not missing, f"platform.{name} is missing {sorted(missing)}"


@pytest.mark.parametrize("name", BUILD_ORDER)
def test_lineage_is_a_list_of_strings(warehouse: object, name: str) -> None:
    """The fan-out predicate is `list_has_any(lineage, [...])` for every provider, which
    needs one type everywhere — a VARCHAR[] of node/firm/team ids."""
    assert _columns(warehouse, f"platform.{name}")["lineage"] == "VARCHAR[]"


@pytest.mark.parametrize(
    ("name", "column"),
    [
        ("valuations", "market_value"),
        ("valuations", "cash_value"),
        ("valuations", "book_cost"),
        ("transactions", "gross_amount"),
        ("transactions", "net_amount"),
        ("transactions", "fee_amount"),
        ("positions", "market_value"),
        ("positions", "book_cost"),
    ],
)
def test_money_is_decimal_not_double(warehouse: object, name: str, column: str) -> None:
    """Money published as DOUBLE arrives in OLTP numeric(20,4) already rounded wrong. Seccl
    sends doubles (and sometimes strings); the cast belongs here, where the quirk is known."""
    assert _columns(warehouse, f"platform.{name}")[column].startswith("DECIMAL")


def test_ids_are_varchar_across_models(warehouse: object) -> None:
    """Seccl mixes bigint node ids with string account ids; the materialiser joins them to
    text external ids, so every id crosses the boundary as VARCHAR."""
    for name in BUILD_ORDER:
        columns = _columns(warehouse, f"platform.{name}")
        for column, kind in columns.items():
            if column == "external_id" or column.endswith(("_key", "_external_id")):
                assert kind == "VARCHAR", f"platform.{name}.{column} is {kind}"


#: Conformed columns that MUST arrive as a scalar string. Seccl nests far more than its field
#: names suggest -- a phone number is an object, not a number -- and the sink casts whatever it
#: gets to VARCHAR without complaint, so an un-extracted object reaches the book looking like data.
#: That is a worse failure than a Binder Error: it is silent, and it surfaces on a client's record.
CONTACT_SCALARS: tuple[tuple[str, str], ...] = (
    ("clients", "email"),
    ("clients", "mobile"),
    ("clients", "phone"),
    ("clients", "first_name"),
    ("clients", "surname"),
    ("clients", "title"),
    ("clients", "national_identifier"),
    ("nodes", "name"),
    ("instruments", "name"),
    ("accounts", "account_name"),
    ("models", "name"),
    ("transfer_providers", "name"),
    ("third_party_providers", "name"),
    ("fee_packages", "name"),
)


@pytest.mark.parametrize(("name", "column"), CONTACT_SCALARS)
def test_a_conformed_scalar_is_not_a_nested_object(
    warehouse: object, name: str, column: str
) -> None:
    """A JSON/STRUCT where the contract says "a string" is the one shape that fails silently.

    `book_sink.entities` casts every one of these with `CAST(s.x AS VARCHAR)`, and casting JSON to
    VARCHAR always succeeds -- so the object is stored verbatim in a text column and displayed as
    itself. Contrast the `nationality` cast in the same file, which is deliberately shaped to raise
    on a wrong type. These have no such protection, so the check belongs here.
    """
    kind = _columns(warehouse, f"platform.{name}")[column]
    assert kind == "VARCHAR", (
        f"platform.{name}.{column} is {kind}, not VARCHAR -- if the provider nests it, "
        f"extract the field with `->>` rather than publishing the object"
    )


def test_the_client_extractor_guarantees_every_column_the_model_reads() -> None:
    """`_CLIENT_SPINE` is what makes this model's reads safe; the stub only makes them testable.

    `records_to_table` infers columns from the records it is handed, so a field no client in the
    firm carries is not a column and the model aborts on it -- the failure `phone` produced, one
    firm of clients with no mobile away from happening to a field Seccl really does publish. The
    spine is the guarantee, and it is only a guarantee while it covers what the SQL names.

    Read from the AST rather than imported: importing `models.raw_core` registers models into
    interlace's global registry, which `test_project.py` loads for itself.
    """
    tree = ast.parse((MODELS.parent / "raw_core.py").read_text())
    spine = {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_CLIENT_SPINE"
        for key in node.value.keys  # type: ignore[union-attr]
        if isinstance(key, ast.Constant)
    }
    assert spine, "_CLIENT_SPINE is not a dict literal any more -- this scan needs updating"
    body = re.sub(r"--[^\n]*", "", _model_sql("clients"))
    read = set(re.findall(r"\bc\.([a-z_][a-z0-9_]*)", body))
    assert read <= spine, (
        f"platform.clients reads {sorted(read - spine)} off raw.clients, which the extractor does "
        f"not guarantee -- add it to _CLIENT_SPINE, or stop reading it"
    )


def test_the_third_party_extractor_guarantees_every_column_the_model_reads() -> None:
    """Production omits `external_system`; without the spine the conformed model fails to bind."""
    tree = ast.parse((MODELS.parent / "raw_core.py").read_text())
    spine = {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_THIRD_PARTY_SPINE"
        for key in node.value.keys  # type: ignore[union-attr]
        if isinstance(key, ast.Constant)
    }
    assert spine, "_THIRD_PARTY_SPINE is not a dict literal any more -- this scan needs updating"
    body = re.sub(r"--[^\n]*", "", _model_sql("third_party_providers"))
    read = set(re.findall(r"\bsrc\.([a-z_][a-z0-9_]*)", body)) - {"node_key"}
    read |= set(re.findall(r"\bt\.([a-z_][a-z0-9_]*)", body))
    assert read <= spine, (
        f"platform.third_party_providers reads {sorted(read - spine)} off "
        f"raw.third_party_products, which the extractor does not guarantee"
    )


def test_instruments_are_global_reference_data(warehouse: object) -> None:
    """An empty lineage is how a conformed model says "not owned by a tenant" — the
    replacement for fanout.py's hand-maintained _SEI_GLOBAL_TABLES set."""
    sql = _model_sql("instruments")
    assert "[]::varchar[]" in sql.lower()


def test_a_scalar_or_list_node_id_resolves_to_one_key(warehouse: object) -> None:
    """Seccl sends node_id as `["9"]` on clients and `41` on positions. Both must yield the
    bare key, or the join to platform.nodes silently misses and lineage comes back empty."""
    warehouse.execute("DELETE FROM raw.nodes")  # type: ignore[attr-defined]
    warehouse.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.nodes VALUES (9, NULL, 'FIRM', 'Root', 'Firm'), "
        "(41, 9, 'FIRM', 'Branch', 'Adviser')"
    )
    warehouse.execute("CREATE OR REPLACE TABLE platform.nodes AS " + _model_sql("nodes"))  # type: ignore[attr-defined]
    warehouse.execute("DELETE FROM raw.clients")  # type: ignore[attr-defined]
    warehouse.execute(  # type: ignore[attr-defined]
        """INSERT INTO raw.clients (id, node_id, client_type, status)
           VALUES ('c1', '["9"]', 'Individual', 'Active'), ('c2', '41', 'Individual', 'Active')"""
    )
    rows = warehouse.execute(  # type: ignore[attr-defined]
        "SELECT external_id, key, lineage FROM (" + _model_sql("clients") + ") ORDER BY 1"  # noqa: S608 - this is the model's own SQL
    ).fetchall()
    assert rows == [("c1", "9", ["9"]), ("c2", "41", ["9", "41"])]


# --- fee packages -----------------------------------------------------------------------
# Seccl publishes ONE shape for all three kinds of charge -- a `tiers` array -- so which kind a
# structure is has to be read out of it. These are the cases that price an invoice wrongly if the
# reading is wrong, which is why they are executed rather than asserted about the SQL text.

_FIRM_ROOT = "INSERT INTO raw.nodes VALUES (0, NULL, 'SELCT', 'Select Platform Ltd', 'Default')"


def _fee_packages(connection: object, rows: list[tuple[str | None, ...]]) -> list[tuple]:
    """Build platform.fee_packages over `rows` and return it, one tuple per structure."""
    connection.execute("DELETE FROM raw.nodes")  # type: ignore[attr-defined]
    connection.execute(_FIRM_ROOT)  # type: ignore[attr-defined]
    connection.execute("CREATE OR REPLACE TABLE platform.nodes AS " + _model_sql("nodes"))  # type: ignore[attr-defined]
    connection.execute("DELETE FROM raw.fee_structures")  # type: ignore[attr-defined]
    for row in rows:
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO raw.fee_structures VALUES (?, ?, ?, ?, ?, ?)", list(row)
        )
    return connection.execute(  # type: ignore[attr-defined]
        "SELECT external_id, key, lineage, category, fee_type, charge_basis, rule, "  # noqa: S608 - the model's own SQL
        "excluded_products, firm_key FROM (" + _model_sql("fee_packages") + ") ORDER BY external_id"
    ).fetchall()


def test_a_single_flat_amount_is_a_fixed_fee(warehouse: object) -> None:
    rows = _fee_packages(
        warehouse,
        [("f1", "SELCT", "Platform Fee", "Flat fee", '[{"fromAmount":0,"amount":120}]', None)],
    )
    assert rows[0][3:7] == ("platform", "fixed", "flat", '{"amount":120.0000}')


def test_a_single_uncapped_rate_is_a_percentage_fee(warehouse: object) -> None:
    rows = _fee_packages(
        warehouse,
        [("f1", "SELCT", "Adviser Fee", "0.5% ongoing", '[{"fromAmount":0,"rate":0.005}]', None)],
    )
    # Scaled from Seccl's fraction to the 0-100 platform.fee_packages stores, and EXACT: the cast
    # to DECIMAL before scaling is what keeps this off 0.5000000000000001.
    assert rows[0][3:7] == ("adviser", "percentage", "aum", '{"rate":0.50000000}')


def test_seccls_next_penny_bands_are_normalised_contiguous(warehouse: object) -> None:
    """Seccl starts the next band at the next penny (`10000.01`); app/catalogue.py's
    validate_fee_rule rejects a gap outright, because a gap charges nothing inside it. Each band
    therefore starts where the previous one ended -- the same pricing, half-open."""
    tiers = (
        '[{"fromAmount":0,"toAmount":10000,"rate":0.0035},{"fromAmount":10000.01,"rate":0.0005}]'
    )
    rows = _fee_packages(warehouse, [("f1", "SELCT", "Platform Fee", "Tiered", tiers, None)])
    assert rows[0][4] == "tiered"
    bands = json.loads(rows[0][6])["tiers"]
    assert [(band["from"], band["to"]) for band in bands] == [(0, 10000), (10000, None)]
    # Fractions scaled to percent: 0.35% then 0.05%.
    assert [band["rate"] for band in bands] == [0.35, 0.05]


def test_an_unpriceable_structure_publishes_no_fee_type(warehouse: object) -> None:
    """A structure with no tiers, or a multi-tier one where a band carries no rate, cannot be
    expressed as a canonical rule. fee_type NULL makes it a counted reject in the materialiser --
    recoverable, where a fee filed under the wrong shape silently misprices an invoice."""
    mixed = '[{"fromAmount":0,"toAmount":100,"rate":0.01},{"fromAmount":100.01,"amount":50}]'
    rows = _fee_packages(
        warehouse,
        [
            ("f1", "SELCT", "Platform Fee", "Nothing priced", "[]", None),
            ("f2", "SELCT", "Platform Fee", "Mixed bands", mixed, None),
        ],
    )
    assert [row[4] for row in rows] == [None, None]
    assert [row[6] for row in rows] == [None, None]


def test_a_fee_structure_is_firm_wide_not_scoped_to_the_firms_root_node(warehouse: object) -> None:
    """An empty lineage is what admits a row to every tenant, and it is right here: a fee
    structure carries no node, so Seccl applies it across the firm.

    Scoping it to the firm ROOT instead looks tighter and silently withholds it. `lineage`
    containment tests ANCESTRY -- a client at node 41 publishes [0, 9, 41] and matches a tenant
    holding 9 -- so a root-owned row publishing [0] matches only a tenant holding 0, and every
    tenant enabled on a sub-node gets none of its own firm's pricing. Verified on dev, where the
    tenant is enabled on node 1 and the fan-out projects 0 of the firm's 7 root-owned
    `third_party_providers` to it."""
    rows = _fee_packages(
        warehouse,
        [("f1", "SELCT", "Platform Fee", "Flat", '[{"fromAmount":0,"amount":1}]', None)],
    )
    assert rows[0][1] is None
    assert rows[0][2] == []


def test_the_firm_that_priced_a_structure_is_still_published(warehouse: object) -> None:
    """`firm_key` is what makes the one-firm-per-catalogue premise behind that empty lineage
    checkable rather than assumed -- and it is the column a predicate would narrow on if a
    catalogue were ever made to hold two firms."""
    one_tier = '[{"fromAmount":0,"amount":1}]'
    rows = _fee_packages(
        warehouse,
        [
            ("f1", "SELCT", "Platform Fee", "Ours", one_tier, None),
            ("f2", "OTHER", "Platform Fee", "Someone else's", one_tier, None),
        ],
    )
    assert [(row[0], row[8]) for row in rows] == [("f1", "SELCT"), ("f2", "OTHER")]


def test_excluded_wrappers_are_published_as_stated(warehouse: object) -> None:
    """Not inverted here: the complement needs the canonical wrapper set and its PENSION -> SIPP
    alias, which app/materialise.py owns and this layer does not."""
    rows = _fee_packages(
        warehouse,
        [("f1", "SELCT", "Platform Fee", "No ISA", '[{"fromAmount":0,"amount":1}]', '["ISA"]')],
    )
    assert rows[0][7] == ["ISA"]


def test_a_firm_with_no_fee_structures_still_builds(warehouse: object) -> None:
    """The live case: staging's register is empty (verified 04 Aug 2026, meta.count 0). The
    extractor's spine is what makes this bind at all -- with no rows to infer from, `tiers` and
    `type` would not exist as columns."""
    assert _fee_packages(warehouse, []) == []


# --- third-party providers ----------------------------------------------------------------
# Firm data scoped by Seccl's own `available_to_node_id`. The failure worth pinning is the one
# that made the register invisible: a row available at the firm ROOT publishing the root's own
# lineage, which no sub-node tenant's keys contain.


def _third_party(connection: object, node_id: str) -> list[tuple]:
    connection.execute("DELETE FROM raw.nodes")  # type: ignore[attr-defined]
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.nodes VALUES (0, NULL, 'SELCT', 'Select Platform Ltd', 'Default'), "
        "(1, 0, 'SELCT', 'Select Platform', 'Firm'), (4, 1, 'SELCT', 'Select Advised', 'Adviser')"
    )
    connection.execute("CREATE OR REPLACE TABLE platform.nodes AS " + _model_sql("nodes"))  # type: ignore[attr-defined]
    connection.execute("DELETE FROM raw.third_party_products")  # type: ignore[attr-defined]
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.third_party_products (id, available_to_node_id, product_name, "
        "wrapper_type) VALUES ('0053L', ?, 'Seccl Personal Pension', 'TPPENSION')",
        [node_id],
    )
    return connection.execute(  # type: ignore[attr-defined]
        "SELECT external_id, key, lineage FROM ("  # noqa: S608 - the model's own SQL
        + _model_sql("third_party_providers")
        + ")"
    ).fetchall()


def test_a_product_available_at_the_firm_root_is_firm_wide(warehouse: object) -> None:
    """Publishing the root's own lineage ([0]) withheld the whole register: containment tests
    ANCESTRY, so a tenant enabled on node 1 holds a key that [0] does not contain. Verified on
    dev, where the fan-out projected 0 of the firm's 7 and it read as "no third-party products"."""
    assert _third_party(warehouse, '["0"]') == [("0053L", "0", [])]


def test_a_product_available_to_a_sub_node_still_narrows_to_that_subtree(
    warehouse: object,
) -> None:
    """The ELSE branch, unchanged and correct: a row owned by node 4 publishes [0, 1, 4], which a
    tenant holding 0, 1 or 4 matches and a tenant holding only a sibling does not."""
    assert _third_party(warehouse, '["4"]') == [("0053L", "4", ["0", "1", "4"])]


def test_model_charge_is_scaled_from_seccls_fraction(warehouse: object) -> None:
    """Seccl publishes `modelCharge` as `0.002` meaning 0.2%. `platform.models.model_charge` is
    0-100 (the catalogue's (%) label, `target_return`, CTC's `% management charge`). Unscaled it
    showed 0.0020 under that label and an illustration ran net of a hundredth of the charge.

    DECIMAL before * 100, not float: the fee_packages rule. `0.002::double * 100` is
    0.20000000000000004."""
    warehouse.execute("DELETE FROM raw.models")  # type: ignore[attr-defined]
    warehouse.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO raw.models (
            id, node_id, name, aim, model_type, investment_decision, product_type,
            risk_rating, model_charge, vatable, suspend_rebalances, cash_allocation,
            status, allocations, asset_mix, commentary, available_to_node_id,
            available_to_node_name, audit_details
        ) VALUES (
            'm1', '1', 'Balanced', NULL, NULL, NULL, NULL,
            NULL, '0.002', NULL, NULL, NULL,
            'active', NULL, NULL, NULL, NULL,
            NULL, NULL
        )
        """
    )
    charged = warehouse.execute(  # type: ignore[attr-defined]
        "SELECT model_charge FROM (" + _model_sql("models") + ")"  # noqa: S608 - the model's own SQL
    ).fetchone()[0]
    assert float(charged) == 0.2
    warehouse.execute("UPDATE raw.models SET model_charge = NULL")  # type: ignore[attr-defined]
    empty = warehouse.execute(  # type: ignore[attr-defined]
        "SELECT model_charge FROM (" + _model_sql("models") + ")"  # noqa: S608 - the model's own SQL
    ).fetchone()[0]
    assert empty is None


def _warehouse() -> object:
    """A fresh book, so these rows cannot leak into the module fixture's other cases."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA derived")
    connection.execute("CREATE SCHEMA platform")
    for table, columns in RAW_TABLES.items():
        if table.startswith("raw."):
            connection.execute(f"CREATE TABLE {table} ({columns})")
    connection.execute("CREATE TABLE platform.nodes (key varchar, lineage varchar[])")
    for name in DERIVED_BUILD_ORDER:
        connection.execute(f"CREATE TABLE derived.{name} AS {_derived_sql(name)}")
    return connection


def test_the_isa_allowance_is_used_plus_remaining_and_a_jisa_is_per_child() -> None:
    """Remaining comes from the portfolio summary. Used is this tax year's subscription
    movements. A client-level ISA row is shared; a JISA row that names an account is that
    child's. A movement before 6 April does not count, and a remaining figure with no
    movement is not turned into a limit."""
    from datetime import date
    from decimal import Decimal

    connection = _warehouse()
    today = date.today()
    start_year = today.year if (today.month, today.day) >= (4, 6) else today.year - 1
    tax_year = f"{start_year}/{(start_year + 1) % 100:02d}"
    in_year = date(start_year, 5, 1).isoformat()
    before = date(start_year, 4, 1).isoformat()

    connection.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO raw.accounts (id, account_type, wrapper_detail, lead_client_id, clients)
        VALUES
            ('ISA1', 'Wrapper', '{"wrapperType": "ISA"}', 'C1', '["C1"]'),
            ('ISA2', 'Wrapper', '{"wrapperType": "ISA"}', 'C1', '["C1"]'),
            ('J1', 'Wrapper', '{"wrapperType": "JISA"}', 'C1', '["C1"]'),
            ('J2', 'Wrapper', '{"wrapperType": "JISA"}', 'C1', '["C1"]'),
            ('GIA1', 'Wrapper', '{"wrapperType": "GIA"}', 'C1', '["C1"]'),
            ('ISA3', 'Wrapper', '{"wrapperType": "ISA"}', 'C2', '["C2"]')
        """
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.portfolio_summaries (id, products) VALUES (?, ?), (?, ?)",
        [
            "C1",
            json.dumps(
                [
                    {"wrapperType": "ISA", "remainingSubscriptionAmount": 15000},
                    {"wrapperType": "JISA", "accountId": "J1", "remainingSubscriptionAmount": 4000},
                    {"wrapperType": "JISA", "accountId": "J2", "remainingSubscriptionAmount": 9000},
                ]
            ),
            "C2",
            json.dumps([{"wrapperType": "ISA", "remainingSubscriptionAmount": 8000}]),
        ],
    )
    connection.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO raw.transactions (id, account_id, transaction_date, subscription_amount)
        VALUES
            ('t1', 'ISA1', ?, '3000'),
            ('t2', 'ISA2', ?, '2000'),
            ('old', 'ISA1', ?, '9999'),
            ('j1', 'J1', ?, '1000'),
            ('j2', 'J2', ?, '500')
        """,
        [in_year, in_year, before, in_year, in_year],
    )
    rows = {
        row[0]: row[1:]
        for row in connection.execute(  # type: ignore[attr-defined]
            "SELECT external_id, allowance_used, allowance_limit, allowance_remaining, "  # noqa: S608
            "allowance_tax_year FROM (" + _model_sql("accounts") + ")"
        ).fetchall()
    }
    assert rows["ISA1"] == (
        Decimal("5000.0000"),
        Decimal("20000.0000"),
        Decimal("15000.0000"),
        tax_year,
    )
    assert rows["ISA2"] == rows["ISA1"]
    assert rows["J1"] == (
        Decimal("1000.0000"),
        Decimal("5000.0000"),
        Decimal("4000.0000"),
        tax_year,
    )
    assert rows["J2"] == (
        Decimal("500.0000"),
        Decimal("9500.0000"),
        Decimal("9000.0000"),
        tax_year,
    )
    assert rows["GIA1"] == (None, None, None, None)
    assert rows["ISA3"] == (None, None, Decimal("8000.0000"), tax_year)


def test_a_summary_that_is_not_keyed_by_the_client_matches_the_product() -> None:
    """The portfolio list's id is the client id. A summary that is not — a row whose id is
    some other key — still attaches when `clientProductId` is the account's own product."""
    from decimal import Decimal

    connection = _warehouse()
    connection.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO raw.accounts (id, account_type, wrapper_detail, lead_client_id, clients)
        VALUES
            ('ISA1', 'Wrapper', ?, 'C1', '["C1"]'),
            ('ISA2', 'Wrapper', ?, 'C1', '["C1"]')
        """,
        [
            json.dumps({"wrapperType": "ISA", "clientProductId": "prod-isa"}),
            json.dumps({"wrapperType": "ISA", "clientProductId": "prod-other"}),
        ],
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.portfolio_summaries (id, products) VALUES (?, ?)",
        [
            "NOT-THE-CLIENT",
            json.dumps(
                [
                    {
                        "wrapperType": "ISA",
                        "clientProductId": "prod-isa",
                        "remainingSubscriptionAmount": 15000,
                    }
                ]
            ),
        ],
    )
    rows = {
        row[0]: row[1]
        for row in connection.execute(  # type: ignore[attr-defined]
            "SELECT external_id, allowance_remaining FROM ("  # noqa: S608
            + _model_sql("accounts")
            + ")"
        ).fetchall()
    }
    assert rows["ISA1"] == Decimal("15000.0000")
    assert rows["ISA2"] is None


def test_a_sibling_product_does_not_outrank_the_clients_remaining() -> None:
    """A products row that merely carries some clientProductId joined on the client. It is
    not more specific than the client's own remaining unless that id is this wrapper's."""
    from decimal import Decimal

    connection = _warehouse()
    connection.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO raw.accounts (id, account_type, wrapper_detail, lead_client_id, clients)
        VALUES
            ('ISA1', 'Wrapper', ?, 'C1', '["C1"]'),
            ('ISA2', 'Wrapper', ?, 'C1', '["C1"]'),
            ('ISA3', 'Wrapper', ?, 'C2', '["C2"]')
        """,
        [
            json.dumps({"wrapperType": "ISA", "clientProductId": "prod-isa"}),
            json.dumps({"wrapperType": "ISA", "clientProductId": "prod-other"}),
            json.dumps({"wrapperType": "ISA", "clientProductId": "prod-isa"}),
        ],
    )
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.portfolio_summaries (id, products) VALUES (?, ?), (?, ?), (?, ?)",
        [
            "C1",
            json.dumps(
                [
                    {"wrapperType": "ISA", "remainingSubscriptionAmount": 15000},
                    {
                        "wrapperType": "ISA",
                        "clientProductId": "prod-other",
                        "remainingSubscriptionAmount": 100,
                    },
                ]
            ),
            "C2",
            json.dumps(
                [
                    {
                        "wrapperType": "ISA",
                        "clientProductId": "prod-other",
                        "remainingSubscriptionAmount": 50,
                    }
                ]
            ),
            "SOME-OTHER-SUMMARY",
            json.dumps(
                [
                    {
                        "wrapperType": "ISA",
                        "clientProductId": "prod-sibling",
                        "remainingSubscriptionAmount": 1,
                    }
                ]
            ),
        ],
    )
    rows = {
        row[0]: row[1]
        for row in connection.execute(  # type: ignore[attr-defined]
            "SELECT external_id, allowance_remaining FROM ("  # noqa: S608
            + _model_sql("accounts")
            + ")"
        ).fetchall()
    }
    assert rows["ISA1"] == Decimal("15000.0000")
    assert rows["ISA2"] == Decimal("100.0000")
    assert rows["ISA3"] is None


def test_the_account_detail_spine_guarantees_portfolio_groups() -> None:
    """platform.valuations reads the group off the account detail. A book of GIAs never sends
    the field, so it has to be on that extract's spine or the model fails to bind. The same
    spine also names the account-terms columns `derived.account_terms` reads."""
    tree = ast.parse((MODELS.parent / "raw_fanout.py").read_text())
    keys: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_DETAIL_SPINE" or not isinstance(node.value, ast.Dict):
            continue
        keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert keys is not None
    assert {"id", "portfolio_groups"} <= keys
    assert "d.portfolio_groups" in _model_sql("valuations")


def test_a_pension_group_is_the_crystallisation_split() -> None:
    """The account's current value is wholly one side. The empty side is zero only once the
    group is known. Two disagreeing groups, a GIA, and an account with no group stay unset."""
    from decimal import Decimal

    connection = _warehouse()

    def add(account_id: str, wrapper: str, value: str, groups: list[dict[str, str]] | None) -> None:
        connection.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO raw.accounts (
                id, account_type, wrapper_detail, current_value
            ) VALUES (?, 'Wrapper', ?, ?)
            """,
            [account_id, json.dumps({"wrapperType": wrapper}), value],
        )
        if groups is not None:
            connection.execute(  # type: ignore[attr-defined]
                """
                INSERT INTO raw.account_details (id, portfolio_groups) VALUES (?, ?)
                """,
                [account_id, json.dumps(groups)],
            )

    add("P1", "PENSION", "100000", [{"groupType": "Crystallised"}])
    add("P2", "SIPP", "80000", [{"groupType": "Uncrystallised"}])
    add("P3", "PENSION", "5000", [{"groupType": "Beneficiary"}])
    add("P4", "PENSION", "1000", [{"groupType": "CrystallisedTransfer"}])
    add(
        "P5",
        "PENSION",
        "1000",
        [{"groupType": "Crystallised"}, {"groupType": "Uncrystallised"}],
    )
    add("P6", "PENSION", "1000", None)
    add("G1", "GIA", "1000", [{"groupType": "Crystallised"}])

    rows = {
        row[0]: (row[1], row[2])
        for row in connection.execute(  # type: ignore[attr-defined]
            "SELECT external_id, crystallised_value, uncrystallised_value FROM ("  # noqa: S608
            + _model_sql("valuations")
            + ")"
        ).fetchall()
    }
    zero = Decimal("0.0000")
    assert rows["P1"] == (Decimal("100000.0000"), zero)
    assert rows["P2"] == (zero, Decimal("80000.0000"))
    assert rows["P3"] == (Decimal("5000.0000"), zero)
    assert rows["P4"] == (Decimal("1000.0000"), zero)
    assert rows["P5"] == (None, None)
    assert rows["P6"] == (None, None)
    assert rows["G1"] == (None, None)


# --- the statement bucket (report_group_kind) ------------------------------------------------
#
# THIS SECTION EXISTS BECAUSE THE STUBS ABOVE ARE EMPTY. A model over zero rows proves it binds
# and nothing else, and every failure mode of this resolution is value-dependent: the vendor's
# reference list registers one code for both legs of an internal transfer, registers two codes
# for one triple, and spells "N/A" two ways. Each of those is a row, not a column, so each needs
# a row. (docs/sei.md records learning the same lesson from the other side — an empty fixture
# cannot catch a JSON function that is never asked to parse anything.)
#
# The fixtures are the real reference rows, transcribed from `/static/transactiontype`.

#: (id, name, transaction_type, transaction_sub_type, movement_type, account_report_group,
#:  subscription) — as `records_to_table` lands them, which is why the group is a VARCHAR that is
#: sometimes a JSON array.
_CODES: tuple[tuple[str, str, str, str | None, str | None, str | None, bool], ...] = (
    ("CPIN", "Client Payment", "Payment", "Deposit", "In", "Payments In", True),
    # Same triple as CPIN, different bucket. Nothing on a transaction separates them.
    ("HPDC", "Dividend Tax Reclaim", "Payment", "Deposit", "In", "Income", True),
    ("CPOU", "Withdrawal", "Payment", "Withdrawal", "Out", "Payments Out", True),
    ("PFOF", "Platform Monthly Fee", "Cash", "Platform Fee", "Out", "Charges", False),
    ("PINT", "Interest", "Cash", "Interest", "In", "Income", False),
    # One row, both legs: registered 'Out', group is the ordered pair.
    (
        "ACST",
        "Account cash transfer",
        "Cash Transfer",
        "Internal",
        "Out",
        '["Internal Transfers Out", "Internal Transfers In"]',
        True,
    ),
    # The two spellings of "no bucket", on the two legs of a market order.
    ("BUY", "Buy", "Order", "At Best", "Invest", "N/A", False),
    ("SELL", "Sell", "Order", "At Best", "Raise", "Transactions Realised", False),
    ("BUY2", "Buy", "Order", "At Best", "Invest", "N/a", False),
)


def _statement(connection: object, rows: list[tuple]) -> dict[str, tuple]:
    """Build the transactions model over real rows; return id → (group, subscription, …)."""
    connection.execute("DELETE FROM raw.transaction_codes")  # type: ignore[attr-defined]
    for code in _CODES:
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO raw.transaction_codes (id, name, transaction_type, transaction_sub_type, "
            "movement_type, account_report_group, subscription) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(code),
        )
    connection.execute("DELETE FROM raw.transactions")  # type: ignore[attr-defined]
    for txn in rows:
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO raw.transactions (id, account_id, transaction_type, "
            "transaction_sub_type, movement_type, amount, consideration, book_value, information) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(txn),
        )
    out = connection.execute(  # type: ignore[attr-defined]
        "SELECT external_id, report_group_kind, subscription_amount, realised_gain, "  # noqa: S608
        "       equalisation_payment, trade_date FROM (" + _model_sql("transactions") + ")"
    ).fetchall()
    return {row[0]: row[1:] for row in out}


def _txn(
    tid: str,
    ttype: str,
    subtype: str | None,
    movement: str | None,
    amount: float = 100.0,
    consideration: float | None = None,
    book_value: str | None = None,
    information: str | None = None,
) -> tuple:
    return (tid, "ACC1", ttype, subtype, movement, amount, consideration, book_value, information)


def test_a_movement_takes_the_vendors_own_statement_bucket(warehouse: object) -> None:
    """The whole point: the breakdown comes from the vendor's reference data, not a hand-written
    map. A charge is a charge because `/static/transactiontype` says PFOF is."""
    got = _statement(
        warehouse,
        [
            _txn("t1", "Cash", "Platform Fee", "Out"),
            _txn("t2", "Cash", "Interest", "In"),
            _txn("t3", "Payment", "Withdrawal", "Out"),
        ],
    )
    assert got["t1"][0] == "Charges"
    assert got["t2"][0] == "Income"
    assert got["t3"][0] == "Payments Out"


def test_both_legs_of_an_internal_transfer_resolve_from_one_code_row(warehouse: object) -> None:
    """ACST is registered `movementType = 'Out'` ONLY, with `['Internal Transfers Out', 'Internal
    Transfers In']` as its group — one row for a movement that is Out of one account and In to the
    other. Joining on the full triple therefore matched the outbound leg and missed every inbound
    one (30 of them on the dev corpus), which on a statement reads as money leaving and never
    arriving."""
    got = _statement(
        warehouse,
        [
            _txn("out", "Cash Transfer", "Internal", "Out"),
            _txn("in", "Cash Transfer", "Internal", "In"),
        ],
    )
    assert got["out"][0] == "Internal Transfers Out"
    assert got["in"][0] == "Internal Transfers In"


def test_an_ambiguous_triple_is_refused_rather_than_guessed(warehouse: object) -> None:
    """`('Payment','Deposit','In')` is BOTH CPIN (Client Payment → Payments In) and HPDC (Dividend
    Tax Reclaim → Income), and `/portfoliotransaction` publishes no narrative to tell them apart.
    Those are different lines of a statement — money the client paid in against money the account
    earned — so the answer is NULL and the statement counts it under "other", where it is visible.
    Filing it under the likelier of the two would be a figure nobody could audit.

    It also must not FAN OUT: two matching code rows joined naively would double the row."""
    got = _statement(warehouse, [_txn("t1", "Payment", "Deposit", "In")])
    assert len(got) == 1, "an ambiguous code must not duplicate the transaction"
    assert got["t1"][0] is None


def test_the_two_spellings_of_na_are_one_answer(warehouse: object) -> None:
    """The vendor's list spells the empty bucket `N/A` on 17 codes and `N/a` on 3. Without
    normalising, a market order's BUY leg matches two codes whose groups "disagree" and falls into
    the refusal above — so every purchase in the book would land under "other"."""
    got = _statement(warehouse, [_txn("buy", "Order", "At Best", "Invest")])
    assert got["buy"][0] is None, "N/A means no bucket, not a bucket called N/A"


def test_a_combination_the_reference_list_does_not_cover_is_null(warehouse: object) -> None:
    """A notional accumulation dividend (`Notional / Accumulation Dividend / In`) has no row in the
    reference list at all — 238 of them on the dev corpus. NULL rather than a failure: an unknown
    code has to land and be countable, which is the same rule the merge applies to an unmapped
    `type`."""
    got = _statement(warehouse, [_txn("t1", "Notional", "Accumulation Dividend", "In")])
    assert got["t1"][0] is None


def test_subscription_is_signed_by_direction(warehouse: object) -> None:
    """An ISA payment in CONSUMES allowance and a withdrawal reclaims it (on a flexible ISA), and
    the vendor flags both `subscription`. Unsigned, the remaining allowance would count a
    withdrawal as further use of it. A non-subscribing code contributes nothing at all rather than
    zero, so "did not touch the allowance" and "touched it for £0" stay distinguishable.

    Remaining allowance is still `platform.accounts.allowance_*`; this is the row's own amount."""
    got = _statement(
        warehouse,
        [
            _txn("out", "Payment", "Withdrawal", "Out", amount=250.0),
            _txn("fee", "Cash", "Platform Fee", "Out", amount=12.0),
        ],
    )
    assert float(got["out"][1]) == -250.0
    assert got["fee"][1] is None


def test_the_cost_basis_is_JOINED_not_recomputed_here(warehouse: object) -> None:
    """`book_cost` and `realised_gain` come from `derived.book_costs`, which computes the POOLED
    running average in Python and applies the 30-day share-matching rule on a GIA.

    This model used to report `abs(proceeds) - abs(bookValue)` off Seccl's per-movement `bookValue`.
    That matched the vendor's own `profitLoss` on the rows where its `bookValue` was already the
    pooled figure, and silently disagreed everywhere else. So what is pinned here is that the
    conformed model no longer has an opinion of its own: with no row in `derived.book_costs` the
    gain is NULL, not a figure this model reconstructed from the transaction.

    The arithmetic itself is tested in `test_book_costs.py`, over sequences, where it can be."""
    got = _statement(
        warehouse,
        [_txn("sell", "Order", "At Best", "Raise", consideration=-29.86, book_value="-28.37")],
    )
    assert got["sell"][2] is None, "the conformed model must not derive a gain of its own"


def test_an_accumulation_distribution_splits_capital_from_income(warehouse: object) -> None:
    """One transaction, two statement lines. An equalisation payment is a return of the capital
    embedded in the price paid, so it belongs in payments-in, while the REST of the same
    distribution is income — which is why the vendor's own report lists the same transactionId in
    both `payments.transactionsIn` and `income.transactions`, each with its own share. Summing
    `amount` in either bucket would count the whole distribution twice."""
    got = _statement(
        warehouse,
        [
            _txn(
                "t1",
                "Notional",
                "Accumulation Dividend",
                "In",
                amount=0.91,
                information='{"equalisationAmountPayCy": 0.52, "eventExDate": "2024-11-01"}',
            )
        ],
    )
    equalisation, ex_date = got["t1"][3], got["t1"][4]
    assert float(equalisation) == pytest.approx(0.52)
    # And the ex-date is lifted, which is the date that decides entitlement at all.
    assert str(ex_date) == "2024-11-01"


# --- the account's own terms (derived.account_terms) ------------------------------------------
#
# Rows, not stubs, for three reasons that are all value-dependent: `recurringPayments` is ONE array
# carrying both directions, the two tier shapes use DIFFERENT key names for the same bounds, and a
# charge an account does not carry must contribute no row rather than a row of nothing.


def _terms(
    connection: object, detail: dict[str, object], model: dict[str, str] | None = None
) -> dict:
    """Build `derived.account_terms` over one account's detail record."""
    connection.execute("DELETE FROM raw.account_details")  # type: ignore[attr-defined]
    connection.execute("DELETE FROM raw.accounts")  # type: ignore[attr-defined]
    connection.execute("DELETE FROM raw.models")  # type: ignore[attr-defined]
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO raw.accounts (id, node_id, wrapper_detail, name, status, currency) "
        "VALUES ('ACC1', '1', ?, 'Test', 'Active', 'GBP')",
        [json.dumps({"wrapperType": "GIA", "assetAllocationId": (model or {}).get("id")})],
    )
    if model:
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO raw.models (id, node_id, name, model_charge) VALUES (?, '1', ?, ?)",
            [model["id"], model["name"], model["charge"]],
        )
    columns = ["id", *detail]
    values = ["ACC1", *detail.values()]
    marks = ", ".join("?" for _ in columns)
    connection.execute(  # type: ignore[attr-defined]
        f"INSERT INTO raw.account_details ({', '.join(columns)}) VALUES ({marks})",  # noqa: S608
        values,
    )
    rows = connection.execute(  # type: ignore[attr-defined]
        "SELECT * FROM (" + _derived_sql("account_terms") + ")"  # noqa: S608 - the model's own SQL
    ).df()
    return {} if rows.empty else rows.iloc[0].to_dict()


def test_one_recurring_array_splits_into_payments_and_withdrawals(warehouse: object) -> None:
    """Seccl publishes ONE `recurringPayments` array with `movementType` In or Out — money arriving
    by standing order and money leaving by regular withdrawal in the same list. The book has had
    separate columns since 0002 and nothing ever wrote to either, so the split happens here."""
    row = _terms(
        warehouse,
        {
            "recurring_payments": json.dumps(
                [
                    {
                        "paymentDay": 14,
                        "amount": 100,
                        "movementType": "Out",
                        "method": "BACS Credit",
                    },
                    {
                        "paymentDay": 1,
                        "amount": 250,
                        "movementType": "In",
                        "method": "Direct Debit",
                    },
                ]
            ),
            "auto_disinvest": "Largest",
        },
    )
    assert [p["amount"] for p in row["payments"]] == [250]
    assert [w["amount"] for w in row["withdrawals"]] == [100]
    assert row["withdrawals"][0]["payment_day"] == 14
    # A `paymentDay` IS the frequency, and it is monthly. Stated so the surface can render "14th of
    # every month" without inventing the second half of the sentence itself.
    assert row["withdrawals"][0]["frequency"] == "Monthly"


def test_auto_disinvest_travels_only_with_money_going_out(warehouse: object) -> None:
    """It is the account's rule for RAISING the cash a withdrawal needs ('Largest' sells down the
    biggest holding), so it means nothing on money coming in. Repeating it on an inbound row would
    state a rule that does not apply to it — the surface shows "N/A" there for a reason."""
    row = _terms(
        warehouse,
        {
            "recurring_payments": json.dumps(
                [
                    {"paymentDay": 14, "amount": 100, "movementType": "Out", "method": "BACS"},
                    {"paymentDay": 1, "amount": 250, "movementType": "In", "method": "DD"},
                ]
            ),
            "auto_disinvest": "Largest",
        },
    )
    assert row["withdrawals"][0]["auto_disinvest"] == "Largest"
    assert row["payments"][0]["auto_disinvest"] is None


def test_a_flat_adviser_charge_becomes_one_fee_row(warehouse: object) -> None:
    """`adviserCharge` is a FRACTION — 0.01 is 1% — and the book's `fees` column is a list of
    `{name, type, frequency, rate, ...}`, which the account record already renders. Shaping it here
    rather than in a portal means the admin book's read-only mount gets the same rows."""
    row = _terms(warehouse, {"adviser_charge": 0.01})
    fees = list(row["fees"])
    assert len(fees) == 1
    assert fees[0]["type"] == "adviser"
    assert float(fees[0]["rate"]) == pytest.approx(0.01)
    assert fees[0]["basis"] == "percentage"


def test_a_tiered_charge_reads_the_keys_the_ADVISER_shape_uses(warehouse: object) -> None:
    """THE TRAP IN THIS MODEL. The same concept has two spellings in one vendor's API: a platform
    tier is `{from, to}` and an adviser tier is `{fromAmount, toAmount}`. Reading the wrong pair
    yields NULL bounds — which price the whole balance at the first tier's rate and look entirely
    plausible."""
    row = _terms(
        warehouse,
        {
            "adviser_tiered_charges": json.dumps(
                {
                    "tiers": [
                        {"rate": 0.01, "fromAmount": 0, "toAmount": 250000},
                        {"rate": 0.005, "fromAmount": 250000, "toAmount": None},
                    ]
                }
            )
        },
    )
    tiers = list(row["adviser_tiered_charges"])
    assert [float(t["rate"]) for t in tiers] == [pytest.approx(0.01), pytest.approx(0.005)]
    assert float(tiers[0]["amount_to"]) == 250000, "the bounds must not be NULL"
    assert float(tiers[1]["amount_from"]) == 250000
    # The summary row on the card says it is tiered rather than quoting the first rate as the
    # charge.
    fees = list(row["fees"])
    assert fees[0]["basis"] == "tiered"


def test_a_flat_charge_wins_over_the_tiers(warehouse: object) -> None:
    """Never both: a firm prices an account flat OR on a scale, and v1's fee model reads the flat
    one first. Emitting two adviser rows would double the charge on the card and in any estimate."""
    row = _terms(
        warehouse,
        {
            "adviser_charge": 0.0075,
            "adviser_tiered_charges": json.dumps({"tiers": [{"rate": 0.01, "fromAmount": 0}]}),
        },
    )
    fees = [f for f in row["fees"] if f["type"] == "adviser"]
    assert len(fees) == 1
    assert float(fees[0]["rate"]) == pytest.approx(0.0075)
    assert fees[0]["basis"] == "percentage"


def test_the_dfm_charge_comes_from_the_model_the_account_is_managed_to(warehouse: object) -> None:
    """It is a property of the MODEL, not of the account, so it resolves through
    `wrapperDetail.assetAllocationId`. Named after the model, because a client's statement should
    say which mandate it is paying for."""
    row = _terms(
        warehouse,
        {"adviser_charge": 0.01},
        model={"id": "251GF", "name": "Marlborough MPS Passive 8", "charge": "0.0025"},
    )
    dfm = [f for f in row["fees"] if f["type"] == "dfm"]
    assert len(dfm) == 1
    assert float(dfm[0]["rate"]) == pytest.approx(0.0025)
    assert "Marlborough MPS Passive 8" in dfm[0]["name"]


def test_an_advisory_account_has_no_management_fee_row_at_all(warehouse: object) -> None:
    """NO ROW, not a row with a nil rate. An account with no model is not charged for management,
    and "no management fee" reads very differently from "a management fee of nothing" on a client's
    account."""
    row = _terms(warehouse, {"adviser_charge": 0.01})
    assert [f["type"] for f in row["fees"]] == ["adviser"]


def test_an_account_with_no_terms_at_all_still_produces_a_row(warehouse: object) -> None:
    """The commonest case on a small firm: no standing order, no charge against the account. Empty
    lists rather than no row, so the conformed LEFT JOIN finds the account and the surface says
    "none recorded" — which is what it means."""
    row = _terms(warehouse, {})
    assert list(row["fees"]) == []
    assert list(row["payments"]) == []
    assert list(row["withdrawals"]) == []


def test_the_fee_package_key_reaches_the_conformed_account(warehouse: object) -> None:
    """`feeStructures.adviserFeeId` names a row in the firm's priced register
    (`platform.fee_packages`), and `platform/accounts.sql` published a hardcoded NULL for it — so an
    account could never be joined to the pricing the firm actually edits."""
    row = _terms(
        warehouse, {"fee_structures": json.dumps({"adviserFeeId": "64f1a2b3c4d5e6f7a8b9c0d1"})}
    )
    assert row["adviser_fee_package_key"] == "64f1a2b3c4d5e6f7a8b9c0d1"
