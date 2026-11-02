"""`platform.users` — executed in DuckDB, over rows.

This relation feeds `auth.merge_provider_user` (0123), which either ATTACHES a SEI employee id to
the Graphene login that already holds the same email or INSERTS a disabled placeholder. Both
outcomes act on `auth.users`, so a wrong row here reaches identity rather than a report.

Three failures are worth executing for, and none of them is visible to a model that only binds:

1. **THE SCOPE.** `UserDetail` is 1,591 rows on the 2026-03-27 drop, and only 706 are staff:
   `USER_TYPE` 1 Employee / 2 End Client (865) / 3 Data Integration (20). Publishing type 2
   would put investors in the firm's staff list and let the attach rule bind a custodian's
   staff id onto a client's own portal login. An empty stub table binds perfectly and can never
   see a `WHERE` clause that does not fire.
2. **THE GRAIN.** The report is a DAILY SNAPSHOT and `raw.user_detail` merges every drop, so
   without a recency window the relation holds one row per user per day — 74 rows per person
   over this corpus — and `unique: external_id` fails the build.
3. **THE NAME SPLIT.** The drop has one `USER_DISPLAY_NAME` field holding `"Armasoiu, Maria"`.
   Parsing it is arithmetic a parse-only test cannot see, and the interesting cases (no comma,
   a compound surname) are value-dependent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

#: Exactly the twelve columns SEI delivers, in the report's own order, plus interlace's `dt`.
#: Verified against a decrypted p04 drop rather than transcribed from a spec.
USER_DETAIL = """
    dt date,
    "ENTITY_ID" varchar, "USER_DISPLAY_NAME" varchar, "LOGIN_USER_ID" varchar,
    "USER_CREATION_DATE" date, "ECX_ACCESS_FLAG" boolean, "FIRM_ID" varchar,
    "LAST_LOGIN_TIME" timestamptz, "USER_STATUS" boolean, "MONEY_MOVEMENT_LIMIT" varchar,
    "SSO_KEY" varchar, "USER_TYPE" varchar, "LOGIN_PHONE" varchar
"""

EMPLOYEE_USERS = """
    entity_id varchar, firm_id varchar, email varchar, first_name varchar,
    middle_name varchar, last_name varchar, login_name varchar, phone varchar,
    crd_number varchar, state varchar, as_of_date date, fetched_at timestamptz
"""

#: SEI's `USER_TYPE` codes, from the Reference report's own set.
EMPLOYEE = "1"
END_CLIENT = "2"
DATA_INTEGRATION = "3"


def _model_sql(name: str) -> str:
    sql = (MODELS / f"{name}.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "ref", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"CREATE TABLE raw.user_detail ({USER_DETAIL})")
    connection.execute(f"CREATE TABLE ref.employee_users ({EMPLOYEE_USERS})")
    return connection


def _user(
    connection: duckdb.DuckDBPyConnection,
    *,
    entity_id: str = "57817496",
    display_name: str = "Armasoiu, Maria",
    login: str = "marmasoiu",
    firm: str = "3102",
    user_type: str = EMPLOYEE,
    active: bool = True,
    phone: str | None = "+4407770801742",
    day: str = "2026-03-27",
) -> None:
    connection.execute(
        "INSERT INTO raw.user_detail VALUES (?, ?, ?, ?, DATE '2024-07-02', false, ?,"
        " NULL, ?, NULL, NULL, ?, ?)",
        [day, entity_id, display_name, login, firm, active, user_type, phone],
    )


def _api(
    connection: duckdb.DuckDBPyConnection,
    *,
    entity_id: str = "57817496",
    email: str | None = "maria.armasoiu@firm.test",
    first: str | None = "Maria",
    last: str | None = "Armasoiu",
    middle: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO ref.employee_users VALUES (?, '3102', ?, ?, ?, ?, 'marmasoiu',"
        " '+4407770801742', NULL, 'ACTIVE', DATE '2026-03-27', now())",
        [entity_id, email, first, middle, last],
    )


def _run(connection: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    connection.execute(f"CREATE OR REPLACE VIEW platform.users AS {_model_sql('users')}")
    cursor = connection.execute("SELECT * FROM platform.users ORDER BY external_id")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class TestTheScope:
    """`USER_TYPE = 1` only. The most consequential line in the model."""

    def test_an_employee_is_published(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _user(warehouse)
        rows = _run(warehouse)
        assert [r["external_id"] for r in rows] == ["57817496"]

    def test_an_end_client_is_not_a_member_of_staff(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """865 of the drop's 1,591 rows. Publishing them would put investors in the staff list
        AND let the attach rule bind a staff-shaped custodian id onto a client's portal login."""
        _user(warehouse, entity_id="57900001", user_type=END_CLIENT)
        assert _run(warehouse) == []

    def test_a_service_account_is_not_a_person(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _user(warehouse, entity_id="57900002", user_type=DATA_INTEGRATION)
        assert _run(warehouse) == []

    def test_a_service_login_is_excluded_even_when_the_api_returns_it(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """THE DROP IS THE GATE, and this is why it has to be.

        `GET /employeeservice/firmemployeeusers` carries no `userType` — verified against the
        vendor's own spec, whose `FirmEmployeeUser` is exactly crdNumber / emailId / firmId /
        firstName / lastName / loginUserName / middleName / phone / state / uniqueUserId. So
        nothing in the API response distinguishes an employee from a service login, and the
        endpoint's NAME is a claim we cannot verify from here (SEI allowlists it by source IP).

        A machine account with an ops mailbox is the bad case: it would not merely sit in a staff
        list, it would ATTACH its custodian id to whichever real login shares that address.
        """
        _user(warehouse, entity_id="57900002", user_type=DATA_INTEGRATION)
        _api(warehouse, entity_id="57900002", email="sftp-integration@firm.test")
        assert _run(warehouse) == [], "the API offering it does not make it a person"

    def test_the_three_types_are_separated_in_one_drop(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The real shape: all three types arrive in the same file, so the filter must select
        rather than merely not-crash."""
        _user(warehouse, entity_id="1", user_type=EMPLOYEE)
        _user(warehouse, entity_id="2", user_type=END_CLIENT)
        _user(warehouse, entity_id="3", user_type=DATA_INTEGRATION)
        _user(warehouse, entity_id="4", user_type=EMPLOYEE)
        assert [r["external_id"] for r in _run(warehouse)] == ["1", "4"]

    def test_a_departed_employee_is_still_published(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """470 of the 706 are already inactive at SEI, and dropping them would leave every
        account they advised attributed to nobody. `is_active` says which is which."""
        _user(warehouse, entity_id="57817496", active=False)
        rows = _run(warehouse)
        assert len(rows) == 1
        assert rows[0]["is_active"] is False


class TestTheGrain:
    """A daily snapshot merged across drops is one row per user per DAY."""

    def test_the_latest_drop_wins_and_the_user_appears_once(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _user(warehouse, display_name="Old, Name", day="2026-03-25")
        _user(warehouse, display_name="Armasoiu, Maria", day="2026-03-27")
        _user(warehouse, display_name="Older, Still", day="2026-03-20")
        rows = _run(warehouse)
        assert len(rows) == 1, "the snapshot was not windowed to the latest drop"
        assert rows[0]["display_name"] == "Armasoiu, Maria"
        assert rows[0]["as_of_date"].isoformat() == "2026-03-27"

    def test_the_declared_unique_check_would_hold(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The model declares `unique: external_id`; this is that check, executed."""
        for day in ("2026-03-25", "2026-03-26", "2026-03-27"):
            _user(warehouse, entity_id="1", day=day)
            _user(warehouse, entity_id="2", day=day)
        rows = _run(warehouse)
        assert len({r["external_id"] for r in rows}) == len(rows) == 2


class TestTheName:
    """`USER_DISPLAY_NAME` is "Last, First" in one field; the API splits it properly."""

    def test_the_api_split_wins_when_it_is_there(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _user(warehouse, display_name="Armasoiu, Maria")
        _api(warehouse, first="Maria", last="Armasoiu", middle="Jane")
        row = _run(warehouse)[0]
        assert (row["first_name"], row["middle_name"], row["last_name"]) == (
            "Maria",
            "Jane",
            "Armasoiu",
        )

    def test_the_display_field_is_split_on_the_comma_when_the_api_is_off(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """No API credentials at all: the population still lands, named."""
        _user(warehouse, display_name="Armasoiu, Maria")
        row = _run(warehouse)[0]
        assert row["first_name"] == "Maria"
        assert row["last_name"] == "Armasoiu"

    def test_a_compound_surname_is_not_cut_at_a_space(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _user(warehouse, display_name="de la Rue, Jean Paul")
        row = _run(warehouse)[0]
        assert row["last_name"] == "de la Rue"
        assert row["first_name"] == "Jean Paul"

    def test_a_name_with_no_comma_becomes_the_first_name_whole(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """`auth.users.name` is generated from first || ' ' || last, so putting an unsplittable
        string in `first_name` renders it intact. Guessing a surname at the last space would
        turn a mononym or a compound into two wrong halves."""
        _user(warehouse, display_name="Prince")
        row = _run(warehouse)[0]
        assert row["first_name"] == "Prince"
        assert row["last_name"] is None


class TestTheEmail:
    """The attach key, and the one thing the drop cannot supply."""

    def test_the_api_email_reaches_the_relation(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        _user(warehouse)
        _api(warehouse, email="maria.armasoiu@firm.test")
        assert _run(warehouse)[0]["email"] == "maria.armasoiu@firm.test"

    def test_no_api_leg_means_a_null_email_and_not_a_missing_row(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """A deployment with SFTP credentials and no API ones publishes the whole population with
        every email NULL. `auth.merge_provider_user` counts those as `no email at the provider`
        rather than inventing an address — a plausible fabrication in `auth.users.email` is one
        careless invite away from being treated as real."""
        _user(warehouse)
        row = _run(warehouse)[0]
        assert row["external_id"] == "57817496"
        assert row["email"] is None

    def test_an_api_row_for_someone_not_in_the_drop_adds_nobody(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """The drop is the POPULATION and the API is an enrichment. A LEFT JOIN the other way
        round would publish End Clients and service accounts, which the API does not label."""
        _user(warehouse, entity_id="57817496")
        _api(warehouse, entity_id="99999999", email="ghost@firm.test")
        rows = _run(warehouse)
        assert [r["external_id"] for r in rows] == ["57817496"]
        assert rows[0]["email"] is None


class TestTheTenancy:
    """The firm axis only — `book_sink.predicate` is told the same thing."""

    def test_the_firm_is_published_on_its_own_axis(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        _user(warehouse, firm="3102")
        row = _run(warehouse)[0]
        assert row["firm_id"] == "3102"
        assert row["key"] == "3102"

    def test_the_lineage_names_the_firm_rather_than_being_empty(
        self, warehouse: duckdb.DuckDBPyConnection
    ) -> None:
        """An EMPTY `lineage` means global reference data that projects to EVERY tenant
        (`predicate.conformed`'s rule, which `platform.fee_packages` relies on). For people that
        would show one firm another firm's employees."""
        _user(warehouse, firm="3106")
        assert _run(warehouse)[0]["lineage"] == ["3106"]

    def test_a_user_with_no_firm_is_dropped(self, warehouse: duckdb.DuckDBPyConnection) -> None:
        """`not_null: firm_id` is declared; an unattributable person would otherwise be
        unprojectable and would abort nothing — it would simply belong to no firm."""
        _user(warehouse, entity_id="1", firm=None)  # type: ignore[arg-type]
        assert _run(warehouse) == []
