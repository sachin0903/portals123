"""A null merge value must render EMPTY, never the literal "None".

THE BUG. Jinja stringifies a *defined* ``None`` as ``"None"``. It is not Undefined, so
``ChainableUndefined`` never intercepts it and ``render_with_diagnostics`` correctly does not flag
it — yet four visible characters land in a client-facing email or document. Several send-site
context keys are legitimately nullable (``first_name``, which ``routers/internal.py`` hard-codes to
None for password resets (no account-existence oracle); ``sent_by``; the adviser's optional covering
``message``; ``due_date``; ``portal_url``, which lands inside a CTA ``href``;
``brand.support_email``). The shipped defaults dodge it BY HAND
(``{{ first_name or 'there' }}``, ``{% if message %}``) — a firm-authored template has no reason to.

THE FIX, pinned here: ``jinja_engine._finalize`` maps ``None`` to "" for the ONE shared environment,
plus a ``None`` guard in each formatting filter (the second door — a filter stringifies before
finalize ever sees the value). These tests drive the real seams: the environment itself, the email
render path (``email_render.render_or_default``), and the preview.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from app.email import defaults as email_defaults
from app.email import preview as email_preview
from app.email import render as email_render
from app.email.transport import Brand
from app.render import jinja_engine

# --------------------------------------------------------------------------------------------
# The render boundary itself
# --------------------------------------------------------------------------------------------


def test_a_null_token_renders_empty_not_the_literal_none() -> None:
    assert jinja_engine.render("<p>Hi {{ first_name }},</p>", {"first_name": None}) == "<p>Hi ,</p>"


@pytest.mark.parametrize(
    "key",
    [
        # Every nullable key the wired send sites can pass as None. Named individually rather
        # than looped over a set so a new nullable key is a new line here, not a silent pass.
        "first_name",
        "sent_by",
        "message",
        "due_date",
        "portal_url",
        "set_password_url",
        "document_name",
        "portal_label",
        "task_title",
        "task_description",
        "button_label",
        "button_url",
    ],
)
def test_every_nullable_send_site_key_renders_empty(key: str) -> None:
    out = jinja_engine.render(f"[{{{{ {key} }}}}]", {key: None})
    assert out == "[]", f"{key}=None rendered {out!r}"


def test_nested_brand_support_email_renders_empty() -> None:
    """``brand`` is the one nested context root, and ``support_email`` is nullable on ``Brand`` —
    the footer address every shipped email carries. Both the dotted-key and pre-nested shapes."""
    tpl = "<a href='mailto:{{ brand.support_email }}'>{{ brand.support_email }}</a>"
    expect = "<a href='mailto:'></a>"
    assert jinja_engine.render(tpl, {"brand": {"support_email": None}}) == expect
    assert jinja_engine.render(tpl, {"brand.support_email": None}) == expect


def test_a_null_portal_url_yields_an_empty_href_not_href_none() -> None:
    """The worst instance: ``portal_url`` is ``settings.client_portal_base_url or None`` and lands
    inside the CTA ``href``. ``href="None"`` is a live link to a relative path called None."""
    html = jinja_engine.render(
        '<a href="{{ portal_url }}">Open your portal</a>', {"portal_url": None}
    )
    assert html == '<a href="">Open your portal</a>'
    assert "None" not in html


def test_finalize_is_narrow_falsy_values_still_render_themselves() -> None:
    """Only ``None`` is mapped. ``0``/``False``/``[]`` are real values an author may want printed —
    a "0 documents outstanding" line must not silently blank."""
    ctx: dict[str, Any] = {"a": 0, "b": False, "c": [], "d": "", "e": 0.0}
    assert jinja_engine.render("{{ a }}|{{ b }}|{{ c }}|{{ d }}|{{ e }}", ctx) == "0|False|[]||0.0"


def test_conditionals_and_default_expressions_still_see_the_raw_null() -> None:
    """``finalize`` applies to OUTPUT only, so the shipped defaults' own guards are untouched: the
    ``{% if message %}`` block still hides and ``{{ first_name or 'there' }}`` still says there."""
    assert jinja_engine.render("{% if message %}NOTE{% endif %}", {"message": None}) == ""
    assert jinja_engine.render("{{ first_name or 'there' }}", {"first_name": None}) == "there"
    assert jinja_engine.render("{{ x if x else 'fallback' }}", {"x": None}) == "fallback"


def test_autoescaping_is_unaffected_by_finalize() -> None:
    """finalize runs BEFORE escaping, so a hostile value must still be escaped."""
    out = jinja_engine.render("{{ v }}", {"v": "<script>x</script>"})
    assert out == "&lt;script&gt;x&lt;/script&gt;"


#: One null case per registered filter. Expressions rather than bare names, because a filter's
#: required arguments cannot be derived — ``sum_currency`` needs the field to total. The companion
#: test scans ``get_env`` for the registered names and holds this list complete against them.
_NULL_FILTER_CASES = [
    "{{ x | date }}",
    "{{ x | currency }}",
    "{{ x | number }}",
    "{{ x | percentage }}",
    "{{ x | join_list }}",
    "{{ x | join }}",
    "{{ x | truncate_text }}",
    "{{ x | truncate }}",
    "{{ x | lower }}",
    # `paragraphs` is the adviser's covering note, which is nullable on every send that carries
    # one — and it returns Markup, so a stray "None" here would be UNESCAPED into the email.
    "{{ x | paragraphs }}",
    # `sum_currency` is the case that bites hardest: a null fails its `__iter__` test, and
    # formatting a zero there prints "£0.00" — a CONFIDENT FIGURE on a send that holds no figures,
    # which is a wrong number rather than a missing one. An empty LIST is the opposite case and
    # still totals zero — see `test_summing_nothing_is_blank_but_summing_an_empty_list_is_zero`.
    "{{ x | sum_currency('amount') }}",
    # `fallback` completes the twelve. Its answer for a null IS its `default`, "" unless the author
    # typed one, so it belongs ON this list rather than beside it as a special case.
    "{{ x | fallback }}",
    # The six shadowed Jinja built-ins (`_NULL_UNSAFE_BUILTINS`). Two of them RAISE on a null
    # unguarded, so these cases fail the whole render rather than merely printing the wrong thing.
    "{{ x | upper }}",
    "{{ x | title }}",
    "{{ x | capitalize }}",
    "{{ x | abs }}",
    "{{ x | round(2) }}",
    "{{ x | int }}",
]


@pytest.mark.parametrize("expr", _NULL_FILTER_CASES)
def test_the_formatting_filters_are_null_safe_too(expr: str) -> None:
    """The second door: a filter calls ``str(value)`` internally, so finalize would only ever see
    the string "None" it had already produced. ``{{ due_date | date }}`` is the realistic case
    (``lower`` produced the even sneakier "none")."""
    assert jinja_engine.render(expr, {"x": None}) == ""


@pytest.mark.parametrize("expr", _NULL_FILTER_CASES)
def test_the_filters_are_safe_on_an_UNSOURCED_token_too_not_only_a_null(expr: str) -> None:
    """The third door, and the one a null case cannot see: the field is not in the context at all.

    A null and an unsourced token arrive as different objects — ``None`` against an Undefined — so a
    guard written for one does not cover the other, and ``abs``/``round``/``int`` RAISED here while
    every case above passed. A raise is the expensive failure: the notification consumer reads a
    batch and advances its cursor in one transaction, so one unrenderable send stops every firm's
    mail behind it. The realistic author is the one who hand-typed ``{{ x | round(2) }}`` — the
    exact expression ``_NULL_UNSAFE_BUILTINS`` stays registered for."""
    assert jinja_engine.render(expr, {}) == ""


#: The filters that answer an absent field WITHOUT recording the miss, and may.
#:
#: ``fallback`` is the author saying "print this instead", so the token is handled rather than
#: unsourced and reporting it would contradict the instruction. Everything else must record, which
#: is why this list is an allowlist and not a note.
_UNREPORTED_ON_PURPOSE = {"fallback"}


@pytest.mark.parametrize("expr", _NULL_FILTER_CASES)
def test_an_unsourced_token_is_still_reported_missing_through_every_filter(expr: str) -> None:
    """A filter must not blank an unsourced token SILENTLY.

    ``render_with_diagnostics`` records a miss only when something stringifies the token, so a
    filter that returns "" before touching its value produces the blank and drops the field out of
    ``missing`` — the shape :func:`_sum_currency`'s docstring calls the one an author cannot find.
    ``join`` had it: an Undefined iterates as EMPTY, so a misspelled list read exactly like a list
    with nothing in it."""
    filter_name = expr.split("|")[1].strip().split("(")[0].split()[0]
    html, missing = jinja_engine.render_with_diagnostics(expr, {})
    assert html == ""
    assert missing == ([] if filter_name in _UNREPORTED_ON_PURPOSE else ["x"])


def test_every_registered_filter_has_a_null_case() -> None:
    """The list above must name every filter the environment registers.

    The cases are hand-written, and an omission in a hand-written list is SILENT: a filter with no
    null case ships whatever it happens to answer, which for a formatter is either the word "None"
    or a made-up figure in a client's email. So the COVERAGE is derived even though the cases cannot
    be — a filter added to ``get_env`` fails here, in the suite that owns null behaviour.

    Read as source TEXT rather than off ``get_env().filters``, because ours deliberately shadow
    Jinja built-ins of the same name (``join``, ``truncate``, ``lower``, and the six in
    ``_NULL_UNSAFE_BUILTINS``): a set difference against a bare environment cancels exactly those
    out and quietly stops requiring them.

    The loop-registered names are read from the tuple, not from the loop body, because the whole
    point of registering them is that they are NOT null-safe on their own — a name added there and
    nowhere here would ship the behaviour this suite exists to refuse.
    """
    registered = set(
        re.findall(r'env\.filters\["(\w+)"\]', inspect.getsource(jinja_engine.get_env))
    ) | set(jinja_engine._NULL_UNSAFE_BUILTINS)
    covered = {m.group(1) for e in _NULL_FILTER_CASES if (m := re.search(r"\|\s*(\w+)", e))}
    assert registered == covered, (
        f"filters with no null case: {sorted(registered - covered)}; "
        f"cases for no such filter: {sorted(covered - registered)}"
    )


def test_summing_nothing_is_blank_but_summing_an_empty_list_is_zero() -> None:
    """The distinction ``sum_currency``'s null guard turns on, and the reason it is a guard on
    ``None`` alone rather than on falsiness.

    A ``None`` is the send site supplying no collection: there are no figures, and "£0.00" asserts
    a total we do not hold. An empty LIST is a collection that exists and has nothing in it, whose
    total genuinely IS zero — a client with an account and no transactions this quarter is owed
    "£0.00", not a blank. Conflating the two would break one case to fix the other.

    It is also what makes ``fallback`` reachable on a total. Formats run first
    (``_fmt_fallback``'s documented order), so ``| sum_currency | fallback('—')`` can only ever see
    the string the format produced: while that string was "£0.00" for an absent list, no author
    could write "—" for "no figures" at all.
    """
    assert jinja_engine.render("{{ x | sum_currency('amount') }}", {"x": []}) == "£0.00"
    assert jinja_engine.render("{{ x | sum_currency('amount') }}", {"x": None}) == ""
    rows = [{"amount": "10.50"}, {"amount": 2}]
    assert jinja_engine.render("{{ x | sum_currency('amount') }}", {"x": rows}) == "£12.50"
    # The chain an author actually writes, both ways round the branch.
    chain = "{{ x | sum_currency('amount') | fallback('—') }}"
    assert jinja_engine.render(chain, {"x": None}) == "—"
    assert jinja_engine.render(chain, {"x": []}) == "£0.00"


def test_an_unsourced_total_is_blank_and_still_reported_missing() -> None:
    """A MISSPELLED collection is the sharpest edge of the same defect, and the one the ``missing``
    diagnostic has to keep catching.

    ``{{ transactons | sum_currency('amount') }}`` sources nothing, so there is nothing to total.
    Answering "£0.00" states a figure — and because a filter that never stringifies its input gives
    ``render_with_diagnostics`` nothing to record, it states it with no ``missing`` entry either: a
    wrong number that neither a gate nor the author's own warning list can see. ``currency`` and
    ``date`` already answer "" AND report, via the ``str(value)`` their ``Decimal`` conversion falls
    through to, so this is the surrounding behaviour rather than a rule invented for one filter.
    """
    html, missing = jinja_engine.render_with_diagnostics(
        "{{ transactons | sum_currency('amount') }}", {"transactions": [{"amount": 5}]}
    )
    assert html == ""
    assert missing == ["transactons"]
    # And a null is still NOT a typo — present but empty keeps its distinction from unsourced.
    assert jinja_engine.render_with_diagnostics(
        "{{ x | sum_currency('amount') }}", {"x": None}
    ) == ("", [])


def test_the_filters_still_format_real_values() -> None:
    """The null guard must not have swallowed the formatting itself."""
    assert jinja_engine.render("{{ x | currency }}", {"x": "1234.5"}) == "£1,234.50"
    assert jinja_engine.render("{{ x | date }}", {"x": "2026-05-14"}) == "14 May 2026"
    assert jinja_engine.render("{{ x | percentage }}", {"x": "1.25"}) == "1.25%"
    assert jinja_engine.render("{{ x | number }}", {"x": "1000"}) == "1,000.00"
    assert jinja_engine.render("{{ x | join_list }}", {"x": ["a", "b"]}) == "a, b"
    assert jinja_engine.render("{{ x | lower }}", {"x": "ABC"}) == "abc"
    # `paragraphs` escapes its input and emits Markup, so the tags survive and the content does not.
    assert jinja_engine.render("{{ x | paragraphs }}", {"x": "a\n\nb & <c>"}) == (
        '<p style="margin:0 0 12px">a</p><p style="margin:0 0 12px">b &amp; &lt;c&gt;</p>'
    )
    assert jinja_engine.render("{{ x | paragraphs }}", {"x": "   \n  "}) == ""
    # `fallback` returns the value untouched when there IS one, falsy ones included — the whole
    # reason it is not `or`. A true £0.00 must survive a fallback offered on the same field.
    assert jinja_engine.render("{{ x | fallback('—') }}", {"x": "£0.00"}) == "£0.00"
    assert jinja_engine.render("{{ x | currency | fallback('—') }}", {"x": 0}) == "£0.00"


# --------------------------------------------------------------------------------------------
# The `missing` diagnostic must keep telling the two apart
# --------------------------------------------------------------------------------------------


def test_a_null_value_is_not_reported_missing_but_an_unsourced_token_is() -> None:
    """The distinction the fix must not blur. ``missing`` means "this token has no source" — an
    author reads it as "you misspelled it". A null-valued key is present and correctly spelled; it
    simply has no value for this send. Reporting it would send authors hunting a typo that isn't
    there, while a genuinely unsourced token must still surface."""
    html, missing = jinja_engine.render_with_diagnostics(
        "{{ first_name }}|{{ mystery }}|{{ brand.support_email }}|{{ brand.typo }}",
        {"first_name": None, "brand": {"support_email": None}},
    )
    assert html == "|||"  # all four rendered blank
    assert missing == ["mystery", "typo"]  # only the unsourced ones
    assert "first_name" not in missing and "support_email" not in missing


def test_a_null_renders_empty_through_the_diagnostic_overlay_too() -> None:
    """``render_with_diagnostics`` runs on an ``overlay()`` of the shared env — confirm the overlay
    inherits ``finalize`` (it is the path the worker and the preview both take, so a finalize that
    only applied to ``render()`` would fix nothing where it matters most)."""
    html, missing = jinja_engine.render_with_diagnostics(
        "<p>Hi {{ first_name }},</p>", {"first_name": None}
    )
    assert html == "<p>Hi ,</p>"
    assert missing == []


# --------------------------------------------------------------------------------------------
# The preview: it must agree with the send
# --------------------------------------------------------------------------------------------


def test_preview_no_longer_manufactures_a_support_address() -> None:
    """The placeholder was removed: a firm with no support email previews EMPTY, exactly as the
    send renders. Preview/send agreement is the property that matters — a plausible-looking
    ``support@example.com`` in the preview footer was worse than a blank one."""
    ctx = email_preview.sample_context(Brand())
    assert ctx["brand"]["support_email"] is None
    result = email_preview.render_preview(
        subject="s",
        preheader="",
        html="<p>Contact {{ brand.support_email }}.</p>",
        css="",
        brand=Brand(),
    )
    assert "<p>Contact .</p>" in result.html
    assert "example.com" not in result.html
    # And it is not flagged as a typo — the field is real, the firm just hasn't set one.
    assert result.missing == []


def test_preview_keeps_a_real_support_address() -> None:
    ctx = email_preview.sample_context(Brand(support_email="help@acme.test"))
    assert ctx["brand"]["support_email"] == "help@acme.test"


def test_password_reset_previews_nameless_because_that_send_always_is() -> None:
    """``routers/internal.py`` hard-codes ``first_name=None`` for password_reset (no
    account-existence oracle), so a "Sarah" sample there was guaranteed-wrong, not representative.
    Other codes keep the populated sample — a nullable key's populated case is a useful preview."""
    assert email_preview.sample_context(Brand(), "password_reset")["first_name"] is None
    assert email_preview.sample_context(Brand(), "invite")["first_name"] == "Sarah"
    result = email_preview.render_preview(
        subject="Reset your password",
        preheader="",
        html="<p>Hi {{ first_name }},</p>",
        css="",
        brand=Brand(),
        code="password_reset",
    )
    assert "<p>Hi ,</p>" in result.html
    assert "None" not in result.html


def test_the_shipped_password_reset_default_previews_identically_to_its_send() -> None:
    """The shipped default guards with ``{{ first_name or 'there' }}``; with the override in place
    the preview renders the same "Hi there," the real send does."""
    default = email_defaults.default_for("password_reset")
    assert default is not None
    result = email_preview.render_preview(
        subject=default.subject,
        preheader=default.preheader,
        html=default.html,
        css=default.css,
        brand=Brand(name="Ridgeway Wealth"),
        code="password_reset",
    )
    assert "Hi there," in result.html
    assert "None" not in result.html


# --------------------------------------------------------------------------------------------
# End to end: a firm-authored template with NO guards, through render_or_default
# --------------------------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> dict[str, Any]:
        return self._row


class _FakeSession:
    """The published-template row, without Docker — mirrors tests/test_email_render_fallback.py.
    ``render_published_email``'s only DB interaction is that one SELECT, so this still drives the
    REAL Jinja merge and the REAL ``to_email_html`` inlining."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    async def execute(self, *_a: Any, **_k: Any) -> _FakeResult:
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_document_sent_with_every_optional_key_null_ships_no_none() -> None:
    """The other realistic shape: a firm-authored ``document_sent`` body using all three of that
    send site's nullable keys (``sent_by``, the covering ``message``, and ``portal_url`` in the CTA
    href) with no ``{% if %}`` guards. Hermetic, so this pin holds even where Docker doesn't."""
    row = {
        "subject": "{{ document_name }} from {{ sent_by }}",
        "preheader": "Sent by {{ sent_by }}",
        "schema": {
            "html": (
                "<p>Hi {{ first_name }},</p><p>{{ sent_by }} sent {{ document_name }}.</p>"
                '<p>{{ message }}</p><a href="{{ portal_url }}">Open your portal</a>'
            ),
            "css": "",
        },
    }
    subject, html = await email_render.render_or_default(
        _FakeSession(row),  # type: ignore[arg-type]
        tenant_id=1,
        code="document_sent",
        context={
            "document_name": "Suitability Report",
            "first_name": None,
            "sent_by": None,
            "message": None,
            "portal_url": None,
            "brand": {"name": "F", "logo_url": None, "accent": "#000", "support_email": None},
        },
    )
    assert "None" not in html and "None" not in subject
    assert 'href=""' in html
    assert subject == "Suitability Report from "
    # The override was used, not the fallback — the body still has renderable content.
    assert "Suitability Report" in html


@pytest.mark.asyncio
async def test_a_firm_authored_template_with_no_guards_does_not_say_hi_none(db) -> None:
    """The reported symptom, end to end through the real send seam.

    A firm writes ``<p>Hi {{ first_name }},</p>`` — no ``or 'there'``, because why would they — and
    publishes it. The password_reset send site passes ``first_name=None`` deliberately. Before the
    fix this email said "Hi None,". Real Postgres, real ``email.templates`` rows, real
    ``render_or_default``; nothing is sent (it returns the subject/html the caller would send).
    """
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db["app"])
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            s = uuid.uuid4().hex[:8]
            tenant = int(
                (
                    await session.execute(
                        text(
                            "INSERT INTO auth.tenants (uid, alias, name) "
                            "VALUES (:u, :a, 'Nullfield Wealth') RETURNING id"
                        ),
                        {"u": f"nn_{s}", "a": f"nn-{s}"},
                    )
                ).scalar_one()
            )
            author = (
                await session.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, first_name, last_name) "
                        "VALUES (:u, :t, :e, 'Ann', 'Author') RETURNING id"
                    ),
                    {"u": f"nu_{s}", "t": tenant, "e": f"ann_{s}@t.test"},
                )
            ).scalar_one()
            tpl = int(
                (
                    await session.execute(
                        text(
                            "INSERT INTO email.templates (uid, tenant_id, code, name, created_by) "
                            "VALUES (:u, :t, 'password_reset', 'Reset', :by) RETURNING id"
                        ),
                        {"u": f"et_{s}", "t": tenant, "by": author},
                    )
                ).scalar_one()
            )
            # An UNGUARDED firm-authored body, plus a nullable token in the subject and preheader.
            ver = int(
                (
                    await session.execute(
                        text(
                            "INSERT INTO email.template_versions "
                            "  (uid, tenant_id, template_id, version_number, status, subject, "
                            "   preheader, schema, created_by) "
                            "VALUES (:u, :t, :tpl, 1, 'published', :sub, :pre, "
                            "        CAST(:sch AS jsonb), :by) RETURNING id"
                        ),
                        {
                            "u": f"ev_{s}",
                            "t": tenant,
                            "tpl": tpl,
                            "sub": "{{ first_name }}, reset your password",
                            "pre": "A reset link for {{ first_name }}",
                            "sch": (
                                '{"html": "<p>Hi {{ first_name }},</p>'
                                "<p>Reach us at {{ brand.support_email }}.</p>"
                                '<a href=\\"{{ portal_url }}\\">Portal</a>", "css": ""}'
                            ),
                            "by": author,
                        },
                    )
                ).scalar_one()
            )
            await session.execute(
                text("UPDATE email.templates SET current_published_version_id = :v WHERE id = :i"),
                {"v": ver, "i": tpl},
            )

            subject, html = await email_render.render_or_default(
                session,
                tenant_id=tenant,
                code="password_reset",
                # Exactly what routers/internal.py passes on the no-oracle path.
                context={
                    "first_name": None,
                    "portal_url": None,
                    "brand": {
                        "name": "Nullfield Wealth",
                        "logo_url": None,
                        "accent": "#0f172a",
                        "support_email": None,
                    },
                },
            )
    finally:
        await engine.dispose()

    # The published override was used (not the fallback), and nowhere does it say None.
    assert "Hi ," in html
    assert "Hi None" not in html
    assert "None" not in html
    assert 'href=""' in html  # not href="None"
    assert subject == ", reset your password"
    assert "None" not in subject


def test_the_date_filter_writes_a_day_the_way_a_person_does() -> None:
    """ "01 October 2026" reads as a form field; British prose is "1 October 2026". `strftime` pads
    `%d` on every platform and `%-d`/`%#d` are platform-specific, so the default format is BUILT.
    An explicit format the author passed is honoured exactly as given."""
    from datetime import date as _date

    from app.render.jinja_engine import render

    assert render("{{ d | date }}", {"d": _date(2026, 10, 1)}) == "1 October 2026"
    assert render("{{ d | date }}", {"d": _date(2026, 10, 21)}) == "21 October 2026"
    # Parsed from a string the same way, so a send site passing ISO text is not a second answer.
    assert render("{{ d | date }}", {"d": "2026-01-05"}) == "5 January 2026"
    assert render("{{ d | date('%Y-%m-%d') }}", {"d": _date(2026, 10, 1)}) == "2026-10-01"
    # And the unreadable cases still degrade rather than raising.
    assert render("{{ d | date }}", {"d": None}) == ""
    assert render("{{ d | date }}", {"d": "not a date"}) == "not a date"
