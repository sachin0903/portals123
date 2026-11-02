"""One contract for the book-sized listings: search, filters, paging and facets.

``pkgs/ui/.../book/listing/filters.ts`` opens by saying filtering is client-side "because these
lists are a firm's book, already fetched in one call. When one outgrows that, ``matches``
becomes the server query and the page components don't change." This is that server query.

**Why it has to move.** A client-side filter over a page is a lie: the toolbar says "Status:
active" and shows you the active rows *out of the hundred you happen to have*, with no
indication that the other nine hundred were never considered. Search is worse, because an empty
result reads as "this firm has no client called Halloway" rather than "not on this page".
``/crm/accounts`` already made search server-side for exactly this reason; the rest follow.

**Three rules, and they are the whole design:**

1. **Paged only on request.** With no ``limit`` the response is the bare list every existing
   caller already parses; with one, the ``{items, total, facets}`` envelope. That rule is
   already in ``crm.py::_page`` and this keeps it, so nothing has to be migrated in lockstep.

2. **``total`` is the count of the FILTERED set, not the visible page and not the whole book.**
   It is what lets the UI say "41 of 1,340" honestly, and what the pager needs to know whether
   there is a next page.

3. **Facets are computed over the filtered-visible set MINUS the facet's own filter.** This is
   the subtle one, and getting it wrong makes a filter a one-way door: if the adviser options
   were derived from rows already narrowed by adviser, picking "Dan Reeve" would leave "Dan
   Reeve" as the only option and there would be no way back to the full list except clearing
   by hand. The same reasoning is written out in ``catalogue.py::provider_facets``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

#: The sentinel the portal's Select uses for "no filter applied" (``filters.ts::ALL``). It
#: arrives on the wire when a user re-selects "All …", so it is treated as absent rather than
#: as a value nothing will ever equal.
ALL = "__all__"

#: Hard ceiling on a page — a caller asking for 10,000 rows is either paginating wrong or
#: trying to dump the book. 200 because that is what ``/crm/accounts`` and ``/crm/holdings``
#: have always clamped to; ``crm.py::_PAGE_LIMIT_MAX`` now reads this rather than restating it,
#: so the two cannot drift (they briefly did, which quietly raised the ceiling to 500).
PAGE_LIMIT_MAX = 200

#: The page size every listing endpoint hands out when the caller names none.
#:
#: 25, matching ``pkgs/ui/book/listing``'s ``PAGE_SIZE`` — the portals' own default — so a listing
#: reads the same whether the client asked for a size or not. It used to be 50 here and 100 on
#: ``/crm/transactions``, which is how the same ledger paged differently depending on which surface
#: opened it. ``PAGE_LIMIT_MAX`` above is still the ceiling; this is only the fallback.
DEFAULT_LIMIT = 25


def clean(value: str | None) -> str | None:
    """A filter value, or None when it is absent/blank/the "all" sentinel."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped == ALL:
        return None
    return stripped


def clean_many(values: str | Sequence[str] | None) -> list[str] | None:
    """The same, for a filter a reader may give SEVERAL values of (`?status=a&status=b`).

    None when nothing usable survives, so an all-blank query string is indistinguishable from no
    filter at all — the predicates below treat None as "unset", and a filter that narrowed to the
    empty set on a stray `&status=` would read as a table with no rows rather than as no filter.
    Order is the caller's and duplicates are dropped.

    A BARE STRING is one value, not a sequence of characters. FastAPI always hands these params in
    as a list, but the routers are plain async functions that other code — and every router test —
    calls directly with `type="organisation"`; iterating that string would filter on "o", "r", "g".
    """
    if values is None:
        return None
    if isinstance(values, str):
        one = clean(values)
        return [one] if one is not None else None
    out: list[str] = []
    for value in values:
        stripped = clean(value)
        if stripped is not None and stripped not in out:
            out.append(stripped)
    return out or None


def clean_range(values: str | Sequence[str] | None) -> list[str] | None:
    """A numeric ``[from, to]`` pair, kept POSITIONALLY.

    ``clean_many`` cannot be used for this and the difference is not cosmetic: it drops blanks and
    deduplicates, so ``["", "3000"]`` — "up to 3000", an open lower bound a reader sets on purpose
    — arrives as ``["3000"]`` and is then read as "from 3000", the opposite question. And
    ``["50", "50"]`` — a range of exactly one value — would collapse to a single open bound.

    So both slots always survive, blank where the bound is absent or unparseable, and the whole
    filter is None only when neither end says anything. Unparseable rather than rejected because
    this arrives from a URL a person can hand-edit; a typo must narrow nothing rather than 422 a
    whole listing. The TypeScript twin is ``applied.ts::withinRange``, which drops a bad bound the
    same way.
    """
    if values is None:
        return None
    raw = [values] if isinstance(values, str) else list(values)
    pair = [(raw[index] if index < len(raw) else "") or "" for index in range(2)]

    def bound(text: str) -> str:
        stripped = text.strip()
        if not stripped or stripped == ALL:
            return ""
        # A NUMBER or a DATE. `float()` alone silently ate every `YYYY-MM-DD`, so a date range
        # cleaned to None and the filter did nothing at all. The PREDICATE decides how to compare.
        try:
            number = float(stripped)
        except ValueError:
            try:
                date.fromisoformat(stripped)
            except ValueError:
                return ""
            return stripped
        # FINITE, or it is not a bound. `float()` also takes "nan"/"inf"/"1e999": `lo = nan` makes
        # every comparison False (narrows nothing) and `inf` empties the book. Dropped like "abc".
        return stripped if math.isfinite(number) else ""

    out = [bound(pair[0]), bound(pair[1])]
    return out if any(out) else None


def within_range(filter_value: str | Sequence[str] | None, value: Any) -> bool:
    """The Python twin of ``applied.ts::withinRange`` — is a figure inside ``[from, to]``?

    Both ends are INCLUSIVE: a reader typing "0 to 5" means the 5s as well, and an exclusive bound
    drops rows they can see themselves sitting on the number they typed.

    A row whose own figure is missing or not a number matches only when nothing is being asked —
    a client with no valued account is not "worth less than a million", it is unknown, and putting
    it in the answer to a value question would overstate the book.

    **A DATE range is read as a date range.** This parsed both bounds with ``float()`` alone while
    ``clean_range`` above deliberately accepted ISO dates, so the pair was dishonest: a cleaned
    ``["2026-01-01", "2026-01-31"]`` reached here, both bounds read as None, and the function
    returned True for every row — the filter did nothing at all, quietly, which the module
    docstring's own words call the worst way for a filter to be wrong. ``documents.py`` and
    ``files.py`` each hand-rolled an ordinal reducer to work around exactly that, in two copies of
    the same fifteen lines. The AXIS is decided by the BOUNDS (they are what the reader typed) and
    then read the same way on both sides, so a date bound never silently compares against a number.
    """
    if filter_value is None:
        return True
    pair = [filter_value, ""] if isinstance(filter_value, str) else list(filter_value)
    raw_lo = pair[0] if len(pair) > 0 else None
    raw_hi = pair[1] if len(pair) > 1 else None
    read = _axis(raw_lo, raw_hi)
    lo = read(raw_lo)
    hi = read(raw_hi)
    if lo is None and hi is None:
        return True
    figure = read(value)
    if figure is None:
        return False
    if lo is not None and figure < lo:
        return False
    return not (hi is not None and figure > hi)


def _axis(*bounds: Any) -> Callable[[Any], float | None]:
    """How to read BOTH sides of a range: as plain numbers, or as calendar days.

    Decided by the bounds rather than by the row, because the bounds are what a person typed and
    the row is whatever the column happens to hold. One date bound makes the whole range a date
    range — a range with a number at one end and a date at the other is not a question anyone
    asks, and the end that cannot be read on the chosen axis is simply dropped, which is the same
    thing `clean_range` does to a bound it cannot parse.
    """
    if any(_as_number(b) is None and _as_ordinal(b) is not None for b in bounds):
        return _as_ordinal
    return _as_number


def _as_number(value: Any) -> float | None:
    """A figure, or None for anything that is not one — blank, prose, or a null column.

    NOT `nan` or `inf` either. They survive `float()` and then poison every comparison silently:
    `figure < nan` and `figure > nan` are both False, so a row would sit inside any range at all.
    See `clean_range`, which drops them on the way in for the same reason.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_ordinal(value: Any) -> float | None:
    """A date — a `date`, a `datetime`, or an ISO string of either — as its calendar DAY's number.

    The DAY, not the moment, and that is the load-bearing part: a bound is a date picked off a
    calendar, so a document created at 23:58 on the last day of a range still has to count as
    inside it. Reducing both sides to the day's ordinal is what makes both ends agree on that.

    Named here rather than in each router because the two that needed it (`documents.py` and
    `files.py`) wrote the same reducer twice and said in both docstrings that they were working
    around this module's numbers-only ranges.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        value = value.date()
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            value = date.fromisoformat(stripped)
        except ValueError:
            try:
                value = datetime.fromisoformat(stripped).date()
            except ValueError:
                return None
    if isinstance(value, date):
        return float(value.toordinal())
    return None


def escape_like(value: str) -> str:
    """Neutralise the LIKE/ILIKE wildcards in a user's search term.

    For the endpoints that push their search down into SQL rather than running ``search`` over
    rows in Python. Without it a subject called "100%" matches every row in the book and a term
    of "_" returns the whole table — and a search box that silently returns everything reads as
    broken data rather than as a filter that found a lot.

    The backslash is escaped FIRST: doing it after the wildcards would re-escape the escapes this
    function just added. Callers must pair the result with ``ESCAPE '\\'`` in the statement,
    because Postgres' LIKE has no escape character unless one is named.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def matches(filter_value: str | Sequence[str] | None, value: Any) -> bool:
    """The Python twin of ``filters.ts::matches`` — unset, or one of the chosen values.

    A LIST is an OR, not an AND: "Active or Onboarding" is the question a book is read with, and
    no row could satisfy two values of one column at once anyway.
    """
    if filter_value is None:
        return True
    text = str(value or "")
    if isinstance(filter_value, str):
        return text == filter_value
    return any(text == wanted for wanted in filter_value)


def matches_any(filter_value: str | Sequence[str] | None, values: Iterable[Any]) -> bool:
    """``filters.ts::matchesAny`` — for multi-valued columns like a client's advisers.

    Both sides may be several: a client advised by Cole and Shaw matches a filter of Shaw or Vale.
    """
    if filter_value is None:
        return True
    texts = [str(value or "") for value in values]
    if isinstance(filter_value, str):
        return any(text == filter_value for text in texts)
    return any(text == wanted for wanted in filter_value for text in texts)


def search(query: str | None, *fields: Any) -> bool:
    """``filters.ts::search`` — case-insensitive contains across a row's searchable text."""
    needle = (query or "").strip().lower()
    if not needle:
        return True
    return any(needle in str(field or "").lower() for field in fields)


#: How one filter narrows a row: `(row, filter value or None) -> keep?`.
Predicate = Callable[[dict[str, Any], "str | Sequence[str] | None"], bool]

#: One facet: the key the UI filters on, and how to read its value(s) off a row.
Reader = Callable[[dict[str, Any]], Any]
Facet = tuple[str, Reader]

#: How one sortable column reads its value off a row.
SortReader = Callable[[dict[str, Any]], Any]

#: The two tables a listing declares. Aliased so a router can annotate its module-level
#: constants without restating the callable signature — an `object` annotation there compiles
#: and then fails at the call, which is the error these exist to prevent.
Filters = dict[str, Predicate]
# The RANGE filter keys. Declared, not inferred: every other filter must have a facet.
Ranges = frozenset[str]
Facets = list[Facet]
#: Sortable columns, by the key the UI sends. A CLOSED SET, and that is the security property:
#: the key arrives from a query string, so anything not declared here is ignored rather than
#: reaching an ORDER BY.
Sorts = dict[str, SortReader]


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _sort_key(value: Any) -> Any:
    """A comparable key for one cell. Types are normalised so a column never mixes them."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).casefold()


def sort_rows(
    rows: Sequence[dict[str, Any]], sorts: Sorts, key: str | None, direction: str | None
) -> list[dict[str, Any]]:
    """Order by a DECLARED column, or leave the endpoint's own order alone.

    An unknown key is ignored rather than refused: `sort` comes off a query string, so a stale
    bookmark naming a column that has since been renamed should show the list in its default order
    rather than a 422 on a page the reader only wanted to look at.

    Blanks sort last in BOTH directions, so reversing a column does not fill the top of the table
    with the rows that have nothing in it.
    """
    read = sorts.get(key or "")
    if read is None:
        return list(rows)
    # PARTITIONED, not reversed. A blank is not "the smallest value" — a client with no valuation
    # is not the poorest — so blanks go last whichever way the column points, and reversing the
    # whole list would have swung them to the top on the first `desc` click. `data-table/sort.ts`
    # partitions the same way, so a page ordered here and one ordered there read alike.
    blanks = [row for row in rows if _is_blank(read(row))]
    rest = [row for row in rows if not _is_blank(read(row))]
    rest.sort(key=lambda row: _sort_key(read(row)), reverse=direction == "desc")
    return rest + blanks


def _values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(v) for v in raw if v not in (None, "")]
    return [str(raw)] if raw != "" else []


def bounds_for(
    rows: Sequence[dict[str, Any]],
    readers: Mapping[str, Callable[[dict[str, Any]], float | None]],
) -> dict[str, list[float]]:
    """How far each RANGE filter's column actually runs, as ``{key: [min, max]}``.

    The facets' equivalent for a continuous column: a range has no vocabulary to list, but it
    does have ends, and a reader cannot judge "between 50 and 3,000" without knowing whether the
    book stops at four thousand or at four million. The portal draws a slider from these.

    Taken over the SAME rows the facets are — the match set with filters lifted — so the track
    does not shrink to whatever is left after narrowing and then move under the handles. A column
    with nothing in it is absent rather than ``[0, 0]``: no ends is not the same as a book worth
    nothing, and a slider from zero to zero is a control that cannot be moved.
    """
    out: dict[str, list[float]] = {}
    for key, read in readers.items():
        seen = [value for value in (read(row) for row in rows) if value is not None]
        if not seen:
            continue
        out[key] = [min(seen), max(seen)]
    return out


def union_facet(options: dict[str, list[str]], key: str, extra: Iterable[str]) -> None:
    """Add values the match set never produced.

    A facet lists what occurs. An adviser filter is a directory: someone with no row in
    this cut is still someone you can ask for, and the browser narrows that list.
    """
    have = list(options.get(key) or [])
    options[key] = sorted(
        {name for name in (*have, *extra) if name and str(name).strip()},
        key=str.casefold,
    )


def facets_for(
    rows: Sequence[dict[str, Any]],
    facets: Sequence[Facet],
    active: Mapping[str, str | Sequence[str] | None],
    predicates: Mapping[str, Callable[[dict[str, Any], str | Sequence[str] | None], bool]],
) -> dict[str, list[str]]:
    """Distinct option values per facet, each computed with its OWN filter lifted.

    ``predicates`` says how each facet narrows a row, so this can re-apply every filter except
    the one it is building options for. That is what keeps a filter reversible — see the module
    docstring. Values come back sorted; the portal supplies the labels, because humanising an
    enum is presentation.

    FOR A CALLER THAT BUILDS NO ENVELOPE. Anything handing its options to ``page`` wants
    ``facets_and_counts`` instead: the tally is computed either way (this discards it), the
    envelope has a ``facet_counts`` slot, and passing only the options is how four listings came
    to ship ``facet_counts: {}`` — the same filter panel drawing a number beside every option on
    ``/crm/contacts`` and none at all on the four beside it.
    """
    options, _ = facets_and_counts(rows, facets, active, predicates)
    return options


def facets_and_counts(
    rows: Sequence[dict[str, Any]],
    facets: Sequence[Facet],
    active: Mapping[str, str | Sequence[str] | None],
    predicates: Mapping[str, Callable[[dict[str, Any], str | Sequence[str] | None], bool]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, int]]]:
    """``facets_for``, and HOW MANY rows carry each value.

    One pass for both, because the count has to be taken over exactly the rows the option list is
    taken over — with this facet's own filter lifted and every other one applied. That is what
    makes a count answer "what would ticking this give me" rather than "how many of what I can
    already see", which is the only version worth showing: the second is always the number already
    on screen, and it reads as a promise the table then breaks.

    A value with a count of ZERO cannot appear here — it is counted from rows that have it — but
    the portal may still show one for a value it knows about from elsewhere, and that zero is the
    answer to "why is this table empty" before anything is narrowed to nothing.
    """
    options: dict[str, list[str]] = {}
    counts: dict[str, dict[str, int]] = {}
    for key, read in facets:
        tally: dict[str, int] = {}
        for row in rows:
            # Every other facet still applies, so the options offered are ones that would
            # actually return something given the rest of the filter state.
            if not all(
                predicate(row, active.get(other))
                for other, predicate in predicates.items()
                if other != key
            ):
                continue
            for value in set(_values(read(row))):
                tally[value] = tally.get(value, 0) + 1
        options[key] = sorted(tally, key=lambda v: v.casefold())
        counts[key] = tally
    return options, counts


def apply(
    rows: Sequence[dict[str, Any]],
    *,
    q: str | None,
    search_fields: Sequence[str],
    filters: Filters,
    facets: Sequence[Facet],
    active: Mapping[str, str | Sequence[str] | None],
    limit: int | None,
    offset: int = 0,
    bounds: Mapping[str, Callable[[dict[str, Any]], float | None]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Search, filter, facet and cut in the one order every listing needs them in.

    Returns ``(rows, envelope)``: the rows to shape, and the paged envelope to spread them back
    into — or ``None`` when the caller passed no ``limit`` and wants the bare list.

    This exists because ONE listing is built from TWO endpoints. The documents library merges
    ``GET /documents`` with ``GET /files``, and its whole contract is that a filter narrows both
    halves or neither: a filter applied to one would silently leave every supporting file in the
    table, which reads as the filter being ignored rather than as half of it working. Written out
    twice, that invariant survives only as long as nobody edits one copy — adding a fourth filter,
    or widening the search to a second field, has to happen in both places on the same day.

    That is not a hypothetical. Both routers DID inline this sequence for a while, each with a
    comment saying ``apply`` "has no way to hand ``page`` a ``bounds`` reader" — true at the time,
    and the wrong fix: the function stating the rule went unreachable while the duplication it
    forbids shipped in both halves, and the two copies immediately drifted (one grew
    ``facet_labels``, and neither grew ``facet_counts``). The missing parameters are ``bounds``
    and the count tally, so they are parameters now. ``bounds`` takes the READERS, not the
    computed ends, because they have to be read over ``found`` — the searched-not-filtered set —
    exactly as the facets are, or a slider's track shrinks to what is left after narrowing and
    then moves under its own handles.

    The order is the load-bearing part and is the reason this is not four separate calls at each
    site: search first (so facets describe what was searched), filter second, facet over the
    SEARCHED-not-filtered set with each facet's own filter lifted (rule 3 above), and cut last (so
    ``total`` counts matches rather than the size of the book).
    """
    needle = clean(q)
    found = [row for row in rows if search(needle, *(row.get(f) for f in search_fields))]
    visible = [
        row
        for row in found
        if all(predicate(row, active.get(key)) for key, predicate in filters.items())
    ]
    if limit is None:
        return visible, None
    options, counts = facets_and_counts(found, facets, active, filters)
    paged = page(
        visible,
        limit=limit,
        offset=offset,
        facets=options,
        facet_counts=counts,
        bounds=bounds_for(found, bounds) if bounds else None,
    )
    envelope = cast(dict[str, Any], paged)
    return list(envelope["items"]), envelope


def page(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int | None,
    offset: int = 0,
    facets: dict[str, list[str]] | None = None,
    facet_counts: dict[str, dict[str, int]] | None = None,
    bounds: dict[str, list[float]] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Cut the page and wrap it — or return the bare list when the caller did not ask to page.

    ``rows`` is the already-filtered set, so ``total`` is the number of matches rather than the
    size of the book. The cut happens last for the same reason it does in ``crm.py``: a total
    taken before filtering would tell the pager there are pages that do not exist.
    """
    if limit is None:
        return list(rows)
    start = max(offset, 0)
    size = min(max(limit, 1), PAGE_LIMIT_MAX)
    return {
        "items": list(rows[start : start + size]),
        "total": len(rows),
        "facets": facets or {},
        "facet_counts": facet_counts or {},
        "bounds": bounds or {},
    }


def like_pattern(q: str | None, *, cap: int = 80) -> str | None:
    """An ``ILIKE`` pattern for a listing search box, or None when there is nothing to search.

    Wildcards in the user's term are escaped so a subject called ``100%`` is not every row.
    Pair with ``ESCAPE '\\'`` in the statement.
    """
    term = clean(q)
    if term is None:
        return None
    return f"%{escape_like(term[:cap])}%"


#: The `_search` list boxes' typo threshold. PostgreSQL's default (0.6) rejects ordinary one-letter
#: misspellings such as "Margret" for "Margaret" (0.55). The Spotlight bar's 0.3 is too loose for a
#: list: a list is sorted by column, not relevance, so a weak match is not pushed below the real
#: ones, and at 0.3 "sipp" matched "Sizeable Trust" (0.4).
FUZZY_THRESHOLD_SQL = "SET LOCAL pg_trgm.word_similarity_threshold = 0.5"


def fuzzy_allowed(term: str) -> bool:
    """Whether ``term`` is long and plain enough for typo matching.

    From four characters: a three-letter term shares half its trigrams with any word that starts
    with the same two letters, and exact and prefix matching already answer it. Letters, digits
    and spaces only: punctuation is taken literally, so ``c_le`` is not a misspelling of Cole."""
    return len(term) >= 4 and all(ch.isalnum() or ch.isspace() for ch in term)


def search_params(q: str | None, *, cap: int = 80) -> dict[str, object]:
    """The binds ``search_sql`` names, folded the way ``search.normalise`` folds ``_search``.

    Lower case with whitespace collapsed, so ``"Ash  Worth"`` compares with the stored text as
    typed. With nothing to search, ``q_like`` is NULL and the predicate matches every row."""
    term = clean(q)
    if term is None:
        return {"q_like": None, "q_norm": None, "q_fuzzy": False}
    norm = " ".join(term[:cap].lower().split())
    return {"q_like": f"%{escape_like(norm)}%", "q_norm": norm, "q_fuzzy": fuzzy_allowed(norm)}


def search_sql(*columns: str, also: Sequence[str] = ()) -> str:
    """A list box's search over one or more ``_search`` columns (the Spotlight bar's columns).

    Each column matches when it contains the term (as the boxes always did) or, from four
    characters, is word-similar to it (``<%``, served by the column's trigram index). ``also``
    names extra text expressions that are not in any ``_search`` and are matched by containment
    only: a task description, an account's product type. Run ``FUZZY_THRESHOLD_SQL`` in the same
    transaction first, or ``<%`` uses PostgreSQL's stricter default.

    Only fixed column expressions are interpolated; the term itself is always bound."""
    matched = [
        f"{column} LIKE :q_like ESCAPE '\\' "
        f"OR (CAST(:q_fuzzy AS boolean) AND CAST(:q_norm AS text) <% {column})"
        for column in columns
    ] + [f"{expr} ILIKE :q_like ESCAPE '\\'" for expr in also]
    return "(CAST(:q_like AS text) IS NULL OR " + " OR ".join(matched) + ")"


def range_sql(key: str, expr: str) -> str:
    """``within_range`` in SQL: ``[from, to]``, both ends inclusive, either end possibly unset.

    A NULL ``expr`` matches no bounded range at all, which is the same call ``within_range``
    makes — an unvalued client is not "worth under a million", it is unknown. So a column that
    COALESCEs its nulls away must hand the UNCOALESCED expression in, or the whole unpriced tail
    is swept into the bottom bucket.
    """
    lo, hi = f":{key}_min", f":{key}_max"
    return (
        f"((CAST({lo} AS float8) IS NULL OR ({expr}) >= CAST({lo} AS float8))"
        f" AND (CAST({hi} AS float8) IS NULL OR ({expr}) <= CAST({hi} AS float8)))"
    )


def range_params(key: str, values: str | Sequence[str] | None) -> dict[str, float | None]:
    """The two binds ``range_sql`` names, from the raw query parameter.

    A bound that is not a finite number ends up NULL — the same "a typo narrows nothing" rule
    ``clean_range`` applies, extended to the ISO dates it accepts for the date ranges elsewhere,
    which are not a question a numeric column can answer.
    """
    pair = clean_range(values) or ["", ""]
    return {f"{key}_min": _as_number(pair[0]), f"{key}_max": _as_number(pair[1])}


def order_sql(
    sort: str | None,
    direction: str | None,
    columns: dict[str, str],
    default: str,
) -> str:
    """``ORDER BY`` for a DECLARED column, blanks last in both directions.

    ``columns`` is a closed set of our own SQL expressions — never request text. An unknown
    ``sort`` keeps the endpoint's default rather than 422ing a stale bookmark.
    """
    expr = columns.get((sort or "").strip())
    if expr is None:
        return default
    desc = (direction or "").strip().lower() == "desc"
    return f"(({expr}) IS NULL) ASC, ({expr}) {'DESC' if desc else 'ASC'} NULLS LAST"


def clamp_limit(limit: int | None) -> int | None:
    """None stays None (bare list). Anything else is forced into 1..``PAGE_LIMIT_MAX``."""
    if limit is None:
        return None
    return min(max(limit, 1), PAGE_LIMIT_MAX)
