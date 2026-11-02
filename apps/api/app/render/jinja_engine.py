"""Sandboxed Jinja2 environment for document rendering (ported from platform-1).

Rules:
  - SandboxedEnvironment — no arbitrary attribute/item access on builtins.
  - autoescape enabled (HTML context).
  - No filesystem loader — templates are passed as strings (the template version's saved HTML).
  - ChainableUndefined — ``{{ missing.attr }}`` renders as "" instead of raising, so sparse inputs
    still render.
  - ``finalize`` maps ``None`` to "" — see :func:`_finalize`. A *present but null* value is the
    other half of the sparse-input story, and Jinja's default would print the literal "None".

The platform-1 calc-library globals (``scenario_projections`` etc.) are intentionally not wired here
yet — they're an enrichment for later; templates that don't call them render fine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from jinja2 import ChainableUndefined, Undefined, meta
from jinja2 import filters as jinja_filters
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

_env: SandboxedEnvironment | None = None


#: A name this engine can resolve as a variable: letters, digits and underscores.
#:
#: NO DOTS. A dot is a scope path, so a variable called ``brand.support_email`` does not sit beside
#: the brand scope — it replaces it, and an author could put any address under the firm's name.
#:
#: NO HYPHENS, which is the trap, because a hyphenated name READS as an identifier. Jinja parses
#: ``{{ client-ref }}`` as ``client - ref``; both operands are :class:`ChainableUndefined`, and its
#: arithmetic raises ``UndefinedError``. Such a template publishes clean and then wedges the whole
#: notification tail, because the consumer reads a batch and advances its cursor in ONE transaction
#: — so the raise rolls the cursor back and every firm's mail stops behind it.
#:
#: DECLARED HERE, ONCE, and read by every layer that accepts a name: a template's own declarations
#: (``email_templates_service``), a flow action's merge fields (``flows.effects``) and a
#: notification rule's supplied values (``notifications.rules_service``). It belongs with the engine
#: because it is a fact ABOUT the engine — what it can resolve — and all three already import this
#: module, so there is no new dependency in reading it from here. Three copies of it drifted once,
#: and the loosest accepted a name the editor could not save.
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _expand_dotted_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Expand flat dotted keys to nested dicts so Jinja2 can resolve them.

    ``{'client.full_name': 'Jane'}`` → ``{'client': {'full_name': 'Jane'}}``. Non-dotted keys pass
    through; pre-nested dicts are merged.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if "." in str(key):
            parts = str(key).split(".")
            d = result
            for part in parts[:-1]:
                if part not in d or not isinstance(d[part], dict):
                    d[part] = {}
                d = d[part]
            d[parts[-1]] = value
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def _finalize(value: Any) -> Any:
    """Map ``None`` to "" for every ``{{ … }}`` output, leaving everything else untouched.

    THE BUG THIS CLOSES: Jinja stringifies a *defined* ``None`` as the literal "None". It is not
    Undefined, so ``ChainableUndefined`` never sees it and ``render_with_diagnostics`` correctly
    does not flag it — yet it renders four visible characters into a client-facing document or
    email. Many merge-context keys are legitimately nullable (an unset client phone; ``first_name``
    on a password reset, which ``routers/internal.py`` hard-codes to None on purpose; the adviser's
    optional covering ``message``; ``portal_url`` when the deployment has no client-portal host —
    that one lands inside a CTA ``href``). The shipped templates dodge it by hand
    (``{{ first_name or 'there' }}``, ``{% if message %}``), but a FIRM-AUTHORED template has no
    reason to, so the fix belongs here, once, where every nullable key present and future is
    covered automatically.

    Deliberately narrow: only ``None``. ``0``, ``False`` and ``[]`` still render as themselves, and
    an Undefined passes through unchanged so the ``missing`` diagnostic keeps working — a null value
    is *present but empty*, which is a different thing from an unsourced token and must not start
    being reported as a typo. ``{% if %}``/``or`` see the raw value (finalize applies to output
    only), so the defaults' own guards behave exactly as before.
    """
    return "" if value is None else value


# Each filter guards ``None`` itself, because a filter is the SECOND door to the same bug: finalize
# only sees the end of the expression, so ``{{ due_date | date }}`` on a null date would hand
# finalize the string "None" that ``str(value)`` had already produced inside the filter. "" is the
# right answer for a formatter with nothing to format.
def _fmt_currency(value: object, symbol: str = "£", decimals: int = 2) -> str:
    if value is None:
        return ""
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    sign = "-" if n < 0 else ""
    return f"{sign}{symbol}{abs(n):,.{decimals}f}"


def _fmt_percentage(value: object, decimals: int = 2) -> str:
    if value is None:
        return ""
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    return f"{n:.{decimals}f}%"


def _fmt_number(value: object, decimals: int = 2) -> str:
    if value is None:
        return ""
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    return f"{n:,.{decimals}f}"


#: The default, named because `_render_default_date` special-cases exactly it.
_DATE_FMT = "%d %B %Y"


def _render_default_date(value: date | datetime, fmt: str) -> str:
    """`strftime` zero-pads `%d` on every platform, and `%-d`/`%#d` are platform-specific, so the
    default format is built rather than formatted: British prose is "1 October 2026", where
    "01 October 2026" reads as a form field. Any other `fmt` the author passed is honoured as given.
    """
    if fmt != _DATE_FMT:
        return value.strftime(fmt)
    return f"{value.day} {value.strftime('%B %Y')}"


def _fmt_date(value: object, fmt: str = _DATE_FMT) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return _render_default_date(value, fmt)
    if isinstance(value, str):
        for parser_fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return _render_default_date(datetime.strptime(value, parser_fmt), fmt)
            except ValueError:
                continue
    return str(value)


def _fmt_join(value: object, separator: str = ", ") -> str:
    if value is None:
        return ""
    # An UNSOURCED collection is stringified rather than joined, for the reason
    # :func:`_sum_currency` gives at length: an Undefined ITERATES AS EMPTY, so joining it answers
    # "" without ever stringifying the token and ``render_with_diagnostics`` records no miss beside
    # it. A misspelled list would then read exactly like a list with nothing in it.
    if isinstance(value, Undefined):
        return str(value)
    if not hasattr(value, "__iter__") or isinstance(value, str):
        return str(value)
    return separator.join(str(v) for v in value)


def _fmt_truncate(value: object, length: int = 100, suffix: str = "…") -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= length else s[:length].rstrip() + suffix


def _fmt_lower(value: object) -> str:
    if value is None:
        return ""
    return str(value).lower()


def _fmt_paragraphs(value: object) -> Markup:
    """Free text a person TYPED, as safe HTML: escaped, blank lines to ``<p>``, newlines to
    ``<br>``.

    The adviser's covering note on a document send is the case this exists for. ``{{ message }}``
    alone collapses it: a note typed as three paragraphs arrives as one run-on sentence, because
    HTML does not honour newlines. That was a real divergence — the send path did this in Python
    (``email._paragraphs``) while the editor prefill emitted the bare token, so a firm publishing
    the prefill unchanged silently flattened every note. Making it a FILTER is what lets one
    implementation serve both, and lets a firm's own authored template have it too.

    Returns ``Markup``, so autoescape leaves the paragraph tags alone; the text INSIDE them is
    escaped here, so the whole is safe by construction rather than by the caller remembering. Blank
    (or whitespace-only) input yields "", which is falsy — so ``{% if message | paragraphs %}`` is
    the honest guard for "is there a note", where ``{% if message %}`` calls a box of spaces a note.
    """
    if value is None:
        return Markup("")
    blocks = [b.strip() for b in str(value).replace("\r\n", "\n").split("\n\n") if b.strip()]
    return Markup("").join(
        Markup('<p style="margin:0 0 12px">{}</p>').format(
            escape(block).replace("\n", Markup("<br>"))
        )
        for block in blocks
    )


def _fmt_fallback(value: object, default: object = "") -> object:
    """The value, or ``default`` when the value is genuinely ABSENT.

    THIS IS NOT ``or``, AND THAT IS THE WHOLE POINT. Jinja's ``{{ x or 'y' }}`` fires on anything
    falsy, so a real ``0``, a ``False`` and an empty list all get replaced — which for a money or a
    count field means a fallback placeholder standing where a true zero belongs. An email telling a
    client their balance is "—" when it is £0.00 is worse than one that says nothing.

    Absent means ``None``, Jinja's Undefined, or a string with nothing but whitespace in it. Every
    other value is the answer, including the falsy ones.

    ORDER MATTERS AND THE EDITOR RELIES ON IT: a format runs FIRST, so
    ``{{ amount | currency | fallback('—') }}`` asks the fallback about ``"£0.00"`` — a non-empty
    string — rather than about the number 0. That is what lets a fallback be offered on every field
    rather than only on the ones carrying no format. ``field-filters.ts::fieldToken`` builds the
    chain in that order; ``parseFieldToken`` refuses any other, so a template cannot be saved with
    the two the wrong way round.

    ``default`` is author-typed text and is returned unescaped-but-unsafe, i.e. as a plain ``str``,
    so autoescape treats it exactly as it treats the value it stands in for.
    """
    if value is None or isinstance(value, Undefined):
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


def _sum_currency(items: object, field: str, symbol: str = "£") -> str:
    """Sum a numeric field across a list of dicts/objects and format as currency.

    A ``None`` collection totals to "", an EMPTY one to "£0.00", and the difference is the whole
    care taken here. A null means the send site supplied no collection at all — there are no
    figures, and a confident "£0.00" asserts a total we do not hold, which in a client-facing
    document is a wrong number rather than a missing one. A list that exists and has nothing in it
    genuinely totals zero: a client with an account and no transactions this quarter is owed
    "£0.00", not a blank. So the guard is on ``None`` alone and never on falsiness.

    It is also what keeps ``fallback`` reachable on a total. A format runs FIRST (see
    :func:`_fmt_fallback`), so ``{{ items | sum_currency('amount') | fallback('—') }}`` only ever
    asks the fallback about the string the format produced — and a non-empty one for an absent list
    leaves an author no way at all to say "—" where there are no figures.

    An UNSOURCED collection (a misspelled token) is stringified rather than merely blanked, because
    ``str()`` on an Undefined is the door ``render_with_diagnostics`` records a miss through — the
    same one ``currency`` and ``date`` go through when their ``Decimal`` conversion falls back to
    ``str(value)``. A figure answered silently here would be a wrong number with no ``missing``
    entry beside it, which is the one shape of this bug an author cannot find.
    """
    if items is None:
        return ""
    if isinstance(items, Undefined):
        return str(items)
    total = Decimal(0)
    if not hasattr(items, "__iter__"):
        return _fmt_currency(0, symbol)
    for item in items:
        try:
            raw = item[field] if isinstance(item, dict) else getattr(item, field, 0)
            total += Decimal(str(raw))
        except (InvalidOperation, KeyError, TypeError):
            pass
    return _fmt_currency(total, symbol)


#: Jinja builtins an editor offers that are NOT absence-safe, wrapped by :func:`_null_safe`.
#:
#: Every filter this module registers answers "" for an absent value, which is what lets a template
#: reference a field the send site did not supply and print a blank rather than a claim. Jinja's own
#: filters do not, and the two kinds of absence fail differently. On a NULL: ``abs`` and ``round``
#: raise ``TypeError``, ``upper`` yields the literal "NONE", ``title``/``capitalize`` yield "None",
#: and ``int`` yields "0" — a figure asserting a number we do not hold, the defect
#: :func:`_sum_currency` documents. On an UNSOURCED token: ``abs`` and ``round`` raise ``TypeError``
#: (an Undefined defines neither ``__abs__`` nor ``__round__``) and ``int`` raises
#: ``UndefinedError`` — so the very expression the last paragraph keeps these registered for,
#: ``{{ x | round(2) }}``, is the one that failed the whole render for a field this send happens not
#: to carry. Which is the wedge the module header opens with.
#:
#: SHADOWED, NOT REPLACED. Each wrapper delegates to Jinja's own filter for every value it has, so
#: behaviour is identical wherever a value exists. That is the difference between these and
#: ``truncate``/``join``, which shadow a builtin with a DIFFERENT SIGNATURE and therefore genuinely
#: diverge — a hazard these must not be read as extending.
#:
#: Registered rather than dropped from the editors' menus, because a Jinja builtin is reachable by
#: typing it: narrowing a menu leaves ``{{ x | round(2) }}`` failing the render for anyone who hand-
#: writes it. ``_finalize`` cannot help either — it maps a None to "" on the OUTPUT of the whole
#: expression, by which point the builtin has already raised or stringified.
_NULL_UNSAFE_BUILTINS = ("upper", "title", "capitalize", "abs", "round", "int")


def _null_safe(name: str) -> Callable[..., object]:
    """Jinja's ``name`` filter, answering "" for an absent value instead of raising or printing
    "None"."""
    inner = jinja_filters.FILTERS[name]

    def guarded(value: object, *args: object, **kwargs: object) -> object:
        # ``str()`` and not a bare "" for an Undefined: :func:`render_with_diagnostics` records a
        # miss only when something stringifies the token, so returning a literal here would produce
        # the blank AND drop the field out of the ``missing`` list — costing the unsourced-token
        # warning the very case it exists to report.
        if isinstance(value, Undefined):
            return str(value)
        if value is None:
            return ""
        return inner(value, *args, **kwargs)

    guarded.__name__ = f"null_safe_{name}"
    return guarded


def get_env() -> SandboxedEnvironment:
    global _env
    if _env is None:
        env = SandboxedEnvironment(
            autoescape=True, undefined=ChainableUndefined, finalize=_finalize
        )
        for _name in _NULL_UNSAFE_BUILTINS:
            env.filters[_name] = _null_safe(_name)
        env.filters["currency"] = _fmt_currency
        env.filters["percentage"] = _fmt_percentage
        env.filters["number"] = _fmt_number
        env.filters["date"] = _fmt_date
        env.filters["join_list"] = _fmt_join
        env.filters["join"] = _fmt_join
        env.filters["truncate_text"] = _fmt_truncate
        env.filters["truncate"] = _fmt_truncate
        env.filters["lower"] = _fmt_lower
        env.filters["paragraphs"] = _fmt_paragraphs
        env.filters["sum_currency"] = _sum_currency
        env.filters["fallback"] = _fmt_fallback
        _env = env
    return _env


def render(template_str: str, variables: dict[str, Any]) -> str:
    """Render a Jinja2 template string against ``variables`` and return the HTML string. Dotted keys
    are expanded to nested dicts first (so ``{{ client.name }}`` resolves)."""
    return get_env().from_string(template_str).render(**_expand_dotted_keys(variables))


def render_text(template_str: str, variables: dict[str, Any]) -> str:
    """Render a template as PLAIN TEXT — same environment, same filters, autoescape OFF.

    For an email SUBJECT, which is a header value and not HTML: a client called "Ridley & Co" must
    show up in the inbox as "Ridley & Co", not "Ridley &amp; Co". Every email path renders its
    subject through here (``email_render.render_default``/``render_published_email``,
    ``email_preview``) so the three cannot disagree about it. Bodies keep autoescape, obviously.
    """
    return (
        get_env()
        .overlay(autoescape=False)
        .from_string(template_str)
        .render(**_expand_dotted_keys(variables))
    )


def render_with_diagnostics(
    template_str: str, variables: dict[str, Any], *, autoescape: bool = True
) -> tuple[str, list[str]]:
    """Render, and capture the merge tokens that resolved to Undefined *during* rendering — the
    silent blanks — so a caller can warn "these fields came out empty" instead of shipping them
    unnoticed.

    Complements the static publish/generate gate: this catches attribute-level misses the root-level
    catalogue can't (e.g. ``{{ one.fees.typo }}`` — ``one`` is known but ``.typo`` isn't). A token
    whose value is ``""`` **or ``None``** (a genuinely empty field) is DEFINED, so it is never
    flagged — only truly unsourced tokens are. That distinction is load-bearing: ``_finalize`` now
    renders a null as "", and if a null started being reported here we would be telling an author
    their token is misspelled when it is correct and simply has no value for this send.

    ``autoescape=False`` is for a subject line — see :func:`render_text`, which this mirrors for the
    preview, where the same diagnostics are wanted on the plain-text half too."""
    missing: set[str] = set()

    class _Recording(ChainableUndefined):
        __slots__ = ()

        def _seen(self) -> None:
            name = self._undefined_name
            if name:
                missing.add(str(name))

        def __str__(self) -> str:
            self._seen()
            return ""

        def __html__(self) -> str:
            self._seen()
            return ""

    env = get_env().overlay(undefined=_Recording, autoescape=autoescape)
    html = env.from_string(template_str).render(**_expand_dotted_keys(variables))
    return html, sorted(missing)


def undeclared_variables(template_str: str) -> list[str]:
    """The top-level variable names a template references (e.g. ``{{ client.name }}`` → ``client``).
    Used to warn when the collected inputs won't satisfy the template. Empty on a syntax error."""
    try:
        return sorted(meta.find_undeclared_variables(get_env().parse(template_str)))
    except Exception:  # noqa: BLE001 — a malformed template just yields no names here
        return []


def unknown_variables(template_str: str, known: set[str]) -> list[str]:
    """Referenced top-level variables that aren't in ``known`` (the template's variable catalogue) —
    i.e. likely typos or hand-typed tokens that won't populate at render time. Loop-bound names
    (``{% for row in ... %}`` → ``row``) are already excluded by Jinja's own scoping above."""
    return [v for v in undeclared_variables(template_str) if v not in known]
