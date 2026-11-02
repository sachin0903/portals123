"""The staging projection binds, and publishes what the merge then needs.

Two failures this exists to catch, both of which read as "this firm has no data":

* a column the conformed layer does not publish -- a DuckDB Binder Error, which aborts the tenant;
* a column the MERGE needs that staging drops. A missing `account_external_id` does not error: the
  parent resolves to NULL for every row and the whole slice becomes a reject.

The stubs are empty, and deliberately so: a projection that binds against zero rows can still be
wrong about the data, but it cannot be wrong about a column name or a type. Whether it binds against
SECCL's real conformed output is a separate test in that service, over the real models.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from book_sink import entities

duckdb = pytest.importorskip("duckdb")

# The conformed columns each entity's projection reads, with the types the models publish. Money is
# DECIMAL because the conformed layer casts it so (a DOUBLE would lose pennies at scale), lineage
# the nationality and owner lists are LISTs, and `data` is already a JSON string.
_STUBS: dict[str, str] = {
    "nodes": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], type_raw VARCHAR,"
        " type VARCHAR, name VARCHAR, parent_key VARCHAR, as_of_date DATE,"
        " source_updated_at TIMESTAMP, data VARCHAR"
    ),
    "instruments": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], isin VARCHAR, sedol VARCHAR,"
        " cusip VARCHAR, name VARCHAR, legal_name VARCHAR, asset_type VARCHAR, currency VARCHAR,"
        " exchange_code VARCHAR, market_country VARCHAR, issuer_country VARCHAR,"
        " credit_rating VARCHAR, interest_rate DECIMAL(12,6), issued_on DATE, matures_on DATE,"
        " ocf DECIMAL(18,10),"
        " as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    "clients": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], client_type VARCHAR,"
        " client_type_raw VARCHAR, sub_type VARCHAR, first_name VARCHAR, middle_name VARCHAR,"
        " surname VARCHAR,"
        " entity_name VARCHAR, title VARCHAR, date_of_birth DATE, nationality VARCHAR[],"
        " national_identifier VARCHAR, national_identifier_type VARCHAR,"
        " gender VARCHAR, marital_status VARCHAR, is_vulnerable BOOLEAN,"
        " country_of_birth VARCHAR, fatca_status VARCHAR, us_person BOOLEAN,"
        " passport_number VARCHAR, passport_country VARCHAR, email VARCHAR,"
        " mobile VARCHAR, phone VARCHAR,"
        " address STRUCT(lines VARCHAR[], city VARCHAR, region VARCHAR, postcode VARCHAR,"
        "                country VARCHAR),"
        " base_currency VARCHAR,"
        " status VARCHAR, as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    "accounts": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], account_name VARCHAR,"
        " product_key VARCHAR, product_external_id VARCHAR, status VARCHAR,"
        " base_currency VARCHAR, is_discretionary BOOLEAN,"
        " model_key VARCHAR, owner_external_ids VARCHAR[], lead_client_external_id VARCHAR,"
        " fee_package_key VARCHAR, allowance_used DECIMAL(20,4), allowance_limit DECIMAL(20,4),"
        " allowance_remaining DECIMAL(20,4), allowance_tax_year VARCHAR, opened_on DATE,"
        " fees STRUCT(name VARCHAR, type VARCHAR, frequency VARCHAR, rate DECIMAL(12,8),"
        "             amount DECIMAL(20,2), basis VARCHAR)[],"
        " payments STRUCT(amount DECIMAL(20,2), payment_day INTEGER, method VARCHAR,"
        "                 movement_type VARCHAR, auto_disinvest VARCHAR, frequency VARCHAR)[],"
        " withdrawals STRUCT(amount DECIMAL(20,2), payment_day INTEGER, method VARCHAR,"
        "                    movement_type VARCHAR, auto_disinvest VARCHAR, frequency VARCHAR)[],"
        " bank_accounts JSON,"
        " as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    "portfolios": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], account_external_id VARCHAR,"
        " name VARCHAR, portfolio_type VARCHAR, portfolio_type_raw VARCHAR, purpose VARCHAR,"
        " is_principal BOOLEAN, linked_external_id VARCHAR, opened_on DATE, closed_on DATE,"
        " as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    # The firm's own staff at a custodian. `email` is NULLABLE on purpose and that is the whole
    # point of the column: the drop's `UserDetail` carries no address at all, so a deployment with
    # no API credentials publishes the population with every email NULL — and
    # `auth.merge_provider_user` must count those rather than invent one.
    "users": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], firm_id VARCHAR,"
        " email VARCHAR, first_name VARCHAR, middle_name VARCHAR, last_name VARCHAR,"
        " display_name VARCHAR, phone VARCHAR, login_name VARCHAR, crd_number VARCHAR,"
        " is_active BOOLEAN, created_on DATE, money_movement_limit VARCHAR,"
        " has_ecx_access BOOLEAN, as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    "valuations": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], account_external_id VARCHAR,"
        " as_of_date DATE, market_value DECIMAL(20,4), cash_value DECIMAL(20,4),"
        " book_cost DECIMAL(20,4), unrealised_gain DECIMAL(20,4), opening_value DECIMAL(20,4),"
        # The crystallisation split (0117). In the STUB because every provider must publish the
        # column — only Morgan Lloyd has a value for it, and the others publish a typed NULL.
        # This fixture is the contract: it is what caught the columns being read before Seccl and
        # SEI published them.
        " crystallised_value DECIMAL(20,4), uncrystallised_value DECIMAL(20,4),"
        " currency VARCHAR, data VARCHAR"
    ),
    "models": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], name VARCHAR, aim VARCHAR,"
        " model_type VARCHAR, decision_maker JSON, product_key VARCHAR, risk_rating VARCHAR,"
        " model_charge DECIMAL(8,4), vatable BOOLEAN, suspend_rebalances BOOLEAN,"
        " cash_allocation DECIMAL(9,6), status VARCHAR, status_raw VARCHAR, allocations JSON,"
        " asset_mix JSON, commentary JSON, available_to_key VARCHAR,"
        " as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
    # These three are typed from what Seccl's conformed models really publish (read out of the
    # models, not guessed): `rule` and `data` are JSON, `excluded_products` is already a LIST, and
    # `rails` is a STRUCT[] — which is why it needs to_json rather than a cast.
    "fee_packages": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], firm_key VARCHAR, name VARCHAR,"
        " category VARCHAR, fee_category_raw VARCHAR, fee_type VARCHAR, frequency VARCHAR,"
        " rule JSON, charge_basis VARCHAR, payer VARCHAR, currency VARCHAR,"
        " excluded_products VARCHAR[], tier_count BIGINT, source_updated_at TIMESTAMP,"
        " as_of_date DATE, data JSON"
    ),
    "transfer_providers": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], name VARCHAR, status VARCHAR,"
        " is_active BOOLEAN,"
        " rails STRUCT(rail VARCHAR, reference VARCHAR, products VARCHAR[])[],"
        " products VARCHAR[], transfers_in BOOLEAN, transfers_out BOOLEAN,"
        " origo_id VARCHAR, atg_reference VARCHAR, wrapper_types_provided JSON,"
        " source_updated_at TIMESTAMP, as_of_date DATE, data JSON"
    ),
    "third_party_providers": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], name VARCHAR,"
        " scheme_name VARCHAR, product_key VARCHAR, contact_name VARCHAR, origo_identifiers JSON,"
        " external_system VARCHAR, source_updated_at TIMESTAMP, as_of_date DATE, data JSON"
    ),
    "transactions": (
        "external_id VARCHAR, key VARCHAR, lineage VARCHAR[], account_external_id VARCHAR,"
        " instrument_key VARCHAR, type VARCHAR, source_type VARCHAR, source_subtype VARCHAR,"
        " isin VARCHAR, asset_name VARCHAR, gross_amount DECIMAL(20,4), net_amount DECIMAL(20,4),"
        " book_value DECIMAL(20,4),"
        " quantity DECIMAL(28,8), unit_price DECIMAL(20,8), fee_amount DECIMAL(20,4),"
        " tax_amount DECIMAL(20,4),"
        " currency VARCHAR,"
        " transaction_date DATE, settlement_date DATE, posted_at TIMESTAMP, status VARCHAR,"
        # §3's `order_key` — what ties an order's legs together at the custodian. Seccl publishes a
        # link/core id, SEI an explicit NULL (its drop groups nothing); it stages into the book's
        # `report_group`, which 0002 created and nothing had ever written.
        " order_key VARCHAR,"
        # The statement layer. Every provider publishes the column, not necessarily a value:
        # `report_group_kind` is the custodian's own statement bucket (Seccl reads it out of
        # `/static/transactiontype`; SEI and Morgan Lloyd have no equivalent and publish NULL), and
        # the rest are what a ledger row is read to answer — what it cost, what it realised, when
        # the entitlement was struck, what it did to the allowance. Named in the SHARED
        # projection on purpose: a provider that stops publishing one fails here rather than
        # silently landing NULLs that read as "this firm has no charges".
        " report_group_kind VARCHAR, dealing_charges DECIMAL(20,4), other_charges DECIMAL(20,4),"
        " fee_type VARCHAR, book_cost DECIMAL(20,4), realised_gain DECIMAL(20,4), trade_date DATE,"
        " equalisation_payment DECIMAL(20,4), subscription_amount DECIMAL(20,4),"
        " as_of_date DATE, source_updated_at TIMESTAMP, data VARCHAR"
    ),
}


@pytest.fixture(scope="module")
def lake() -> Any:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    for relation, columns in _STUBS.items():
        connection.execute(f"CREATE TABLE slice.{relation} ({columns})")
    return connection


def _relations() -> dict[str, str]:
    return {name: f"slice.{name}" for name in _STUBS}


def _staged(connection: Any, entity: entities.Entity) -> dict[str, str]:
    sql = entity.staging_sql(tenant_id=7, provider="seccl", relations=_relations())
    connection.execute(f"CREATE OR REPLACE TEMP VIEW staged AS {sql}")
    return {row[0]: row[1] for row in connection.execute("DESCRIBE staged").fetchall()}


def test_every_relation_an_entity_reads_has_a_stub() -> None:
    """A new entity reading a relation with no stub would never be checked. Keyed on the SOURCE
    relations, because two entities can share one — the shelf and each platform's reference for it
    both come from `accounts`."""
    read = {relation for entity in entities.CONFORMED for relation in entity.relations()}
    assert read == set(_STUBS)


@pytest.mark.parametrize("entity", entities.CONFORMED, ids=lambda e: e.slug)
def test_the_projection_binds(lake: Any, entity: entities.Entity) -> None:
    assert _staged(lake, entity), f"{entity.slug} staged nothing"


@pytest.mark.parametrize("entity", entities.CONFORMED, ids=lambda e: e.slug)
def test_the_projection_publishes_what_the_merge_resolves(
    lake: Any, entity: entities.Entity
) -> None:
    """A parent whose source column is not staged does not error -- it resolves to NULL for every
    row, and the whole slice becomes a reject. That is the silent version of this test failing."""
    columns = set(_staged(lake, entity))
    for parent in entity.parents:
        if not parent.source:
            continue  # `platform` needs no key from the provider: the provider IS the key
        assert parent.source in columns, f"{entity.slug} does not stage {parent.source}"
    assert entity.row_key in columns, f"{entity.slug} cannot name its own rows in the ledger"


@pytest.mark.parametrize(
    "entity", [e for e in entities.CONFORMED if e.link_type], ids=lambda e: e.slug
)
def test_a_linked_entity_stages_its_external_id(lake: Any, entity: entities.Entity) -> None:
    """Identity IS the link, so a linked entity with no external_id has none -- and every run would
    insert the whole slice again."""
    assert "external_id" in _staged(lake, entity)


@pytest.mark.parametrize(
    "entity", [e for e in entities.CONFORMED if not e.link_type], ids=lambda e: e.slug
)
def test_a_keyed_entity_stages_the_columns_of_its_key(lake: Any, entity: entities.Entity) -> None:
    """Except the ones the merge resolves for it: `account_id` is a book id, so it arrives from the
    parent join rather than from the provider."""
    resolved = {parent.column for parent in entity.parents}
    columns = set(_staged(lake, entity))
    for column in entity.key:
        assert column in columns or column in resolved, f"{entity.slug} stages no {column}"


def test_the_payload_is_not_double_encoded(lake: Any) -> None:
    """The conformed layer already publishes `to_json(t) AS data`. Wrapping it again would land a
    JSON string inside a JSON string, and every reader of links.data would have to know which layer
    did it. Only for the entities that carry a payload at all: a shelf item and a platform reference
    are Graphene's own rows, assembled from a wrapper string, so there is no provider record of them
    to keep.
    """
    carries_payload = [e for e in entities.CONFORMED if "data" in _staged(lake, e)]
    assert {e.slug for e in carries_payload} == {
        "nodes",
        "instruments",
        "clients",
        "accounts",
        "models",
        "fee_packages",
        "fee_package_platforms",
        "transfer_providers",
        "third_party_providers",
        "portfolios",
        "valuations",
        "transactions",
    }
    for entity in carries_payload:
        assert _staged(lake, entity)["data"] == "VARCHAR"


def test_a_clients_nationality_stays_a_list(lake: Any) -> None:
    """`crm.contacts.nationality` is `text[]` — a person can hold two — so the projection must not
    reduce it. Staging it as a scalar made the merge cast 'GB' to text[], which Postgres refuses as
    a malformed array literal; the parity test is what found that, because every other fixture left
    the field NULL."""
    clients = next(entity for entity in entities.CONFORMED if entity.slug == "clients")
    assert _staged(lake, clients)["nationality"] == "VARCHAR[]"


def test_clients_refresh_the_custodian_status(lake: Any) -> None:
    """Contacts are insert-only for the firm's own words; the custodian's Suspended is the
    one column the nightly run must be allowed to refresh, or a client Active on first
    sighting stays Active for ever. Base currency is the same kind of fact: a USD client
    first inserted before the column existed would otherwise stay labelled GBP."""
    clients = next(entity for entity in entities.CONFORMED if entity.slug == "clients")
    assert "provider_status" in clients.updatable
    assert "currency" in clients.updatable
    assert "type" in clients.updatable
    assert "sub_type" in clients.updatable
    assert "national_identifier" in clients.fill_if_null
    assert "email" in clients.fill_if_null
    assert "gender" in clients.fill_if_null
    assert "fatca_status" in clients.fill_if_null
    assert "is_vulnerable" in clients.fill_if_null
    assert "gender" not in clients.updatable
    assert "email" not in clients.updatable
    assert "AS provider_status" in entities._CLIENTS
    assert "AS currency" in entities._CLIENTS
    assert "provider_status" in _staged(lake, clients)
    assert "currency" in _staged(lake, clients)
    assert "type" in _staged(lake, clients)
    assert "sub_type" in _staged(lake, clients)


def test_an_individual_stays_a_person_and_a_trust_stays_a_trust() -> None:
    """The book stores individual | organisation, and trust | company | charity only
    on an organisation. An individual who also carries a sub-type must not: the
    check on crm.contacts rejects that row."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    connection.execute(f"CREATE TABLE slice.clients ({_STUBS['clients']})")
    connection.execute(
        """
        INSERT INTO slice.clients (
            external_id, key, client_type, sub_type, first_name, surname, entity_name
        ) VALUES
            ('p', 'k', 'individual', 'trust', 'Ada', 'Lovelace', NULL),
            ('t', 'k', 'entity', 'trust', NULL, 'The Trust', 'The Trust'),
            ('o', 'k', 'entity', 'pension', NULL, 'Scheme', 'Scheme')
        """
    )
    clients = next(entity for entity in entities.CONFORMED if entity.slug == "clients")
    sql = clients.staging_sql(tenant_id=7, provider="sei", relations={"clients": "slice.clients"})
    connection.execute(f"CREATE VIEW staged AS {sql}")
    got = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT external_id, type, sub_type FROM staged ORDER BY external_id"
        ).fetchall()
    }
    assert got == {
        "o": ("organisation", None),
        "p": ("individual", None),
        "t": ("organisation", "trust"),
    }


def test_a_client_fact_outside_the_book_vocabulary_is_dropped() -> None:
    """A gender or a FATCA status the check does not allow must not reach Postgres:
    one bad value aborts the client merge for the tenant."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    connection.execute(f"CREATE TABLE slice.clients ({_STUBS['clients']})")
    connection.execute(
        """
        INSERT INTO slice.clients (
            external_id, key, client_type, gender, marital_status, country_of_birth,
            fatca_status, us_person, passport_country
        ) VALUES
            ('ok', 'k', 'individual', 'male', 'married', 'gb', 'w9', true, 'US'),
            ('bad', 'k', 'individual', 'alien', 'partnered', 'United Kingdom',
             'institution', false, 'GBR'),
            ('clash', 'k', 'individual', NULL, NULL, NULL, 'w8', true, NULL)
        """
    )
    clients = next(entity for entity in entities.CONFORMED if entity.slug == "clients")
    sql = clients.staging_sql(tenant_id=7, provider="sei", relations={"clients": "slice.clients"})
    connection.execute(f"CREATE VIEW staged AS {sql}")
    got = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT external_id, gender, marital_status, country_of_birth, fatca_status,"
            " us_person, passport_country FROM staged ORDER BY external_id"
        ).fetchall()
    }
    assert got["ok"] == ("male", "married", "GB", "w9", True, "US")
    assert got["bad"] == (None, None, None, None, False, None)
    assert got["clash"] == (None, None, None, "w8", None, None)


def test_accounts_refresh_their_base_currency(lake: Any) -> None:
    """An account's denomination is the custodian's, like its value. Leaving it insert-only
    kept a book in the currency of its first sighting."""
    accounts = next(entity for entity in entities.CONFORMED if entity.slug == "accounts")
    assert "currency" in accounts.updatable
    assert "currency" in _staged(lake, accounts)
    assert "opened_at" in accounts.updatable
    assert "opened_at" in _staged(lake, accounts)
    assert "bank_accounts" in _staged(lake, accounts)
    assert "bank_accounts" not in accounts.updatable


def test_an_accounts_value_comes_from_its_latest_valuation(lake: Any) -> None:
    """The declared exception in 0064: the balance is the provider's to state. It is joined here
    rather than patched on afterwards by a second pass over the book."""
    accounts = next(entity for entity in entities.CONFORMED if entity.slug == "accounts")
    assert "value" in _staged(lake, accounts)
    assert "valuations" in accounts.reads


def test_an_instrument_ongoing_charge_fills_a_blank(lake: Any) -> None:
    """The charge file is mostly empty, and Seccl publishes NULL for the same ISIN.
    Refreshing the column would clear a figure the other custodian had stored."""
    instruments = next(entity for entity in entities.CONFORMED if entity.slug == "instruments")
    assert instruments.fill_if_null == ("ocf",)
    assert "ocf" not in instruments.updatable
    assert "ocf" in _staged(lake, instruments)


def test_the_tenant_is_stamped_on_every_tenant_scoped_row(lake: Any) -> None:
    """platform.instruments is the exception, and the only one: an ISIN is not tenant data, which is
    why 0031 leaves that table un-RLS'd."""
    for entity in entities.CONFORMED:
        columns = _staged(lake, entity)
        if entity.slug in {"instruments", "fee_package_platforms"} and "tenant_id" not in columns:
            # fee_package_platforms stages a tenant_id; instruments deliberately does not.
            assert entity.slug == "instruments"
            continue
        if entity.slug == "instruments":
            assert "tenant_id" not in columns
        else:
            assert columns.get("tenant_id") in {"INTEGER", "BIGINT"}


def test_an_accounts_product_type_applies_the_wrapper_alias() -> None:
    """The account's product parent joins on the CANONICAL type, and this is executed over ROWS.

    The defect this pins: `PRODUCT_ALIASES` was applied when DISCOVERING a product and nowhere on
    the account, so the parent compared Seccl's raw 'PENSION' against a `platform.products` row
    holding 'SIPP' and never matched -- 98 of 379 accounts on the dev book, every Seccl pension,
    carried a NULL `product_id`. Drawdown then answered "pension accounts only" for real SIPPs.

    Every other test in this module DESCRIBEs an empty stub, and none of them could have caught it:
    binding is not evaluating, and a CASE arm that never fires is invisible to a schema check.
    """
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    for relation, columns in _STUBS.items():
        connection.execute(f"CREATE TABLE slice.{relation} ({columns})")
    connection.execute(
        "INSERT INTO slice.accounts (external_id, product_key) VALUES"
        # Seccl's word for the SIPP wrapper, the one alias we declare.
        " ('a-pension', 'PENSION'),"
        # Already canonical, and lower-cased, because the CASE upper()s its subject.
        " ('a-isa', 'isa'),"
        # A wrapper our enum has no member for: NULL, so the parent is a NULL rather than a guess.
        " ('a-ssas', 'SSAS'),"
        " ('a-none', NULL)"
    )
    accounts = next(entity for entity in entities.CONFORMED if entity.slug == "accounts")
    sql = accounts.staging_sql(tenant_id=7, provider="seccl", relations=_relations())
    # noqa justified: the only interpolated value is the entity's OWN generated projection, built
    # from module constants. No caller input reaches it.
    rows = connection.execute(
        f"SELECT external_id, product_key, product_type FROM ({sql}) ORDER BY external_id"  # noqa: S608
    ).fetchall()

    assert rows == [
        ("a-isa", "isa", "ISA"),
        ("a-none", None, None),
        ("a-pension", "PENSION", "SIPP"),
        ("a-ssas", "SSAS", None),
    ]

    parent = next(p for p in accounts.parents if p.column == "product_id")
    assert parent.source == "product_type", "the parent must join on the canonical type"


def test_bank_accounts_stage_as_a_json_list() -> None:
    """The column is already JSON. Quoting it again would make the merge skip the account."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    for relation, columns in _STUBS.items():
        connection.execute(f"CREATE TABLE slice.{relation} ({columns})")
    payload = '[{"provider":"sei","direction":"pay_to","provider_ref":"P1"}]'
    connection.execute(
        "INSERT INTO slice.accounts (external_id, bank_accounts) VALUES ('a-1', ?::JSON)",
        [payload],
    )
    accounts = next(entity for entity in entities.CONFORMED if entity.slug == "accounts")
    sql = accounts.staging_sql(tenant_id=7, provider="seccl", relations=_relations())
    staged = connection.execute(
        f"SELECT bank_accounts FROM ({sql}) WHERE external_id = 'a-1'"  # noqa: S608
    ).fetchone()
    assert staged is not None
    assert json.loads(staged[0]) == [
        {"provider": "sei", "direction": "pay_to", "provider_ref": "P1"}
    ]


def test_an_unnamed_model_stages_its_own_id_as_the_name() -> None:
    """`platform.models.name` is NOT NULL, and a provider is entitled to publish no name.

    Executed over ROWS, and it has to be: the coalesce is a value-dependent expression, and every
    schema check in this module DESCRIBEs an empty stub where it never fires.

    SEI is the provider that makes this real rather than defensive. Its own two model reports are
    declared and 0 rows on all 74 drops of the p04 corpus, so the catalogue is discovered from the
    accounts that name a model — and `IMPLEMENTATION_MODEL_NAME` is populated on 6% of those rows,
    so **26 of the firm's 49 models have no name anywhere**. Without the coalesce the merge fails
    the whole entity on a not-null violation, which reads as "this firm has no models" rather than
    as "this firm has models nobody named".

    The id as the name is what a later read can recognise. The nightly merge replaces a name
    that is still the model's own id, and leaves a name a firm has typed.
    """
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    for relation, columns in _STUBS.items():
        connection.execute(f"CREATE TABLE slice.{relation} ({columns})")
    connection.execute(
        "INSERT INTO slice.models (external_id, name) VALUES"
        " ('87955', 'AIM Withdrawal'),"
        " ('87984', NULL),"
        # An empty string is NOT a name either. It is what 94% of the drop's rows carry in that
        # column, and `coalesce` does not catch it — the conformed model `nullif`s it upstream, so
        # this asserts the shape the projection is actually handed.
        " ('83011', '')"
    )
    models = next(entity for entity in entities.CONFORMED if entity.slug == "models")
    sql = models.staging_sql(tenant_id=7, provider="sei", relations=_relations())
    staged = {
        row[0]: row[1]
        for row in connection.execute(
            f"SELECT external_id, name FROM ({sql}) ORDER BY external_id"  # noqa: S608
        ).fetchall()
    }

    assert staged["87955"] == "AIM Withdrawal"
    assert staged["87984"] == "87984", "an unnamed model takes its own id, and still lands"
    assert staged["83011"] == "", (
        "an EMPTY name passes through — `coalesce` only catches NULL. The conformed model is what "
        "turns SEI's blank into a NULL (`nullif(trim(...), '')`), and that is asserted in "
        "services/sei/tests/test_platform_models.py"
    )
    assert models.replace_placeholder == ("name",)
    assert models.fill_if_null == ("model_type",)


def test_a_placeholder_model_name_is_replaced_when_a_real_name_arrives() -> None:
    """The id stored as the name is the unresolved case. A name the firm typed is not."""
    from book_sink.merge import _fill_unresolved_parents

    models = next(entity for entity in entities.CONFORMED if entity.slug == "models")

    class Cursor:
        def __init__(self) -> None:
            self.sql = ""
            self.rowcount = 2

        def execute(self, sql: str, params: object = None) -> None:
            self.sql = sql

    cursor = Cursor()
    updated = _fill_unresolved_parents(cursor, models, "staged", tenant_id=1, provider="sei")
    assert updated == 2
    assert "t.name::text = l.external_id" in cursor.sql
    assert "t.model_type IS NULL AND s.model_type IS NOT NULL" in cursor.sql
    assert "COALESCE(t.name" not in cursor.sql


def test_a_numeric_title_is_replaced_and_a_word_is_not() -> None:
    """SEI's first load stored the TITLE code. A later run that has resolved it replaces
    a value that is still only digits. A courtesy title the firm has typed is not."""
    from book_sink.merge import _fill_unresolved_parents

    clients = next(entity for entity in entities.CONFORMED if entity.slug == "clients")
    assert clients.replace_numeric == ("title",)

    class Cursor:
        def __init__(self) -> None:
            self.sql = ""
            self.rowcount = 3

        def execute(self, sql: str, params: object = None) -> None:
            self.sql = sql

    cursor = Cursor()
    updated = _fill_unresolved_parents(cursor, clients, "staged", tenant_id=1, provider="sei")
    assert updated == 3
    assert "t.title::text ~ '^[0-9]+$'" in cursor.sql
    assert "title = s.title" in cursor.sql
    assert "COALESCE(t.title" not in cursor.sql


def test_a_zero_gross_does_not_beat_the_net_the_money_is_on() -> None:
    """SEI's displayed amount is the cash that moved, which is the net. A buy's net is the
    gross plus charges; a dividend's net is the gross less tax. Gross is published as 0.0000
    (a figure, not a NULL) on interest, fees and FX, and a stock transfer publishes both cash
    columns as 0 while the cost sits on book_value. A plain coalesce of gross-then-net printed
    the trade before charges and the transfer as 0.

    Executed over rows: the coalesce is value-dependent and an empty stub can never see it fire.
    """
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA slice")
    for relation, columns in _STUBS.items():
        connection.execute(f"CREATE TABLE slice.{relation} ({columns})")
    connection.execute(
        "INSERT INTO slice.transactions"
        " (external_id, account_external_id, type, gross_amount, net_amount, book_value) VALUES"
        # The cash that left the account, not the consideration before charges.
        " ('buy', 'A1', 'buy', -333.09, -338.09, NULL),"
        # The placeholder gross. The money is the net.
        " ('interest', 'A1', 'interest', 0, 12.34, NULL),"
        # A gross the provider left out altogether still falls through to the net.
        " ('fee', 'A1', 'fee', NULL, -12.5, NULL),"
        # No cash. The book value is the figure a free receipt actually carries.
        " ('transfer', 'A1', 'transfer_in', 0, 0, 3763.20),"
        # Neither stated: the column is NOT NULL, so 0 is what lands.
        " ('memo', 'A1', 'corporate_action', NULL, NULL, NULL),"
        " ('nil', 'A1', 'adjustment', 0, 0, 0)"
    )
    transactions = next(entity for entity in entities.CONFORMED if entity.slug == "transactions")
    sql = transactions.staging_sql(tenant_id=7, provider="sei", relations=_relations())
    staged = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            f"SELECT source_transaction_id, amount, settlement_amount FROM ({sql})"  # noqa: S608
        ).fetchall()
    }

    assert staged["buy"] == (Decimal("-338.09"), Decimal("-338.09"))
    assert staged["interest"] == (Decimal("12.34"), Decimal("12.34"))
    assert staged["fee"] == (Decimal("-12.5"), Decimal("-12.5"))
    assert staged["transfer"] == (Decimal("3763.20"), Decimal("0"))
    assert staged["memo"] == (Decimal("0"), None)
    assert staged["nil"] == (Decimal("0"), Decimal("0"))


def test_the_third_party_register_names_the_wrapper_it_provides(lake: Any) -> None:
    """0130: `third_party_providers.product`, derived from the register's own `product_key`.

    EXECUTED OVER ROWS, not parsed. An empty stub binds a CASE perfectly and can never see an arm
    that does not fire — which is exactly how `PRODUCT_ALIASES` came to be applied when
    DISCOVERING a product and not when resolving an account's parent, leaving 98 of 379 accounts
    with no product at all.

    The conformed model has published `product_key` all along and this projection dropped it, so
    the only thing that ever populated the old `third_party_provider_products` join table was a
    column of admin checkboxes — and it held 0 rows on every deployment.
    """
    connection = lake
    connection.execute(
        "INSERT INTO slice.third_party_providers (external_id, name, product_key) VALUES"
        # Seccl's word for the SIPP wrapper. 2 of the dev book's 7 rows carry it, so skipping the
        # alias would leave them naming a wrapper the shelf has no member for.
        " ('p-1', 'Curtis Banks', 'PENSION'),"
        " ('p-2', 'InvestAcc', 'TPPENSION'),"
        " ('p-3', 'Canada Life', 'TPOFFBOND'),"
        # A wrapper the enum has no member for, and one the register left blank: NULL beats a
        # guess, and the column is TEXT so an unmodelled wrapper is still worth showing.
        " ('p-4', 'Something New', 'ANNUITY'),"
        " ('p-5', 'Unstated', NULL)"
    )
    entity = next(e for e in entities.CONFORMED if e.slug == "third_party_providers")
    sql = entity.staging_sql(tenant_id=7, provider="seccl", relations=_relations())
    rows = connection.execute(
        f"SELECT name, product FROM ({sql}) ORDER BY name"  # noqa: S608 - the entity's own SQL
    ).fetchall()

    assert rows == [
        ("Canada Life", "TPOFFBOND"),
        ("Curtis Banks", "SIPP"),
        ("InvestAcc", "TPPENSION"),
        ("Something New", None),
        ("Unstated", None),
    ], rows


def test_a_product_platform_mapping_states_what_the_provider_does(lake: Any) -> None:
    """0122's `role`, stamped on every insert rather than once by a backfill.

    0122 set it for the rows that existed and nothing wrote it afterwards, so every mapping the
    pipeline created since landed NULL — and `GET /catalog/products` PREFERS `role = 'custodian'`
    when choosing which platform to name a product as held at, so it was silently falling through
    to "any mapping" on precisely the rows a running pipeline produces.

    The value is the PROVIDER's, which is the point: the same projection must file Morgan Lloyd's
    mapping as the scheme administrator and Seccl's as the custodian, or a SIPP custodied at SEI
    and administered at ML cannot be told apart from one held at ML.
    """
    connection = lake
    connection.execute(
        "INSERT INTO slice.accounts (external_id, product_key) VALUES ('a-1', 'PENSION')"
    )
    entity = next(e for e in entities.CONFORMED if e.slug == "product_platforms")

    def role_for(provider: str) -> object:
        sql = entity.staging_sql(tenant_id=7, provider=provider, relations=_relations())
        return connection.execute(f"SELECT role FROM ({sql})").fetchone()[0]  # noqa: S608

    assert role_for("seccl") == "custodian"
    assert role_for("sei") == "custodian"
    assert role_for("morgan-lloyd") == "scheme_administrator"
    # An unlisted provider stamps NULL rather than guessing: a wrong role is worse than none,
    # because the reader prefers the custodian's row and would name the wrong platform.
    assert role_for("newcustodian") is None


def test_the_roles_are_the_ones_the_database_admits() -> None:
    """`PROVIDER_ROLES` mirrors `app/providers.py::PROVIDER_META[...]["role"]` and must stay inside
    `product_platforms_role_check` (0122). The sink runs in the provider's own DAG and cannot
    import the API's registry, so this is the seam where the two could drift — and a role outside
    the CHECK fails the whole entity's merge, which reads as "this firm has no products"."""
    # AGAINST THE REGISTRY, not a literal. `PROVIDER_ROLES` mirrors
    # `app/providers.py::PROVIDER_META[...]["role"]` by hand, and the API side deliberately
    # DERIVES `administrators()`/`custodians()` from it — so pinning this to
    # `{"morgan-lloyd", "seccl", "sei"}` meant a fourth custodian added to the registry left the
    # pipeline stamping `product_platforms.role` NULL on every mapping it created, with
    # `GET /catalog/products` falling through to "any mapping" and naming the wrong provider on a
    # shelf item — every test green. book-sink cannot import the API, so the TEST is the seam.
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))
        # Resolved at RUN time off the sys.path insert above, which mypy cannot follow — and it
        # is guarded by the ImportError below for a checkout without the API beside it.
        from app import providers as api_providers  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - book-sink installed without the API beside it
        pytest.skip("apps/api not importable from here; the registry cannot be compared")
    assert set(entities.PROVIDER_ROLES) == set(api_providers.PROVIDERS)
    assert entities.PROVIDER_ROLES == {
        code: api_providers.PROVIDER_META[code]["role"] for code in api_providers.PROVIDERS
    }
    assert set(entities.PROVIDER_ROLES.values()) <= {
        "custodian",
        "scheme_administrator",
        "wrapper_provider",
    }


def test_product_platforms_merge_does_not_touch_the_opening_template() -> None:
    """The enrolment template is operator-owned. The pipeline updates the id it READS
    (`external_id`); if `account_open_reference` were updatable a nightly run would wipe
    the judgement the admin products page just recorded."""
    entity = next(e for e in entities.CONFORMED if e.slug == "product_platforms")
    assert entity.updatable == ("external_id",)
    assert "account_open_reference" not in entity.updatable
    # A null role is the unconnected product. The merge fills it once and leaves
    # a role the firm has already set.
    assert entity.fill_if_null == ("role",)


class TestTheShelfStatesWhetherAWrapperIsAPension:
    """`platform.products.is_pension` is what SHOWS the client record's Pension and Employer
    sections and the account's Beneficiaries panel (0136). Discovery is what CREATES a product, so
    a projection that does not state it leaves every discovered shelf item on the column's
    `false` default — and the entity is insert-only, so no later run corrects it. 0135's one-off
    UPDATE patched the rows it found and left the writer alone.
    """

    def test_a_pension_wrapper_is_staged_true(self) -> None:
        sql = entities._PRODUCTS
        assert "AS is_pension" in sql
        for wrapper in ("SIPP", "SSAS", "TPPENSION"):
            assert f"'{wrapper}'" in sql.split("AS is_pension")[0].rsplit("IN (", 1)[1]

    def test_a_gia_is_not_a_pension(self) -> None:
        listed = entities._PRODUCTS.split("AS is_pension")[0].rsplit("IN (", 1)[1]
        for wrapper in ("GIA", "ISA", "JISA", "TPONBOND", "TPOFFBOND"):
            assert f"'{wrapper}'" not in listed

    def test_tppension_is_shown_even_though_the_integration_gates_exclude_it(self) -> None:
        """What is SHOWN and what is SENT are different questions: a third-party pension is
        administered elsewhere and is not a Morgan Lloyd scheme, so `cam_createscheme`'s own set
        excludes it — while the client record must still offer the Pension section for one."""
        assert "TPPENSION" in entities.PENSION_WRAPPERS
