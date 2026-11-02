"""Three guards: the two publish content gates, and the preview's audience.

**Publishing an empty version is not cosmetic.** `render_published_email` resolves a tenant's
published version on every send, so a published-but-blank body permanently switches OFF the
fail-safe fallback to the shipped default for that code: clients get a subject and an empty message.
The gate asks whether the body renders SUBSTANCE, not whether the markup is non-empty — a layout
section with nothing typed into it is perfectly non-empty markup that paints a blank page.

**Publishing the editor's own scaffold copy is the same defect wearing the opposite mask.** A real
test send delivered an email reading "Add note content here." — the Note Box placeholder, dropped,
never typed into, published. Blank looks broken; that looked authored, which is why it reached an
inbox. So the palette's placeholders are refused at publish too, on a WHOLE-ELEMENT rule, and the
list is pinned against `pkgs/ui/.../email-editor/create.ts` at the end of this file.

**The preview's brand follows the AUDIENCE, not the category.** `category` gates how a code is
permissioned; it says nothing about who receives the mail (`internal.py`'s password_reset is always
client-branded), so the preview takes the audience on the request.
"""

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.email import content as email_content
from app.email import templates_service as ets
from app.email import transport as email_mod
from app.routers import email_templates as et
from tests import email_support

# ── the content scanner (pure) ────────────────────────────────────────────────

BLANK: list[Any] = [
    {},
    {"css": ".x{color:red}"},
    {"html": ""},
    {"html": "   \n\t "},
    None,
    ["<p>hi</p>"],
    {"html": "<body></body>"},
    # THE regression: an author adds a section and types nothing — non-empty markup, blank page.
    {"html": '<div style="padding:32px"></div>'},
    {"html": "<body><div><div></div></div></body>"},
    {"html": '<section class="wrap"><div class="col"></div></section>'},
    # Tables with empty cells, including `&nbsp;` and zero-width padding `str.strip()` would miss.
    {"html": "<table><tbody><tr><td></td></tr></tbody></table>"},
    {"html": "<table><tr><td>&nbsp;&nbsp;</td></tr></table>"},
    {"html": "<table><tr><td>&#160;</td></tr></table>"},
    {"html": "<p>​﻿</p>"},
    # Unreadable text and decoration: colour is deliberately not content, a background image is.
    {"html": "<!-- hero goes here -->"},
    {"html": "<style>.btn{color:#fff}</style><div></div>"},
    {"html": "<script>var tracked = 1;</script>"},
    {"html": '<hr style="border-top:1px solid #eee">'},
    {"html": '<div style="background:#0f766e;height:120px"></div>'},
    # No exported html: the send path renders schema['html'] alone, so this once published blank.
    {"gjs_project": {"pages": [{"frames": [{"component": {"components": [{"tagName": "div"}]}}]}]}},
    {"gjs_project": {"pages": []}, "html": "<div></div>"},
]

SUBSTANCE: list[Any] = [
    {"html": "<p>Hello</p>"},
    {"html": "<body><p>Your report is ready.</p></body>"},
    {"html": "<div><table><tr><td><span>Hi</span></td></tr></table></div>"},
    {"html": "<a href='https://x/set-password'>Set your password</a>"},
    {"html": "Your statement is attached."},  # a bare fragment, text outside any element
    {
        "html": "<html><head><title>S</title><style>a{color:red}</style></head>"
        "<body><p>Hi</p></body></html>"
    },
    {"html": "<p>{{ first_name }}, your review is booked.</p>"},
    {"html": "{{ message }}"},
    {"html": '<div><img src="https://x/poster.png" alt=""></div>'},
    {"html": "<div style=\"background-image:url('https://x/hero.png');height:200px\"></div>"},
    {"html": '<table><tr><td background="https://x/bg.png"></td></tr>'},
    {"gjs_project": {"pages": []}, "html": "<p>Hi</p>"},  # the html decides, not the project
]


@pytest.mark.parametrize("schema", BLANK)
def test_a_body_that_paints_blank_is_not_publishable(schema: Any) -> None:
    assert ets._has_content(schema) is False


@pytest.mark.parametrize("schema", SUBSTANCE)
def test_a_body_with_something_to_read_is_publishable(schema: Any) -> None:
    assert ets._has_content(schema) is True


def test_scanner_is_reusable_and_html_only() -> None:
    assert email_content.has_renderable_content("<p>Hi</p>") is True
    assert email_content.has_renderable_content("<div></div>") is False


@pytest.mark.parametrize(
    "html",
    [
        "<div><noscript><img src='x'></noscript></div>",
        "<template><img src='x'></template>",
        "<head><title>t</title></head>",
        "<noscript><td background='x.png'></td></noscript>",
        "<noscript>real text a recipient never sees</noscript>",
    ],
)
def test_content_inside_an_invisible_container_does_not_count(html: str) -> None:
    # Only <script>/<style> are CDATA to html.parser, so these are walked and must not count.
    assert email_content.has_renderable_content(html) is False


def test_a_visible_image_after_an_invisible_container_still_counts() -> None:
    assert email_content.has_renderable_content("<noscript>x</noscript><img src='y'>") is True


# ── the placeholder scanner (pure) ────────────────────────────────────────────

NOTE_BOX_HTML = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="margin:12px 0"><tr><td style="padding:12px 14px;background-color:#f3f7fe;'
    'border-left:3px solid #3b82f6;font-size:13px;line-height:1.6;color:#374151">'
    "Add note content here.</td></tr></table>"
)


def test_the_note_box_placeholder_that_reached_a_real_inbox_is_found() -> None:
    """THE regression, in the exact markup the editor emits for an untouched Note Box — and through
    the service predicate the gate actually calls."""
    assert email_content.placeholder_copy(NOTE_BOX_HTML) == "Add note content here."
    assert ets._placeholder_copy({"html": NOTE_BOX_HTML}) == "Add note content here."


def test_every_listed_placeholder_is_found_on_its_own() -> None:
    for placeholder in email_content.EDITOR_PLACEHOLDERS:
        for markup in (
            f"<p>{placeholder}</p>",
            f"<li>{placeholder}</li>",
            f'<td style="padding:4px">{placeholder}</td>',
        ):
            assert email_content.placeholder_copy(markup) == placeholder


def test_the_starter_paragraph_is_found_across_its_inline_markup() -> None:
    """STARTER_HTML's copy is broken up by two `<strong>`s, so a rule that flushed on every tag
    would see five fragments and match none. Inline formatting joins."""
    assert (
        email_content.placeholder_copy(
            '<p style="margin:0 0 16px">Write your message here. Use the '
            "<strong>Insert field</strong> menu to add personalised details, and <strong>Preview"
            "</strong> to see it with sample values.</p>"
        )
        is not None
    )


NOT_A_PLACEHOLDER = [
    "<p>The first item on the agenda is your review.</p>",
    "<p>See the fees under the Heading below.</p>",
    "<li>First item: bring your latest statement</li>",
    "<p>Write your message here in the portal and we'll reply.</p>",
    "<p>Write your update here.</p>",
    "<li>First item — your ISA allowance</li>",
    "<!-- Add note content here. -->",
    "<style>/* First item */</style><p>Hi</p>",
    "<script>var x = 'Link text';</script><p>Hi</p>",
    '<img src="https://x/p.png" alt="Heading"><p>Hi</p>',
    "",
]


@pytest.mark.parametrize("markup", NOT_A_PLACEHOLDER)
def test_real_copy_is_not_refused_as_a_placeholder(markup: str) -> None:
    assert email_content.placeholder_copy(markup) is None


@pytest.mark.parametrize(
    "markup",
    [
        "<p>  Add note content here.  </p>",
        "<p>Add note\n  content here.</p>",
        "<p>Add&nbsp;note content here.</p>",
        "<p>add NOTE content HERE.</p>",
    ],
)
def test_placeholder_matching_tolerates_the_editor_reformatting_the_text(markup: str) -> None:
    assert email_content.placeholder_copy(markup) == "Add note content here."


def test_the_button_label_is_not_treated_as_a_placeholder() -> None:
    """The one palette default that is real copy: exactly what a firm would put on that button, so
    refusing it would block a finished email."""
    button = (
        '<table role="presentation"><tr><td style="background-color:#0f172a;border-radius:6px">'
        '<a href="https://portal.example/login" style="color:#ffffff">Open portal</a>'
        "</td></tr></table>"
    )
    assert "Open portal" not in email_content.EDITOR_PLACEHOLDERS
    assert email_content.placeholder_copy(button) is None


def test_no_shipped_default_is_made_unpublishable_by_this_gate() -> None:
    """The gate must not refuse our OWN copy: every shipped default is seeded into the model tenant
    and cloned into every new firm, so a placeholder-shaped phrase in one would leave a firm holding
    a preset it cannot re-publish after an edit — failing on their screen, not ours."""
    from app.email import catalog as email_catalog
    from app.email import defaults as email_defaults

    for entry in email_catalog.KNOWN_CODES:
        default = email_defaults.default_for(entry.code)
        if default is not None:
            assert email_content.placeholder_copy(default.html) is None, entry.code


def test_the_placeholder_predicate_survives_the_shapes_the_api_can_hold() -> None:
    for schema in (None, {}, {"css": ".x{color:red}"}, {"html": None}, ["<p>Add note content."]):
        assert ets._placeholder_copy(schema) is None  # type: ignore[arg-type]


# ── endpoint behaviour (real Postgres, RLS-bound session) ─────────────────────


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, first_name="Erin")


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff))
    yield opener
    await engine.dispose()


async def _new_template(session: AsyncSession, principal: Principal, *, category: str) -> str:
    created = await et.create_email_template(
        body=et.CreateEmailTemplate(
            code=f"custom_{uuid.uuid4().hex[:6]}",
            name="Review booked",
            category=category,  # type: ignore[arg-type]
        ),
        principal=principal,
        session=session,
    )
    return created.uid


async def _save(
    session: AsyncSession, principal: Principal, uid: str, *, subject: str, html: str
) -> str:
    version = await et.save_email_template_version(
        uid=uid,
        body=et.SaveEmailVersion(subject=subject, schema={"html": html, "css": ""}),
        principal=principal,
        session=session,
    )
    return version.uid


async def _published_version_of(session: AsyncSession, uid: str) -> int | None:
    return (
        await session.execute(
            text("SELECT current_published_version_id FROM email.templates WHERE uid = :u"),
            {"u": uid},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_publish_refuses_a_body_that_renders_blank(
    session_factory: Any, staff: dict[str, str]
) -> None:
    """The whole point: a section with no words in it cannot silently replace the shipped
    default."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        uid = await _new_template(session, principal, category="notification")
        vid = await _save(
            session,
            principal,
            uid,
            subject="Your review is booked",
            html='<div style="padding:32px"><div class="col"></div></div>',
        )
        with pytest.raises(HTTPException) as exc:
            await et.publish_email_template_version(
                uid=uid, vid=vid, principal=principal, session=session
            )
    assert exc.value.status_code == 422
    assert "blank message" in str(exc.value.detail)  # names the consequence, not the internals

    async with session_factory() as session:
        assert await _published_version_of(session, uid) is None
        statuses = (
            (
                await session.execute(
                    text(
                        "SELECT v.status::text FROM email.template_versions v "
                        "JOIN email.templates t ON t.id = v.template_id WHERE t.uid = :u"
                    ),
                    {"u": uid},
                )
            )
            .scalars()
            .all()
        )
    assert set(statuses) == {"draft"}


@pytest.mark.asyncio
async def test_publish_still_refuses_a_missing_subject(
    session_factory: Any, staff: dict[str, str]
) -> None:
    """The pre-existing subject guard is untouched by the content rule (and fires first)."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        uid = await _new_template(session, principal, category="notification")
        vid = await _save(session, principal, uid, subject="  ", html="<p>Hi</p>")
        with pytest.raises(HTTPException) as exc:
            await et.publish_email_template_version(
                uid=uid, vid=vid, principal=principal, session=session
            )
    assert exc.value.status_code == 422
    assert "subject" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_publish_refuses_leftover_placeholder_copy_and_names_it(
    session_factory: Any, staff: dict[str, str]
) -> None:
    """THE regression, end to end: a Note Box dropped and never typed into. The body is NOT blank —
    it has real words — so the content gate passes it and only this one stops it. The refusal quotes
    the offending text, because an author staring at a canvas needs to know which block it is."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        uid = await _new_template(session, principal, category="notification")
        vid = await _save(
            session,
            principal,
            uid,
            subject="Your review is booked",
            html=f"<p>Hi {{{{ first_name }}}}, your review is booked.</p>{NOTE_BOX_HTML}",
        )
        with pytest.raises(HTTPException) as exc:
            await et.publish_email_template_version(
                uid=uid, vid=vid, principal=principal, session=session
            )
    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert "Add note content here." in detail  # names the text, not merely "placeholder found"
    assert "placeholder" in detail.lower()
    async with session_factory() as session:
        assert await _published_version_of(session, uid) is None  # fail-safe still armed


@pytest.mark.asyncio
async def test_saving_a_draft_full_of_placeholders_still_works(
    session_factory: Any, staff: dict[str, str]
) -> None:
    """The gate is at PUBLISH only. Half-built drafts are the normal state of an email under
    construction — a save that refused the palette's own copy would make the editor unusable (drop
    a block, lose it on the next autosave)."""
    principal = email_support.principal(staff)
    async with session_factory() as session:
        uid = await _new_template(session, principal, category="notification")
        saved = await et.save_email_template_version(
            uid=uid,
            body=et.SaveEmailVersion(
                subject="Draft in progress",
                schema={"html": f"<h2>Heading</h2>{NOTE_BOX_HTML}", "css": ""},  # type: ignore[call-arg]
            ),
            principal=principal,
            session=session,
        )
        assert saved.status == "draft"
        stored = (
            await session.execute(
                text("SELECT schema->>'html' FROM email.template_versions WHERE uid = :v"),
                {"v": saved.uid},
            )
        ).scalar_one()
        assert "Add note content here." in stored  # it really persisted, placeholders and all

        with pytest.raises(HTTPException) as exc:  # …and publishing that same draft is what fails
            await et.publish_email_template_version(
                uid=uid, vid=saved.uid, principal=principal, session=session
            )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_publish_accepts_the_emails_the_gates_must_not_block(
    session_factory: Any, staff: dict[str, str]
) -> None:
    """The negatives that keep both gates honest: a merge-token line, an image-only body, and real
    sentences containing every placeholder phrase (a substring rule would refuse all three)."""
    principal = email_support.principal(staff)
    bodies = [
        "<p>Hi {{ first_name }}, your review is booked.</p>",
        '<table><tr><td><img src="https://x/statement.png" alt="Statement"></td></tr></table>',
        (
            "<h2>Your annual review</h2>"
            "<p>The first item on the agenda is your pension. Write your message here in the "
            "portal if you'd like to add anything.</p>"
            "<ul><li>First item: your latest statement</li><li>Second item: ID</li></ul>"
            '<table><tr><td style="background-color:#0f172a"><a href="https://x/login">'
            "Open portal</a></td></tr></table>"
        ),
    ]
    async with session_factory() as session:
        for html in bodies:
            uid = await _new_template(session, principal, category="notification")
            vid = await _save(session, principal, uid, subject="Your review", html=html)
            published = await et.publish_email_template_version(
                uid=uid, vid=vid, principal=principal, session=session
            )
            assert published.status == "published", html[:40]


def _recording_brand(calls: list[str]) -> Any:
    async def brand_for(
        session: AsyncSession, tenant_id: int | None, *, portal: str = "client"
    ) -> email_mod.Brand:
        calls.append(portal)
        return email_mod.Brand(name=f"Brand::{portal}", accent="#0f766e")

    return brand_for


@pytest.mark.asyncio
async def test_preview_takes_the_audience_from_the_request_not_the_category(
    session_factory: Any, staff: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: the portal used to be derived from `category`, so every 'transactional'
    template (invite, password_reset) previewed in the STAFF brand — though password_reset always
    sends client-branded. The audience is the caller's to state, and it defaults to client."""
    principal = email_support.principal(staff)
    calls: list[str] = []
    monkeypatch.setattr(email_mod, "brand_for", _recording_brand(calls))
    async with session_factory() as session:
        uid = await _new_template(session, principal, category="transactional")
        body = et.PreviewEmail(html="<p>From {{ brand.name }}</p>")
        assert body.audience == "client"  # the default is explicit in the contract
        default = await et.preview_email_template(
            uid=uid, body=body, principal=principal, session=session
        )
        staff_view = await et.preview_email_template(
            uid=uid,
            body=et.PreviewEmail(html="<p>From {{ brand.name }}</p>", audience="staff"),
            principal=principal,
            session=session,
        )
    assert calls == ["client", "platform"]
    assert "Brand::client" in default.html
    assert "Brand::platform" in staff_view.html


# ── the cross-language pin ────────────────────────────────────────────────────


@pytest.mark.pkgs_ui_pin
class TestPlaceholderListMatchesTheEditor:
    """THE CROSS-LANGUAGE PIN. `email_content.EDITOR_PLACEHOLDERS` transcribes the block defaults in
    `pkgs/ui/src/lib/components/email-editor/create.ts`, and the API container ships without
    pkgs/ui, so Python cannot read them at runtime — the same idiom as `block-contract.json`.

    The direction that matters: **a string we refuse must still exist in the editor.** Reworded
    there and not here, the gate quietly stops catching the defect it was built for and nothing
    fails — precisely how "Add note content here." reached a real inbox. And it must still be a
    WHOLE element's visible text, because that is what `placeholder_copy` matches on: a Note Box
    re-cut as two cells would satisfy a substring check and defeat the gate.
    """

    CREATE_TS = (
        Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/components/email-editor/create.ts"
    )

    @classmethod
    def _template_literals(cls) -> list[str]:
        """Every backtick literal in the palette's own region of create.ts: `STARTER_HTML`, the
        shared markup the blocks compose from (`tbl`/`row`/`slot`, `BRAND_LOGO_HTML`,
        `SECTION_HTML`), and the `EMAIL_BLOCKS` array itself, ending at that array's own closing
        bracket.

        THE END LANDMARK IS THAT BRACKET, not the next declaration. Some 330 lines of editor
        plumbing sit between `EMAIL_BLOCKS` and `registerEmailBlocks` — an inline SVG, a
        `url("${painted}")` background, `fieldSpanHtml`'s attribute fragments, an image tooltip —
        and none of it is palette copy, so ending the region there enrols all of it, plus whatever a
        later change drops into that gap, in the segment set the pins below compare against. A
        top-level `];` is where the array really ends and no formatter can move it off column 0,
        which is why the end is anchored on it rather than on a neighbouring name.

        COMMENTS ARE STRIPPED FIRST, and that is not tidiness: create.ts explains itself in prose
        that quotes identifiers in backtick pairs, and the great majority of the region's backticks
        are that prose rather than literal delimiters — so pairing them off blindly matches comment
        text as markup and shifts every later pair by one. That is how this parse can look like it
        works and read nothing."""
        # Every `//` comment is a whole line; looser matching would eat an `http://…` literal, and
        # `test_no_comment_in_the_region_carries_a_backtick` holds that assumption true.
        return re.findall(
            r"`([^`]*)`",
            "\n".join(
                line for line in cls._region().splitlines() if not line.lstrip().startswith("//")
            ),
        )

    @classmethod
    def _region(cls) -> str:
        """The palette's own source text, block comments already removed."""
        source = cls.CREATE_TS.read_text(encoding="utf-8")
        region = source.split("export const STARTER_HTML", 1)[1]
        before_blocks, blocks = region.split("export const EMAIL_BLOCKS", 1)
        region = before_blocks + blocks.split("\n];", 1)[0]
        return re.sub(r"/\*.*?\*/", "", region, flags=re.S)  # JSDoc + block comments

    @classmethod
    def _segments(cls) -> set[str]:
        """Every whole-element visible text the palette produces, in comparison form. Each literal
        is scanned SEPARATELY (concatenating would join text across unrelated blocks) through the
        gate's own scanner, so the two sides are compared by exactly the rule the gate applies."""
        segments: set[str] = set()
        for literal in cls._template_literals():
            scanner = email_content._SegmentScanner()
            scanner.feed(literal)
            scanner.close()
            segments |= {email_content._normalise(s) for s in scanner.segments}
        segments.discard("")
        return segments

    def test_every_refused_placeholder_still_exists_in_the_editor(self) -> None:
        segments = self._segments()
        for placeholder in email_content.EDITOR_PLACEHOLDERS:
            assert email_content._normalise(placeholder) in segments, (
                f"{placeholder!r} is no longer a block default in {self.CREATE_TS.name} — the "
                "publish gate has stopped catching it. Update EDITOR_PLACEHOLDERS and this test "
                "together (email/content.py explains the list)."
            )

    def test_the_button_label_is_a_block_default_we_deliberately_allow(self) -> None:
        """Pins the ONE exclusion AS an exclusion: "Open portal" is a palette default like the rest,
        so its absence from the list must be a decision on the record, not an oversight."""
        assert email_content._normalise("Open portal") in self._segments()
        assert "Open portal" not in email_content.EDITOR_PLACEHOLDERS

    def test_the_parse_finds_the_blocks_so_the_above_cannot_pass_vacuously(self) -> None:
        """Reading another language's source is brittle by construction, so a parse that has quietly
        stopped finding anything must fail HERE rather than let the assertions above pass on an
        empty set."""
        source = self.CREATE_TS.read_text(encoding="utf-8")
        for landmark in ("export const STARTER_HTML", "export const EMAIL_BLOCKS"):
            assert landmark in source  # the two names the parse navigates by
        # And the third landmark, which is not a name: the array's own closing bracket, where the
        # region stops.
        assert "\n];" in source.split("export const EMAIL_BLOCKS", 1)[1]
        # EXACT counts, never floors. The comment stripper is line-based, so a `//` on a line that
        # also holds a backtick desynchronises every later pair, and the parse then reads prose as
        # markup while still returning plenty of both — a floor is satisfied by a fraction of these
        # numbers, so it cannot tell that outcome from a healthy one. An exact count can. Both
        # figures move legitimately when a palette block is added or reworded, so update them
        # alongside EDITOR_PLACEHOLDERS.
        # Both moved when the Brand icon block was added — four more literals (its concatenated
        # parts, one of them the arm attribute) and two more segments — and both moved BACK when
        # that block was deleted. There is ONE brand block: `brand.logo_url` is the resolved
        # masthead, so a second block on `brand.icon_url` was a second spelling of the same idea
        # that ignored the firm's own `logo_variant`. The segments are interpolation fragments,
        # not authored copy — an `<img>` has no visible text of its own — which is the same reason
        # `${cells}` and `${rows}` are already in the set.
        assert len(self._template_literals()) == 24
        assert len(self._segments()) == 24

    def test_no_comment_in_the_region_carries_a_backtick(self) -> None:
        """The stripper's own assumption, asserted rather than hoped for.

        It drops a line only when the line STARTS with `//`, on the stated ground that every comment
        in this region is a whole line — looser matching would eat an `http://…` inside a literal.
        A trailing `//` is therefore kept, comment text and all, so a backtick inside one shifts
        every later pair and the parse starts reading prose as markup.

        The exact counts above would catch the shift; this names it. A count that has moved says
        only "24 became 23", which reads as a palette edit — the likeliest first guess, and wrong.
        """
        offenders = []
        for line in self._region().splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            code, _, comment = line.partition("//")
            if comment and "`" in comment and code.count("`") % 2 == 0:
                offenders.append(line.strip()[:90])
        assert not offenders, (
            "a trailing `//` comment in the palette region carries a backtick, which "
            f"desynchronises the literal pairing: {offenders}. Put the comment on its own line."
        )


# --- A PUBLISHED TEMPLATE MUST BE RENDERABLE -------------------------------------------------
#
# Publish gated on subject / non-empty body / placeholder copy, and NOTHING on the send path catches
# a Jinja raise. So an unrenderable template published clean and then wedged the notification tail:
# `consume_once` reads a batch and advances its cursor in ONE transaction, so the raise rolled the
# cursor back, the next pass re-read the same event, and no notification email sent for ANY tenant —
# the cursor is a single global row. Two guards now, independent of each other: publish refuses,
# and the consumer
# isolates each event behind a savepoint so a survivor costs one email rather than all of them.


def test_a_template_that_cannot_render_is_named_as_the_reason() -> None:
    """The Jinja error reaches the author, who is looking at the template — not a client."""
    from app.email import templates_service as svc

    for body, needle in [
        ("<p>Hi {{ client-ref }}</p>", "client"),
        ("<p>{% if %}</p>", "Expected an expression"),
        ("<p>{{ 1/0 }}</p>", "division"),
    ]:
        failure = svc._render_failure(
            {"subject": "Hi", "preheader": "", "schema": {"html": body}}, "invite"
        )
        assert failure is not None, body
        assert needle in failure


def test_the_subject_is_checked_too() -> None:
    """A subject is rendered at send exactly like the body, and breaks the send just as hard."""
    from app.email import templates_service as svc

    assert svc._render_failure(
        {"subject": "{{ a-b }}", "preheader": "", "schema": {"html": "<p>ok</p>"}}, "invite"
    )


def test_the_PREHEADER_is_checked_too() -> None:
    """It is Jinja and it is rendered on every send (`email_render.render_published_email`), and the
    gate did not look at it. That omission was not cosmetic: `render_or_default` calls
    `render_published_email` with no try/except and falls back only on a missing ROW, so a Jinja
    error in the preview line propagates out of the send site — on `invite` that is `provisioning`,
    which does not catch it either. A published template with `{% if x %}` and no `{% endif %}` in
    its preview line answered 500 and sent no invite at all, for every member from then on.

    The key is read STRICTLY (`row["preheader"]`, not `.get`) so a caller that forgets to SELECT it
    fails loudly rather than silently skipping the check — which is exactly how this was missed.
    """
    from app.email import templates_service as svc

    for line, needle in [
        # The unclosed tag: Jinja names the block it is still waiting to close.
        ("{% if x %}", "endif"),
        # The hyphen Jinja reads as subtraction, which is how an author reaches this by accident.
        ("{{ a-b }}", "undefined"),
    ]:
        failure = svc._render_failure(
            {"subject": "Hi", "preheader": line, "schema": {"html": "<p>ok</p>"}}, "invite"
        )
        assert failure is not None, line
        assert needle in failure

    # And a token that merely merges to nothing is still fine, as everywhere else.
    assert (
        svc._render_failure(
            {"subject": "Hi", "preheader": "{{ message }}", "schema": {"html": "<p>ok</p>"}},
            "invite",
        )
        is None
    )


def test_a_blank_merge_value_is_NOT_a_failure() -> None:
    """Only a RAISE is. A body of `{{ message }}` merges to nothing when nobody wrote a note, which
    is an ordinary send that the shipped-default fallback already covers."""
    from app.email import templates_service as svc

    assert (
        svc._render_failure(
            {
                "subject": "Hi",
                "preheader": "",
                "schema": {"html": "<p>{{ message }}{{ nope.missing }}</p>"},
            },
            "invite",
        )
        is None
    )


def test_the_consumer_isolates_each_event_and_keeps_its_cursor() -> None:
    """Read off the source: a savepoint per event, the failure logged and swallowed, and the cursor
    updated OUTSIDE the per-event block. Without all three, one bad template stops all mail."""
    import inspect

    from app.notifications import email_consumer

    src = inspect.getsource(email_consumer.consume_once)
    assert "async with session.begin_nested():" in src
    assert "logger.exception(" in src
    # The cursor UPDATE must not be inside the try — it is what makes a poison pill survivable.
    body = src.split("for event in events:")[1]
    assert "UPDATE notifications.cursor" in body
    assert body.index("except Exception:") < body.index("UPDATE notifications.cursor")


# ── the soft warning beside the gate: reported, never refused ────────────────────────────────


def test_an_unsourced_token_publishes_and_is_named() -> None:
    """The gate above refuses only a RAISE; this names what will render BLANK.

    THE DEFECT THIS CLOSES: publish renders all three parts and asks only "did it raise", so a
    hand-typed token nothing supplies published clean and rendered empty on every send. The list was
    reachable only by opening the template AND opening the Preview dialog — so a template published
    without opening Preview shipped its blanks unremarked. A real one is already stored: a published
    `notification` version whose preview line reads `… — {{ client.name }}` sends as `… — `.
    """
    from app.email import templates_service as svc

    row = {
        "subject": "Hi {{ first_name }}",
        "preheader": "A note — {{ client.name }}",
        "schema": {"html": "<p>{{ message }} {{ nope }}</p>"},
    }
    # Still publishable: an unknown token is not a failure, which the test above pins.
    assert svc._render_failure(row, "document_sent") is None
    named = svc.unsourced_tokens(row, "document_sent")
    # WHATEVER WAS UNDEFINED IS NAMED, which for a dotted path is the first segment that was not
    # there: `{{ client.name }}` reports `client`, because nothing supplies a `client` scope at all.
    # Where the ROOT does exist the LEAF is named instead — `{{ brand.nope }}` reports `nope` —
    # so an author reads the offending word either way, not a path they did not type.
    assert named == ["client", "nope"], named
    # `message` and `first_name` ARE supplied by that code's send site, so neither is named.
    assert "message" not in named and "first_name" not in named, named


def test_the_offending_name_is_reported_at_whichever_level_is_undefined() -> None:
    """The root when the scope is absent, the leaf when only the attribute is — pinned because the
    two read very differently to an author, and the distinction is not obvious from the name."""
    from app.email import templates_service as svc

    def named(html: str) -> list[str]:
        return svc.unsourced_tokens(
            {"subject": "s", "preheader": "", "schema": {"html": html}}, "document_sent"
        )

    assert named("<p>{{ client.name }}</p>") == ["client"]  # no `client` scope exists
    assert named("<p>{{ brand.nope }}</p>") == ["nope"]  # `brand` exists, the attribute does not


def test_a_present_but_empty_value_is_not_reported_as_unsourced() -> None:
    """`password_reset` deliberately carries no name — all three spellings are supplied as None, so
    they are DEFINED and render blank on purpose. Reporting them would tell an author their correct
    template is misspelled, which is the distinction `render_with_diagnostics` exists to keep."""
    from app.email import templates_service as svc

    named = svc.unsourced_tokens(
        {"subject": "Reset", "preheader": "", "schema": {"html": "<p>{{ first_name }}</p>"}},
        "password_reset",
    )
    assert named == [], named


def test_nothing_is_reported_for_a_code_the_catalogue_does_not_know() -> None:
    """A custom code's sample context is every sample key there is — permissiveness so any token can
    be previewed, not a claim about a send site. Such a code sends through the flow action against
    flow facts, so a list derived from that context would be noise in both directions."""
    from app.email import templates_service as svc

    assert (
        svc.unsourced_tokens(
            {"subject": "x", "preheader": "", "schema": {"html": "<p>{{ anything }}</p>"}},
            "quarterly_statement",
        )
        == []
    )


def test_the_warning_rides_on_the_publish_answer() -> None:
    """Both publish doors must carry it, or the moment an author is looking passes unremarked."""
    import inspect

    from app.email import templates_service as svc
    from app.routers import admin, email_templates

    assert 'detail["unsourced"]' in inspect.getsource(svc.publish_version)
    for module, name in (
        (email_templates, "publish_email_template_version"),
        (admin, "admin_publish_email_template_version"),
    ):
        source = inspect.getsource(getattr(module, name))
        assert "PublishEmailResultOut" in source, f"{name} does not answer with the warning"


def test_a_contains_condition_survives_a_null_or_numeric_field() -> None:
    """The compiled shape the editor writes, executed against the REAL engine.

    THE DEFECT THIS CLOSES. `contains` compiled to a bare ``'x' in field``, which raises
    ``TypeError: argument of type 'NoneType' is not a container`` the moment the field is null — and
    null is ordinary: ``first_name`` comes straight off a nullable column on the invite path. The
    publish gate could not catch it: `_render_failure` renders against `sample_context`, where every
    field holds a string, so such a condition published CLEAN and then answered 500 with no invite
    sent for the first recipient who had no first name.

    Pinned on the Python side as well as in `conditions/model.test.ts` because the TS test can only
    assert the STRING it generates; only executing it proves the string is renderable. The exact
    spelling lives there — this asserts the property that matters whatever the spelling becomes.
    """
    from app.render import jinja_engine

    # As `conditions/model.ts::ruleExpression` compiles `contains`.
    compiled = (
        "{% if 'Dr' in ((first_name if first_name is not none else '') | string) %}Y{% endif %}"
    )
    for label, value, expected in (
        ("a match", "Dr Sarah", "Y"),
        ("a miss", "Sarah", ""),
        ("null — the case that used to 500", None, ""),
        ("numeric — the same raise", 123, ""),
    ):
        assert jinja_engine.render(compiled, {"first_name": value}) == expected, label

    # A field of 0 still CONTAINS '0': the guard is on none, never on falsiness.
    zero = "{% if '0' in ((n if n is not none else '') | string) %}Y{% endif %}"
    assert jinja_engine.render(zero, {"n": 0}) == "Y"
    # And a null does not answer TRUE for the literal "None", which stringifying it would have done.
    none_probe = "{% if 'None' in ((n if n is not none else '') | string) %}Y{% endif %}"
    assert jinja_engine.render(none_probe, {"n": None}) == ""
