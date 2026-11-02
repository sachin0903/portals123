# Management reports

A report is a reviewed query that returns a table. Graphene ships the query in
[`apps/api/app/reports/catalogue.py`](../apps/api/app/reports/catalogue.py). Operations switches
it on for every firm from **Internal operations → Reports**. A firm then runs it from
**Investments → Reports** and downloads CSV or Excel.

The query is not stored in a row and is not edited on a screen. A parameter is a value bound by
the driver (a date, a transaction type). Adding a report is a code change; it appears on the
operations screen switched off until someone turns it on.

## Who sees which rows

The report runs as the person who asked for it, under the same row-level security as the book.
Someone without `contacts:read_all` gets their own clients and accounts. The preview says so.
`reports:read` is the preview. `reports:export` is the file, and it is not implied by anything else.
The preset grants both to the owner, adviser and compliance roles, and to any existing role
that can already see the book. A firm can take the export off a role afterwards. Switching a
report on is `mi_reports:write`, held by the operations roles that manage platform terms.

An export that would pass 50,000 rows is refused. No partial file is written. Narrow the dates
and run it again. A preview is the first 100 rows and says when that is not the whole extract.

## Identifiers

A row that is one client, account, or movement carries that record's id, as text, so a
spreadsheet does not strip a leading zero. Client ID and Account ID are the public ids
(`crm.contacts.uid`, `platform.accounts.uid`). Transaction ID is the custodian's own
`source_transaction_id` — a movement has no id of its own. The adviser book and the model book
are totals, so they do not carry a client, account, or transaction id.

## What a number means

Market value is the sum of `platform.accounts.value` on accounts that are not draft or closed,
the same basis as the firm overview. Amounts are not converted into a firm currency.

A client has one adviser (0245), so the adviser book counts each client once and its market
values add up to the firm, less the clients with no adviser. Paraplanners and other staff on a
client's team are not an adviser book.

Latest valuations are one row per account and source, at that source's latest date. Two sources
for one pension are not added together.

## Cash

Uninvested cash is not a column on the account. The report reads the firm's current positions
in the lake, using the same cash rule as the holdings summary: a position whose type is cash,
or whose name is cash, a money-market fund, or liquidity. `platform.valuations.cash_value` is a
different balance and is not used.

An account with no position in that slice is left out. If the slice cannot be read, the report
says so and writes nothing. An empty file would look like a firm that holds no cash.

Historical holdings are not available. The lake keeps a current snapshot.
