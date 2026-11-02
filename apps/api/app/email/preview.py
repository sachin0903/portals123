"""Email-template PREVIEW — merge the editor's current content against sample values and inline it.

Preview renders the content the author is editing RIGHT NOW (the live, possibly-unsaved canvas)
against representative sample values, so they see the merged, email-safe result exactly as a client
would receive it — WITHOUT sending anything. This module deliberately has no send path: it only
Jinja-merges (``app/render/jinja_engine.py``) and folds CSS inline (``app/email/document.py``), text
in → text out. The firm's real brand (name/logo/accent) is passed in so the preview is on-brand.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, NamedTuple

from app.email import defaults as email_defaults
from app.email.document import to_email_html
from app.email.transport import Brand, brand_tokens
from app.render import jinja_engine

# Fixed reference date, so a preview and its tests are deterministic rather than about "today".
_TODAY = date(2026, 1, 15)


# Samples for the FLAT tokens the send sites pass (brand aside) — keys mirror their call sites.
_SAMPLE: dict[str, Any] = {
    "first_name": "Sarah",
    # Three spellings of the same person, because a greeting and a formal line want different ones:
    # "Hi Sarah" vs "Dear Ms Whitfield". Every site that resolves a name can answer all three.
    "full_name": "Sarah Whitfield",
    "surname": "Whitfield",
    "sent_by": "James Fletcher",  # the adviser who sent a document
    "portal_label": "Client Portal",
    "portal_url": "https://portal.example.com",
    "set_password_url": "https://portal.example.com/set-password?token=sample-token",
    "document_name": "Suitability Report",
    "item_titles": ["Proof of identity", "Proof of address", "Attitude-to-risk questionnaire"],
    "due_date": (_TODAY + timedelta(days=7)).isoformat(),
    "message": "Thanks — let me know if you have any questions.",  # optional adviser covering note
    # ONE set for both task codes: they take the same keys and differ only in their authored copy.
    "task_title": "Review Q2 suitability report",
    "task_description": "Check the updated attitude-to-risk answers before the annual review call.",
    "button_label": "Open your portal",
    "button_url": "https://portal.example.com",
    "title": "A document was shared with you",
    "body": "Your adviser has shared a document. Open your portal to view it.",
    # What ``email_consumer._template_context`` also passes; an unsampled key reads ``missing``.
    "shared_by": "James Fletcher",  # the actor on a share, where ``sent_by`` is the sender
    "account_name": "Sarah Whitfield ISA",
    "account_number": "SW-0041827",
}

# A sample that would be a LIE: ``password_reset`` carries no name — an account-existence oracle.
_CODE_SAMPLE_OVERRIDES: dict[str, dict[str, Any]] = {
    "password_reset": {"first_name": None, "full_name": None, "surname": None},
}

# Each code's real send-site keys, so ``missing`` catches a token no send supplies. Keep in step.
_CODE_KEYS: dict[str, list[str]] = {
    "invite": ["first_name", "full_name", "surname", "portal_label", "set_password_url"],
    "password_reset": ["first_name", "full_name", "surname", "set_password_url"],
    "document_sent": [
        "first_name",
        "full_name",
        "surname",
        "document_name",
        "sent_by",
        "message",
        "portal_url",
    ],
    "request_raised": [
        "first_name",
        "full_name",
        "surname",
        "item_titles",
        "due_date",
        "message",
        "portal_url",
    ],
    # The same five keys on both: one notifier context picks between the codes (`_send_one`).
    "task_assigned": [
        "task_title",
        "task_description",
        "due_date",
        "button_label",
        "button_url",
    ],
    "task_reminder": [
        "task_title",
        "task_description",
        "due_date",
        "button_label",
        "button_url",
    ],
    # Longest here, not shortest; pkgs/ui's catalogue mirrors it, so an omission bites both sides.
    "notification": [
        "title",
        "body",
        "button_label",
        "button_url",
        "first_name",
        "full_name",
        "surname",
        "document_name",
        "sent_by",
        "shared_by",
        "task_title",
        "task_description",
        "account_name",
        "account_number",
        "portal_url",
        "message",
        "item_titles",
        "due_date",
    ],
}


def scoped_codes() -> frozenset[str]:
    """The codes whose real send-site vocabulary is known here.

    Asked by anything that needs to tell "this code's send site provides exactly these keys" from
    "we have no idea what fills this one". For an UNSCOPED code :func:`sample_context` answers with
    every sample key it holds — permissiveness so any token can be previewed, never a claim about a
    send site — so a caller deriving a judgement from that context must check this first.

    Deliberately not ``send_site_keys``' emptiness, which answers the same question by comparing two
    sample contexts: that comparison is two keys away from being accidentally true for the wrapper
    code, and a judgement that silently switches itself off is worse than none.
    """
    return frozenset(_CODE_KEYS)


def sample_context(
    brand: Brand,
    code: str | None = None,
    *,
    viewer_first_name: str | None = None,
) -> dict[str, Any]:
    """Representative merge values for a preview. ``brand`` is the firm's REAL resolved identity
    (nested, as every send site passes it). For a KNOWN ``code`` only that code's send-site keys are
    included, so previewing a template that references a token its send site won't provide surfaces
    it as ``missing`` rather than filling it (which would mislead — it renders blank at real send).
    An unknown/None code (a custom code) gets all sample keys, so any token can be previewed.

    ``viewer_first_name`` replaces the sampled ``first_name`` with the name of the person looking at
    (or being sent) the email. A test send lands in the author's own inbox, and one addressed "Hi
    Sarah," reads as mail for somebody else. Their own name is also a better sample than a fixture:
    it is a real name, of real length, and it makes a broken ``{{ first_name }}`` obvious rather
    than plausible.

    It is an explicit argument rather than something read from the session here, so the sampled
    values stay deterministic for the tests that call this directly — a preview whose fixtures move
    with whoever is logged in is not a fixture. Absent (or blank), "Sarah" stands.

    An override in :data:`_CODE_SAMPLE_OVERRIDES` BEATS the viewer's name, which is what keeps
    ``password_reset`` nameless for its author too — see there for why that send carries no name.

    ``brand`` is passed through EXACTLY as the send sites pass it — including a null
    ``support_email``. Never substitute a plausible-looking stand-in: an author shown a support
    address in the footer that the real send cannot produce is the worst kind of preview bug, and
    the empty footer IS the truthful signal that the firm has no support email set."""
    keys = _CODE_KEYS[code] if code in _CODE_KEYS else list(_SAMPLE)
    overrides = _CODE_SAMPLE_OVERRIDES.get(code or "", {})
    # `brand_tokens`, as the send sites use, so neither can offer a differently-shaped `brand`.
    ctx: dict[str, Any] = {"brand": brand_tokens(brand)}
    viewer = (viewer_first_name or "").strip()
    for key in keys:
        if key == "first_name" and viewer and "first_name" not in overrides:
            ctx[key] = viewer
            continue
        ctx[key] = overrides[key] if key in overrides else _SAMPLE[key]
    # Derived the way the send derives it: a sample would let the preview and the inbox disagree.
    ctx.update(email_defaults.derived_tokens(code or "", ctx))
    return ctx


class PreviewResult(NamedTuple):
    subject: str
    preheader: str
    html: str
    # Surfaced so an author can tell a genuinely-blank field from a typo'd or unsupported token.
    missing: list[str]


def render_preview(
    *,
    subject: str,
    preheader: str,
    html: str,
    css: str,
    brand: Brand,
    code: str | None = None,
    viewer_first_name: str | None = None,
) -> PreviewResult:
    """Merge subject / preheader / body against :func:`sample_context` (scoped to ``code``'s
    tokens), inline the CSS for email clients, and return the rendered pieces plus the tokens that
    resolved to empty. Never sends.

    ``viewer_first_name`` is threaded through to the sample context — see there. Both the preview
    and the test send call THIS function, so passing it here is what keeps the two showing the same
    name; substituting it only for the send would make an author's inbox disagree with the preview
    they had just been looking at."""
    ctx = sample_context(brand, code, viewer_first_name=viewer_first_name)
    # Plain text on the wire, so autoescape is OFF as on both send paths: not "Ridley &amp; Co".
    subject_out, missing_s = jinja_engine.render_with_diagnostics(subject, ctx, autoescape=False)
    preheader_out, missing_p = jinja_engine.render_with_diagnostics(preheader, ctx)
    body_out, missing_b = jinja_engine.render_with_diagnostics(html, ctx)
    missing = sorted(set(missing_s) | set(missing_p) | set(missing_b))
    return PreviewResult(
        subject=subject_out,
        preheader=preheader_out,
        # Include the hidden preview-text span so the preview matches what actually sends.
        # `asset_origin=None` — the ONE renderer that wants the paths left relative. A preview is
        # displayed inside the portal that stored the image, so `/_assets/{name}` resolves there
        # (and an absolute URL to the same host would fetch the same bytes); it is a SEND that has
        # no origin of its own. Stated rather than defaulted, because the parameter exists to make
        # every renderer answer the question.
        html=to_email_html(body_out, css, preheader=preheader_out, asset_origin=None),
        missing=missing,
    )
