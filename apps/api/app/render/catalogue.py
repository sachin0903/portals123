"""THE dataset catalogue — Graphene-authored, parameterised queries a firm picks from.

This module IS the set of datasets a document template can draw on. It replaces free-text SQL
authoring, which was deleted: a firm could type any ``SELECT`` and the whole defence was a keyword
denylist, a lexer and a bind allowlist guarding a query nobody had reviewed. What a firm actually
needed was never "any query" — it was "the valuations table, for this client, over this period", so
the query is ours and the CHOICES are theirs.

Entries live in CODE, not in rows. A new entry is available to every firm the moment it ships, an
entry cannot drift per tenant, and there is nothing to seed or clone on tenant creation — the same
posture ``app/email/defaults.py`` takes for the shipped email defaults. A firm may still CONFIGURE
one: a ``documents.datasets`` row with ``format='catalogue'`` and ``catalogue_ref = {key, params}``
holds its chosen parameters (and its own name), and resolution reads that row when it exists and
falls back to the entry's declared defaults when it does not. Two configurations of one entry are
legitimate ("Transactions, last 12 months" and "Transactions, year to date"), which is why the row
carries its own ``code`` and the ``key`` names the entry.

WHAT A PARAMETER IS ALLOWED TO BE is the whole security argument. A parameter is a VALUE bound by
the driver, never text spliced into SQL — no parameter names a table, a column, an order or a
predicate. So the SQL of an entry is a constant, and the only thing a firm varies is what
Postgres compares against. That is what lets the executor drop the validator: there is no author
SQL left to validate.

TWO RULES THE SQL ITSELF MUST FOLLOW, both learned the expensive way:

* **``CAST(x AS type)``, never ``x::type``, anywhere near a bind.** SQLAlchemy's bind scanner reads
  ``:from_date::date`` as a bind named ``from_dat`` and ``:a::text, :b`` as the single bind ``b`` —
  it silently truncates or drops. Postgres then raises a bare syntax error, which
  ``resolve_datasets_reporting`` launders into "could not be read. Check the query and try again."
  on a query the firm cannot check because we wrote it. ``app/render/resolve.py`` asserts the bind
  names it was handed match the SQL's own, so a slip is a named internal error rather than that.
* **Every output column is DECLARED** (:class:`Column`) and its name is unique. The resolver reads
  rows into dicts, so two columns of one name collapse into one and ``output_schema`` would promise
  a column the rows do not carry.

Entitlement is checked when a firm CONFIGURES or LINKS an entry, and on the live data door — not at
render time. A queued render has no acting human: the worker drains ``documents.render_jobs`` with a
tenant id and no principal, so the only boundary it can enforce is the one the envelope already
enforces (``SET LOCAL ROLE authenticated`` plus the tenant's claims). A per-user entitlement is
therefore an AUTHORING question, which is where :data:`Entry.entitlement` is consulted.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

#: The client a ``subject`` entry is about (``crm.contacts.id``). Supplied by the resolver, never
#: declared as a parameter, and a ``subject`` entry MUST bind it in its own ``WHERE``.
SUBJECT_BIND = "subject_id"

#: The firm. Supplied by the resolver; any entry may bind it and every entry that can, should.
TENANT_BIND = "tenant_id"

#: Binds the resolver fills in. An entry may use these and may not declare them.
RESOLVER_BINDS = frozenset({SUBJECT_BIND, TENANT_BIND})

#: Every shipped key starts with this, and a firm's own dataset code may not.
#:
#: A KEY MUST BE A BARE IDENTIFIER — no dots. The first version of this used a namespace
#: (``book.valuations``), which reads better and does not work: a template binds a dataset by
#: writing ``{% for row in <code> %}``, and ``components.loop_sources`` deliberately EXCLUDES a
#: dotted source because ``{% for a in answers.items %}`` is a path into the context, not a dataset
#: name. So a dotted key was silently dropped, the dataset was never resolved, and Jinja saw
#: ``book`` as an undeclared root — every cell in the table rendered blank, with the only symptom
#: being an authoring warning naming a variable the author never wrote.
#:
#: The prefix is what keeps a firm's code from colliding with a shipped key. The dot used to do
#: that for free (``_CODE_RE`` forbids it), so with the dot gone the rule has to be stated.
RESERVED_KEY_PREFIX = "book_"

ParamKind = Literal["date", "int", "bool", "enum"]

#: What a declared column holds, for the table renderer and the chart helpers. Not a SQL type — the
#: query already casts; this is how the value should be PRESENTED.
ColumnKind = Literal["text", "money", "number", "date", "percent"]


class ParamError(ValueError):
    """A supplied parameter is not one the entry declares, or not a value it accepts.

    Raised at configure time and at compile time. The message is author-facing: it names the
    parameter and what was wrong, because the alternative is a query that quietly ignores the
    setting — the failure mode that reads as success.
    """


@dataclass(frozen=True, slots=True)
class Column:
    """A declared output column of an entry."""

    name: str
    label: str
    kind: ColumnKind = "text"


@dataclass(frozen=True, slots=True)
class Param:
    """A value a firm may set on an entry.

    ``default`` is the value used when the firm sets nothing, and for a ``date`` it may be a
    RELATIVE token (``-12m``): a statement generated every quarter should mean "the last twelve
    months" each time, not the twelve months that were current when somebody filled the form in.
    ``None`` is a real default and means "no bound" — every date predicate is written to pass when
    its bind is NULL.
    """

    name: str
    kind: ParamKind
    label: str
    help: str = ""
    default: Any = None
    #: ``enum`` only: the accepted values, as (value, label) pairs.
    options: tuple[tuple[str, str], ...] = ()
    #: ``int`` only.
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    """One catalogue dataset."""

    key: str
    name: str
    description: str
    #: ``subject`` binds :data:`SUBJECT_BIND` and is about one client; ``global`` is the firm.
    scope: Literal["subject", "global"]
    sql: str
    columns: tuple[Column, ...]
    params: tuple[Param, ...] = ()
    #: A permission slug required to CONFIGURE or LINK this entry. ``None`` means any author who may
    #: write datasets. See the module docstring for why this is not a render-time check.
    entitlement: str | None = None
    #: Longer note shown in the picker — what the rows mean, and any caveat about where they come
    #: from. Deliberately separate from ``description`` (one line, listing) so a caveat cannot be
    #: truncated out of existence.
    note: str = ""
    _by_name: dict[str, Param] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.key}: duplicate output column name")
        pnames = [p.name for p in self.params]
        if len(pnames) != len(set(pnames)):
            raise ValueError(f"{self.key}: duplicate parameter name")
        clash = RESOLVER_BINDS.intersection(pnames)
        if clash:
            raise ValueError(
                f"{self.key}: {', '.join(sorted(clash))} is supplied by the resolver, not declared"
            )
        # A subject entry SCOPES ITSELF. RLS cannot be the mechanism: measured on the live schema,
        # `platform.valuations` has no contact branch at all (its only client door is
        # `account_id = ANY (auth.account_scope())`), while `platform.contributions`' tenant branch
        # is NOT gated on `auth.can_read_all()` — so under one set of minted subject claims the
        # first table returns nothing and the second returns the whole firm. One is a blank table
        # in a client's statement, the other is another client's money in it.
        if self.scope == "subject" and f":{SUBJECT_BIND}" not in self.sql:
            raise ValueError(f"{self.key}: a per-client entry must bind :{SUBJECT_BIND} itself")
        # The mirror, and the other half of making `scope` mean something. A ``global`` entry is
        # resolved FIRM-WIDE (minted ``contacts:read_all``), so one that filters on the render's
        # client is not the dataset it says it is: it would come back client-shaped under firm-wide
        # claims, which is how "firm-wide" quietly became "whichever client this document is for".
        # The resolver used to catch this per row at render; declaring the scope beside the SQL
        # makes it a contradiction that cannot be written down.
        if self.scope != "subject" and f":{SUBJECT_BIND}" in self.sql:
            raise ValueError(f"{self.key}: a firm-wide entry must not bind :{SUBJECT_BIND}")
        # An entry with no columns yields no declared schema, so the preview synthesiser produces
        # nothing and the field picker renders an empty list with no error to explain it.
        if not self.columns:
            raise ValueError(f"{self.key}: an entry must declare its output columns")
        # A BARE IDENTIFIER, matching what a template may write in `{% for row in <code> %}` and
        # what `_CODE_RE` accepts. See :data:`RESERVED_KEY_PREFIX` — a dotted key is dropped by
        # `components.loop_sources` and renders a blank table.
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", self.key):
            raise ValueError(f"{self.key}: a key must be lower_snake_case with no dots")
        # ``::`` next to a bind is the SQLAlchemy truncation trap in the module docstring. Refuse it
        # here rather than in review: the symptom appears only as a Postgres syntax error at render.
        if re.search(r":\w+::", self.sql):
            raise ValueError(f"{self.key}: use CAST(x AS type), not x::type, next to a bind")
        object.__setattr__(self, "_by_name", {p.name: p for p in self.params})

    def param(self, name: str) -> Param | None:
        return self._by_name.get(name)


_REL_RE = re.compile(r"^([+-])(\d{1,4})([dmy])$")


def _shift_months(anchor: date, months: int) -> date:
    """``anchor`` moved by whole months, with the day clamped to the target month's length.

    31 January minus one month is 31 December, not the 31st of a month that has 30 days — clamping
    is the only answer that keeps a monthly range from skipping or repeating a day.
    """
    total = (anchor.year * 12 + anchor.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def resolve_date(token: Any, *, today: date) -> date | None:
    """A date parameter's value: an ISO date, ``today``, a relative token, or ``None``.

    Relative tokens are ``±N`` followed by ``d`` (days), ``m`` (months) or ``y`` (years) — ``-12m``
    is twelve months back from ``today``. They exist so a configured range stays current: the
    alternative is a firm's quarterly statement silently reporting the same fixed quarter for ever.
    """
    if token is None or token == "":
        return None
    if isinstance(token, date):
        return token
    if not isinstance(token, str):
        raise ParamError(f"{token!r} is not a date")
    text = token.strip().lower()
    if text == "today":
        return today
    rel = _REL_RE.match(text)
    if rel:
        sign = -1 if rel.group(1) == "-" else 1
        amount = int(rel.group(2)) * sign
        unit = rel.group(3)
        if unit == "d":
            return date.fromordinal(today.toordinal() + amount)
        if unit == "m":
            return _shift_months(today, amount)
        return _shift_months(today, amount * 12)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ParamError(
            f"{token!r} is not a date. Use YYYY-MM-DD, 'today', or a relative range like '-12m'."
        ) from exc


def _coerce(param: Param, value: Any, *, today: date) -> Any:
    if param.kind == "date":
        return resolve_date(value, today=today)
    if param.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "on", "off", ""}:
            return value.strip().lower() in {"true", "on"}
        raise ParamError(f"{param.name}: {value!r} is not yes or no")
    if param.kind == "int":
        if value is None or value == "":
            return None
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise ParamError(f"{param.name}: {value!r} is not a whole number") from exc
        if param.minimum is not None and number < param.minimum:
            raise ParamError(f"{param.name}: must be {param.minimum} or more")
        if param.maximum is not None and number > param.maximum:
            raise ParamError(f"{param.name}: must be {param.maximum} or less")
        return number
    # enum
    if value is None or value == "":
        return None
    allowed = {v for v, _ in param.options}
    text = str(value)
    if text not in allowed:
        # Name what IS allowed. The vendor-integration lesson: a refusal that does not name the
        # accepted values is answered by guessing.
        raise ParamError(f"{param.name}: {text!r} is not one of {', '.join(sorted(allowed))}")
    return text


def compile_entry(
    entry: Entry,
    params: dict[str, Any] | None,
    *,
    today: date,
) -> tuple[str, dict[str, Any]]:
    """The entry's SQL and the full, typed bind dict for it.

    Every declared parameter appears in the binds — absent ones as their (resolved) default —
    because a bind the SQL names and the caller omits is a driver error, not a NULL. An UNDECLARED
    parameter is refused rather than dropped: silently ignoring one means a firm's setting does
    nothing and the document looks right.

    :data:`SUBJECT_BIND` is not added here; the resolver supplies it for a ``subject`` entry.
    """
    supplied = dict(params or {})
    unknown = sorted(set(supplied) - {p.name for p in entry.params})
    if unknown:
        raise ParamError(
            f"{entry.key} takes no setting called {', '.join(unknown)}"
            + (f" (it takes {', '.join(p.name for p in entry.params)})" if entry.params else "")
        )
    binds: dict[str, Any] = {}
    for param in entry.params:
        raw = supplied.get(param.name, param.default)
        if param.name in supplied and supplied[param.name] in (None, ""):
            # An explicitly cleared value means "no bound", NOT "fall back to the default" — a
            # firm that empties a date is saying it wants the whole history.
            raw = None
        binds[param.name] = _coerce(param, raw, today=today)
    return entry.sql, binds


def validate_params(entry: Entry, params: dict[str, Any] | None) -> dict[str, Any]:
    """Check a firm's chosen parameters at CONFIGURE time and return them as stored.

    Stored VERBATIM (``-12m`` stays ``-12m``), because the whole point of a relative token is that
    it is resolved on each render. This only proves the values are ones the entry accepts, so the
    refusal lands on the person who typed them rather than on a document a year later.
    """
    supplied = dict(params or {})
    # Compiling against a fixed anchor is enough to validate: a token that resolves today resolves
    # on any date, and an ISO date is anchor-independent.
    compile_entry(entry, supplied, today=date(2000, 1, 1))
    return supplied


#: Every shipped entry, by key. Populated below; ``by_key`` is the only reader.
_ENTRIES: dict[str, Entry] = {}


def register(entry: Entry) -> Entry:
    if entry.key in _ENTRIES:
        raise ValueError(f"duplicate catalogue key {entry.key}")
    _ENTRIES[entry.key] = entry
    return entry


def by_key(key: str) -> Entry | None:
    return _ENTRIES.get(key)


def all_entries() -> tuple[Entry, ...]:
    """Every entry, in declaration order — which is the order the picker shows."""
    return tuple(_ENTRIES.values())


def keys() -> frozenset[str]:
    return frozenset(_ENTRIES)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# THE SHIPPED ENTRIES
#
# Every one of these was written against the live schema and EXECUTED before it was declared —
# column names, nullability and join paths are measured, not remembered. Four rules they all
# follow, each of them a defect that was found rather than a preference:
#
# 1. A per-client entry filters on ``:subject_id`` ITSELF (enforced by ``Entry.__post_init__``).
# 2. A date range is a pair of NULLABLE binds — ``CAST(:date_from AS date) IS NULL OR …`` — so
#    "no bound" is expressible and no string is ever assembled.
# 3. Where the underlying date column is nullable, the filter and the DISPLAYED date are the SAME
#    ``COALESCE`` expression. Filtering on one and showing another drops rows invisibly.
# 4. Draft accounts are excluded, on ``worker.py::_client_accounts_context``'s stated grounds: an
#    unopened account in a client-facing valuation table is a misstatement we would be sending out.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

_AS_AT = Param(
    "as_at",
    "date",
    "As at",
    help="The position as it stood on this date. Leave blank for the latest held.",
    default="today",
)


def _date_range(default_from: str) -> tuple[Param, Param]:
    return (
        Param(
            "date_from",
            "date",
            "From",
            help="Blank for no start. A relative range like -12m stays current on every render.",
            default=default_from,
        ),
        Param("date_to", "date", "To", help="Blank for no end.", default="today"),
    )


VALUATIONS = register(
    Entry(
        key="book_valuations",
        name="Account valuations",
        description="What each of the client's accounts was worth, and when.",
        note=(
            "One row per account: the most recent valuation on or before the as-at date. The "
            "valued-on date is a column because a provider's series can end days apart across "
            "accounts, so a single date on the document would misstate some of them."
        ),
        scope="subject",
        params=(_AS_AT,),
        columns=(
            Column("account_reference", "Account", "text"),
            Column("account_name", "Name", "text"),
            Column("product", "Product", "text"),
            Column("valued_on", "Valued on", "date"),
            Column("value", "Value", "money"),
            Column("cash", "Cash", "money"),
            Column("cost", "Cost", "money"),
            Column("gain", "Gain", "money"),
            Column("currency", "Currency", "text"),
        ),
        # ``platform.valuations`` is a DAILY series — 367 dates per account on the dev book, so the
        # raw series is thousands of rows for one client against a 5,000-row cap, and the resolver
        # reports truncation as an ADVISORY. A statement that stops mid-year behind a warning
        # nobody reads is worse than none, so this is latest-per-account and not the series.
        sql="""
WITH latest AS (
    SELECT DISTINCT ON (a.id)
           a.uid                  AS account_reference,
           a.name                 AS account_name,
           p.product_type::text   AS product,
           v.as_of_date           AS valued_on,
           v.market_value         AS value,
           v.cash_value           AS cash,
           v.book_cost            AS cost,
           v.unrealised_gain      AS gain,
           v.currency             AS currency
      FROM platform.valuations v
      JOIN platform.accounts a
        ON a.id = v.account_id AND a.tenant_id = v.tenant_id
      LEFT JOIN platform.products p
        ON p.id = a.product_id
     WHERE a.tenant_id = :tenant_id
       AND a.contact_id = :subject_id
       AND a.status <> 'draft'
       AND (CAST(:as_at AS date) IS NULL OR v.as_of_date <= CAST(:as_at AS date))
     ORDER BY a.id, v.as_of_date DESC
)
SELECT * FROM latest ORDER BY account_name
""".strip(),
    )
)


TRANSACTIONS = register(
    Entry(
        key="book_transactions",
        name="Transactions",
        description="Money and investments moving in and out of the client's accounts.",
        note=(
            "Reversed movements are left out: the book keeps them with a status of 'deleted' and "
            "the firm's own listing shows them, but a client statement that prints a movement "
            "which was undone states something that did not happen."
        ),
        scope="subject",
        params=_date_range("-12m"),
        columns=(
            Column("date", "Date", "date"),
            Column("account_reference", "Account", "text"),
            Column("account_name", "Name", "text"),
            Column("movement", "Movement", "text"),
            Column("detail", "Detail", "text"),
            Column("investment", "Investment", "text"),
            Column("isin", "ISIN", "text"),
            Column("amount", "Amount", "money"),
            Column("quantity", "Units", "number"),
            Column("currency", "Currency", "text"),
        ),
        # The date is a COALESCE of four columns, copied from ``routers/crm.py``'s listing so the
        # catalogue and the firm's own screen date a movement identically. It appears three times —
        # once shown, twice filtered — and the three MUST stay the same expression.
        sql="""
SELECT COALESCE(t.transaction_date, t.value_date,
                t.posted_at::date, t.synced_at::date)    AS date,
       a.uid                                             AS account_reference,
       a.name                                            AS account_name,
       COALESCE(NULLIF(t.source_type, ''), t.type::text)  AS movement,
       NULLIF(t.source_subtype, '')                       AS detail,
       t.asset_name                                       AS investment,
       t.isin                                             AS isin,
       t.amount                                           AS amount,
       t.quantity                                         AS quantity,
       t.currency                                          AS currency
  FROM platform.transactions t
  JOIN platform.accounts a
    ON a.id = t.account_id AND a.tenant_id = t.tenant_id
 WHERE a.tenant_id = :tenant_id
   AND a.contact_id = :subject_id
   AND a.status <> 'draft'
   AND COALESCE(NULLIF(t.status, ''), 'settled') <> 'deleted'
   AND (CAST(:date_from AS date) IS NULL
        OR COALESCE(t.transaction_date, t.value_date,
                    t.posted_at::date, t.synced_at::date) >= CAST(:date_from AS date))
   AND (CAST(:date_to AS date) IS NULL
        OR COALESCE(t.transaction_date, t.value_date,
                    t.posted_at::date, t.synced_at::date) <= CAST(:date_to AS date))
 ORDER BY 1 DESC, a.uid
""".strip(),
    )
)


CONTRIBUTIONS = register(
    Entry(
        key="book_contributions",
        name="Contributions",
        description="Money the client or their employer has paid in, and the tax relief on it.",
        note=(
            "Cancelled contributions are left out. A booked contribution with no date yet is "
            "dated by when it was booked, so it cannot fall out of a range silently."
        ),
        scope="subject",
        params=_date_range("-12m"),
        columns=(
            Column("date", "Date", "date"),
            Column("account_reference", "Account", "text"),
            Column("account_name", "Name", "text"),
            Column("kind", "Type", "text"),
            Column("frequency", "Frequency", "text"),
            Column("paid_by", "Paid by", "text"),
            Column("amount", "Amount", "money"),
            Column("gross_amount", "Gross", "money"),
            Column("tax_relief", "Tax relief", "money"),
            Column("tax_year", "Tax year", "text"),
            Column("status", "Status", "text"),
            Column("employer", "Employer", "text"),
        ),
        # ``contributions.contact_id`` is NULLABLE and ``account_id`` is not, so the client is
        # reached through the account as a fallback. Filtering on ``c.contact_id`` alone drops
        # every row that was written without one.
        sql="""
SELECT COALESCE(c.contribution_date, c.created_at::date) AS date,
       a.uid                                             AS account_reference,
       a.name                                            AS account_name,
       c.category                                        AS kind,
       c.frequency                                       AS frequency,
       c.paid_by                                         AS paid_by,
       c.amount                                          AS amount,
       c.gross_amount                                    AS gross_amount,
       c.relief_amount                                   AS tax_relief,
       c.tax_year                                        AS tax_year,
       c.status                                          AS status,
       c.employer_name                                   AS employer
  FROM platform.contributions c
  JOIN platform.accounts a
    ON a.id = c.account_id AND a.tenant_id = c.tenant_id
 WHERE c.tenant_id = :tenant_id
   AND COALESCE(c.contact_id, a.contact_id) = :subject_id
   AND c.status <> 'cancelled'
   AND (CAST(:date_from AS date) IS NULL
        OR COALESCE(c.contribution_date, c.created_at::date) >= CAST(:date_from AS date))
   AND (CAST(:date_to AS date) IS NULL
        OR COALESCE(c.contribution_date, c.created_at::date) <= CAST(:date_to AS date))
 ORDER BY 1 DESC
""".strip(),
    )
)


FEES = register(
    Entry(
        key="book_fees",
        name="Charges",
        description="The firm's own schedule of charges.",
        note=(
            "The firm's register, not one client's charges. A per-client charges table is not "
            "offered yet: the account-level link is empty across the whole book, so it would "
            "render blank for every client — which reads as 'no charges apply'."
        ),
        scope="global",
        params=(_AS_AT,),
        columns=(
            Column("reference", "Reference", "text"),
            Column("name", "Charge", "text"),
            Column("category", "Category", "text"),
            Column("frequency", "Frequency", "text"),
            Column("basis", "Basis", "text"),
            Column("charged_on", "Charged on", "text"),
            Column("paid_by", "Paid by", "text"),
            Column("minimum", "Minimum", "money"),
            Column("maximum", "Maximum", "money"),
            Column("currency", "Currency", "text"),
            Column("effective_from", "From", "date"),
            Column("effective_to", "To", "date"),
        ),
        # Both effective dates are nullable and mean "open ended", so the as-at test is a pair of
        # OR-NULL comparisons rather than a BETWEEN, which would drop every open-ended charge.
        sql="""
SELECT fp.external_id      AS reference,
       fp.name             AS name,
       fp.category         AS category,
       fp.frequency::text  AS frequency,
       fp.fee_type::text   AS basis,
       fp.charge_basis     AS charged_on,
       fp.payer            AS paid_by,
       fp.minimum_amount   AS minimum,
       fp.maximum_amount   AS maximum,
       fp.currency         AS currency,
       fp.effective_from   AS effective_from,
       fp.effective_to     AS effective_to
  FROM platform.fee_packages fp
 WHERE fp.tenant_id = :tenant_id
   AND COALESCE(fp.enabled, true)
   AND (CAST(:as_at AS date) IS NULL
        OR ((fp.effective_from IS NULL OR fp.effective_from <= CAST(:as_at AS date))
        AND (fp.effective_to IS NULL OR fp.effective_to >= CAST(:as_at AS date))))
 ORDER BY fp.sort_order NULLS LAST, fp.name
""".strip(),
    )
)
