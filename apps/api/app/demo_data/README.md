# Demo client book

`client_book.json` is the anonymised source shape for the non-production tenant-data seed.

It was generated from one complete four-account provider extract using
`scripts/build_demo_book_fixture.py`. The original files are not committed because they contain a
client name and provider account numbers. The generated fixture:

- replaces every client/account label and identifier;
- keeps all 471 distinct economic transaction rows and their provider fields;
- takes the latest dated position per account and instrument (165 positions, including zero-value
  residual positions — the seed drops those eight, because they are dividend entitlements and
  leftover lots, not holdings);
- synthesises an opening buy or deposit for every current holding the transaction extract never
  touched (the ISA and GIA have none at all);
- stores `book_cost`, `unrealised_gain` and `unrealised_gain_percent` on every non-zero position.
  The holdings extract never had them; they are backed out from the account's stated return
  (workbook cumulative return, or the daily series when that move stays inside ±100%, else 12%).
  `_source_book()` stamps the same three fields on load so a rebuild that forgot them still
  seeds a numeric lake column. Cash is cost = value, gain = 0. Zero-value residual lots stay
  bare. A DuckLake table first created with those keys as NULL keeps serving NULL across
  `CREATE OR REPLACE` (Cash still works because `market_value` was numeric from day one), so the
  write **drops** the relation first, and the demo holdings reader fills any leftover NULL from
  the valuation series so a tenant that has not been re-seeded still shows a cost and a gain;
- removes report duplication caused by the same position appearing once per servicing team;
- retains all 79 supplied monthly return observations in each account's `performance` field;
- keeps the provider's actual 113-business-day market and cash valuation history for the monetary
  portal chart.

The workbook contains returns, not historical account balances, so it is not converted into
pounds. The account `performance` field keeps those percentages separately; the monetary chart
uses the holdings report's genuine daily balances.

The first seeded client receives the complete four-account book at source values. The remaining
clients use deterministic subsets and scales of those account shapes, giving the demo firm varied
wrappers, asset mixes and portfolio sizes without introducing invented securities.

Operations can refresh that book without wiping the firm: `POST /admin/tenants/{uid}/data-job`
with `kind=seed_book` (or `scripts/reset_tenant_data.py ALIAS --seed-book`) deletes the previous
`seeded_demo` clients and their accounts/transactions/holdings, then writes them again. It does
not require the tenant to be suspended and does not touch real clients, documents or staff.

Account links are tagged with an enabled **custodian** (`seccl`, then `sei`) rather than the first
`tenant_platforms` row alphabetically. Graphene enables Morgan Lloyd as a scheme administrator;
the client portal home only admits custodian (or `seeded_demo`) links, so tagging the book as
Morgan Lloyd made every wrapper vanish for a client login.
