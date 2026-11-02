"""The management reports a firm can run.

Each entry is a reviewed query, the same posture as the document dataset catalogue: the SQL is a
constant in this module, and a parameter is a value bound by the driver. Nothing here names a
table, a column, or a sort from outside the file. Operations switches an entry on; the query is
not stored in a row and is not edited from a screen.

Market value is the sum of ``platform.accounts.value`` on accounts that are not draft or closed,
the same basis as the firm overview. Values are not converted into a firm currency. A client
named on two advisers is counted on both adviser rows. Two valuation sources for one pension are
separate rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ColumnKind = Literal["text", "money", "number", "date", "percent"]
ParamKind = Literal["date", "enum"]

#: Canonical transaction types. The optional filter on the transactions report binds one of these.
TRANSACTION_TYPES: tuple[str, ...] = (
    "buy",
    "sell",
    "dividend",
    "interest",
    "fee",
    "tax",
    "transfer_in",
    "transfer_out",
    "deposit",
    "withdrawal",
    "corporate_action",
    "adjustment",
)


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    label: str
    kind: ColumnKind


@dataclass(frozen=True, slots=True)
class Param:
    key: str
    label: str
    kind: ParamKind
    required: bool = False
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Report:
    """One shipped report. ``sql`` is None for the cash report, which reads the lake."""

    code: str
    name: str
    description: str
    columns: tuple[Column, ...]
    params: tuple[Param, ...] = ()
    sql: str | None = None

    @property
    def reads_lake(self) -> bool:
        return self.sql is None


def _col(key: str, label: str, kind: ColumnKind = "text") -> Column:
    return Column(key, label, kind)


CLIENTS_MARKET_VALUE_SQL = """
SELECT c.uid AS client_id,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name) AS client_name,
       c.email,
       CAST(c.service_type AS text) AS service_type,
       CASE
         WHEN c.provider_status IN ('suspended', 'inactive') THEN 'suspended'
         WHEN NOT EXISTS (
           SELECT 1 FROM platform.accounts owned WHERE owned.contact_id = c.id
         ) THEN 'lead'
         ELSE 'active'
       END AS status,
       -- The contact's adviser (role = 'adviser' is unique per contact, 0245) — not the whole
       -- client team, hence the column staying singular despite its plural name.
       (
         SELECT u.name
         FROM crm.client_advisers ca
         JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.id AND ca.role = 'adviser' AND u.name IS NOT NULL
       ) AS advisers,
       COALESCE(acc.open_accounts, 0) AS open_accounts,
       CASE WHEN COALESCE(acc.open_accounts, 0) > 0 THEN acc.portfolio_value END AS portfolio_value,
       acc.valued_at
FROM crm.contacts c
LEFT JOIN LATERAL (
  SELECT SUM(a.value) FILTER (WHERE a.status NOT IN ('draft', 'closed')) AS portfolio_value,
         count(*) FILTER (WHERE a.status NOT IN ('draft', 'closed')) AS open_accounts,
         max(COALESCE(a.data ->> 'valued_at', CAST(a.updated_at AS text)))
           FILTER (WHERE a.status NOT IN ('draft', 'closed')) AS valued_at
  FROM platform.accounts a
  WHERE a.contact_id = c.id
) acc ON true
WHERE c.deleted_at IS NULL
ORDER BY client_name, c.uid
"""

ACCOUNTS_SQL = """
SELECT a.uid AS account_id,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name) AS client_name,
       c.uid AS client_id,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (
         SELECT u.name
         FROM crm.client_advisers ca
         JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.id AND ca.role = 'adviser'
       ) AS adviser,
       m.name AS model,
       CAST(p.product_type AS text) AS wrapper,
       CAST(a.custodian AS text) AS custodian,
       CAST(a.status AS text) AS status,
       a.currency,
       a.value AS market_value,
       COALESCE(a.data ->> 'valued_at', CAST(a.updated_at AS text)) AS valued_at,
       a.opened_at AS opened
FROM platform.accounts a
JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL
LEFT JOIN platform.products p ON p.id = a.product_id
LEFT JOIN platform.models m ON m.id = a.model_id
ORDER BY client_name, a.uid
"""

ADVISER_BOOK_SQL = """
SELECT u.name AS adviser,
       u.email,
       count(DISTINCT c.id) AS clients,
       count(DISTINCT a.id) FILTER (
         WHERE a.status NOT IN ('draft', 'closed')
       ) AS open_accounts,
       COALESCE(SUM(a.value) FILTER (WHERE a.status NOT IN ('draft', 'closed')), 0) AS market_value
FROM crm.client_advisers ca
JOIN auth.users u ON u.id = ca.adviser_id
JOIN crm.contacts c ON c.id = ca.contact_id AND c.deleted_at IS NULL
LEFT JOIN platform.accounts a ON a.contact_id = c.id
-- An adviser's book is the clients where THEY are the adviser, not any client they happen to
-- support in another role.
WHERE ca.role = 'adviser'
GROUP BY u.id, u.name, u.email
ORDER BY u.name
"""

MODEL_BOOK_SQL = """
SELECT m.name AS model,
       m.code,
       count(DISTINCT a.id) AS accounts,
       count(DISTINCT a.contact_id) AS clients,
       COALESCE(SUM(a.value), 0) AS market_value,
       m.target_return,
       m.model_charge
FROM platform.models m
JOIN platform.accounts a
  ON a.model_id = m.id AND a.status NOT IN ('draft', 'closed')
JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL
GROUP BY m.id, m.name, m.code, m.target_return, m.model_charge
ORDER BY m.name
"""

TRANSACTIONS_SQL = """
SELECT COALESCE(t.transaction_date, t.value_date,
         CAST(t.posted_at AT TIME ZONE 'UTC' AS date),
         CAST(t.synced_at AT TIME ZONE 'UTC' AS date)) AS transaction_date,
       t.source_transaction_id AS transaction_id,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name) AS client_name,
       c.uid AS client_id,
       COALESCE(a.name, a.uid) AS account_name,
       a.uid AS account_id,
       CAST(p.product_type AS text) AS wrapper,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (
         SELECT u.name
         FROM crm.client_advisers ca
         JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = c.id AND ca.role = 'adviser'
       ) AS adviser,
       CAST(t.type AS text) AS type,
       COALESCE(NULLIF(t.source_subtype, ''), NULLIF(t.source_type, '')) AS custodian_type,
       t.asset_name AS instrument,
       t.isin,
       t.amount AS amount,
       t.currency,
       COALESCE(NULLIF(t.status, ''), 'settled') AS status
FROM platform.transactions t
JOIN platform.accounts a ON a.id = t.account_id AND a.tenant_id = t.tenant_id
-- The client is the account's. A synced movement names its account and not its contact
-- (`contact_id` is null on the custodian feeds), so a join on `t.contact_id` loses the book.
JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL
LEFT JOIN platform.products p ON p.id = a.product_id
WHERE COALESCE(t.transaction_date, t.value_date,
         CAST(t.posted_at AT TIME ZONE 'UTC' AS date),
         CAST(t.synced_at AT TIME ZONE 'UTC' AS date)) >= CAST(:from_date AS date)
  AND COALESCE(t.transaction_date, t.value_date,
         CAST(t.posted_at AT TIME ZONE 'UTC' AS date),
         CAST(t.synced_at AT TIME ZONE 'UTC' AS date)) <= CAST(:to_date AS date)
  AND (CAST(:type AS text) IS NULL OR CAST(t.type AS text) = CAST(:type AS text))
ORDER BY transaction_date, t.id
"""

LATEST_VALUATIONS_SQL = """
SELECT client_name, client_id, account_id, wrapper, source, as_of_date, market_value, currency,
       crystallised_value, uncrystallised_value
FROM (
  SELECT DISTINCT ON (v.account_id, v.source_system)
         COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name) AS client_name,
         c.uid AS client_id,
         a.uid AS account_id,
         CAST(p.product_type AS text) AS wrapper,
         v.source_system AS source,
         v.as_of_date,
         v.market_value,
         v.currency,
         v.crystallised_value,
         v.uncrystallised_value
  FROM platform.valuations v
  JOIN platform.accounts a ON a.tenant_id = v.tenant_id AND a.id = v.account_id
  JOIN crm.contacts c ON c.id = a.contact_id AND c.deleted_at IS NULL
  LEFT JOIN platform.products p ON p.id = a.product_id
  ORDER BY v.account_id, v.source_system, v.as_of_date DESC
) latest
ORDER BY client_name, wrapper, source
"""


CATALOGUE: tuple[Report, ...] = (
    Report(
        code="clients_market_value",
        name="Clients and market value",
        description=(
            "Every client, who advises them, and what their open accounts are worth today."
        ),
        columns=(
            _col("client_id", "Client ID"),
            _col("client_name", "Client"),
            _col("email", "Email"),
            _col("service_type", "Service"),
            _col("status", "Status"),
            _col("advisers", "Advisers"),
            _col("open_accounts", "Open accounts", "number"),
            _col("portfolio_value", "Market value", "money"),
            _col("valued_at", "Valued at", "date"),
        ),
        sql=CLIENTS_MARKET_VALUE_SQL,
    ),
    Report(
        code="accounts",
        name="Accounts",
        description=(
            "Every account, with its adviser and investment model. Useful for checking "
            "who looks after what."
        ),
        columns=(
            _col("account_id", "Account ID"),
            _col("client_name", "Client"),
            _col("client_id", "Client ID"),
            _col("adviser", "Adviser"),
            _col("model", "Model"),
            _col("wrapper", "Wrapper"),
            _col("custodian", "Custodian"),
            _col("status", "Status"),
            _col("currency", "Currency"),
            _col("market_value", "Market value", "money"),
            _col("valued_at", "Valued at", "date"),
            _col("opened", "Opened", "date"),
        ),
        sql=ACCOUNTS_SQL,
    ),
    Report(
        code="adviser_book",
        name="Adviser book",
        description=(
            "How many clients each adviser looks after, and what those clients hold. A "
            "client shared by two advisers appears under both, so the totals can come to "
            "more than the firm's."
        ),
        columns=(
            _col("adviser", "Adviser"),
            _col("email", "Email"),
            _col("clients", "Clients", "number"),
            _col("open_accounts", "Open accounts", "number"),
            _col("market_value", "Market value", "money"),
        ),
        sql=ADVISER_BOOK_SQL,
    ),
    Report(
        code="model_book",
        name="Model book",
        description="Each investment model in use, and how much is invested in it.",
        columns=(
            _col("model", "Model"),
            _col("code", "Code"),
            _col("accounts", "Accounts", "number"),
            _col("clients", "Clients", "number"),
            _col("market_value", "Market value", "money"),
            _col("target_return", "Target return", "number"),
            _col("model_charge", "Model charge", "number"),
        ),
        sql=MODEL_BOOK_SQL,
    ),
    Report(
        code="uninvested_cash",
        name="Uninvested cash",
        description=(
            "Cash held in clients' accounts that is not yet invested, including money-market "
            "funds. Accounts we have no current holdings for are not included."
        ),
        columns=(
            _col("client_name", "Client"),
            _col("client_id", "Client ID"),
            _col("account_id", "Account ID"),
            _col("wrapper", "Wrapper"),
            _col("model", "Model"),
            _col("currency", "Currency"),
            _col("market_value", "Market value", "money"),
            _col("cash", "Cash", "money"),
            _col("cash_share", "Cash %", "percent"),
        ),
    ),
    Report(
        code="transactions",
        name="Transactions",
        description=(
            "Payments, withdrawals, trades, fees and income across your clients' accounts, "
            "for the dates you choose."
        ),
        params=(
            Param("from_date", "From", "date", required=True),
            Param("to_date", "To", "date", required=True),
            Param("type", "Type", "enum", options=TRANSACTION_TYPES),
        ),
        columns=(
            _col("transaction_date", "Date", "date"),
            _col("transaction_id", "Transaction ID"),
            _col("client_name", "Client"),
            _col("client_id", "Client ID"),
            _col("account_name", "Account"),
            _col("account_id", "Account ID"),
            _col("wrapper", "Wrapper"),
            _col("adviser", "Adviser"),
            _col("type", "Type"),
            _col("custodian_type", "Custodian type"),
            _col("instrument", "Instrument"),
            _col("isin", "ISIN"),
            _col("amount", "Amount", "money"),
            _col("currency", "Currency"),
            _col("status", "Status"),
        ),
        sql=TRANSACTIONS_SQL,
    ),
    Report(
        code="latest_valuations",
        name="Latest valuations",
        description=(
            "The most recent value of each account. Where a pension is valued by two "
            "providers, both values are shown; add up only one of them."
        ),
        columns=(
            _col("client_name", "Client"),
            _col("client_id", "Client ID"),
            _col("account_id", "Account ID"),
            _col("wrapper", "Wrapper"),
            _col("source", "Source"),
            _col("as_of_date", "Date", "date"),
            _col("market_value", "Market value", "money"),
            _col("currency", "Currency"),
            _col("crystallised_value", "Crystallised", "money"),
            _col("uncrystallised_value", "Uncrystallised", "money"),
        ),
        sql=LATEST_VALUATIONS_SQL,
    ),
)

BY_CODE: dict[str, Report] = {report.code: report for report in CATALOGUE}


def get(code: str) -> Report | None:
    return BY_CODE.get(code)
