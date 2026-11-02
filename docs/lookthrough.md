# IFSL fund look-through

IFSL/Malborough supplies dated XLSX valuation reports for funds administered by IFSL. Graphene
imports those reports once and makes the composition available to any client or staff user whose
RLS-visible book currently holds a matching share-class ISIN.

## Data lineage

The import reads the stable sections verified against the 491/492/493 reports:

- `IOControl`: fund code and report date (`E2`/`E3`), plus share classes in columns
  `AA:AF` (class code, name, ISIN and SEDOL);
- `Valuation`: its date in `A7`, and constituent rows from row 12 using SEDOL, ISIN, name,
  long name, maturity, currency, quantity, price, book cost, asset-group code, market value,
  portfolio weight, prior price, daily movement and country;
- `DailyNAV`: the workbook's rolling NAV, units and fund-value observations by share class;
- `Cash_at_Bank`: signed base-currency cash and NAV weight by currency;
- `Open_Currency_Contracts`: fund-level and share-class currency contracts, delivery date,
  direction, current value, unrealised gain/loss and NAV impact.

The three analytics sheets are optional so an older disclosure remains importable; its
corresponding visual states that the data was not supplied. The dated composition itself still
requires `IOControl` and `Valuation`.

Both dates must agree. Share-class ISINs must pass the ISO 6166 shape and check digit. A
constituent may lack an ISIN, because cash and some derivatives legitimately do. Total rows are
not positions. Unknown asset-group codes are retained in `asset_group` and presented as `Other`;
the importer never invents sector data and does not recursively expand a constituent fund.

The upload is capped at 25 MiB per workbook, 100 MiB expanded (zip-bomb guard), and ten workbooks
per request. Only `.xlsx` is accepted. `openpyxl` runs with formulas resolved to the values cached
in the supplied report.

## Storage and history

Migrations `0124_ifsl_fund_lookthrough` and `0126_lookthrough_analytics` create global
`lookthrough.{funds,share_classes,snapshots,positions,nav_history,cash_balances,currency_contracts}`
tables:

- one fund per IFSL fund code;
- one globally unique, upper-case ISIN per share class;
- one immutable snapshot per uploaded file hash;
- ordered constituent positions carrying raw and broad classifications.

Every dated snapshot is retained. Importing the same bytes is an `unchanged` no-op. Different
bytes for the same fund/date create a new snapshot whose `supersedes_id` points to the previous
active import. Reads select the latest correction for each date and default to the newest date.
The source filename, SHA-256 digest, actor and import time remain attached to the snapshot.

The workbook itself is not retained. Re-uploading a file imported before `0126` enriches its
existing snapshot in place and reports `enriched`; it does not invent a second source snapshot.
The next identical upload is again `unchanged`.

## Access rule

Operations permissions are `lookthrough:read` and `lookthrough:write`; their declarations make
them impossible to grant to a firm role. The admin portal lists coverage/history and imports
reports. Its register shows every share-class ISIN that can match a holding, and each fund opens
an operations-only reference view of the same dated composition without requiring the operator
to hold it.

The global tables being readable is not sufficient to open a fund composition. The client
endpoint first resolves only accounts visible through `book_session`, reads those accounts from
their own primary-custodian lake slices, and requires one current holding whose normalised ISIN
belongs to the requested fund. `?account=<uid>` narrows that proof and the illustrative indirect
exposure to one account. An unknown fund, an unavailable date and a known fund the caller does
not hold all return the same 404.

Holdings receive `lookthrough_uid`, `lookthrough_fund_name` and `lookthrough_as_of` only when an
active imported snapshot exists. The portals use that annotation to show the action; they do not
infer eligibility client-side.

## Presentation

The shared `@graphene/ui/book/lookthrough` component is the sole presentation across client,
platform and admin. The holder posture shows the selected source date, the client's current
holding value, the ten largest positions, ranked country/currency/asset-class allocations and the
complete sortable composition. The largest-positions card is a ranked list (`topHoldings`): each
row names the position, where it sits (country · asset class), its weight against the largest as
a bar, and — for a holder — the slice of their own money it represents; the heading states the
share of the fund the ten account for. It replaced a one-row treemap that labelled a cell only when
it was wide enough, which in a real fund of several hundred positions was never, so the picture
was unlabelled slivers beside one block called "Other positions". The analysis section adds:

- a NAV line chart using the held share class where supplied, then the fund-level series;
- a weighted bond-maturity profile, including perpetual instruments;
- disclosed securities versus signed net cash and remaining NAV items;
- open currency contracts narrowed to the held share class plus fund-level hedges.

**Colour.** The headline sits on the product's hero band (`hero-surface`, the same dark anchor as
every record screen, with the firm's accent as its hairline and a soft accent bloom behind the
figure); it was a pale primary wash that left the whole report one tone. The charts take the
firm's colours from the categorical ramp (`series()` in `categorical.ts`): the three allocation
breakdowns each wear their own step so they read as three, the maturity profile shares the asset
class step, the cash bar's three segments are three steps with a matching legend dot, the NAV
line runs primary → brand over a brand fill, and the top-ten bars run primary → brand with the
first three ranks badged in the ramp. Nothing is a hard-coded colour, so a re-themed firm
re-themes the report.

**The way in.** `LookThroughButton` (`investment/look-through-button.svelte`) is the control on a
holdings row: a pill in primary → brand with a brand glow that breathes and a sheen that sweeps
on hover (both off under `prefers-reduced-motion`). With `href` it is an anchor (the holdings
tables); without, a decorative `<span>` inside a row that is already the link (`HoldingsGrouped`,
the client's account page), where a nested anchor would be invalid. On that grouped row the pill
is the last thing on the row, after the value, where the chevron would otherwise be; the text is
already taller than the pill, so an eligible fund does not grow its row.
It replaced a twelve-pixel text link and, on the grouped rows, a bare
rows, a bare
chevron that said nothing about what the row opened.

Underlying gain/loss (market value against the workbook's book cost) is no longer shown as a card;
the per-position figure remains a column of the composition table. A fund's book cost is the
manager's, not the client's, and the card read as if it were the client's own gain.

Indirect values are illustrative (`current held value × report weight`), not separately valued
positions. The admin reference posture replaces the personal value with the complete
matching-share-class register and does not invent indirect money values. Coverage is the sum of
report weights — how much of the fund the disclosed positions account for — and any shortfall is
stated as residual/unclassified; the client-facing stat is labelled "Disclosed", since "coverage"
names nothing a client would recognise.

The register lists one ISIN per fund with a `+n` count (the full set is on the fund page, and a
share class per row made every row taller than the fund name it belonged to).

Numerics are converted to floats before they leave the router. These endpoints declare
`dict[str, object]`, so FastAPI serialises through pydantic rather than `jsonable_encoder`, and
pydantic renders a `Decimal` as a JSON **string** — which every reader types as a number. That is
silent in both directions: the register printed a raw `0.9934578303` where a percentage belonged,
and a string weight failed `Number.isFinite`, so the largest-positions card reported "no position weights are
available for this snapshot" for a fully populated fund. `tests/test_lookthrough.py` pins the wire
types.
