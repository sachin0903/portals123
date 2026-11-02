"""The whole merge chain, as `book_writer`, against a real Postgres.

This is the test the old path never had. app/materialise.py's tests exercise its Python; what broke
in production was the seam -- a credential the store had forgotten, a schema read under the wrong
name, a link discarded after the entity was created -- and every one of those reported success while
leaving the book empty.

So this drives `book_sink.merge_tenant` end to end over landing tables shaped exactly as interlace
delivers them (everything TEXT, because the Arrow path types nothing), connected as **book_writer**
rather than a superuser: if 0062's grants are short of what the merge needs, that is a failure here
and not at 3am.

What it asserts, in order of how badly each one bit:

1. a first run creates the entities AND their links -- the ML contact sync created the person,
   failed to read the id out of the action envelope, recorded no link, and duplicated them next run;
2. a second run creates nothing -- idempotency is the whole basis for a nightly batch;
3. an unresolvable parent is a RECORDED reject, not a row with a NULL that later reads as data;
4. an unmapped transaction type is a recorded reject too (117 of them on the dev book);
5. the entity's typed columns survive a human edit, and links.data does not go stale.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text

from tests.conftest import SECCL_PLATFORM_SQL

pytest.importorskip("psycopg")
book_sink = pytest.importorskip("book_sink")

PROVIDER = "seccl"

# The landing tables, exactly as `book_sink.entities`' projections publish them -- and all TEXT,
# which is what the Arrow path really delivers. If the merge stopped casting, this is where it
# shows.
_LANDING: dict[str, str] = {
    "nodes": (
        "tenant_id int, provider text, key text, lineage text, type text, name text,"
        " parent_key text, external_id text, data text"
    ),
    "instruments": (
        "external_id text, isin text, sedol text, cusip text, name text, legal_name text,"
        " asset_type text, currency text, exchange_code text, market_country text,"
        " issuer_country text, credit_rating text, interest_rate text, issued_on text,"
        " matures_on text, ocf text, source_updated_at text, data text"
    ),
    "clients": (
        "tenant_id int, external_id text, type text, name text, title text, first_name text,"
        " middle_name text, surname text, dob text, nationality text, national_identifier text,"
        " national_identifier_type text, email text, mobile text, phone text,"
        " address text, key text,"
        " gender text, marital_status text, is_vulnerable text, country_of_birth text,"
        " fatca_status text, us_person text, passport_number text, passport_country text,"
        " source_updated_at text, data text"
    ),
    "accounts": (
        "tenant_id int, external_id text, client_external_id text, key text, status text,"
        " name text, currency text, is_discretionary text, product_key text,"
        " product_external_id text, product_type text,"
        " model_key text, value text, source_updated_at text, data text"
    ),
    "products": (
        "tenant_id int, product_type text, name text, code text, enabled text, product_key text"
    ),
    "product_platforms": (
        "tenant_id int, product_type text, external_id text, external_name text, role text,"
        " enabled text, product_key text"
    ),
    "models": (
        "tenant_id int, external_id text, name text, model_type text, aim text, risk_rating text,"
        " model_charge text, vatable text, suspend_rebalances text, cash_allocation text,"
        " status text, products text, unmodelled_wrappers text, allocation_count text,"
        " source_updated_at text, data text"
    ),
    "model_platforms": ("tenant_id int, external_id text"),
    "model_allocations": (
        "tenant_id int, model_external_id text, external_asset_id text, isin text, name text,"
        " percentage text, sort_order text, cash_allocation text, as_of_date text, asset_mix text"
    ),
    "model_commentary": ("tenant_id int, model_external_id text, body text"),
    "fee_packages": (
        "tenant_id int, external_id text, name text, category text, frequency text,"
        " fee_type text, rule text, charge_basis text, payer text, currency text,"
        " applies_to text, external_name text, source_updated_at text, data text"
    ),
    "fee_package_platforms": ("tenant_id int, external_id text, external_name text, data text"),
    "transfer_providers": (
        "tenant_id int, name text, masterparty_id text, origo_id text, atg_reference text,"
        " status text, rails text, products text, enabled text, source_updated_at text, data text"
    ),
    "third_party_providers": (
        "tenant_id int, name text, enabled text, source_updated_at text, data text"
    ),
    "portfolios": (
        "tenant_id int, provider text, external_id text, account_external_id text, name text,"
        " portfolio_type text, purpose text, is_principal text, linked_external_id text, data text"
    ),
    # A custodian's own employee users (0123). `email` is nullable, which is the whole point:
    # the drop carries the population and the API carries the address, so a user the API has not
    # seen has none — and `auth.merge_provider_user` must count that rather than invent one.
    "users": (
        "tenant_id int, provider text, external_id text, email text, first_name text,"
        " last_name text, phone text"
    ),
    "valuations": (
        "tenant_id int, account_external_id text, as_of_date text, source_system text,"
        " market_value text, cash_value text, book_cost text, unrealised_gain text,"
        " opening_value text, currency text, data text"
    ),
    "transactions": (
        "tenant_id int, account_external_id text, instrument_key text, source_system text,"
        " source_transaction_id text, type text, source_type text, source_subtype text,"
        " isin text, asset_name text, amount text, currency text, quantity text, price text,"
        " settlement_amount text, fee_amount text, transaction_date text, settlement_date text,"
        " posted_at text, status text, source_updated_at text, data text"
    ),
}


def _book_dsn(db: dict[str, str]) -> str:
    """The pipeline's own credential. Derived from the superuser URL because the container's host
    port is only known there; the ROLE is what this test is about."""
    url = db["super"]
    _, _, rest = url.partition("@")
    return f"postgresql://book_writer:book_writer@{rest}"


@pytest.fixture
def tenant(super_engine: Engine) -> dict[str, Any]:
    """A firm with its own uid, and the landing tables its staging models would have delivered.

    The uid deliberately begins with a DIGIT. A real one is `encode(gen_random_bytes(8), 'hex')`, so
    six firms in ten look like this, and `inbound.401d0a…__orgs` is not an identifier -- Postgres
    reads it as "trailing junk after numeric literal". Unquoted, the pipeline worked for some firms
    and refused to exist for the rest; leaving this to chance would have made the test flaky rather
    than wrong.
    """
    with super_engine.connect() as conn:
        row = conn.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                "         'sink-' || encode(gen_random_bytes(4), 'hex'), 'Sink Test Firm')"
                " RETURNING id, uid"
            )
        ).one()
        tenant_id, uid = int(row[0]), str(row[1])
        # Created AS book_writer, because that is who creates them: interlace delivers over the
        # attached database as the pipeline's own login. Creating them as the superuser instead made
        # the first run fail with "permission denied for table …__orgs" -- 0062 grants book_writer
        # the merged relations, not whatever else appears in `inbound`.
        conn.execute(text("SET ROLE book_writer"))
        # Both providers. A SEI merge must not be able to see what Seccl staged, which is the
        # whole point of the schema split — so the empty SEI tables have to exist too.
        for provider in ("seccl", "sei"):
            for entity, columns in _LANDING.items():
                conn.execute(
                    text(
                        f"CREATE TABLE {book_sink.landing_table(uid, entity, provider)} ({columns})"
                    )
                )
        conn.execute(text("RESET ROLE"))
        # The platforms registry, seeded exactly as providers.sync_platforms() seeds it when the API
        # starts. Not optional scaffolding: platform.model_platforms and product_platforms are keyed
        # on it, and nothing had ever written to that table before 0055 -- which is what rejected
        # every product for every provider and left the whole book with no product_id.
        conn.execute(text(SECCL_PLATFORM_SQL))
    return {"id": tenant_id, "uid": uid}


def _isin(tenant: dict[str, Any]) -> str:
    """An ISIN of this firm's own. The session database is never truncated, so a fixed one
    would make every count depend on which tests ran before -- except where sharing it IS the
    test (two firms holding one fund)."""
    return f"GB00{str(tenant['uid'])[:8].upper()}"


def _stage(super_engine: Engine, tenant: dict[str, Any], isin: str | None = None) -> None:
    """One org, one instrument, one client, one account, one valuation and three transactions --
    two of which cannot be written, on purpose."""
    uid, tenant_id = tenant["uid"], tenant["id"]
    isin = isin or _isin(tenant)
    with super_engine.connect() as conn:

        def insert(entity: str, columns: str, values: str, params: dict[str, Any]) -> None:
            conn.execute(
                text(
                    # S608: the table name is book_sink's own and the values are bound below.
                    f"INSERT INTO {book_sink.landing_table(uid, entity, PROVIDER)} ({columns})"  # noqa: S608
                    f" VALUES ({values})"
                ),
                params,
            )

        insert(
            "nodes",
            "tenant_id, provider, key, lineage, type, name, external_id, data",
            ":t, 'seccl', '1', '{1}', 'firm', 'Node One', '1', '{\"n\": 1}'",
            {"t": tenant_id},
        )
        insert(
            "instruments",
            "external_id, isin, name, asset_type, currency, data",
            "'A1', :isin, 'A Fund', 'fund', 'GBP', '{\"a\": 1}'",
            {"isin": isin},
        )
        insert(
            "clients",
            "tenant_id, external_id, type, name, first_name, surname, dob, key, data",
            ":t, 'C1', 'individual', 'Ada Lovelace', 'Ada', 'Lovelace', '1980-04-01', '1',"
            " '{\"c\": 1}'",
            {"t": tenant_id},
        )
        insert(
            "accounts",
            "tenant_id, external_id, client_external_id, key, status, name, currency,"
            " is_discretionary, product_key, product_type, model_key, value, data",
            ":t, 'A100', 'C1', '1', 'active', 'ISA', 'GBP', 'true', 'ISA', 'ISA', 'M1',"
            " '1250.5000', '{\"v\": 1}'",
            {"t": tenant_id},
        )
        # The firm's shelf, as the staging model discovers it from those accounts: one wrapper the
        # enum has ('ISA') and one it does not ('SSAS'), which must be a named reject rather than a
        # guessed mapping.
        for product_type, key in (("ISA", "ISA"), (None, "SSAS")):
            insert(
                "products",
                "tenant_id, product_type, name, code, enabled, product_key",
                ":t, :pt, 'Individual Savings Account', :pt, 'true', :k",
                {"t": tenant_id, "pt": product_type, "k": key},
            )
            insert(
                "product_platforms",
                "tenant_id, product_type, external_id, external_name, enabled, product_key",
                ":t, :pt, :k, :pt, 'true', :k",
                {"t": tenant_id, "pt": product_type, "k": key},
            )
        insert(
            "models",
            "tenant_id, external_id, name, model_type, risk_rating, status, products,"
            " unmodelled_wrappers, allocation_count, data",
            ":t, 'M1', 'Balanced', 'Discretionary', '4', 'active', '{ISA,SIPP}', 'SSAS', '2',"
            " '{\"m\": 1}'",
            {"t": tenant_id},
        )
        insert("model_platforms", "tenant_id, external_id", ":t, 'M1'", {"t": tenant_id})
        # The allocation, already exploded and scaled to 0-100 by the staging projection.
        lines = (("A1", "A Fund", "24.5", "0"), ("A9", "B Fund", "70.5", "1"))
        for asset, name, pct, order in lines:
            insert(
                "model_allocations",
                "tenant_id, model_external_id, external_asset_id, name, percentage, sort_order,"
                " cash_allocation, as_of_date",
                ":t, 'M1', :a, :n, :p, :o, '5.0', '2026-08-01'",
                {"t": tenant_id, "a": asset, "n": name, "p": pct, "o": order},
            )
        insert(
            "model_commentary",
            "tenant_id, model_external_id, body",
            ":t, 'M1', 'Held for the long term.'",
            {"t": tenant_id},
        )
        # The firm's priced charges: one the conformed layer could price, and one whose shape it
        # could not read — which must be a reject, because a rule stored under the wrong shape
        # passes its CHECK and then bills the wrong amount.
        insert(
            "fee_packages",
            "tenant_id, external_id, name, category, frequency, fee_type, rule, currency,"
            " applies_to, external_name, data",
            ":t, 'F1', 'Platform charge', 'platform', 'ongoing', 'percentage',"
            " '{\"rate\": 0.35}', 'GBP', '{GIA,JISA,SIPP,TPONBOND,TPOFFBOND,TPPENSION}',"
            " 'PlatformFee', '{\"f\": 1}'",
            {"t": tenant_id},
        )
        insert(
            "fee_packages",
            "tenant_id, external_id, name, category, frequency, fee_type, rule, currency, data",
            ":t, 'F2', 'Something unpriceable', 'platform', 'ongoing', NULL, NULL, 'GBP', '{}'",
            {"t": tenant_id},
        )
        insert(
            "fee_package_platforms",
            "tenant_id, external_id, external_name, data",
            ":t, 'F1', 'PlatformFee', '{}'",
            {"t": tenant_id},
        )
        insert(
            "transfer_providers",
            "tenant_id, name, masterparty_id, origo_id, status, rails, products, enabled, data",
            ":t, 'A Ceding Scheme', 'MP1', 'ORIGO1', 'Active',"
            " '[{\"rail\": \"origo\"}]', '{ISA,SIPP}', 'true', '{\"tp\": 1}'",
            {"t": tenant_id},
        )
        insert(
            "third_party_providers",
            "tenant_id, name, enabled, data",
            ":t, 'A Bond Provider', 'false', '{\"3p\": 1}'",
            {"t": tenant_id},
        )
        insert(
            "valuations",
            "tenant_id, account_external_id, as_of_date, source_system, market_value, currency,"
            " data",
            ":t, 'A100', '2026-08-01', 'seccl', '1250.5000', 'GBP', '{}'",
            {"t": tenant_id},
        )
        # A valuation for an account this provider has never published: a recorded reject.
        insert(
            "valuations",
            "tenant_id, account_external_id, as_of_date, source_system, market_value, data",
            ":t, 'GHOST', '2026-08-01', 'seccl', '99.0000', '{}'",
            {"t": tenant_id},
        )
        insert(
            "transactions",
            "tenant_id, account_external_id, instrument_key, source_system,"
            " source_transaction_id, type, source_type, amount, currency, transaction_date, data",
            ":t, 'A100', 'A1', 'seccl', :txn, 'buy', 'Order', '500.0000', 'GBP', '2026-07-01',"
            " '{\"t\": 1}'",
            {"t": tenant_id, "txn": f"T1-{uid}"},
        )
        # The conformed layer could not map this one's taxonomy, so `type` is NULL. Filing money
        # under a guessed type is worse than not filing it.
        insert(
            "transactions",
            "tenant_id, account_external_id, source_system, source_transaction_id, type,"
            " source_type, source_subtype, amount, data",
            ":t, 'A100', 'seccl', :txn, NULL, 'Expectation', 'FAD Gross Withdrawal', '10.0000',"
            " '{}'",
            {"t": tenant_id, "txn": f"T2-{uid}"},
        )
        insert(
            "transactions",
            "tenant_id, account_external_id, source_system, source_transaction_id, type,"
            " amount, data",
            ":t, 'GHOST', 'seccl', :txn, 'buy', '1.0000', '{}'",
            {"t": tenant_id, "txn": f"T3-{uid}"},
        )


def _merge(
    db: dict[str, str],
    tenant: dict[str, Any],
    provider: str = PROVIDER,
    entities: str = PROVIDER,
) -> dict[str, dict[str, Any]]:
    """`provider` is the name the merge writes under; `entities` chooses whose entity list it runs.

    Separate on purpose. `test_a_provider_absent_from_the_registry_is_named` merges Seccl's
    entities under a provider name the registry has no row for — the two must be independent for
    that to be expressible — while a SEI-only entity (`portfolios`) needs SEI's list.
    """
    import psycopg

    with psycopg.connect(_book_dsn(db), autocommit=True) as conn:
        ledger = book_sink.merge_tenant(
            conn,
            book_sink.for_provider(entities),
            uid=tenant["uid"],
            tenant_id=tenant["id"],
            provider=provider,
        )
    for record in ledger:
        assert "error" not in record, f"{record['entity']}: {record.get('error')}"
    return {record["entity"]: record for record in ledger}


def test_a_first_run_creates_the_book_and_its_links(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["nodes"]["created"] == 1
    assert ledger["instruments"]["created"] == 1
    assert ledger["clients"]["created"] == 1
    assert ledger["accounts"]["created"] == 1
    assert ledger["valuations"]["created"] == 1
    assert ledger["transactions"]["created"] == 1

    with super_engine.connect() as conn:
        # Every linked entity carries its link, with the provider's payload on it. A row created
        # without one is the duplicate-the-client bug: the next run cannot see that it exists.
        links = dict(
            conn.execute(
                text(
                    "SELECT entity_type, count(*) FROM platform.links"
                    " WHERE tenant_id = :t AND provider = 'seccl' GROUP BY entity_type"
                ),
                {"t": tenant["id"]},
            ).all()
        )
        assert links == {"instrument": 1, "contact": 1, "account": 1, "model": 1}

        account = (
            conn.execute(
                text(
                    "SELECT a.name, a.value, a.status, a.currency, a.is_discretionary,"
                    "       c.name AS client, o.key"
                    "  FROM platform.accounts a"
                    "  LEFT JOIN crm.contacts c ON c.id = a.contact_id"
                    "  LEFT JOIN platform.nodes o ON o.id = a.node_id"
                    " WHERE a.tenant_id = :t"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
        # The parents resolved: contact through platform.links, org through its natural key. Both
        # were the commonest silent failure of the old path, counted as "unknown account".
        assert account["client"] == "Ada Lovelace"
        assert account["key"] == "1"
        assert account["name"] == "ISA"
        assert str(account["status"]) == "active"
        assert account["is_discretionary"] is True
        # The declared exception: the balance is the provider's to state, TEXT in and numeric out.
        assert float(account["value"]) == 1250.5

        contact = (
            conn.execute(
                text("SELECT uid, dob, type FROM crm.contacts WHERE tenant_id = :t"),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
        assert contact["uid"], "a contact with no uid is unaddressable by every portal surface"
        assert str(contact["dob"]) == "1980-04-01", "TEXT in, DATE out"

        transaction = (
            conn.execute(
                text(
                    "SELECT t.amount, t.type, i.isin FROM platform.transactions t"
                    " LEFT JOIN platform.instruments i ON i.id = t.instrument_id"
                    " WHERE t.tenant_id = :t AND t.source_transaction_id LIKE 'T1-%'"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
        assert float(transaction["amount"]) == 500.0
        assert str(transaction["type"]) == "buy"
        assert transaction["isin"] == _isin(tenant), "the instrument resolved through its link"


def test_a_seccl_client_product_lands_on_the_client(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The client product is one id per wrapper, stored on the contact.

    Two accounts of one wrapper share it. A later drop that names a different id updates
    the link. The account row itself does not carry the column.
    """
    _stage(super_engine, tenant)
    accounts = book_sink.landing_table(tenant["uid"], "accounts", PROVIDER)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {accounts} SET product_external_id = '02G2J'"  # noqa: S608
                " WHERE external_id = 'A100'"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {accounts}"  # noqa: S608
                " (tenant_id, external_id, client_external_id, key, status, name,"
                " currency, is_discretionary, product_key, product_type,"
                " product_external_id, model_key, value, data)"
                " VALUES (:t, 'A101', 'C1', '1', 'active', 'ISA two', 'GBP', 'true',"
                " 'ISA', 'ISA', '02G2H', 'M1', '10.0000', '{}')"
            ),
            {"t": tenant["id"]},
        )
        conn.commit()
    _merge(db, tenant)

    def stored(conn: Any) -> list[tuple[str, str]]:
        return [
            (row[0], row[1])
            for row in conn.execute(
                text(
                    "SELECT l.link_type, l.external_id FROM platform.links l"
                    " WHERE l.tenant_id = :t AND l.provider = 'seccl'"
                    "   AND l.entity_type = 'contact'"
                    "   AND l.link_type LIKE 'client_product:%'"
                ),
                {"t": tenant["id"]},
            ).all()
        ]

    with super_engine.connect() as conn:
        assert stored(conn) == [("client_product:ISA", "02G2H")]
    with super_engine.connect() as conn:
        conn.execute(text(f"UPDATE {accounts} SET product_external_id = '02G2K'"))  # noqa: S608
        conn.commit()
    _merge(db, tenant)
    with super_engine.connect() as conn:
        assert stored(conn) == [("client_product:ISA", "02G2K")]


def test_a_second_run_writes_nothing(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The basis of a nightly batch. A merge that re-wrote every row would also bury the audit
    trail: platform.links and platform.nodes carry audit.record triggers and the pipeline has no
    actor, so an idempotent merge is what keeps that trail readable."""
    _stage(super_engine, tenant)
    _merge(db, tenant)
    again = _merge(db, tenant)
    for entity, record in again.items():
        assert record["created"] == 0, f"{entity} created rows on a re-run"
        assert record["updated"] == 0, f"{entity} rewrote unchanged rows"


def test_an_account_written_before_its_client_is_linked_when_the_client_arrives(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """contact_id is not in the updatable list, and must not be: a later drop must not move an
    account onto a different person. A NULL is the other case. SEI's accounts reached the book
    while the client slice was empty, and every one of them still has no contact."""
    uid, tenant_id = tenant["uid"], tenant["id"]
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(uid, 'nodes', PROVIDER)}"  # noqa: S608
                " (tenant_id, provider, key, lineage, type, name, external_id, data)"
                " VALUES (:t, 'seccl', '1', '{1}', 'firm', 'Node One', '1', '{}')"
            ),
            {"t": tenant_id},
        )
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(uid, 'accounts', PROVIDER)}"  # noqa: S608
                " (tenant_id, external_id, client_external_id, key, status, name, currency,"
                "  is_discretionary, value, data)"
                " VALUES (:t, 'A100', 'C-later', '1', 'active', 'ISA', 'GBP', 'true',"
                "  '10.0000', '{}')"
            ),
            {"t": tenant_id},
        )

    _merge(db, tenant)
    with super_engine.connect() as conn:
        contact_id = conn.execute(
            text("SELECT contact_id FROM platform.accounts WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar_one()
    assert contact_id is None

    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(uid, 'clients', PROVIDER)}"  # noqa: S608
                " (tenant_id, external_id, type, name, first_name, surname, key, data)"
                " VALUES (:t, 'C-later', 'individual', 'Ada Lovelace', 'Ada', 'Lovelace',"
                "  '1', '{}')"
            ),
            {"t": tenant_id},
        )
    _merge(db, tenant)
    with super_engine.connect() as conn:
        linked = conn.execute(
            text(
                "SELECT c.name FROM platform.accounts a"
                " JOIN crm.contacts c ON c.id = a.contact_id WHERE a.tenant_id = :t"
            ),
            {"t": tenant_id},
        ).scalar_one()
    assert linked == "Ada Lovelace"

    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(uid, 'accounts', PROVIDER)}"  # noqa: S608
                " SET client_external_id = 'C-other'"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(uid, 'clients', PROVIDER)}"  # noqa: S608
                " (tenant_id, external_id, type, name, first_name, surname, key, data)"
                " VALUES (:t, 'C-other', 'individual', 'Grace Hopper', 'Grace', 'Hopper',"
                "  '1', '{}')"
            ),
            {"t": tenant_id},
        )
    _merge(db, tenant)
    with super_engine.connect() as conn:
        still = conn.execute(
            text(
                "SELECT c.name FROM platform.accounts a"
                " JOIN crm.contacts c ON c.id = a.contact_id WHERE a.tenant_id = :t"
            ),
            {"t": tenant_id},
        ).scalar_one()
    assert still == "Ada Lovelace"


def test_the_run_is_recorded_in_the_book(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The merge writes its own ledger to `platform.merge_runs` (0076).

    Until it did, "did this firm's chain run?" was answerable only from
    `book_{uid}.merge_ledger` — a warehouse relation declaring no strategy, so holding ONE run,
    reachable only from the admin SQL console. A firm with no data and a firm whose daemon never
    ran presented identically everywhere else.

    ONE `run_id` for the whole walk: minted per call rather than defaulted in the column, because
    a default would give every entity its own and turn a run into N unrelated rows.
    """
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    with super_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT run_id, entity, created FROM platform.merge_runs"
                    " WHERE tenant_id = :t AND provider = :p"
                ),
                {"t": tenant["id"], "p": PROVIDER},
            )
            .mappings()
            .all()
        )

    assert {r["entity"] for r in rows} == set(ledger), "every entity merged is an entity recorded"
    assert len({r["run_id"] for r in rows}) == 1, "one walk is one run"
    recorded = {r["entity"]: r["created"] for r in rows}
    assert recorded["accounts"] == ledger["accounts"]["created"]

    # A second walk is a second run, not an amendment of the first: the ledger is append-only, so
    # history accumulates and "it wrote nothing tonight" stays distinguishable from "it never ran".
    _merge(db, tenant)
    with super_engine.connect() as conn:
        runs = conn.execute(
            text("SELECT count(DISTINCT run_id) FROM platform.merge_runs WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        ).scalar_one()
    assert runs == 2


def test_an_unresolvable_parent_is_recorded_not_written(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The reject ledger is the whole point of 0062: a projection with no merge leaves a firm
    looking empty, which is indistinguishable from "this firm has no data with this provider". The
    reasons are the only thing that tells the two apart."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["valuations"]["rejected"] == 1
    assert any("unresolved account_id" in reason for reason in ledger["valuations"]["reasons"])

    with super_engine.connect() as conn:
        rejects = (
            conn.execute(
                text(
                    "SELECT entity, reason, row_key FROM platform.merge_rejects"
                    " WHERE tenant_id = :t ORDER BY entity, row_key"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .all()
        )
    # Persisted, not just returned in an HTTP response nobody kept.
    assert {row["entity"] for row in rejects} == {
        "valuations",
        "transactions",
        # The staged SSAS wrapper: unmodelled, so it reaches neither the shelf nor its reference.
        "products",
        "product_platforms",
        # And the fee structure whose shape the conformed layer could not price.
        "fee_packages",
    }
    ghost = next(row for row in rejects if row["entity"] == "valuations")
    assert "GHOST" in ghost["reason"]
    # The KEY, never the row: a rejected transaction is still client data and this is not a
    # quarantine.
    assert ghost["row_key"] == "GHOST"

    with super_engine.connect() as conn:
        written = conn.execute(
            text("SELECT count(*) FROM platform.valuations WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        ).scalar_one()
    assert written == 1, "the unresolvable valuation must not be written with a NULL account"


def test_an_unmapped_transaction_type_is_named_in_the_ledger(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """113 of the dev book's 117 rejects are one type. Pooled by reason, so it reads as one line an
    operator can act on rather than 117 rows."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["transactions"]["rejected"] == 2  # the unmapped type, and the ghost account
    reasons = ledger["transactions"]["reasons"]
    assert "unmapped type: Expectation/FAD Gross Withdrawal" in reasons
    assert reasons["unmapped type: Expectation/FAD Gross Withdrawal"] == 1

    with super_engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM platform.transactions WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        ).scalar_one()
    assert count == 1, "money under a guessed type is worse than money not filed"


def test_a_firm_edit_survives_the_provider(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The policy, at the far end of the chain: the typed columns are Graphene's, the payload is the
    provider's and stays current."""
    _stage(super_engine, tenant)
    _merge(db, tenant)
    with super_engine.connect() as conn:
        # A person's `name` is their parts (0246), so the firm corrects a part and the name follows.
        conn.execute(
            text("UPDATE crm.contacts SET surname = 'King' WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(tenant['uid'], 'clients', PROVIDER)}"  # noqa: S608
                " SET data = '{\"c\": 2}'"
            )
        )
    ledger = _merge(db, tenant)
    assert ledger["clients"]["updated"] == 1

    with super_engine.connect() as conn:
        name, surname = conn.execute(
            text("SELECT name, surname FROM crm.contacts WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        ).one()
        payload = conn.execute(
            text(
                "SELECT data FROM platform.links WHERE tenant_id = :t AND entity_type = 'contact'"
            ),
            {"t": tenant["id"]},
        ).scalar_one()
    assert (name, surname) == ("Ada King", "King")
    assert payload == {"c": 2}, "the provider's payload must not go stale"


def test_two_firms_holding_one_fund_reach_the_same_instrument(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any], request: Any
) -> None:
    """platform.instruments is global and deduped by ISIN (0031: "an ISIN is not tenant data"), so
    the second firm to hold a fund must ADOPT the row rather than insert a second one --
    instruments_isin_key would refuse it and take the whole entity down with it.

    This is what 0066 exists for. The old key on platform.links was
    (provider, entity_type, entity_id, link_type) with no tenant_id, so the second firm's mapping
    collided with the first firm's and could not be recorded at all. Latent only because one tenant
    on dev is enabled for Seccl.
    """
    _stage(super_engine, tenant)
    _merge(db, tenant)

    with super_engine.connect() as conn:
        row = conn.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                "         'sink-' || encode(gen_random_bytes(4), 'hex'), 'Second Firm')"
                " RETURNING id, uid"
            )
        ).one()
        other = {"id": int(row[0]), "uid": str(row[1])}
        conn.execute(text("SET ROLE book_writer"))
        for provider in ("seccl", "sei"):
            for entity, columns in _LANDING.items():
                conn.execute(
                    text(
                        "CREATE TABLE "
                        f"{book_sink.landing_table(other['uid'], entity, provider)} ({columns})"
                    )
                )
        conn.execute(text("RESET ROLE"))
    _stage(super_engine, other, isin=_isin(tenant))

    before = _instrument_count(super_engine, _isin(tenant))
    ledger = _merge(db, other)
    assert ledger["instruments"]["created"] == 0, "the ISIN exists; adopting it is the point"
    assert _instrument_count(super_engine, _isin(tenant)) == before

    with super_engine.connect() as conn:
        holders = conn.execute(
            text(
                "SELECT count(DISTINCT tenant_id) FROM platform.links"
                " WHERE entity_type = 'instrument' AND external_id = 'A1'"
                "   AND entity_id = (SELECT id FROM platform.instruments WHERE isin = :isin)"
            ),
            {"isin": _isin(tenant)},
        ).scalar_one()
    assert holders == 2, "each firm holds its own mapping to the one shared instrument"


def test_a_firm_adopts_an_isin_inserted_while_it_waits(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Two firms can merge one new fund in the same sync. The later one must adopt the row the
    earlier one inserts while it holds the catalogue lock. Inserting instead is refused by
    instruments_isin_key and fails that firm's whole instrument merge — the Select Platform
    failure, where Graphene's instruments committed a moment earlier.
    """
    import threading

    import psycopg

    isin = f"GB00{tenant['uid'][:8].upper()}"
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(tenant['uid'], 'instruments', PROVIDER)}"  # noqa: S608
                " (external_id, isin, name, data) VALUES ('A1', :isin, 'A Fund', '{}')"
            ),
            {"isin": isin},
        )
    entity = next(item for item in book_sink.for_provider(PROVIDER) if item.slug == "instruments")
    locked = threading.Event()
    release = threading.Event()
    outcome: dict[str, Any] = {}

    def hold() -> None:
        with psycopg.connect(_book_dsn(db)) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    book_sink.merge._hold_shared_catalogue(cur, entity.target)
                    locked.set()
                    assert release.wait(timeout=10), "the merge never arrived"
                    cur.execute(
                        "INSERT INTO platform.instruments (isin, name) VALUES (%s, 'A Fund')",
                        (isin,),
                    )

    def merge() -> None:
        locked.wait(timeout=10)
        with psycopg.connect(_book_dsn(db)) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    outcome["ledger"] = book_sink.merge.merge_entity(
                        cur, entity, uid=tenant["uid"], tenant_id=tenant["id"], provider=PROVIDER
                    )

    holder = threading.Thread(target=hold)
    merger = threading.Thread(target=merge)
    holder.start()
    assert locked.wait(timeout=10), "the catalogue lock was never taken"
    merger.start()
    release.set()
    holder.join(timeout=15)
    merger.join(timeout=15)
    assert not holder.is_alive() and not merger.is_alive()
    ledger = outcome["ledger"]
    assert ledger.created == 0, "the ISIN was inserted while this merge waited"
    assert ledger.rejected == 0
    assert _instrument_count(super_engine, isin) == 1
    with super_engine.connect() as conn:
        holders = conn.execute(
            text(
                "SELECT count(*) FROM platform.links"
                " WHERE tenant_id = :t AND entity_type = 'instrument' AND external_id = 'A1'"
                "   AND entity_id = (SELECT id FROM platform.instruments WHERE isin = :isin)"
            ),
            {"t": tenant["id"], "isin": isin},
        ).scalar_one()
    assert holders == 1


def test_two_firms_syncing_one_provider_transaction_each_keep_a_row(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Two tenants on one custodian firm stage the same provider transaction id. Each keeps a
    row. The second merge must not rewrite the first firm's amount."""
    _stage(super_engine, tenant)
    first = _merge(db, tenant)
    assert first["transactions"]["created"] == 1
    shared = f"T1-{tenant['uid']}"

    with super_engine.connect() as conn:
        row = conn.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                "         'sink-' || encode(gen_random_bytes(4), 'hex'), 'Second Firm')"
                " RETURNING id, uid"
            )
        ).one()
        other = {"id": int(row[0]), "uid": str(row[1])}
        conn.execute(text("SET ROLE book_writer"))
        for provider in ("seccl", "sei"):
            for entity, columns in _LANDING.items():
                conn.execute(
                    text(
                        "CREATE TABLE "
                        f"{book_sink.landing_table(other['uid'], entity, provider)} ({columns})"
                    )
                )
        conn.execute(text("RESET ROLE"))
    _stage(super_engine, other)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(other['uid'], 'transactions', PROVIDER)}"  # noqa: S608
                " SET source_transaction_id = :shared, amount = '42.0000'"
                " WHERE source_transaction_id = :own"
            ),
            {"shared": shared, "own": f"T1-{other['uid']}"},
        )
    second = _merge(db, other)
    assert second["transactions"]["created"] == 1

    with super_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tenant_id, amount::text FROM platform.transactions"
                " WHERE source_system = 'seccl' AND source_transaction_id = :id"
            ),
            {"id": shared},
        ).all()
    assert {(int(tenant_id), amount) for tenant_id, amount in rows} == {
        (tenant["id"], "500.0000"),
        (other["id"], "42.0000"),
    }


def test_a_second_provider_id_for_one_isin_is_rejected_not_duplicated(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """A provider publishing two of its own ids for one ISIN cannot have both: the instrument is one
    row. Inserting the second would be refused by instruments_isin_key and take the entity with it,
    so it is a named reject -- the mapping we kept is stated, and the one we did not is visible."""
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(tenant['uid'], 'instruments', PROVIDER)}"  # noqa: S608
                " (external_id, isin, name, data)"
                " VALUES ('A1-DUP', :isin, 'A Fund, again', '{}')"
            ),
            {"isin": _isin(tenant)},
        )
    ledger = _merge(db, tenant)
    assert ledger["instruments"]["created"] == 1, "one instrument, from whichever id was first"
    assert ledger["instruments"]["rejected"] == 1
    assert any("already mapped" in reason for reason in ledger["instruments"]["reasons"])
    assert _instrument_count(super_engine, _isin(tenant)) == 1


def _instrument_count(super_engine: Engine, isin: str) -> int:
    with super_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM platform.instruments WHERE isin = :isin"),
                {"isin": isin},
            ).scalar_one()
        )


def test_an_unstaged_entity_says_so(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """ "No rows" and "no table" are different answers, and conflating them is how a provider that
    has not run yet reads as a firm with no data."""
    with super_engine.connect() as conn:
        table = book_sink.landing_table(tenant["uid"], "transactions", PROVIDER)
        conn.execute(text(f"DROP TABLE {table}"))
    ledger = _merge(db, tenant)
    assert ledger["transactions"]["note"].startswith("not staged:")
    assert ledger["transactions"]["created"] == 0


def test_the_shelf_is_discovered_and_the_account_carries_its_wrapper(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`platform.accounts.product_id` was NULL for the whole book: `_sync_products` computed the
    key->id map and `materialise_tenant` discarded it, and `_upsert_account` had no model_id
    parameter at all. An account with no product has no `product_type`, which is what every "is this
    a pension?" surface gates on -- so POST /crm/accounts/{id}/drawdown-session answered "pension
    accounts only" for every real SIPP in the book.
    """
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["products"]["created"] == 1, "one wrapper the enum has; SSAS is a reject"
    assert ledger["product_platforms"]["created"] == 1
    assert ledger["models"]["created"] == 1
    assert ledger["model_platforms"]["created"] == 1

    with super_engine.connect() as conn:
        account = (
            conn.execute(
                text(
                    "SELECT CAST(p.product_type AS text) AS wrapper, p.name AS product_name,"
                    "       p.code, pp.external_id AS platform_ref, m.name AS model,"
                    "       CAST(m.products AS text) AS model_products, mp.external_id AS model_ref"
                    "  FROM platform.accounts a"
                    "  LEFT JOIN platform.products p ON p.id = a.product_id"
                    "  LEFT JOIN platform.product_platforms pp ON pp.product_id = p.id"
                    "  LEFT JOIN platform.models m ON m.id = a.model_id"
                    "  LEFT JOIN platform.model_platforms mp ON mp.model_id = m.id"
                    " WHERE a.tenant_id = :t"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
    assert account["wrapper"] == "ISA", "the account resolves the wrapper it is held in"
    # A READABLE name and the wrapper CODE, the right way round: discovery used to write the enum
    # member into `name` and leave `code` NULL, so a shelf listed "TPONBOND" as a product's NAME.
    assert account["product_name"] == "Individual Savings Account"
    assert account["code"] == "ISA"
    # The exact string the custodian's API takes, which is what makes the row actionable.
    assert account["platform_ref"] == "ISA"
    assert account["model"] == "Balanced", "and the model it is managed to"
    assert account["model_products"] == "{ISA,SIPP}"
    assert account["model_ref"] == "M1"


def test_an_unmodelled_wrapper_is_named_never_guessed(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Seccl's models list SSAS and our enum has no member for it. Inventing a mapping is how a
    firm's shelf ends up wrong, so it is a reject on the shelf -- and, one level up, a counted
    OBSERVATION on the model, which is written anyway because four of its five wrappers are real."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["products"]["rejected"] == 1
    assert "unmodelled wrapper: SSAS" in ledger["products"]["reasons"]

    # The model was WRITTEN, and the wrapper we could not map is still counted: `rejected` stays 0
    # because the row went in. That distinction is the whole reason the ledger has both.
    assert ledger["models"]["created"] == 1
    assert ledger["models"]["rejected"] == 0
    assert ledger["models"]["reasons"] == {"unmodelled model wrapper: SSAS": 1}


def test_one_providers_clients_are_not_merged_as_another(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Seccl's staged clients must not become SEI contacts.

    On 27 Sep 2026 both pipelines delivered into `inbound.{uid}__clients`. SEI's merge ran three
    seconds after Seccl's and inserted all 304 of Seccl's people again, stamped `provider = sei`.
    The landing schema is the provider's now, so an empty SEI table stays empty.
    """
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant, provider="sei", entities="sei")
    assert ledger["clients"]["created"] == 0
    with super_engine.connect() as conn:
        copied = conn.execute(
            text(
                "SELECT count(*) FROM platform.links"
                " WHERE tenant_id = :t AND provider = 'sei' AND entity_type = 'contact'"
            ),
            {"t": tenant["id"]},
        ).scalar_one()
    assert copied == 0


def test_a_provider_outside_the_vocabulary_cannot_run_at_all(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """INVERTED BY 0131, deliberately, and this is the better failure.

    It used to assert that a provider `platform.platforms` had no row for produced a per-entity
    REJECT reading "nothing registered for X" — because the registry was a table, the merge
    resolved `provider -> id` against it, and a table can be empty. It was: nothing had ever
    written to it, which rejected every product for every provider and left the whole book with no
    `product_id`, read as "this firm has no data with this custodian".

    The provider is a `platform.provider_code` value now, so an unknown one is refused by the
    database's own vocabulary before a single row is staged. A chain cannot half-run under a name
    that does not exist — which is what the reject reason was trying to tell somebody after the
    fact.
    """
    import psycopg

    _stage(super_engine, tenant)
    # The rows have to be in THIS provider's schema. Reading Seccl's table and then failing the
    # enum would be the old bug wearing the vocabulary check as a costume.
    uid = tenant["uid"]
    with super_engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS inbound_not_registered"))
        conn.execute(text("GRANT USAGE ON SCHEMA inbound_not_registered TO book_writer"))
        for entity in _LANDING:
            source = book_sink.landing_table(uid, entity, PROVIDER)
            target = book_sink.landing_table(uid, entity, "not_registered")
            conn.execute(text(f"CREATE TABLE {target} AS SELECT * FROM {source}"))  # noqa: S608
            conn.execute(text(f"GRANT SELECT ON {target} TO book_writer"))
    with psycopg.connect(_book_dsn(db), autocommit=True) as conn:
        ledger = book_sink.merge_tenant(
            conn,
            book_sink.for_provider(PROVIDER),
            uid=tenant["uid"],
            tenant_id=tenant["id"],
            provider="not_registered",
        )

    # The entities that RESOLVE a provider all fail, and each names the vocabulary. Not every
    # entity: `orgs` carries the provider as a plain staged column that Postgres coerces, so it
    # is the one that still lands — which is worth knowing rather than asserting away, because it
    # means a chain under an unknown name is not atomic.
    #
    # What matters is the contrast with the old shape: that reported a reject on
    # `product_platforms` alone and let everything downstream SUCCEED, so a firm's book filled up
    # under a provider name nothing recognised. Now the failure is loud, per entity, and says
    # which vocabulary refused it.
    assert ledger, "the chain ran no entities at all, which says nothing"
    failed = [record for record in ledger if "error" in record]
    assert failed, ledger
    for record in failed:
        assert "provider_code" in record["error"], record
        assert "not_registered" in record["error"], record
    assert all(record["created"] == 0 for record in failed), failed
    # The linked entities — the ones whose identity IS a `platform.links` row — are the ones that
    # must not half-run.
    assert {"instruments", "product_platforms"} <= {r["entity"] for r in failed}, failed


def test_the_fee_register_lands_with_its_wrappers_inverted(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Seccl states which wrappers a charge EXCLUDES; platform.fee_packages stores the positive
    list. Nothing excluded is NULL, not all seven — NULL is `applies_to`'s own way of saying "no
    restriction", and materialising the whole enum would freeze today's membership into every
    firm's register the day an eighth wrapper is added."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["fee_packages"]["created"] == 1
    assert ledger["fee_packages"]["rejected"] == 1
    assert any("unmapped fee shape" in reason for reason in ledger["fee_packages"]["reasons"])
    assert ledger["fee_package_platforms"]["created"] == 1

    with super_engine.connect() as conn:
        fee = (
            conn.execute(
                text(
                    "SELECT f.name, CAST(f.fee_type AS text) AS fee_type, f.rule, f.enabled,"
                    "       CAST(f.applies_to AS text) AS applies_to, fp.external_name"
                    "  FROM platform.fee_packages f"
                    "  LEFT JOIN platform.fee_package_platforms fp ON fp.fee_package_id = f.id"
                    " WHERE f.tenant_id = :t"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
    assert fee["fee_type"] == "percentage"
    assert fee["rule"] == {"rate": 0.35}
    # Discovered ENABLED: a fee structure exists because someone at the firm priced it, so arriving
    # switched off would hide the register the sync just found.
    assert fee["enabled"] is True
    # Parsed as a set, not searched as a string: "ISA" is a substring of "JISA", so a naive
    # `not in` on the rendered array passes for the wrong reason.
    applies = set(str(fee["applies_to"]).strip("{}").split(","))
    assert "ISA" not in applies, "the excluded wrapper is the one left out"
    assert {"GIA", "JISA", "SIPP"} <= applies
    assert fee["external_name"] == "PlatformFee", "the custodian's own word for it"


def test_the_two_provider_registers_arrive_on_the_right_side_of_the_switch(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The distinction is whose list it is. The transfer register is the custodian's list of schemes
    an instruction can actually name, so arriving off meant every new transfer failed to find its
    ceding provider until someone enabled it by hand. The third-party register is the platform's
    whole address book, so arriving ON would put counterparties in front of an adviser as though the
    firm had approved them."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)

    assert ledger["transfer_providers"]["created"] == 1
    assert ledger["third_party_providers"]["created"] == 1

    with super_engine.connect() as conn:
        ceding = (
            conn.execute(
                text(
                    "SELECT name, masterparty_id, origo_id, status, rails,"
                    "       CAST(products AS text) AS products, enabled"
                    "  FROM platform.transfer_providers WHERE tenant_id = :t"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
        third_party = (
            conn.execute(
                text(
                    "SELECT name, enabled FROM platform.third_party_providers WHERE tenant_id = :t"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
    # Keyed on the PROVIDER'S id, not the name (0046): one scheme exists twice in Seccl's register,
    # once on Origo and once on ATG, and keying on name silently lost a rail.
    assert ceding["masterparty_id"] == "MP1"
    assert ceding["rails"] == [{"rail": "origo"}], "a STRUCT[] in the lake, jsonb in the book"
    assert "SIPP" in str(ceding["products"])
    assert ceding["enabled"] is True
    assert third_party["enabled"] is False


def test_an_operator_switching_a_scheme_off_keeps_it_off(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`enabled` is insert-only on both registers and on the fee register. Discovery decides a row
    EXISTS; whether the firm uses it is the firm's, and a nightly run must not re-assert it."""
    _stage(super_engine, tenant)
    _merge(db, tenant)
    with super_engine.connect() as conn:
        conn.execute(
            text("UPDATE platform.transfer_providers SET enabled = false WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        conn.execute(
            text("UPDATE platform.fee_packages SET enabled = false WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        # A changed payload, so the merge has a genuine reason to touch both rows.
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(tenant['uid'], 'transfer_providers', PROVIDER)}"  # noqa: S608
                " SET status = 'Suspended'"
            )
        )
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(tenant['uid'], 'fee_packages', PROVIDER)}"  # noqa: S608
                " SET name = 'Platform charge (2026)'"
            )
        )
    ledger = _merge(db, tenant)
    assert ledger["transfer_providers"]["updated"] == 1
    assert ledger["fee_packages"]["updated"] == 1

    with super_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT (SELECT enabled FROM platform.transfer_providers WHERE tenant_id = :t),"
                "       (SELECT enabled FROM platform.fee_packages WHERE tenant_id = :t),"
                "       (SELECT status FROM platform.transfer_providers WHERE tenant_id = :t),"
                "       (SELECT name FROM platform.fee_packages WHERE tenant_id = :t)"
            ),
            {"t": tenant["id"]},
        ).one()
    assert rows[0] is False and rows[1] is False, "the operator's decision survives the run"
    assert rows[2] == "Suspended", "and the provider's own columns are still refreshed"
    assert rows[3] == "Platform charge (2026)"


def test_a_model_version_is_minted_once_and_its_allocation_lands(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """A rebalance is one reviewable change. Without the comparison every sync would mint a version
    and the history whose whole purpose is to show WHEN a model was rebalanced would just count sync
    runs."""
    _stage(super_engine, tenant)
    ledger = _merge(db, tenant)
    assert ledger["model_allocations"]["created"] == 1, "one version"
    assert ledger["model_commentary"]["created"] == 1

    with super_engine.connect() as conn:
        version = (
            conn.execute(
                text(
                    "SELECT v.version, v.cash_allocation, v.published_at IS NOT NULL AS published,"
                    "       count(a.id) AS lines, sum(a.percentage) AS total"
                    "  FROM platform.model_versions v"
                    "  JOIN platform.model_allocations a ON a.model_version_id = v.id"
                    " WHERE v.tenant_id = :t GROUP BY v.id, v.version, v.cash_allocation,"
                    "       v.published_at"
                ),
                {"t": tenant["id"]},
            )
            .mappings()
            .one()
        )
        instrument = conn.execute(
            text(
                "SELECT count(*) FROM platform.model_allocations a"
                " JOIN platform.instruments i ON i.id = a.instrument_id"
                " WHERE a.tenant_id = :t"
            ),
            {"t": tenant["id"]},
        ).scalar_one()
    assert version["version"] == 1
    assert version["lines"] == 2
    # 24.5 + 70.5 + 5 cash = 100. Percentages arrive as FRACTIONS from Seccl and the projection
    # scales them; storing the fraction passes the CHECK and then prices a model at a hundredth of
    # its weight.
    assert float(version["total"]) + float(version["cash_allocation"]) == 100.0
    assert version["published"] is True, "the custodian is already trading to it"
    # One line names an asset this tenant has a link for and one does not; a missing instrument is
    # a NULL, not a lost line.
    assert instrument == 1

    again = _merge(db, tenant)
    assert again["model_allocations"]["created"] == 0, "an unchanged allocation is not a rebalance"
    assert again["model_commentary"]["created"] == 0, "nor is the same note, re-published"


def test_a_rebalance_mints_the_next_version_and_keeps_the_old_one(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The history IS the point: a version is written once and never updated, so what the model was
    stays readable after it changes."""
    _stage(super_engine, tenant)
    _merge(db, tenant)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(tenant['uid'], 'model_allocations', PROVIDER)}"  # noqa: S608
                " SET percentage = '30.5' WHERE external_asset_id = 'A1'"
            )
        )
    ledger = _merge(db, tenant)
    assert ledger["model_allocations"]["created"] == 1

    with super_engine.connect() as conn:
        versions = conn.execute(
            text(
                "SELECT version, (SELECT sum(percentage) FROM platform.model_allocations a"
                "                  WHERE a.model_version_id = v.id) AS total"
                "  FROM platform.model_versions v WHERE v.tenant_id = :t ORDER BY version"
            ),
            {"t": tenant["id"]},
        ).all()
    assert [row[0] for row in versions] == [1, 2]
    assert float(versions[0][1]) == 95.0, "version 1 still says what the model was"
    assert float(versions[1][1]) == 101.0


def test_a_model_with_no_allocation_is_counted_not_failed(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """The model still exists and accounts still name it; it just has no version to show. Counted in
    the ledger for the same reason the unmodelled wrapper is -- so "no donut" has an explanation."""
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                "DELETE FROM "  # noqa: S608
                f"{book_sink.landing_table(tenant['uid'], 'model_allocations', PROVIDER)}"
            )
        )
        conn.execute(
            text(
                f"UPDATE {book_sink.landing_table(tenant['uid'], 'models', PROVIDER)}"  # noqa: S608
                " SET allocation_count = '0'"
            )
        )
    ledger = _merge(db, tenant)
    assert ledger["models"]["created"] == 1
    assert ledger["models"]["reasons"].get("no allocations published") == 1
    assert ledger["model_allocations"]["created"] == 0


def test_the_merge_stamps_both_timestamp_columns(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`last_merged_at` (0079) is what this value has always meant — the merge stamping it — and
    `last_fanout_at` is the name it carried from the deleted three-hop path.

    EXPAND PHASE, so both are written: readers prefer the new column and fall back to the old for
    rows last written before the migration, and the contract phase drops the old one once nothing
    reads it. Writing only the new one would make every firm read "never merged" on any surface
    still on the old name; writing only the old one would make the rename cosmetic.
    """
    _stage(super_engine, tenant)
    # The stamp is keyed on the ENABLEMENT, which the other cases do not need and so do not create.
    with super_engine.connect() as conn:
        enablement = conn.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled)"
                " VALUES (:t, :p, '{}'::jsonb, true) RETURNING id"
            ),
            {"t": tenant["id"], "p": PROVIDER},
        ).scalar_one()
        conn.commit()

    import psycopg

    with psycopg.connect(_book_dsn(db), autocommit=True) as conn:
        book_sink.merge_tenant(
            conn,
            book_sink.for_provider(PROVIDER),
            uid=tenant["uid"],
            tenant_id=tenant["id"],
            provider=PROVIDER,
            enablement_id=int(enablement),
        )

    with super_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT last_fanout_at, last_merged_at FROM platform.tenant_platforms"
                " WHERE id = :id"
            ),
            {"id": enablement},
        ).one()
    assert row[0] is not None, "the legacy column must keep being written during expand"
    assert row[1] is not None, "the merge must stamp last_merged_at"


def test_a_firm_with_no_active_storage_registers_no_models(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`book_sink.tenants.enabled()` INNER JOINs an ACTIVE `storage.tenants` row.

    Deliberate — a firm with no bucket has nowhere to put its own lake — but the consequence is
    the pipeline's signature failure: such a firm registers NO models on the daemon, so nothing
    runs for it, silently and for ever, while the admin page shows it enabled and green.

    The rule was tested against `providers.enabled_tenants`, the OLD path's tenant iterator,
    which nothing calls any more. Moved here, to the function that actually decides, before that
    dead code goes.
    """
    from book_sink import tenants as tenants_mod
    from book_sink.config import SinkConfig

    config = SinkConfig(book_dsn=_book_dsn(db), datalake_dsn="")

    with super_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled)"
                " VALUES (:t, :p, '{}'::jsonb, true)"
            ),
            {"t": tenant["id"], "p": PROVIDER},
        )
        # The fixture inserts the tenant directly, so it has no storage row — which is itself the
        # state this test is about. Give it an active one to establish the baseline.
        conn.execute(
            text(
                "INSERT INTO storage.tenants (tenant_id, endpoint, bucket, status)"
                " VALUES (:t, '', :bucket, 'active')"
            ),
            {"t": tenant["id"], "bucket": f"t-{tenant['uid']}"},
        )
        conn.commit()

    def uids() -> set[str]:
        return {s.uid for s in tenants_mod.enabled(PROVIDER, config)}

    assert tenant["uid"] in uids()

    with super_engine.connect() as conn:
        conn.execute(
            text("UPDATE storage.tenants SET status = 'suspended' WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        conn.commit()
    assert tenant["uid"] not in uids(), "a suspended bucket must drop the firm from the chain"

    with super_engine.connect() as conn:
        conn.execute(
            text("UPDATE storage.tenants SET status = 'active' WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        conn.execute(
            text("UPDATE platform.tenant_platforms SET enabled = false WHERE tenant_id = :t"),
            {"t": tenant["id"]},
        )
        conn.commit()
    assert tenant["uid"] not in uids(), "a disabled enablement must drop out too"


def test_an_unset_book_dsn_registers_nothing_and_says_so() -> None:
    """The other half, and the one that was inert in production: with no `BOOK_WRITER_DSN` the
    daemon registers no tenant models at all, serves happily and reports healthy. Empty rather
    than raising is deliberate (`interlace plan` must work on a laptop with no book), so the
    WARNING is the only thing that separates a misconfigured daemon from one with no tenants."""
    import logging

    from book_sink import tenants as tenants_mod
    from book_sink.config import SinkConfig

    # Its own handler rather than `caplog`: this module's logger is written to by other tests in
    # this file, and pytest's capture plugin is configured per project — a warning that appears or
    # not depending on test ORDER would be worse than no assertion at all.
    seen: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    logger = logging.getLogger("book_sink.tenants")
    handler = _Capture(level=logging.WARNING)
    previous, was_disabled = logger.level, logger.disabled
    # `disabled` as well as the level: something in this suite's import graph runs a logging
    # config that switches existing loggers off, and a disabled logger drops the record before
    # any handler or level is consulted.
    logger.disabled = False
    # The level too: another test in this file may have raised it, and `logger.warning` checks
    # `isEnabledFor` before it reaches any handler.
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        assert tenants_mod.enabled(PROVIDER, SinkConfig(book_dsn="", datalake_dsn="")) == []
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        logger.disabled = was_disabled
    assert any("BOOK_WRITER_DSN" in message for message in seen), seen


def test_an_aliased_wrapper_resolves_to_the_canonical_product(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """Seccl's `PENSION` account must reach the `SIPP` product row, through the merge.

    `PRODUCT_ALIASES = {"PENSION": "SIPP"}` was applied when DISCOVERING a product and nowhere on
    the account, and the parent joins `products.product_type = s.<source>`. So the account staged
    'PENSION', the shelf held 'SIPP', they never matched, and **98 of 379 accounts on the dev book
    -- every Seccl pension -- carried a NULL `product_id`**.

    That is not cosmetic. `product_type` is what five surfaces gate on: drawdown answered "Drawdown
    applies to pension accounts only" for real SIPPs, `provider_positions` never rendered the 0117
    crystallisation and scheme-cash block, the ML valuation push refused, and the contributions
    pension filter and the MPAA allowance gate both missed.

    Pinned HERE as well as in `pkgs/book-sink`'s own suite because the two halves fail differently:
    that one proves the projection emits `SIPP`, this one proves the parent join finds the row.
    """
    uid, tenant_id = tenant["uid"], tenant["id"]
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:

        def insert(entity: str, columns: str, values: str, params: dict[str, Any]) -> None:
            conn.execute(
                text(
                    f"INSERT INTO {book_sink.landing_table(uid, entity, PROVIDER)} ({columns})"  # noqa: S608
                    f" VALUES ({values})"
                ),
                params,
            )

        # The shelf as discovery writes it: the CANONICAL type, with the provider's own word kept
        # beside it. This is exactly the pairing that never matched.
        insert(
            "products",
            "tenant_id, product_type, name, code, enabled, product_key",
            ":t, 'SIPP', 'Self-Invested Personal Pension', 'SIPP', 'true', 'PENSION'",
            {"t": tenant_id},
        )
        insert(
            "product_platforms",
            "tenant_id, product_type, external_id, external_name, enabled, product_key",
            ":t, 'SIPP', 'PENSION', 'SIPP', 'true', 'PENSION'",
            {"t": tenant_id},
        )
        insert(
            "accounts",
            "tenant_id, external_id, client_external_id, key, status, name, currency,"
            " is_discretionary, product_key, product_type, value, data",
            ":t, 'A200', 'C1', '1', 'active', 'SIPP', 'GBP', 'true', 'PENSION', 'SIPP',"
            " '9000.0000', '{\"v\": 2}'",
            {"t": tenant_id},
        )
        conn.commit()

    _merge(db, tenant)

    with super_engine.connect() as conn:
        wrappers = dict(
            conn.execute(
                text(
                    "SELECT a.name, CAST(p.product_type AS text)"
                    "  FROM platform.accounts a"
                    "  LEFT JOIN platform.products p ON p.id = a.product_id"
                    " WHERE a.tenant_id = :t"
                ),
                {"t": tenant_id},
            ).all()
        )

    assert wrappers["SIPP"] == "SIPP", (
        "a Seccl PENSION account must resolve the SIPP product; a NULL here is the defect that "
        "made drawdown answer 'pension accounts only' for every real SIPP in the book"
    )
    assert wrappers["ISA"] == "ISA", "and the unaliased wrappers are unaffected"


def test_a_portfolio_reaches_the_book_through_its_account(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`platform.portfolios` (0119) — the second half of the address every SEI money instruction
    takes.

    `portfolioId` is required by `createCurrencyReceipt`, `createcashdisbursement`,
    `currencytransfercreate` and every `PSM/model/assignment/*`, and until this table there was
    nowhere to put one: a `platform.links` row permits exactly one per (provider, entity, type),
    and 59 of 1,104 accounts in the SEI drop carry a Capital AND an Income portfolio.

    The account parent is REQUIRED, so a portfolio whose account did not materialise is a counted
    reject rather than a row nothing can address.
    """
    uid, tenant_id = tenant["uid"], tenant["id"]
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:

        def insert(entity: str, columns: str, values: str, params: dict[str, Any]) -> None:
            conn.execute(
                text(
                    f"INSERT INTO {book_sink.landing_table(uid, entity, PROVIDER)} ({columns})"  # noqa: S608
                    f" VALUES ({values})"
                ),
                params,
            )

        # The Capital/Income pair on the account the fixture already staged, plus one orphan whose
        # account was never staged.
        for ext, name, kind, principal, linked, account in (
            ("P1", "Portfolio-01 (Capital)", "capital", "true", None, "A100"),
            ("P2", "Portfolio-01 (Income)", "income", "false", "P1", "A100"),
            ("P9", "Orphan", "capital", "true", None, "A-NEVER-STAGED"),
        ):
            insert(
                "portfolios",
                "tenant_id, provider, external_id, account_external_id, name, portfolio_type,"
                " is_principal, linked_external_id, data",
                ":t, 'seccl', :e, :a, :n, :k, :p, :l, '{\"p\": 1}'",
                {
                    "t": tenant_id,
                    "e": ext,
                    "a": account,
                    "n": name,
                    "k": kind,
                    "p": principal,
                    "l": linked,
                },
            )
        conn.commit()

    ledger = _merge(db, tenant, entities="sei")

    assert ledger["portfolios"]["created"] == 2, "the pair on the staged account"
    assert ledger["portfolios"]["rejected"] == 1, "the orphan, counted rather than written"

    with super_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.external_id, p.portfolio_type, p.is_principal, p.linked_external_id,"
                "       a.uid IS NOT NULL AS has_account"
                "  FROM platform.portfolios p"
                "  JOIN platform.accounts a ON a.id = p.account_id"
                " WHERE p.tenant_id = :t ORDER BY p.external_id"
            ),
            {"t": tenant_id},
        ).all()
        rejects = (
            conn.execute(
                text(
                    "SELECT row_key FROM platform.merge_rejects"
                    " WHERE tenant_id = :t AND entity = 'portfolios'"
                ),
                {"t": tenant_id},
            )
            .scalars()
            .all()
        )

    assert rows == [
        ("P1", "capital", True, None, True),
        ("P2", "income", False, "P1", True),
    ], "both resolved a real account, and the Income one names its Capital sibling"
    assert rejects == ["P9"], "and the ledger names the portfolio, not just a count"


def test_a_custodians_staff_attach_to_a_login_or_arrive_disabled(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`auth.users` through `auth.merge_provider_user` (0123) — the whole chain, on real Postgres.

    This is the one entity that writes IDENTITY, and 0118 argued a custodian's own people should
    NOT be rows in this table because `UNIQUE (tenant_id, email)` makes it unsatisfiable "for
    exactly the people the mapping exists for". That objection is answered by making the collision
    the SIGNAL: a matching email means attach rather than insert.

    So the four outcomes are pinned together, because the risk is that one of them silently
    becomes another — an attach that overwrote a real login's name, or an insert that took a
    portal, would each be invisible to a test of the happy path alone.
    """
    uid, tenant_id = tenant["uid"], tenant["id"]
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:
        # A REAL staff login, provisioned through WorkOS, with the firm's own words on it (0118).
        # Neither the name nor the profile may be touched by an attach.
        conn.execute(
            text(
                "INSERT INTO auth.users"
                " (tenant_id, email, first_name, last_name, workos_user_id, enabled, job_title)"
                " VALUES (:t, 'a.adviser@proof.test', 'Ann', 'Adviser', :w, true,"
                "         'Chartered Financial Planner')"
            ),
            {"t": tenant_id, "w": f"user_{uid}"},
        )

        def insert(entity: str, columns: str, values: str, params: dict[str, Any]) -> None:
            conn.execute(
                text(
                    f"INSERT INTO {book_sink.landing_table(uid, entity, 'sei')} ({columns})"  # noqa: S608
                    f" VALUES ({values})"
                ),
                params,
            )

        for ext, email, first, last in (
            # ATTACH — a different CASE from the login's own address, because
            # `users_tenant_email_key` is case-sensitive and a custodian's spelling need not match.
            ("57817496", "A.Adviser@proof.test", "Ann", "Adviser"),
            # INSERT — nobody holds this address.
            ("57817497", "n.new@proof.test", "Nick", "New"),
            # SKIPPED — the drop carries the population and the API carries the email, so a user
            # the API has not seen has none. Never invented: a plausible fabrication in
            # `auth.users.email` is one careless invite away from being treated as real.
            ("57817498", None, "Ned", "NoMail"),
        ):
            insert(
                "users",
                "tenant_id, provider, external_id, email, first_name, last_name, phone",
                ":t, 'sei', :e, :m, :f, :l, '+447700900001'",
                {"t": tenant_id, "e": ext, "m": email, "f": first, "l": last},
            )
        conn.commit()

    ledger = _merge(db, tenant, provider="sei", entities="sei")

    assert ledger["users"]["created"] == 1, "the one nobody held an address for"
    assert ledger["users"]["updated"] == 1, "the attach, which changes a row rather than adding one"
    # NOTHING IS REJECTED, AND NOTHING IS FILED. `Entity.from_sql` filters `email IS NOT NULL`
    # when the staging model delivers a tenant's slice, so in production an employee the custodian
    # holds no address for never reaches the merge; this test inserts straight into the landing
    # table (`_source_sql` builds its own FROM and ignores `from_sql`), so the row DOES arrive
    # here — and is simply skipped, because a row with no address can neither attach nor insert
    # and there is nothing about that for anyone to decide.
    assert ledger["users"]["rejected"] == 0, "the ledger counts the sink's checks, and it has none"

    with super_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT email, enabled, first_name, last_name, job_title,"
                "       provider_refs ->> 'sei' AS sei_id,"
                "       workos_user_id IS NULL AS is_placeholder"
                "  FROM auth.users WHERE tenant_id = :t ORDER BY email"
            ),
            {"t": tenant_id},
        ).all()
        portals = conn.execute(
            text(
                "SELECT u.email, m.portal, m.status"
                "  FROM auth.organisation_memberships m"
                "  JOIN auth.users u ON u.id = m.user_id"
                " WHERE m.tenant_id = :t ORDER BY u.email"
            ),
            {"t": tenant_id},
        ).all()
        rejects = conn.execute(
            text(
                "SELECT row_key, reason FROM platform.merge_rejects"
                " WHERE tenant_id = :t AND entity = 'users'"
            ),
            {"t": tenant_id},
        ).all()

    by_email = {row[0]: row for row in rows}

    # THE ATTACH IS NON-DESTRUCTIVE. It gains the custodian's id and keeps everything else: the
    # provider owns `provider_refs` on a real login and nothing whatever besides.
    attached = by_email["a.adviser@proof.test"]
    assert attached[5] == "57817496", "the SEI id reached the login that already held the email"
    assert attached[1] is True, "and the real login is still enabled"
    assert attached[2:5] == ("Ann", "Adviser", "Chartered Financial Planner"), (
        "WorkOS owns the names and the firm owns the profile — an attach must overwrite neither"
    )
    assert attached[6] is False, "it is a real login, not a placeholder"

    # THE INSERT IS VISIBLE BUT POWERLESS: disabled, and its membership is `inactive`, which
    # `auth_users_scoped_read` accepts (it does not read status) while `auth.resolve_claims`
    # refuses (it requires 'active'). Those two policies are the whole safety property.
    minted = by_email["n.new@proof.test"]
    assert minted[5] == "57817497"
    assert minted[1] is False, "a synced user cannot sign in"
    assert minted[6] is True, "and has no WorkOS identity to sign in WITH"
    assert ("n.new@proof.test", "platform", "inactive") in portals, (
        "the membership that makes the row readable to its own firm without granting a portal"
    )
    assert not [p for p in portals if p[2] == "active"], "nothing here grants access"

    assert "ned.nomail@proof.test" not in by_email
    assert rejects == [], (
        "this function files NO reject ledger at all — the last reason went when 'already mapped "
        "to a different id' became a skip, and 'no email' is filtered by the staging projection "
        "and derivable from `tenant_{uid}.users WHERE email IS NULL`"
    )

    # THE ATTACH IS CASE-INSENSITIVE, and the staged address above is deliberately cased
    # differently from the login's (`A.Adviser@` against `a.adviser@`). Before 0124 that was the
    # reason the merge carried an ambiguity guard: the unique key was case-sensitive, so one
    # tenant could hold both spellings as two logins. `users_tenant_email_ci_key` forbids that
    # state, so a single match is now a database guarantee rather than a counted check.
    with super_engine.connect() as conn:
        folded = (
            conn.execute(
                text(
                    "SELECT count(*) FROM auth.users"
                    " WHERE tenant_id = :t AND lower(email) = 'a.adviser@proof.test'"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
    assert folded == 1, "one address is one login whatever its case"

    # A minted row is STAFF (0124) — the firm's own person at its custodian — and never `adviser`,
    # which the API cannot tell us and an operator has to state.
    with super_engine.connect() as conn:
        natures = dict(
            conn.execute(
                text("SELECT email, type FROM auth.users WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).all()
        )
    assert natures["n.new@proof.test"] == ["staff"]
    assert natures["a.adviser@proof.test"] == [], (
        "and an ATTACH states nothing about a login's nature — that is the firm's to say"
    )

    # IDEMPOTENT. `platform.links` carries `audit.record` triggers and the pipeline has no actor,
    # so a re-run that rewrote rows would file audit entries nobody performed.
    again = _merge(db, tenant, provider="sei", entities="sei")
    assert (again["users"]["created"], again["users"]["updated"]) == (0, 0)


def test_a_staff_row_naming_a_second_custodian_id_is_skipped_not_overwritten(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """One person carrying two ids at one custodian — the shape two employee records sharing an
    address makes. The run declines to have an opinion: it does not attach, does not insert, and
    above all does not OVERWRITE.

    That last is the property worth a test. `provider_refs['sei']` is what an instruction sends as
    `employeeServicingRelationshipList[].employeeEntityId`, and whatever is already there is either
    an operator's own mapping or an earlier run's — a nightly job is not the thing that should
    change its mind about which.

    It files NO reject either. `auth.merge_provider_user` writes `platform.merge_rejects` not at
    all, which makes it the one merge in the estate that files nothing: every case it skips is a
    statement about the current rows with both sides readable — `platform.users` publishes the
    custodian's whole employee population in the firm's slice, `provider_refs` publishes what is
    mapped — so a stored copy would only go stale nightly, the reason
    `platform.reconciliation_breaks` was never built.
    """
    uid, tenant_id = tenant["uid"], tenant["id"]
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO auth.users (tenant_id, email, first_name, workos_user_id, enabled)"
                " VALUES (:t, 'held@proof.test', 'Hal', :w, true)"
            ),
            {"t": tenant_id, "w": f"w{uid}"},
        )
        # Mapped by hand, to a DIFFERENT SEI employee.
        conn.execute(
            text(
                'UPDATE auth.users SET provider_refs = \'{"sei": "11110000"}\''
                " WHERE tenant_id = :t AND email = 'held@proof.test'"
            ),
            {"t": tenant_id},
        )
        conn.execute(
            text(
                f"INSERT INTO {book_sink.landing_table(uid, 'users', 'sei')}"  # noqa: S608
                " (tenant_id, provider, external_id, email, first_name, last_name, phone)"
                " VALUES (:t, 'sei', '57817500', 'held@proof.test', 'X', 'Y', NULL)"
            ),
            {"t": tenant_id},
        )
        conn.commit()

    ledger = _merge(db, tenant, provider="sei", entities="sei")

    assert (ledger["users"]["created"], ledger["users"]["updated"]) == (0, 0), (
        "it may not be resolved by guessing, and may not be inserted either — the email key "
        "would refuse that and fail the whole entity"
    )
    assert ledger["users"]["rejected"] == 0

    with super_engine.connect() as conn:
        held = (
            conn.execute(
                text(
                    "SELECT provider_refs ->> 'sei' FROM auth.users"
                    " WHERE tenant_id = :t AND email = 'held@proof.test'"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
        # Scoped to `entity = 'users'`: `_stage` deliberately stages rows that OTHER entities
        # reject (an orphan transaction, an unmapped type), so the fixture's ledger is not empty.
        rejects = conn.execute(
            text(
                "SELECT count(*) FROM platform.merge_rejects"
                " WHERE tenant_id = :t AND entity = 'users'"
            ),
            {"t": tenant_id},
        ).scalar_one()

    assert held == "11110000", "an operator's own mapping survives a nightly run"
    assert rejects == 0, "and nothing is filed about it — the state is derivable from both sides"


def test_a_sei_model_discovered_from_its_accounts_reaches_the_book(
    db: dict[str, str], super_engine: Engine, tenant: dict[str, Any]
) -> None:
    """`platform.models` for SEI — the catalogue `model_key` had nothing to resolve against.

    SEI's own two model reports are declared and 0 rows on all 74 drops, so the catalogue is
    DISCOVERED from the accounts that name a model — 49 of them across the p04 corpus, 85% of
    account rows. Until this, every one of those accounts carried `model_id` NULL in the book: the
    same defect as the 311-of-376 Seccl accounts whose model never resolved.

    The two halves worth proving together are that an UNNAMED model still lands (26 of the 49 have
    no name anywhere in the drop) and that a model with NO ALLOCATIONS is an OBSERVATION rather
    than a reject — the model exists and accounts name it; there is simply no version to show.
    """
    uid, tenant_id = tenant["uid"], tenant["id"]
    _stage(super_engine, tenant)
    with super_engine.connect() as conn:

        def insert(entity: str, columns: str, values: str, params: dict[str, Any]) -> None:
            conn.execute(
                text(
                    f"INSERT INTO {book_sink.landing_table(uid, entity, 'sei')} ({columns})"  # noqa: S608
                    f" VALUES ({values})"
                ),
                params,
            )

        # STAGED AS THE PROJECTION PRODUCES IT. `_MODELS` coalesces an unnamed model to its own id
        # ("a model the provider named nothing is still a model") and this test writes straight
        # into the landing table, bypassing that — so the unnamed one is staged as `87984`, and
        # the coalesce itself is proven where it runs, over rows, in
        # `pkgs/book-sink/tests/test_entities.py`. Writing NULL here would have tested the
        # projection from the wrong side and failed the entity on a not-null violation.
        for ext, name in (("87955", "AIM Withdrawal"), ("87984", "87984")):
            insert(
                "models",
                "tenant_id, external_id, name, model_type, aim, risk_rating, model_charge,"
                " vatable, suspend_rebalances, cash_allocation, status, products,"
                " unmodelled_wrappers, allocation_count, data",
                ":t, :e, :n, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'active', '{}', '', '0',"
                " '{\"m\": 1}'",
                {"t": tenant_id, "e": ext, "n": name},
            )
        conn.commit()

    ledger = _merge(db, tenant, provider="sei", entities="sei")

    # TWO. `_stage` writes 'M1' into the Seccl landing schema. That used to be the same table,
    # so this merge created it a third time under SEI — which is the duplication the schema
    # split exists to stop. These two are the only models SEI staged.
    assert ledger["models"]["created"] == 2
    assert ledger["models"]["rejected"] == 0, (
        "a model with no published allocation is not an error — it is counted and written"
    )
    assert ledger["models"]["reasons"].get("no allocations published") == 2, (
        "both staged models publish no allocation, and that is an observation rather than a "
        "reject — how we would find out if SEI ever started publishing allocations."
    )

    with super_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT l.external_id, m.name, m.status"
                "  FROM platform.models m"
                "  JOIN platform.links l"
                "    ON l.entity_id = m.id AND l.entity_type = 'model' AND l.provider = 'sei'"
                " WHERE m.tenant_id = :t AND l.external_id IN ('87955', '87984')"
                " ORDER BY l.external_id"
            ),
            {"t": tenant_id},
        ).all()
        versions = conn.execute(
            text("SELECT count(*) FROM platform.model_versions WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar_one()

    assert rows == [
        ("87955", "AIM Withdrawal", "active"),
        # The unnamed one carries its id, which is what lets a firm rename it — and the nightly
        # run never overwrites that (materialise.PRODUCT_LABELS' rule).
        ("87984", "87984", "active"),
    ]
    # A version is a decision about a set of holdings. SEI published none, so there is nothing
    # to decide. 'M1' stays in the Seccl landing schema and is not part of this merge.
    assert versions == 0, "neither SEI model published holdings, so neither mints a version"
