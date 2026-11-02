"""How the Intelliflo sync finds an account by the lake's account number.

`services/integrations/intelliflo` resolves a lake account number (SEI's ``ACCOUNT_NUMBER``) to the
account's Intelliflo link, to get the plan and policy number a valuation or transaction goes to.
Its own tests stub this DB layer out, so the SQL was never run against real rows. These run the
actual functions against the migrated schema, and pin three rules:

* The number is the ``external_id`` of the account's SEI ``platform.links`` row, and nothing else:
  ``platform.accounts.platform_identifier`` is never consulted (the book-sink merge does not write
  it for a synced account, and a second source for the number is a source of drift).
* The lookup is scoped to ONE portals tenant. SEI account numbers are not unique across tenants
  (graphene and chester-rose both hold firm 3106's), so a lookup by number alone could resolve
  another tenant's Intelliflo plan and post this tenant's valuations or transactions into it.
* No tenant means no accounts, never all of them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

# The Intelliflo service and its psycopg2 driver are not dependencies of the API project, and CI
# installs only the API's: importing them unguarded fails at COLLECTION, which takes down the
# whole API test job and `contract-drift` (it collects every module). A full workspace install
# has both, so this runs wherever the repo is fully installed and skips where it is not.
psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 is not installed in this environment")
iflo_db = pytest.importorskip(
    "intelliflo_sync.providers.intelliflo.db", reason="the Intelliflo service is not installed"
)

pytestmark = pytest.mark.usefixtures("clear_io_plan_links")


@pytest.fixture(autouse=True)
def _intelliflo_on_the_test_db(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point the service's own connection factory at the test container. Each call opens a fresh
    connection because every function under test closes the one it is given."""
    dsn = db["super"].replace("postgresql+psycopg://", "postgresql://")
    monkeypatch.setattr(iflo_db, "_get_pg_connection", lambda: psycopg2.connect(dsn))
    yield


class _Book:
    """One tenant, with helpers to lay down accounts and their links."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        s = uuid.uuid4().hex[:8]
        self.s = s
        with engine.connect() as c:
            self.tenant_id = int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"ianten_{s}", "a": f"ian-{s}", "n": f"IAN Tenant {s}"},
                ).scalar_one()
            )

    def number(self, label: str) -> str:
        """A lake account number, unique to this test so nothing else in the shared DB matches."""
        return f"MSP{label}{self.s}"

    def contact(self, *, io_client_id: str | None = None) -> int:
        with self.engine.connect() as c:
            contact_id = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts "
                        "  (tenant_id, uid, type, first_name, surname, service_type) "
                        "VALUES (:t, :uid, 'individual', 'Ada', 'Lake', 'advised') RETURNING id"
                    ),
                    {"t": self.tenant_id, "uid": f"iacon_{uuid.uuid4().hex[:8]}"},
                ).scalar_one()
            )
        if io_client_id:
            self.link("contact", contact_id, "intelliflo", io_client_id)
        return contact_id

    def account(self, contact_id: int, *, platform_identifier: str | None = None) -> int:
        with self.engine.connect() as c:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, contact_id, uid, status, value, name, platform_identifier) "
                        "VALUES (:t, :con, :uid, CAST('active' AS platform.account_status), 0, "
                        "        'ISA', :pid) RETURNING id"
                    ),
                    {
                        "t": self.tenant_id,
                        "con": contact_id,
                        "uid": f"iaacc_{uuid.uuid4().hex[:8]}",
                        "pid": platform_identifier,
                    },
                ).scalar_one()
            )

    def link(
        self,
        entity_type: str,
        entity_id: int,
        provider: str,
        external_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self.engine.connect() as c:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
                    "VALUES (:t, :et, :eid, CAST(:p AS platform.provider_code), 'primary', "
                    "        :ext, CAST(:data AS jsonb))"
                ),
                {
                    "t": self.tenant_id,
                    "et": entity_type,
                    "eid": entity_id,
                    "p": provider,
                    "ext": external_id,
                    "data": json.dumps(data) if data is not None else None,
                },
            )

    def io_plan(self, account_id: int, *, policy: str, plan: str) -> None:
        self.link("account", account_id, "intelliflo", policy, {"id": plan, "policyNumber": policy})


@pytest.fixture
def book(super_engine: Engine) -> _Book:
    return _Book(super_engine)


@pytest.fixture
def other_book(super_engine: Engine) -> _Book:
    """A second tenant, for the cases about one tenant reaching another's data."""
    return _Book(super_engine)


def test_a_synced_account_is_found_by_its_custodian_link_number(book: _Book) -> None:
    number = book.number("SYN")
    account = book.account(book.contact(io_client_id="IOC-1"))
    book.link("account", account, "sei", number)
    book.io_plan(account, policy="POL-SYN", plan="PLAN-SYN")

    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, [number]) == {
        number: ["POL-SYN"]
    }
    assert iflo_db.get_plan_links_for_accounts(book.tenant_id, [number]) == {
        number: [("PLAN-SYN", "POL-SYN")]
    }
    assert iflo_db.get_transaction_targets(book.tenant_id, [number]) == {
        number: [("IOC-1", "PLAN-SYN", "POL-SYN")]
    }


def test_platform_identifier_is_never_consulted(book: _Book) -> None:
    """An account whose number is ONLY in `platform_identifier` does not resolve. The number is the
    custodian link's external_id; a second place to look is a second source of truth."""
    number = book.number("PID")
    account = book.account(book.contact(io_client_id="IOC-2"), platform_identifier=number)
    book.io_plan(account, policy="POL-PID", plan="PLAN-PID")  # no SEI link

    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, [number]) == {}
    assert iflo_db.get_plan_links_for_accounts(book.tenant_id, [number]) == {}
    assert iflo_db.get_transaction_targets(book.tenant_id, [number]) == {}


def test_a_shared_number_never_resolves_another_tenants_plan(
    book: _Book, other_book: _Book
) -> None:
    """Two tenants hold links to the SAME SEI account number (as graphene and chester-rose do for
    firm 3106), each with its own Intelliflo plan. Each tenant's lookup sees only its own."""
    shared = book.number("SHARED")
    mine = book.account(book.contact(io_client_id="IOC-MINE"))
    book.link("account", mine, "sei", shared)
    book.io_plan(mine, policy="POL-MINE", plan="PLAN-MINE")
    theirs = other_book.account(other_book.contact(io_client_id="IOC-THEIRS"))
    other_book.link("account", theirs, "sei", shared)
    other_book.io_plan(theirs, policy="POL-THEIRS", plan="PLAN-THEIRS")

    for fn, want_mine, want_theirs in (
        (
            iflo_db.get_policy_numbers_for_accounts,
            {shared: ["POL-MINE"]},
            {shared: ["POL-THEIRS"]},
        ),
        (
            iflo_db.get_plan_links_for_accounts,
            {shared: [("PLAN-MINE", "POL-MINE")]},
            {shared: [("PLAN-THEIRS", "POL-THEIRS")]},
        ),
        (
            iflo_db.get_transaction_targets,
            {shared: [("IOC-MINE", "PLAN-MINE", "POL-MINE")]},
            {shared: [("IOC-THEIRS", "PLAN-THEIRS", "POL-THEIRS")]},
        ),
    ):
        assert fn(book.tenant_id, [shared]) == want_mine
        assert fn(other_book.tenant_id, [shared]) == want_theirs


def test_a_tenant_with_no_link_of_its_own_resolves_nothing(book: _Book, other_book: _Book) -> None:
    """Another tenant having linked the number is not a reason this one can use it."""
    number = book.number("ONLY")
    account = book.account(book.contact(io_client_id="IOC-3"))
    book.link("account", account, "sei", number)
    book.io_plan(account, policy="POL-ONLY", plan="PLAN-ONLY")

    assert iflo_db.get_policy_numbers_for_accounts(other_book.tenant_id, [number]) == {}
    assert iflo_db.get_transaction_targets(other_book.tenant_id, [number]) == {}


@pytest.mark.parametrize("no_tenant", [None, "", "  ", "not-a-tenant"])
def test_no_tenant_means_no_accounts_not_all_of_them(book: _Book, no_tenant: object) -> None:
    number = book.number("NOTEN")
    account = book.account(book.contact(io_client_id="IOC-4"))
    book.link("account", account, "sei", number)
    book.io_plan(account, policy="POL-NOTEN", plan="PLAN-NOTEN")

    assert iflo_db.get_policy_numbers_for_accounts(no_tenant, [number]) == {}
    assert iflo_db.get_plan_links_for_accounts(no_tenant, [number]) == {}
    assert iflo_db.get_transaction_targets(no_tenant, [number]) == {}


def test_only_the_sei_link_counts_as_the_lake_number(book: _Book) -> None:
    """Another custodian's account that happens to carry the same string is not this account."""
    number = book.number("OTHER")
    account = book.account(book.contact())
    book.link("account", account, "seccl", number)
    book.io_plan(account, policy="POL-OTHER", plan="PLAN-OTHER")

    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, [number]) == {}
    assert iflo_db.get_plan_links_for_accounts(book.tenant_id, [number]) == {}


def test_an_account_not_linked_to_intelliflo_is_absent(book: _Book) -> None:
    number = book.number("NOIO")
    account = book.account(book.contact(io_client_id="IOC-5"))
    book.link("account", account, "sei", number)

    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, [number]) == {}
    assert iflo_db.get_transaction_targets(book.tenant_id, [number]) == {}


def test_a_transaction_target_needs_the_contacts_intelliflo_client(book: _Book) -> None:
    """The plan alone is not enough for a transaction: the client id comes from the contact."""
    number = book.number("NOCLI")
    account = book.account(book.contact())  # the contact has no Intelliflo link
    book.link("account", account, "sei", number)
    book.io_plan(account, policy="POL-NOCLI", plan="PLAN-NOCLI")

    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, [number]) == {
        number: ["POL-NOCLI"]
    }
    assert iflo_db.get_transaction_targets(book.tenant_id, [number]) == {}


def test_an_empty_request_does_not_touch_the_database(book: _Book) -> None:
    assert iflo_db.get_policy_numbers_for_accounts(book.tenant_id, []) == {}
    assert iflo_db.get_plan_links_for_accounts(book.tenant_id, []) == {}
    assert iflo_db.get_transaction_targets(book.tenant_id, []) == {}


_SEED = (
    Path(__file__).resolve().parents[3]
    / "services/integrations/intelliflo/src/intelliflo_sync/migrations/intelliflo/seeds"
    / "003_accounts_links.sql"
)


def test_the_local_seed_creates_accounts_the_lookup_resolves(
    book: _Book, db: dict[str, str]
) -> None:
    """`intelliflo-seed` (local only) gives a developer accounts with Intelliflo links. They must be
    shaped like synced ones, the number on an SEI link, or the sync could not find them. Run twice:
    a seed has to be repeatable."""
    number = book.number("SEED")
    params = {
        "graphene_tenant_id": book.tenant_id,
        "accounts": json.dumps(
            [{"account_number": number, "policy_number": "POL-SEED", "plan_id": "PLAN-SEED"}]
        ),
    }
    dsn = db["super"].replace("postgresql+psycopg://", "postgresql://")
    for _ in range(2):
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(_SEED.read_text(encoding="utf-8"), params)
            conn.commit()
        finally:
            conn.close()

    assert iflo_db.get_plan_links_for_accounts(book.tenant_id, [number]) == {
        number: [("PLAN-SEED", "POL-SEED")]
    }
    with book.engine.connect() as c:
        accounts = c.execute(
            text(
                "SELECT count(*) FROM platform.accounts "
                "WHERE tenant_id = :t AND data ->> 'account_number' = :n"
            ),
            {"t": book.tenant_id, "n": number},
        ).scalar_one()
    assert accounts == 1  # the second run created nothing new
