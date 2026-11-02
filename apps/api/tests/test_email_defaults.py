"""Built-in email defaults for prefilling the editor (app/email/defaults.py).

The load-bearing guarantee: every default renders with ONLY tokens the send/preview context has, so
a firm that opens a template and publishes the prefill unchanged still gets a fully-populated email
— no stray ``{{ tokens }}`` and no blanks. Checked by merging each default through the preview.
"""

import re
from typing import Any

import pytest

from app.email import defaults as email_defaults
from app.email import preview as email_preview
from app.email import render as email_render
from app.email import shell as email_shell
from app.email.catalog import KNOWN_CODES
from app.email.preview import _CODE_KEYS
from app.email.transport import Brand
from app.render import jinja_engine


def test_default_for_unknown_code_is_none() -> None:
    assert email_defaults.default_for("no_such_code") is None


def test_every_catalogue_code_has_a_default() -> None:
    for c in KNOWN_CODES:
        assert email_defaults.default_for(c.code) is not None, c.code


def test_defaults_render_cleanly_under_their_own_code_context() -> None:
    # Merged against its own code's context only: a default may reference no token the send omits.
    brand = Brand(name="Ridgeway Wealth", accent="#0f766e", support_email="hi@ridgeway.test")
    for c in KNOWN_CODES:
        default = email_defaults.default_for(c.code)
        assert default is not None
        result = email_preview.render_preview(
            subject=default.subject,
            preheader=default.preheader,
            html=default.html,
            css=default.css,
            brand=brand,
            code=c.code,
        )
        assert result.missing == [], f"{c.code}: unresolved tokens {result.missing}"
        assert "{{" not in result.html and "{%" not in result.html, f"{c.code}: token left in body"
        assert "{{" not in result.subject, f"{c.code}: token left in subject"
        assert "Ridgeway Wealth" in result.html  # the token brand really did merge


#: Tokens that are the same fact in different words. Naming them here rather than dropping the guard
#: keeps it honest: a default that prints NONE of a group still fails.
_ALTERNATIVE_SPELLINGS: list[set[str]] = [{"first_name", "full_name", "surname"}]


def test_every_default_uses_every_token_its_send_site_supplies() -> None:
    """The converse of the test above: a value a send site goes to the trouble of putting in the
    merge context should be printed by the shipped default, because a firm that publishes the
    prefill unchanged expects the email it was already getting. A token that should not appear in a
    default belongs OUT of the send site's context, so the one exemption is argued rather than
    allowed.

    ``notification`` IS THE EXEMPTION: ``notifications/email_consumer.py`` builds ONE context
    serving the general wrapper AND whatever custom template a rule points at, so it carries their
    flat aliases. The wrapper prints only the notification's own title, body and CTA, and both
    remedies are worse — referencing ``document_name`` there would print a document's name on a
    notice about no document, and dropping it from the context would break every custom template
    that reads it. What must hold there is that the context and the offered vocabulary agree:
    ``test_the_notification_wrapper_lists_every_key_its_send_site_passes``.
    """
    for c in KNOWN_CODES:
        if c.code == "notification":
            continue
        default = email_defaults.default_for(c.code)
        assert default is not None
        used = set(jinja_engine.undeclared_variables(default.subject)) | set(
            jinja_engine.undeclared_variables(default.html)
        )
        supplied = set(_CODE_KEYS[c.code])
        # A group with one member printed counts as printed for all of them (see the docstring).
        for group in _ALTERNATIVE_SPELLINGS:
            if group & used:
                supplied -= group
        unused = sorted(supplied - used)
        assert not unused, (
            f"{c.code}: the send site provides {unused} but the shipped default prints "
            "none of them — a firm publishing the prefill unchanged loses that content. "
            "Reference it in the default, or stop passing it at the send site."
        )

    wrapper = email_defaults.default_for("notification")
    assert wrapper is not None
    printed = set(jinja_engine.undeclared_variables(wrapper.subject)) | set(
        jinja_engine.undeclared_variables(wrapper.html)
    )
    for own in ("title", "body"):
        assert own in printed, f"the notification wrapper stopped printing its own {own}"


def test_request_raised_default_lists_every_item() -> None:
    default = email_defaults.default_for("request_raised")
    assert default is not None
    result = email_preview.render_preview(
        subject=default.subject,
        preheader=default.preheader,
        html=default.html,
        css=default.css,
        brand=Brand(),
        code="request_raised",
    )
    for title in email_preview.sample_context(Brand(), "request_raised")["item_titles"]:
        assert title in result.html


#: The two task codes and the only words that differ: the subject (and heading) and the lead line.
TASK_CODES = (
    ("task_assigned", "New task", "You have been assigned a task."),
    ("task_reminder", "Reminder", "This task is due soon."),
)


def test_each_task_default_says_its_own_kind_and_branches_only_on_what_is_absent() -> None:
    """Each code states its kind outright, so what is left branches only on values that are
    genuinely absent sometimes: a due date or none, a client's portal CTA against a staff notice
    with none. Driven through `render_default`, the send path's own fallback branch."""
    brand = {
        "name": "Ridgeway Wealth",
        "logo_url": None,
        "accent": "#0f766e",
        "support_email": None,
    }
    base = {
        "brand": brand,
        "task_title": "Review report",
        "task_description": "Check ATR answers.",
    }

    for code, kind, lead in TASK_CODES:
        subject, html = email_render.render_default(
            code,
            base
            | {
                "due_date": "2026-02-01",
                "button_label": "Open your portal",
                "button_url": "https://p.test",
            },
            asset_origin=None,
        )
        assert subject == f"{kind}: Review report", code
        assert lead in html, code
        # …and ONLY its own lead — the property a single branching template could not have.
        others = [other for _c, _k, other in TASK_CODES if other != lead]
        for other in others:
            assert other not in html, f"{code} still carries the other notice's wording"
        assert "Check ATR answers." in html
        # Zero-padded: `_fmt_date`'s shared `%d %B %Y`, since `%-d` is not portable to Windows.
        # No leading zero: the `date` filter builds its default format rather than `strftime`-ing
        # it, because "01 February" reads as a form field and British prose is "1 February".
        assert "Due <strong>1 February 2026</strong>." in html, code
        assert 'href="https://p.test"' in html and "Open your portal" in html
        assert "Ridgeway Wealth" in html  # branded, not a hardcoded Graphene wordmark
        assert "Graphene" not in html

        _subject2, html2 = email_render.render_default(
            code,
            base | {"due_date": None, "button_label": None, "button_url": None},
            asset_origin=None,
        )
        assert lead in html2, code
        assert "Due <strong>" not in html2, code  # no due date → no due phrase
        assert "<a href" not in html2, code  # staff notice → no CTA button
        assert "paste this link" not in html2, code  # …and the fallback-link line goes with it


def test_the_two_task_defaults_differ_in_nothing_but_their_words() -> None:
    """They are near-identical BY DESIGN — same layout, same tokens, same guards — so a firm wanting
    a softer reminder can edit the reminder alone. The failure mode of two near-identical texts is
    that a fix lands in one of them; here they are one authoring parameterised by two words, and
    this asserts it by substituting the words back and demanding the rest be byte-identical."""
    assigned, reminder = (
        email_defaults.default_for("task_assigned"),
        email_defaults.default_for("task_reminder"),
    )
    assert assigned is not None and reminder is not None

    def swap(text: str) -> str:
        """The reminder's wording read as the assignment's — the ONLY licensed difference."""
        return text.replace("Reminder", "New task").replace(
            "This task is due soon.", "You have been assigned a task."
        )

    assert swap(reminder.subject) == assigned.subject
    assert swap(reminder.html) == assigned.html
    assert swap(reminder.source) == assigned.source
    assert reminder.preheader == assigned.preheader


def test_notification_defaults_carry_the_covering_note() -> None:
    # Both must surface {{ message }}, the adviser's note, or publishing the prefill drops it.
    for code in ("document_sent", "request_raised"):
        sample_note = email_preview.sample_context(Brand(), code)["message"]
        default = email_defaults.default_for(code)
        assert default is not None
        result = email_preview.render_preview(
            subject=default.subject,
            preheader=default.preheader,
            html=default.html,
            css=default.css,
            brand=Brand(),
            code=code,
        )
        assert sample_note in result.html, f"{code}: covering note not surfaced"


def test_the_masthead_is_ONE_image_with_a_fallback() -> None:
    """The masthead needs two outcomes — the firm's logo, or its name set as type — and the editor
    shape holds ONE element for them: an image whose second outcome is a PROPERTY of it, so nothing
    conditional is authored and nothing conditional appears on the canvas. The ``html`` shape is the
    two-arm Jinja a firm that never opens the editor sends."""
    for code in ("invite", "password_reset", "task_assigned"):
        default = email_defaults.default_for(code)
        assert default is not None

        assert "{% if brand.logo_url %}" in default.html
        assert "{% else %}" in default.html
        assert "data-fallback" not in default.html, f"{code}: editor attributes reached the send"
        assert "data-if" not in default.html

        assert default.source.count("<img") == 1, f"{code}: not a single image"
        assert "data-fallback=" in default.source
        assert "{% if brand.logo_url %}" not in default.source
        assert 'data-if-label="If brand.logo_url' not in default.source

        for shape in (default.html, default.source):
            assert 'data-brand-arm="logo"' in shape, f"{code}: the image is unmarked"


def test_the_masthead_fallback_carries_the_wordmarks_own_styling() -> None:
    """The fallback's tag and style travel INSIDE the attribute, so the editor's compile step
    re-emits what it was handed rather than knowing what a wordmark looks like. That keeps ONE
    definition of the look: ``email_shell.WORDMARK_STYLE`` is what ``text_wordmark`` renders for the
    send
    shape AND what the fallback carries for the editor shape, so the two arms cannot drift apart."""
    import json
    from urllib.parse import unquote

    default = email_defaults.default_for("invite")
    assert default is not None
    raw = re.search(r'data-fallback="([^"]+)"', default.source)
    assert raw is not None, "no fallback on the masthead"
    payload = json.loads(unquote(raw.group(1)))
    assert payload["field"] == "brand.logo_url"
    assert payload["text"] == "{{ brand.name }}"
    assert payload["tag"] == "div"
    assert payload["style"] == email_shell.WORDMARK_STYLE
    assert email_shell.WORDMARK_STYLE in default.html


def test_the_editor_shape_encodes_conditions_exactly_as_the_editor_does() -> None:
    """``_visibility_attrs`` is a MIRROR of the editor's `encodeVisibility`/`describeVisibility`
    (pkgs/ui `email-editor/conditions/attrs.ts`), pinned on both sides. The value is percent-encoded
    compact JSON, and three things have to agree or the panel decodes nothing and shows no condition
    on a block that has one: the KEY ORDER, the absence of whitespace (`json.dumps` separators vs
    `JSON.stringify`), and the safe set (`quote` vs `encodeURIComponent`). Asserted against the
    literal the editor produces, because re-encoding here with the same helper would pass whatever
    it did."""
    expected = (
        "%7B%22action%22%3A%22show%22%2C%22match%22%3A%22all%22%2C%22rules%22%3A%5B%7B%22field"
        "%22%3A%22brand.logo_url%22%2C%22op%22%3A%22set%22%7D%5D%7D"
    )
    assert email_defaults._visibility_attrs("brand.logo_url", "set") == (
        f' data-if="{expected}" data-if-label="If brand.logo_url has a value"'
    )
    assert "brand.logo_url" in expected


def test_every_code_ships_both_shapes_and_only_the_guards_differ() -> None:
    """One authoring, two shapes — so a code cannot gain a render-ready form without the editor form
    that matches it (``_both`` makes that structural rather than remembered). The editor shape is
    checked to be genuinely CLEANER: the two codes whose only conditionals are the masthead and the
    footer come out with no Jinja control flow at all."""
    for code in (
        "invite",
        "password_reset",
        "document_sent",
        "request_raised",
        "task_assigned",
        "task_reminder",
    ):
        default = email_defaults.default_for(code)
        assert default is not None
        assert default.source, f"{code}: no editor shape"
        assert default.source.count("{%") < default.html.count("{%"), f"{code}: no chips removed"
        assert "data-if" not in default.html, f"{code}: editor attributes in the send shape"

    for clean in ("invite", "password_reset"):
        default = email_defaults.default_for(clean)
        assert default is not None
        assert default.source.count("{%") == 0, f"{clean}: canvas still has code chips"


def test_the_resolved_masthead_carries_no_arm_markers() -> None:
    """`email_shell.masthead` has already chosen — it emits ONE arm for a resolved brand, with no
    conditional and nothing for a canvas to preview. Marking it would put editor bookkeeping in a
    path the editor never sees, so the marker is opt-in and this is the caller that declines it."""

    resolved = email_shell.masthead(Brand(name="Ridgeway Wealth"))
    assert "data-brand-arm" not in resolved
    assert "{% if" not in resolved


def test_the_notification_wrapper_lists_every_key_its_send_site_passes() -> None:
    """``_CODE_KEYS`` is a model of what each send site provides, and the editor's Insert menu is a
    mirror of it (``pkgs/ui`` ``email-editor/merge-fields.ts``, pinned there by a source-reading
    parity test). So a wrong entry here is wrong in three places at once — and that parity test
    cannot catch it, because it compares the editor's catalogue against THIS dict: both sides can
    agree on the same wrong list. This executes ``_template_context`` and asserts the key sets match
    exactly, in both directions: a key the consumer passes and this omits is a token an author
    cannot insert and the preview reports as ``missing`` although the send fills it; a key listed
    here and NOT passed is the opposite lie — an offered token that renders blank in a client's
    inbox.
    """
    from app.notifications import email_consumer

    passed = set(
        email_consumer._template_context(
            brand_tokens={},
            title="t",
            body="b",
            cta=None,
            portal_base=None,
            rctx={},
        )
    )
    # `brand` is added to every code by `sample_context` itself, so it is deliberately not listed.
    passed.discard("brand")
    listed = set(email_preview._CODE_KEYS["notification"])

    assert passed - listed == set(), (
        "the notification send site passes keys the preview does not model: "
        f"{sorted(passed - listed)}"
    )
    assert listed - passed == set(), (
        "the preview models keys the notification send site never passes: "
        f"{sorted(listed - passed)}"
    )


def test_every_modelled_key_has_a_sample_or_it_previews_as_missing() -> None:
    """A key in ``_CODE_KEYS`` with no entry in ``_SAMPLE`` is worse than an unlisted one: the code
    claims its send site provides it, and the preview then renders it blank and reports it missing.
    """
    for code, keys in email_preview._CODE_KEYS.items():
        unsampled = [k for k in keys if k not in email_preview._SAMPLE]
        assert unsampled == [], f"{code}: modelled but unsampled — {unsampled}"


def test_no_shipped_default_carries_an_inline_either_or() -> None:
    """A shipped default may SUPPORT conditions and must not be BUILT of them: a conditional-text
    node in one means an email that says two things, and the answer to that is a second default.

    Still load-bearing although nothing AUTHORS a conditional text: the code view applies a hand
    edit to the editor's own shape (``code-view.svelte``), so one can still be typed in, and
    ``conditions/compile.ts`` honours it rather than shipping loose Jinja. Both shapes are checked —
    the canvas attribute AND the Jinja it compiles to — because either arriving is the same
    regression in different clothes. The ``{% else %}`` half stands on its own: an either/or in the
    COPY is a second default whatever wrote it."""
    for entry in KNOWN_CODES:
        default = email_defaults.default_for(entry.code)
        assert default is not None
        for shape in (default.source, default.html):
            assert "data-choice" not in shape, (
                f"{entry.code}: a shipped default carries a conditional-text node. An either/or in "
                "the copy means this default is two emails — ship the second one instead (see "
                "task_assigned / task_reminder in app/email/catalog.py)."
            )
        # `{% else %}` is legitimate for the masthead, so the ban is on an else inside the body.
        body = default.html.split("</h1>", 1)[-1]
        assert "{% else %}" not in body, (
            f"{entry.code}: an inline either/or is back in the body copy of a shipped default"
        )


# ---- the masthead image a firm actually gets ----------------------------------------------------


def _branding(**kw: object) -> Any:
    from app import branding

    return branding.Branding(**kw)  # type: ignore[arg-type]


def test_the_masthead_wears_the_logo_treatment_the_firm_chose() -> None:
    """``masthead_image`` decides which picture an email opens with, off three branding inputs: a
    wordmark (``logo_url``), a mark (``icon_url``) and the firm's own choice between them
    (``logo_variant``, which its portals honour). Reading ``logo_url`` alone gives a firm that chose
    the ICON treatment its full lockup — in the one medium where a lockup is most likely to be too
    wide — and gives a firm holding only a mark the TEXT wordmark, the fallback for a firm with no
    brand imagery at all. So the chosen treatment decides and the other image is the fallback, and
    an author who picked one and uploaded the other still gets a picture rather than type."""
    from app.email.transport import masthead_image

    wordmark, icon = "https://cdn.test/logo.png", "https://cdn.test/icon.png"

    assert masthead_image(_branding(logo_url=wordmark, icon_url=icon)) == wordmark
    assert masthead_image(_branding(logo_url=wordmark, icon_url=icon, logo_variant="icon")) == icon
    assert masthead_image(_branding(icon_url=icon)) == icon
    assert masthead_image(_branding(logo_url=wordmark, logo_variant="icon")) == wordmark
    assert masthead_image(_branding()) is None


def test_a_data_uri_logo_falls_through_to_the_icon_not_to_type() -> None:
    """Branding accepts a ``data:`` URI and the portals render it happily; mail clients block them,
    so an inlined image is not a usable masthead and the icon is the next thing to try."""
    from app.email.transport import masthead_image

    inlined = "data:image/png;base64,iVBORw0KGgo="
    assert masthead_image(_branding(logo_url=inlined)) is None
    assert (
        masthead_image(_branding(logo_url=inlined, icon_url="https://cdn.test/icon.png"))
        == "https://cdn.test/icon.png"
    )


@pytest.mark.asyncio
async def test_a_proxied_brand_logo_becomes_a_public_portal_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.email import transport

    async def origin(_session: Any, tenant_id: int | None) -> str:
        assert tenant_id == 7
        return "https://client.example.test"

    monkeypatch.setattr(transport, "asset_origin", origin)
    masthead, icon = await transport._email_asset_urls(  # noqa: SLF001
        None,  # type: ignore[arg-type]
        7,
        "client",
        _branding(
            logo_url="/_assets/logo-0123456789abcdef.png",
            icon_url="/_assets/icon-0123456789abcdef.png",
        ),
    )
    assert masthead == "https://client.example.test/_assets/logo-0123456789abcdef.png"
    assert icon == "https://client.example.test/_assets/icon-0123456789abcdef.png"


@pytest.mark.asyncio
async def test_the_logo_survives_a_firm_with_only_the_other_portal_registered() -> None:
    """A firm's logo is served by EITHER of its hosts, so mail from one portal must not fall back
    to type because the other portal is the one with a host row.

    The relay resolves the tenant from whichever host it was called on and then reads that tenant's
    bucket, so both hosts serve the same object. Resolving against the SENDING portal's host alone
    meant a firm with a client portal and no platform host had its staff mail wear the text
    wordmark while holding a perfectly servable logo — the wordmark is the fallback for a firm with
    no brand imagery, and this firm has some.
    """
    from app.email import transport

    class _OneHost:
        """A session that answers `_portal_hosts`' query with a platform host and no client one."""

        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            class _Result:
                def mappings(self) -> Any:
                    class _M:
                        def one_or_none(self) -> dict[str, str | None]:
                            return {
                                "client_portal_host": None,
                                "platform_host": "staff.example.test",
                            }

                    return _M()

            return _Result()

    for portal in ("client", "platform"):
        masthead, _icon = await transport._email_asset_urls(  # noqa: SLF001
            _OneHost(),  # type: ignore[arg-type]
            7,
            portal,  # type: ignore[arg-type]
            _branding(logo_url="/_assets/logo-0123456789abcdef.png"),
        )
        assert masthead == "https://staff.example.test/_assets/logo-0123456789abcdef.png", portal


@pytest.mark.asyncio
async def test_no_registered_host_leaves_the_wordmark_rather_than_a_broken_picture() -> None:
    """With nowhere to serve the asset from there is no URL to state, so the masthead is None and
    the shell's `{% if brand.logo_url %}` wears type instead. A relative path in an inbox resolves
    against nothing, which arrives as a broken image — strictly worse than the firm's name set in
    its own brand."""
    from app.email import transport

    class _NoHost:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            class _Result:
                def mappings(self) -> Any:
                    class _M:
                        def one_or_none(self) -> None:
                            return None

                    return _M()

            return _Result()

    masthead, icon = await transport._email_asset_urls(  # noqa: SLF001
        _NoHost(),  # type: ignore[arg-type]
        7,
        "client",
        _branding(logo_url="/_assets/logo-0123456789abcdef.png"),
    )
    assert masthead is None and icon is None


def test_the_icon_reaches_the_templates_as_its_own_token() -> None:
    """``brand.icon_url`` is carried beside the resolved masthead image, not instead of it: they
    answer different questions. ``logo_url`` is "what does this email open with" (already resolved
    from the treatment), ``icon_url`` is "does this firm have a square mark" — what a template
    placing a badge beside the copy, or a small mark in a footer, has to guard on. A firm can hold
    one and not the other, so one token cannot serve both."""
    from app.email.transport import Brand, brand_tokens

    tokens = brand_tokens(
        Brand(
            name="Ridley", logo_url="https://cdn.test/logo.png", icon_url="https://cdn.test/i.png"
        )
    )
    assert tokens["logo_url"] == "https://cdn.test/logo.png"
    assert tokens["icon_url"] == "https://cdn.test/i.png"
    # None, not an absent key: `finalize` prints a null as "", an absent key previews as missing.
    assert "icon_url" in brand_tokens(Brand(name="Ridley"))


#: Pinned verbatim on the editor side too (`conditions/cross-language.test.ts`); never re-encode.
REQUEST_REPEAT = "%7B%22field%22%3A%22item_titles%22%2C%22alias%22%3A%22item%22%7D"
NOTE_GUARD = (
    "%7B%22action%22%3A%22show%22%2C%22match%22%3A%22all%22%2C%22rules%22%3A%5B%7B%22field%22%3A"
    "%22message%22%2C%22op%22%3A%22set%22%2C%22filter%22%3A%22paragraphs%22%7D%5D%7D"
)


def test_no_shipped_default_puts_raw_jinja_on_the_canvas() -> None:
    """The editor shape of every shipped default is free of Jinja control flow. A chip on the canvas
    is a conditional an author can see, cannot edit and can delete half of, and it renders there as
    visible lines of code — which is why the editor and the preview once disagreed about an email's
    height. `html`, what actually sends, keeps every one of those constructs as Jinja."""
    for code in email_preview._CODE_KEYS:
        default = email_defaults.default_for(code)
        assert default is not None
        assert "{%" not in default.source, (
            f"{code}: raw Jinja on the canvas — {re.findall(r'{%[^%]*%}', default.source)}"
        )
        assert "{%" in default.html, f"{code}: the send shape lost its Jinja"


#: What the Style rail heads itself with, per part, in document order — the palette's OWN words
#: (pkgs/ui ``email-editor/create.ts`` → ``EMAIL_BLOCKS``), so a shipped default reads as the same
#: kind of object as a block an author drags in. "Description" is the one name with no palette
#: counterpart: the task clause is furniture no block ships.
#: The chrome ahead of a default's own copy: the Email card block's parts, the masthead image and
#: the card's heading. Identical on every code, which is why it is factored out of the table.
#:
#: THE `<tr>`s ARE ABSENT and `Card body` is new, because the chrome is generated by
#: `better-svelte-email` now (`pkgs/ui/src/lib/email-chrome/chrome.svelte`): the kit has no
#: bare-row primitive, so a `<tr>` cannot carry an attribute and every row is its own table — which
#: is what introduces the extra `Card body` cell. An unnamed row falls back to the editor's own
#: component vocabulary (`create.ts`), so the tree still reads; it reads 'Table row' rather than
#: 'Accent bar row'.
_CHROME_NAMES = [
    "Email card",
    "Card wrapper",
    "Card",
    "Card body",
    "Accent bar row",
    "Accent bar",
    "Card row",
    "Card content",
    "Brand logo",
    "Heading",
]

#: …and the chrome after it: the button's parts, then the footer's. `Button row` and `Footer row`
#: are gone for the reason `_CHROME_NAMES` gives — the kit cannot name a `<tr>`.
_TAIL_NAMES = [
    "Button",
    "Button fill",
    "Button link",
    "Button label",
    "Footer text",
    "Support address",
    "Support link",
]

#: Per code, the body names between the two — the only part of the document order that varies.
#: A merge field inside a paragraph is its OWN chip, named for the field, so the greeting reads
#: Greeting then First name: `_field` writes the span the palette writes, which is what makes a
#: default's fallback editable from the Field panel instead of being raw `or` nobody can reach.
_BODY_NAMES: dict[str, list[str]] = {
    "invite": ["Greeting", "First name", "Text"],
    "password_reset": ["Greeting", "First name", "Text", "Text"],
    "document_sent": [
        "Greeting",
        "First name",
        "Text",
        "Document name",
        "Sent by",
        "Note Box panel",
    ],
    "request_raised": [
        "Greeting",
        "First name",
        "Text",
        "Bullet List",
        "List item",
        "Text",
        "Note Box panel",
    ],
    "task_assigned": ["Text", "Description", "Text"],
    "task_reminder": ["Text", "Description", "Text"],
    "notification": ["Text"],
}

PART_NAMES: dict[str, list[str]] = {
    code: _CHROME_NAMES + body + _TAIL_NAMES for code, body in _BODY_NAMES.items()
}


def test_every_default_names_its_parts_and_only_on_the_canvas() -> None:
    """A part with no name heads the Style rail with GrapesJS's own answer, which is an internal
    type id — click the greeting and be told you selected "Text", the note box "Element", the button
    "Table cell". So the defaults' parts wear the palette's names, and they wear them on the EDITOR
    shape alone: ``data-gjs-*`` is consumed when the canvas is parsed, and ``html`` is what a
    recipient gets."""
    assert set(PART_NAMES) == set(email_preview._CODE_KEYS), "a code gained or lost a default"
    for code, expected in PART_NAMES.items():
        default = email_defaults.default_for(code)
        assert default is not None
        assert re.findall(r'data-gjs-name="([^"]+)"', default.source) == expected, code
        assert "data-gjs" not in default.html, f"{code}: editor bookkeeping in the send shape"
        # The names themselves, not merely the attribute: nothing may name a part in the inbox.
        for name in set(expected):
            assert name not in default.html, f"{code}: {name!r} reached the send shape"


def test_the_chrome_helpers_name_nothing_unless_asked() -> None:
    """``app/email/transport.py`` is shared with the real send paths — provisioning, the
    notification
    consumer, the flow action — and only ``email_defaults``' ``source`` path passes ``names=True``.
    So the default is what every other caller gets, and it must carry no editor bookkeeping."""
    parts = [
        email_shell.cta_block("Go", "https://p.test", "#0f766e"),
        email_shell.support_link("help@p.test"),
        email_shell.shell_html("Hi", "<p>b</p>", wordmark="", cta="", footer=""),
        email_shell.shell("Hi", "<p>b</p>", "Go", "https://p.test"),
    ]
    for markup in parts:
        assert "data-gjs" not in markup, markup[:80]
    assert 'data-gjs-name="Button fill"' in email_shell.cta_block(
        "Go", "https://p.test", "#0", names=True
    )
    assert 'data-gjs-name="Support link"' in email_shell.support_link("help@p.test", names=True)
    assert 'data-gjs-name="Card content"' in email_shell.shell_html(
        "Hi", "<p>b</p>", wordmark="", names=True
    )


def test_the_request_list_repeats_one_row() -> None:
    """One `<li>` in the document, one per item in the inbox."""
    default = email_defaults.default_for("request_raised")
    assert default is not None
    assert default.source.count("<li") == 1, "the editor shape should hold a single example row"
    assert f'data-repeat="{REQUEST_REPEAT}"' in default.source
    assert "{% for item in item_titles %}" in default.html
    assert "data-repeat" not in default.html


def test_the_note_guard_keeps_its_filter_on_both_sides() -> None:
    """The guard asks about the RENDERED note, and losing the filter is a silent regression: a
    note of nothing but whitespace would pass a bare truthiness test and draw an empty bordered
    box."""
    for code in ("document_sent", "request_raised"):
        default = email_defaults.default_for(code)
        assert default is not None
        assert NOTE_GUARD in default.source, f"{code}: the note guard lost its filter"
        assert "{% if message | paragraphs %}" in default.html, f"{code}: the send lost the filter"


# A subject is a plain text box, so a decision belongs in `derived_tokens`, never in a Jinja tag.


def test_no_shipped_subject_carries_a_jinja_tag() -> None:
    """A subject is a plain text input, so there is nowhere in it to put a control — which is not a
    reason to leave `{% if %}` in the box an author types in: `request_raised` pluralises off the
    item count, a function of what the send already knows, so it is decided in `derived_tokens` and
    the subject holds an ordinary merge field. `{{ … }}` is fine and is the point. What must never
    come back is a TAG: control flow an author cannot edit or preview, and can delete half of."""
    for code in KNOWN_CODES:
        default = email_defaults.default_for(code.code)
        assert default is not None
        assert "{%" not in default.subject, (
            f"{code.code}: the subject carries a Jinja tag — an author edits this in a plain text "
            "box, so a decision belongs in email_defaults.derived_tokens instead"
        )


def test_the_declared_derived_tokens_are_exactly_what_is_produced() -> None:
    """`_SUBJECT_TOKENS` is what the EDITOR's catalogue mirrors (`merge-fields.test.ts` reads this
    module), so a token produced and not declared would render at send and be un-insertable in the
    editor. `derived_tokens` filters its answer through the declaration; this pins that the
    declaration is not merely a superset."""
    inputs: dict[str, dict[str, object]] = {
        "request_raised": {"item_titles": ["a", "b"]},
    }
    for code, declared in email_defaults._SUBJECT_TOKENS.items():
        produced = email_defaults.derived_tokens(code, inputs.get(code, {}))
        assert set(produced) == set(declared), code
        assert all(v for v in produced.values()), f"{code}: a derived token must never be blank"


def test_a_code_with_no_derived_tokens_gets_nothing() -> None:
    """Total, and called for every code by both leaf renderers — so an undeclared code must answer
    `{}` rather than raise on a send path. The two task codes are in the list on their own merit:
    each states its kind in plain characters, so there is nothing there to decide."""
    for code in (
        "invite",
        "password_reset",
        "document_sent",
        "notification",
        "task_assigned",
        "task_reminder",
        "made_up",
    ):
        assert email_defaults.derived_tokens(code, {}) == {}


def test_the_derived_wording_covers_none_one_and_many() -> None:
    """0/1/many is the real shape of the count decision, and "1 things" is the bug to guard. An
    empty list reads "action needed" rather than "0 things need your attention"."""
    summary = lambda titles: email_defaults.derived_tokens(  # noqa: E731
        "request_raised", {"item_titles": titles}
    )["request_summary"]
    assert summary([]) == "action needed"
    assert summary(["one"]) == "action needed"
    assert summary(["one", "two"]) == "2 things need your attention"
    assert email_defaults.derived_tokens("request_raised", {})["request_summary"] == "action needed"


def test_the_reminder_distinction_survives() -> None:
    """Worth keeping rather than flattening away: a reminder is a RE-SEND of an email the person has
    already had, and an inbox saying "New task" twice about one task reads as two tasks. The
    distinction lives where a reader meets it — each template's own subject, heading and lead
    sentence — so the thing to guard is that neither has quietly acquired the other's words. Read
    off the SHIPPED copy rather than a helper, because the copy IS the mechanism."""
    for code, kind, lead in TASK_CODES:
        default = email_defaults.default_for(code)
        assert default is not None
        assert default.subject.startswith(f"{kind}: "), code
        assert f">{kind}: " in default.html, f"{code}: the heading lost its kind"
        assert lead in default.html, code
    shipped = [email_defaults.default_for(c) for c, _k, _l in TASK_CODES]
    assert len({d.subject for d in shipped if d is not None}) == 2


def test_no_default_carries_whitespace_between_tags() -> None:
    """GrapesJS promotes an element whose children include a raw textnode to ONE `text` component.

    So a single space between the accent-bar table and the content table collapses the card into an
    RTE field and it stops being a droppable structure — `pkgs/ui`'s `email-editor/create.ts` states
    the rule (markup rule 2) and nothing enforced it. The chrome had NINE such gaps when it was
    written as an indented Python f-string, and the whole suite passed: this is a GrapesJS parsing
    behaviour, invisible to any assertion about the markup's content.

    BOTH shapes, and all four spellings of the gap. The send shape matters because `_both` keeps the
    pair identical, so a gap there means a gap in the editor shape one edit later; and a gap either
    side of a `%(slot)s` is the same defect one substitution later, once the slot holds markup.
    """
    from app.email import chrome_generated as chrome

    gaps = (r">\s+<", r"\)s\s+<", r">\s+%\(", r"\)s\s+%\(")

    for name in ("SHELL", "SHELL_NAMED", "CTA", "CTA_NAMED", "FOOTER", "FOOTER_NAMED"):
        markup = getattr(chrome, name)
        for gap in gaps:
            assert not re.search(gap, markup), f"{name} carries {gap!r}"

    for code in email_preview._CODE_KEYS:
        default = email_defaults.default_for(code)
        assert default is not None
        for shape, markup in (("html", default.html), ("source", default.source)):
            assert not re.search(r">\s+<", markup), f"{code}.{shape} carries a gap between tags"


def test_the_cta_table_shrinks_to_its_label() -> None:
    """The button is a PILL, and a pill is a table with no width — it takes its size from the cell's
    padding around the label.

    The kit's `Row` hardcodes `width="100%"` before its `...restProps`, so the generated button came
    out as a full-bleed bar spanning the card with the label pinned 24px from the left edge. Nothing
    in the suite saw it: every assertion about the button is about its STYLE layers (the pill on the
    cell, the ink on the anchor, the class for the MSO hook) and all of them still passed. It took
    looking at the delivered email.

    Asserted on the ABSENCE of a width, which is what shrink-to-fit means for a table, and on the
    accent bar still being full width — the fix is an override on one component and the card must
    not pick it up.
    """
    from app.email import chrome_generated as chrome

    for name in ("CTA", "CTA_NAMED"):
        markup = getattr(chrome, name)
        table = markup[: markup.index(">") + 1]
        assert "width=" not in table, f"{name}'s button table carries a width: {table}"
        assert not re.search(r"style=\"[^\"]*\bwidth\s*:", table), f"{name} styles a width"

    # The card and the footer are the opposite case: they SHOULD span.
    for name in ("SHELL", "FOOTER"):
        markup = getattr(chrome, name)
        assert 'width="100%' in markup[: markup.index(">") + 1], f"{name} stopped spanning"
