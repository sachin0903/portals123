"""Sample data for the template editor's live preview (docs/document-generation.md §5.3).

The editor previews a template against representative SAMPLE data — a fake "John Doe" client, a
per-type sample value for each linked form field, and plausible dataset rows — so charts, tables and
merge tokens render like a finished document without generating against a real client. Everything is
a pure function of its inputs except ``sample_datasets``, which reads each dataset's columns to
synthesise rows (a preview never runs live SQL or needs a subject client).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.render import catalogue
from app.render.resolve import rows_from_static

# A fake client + adviser + firm so `{{ client.* }}` and common tokens read like a real document.
_SAMPLE_CLIENT: dict[str, Any] = {
    "name": "John Doe",
    "first_name": "John",
    "surname": "Doe",
    "title": "Mr",
    "email": "john.doe@example.com",
    # `phone` is the mobile. The token kept its name so templates already holding
    # `{{ client.phone }}` still resolve. `telephone` is the other number.
    "phone": "+44 7700 900123",
    "telephone": "0117 900 0000",
    "other_numbers": "0117 900 0000",
    "address": "1 Sample Street, London SW1A 1AA",
    "date_of_birth": "14 May 1980",
}

# Fund-ish labels for synthesised dataset rows, so a preview chart/table looks like a portfolio.
_SAMPLE_LABELS = ["Global Equity", "Corporate Bond", "Property Fund", "Cash", "Index Tracker"]

#: Context keys holding ROWS — the built-in collections an author may bind a repeat, table or chart
#: to exactly as they would a dataset. Mirrors `editor/variables.ts::BUILTIN_COLLECTIONS`.
#:
#: They are NOT datasets and must never be resolved as ones: there is no `documents.datasets` row
#: behind them, so the catalogue answers "no dataset with code 'accounts' in this firm" — and a
#: component binding is a CONTRACT, so that refusal fails the whole render. The renderer supplies
#: them from the context instead (`worker.py::_load_render_inputs`).
BUILTIN_COLLECTIONS: tuple[str, ...] = ("accounts",)

# The sample client's accounts, for `{% for a in accounts %}` and `{{ portfolio.total_value }}`.
#
# THREE of them, and deliberately not one: an author writing an annual review loops over a client's
# accounts, and a single-row sample hides both the things that go wrong at render — a table that
# needs its header repeated, and a total that has to add up to something the eye can check. The
# wrappers differ for the same reason (a client's ISA and SIPP are the common pairing).
#
# VALUES ARE RAW, and formatted at the point of use by the filters the engine already ships
# (`render/jinja_engine.py`): `{{ a.value | currency }}`, `{{ a.opened | date }}`. A pre-formatted
# value would make its filter a no-op, make `sum_currency` impossible, and make a date
# incomparable — an "as at" roll-up over "31 July 2026" and "30 June 2026" can only be found by
# string order, which is not date order. So dates are ISO here and `| date` renders them.
_SAMPLE_ACCOUNTS: list[dict[str, Any]] = [
    {
        "name": "Stocks & Shares ISA",
        "number": "ISA-0010422",
        "product": "Stocks & Shares ISA",
        "product_type": "ISA",
        "status": "active",
        "value": 48250.0,
        "currency": "GBP",
        "risk_level": "5",
        "model": "Balanced Growth",
        "fee_package": "Standard advisory",
        "opened": "2019-04-12",
        "value_as_of": "2026-07-31",
    },
    {
        "name": "Self-Invested Personal Pension",
        "number": "SIPP-0010423",
        "product": "Self-Invested Personal Pension",
        "product_type": "SIPP",
        "status": "active",
        "value": 213400.0,
        "currency": "GBP",
        "risk_level": "6",
        "model": "Adventurous",
        "fee_package": "Standard advisory",
        "opened": "2015-09-03",
        "value_as_of": "2026-07-31",
    },
    {
        "name": "General Investment Account",
        "number": "GIA-0010424",
        "product": "General Investment Account",
        "product_type": "GIA",
        "status": "active",
        "value": 15900.0,
        "currency": "GBP",
        "risk_level": "4",
        "model": "Cautious",
        "fee_package": "Standard advisory",
        "opened": "2022-01-27",
        "value_as_of": "2026-06-30",
    },
]


def sample_field_value(field: dict[str, Any]) -> Any:
    """A plausible sample for one form field, by its ``field_type`` (options win when present)."""
    ftype = str(field.get("field_type") or "text").lower()
    label = str(field.get("label") or field.get("field_key") or "value")
    opts = field.get("options")
    if isinstance(opts, list) and opts:
        first = opts[0]
        return first.get("value", first.get("label")) if isinstance(first, dict) else first
    if ftype in ("number", "integer"):
        return 42
    if ftype in ("currency", "money"):
        return 25000
    if ftype in ("percent", "percentage"):
        return 5.0
    if ftype == "date":
        return date.today().strftime("%d %B %Y")
    if ftype in ("checkbox", "boolean", "bool", "toggle"):
        return True
    if ftype == "email":
        return "john.doe@example.com"
    if ftype in ("phone", "phonenumber"):
        return "+44 7700 900123"
    if ftype == "title":
        return "Mrs"
    if ftype == "gender":
        return "Female"
    if ftype == "marital":
        return "Married"
    if ftype == "employment":
        return "Employed"
    if ftype == "phonenumbers":
        return "0117 900 0000"
    return f"Sample {label.lower()}"


def sample_context(form_fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Jinja variables for a sample preview: the fake client + adviser + firm, their accounts,
    today's date, and a sample value per linked form field (exposed both top-level and grouped under
    ``answers``).

    THIS IS ALSO THE VARIABLE CATALOGUE. ``routers/templates.py::_known_variables`` builds the
    lint's known-token set from these keys, so a root added here becomes acceptable to the preview
    lint, the publish warning and the generate gate at once — and a root the real render supplies
    but this does NOT is reported to the author as unsourced. The real counterpart is
    ``worker.py::_client_context`` + ``_client_accounts_context``; the client-side mirror is
    ``editor/variables.ts::COMMON_ROOTS``. Three places, one vocabulary."""
    today = date.today()
    answers = {
        str(f["field_key"]): sample_field_value(f) for f in form_fields if f.get("field_key")
    }
    return {
        **answers,
        "answers": answers,
        "client": dict(_SAMPLE_CLIENT),
        "client_name": _SAMPLE_CLIENT["name"],
        "client_email": _SAMPLE_CLIENT["email"],
        "adviser": {"name": "Alex Adviser", "email": "alex.adviser@firm.example"},
        "adviser_name": "Alex Adviser",
        # frn = FCA Firm Reference Number, chn = Companies House number (auth.tenants.frn/chn),
        # surfaced so a letterhead's regulatory line ({{ firm.frn }}) previews with a value.
        "firm": {"name": "Sample Advisers Ltd", "frn": "123456", "chn": "01234567"},
        "firm_name": "Sample Advisers Ltd",
        "today": today.isoformat(),
        "date": today.strftime("%d %B %Y"),
        "document_name": "Preview",
        # The client's own book. `accounts` is a list to loop; `portfolio` is what a covering
        # paragraph needs without one ("your three accounts, totalling …").
        "accounts": [dict(a) for a in _SAMPLE_ACCOUNTS],
        "portfolio": sample_portfolio(_SAMPLE_ACCOUNTS),
    }


def sample_portfolio(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """The `portfolio` roll-up over a list of account rows.

    Shared with the worker (`_client_accounts_context`) so the preview and the real render total the
    same way — the sum is trivial, and two trivial sums are exactly how a preview comes to disagree
    with the document a client receives.

    `as_at` is the OLDEST `value_as_of` across the rows, not the newest: a roll-up is only as
    current as its stalest input, and a total stamped with the freshest date overstates how up to
    date it is. It is comparable because the rows hold ISO dates (see `_SAMPLE_ACCOUNTS`) — sorting
    the formatted form would order "9 May" after "31 July". Rows with no valuation date leave it
    blank rather than falling back to today, which would assert a freshness nothing measured; the
    sample rows carry one so an author laying out an "as at …" line sees it filled in the preview
    rather than discovering at render that the line exists."""
    numeric = [
        float(a["value"])
        for a in accounts
        if isinstance(a.get("value"), (int, float)) and not isinstance(a.get("value"), bool)
    ]
    # One currency or none. A mixed-currency book has no single total, and stamping the first row's
    # code onto a sum across two would misstate it — so the total is still returned (an author gets
    # a number with no symbol) but the symbol is withheld rather than guessed.
    codes = {str(a["currency"]) for a in accounts if a.get("currency")}
    dates = sorted(str(a["value_as_of"]) for a in accounts if a.get("value_as_of"))
    return {
        "total_value": sum(numeric),
        "currency": codes.pop() if len(codes) == 1 else "",
        "account_count": len(accounts),
        "as_at": dates[0] if dates else "",
    }


def synthesize_rows(columns: list[str], n: int = 4) -> list[dict[str, Any]]:
    """Plausible sample rows from column names, so a preview's charts/tables show representative
    content: label-ish columns get fund names, numeric-ish columns get descending values."""
    cols = [str(c) for c in columns if c]
    if not cols:
        return []
    year = date.today().year
    rows: list[dict[str, Any]] = []
    for i in range(n):
        row: dict[str, Any] = {}
        for col in cols:
            c = col.lower()
            if "year" in c:
                row[col] = str(year + i)
            elif "date" in c:
                row[col] = date(year, (i % 12) + 1, 1).strftime("%d %b %Y")
            elif any(k in c for k in ("name", "product", "fund", "label", "category", "asset")):
                row[col] = _SAMPLE_LABELS[i % len(_SAMPLE_LABELS)]
            elif any(k in c for k in ("pct", "percent", "allocation", "rate", "weight")):
                row[col] = round(100 / max(n, 1), 1)
            elif any(
                k in c for k in ("value", "amount", "units", "balance", "total", "price", "cost")
            ):
                row[col] = 10000 * (n - i)
            else:
                row[col] = f"{col.replace('_', ' ').title()} {i + 1}"
        rows.append(row)
    return rows


async def sample_datasets(
    session: AsyncSession, tenant_id: int, codes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Sample rows per dataset code (de-duped): inline datasets use their stored rows; provided
    datasets use the author's preview ``sample_data`` when present, else rows synthesised from the
    column schema. Never runs live SQL and never raises — an unknown code → []."""
    out: dict[str, list[dict[str, Any]]] = {}
    for code in dict.fromkeys(c for c in codes if c):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT d.format::text AS format, d.output_schema, "
                        "       v.inline_data, v.sample_data "
                        "FROM documents.datasets d "
                        "LEFT JOIN LATERAL ("
                        "  SELECT inline_data, sample_data FROM documents.dataset_versions v "
                        "  WHERE v.dataset_id = d.id "
                        "  ORDER BY (v.status = 'published') DESC, v.version_number DESC LIMIT 1"
                        ") v ON true "
                        "WHERE d.tenant_id = :t AND d.code = :code AND d.enabled"
                    ),
                    {"t": tenant_id, "code": code},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            # No row of the firm's own — but the code may name a shipped CATALOGUE entry, which has
            # none until a firm configures one. Its DECLARED columns feed the same synthesiser, so
            # a template preview shows plausible rows for a provided dataset instead of an empty
            # table. (This function never runs live SQL; that is what `resolve.py` is for.)
            entry = catalogue.by_key(code)
            out[code] = synthesize_rows([c.name for c in entry.columns]) if entry else []
        elif row["format"] == "inline":
            out[code] = rows_from_static(row["inline_data"])
        elif row["sample_data"]:
            # An author-supplied preview sample beats synthesised rows for a provided dataset.
            out[code] = rows_from_static(row["sample_data"])
        else:
            schema = row["output_schema"] if isinstance(row["output_schema"], dict) else {}
            out[code] = synthesize_rows(schema.get("columns") or [])
    return out
