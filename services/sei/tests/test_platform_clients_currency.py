"""`platform.clients.base_currency` is `CLIENT_BASE_CURRENCY`, not a sterling assumption.

The 2025-10-24 Clients drop is overwhelmingly GBP with a handful of USD rows and a few
blanks. A model that omits the column, or that hard-codes GBP, files a dollar book as
sterling. A blank publishes NULL; the book merge treats that as GBP.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"

CLIENTS = """
    dt VARCHAR,
    "CLIENT_ID" VARCHAR, "CLIENT_TYPE" VARCHAR, "ADDRESS_PURPOSE" VARCHAR,
    "ADDRESS_PRIMARY_FLAG" VARCHAR, "ADDRESS_LAST_UPDATED" VARCHAR, "ADDRESS_ID" VARCHAR,
    "FIRM_ID" VARCHAR, "RESIDING_SUBFIRM_ID" VARCHAR,
    "FIRST_NAME" VARCHAR, "ACCOUNT_HOLDERS_SURNAME" VARCHAR, "TITLE" VARCHAR,
    "DATE_OF_BIRTH" VARCHAR, "NATIONALITY" VARCHAR,
    "COUNTRY_OF_RESIDENCE" VARCHAR, "COUNTRY_OF_DOMICILE" VARCHAR,
    "NATIONAL_INSURANCE_NUMBER" VARCHAR, "COMMENCEMENT_DATE" VARCHAR,
    "CORRESPONDENCE_ADDRESS_LINE1" VARCHAR, "CORRESPONDENCE_ADDRESS_LINE2" VARCHAR,
    "CORRESPONDENCE_ADDRESS_LINE3" VARCHAR, "CORRESPONDENCE_ADDRESS_CITY" VARCHAR,
    "CORRESPONDENCE_ADDRESS_COUNTRY" VARCHAR,
    "STOCK_AND_SHARE_ISA_SUBSCRIPTION_AMOUNT" VARCHAR,
    "TOTAL_REMAINING_ISA_HEADROOM_AMOUNT" VARCHAR,
    "CLIENT_BASE_CURRENCY" VARCHAR,
    "TAX_ID_TYPE" VARCHAR, "TAX_ID_NUMBER" VARCHAR,
    "EMAIL_ADDRESS" VARCHAR, "PHONE_NUMBER" VARCHAR,
    "COMPANY_NAME" VARCHAR, "ORGANIZATION_TYPE" VARCHAR,
    "GENDER" VARCHAR, "MARITAL_STATUS" VARCHAR, "VULNERABLE_FLG" VARCHAR,
    "COUNTRY_OF_BIRTH" VARCHAR, "CERTIFIED_FATCA_COMPLIANCE" VARCHAR,
    "FIRST_PASSPORT_NUMBER" VARCHAR, "COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT" VARCHAR,
    "EXTERNAL_PARTY_ID" VARCHAR,
    "ISA_CONTRIBUTION_TO_DATE" VARCHAR
"""

CONTACT_DETAILS = """
    dt VARCHAR, "ENTITY_IDENTIFIER" VARCHAR, "EMAIL_ADDRESS" VARCHAR,
    "PRIMARY_EMAIL_FLAG" VARCHAR, "PHONE_NUMBER" VARCHAR, "PRIMARY_PHONE_FLAG" VARCHAR,
    "PHONE_NUMBER_TYPE" VARCHAR,
    "PRIMARY_ADDRESS_FLAG" VARCHAR,
    "ADDRESS_LINE_1" VARCHAR, "ADDRESS_LINE_2" VARCHAR, "ADDRESS_LINE_3" VARCHAR,
    "ADDRESS_LINE_4" VARCHAR, "ADDRESS_LINE_5" VARCHAR,
    "ADDRESS_CITY" VARCHAR, "ADDRESS_POLITICAL_SUBDIVISION" VARCHAR,
    "ADDRESS_POSTAL_CODE" VARCHAR, "ADDRESS_COUNTRY_CODE" VARCHAR
"""

GRANTS = """
    subject_id VARCHAR, dsr_id VARCHAR, identifies_firm BOOLEAN,
    firm_id VARCHAR, subfirm_id VARCHAR, scope VARCHAR, grantee VARCHAR
"""


def _model_sql() -> str:
    sql = (MODELS / "clients.sql").read_text()
    return re.sub(r"/\*.*?\*/", "", sql, count=1, flags=re.S).strip()


def _title_codes(connection: duckdb.DuckDBPyConnection) -> None:
    """The TITLE dimension the model joins. Empty is a drop whose reference has not arrived."""
    connection.execute("CREATE SCHEMA ref")
    connection.execute("CREATE TABLE ref.codes (type VARCHAR, code VARCHAR, description VARCHAR)")


def test_the_clients_model_names_the_source_column() -> None:
    """The extract is the quoted report column. A renamed alias with no source read would
    still publish `base_currency` and still be wrong."""
    assert 'c."CLIENT_BASE_CURRENCY"' in _model_sql()


def test_base_currency_follows_the_client_row() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    # The subfirm list is read from here. Empty is a client the report gave no account for.
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    # Column order matches CLIENTS (26). Only identity, firm and currency vary.
    rows = []
    for client_id, currency in (("C-GBP", "GBP"), ("C-USD", "USD"), ("C-BLANK", "  ")):
        row = ["2025-10-24", client_id, "Individual", *([None] * 38)]
        row[7] = "3100"  # FIRM_ID — a client with no firm fans out to nobody
        row[8] = "3100"
        row[25] = currency  # CLIENT_BASE_CURRENCY
        rows.append(row)
    connection.executemany(
        f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
        rows,
    )
    _title_codes(connection)
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT external_id, base_currency FROM platform.clients ORDER BY external_id"
        ).fetchall()
    }
    assert got == {"C-GBP": "GBP", "C-USD": "USD", "C-BLANK": None}


def test_a_clients_subfirms_are_the_accounts_it_holds() -> None:
    """RESIDING_SUBFIRM_ID is blank on the whole drop. Filtering that column matches nobody.

    The subfirm is the account's, and it is a list: one person can hold accounts at two.
    """
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    connection.execute(
        "INSERT INTO platform.tenancy VALUES (['C1'], '704254'), (['C1'], '704766'),"
        " (['C2'], '704254')"
    )
    for client_id in ("C1", "C2", "C3"):
        row = ["2025-10-24", client_id, "Individual", *([None] * 38)]
        row[7] = "3106"
        rows_ok = row
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            rows_ok,
        )
    _title_codes(connection)
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT external_id, subfirm_ids FROM platform.clients ORDER BY external_id"
        ).fetchall()
    }
    assert got["C1"] == ["704254", "704766"]
    assert got["C2"] == ["704254"]
    assert got["C3"] == [], "a client with no account is in no subfirm"


def test_a_national_insurance_number_is_tax_id_type_7() -> None:
    """NATIONAL_INSURANCE_NUMBER is empty on the drop. Type 7 is the NINO; type 36 is
    'No Tax ID on File' and its number is not one."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    for client_id, tax_type, tax_number, email in (
        ("NINO", "7", "AB123456C", None),
        ("NONE", "36", "0000000", None),
        ("MAIL", "36", "0000000", "ada@example.com"),
    ):
        row = ["2025-10-24", client_id, "Individual", *([None] * 38)]
        row[7] = "3106"
        row[26] = tax_type
        row[27] = tax_number
        row[28] = email
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            row,
        )
    _title_codes(connection)
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            "SELECT external_id, national_identifier, national_identifier_type, email"
            " FROM platform.clients ORDER BY external_id"
        ).fetchall()
    }
    assert got["NINO"] == ("AB123456C", "nino", None)
    assert got["NONE"] == (None, None, None)
    assert got["MAIL"] == (None, None, "ada@example.com")


def test_client_type_codes_become_individual_or_an_organisation_kind() -> None:
    """CLIENT_TYPE 5 is an individual. 6 is an entity, and ORGANIZATION_TYPE names
    the three kinds the book stores. A child is an individual under 18, decided
    from the date of birth, not from a code."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    samples = (
        ("PERSON", "5", None, None, "Ada", "Lovelace"),
        ("WORD", "Individual", None, None, "Grace", "Hopper"),
        ("TRUST", "6", "14", "The Hopper Trust", None, "The Hopper Trust"),
        ("CRT", "6", "10", None, None, "Remainder"),
        ("CHARITY", "6", "2", "The Fund", None, "The Fund"),
        ("COMPANY", "6", "4", "Daniscan Future Limited", None, "Daniscan Future Limited"),
        ("LLC", "6", "11", "An LLC", None, "An LLC"),
        ("OTHER", "6", "9", None, None, "Other"),
        ("PENSION", "6", "7", None, None, "Pension"),
    )
    for client_id, client_type, org_type, company, first, surname in samples:
        row = ["2025-10-24", client_id, client_type, *([None] * 38)]
        row[7] = "3116"
        row[9] = first
        row[10] = surname
        row[30] = company
        row[31] = org_type
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            row,
        )
    _title_codes(connection)
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            "SELECT external_id, client_type, sub_type, entity_name FROM platform.clients"
        ).fetchall()
    }
    assert got["PERSON"] == ("individual", None, None)
    assert got["WORD"] == ("individual", None, None)
    assert got["TRUST"] == ("entity", "trust", "The Hopper Trust")
    assert got["CRT"] == ("entity", "trust", None)
    assert got["CHARITY"] == ("entity", "charity", "The Fund")
    assert got["COMPANY"] == ("entity", "company", "Daniscan Future Limited")
    assert got["LLC"] == ("entity", "company", "An LLC")
    assert got["OTHER"] == ("entity", None, None)
    assert got["PENSION"] == ("entity", None, None)


def test_identity_codes_land_on_the_book_vocabulary() -> None:
    """Gender 0 and marital 0 are unset. Vulnerable 1/2 is yes/no. W-9 sets
    us_person; the subject-to-FATCA flag is not that fact. A party id is kept
    even when it is not the client id."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    samples = (
        # id, gender, marital, vulnerable, birth, fatca, passport, passport country, party
        ("PLAIN", "0", "0", "2", "GB", "6", None, "GB", "100"),
        ("PERSON", "1", "2", "1", "US", "1", "123456789", "US", "200-9"),
        ("WIDOW", "2", "10", "2", "gb", "2", None, None, "300"),
        ("ODD", "9", "3", None, "United Kingdom", "7", None, "GBR", None),
    )
    for client_id, gender, marital, vulnerable, birth, fatca, passport, country, party in samples:
        row = ["2025-10-24", client_id, "5", *([None] * 38)]
        row[7] = "3116"
        row[32] = gender
        row[33] = marital
        row[34] = vulnerable
        row[35] = birth
        row[36] = fatca
        row[37] = passport
        row[38] = country
        row[39] = party
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            row,
        )
    _title_codes(connection)
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT external_id, gender, marital_status, is_vulnerable, country_of_birth,"
            " fatca_status, us_person, passport_number, passport_country, external_party_id"
            " FROM platform.clients"
        ).fetchall()
    }
    assert got["PLAIN"] == (None, None, False, "GB", "not_certified", None, None, "GB", "100")
    assert got["PERSON"] == (
        "male",
        "single",
        True,
        "US",
        "w9",
        True,
        "123456789",
        "US",
        "200-9",
    )
    assert got["WIDOW"] == ("female", "widowed", False, "GB", "w8", False, None, None, "300")
    assert got["ODD"] == (None, None, None, None, None, None, None, None, None)


def test_a_title_code_becomes_the_courtesy_title() -> None:
    """TITLE is a Reference code. 1 is Mr, not the digit; 0 is unset; a code with
    no description is not published as that number."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    _title_codes(connection)
    connection.execute(
        "INSERT INTO ref.codes VALUES "
        "('TITLE', '1', 'Mr.'), ('TITLE', '2', 'Mrs.'), ('TITLE', '12', 'Professor'),"
        " ('TITLE', '13', 'The Reverend'), ('TITLE', '21', 'Miss'),"
        " ('TITLE', '24', 'Captain'), ('TITLE', '5', 'The Honorable'),"
        " ('TITLE', '28', 'H E Shaikh'), ('TITLE', '62', 'Baroness')"
    )
    for client_id, title in (
        ("MR", "1"),
        ("MRS", "2"),
        ("PROF", "12"),
        ("REV", "13"),
        ("MISS", "21"),
        ("CAPT", "24"),
        ("HON", "5"),
        ("SHAIKH", "28"),
        ("BARONESS", "62"),
        ("UNSET", "0"),
        ("UNKNOWN", "99"),
        ("SPELLED", "Mx."),
        ("BLANK", None),
    ):
        row = ["2025-10-24", client_id, "5", *([None] * 38)]
        row[7] = "3116"
        row[11] = title
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            row,
        )
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = dict(connection.execute("SELECT external_id, title FROM platform.clients").fetchall())
    assert got == {
        "MR": "Mr",
        "MRS": "Mrs",
        "PROF": "Prof",
        "REV": "Reverend",
        "MISS": "Miss",
        "CAPT": "Capt",
        "HON": "The Honourable",
        "SHAIKH": "H E Sheikh",
        "BARONESS": "Baroness",
        "UNSET": None,
        "UNKNOWN": None,
        "SPELLED": "Mx",
        "BLANK": None,
    }


def test_the_isa_subscription_is_the_contribution_to_date() -> None:
    """The stocks-and-shares subscription column is zero where a contribution was made.
    Used is ISA_CONTRIBUTION_TO_DATE."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    _title_codes(connection)
    row = ["2025-10-24", "ISA", "5", *([None] * 38)]
    row[7] = "3116"
    row[23] = "0"  # STOCK_AND_SHARE_ISA_SUBSCRIPTION_AMOUNT
    row[40] = "16600"  # ISA_CONTRIBUTION_TO_DATE
    connection.execute(
        f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
        row,
    )
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    used = connection.execute(
        "SELECT isa_subscription_used FROM platform.clients WHERE external_id = 'ISA'"
    ).fetchone()[0]
    assert used == "16600"


def test_a_mobile_and_a_second_number_stay_apart() -> None:
    """ContactDetails can hold several phones. The mobile is the mobile; a second
    number that is not a fax or a pager is `phone`. A landline on its own is the
    mobile and is not copied into `phone`. A fax is not the other number."""
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    connection.execute("CREATE SCHEMA platform")
    connection.execute(f"CREATE TABLE raw.clients ({CLIENTS})")
    connection.execute(f"CREATE TABLE raw.contact_details ({CONTACT_DETAILS})")
    connection.execute(f"CREATE TABLE platform.dsr_grants ({GRANTS})")
    connection.execute("CREATE TABLE platform.tenancy (client_ids VARCHAR[], subfirm_id VARCHAR)")
    _title_codes(connection)
    for client_id in ("BOTH", "LANDLINE", "FAX"):
        row = ["2025-10-24", client_id, "Individual", *([None] * 38)]
        row[7] = "3106"
        connection.execute(
            f"INSERT INTO raw.clients VALUES ({', '.join(['?'] * 41)})",  # noqa: S608
            row,
        )
    connection.execute(
        """
        INSERT INTO raw.contact_details (
            dt, "ENTITY_IDENTIFIER", "PHONE_NUMBER", "PRIMARY_PHONE_FLAG", "PHONE_NUMBER_TYPE"
        ) VALUES
            ('2025-10-24', 'BOTH', '07700900123', 'Y', 'Mobile'),
            ('2025-10-24', 'BOTH', '01179000000', NULL, 'Home'),
            ('2025-10-24', 'LANDLINE', '01179000001', 'Y', 'Home'),
            ('2025-10-24', 'FAX', '01179000002', 'Y', 'Fax'),
            ('2025-10-24', 'FAX', '07700900456', NULL, 'Mobile')
        """
    )
    connection.execute(f"CREATE TABLE platform.clients AS {_model_sql()}")
    got = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT external_id, mobile, phone FROM platform.clients ORDER BY external_id"
        ).fetchall()
    }
    assert got["BOTH"] == ("07700900123", "01179000000")
    assert got["LANDLINE"] == ("01179000001", None)
    assert got["FAX"] == ("07700900456", None)
