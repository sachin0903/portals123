"""Render a tenant's email — its own published template, or the shipped default, THE SAME WAY.

The store (``email.templates``) holds firm-edited, versioned templates. ``render_published_email``
resolves the tenant's PUBLISHED template for a given ``code`` (e.g. ``invite``) and renders it,
reusing the document pipeline's Jinja merge for the subject/preheader/body and then folding CSS
inline for email clients (``app/email/document.py``). It returns ``None`` when there is no such
template, so the send path is fail-safe: a firm that has edited nothing still gets a real email.

That fallback — ``render_default`` — is the SAME MACHINE. The shipped defaults are authored once, in
token form, in ``app/email/defaults.py``, and rendering one against a send site's context is what
produces the email. There is no per-code Python builder behind it: deriving the sent form from the
authored one means the editor's "Load default" canvas and the un-overridden send cannot say
different things — not because a test checks that they match, but because they are one string
rendered twice.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import defaults as email_defaults
from app.email import transport as email_transport
from app.email.content import has_renderable_content
from app.email.document import to_email_html
from app.render import jinja_engine


class RenderedEmail(NamedTuple):
    subject: str
    preheader: str
    html: str


def _with_derived(code: str, context: dict[str, Any]) -> dict[str, Any]:
    """``context`` plus the tokens ``code`` DERIVES rather than receives.

    IN THE TWO LEAF RENDERERS, not in ``render_or_default`` above them, and that placement is the
    point: a firm's own published template needs these exactly as much as the shipped default does —
    `{{ request_summary }}` is an ordinary merge field and an author may put it in their own subject
    — and doing it once per branch is how the two come to disagree. Every path that renders an email
    goes through one of the two, so there is no way to reach a renderer without them.

    Total: a code with nothing to derive answers `{}`, so no caller has to know which codes have
    derived tokens.
    """
    derived = email_defaults.derived_tokens(code, context)
    return {**context, **derived} if derived else context


async def render_published_email(
    session: AsyncSession,
    *,
    tenant_id: int,
    code: str,
    context: dict[str, Any],
    asset_origin: str | None = None,
) -> RenderedEmail | None:
    """The tenant's published template for ``code``, merged with ``context`` and inlined — or None.

    ``context`` is the same merge context the document pipeline builds (``client``/``adviser``/
    ``firm``/… plus template-specific values); subject, preheader and the body HTML all carry Jinja
    tokens and are merged identically.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.subject, v.preheader, v.schema "
                    "FROM email.templates t "
                    "JOIN email.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.tenant_id = :tid AND t.code = :code AND t.archived_at IS NULL"
                ),
                {"tid": tenant_id, "code": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    context = _with_derived(code, context)
    body_html = jinja_engine.render(str(schema.get("html") or ""), context)
    # Note the asymmetry with the subject below: the BODY is HTML (autoescape on, so a client's own
    # name cannot inject markup); the SUBJECT is a header value, rendered as plain text.
    # Checked AFTER the merge, not at publish alone: publish refuses a blank body, but a body of
    # `{{ message }}` merges to nothing when the adviser wrote no note. None falls back to the
    # shipped default rather than sending a subject with an empty body.
    if not has_renderable_content(body_html):
        return None
    css = str(schema.get("css") or "")
    preheader = jinja_engine.render(str(row["preheader"] or ""), context)
    return RenderedEmail(
        subject=jinja_engine.render_text(str(row["subject"] or ""), context),
        preheader=preheader,
        # Ship the preheader as the hidden preview-text line inside the email, not just the record.
        html=to_email_html(body_html, css, preheader=preheader, asset_origin=asset_origin),
    )


def sendable(
    body_html: str, *, css: str = "", preheader: str = "", asset_origin: str | None
) -> str:
    """A body FRAGMENT put in the complete client-safe document every send path delivers: CSS folded
    inline, the email base reset applied, an optional hidden preheader span prepended.

    This is the wrapping seam itself, for the send sites that have no catalogue ``code`` to look a
    shipped preheader up by — today that is the flow tier's ``send_email`` action, which renders a
    firm's own arbitrary template and so has no shipped default behind it.

    Anything that DOES have a code must go through ``sendable_default``/``render_or_default``
    instead, so the shipped preheader cannot be forgotten. Every send site is held to one or the
    other by ``tests/test_email_send_parity.py``, which discovers them by scanning for
    ``email.send_email`` calls: a body FRAGMENT posted straight to Resend arrives with no doctype,
    no charset, none of the Outlook base resets and no preheader.

    ``css`` is the template's own stylesheet, folded inline like every other path does
    (``render_published_email``). It was not a parameter at all, so the flow tier delivered a firm's
    template UNSTYLED while a notification rule delivered the same template styled."""
    return to_email_html(body_html, css, preheader=preheader, asset_origin=asset_origin)


def sendable_default(code: str, default_html: str, *, asset_origin: str | None) -> str:
    """A shipped default's merged body put in the SAME envelope every other path uses: CSS folded
    inline, the shipped preview-text line prepended as the hidden preheader span, and the whole
    thing wrapped in a complete client-safe document.

    The path most firms are actually on is the un-overridden one, so this is where a fragment
    reaching Resend hurts most: no doctype, no charset meta, none of the email base reset that
    normalises tables and linked images across clients, and no preheader — while the preview the
    author had just looked at and the published-override send both deliver a full document.

    Additive by construction, and proved so rather than assumed: the shipped defaults are wholly
    inline-styled, so the inliner changes no declaration's value and drops none — see
    ``tests/test_email_send_parity.py::test_wrapping_the_shipped_default_changes_nothing_visual``.

    Takes ``code`` rather than a preheader string so no caller can forget it, and only
    :func:`render_default` calls it, so there is nowhere left to forget it from.
    """
    return sendable(
        default_html, preheader=email_defaults.preheader_for(code), asset_origin=asset_origin
    )


def render_default(
    code: str, context: dict[str, Any], *, asset_origin: str | None
) -> tuple[str, str]:
    """``(subject, html)`` for the SHIPPED default of ``code`` — the email a firm with no published
    override receives, as a complete sendable document.

    The default is rendered, not built: ``email_defaults.default_for`` holds one token authoring per
    code and this merges it through the SAME Jinja pass, with the same filters and the same
    autoescape rules, that ``render_published_email`` runs on a firm's own template. So the shipped
    default is not a privileged shape — it is the first template, and the editor's "Load default"
    hands the author the very characters that were about to send.

    The subject goes through ``render_text`` (autoescape off): it is a header value, not HTML, and a
    firm called "Ridley & Co" must not appear in an inbox as "Ridley &amp; Co".

    Raises ``ValueError`` for a code with no shipped default. That is a programming error, not a
    runtime condition — every catalogue code has one, pinned by
    ``tests/test_email/defaults.py::test_every_catalogue_code_has_a_default`` — and returning None
    would push the decision onto a send site that has nothing to fall back to."""
    context = _with_derived(code, context)
    default = email_defaults.default_for(code)
    if default is None:
        raise ValueError(
            f"no shipped default for email code {code!r} — add one in app/email/defaults.py "
            "(and to app/email/catalog.py, or the firm cannot override it)"
        )
    return (
        jinja_engine.render_text(default.subject, context),
        sendable_default(
            code, jinja_engine.render(default.html, context), asset_origin=asset_origin
        ),
    )


async def render_or_default(
    session: AsyncSession, *, tenant_id: int, code: str, context: dict[str, Any]
) -> tuple[str, str]:
    """``(subject, html)`` from the tenant's published template for ``code``, else the shipped
    default. Fail-safe by design: a firm that hasn't published an override keeps getting the shipped
    default, so wiring this into a send site can never regress delivery. The fallback also covers an
    override that renders EMPTY for this particular send -- see ``render_published_email`` -- so a
    client never receives a blank body.

    A send site passes only the merge ``context`` and never a pre-built default, so ONE context
    feeds both branches: a site that assembled its own default could put a value in one and not the
    other.

    Both branches return a COMPLETE email document, so which branch a send took is not something a
    recipient (or an author checking the preview) can tell from the shape of what arrives."""
    # Resolved ONCE, above the branch, and handed to both: a firm's own template and the shipped
    # default can each carry an author's uploaded image, and resolving it per branch is how the two
    # come to disagree — the reason `_with_derived` gives for sitting where it does, applied to the
    # other direction. One extra read per send, cached nowhere on purpose: a host change must not
    # wait for a TTL to stop pointing mail at the old one.
    origin = await email_transport.asset_origin(session, tenant_id)
    rendered = await render_published_email(
        session, tenant_id=tenant_id, code=code, context=context, asset_origin=origin
    )
    if rendered is not None:
        return rendered.subject, rendered.html
    return render_default(code, context, asset_origin=origin)
