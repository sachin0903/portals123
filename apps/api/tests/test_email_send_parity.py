"""Cross-path parity: what an author previews is what a client receives.

Three paths must agree, and only this module compares ACROSS them: PREVIEW
(`email_preview.render_preview`, also the `[TEST]` send), the un-overridden SEND
(`render_or_default` → `render_default`), and the SEND resolving a firm's published override. The
pieces are covered elsewhere and the shipped copy is pinned as literals in
`test_email_shipped_copy_golden.py`; here it is the comparison, for every catalogue code.

`_CODE_KEYS` being exactly the keys each send site passes is what makes that comparison mean
anything, so it is pinned below rather than assumed.

**DISCOVERY IS OFF `email.send_email`, NOT `render_or_default`.** Scanning for the render seam can
only find the sites already on it, which is how a fourth path — the flow tier's `send_email`
action, posting a raw `email_shell.shell` fragment to real clients with no doctype, no charset, no
Outlook resets and no preheader — stayed invisible to this suite. Scan the SENDS.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from app.email import content as email_content
from app.email import defaults as email_defaults
from app.email import document as render_email
from app.email import preview as email_preview
from app.email import render as email_render
from app.email import shell as email_shell
from app.email.catalog import KNOWN_CODES
from app.email.preview import _CODE_KEYS
from app.email.transport import Brand
from app.render import jinja_engine
from tests import email_support

API_ROOT = Path(__file__).resolve().parents[1]  # apps/api
APP = API_ROOT / "app"

CODES = [c.code for c in KNOWN_CODES]

# Nothing of Graphene's in it, and `logo_url=None` keeps the name as text an assert can find.
FIRM = Brand(
    name="Ridgeway Wealth", logo_url=None, accent="#0f766e", support_email="hello@ridgeway.test"
)

# Distinctive in the delivered markup, and tokenised so preheader MERGE parity is tested too.
AUTHORED_PREHEADER = "A note from {{ brand.name }} — parity marker"
MERGED_PREHEADER = "A note from Ridgeway Wealth — parity marker"

# Wraps each shipped body in the legacy stylesheet canvas, so inlining is exercised every time.
PROBE_CSS = ".parity-probe{border-left:4px solid #123456;padding-left:11px}"


# Harness


# Fakes the SELECT: real merge and inlining, no Docker. The SQL is in test_email_templates_api.py.


def _published(subject: str, preheader: str, html: str, css: str) -> Any:
    return email_support.FakeRenderSession(
        email_support.published_row(html, subject=subject, preheader=preheader, css=css)
    )


def _prefill(code: str) -> email_defaults.ShippedDefault:
    """The shipped default as the EDITOR prefills it: subject/preheader/html/css, unmerged."""
    default = email_defaults.default_for(code)
    assert default is not None, f"{code}: no shipped default to prefill the editor with"
    return default


#: Keys a real send can pass as `None`: four of this suite's catches surfaced only on an absent one.
NULLABLE_KEYS = frozenset(
    {
        "first_name",
        "sent_by",
        "message",
        "due_date",
        "portal_url",
        "button_url",
        "button_label",
        "document_name",
    }
)


def _ctx(code: str, *, sparse: bool = False) -> dict[str, Any]:
    ctx = dict(email_preview.sample_context(FIRM, code))
    if sparse:
        for key in ctx:
            if key in NULLABLE_KEYS:
                ctx[key] = None
    return ctx


def _canvas(code: str) -> tuple[str, str, str]:
    """(subject, canvas html, css) — the shipped body inside the inlining probe."""
    # By name, not positionally: `ShippedDefault` grew a fifth field; `html` is the one that SENDS.
    default = _prefill(code)
    return (
        default.subject,
        f'<div class="parity-probe">{default.html}</div>',
        PROBE_CSS,
    )


async def _sent(code: str, subject: str, preheader: str, html: str, css: str) -> Any:
    """Path 3: what a client receives when the firm has published exactly this content."""
    rendered = await email_render.render_published_email(
        _published(subject, preheader, html, css),  # type: ignore[arg-type]
        tenant_id=1,
        code=code,
        context=_ctx(code),
    )
    assert rendered is not None, f"{code}: the published override was discarded as empty"
    return rendered


def _preview(code: str, subject: str, preheader: str, html: str, css: str) -> Any:
    """Path 1: what the author sees for exactly this content."""
    return email_preview.render_preview(
        subject=subject, preheader=preheader, html=html, css=css, brand=FIRM, code=code
    )


async def _default_send(code: str, *, sparse: bool = False) -> tuple[str, str]:
    """The un-overridden send, end to end. Driven through the REAL `render_or_default` with no
    published row rather than through `sendable_default` directly: building the expectation from the
    helper would prove only that the helper works, not that the fallback BRANCH uses it."""
    return await email_render.render_or_default(
        email_support.FakeRenderSession(None),  # type: ignore[arg-type]
        tenant_id=1,
        code=code,
        context=_ctx(code, sparse=sparse),
    )


def _shipped_fragment(code: str, ctx: dict[str, Any]) -> str:
    """The shipped default's merged body FRAGMENT — what `sendable_default` then wraps."""
    return jinja_engine.render(_prefill(code).html, ctx)


def _text(html: str) -> str:
    """The visible words, tags and whitespace collapsed — for comparing COPY across two paths that
    legitimately differ in markup."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class _Element(NamedTuple):
    tag: str
    attrs: dict[str, str]
    decls: dict[str, str]


_TAG = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
_ATTR = re.compile(r"""\b([a-zA-Z-]+)=("[^"]*"|'[^']*')""")


def _elements(html: str) -> list[_Element]:
    """Every opening tag as (tag, attributes, style declarations). Quote-agnostic on purpose: an
    author's canvas can carry `style='…'` in single quotes and css-inline normalises them to
    double, which is not a styling change and must not read as one."""
    out: list[_Element] = []
    for match in _TAG.finditer(html):
        attrs = {k: v[1:-1] for k, v in _ATTR.findall(match.group(0))}
        style = attrs.pop("style", "")
        decls: dict[str, str] = {}
        for declaration in style.split(";"):
            if ":" in declaration:
                prop, _, value = declaration.partition(":")
                decls[prop.strip().lower()] = " ".join(value.split())
        out.append(_Element(match.group(1), attrs, decls))
    return out


# The structural pins the parity comparisons rest on


def _call_name(node: ast.AST) -> str:
    """The bare name a call names, `f(…)` or `mod.f(…)` alike (`await` unwrapped)."""
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else str(getattr(func, "id", ""))


def _render_or_default_sites() -> list[str]:
    """Every `render_or_default` call in app/ as `file:line` — the population the brand check below
    has to cover one for one."""
    sites: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if _is_dynamic_template_site(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "render_or_default":
                sites.append(f"{path.relative_to(API_ROOT)}:{node.lineno}")
    return sites


#: Every function that returns a COMPLETE sendable document; adding a name here claims it wraps.
#:
#: `_render_or_422` earns its place by DELEGATION, not by wrapping anything itself: it returns
#: `render_preview`'s result unchanged and only turns a parse failure into a 422 (the router would
#: otherwise 500 on an author's half-written condition). It is named here rather than inlined at
#: both preview call sites, which would mean two copies of the same refusal.
WRAPPING_SEAMS = frozenset(
    {
        "render_or_default",
        "render_default",
        "sendable",
        "sendable_default",
        "render_preview",
        "_render_or_422",
    }
)

#: The outbox relays HTML its producer already wrapped; a new entry must name where it is wrapped.
RELAY_SEND_SITES = frozenset({"app/flows/notifications.py"})

#: A rule chooses the template, so the per-code pins skip it; it still wraps for the envelope pin.
DYNAMIC_TEMPLATE_SITES = frozenset({"app/notifications/email_consumer.py"})


def _is_dynamic_template_site(path: Path) -> bool:
    """A send site exempt from the per-code parity pins — see `DYNAMIC_TEMPLATE_SITES`."""
    return path.relative_to(API_ROOT).as_posix() in DYNAMIC_TEMPLATE_SITES


def _wrapped_locals(func: ast.AST) -> set[str]:
    """The names bound anywhere in `func` from a wrapping-seam call — including the tuple form
    `subject, html = await render_or_default(…)` that every send site uses."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or _call_name(node.value) not in WRAPPING_SEAMS:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple):
                names.update(e.id for e in target.elts if isinstance(e, ast.Name))
    return names


def _send_sites() -> list[tuple[str, str, bool]]:
    """`(file, file:line, is_wrapped)` for every `email.send_email` call in app/ — THE
    population.

    "Wrapped" is read locally: `html=` is either a wrapping-seam call or a name bound from one in
    the same function. Deliberately shallow — a site hiding its wrapping behind a helper reads as
    unwrapped, which is the safe direction and one line of `RELAY_SEND_SITES` to resolve."""
    sites: list[tuple[str, str, bool]] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel == "email/transport.py":  # where send_email is DEFINED
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            wrapped = _wrapped_locals(func)
            for node in ast.walk(func):
                if not isinstance(node, ast.Call) or _call_name(node) != "send_email":
                    continue
                html = next((k.value for k in node.keywords if k.arg == "html"), None)
                base = html.value if isinstance(html, ast.Attribute) else html
                ok = _call_name(html) in WRAPPING_SEAMS or (
                    isinstance(base, ast.Name) and base.id in wrapped
                )
                sites.append((f"app/{rel}", f"{path.relative_to(API_ROOT)}:{node.lineno}", ok))
    return sites


def test_every_send_site_wraps_its_html_in_the_document_envelope() -> None:
    """A site that bypasses the seam must FAIL here, not be invisible: an unwrapped fragment
    arrives with no doctype, no charset, no base reset and no preheader span — from a firm whose
    author previewed a complete document."""
    sites = _send_sites()
    assert sites, "no `email.send_email` call sites found — the discovery itself is broken"
    unwrapped = [where for module, where, ok in sites if not ok and module not in RELAY_SEND_SITES]
    assert not unwrapped, (
        f"These send sites hand `html=` something that did not come through a wrapping seam "
        f"({', '.join(sorted(WRAPPING_SEAMS))}): {unwrapped}. A body FRAGMENT is not a sendable "
        "email — route it through `email_render.render_or_default` (if it has a catalogue code) or "
        "`email_render.sendable` (if it does not), the way every other path does."
    )
    # A stale exemption is a claim the next unwrapped site in that module would hide behind.
    modules = {module for module, _where, _ok in sites}
    assert RELAY_SEND_SITES <= modules, (
        f"RELAY_SEND_SITES names modules that no longer send: {sorted(RELAY_SEND_SITES - modules)}"
    )


def _static_codes(code: ast.expr | None, where: str) -> list[str]:
    """The code(s) a `render_or_default` call can resolve, read STATICALLY.

    Usually one literal. It may also be a two-armed ternary of literals, and that is not a loophole
    but a real send shape: `tasks/notifier.py` sends
    `code="task_reminder" if is_reminder else "task_assigned"` — ONE call whose template is chosen
    by which of the two scans it is in (assignment notice vs near-due chase). The pair used to be a
    single `task_notice` code whose template branched internally on an `is_reminder` merge value;
    the branch moved out of the email and into the code, which is the whole point of the split.

    A NAME IS STILL REFUSED. What this check needs is not "a constant" for its own sake but the
    ability to say, for every code a send site can reach, which context keys it provides — that is
    what keeps `_CODE_KEYS` (and so the preview's sample context and the editor's Insert menu)
    matched to reality. A ternary of literals answers that for both arms; a variable answers it for
    none."""
    if isinstance(code, ast.Constant):
        return [str(code.value)]
    if (
        isinstance(code, ast.IfExp)
        and isinstance(code.body, ast.Constant)
        and isinstance(code.orelse, ast.Constant)
    ):
        return [str(code.body.value), str(code.orelse.value)]
    raise AssertionError(
        f"{where}: render_or_default's `code` is neither a literal nor a two-armed ternary of "
        "literals, so the catalogue and the preview's sample context cannot be checked against it"
    )


#: The two render seams a send site may call, and where each carries `code` and `context`.
#:
#: BOTH, not just the override-resolving one. `render_or_default` needs a tenant to look an override
#: up; a send with no tenant host (an ops reset, local dev) calls `render_default` directly, and
#: `routers/internal.py` has one of each for the same code. Scanning only the first leaves half of
#: `password_reset`'s send sites — a code this check CLAIMS to cover — invisible to it.
#:
#: `render_default` takes both POSITIONALLY, so the index is part of the seam's
#: description.
_RENDER_SEAMS = {
    "render_or_default": {"code": None, "context": None},
    "render_default": {"code": 0, "context": 1},
}


def _seam_arg(call: ast.Call, name: str, index: int | None) -> ast.expr | None:
    """A render seam's argument, whether the call site passed it by keyword or by position."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if index is not None and len(call.args) > index:
        return call.args[index]
    return None


def _render_call_sites() -> list[tuple[str, str, set[str]]]:
    """`(where, code, keys)` for every render-seam call in app/ — ONE ENTRY PER CALL SITE.

    Per site rather than unioned by code, because the union is what hid the hole this scan exists to
    find: two branches sending the same code, one passing fewer keys, and the richer one covering
    for it. Every literal send site of a code must provide that code's whole vocabulary — there is
    one template behind it, so a branch supplying less renders the same template with blanks.

    Scans the package, not a hand-kept list, so a new send site cannot hide from this check.
    """
    found: list[tuple[str, str, set[str]]] = []
    for path in sorted(APP.rglob("*.py")):
        # where the seams are DEFINED: `render_or_default` ends by calling `render_default(code, …)`
        # with its own parameter, which is neither a literal nor a send site.
        if path.relative_to(APP).as_posix() == "email/render.py":
            continue
        if _is_dynamic_template_site(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            seam = _RENDER_SEAMS.get(_call_name(node) or "")
            if seam is None:
                continue
            where = f"{path}:{node.lineno}"
            codes = _static_codes(_seam_arg(node, "code", seam["code"]), where)
            context = _seam_arg(node, "context", seam["context"])
            assert isinstance(context, ast.Dict), (
                f"{where}: the render seam's `context` is not a dict literal, so "
                "the keys this send site provides cannot be read statically"
            )
            keys = {k.value for k in context.keys if isinstance(k, ast.Constant)}
            for code in codes:
                found.append((where, code, keys))
    return found


#: Previewed but never sent literally; `notification`'s context is pinned in test_notifications.py.
#: The two task codes joined them when task emails became rule-driven: the worker scan emits an
#: action event and the notification email subscriber renders the rule's `email_code`, so no send
#: site names them as a literal any more.
DYNAMIC_ONLY_CODES = frozenset(
    {"notification", "document_sent", "request_raised", "task_assigned", "task_reminder"}
)


def test_every_send_site_passes_the_keys_the_preview_samples() -> None:
    """THE load-bearing pin. If `_CODE_KEYS` drifts from the real call sites, the preview
    merges tokens the send leaves raw (or flags as missing tokens the send fills), and every other
    test in this file compares two things that were never the same."""
    sites = _render_call_sites()
    # Dynamic-only codes sit in `_CODE_KEYS` but not the scan; `brand` is universal, so excluded.
    scanned = {code for _, code, _ in sites}
    assert scanned == set(_CODE_KEYS) - DYNAMIC_ONLY_CODES, (
        "codes at the send sites and codes in email_preview._CODE_KEYS disagree: "
        f"only at send sites {sorted(scanned - set(_CODE_KEYS))}, "
        f"only in _CODE_KEYS {sorted(set(_CODE_KEYS) - DYNAMIC_ONLY_CODES - scanned)}, "
        # The third way this can fail, and the one the other two cannot express: a code declared
        # DYNAMIC_ONLY_CODES that has a literal send site again. Both differences above are then
        # empty and the message named nothing — a failure a reader can only read as a broken test.
        f"declared dynamic-only but sent literally {sorted(scanned & DYNAMIC_ONLY_CODES)} "
        "(drop it from DYNAMIC_ONLY_CODES — it is back inside this pin)"
    )
    # EVERY SITE, not the union by code: a code sent from two branches where one passes fewer keys
    # renders the same template with blanks on that branch, and unioning lets the richer branch
    # answer for the poorer one.
    for where, code, keys in sorted(sites):
        provided, sampled = keys - {"brand"}, set(_CODE_KEYS[code])
        assert provided == sampled, (
            f"{where} ({code}): this send site and the preview disagree about the merge context. "
            f"Sent but never previewed: {sorted(provided - sampled)}. "
            f"Previewed but never sent (renders blank for a real recipient): "
            f"{sorted(sampled - provided)}."
        )
        assert "brand" in keys, (
            f"{where} ({code}): the send site does not pass `brand` into the merge context"
        )


def test_every_send_site_puts_the_RESOLVED_firm_brand_in_the_merge_context() -> None:
    """Brand universality at the SOURCE, which is where it broke — twice, for `invite` and
    then for its sibling `password_reset`.

    A MISSING `brand` key already fails the pin above, so what is left is PROVENANCE: the entry must
    be `email_mod.brand_tokens(<resolved brand>)`. A hand-rolled dict is refused because it is the
    per-site duplication that let a key go missing; `brand_tokens(GRAPHENE_BRAND)` because it is the
    white-label leak spelled out (its one legitimate use, `internal.py`'s no-tenant-host reset,
    is on the other branch and calls `render_default` directly)."""
    checked: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if _is_dynamic_template_site(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "render_or_default":
                continue
            where = f"{path.relative_to(API_ROOT)}:{node.lineno}"
            context = next((k.value for k in node.keywords if k.arg == "context"), None)
            assert isinstance(context, ast.Dict), f"{where}: context is not a dict literal"
            brand = next(
                (
                    v
                    for k, v in zip(context.keys, context.values, strict=True)
                    if isinstance(k, ast.Constant) and k.value == "brand"
                ),
                None,
            )
            assert brand is not None, (
                f"{where}: no `brand` in the merge context — every shipped default and every "
                "firm-authored template renders the firm's name, logo and accent from it, so this "
                "send would go out unbranded."
            )
            assert _call_name(brand) == "brand_tokens", (
                f"{where}: `brand` is not `email_mod.brand_tokens(<resolved brand>)`. A "
                "hand-rolled dict is how a key goes missing, and a missing key renders blank — "
                "invisible until a client asks who wrote to them."
            )
            assert isinstance(brand, ast.Call) and not any(
                isinstance(a, ast.Attribute) and a.attr == "GRAPHENE_BRAND" for a in brand.args
            ), (
                f"{where}: this send dresses a TENANT's email in GRAPHENE_BRAND — the white-label "
                "leak that reached production twice. Pass the brand resolved for the tenant."
            )
            checked.append(where)
    assert checked == _render_or_default_sites(), (
        "the brand check did not see every render_or_default call site: "
        f"{checked} vs {_render_or_default_sites()}"
    )


# Path 1 vs path 3 — preview and the published send, same content, same context


@pytest.mark.asyncio
@pytest.mark.parametrize("code", CODES)
async def test_preview_and_the_published_send_are_the_same_email(code: str) -> None:
    """THE promise, as strongly as it can be stated: for the same canvas and merge context,
    the Preview pane and the client's inbox get the SAME BYTES. Not a spot-check, because the two
    paths merge and inline through separately-written code and any wording, escaping or inlining
    difference is a broken promise however small.

    Then the properties byte-equality cannot state on its own, on both paths at once: no unmerged
    token in subject or preheader; the authored preheader DELIVERED (it regressed once as
    recorded-but-not-shipped) inside the hidden preview-text block and FIRST, since the line an
    inbox shows is the document's first text; the firm's brand and accent present and Graphene's
    absent (the white-label leak); the CSS inlined with no `<style>` surviving, since clients drop
    them."""
    subject, canvas, css = _canvas(code)
    preview = _preview(code, subject, AUTHORED_PREHEADER, canvas, css)
    sent = await _sent(code, subject, AUTHORED_PREHEADER, canvas, css)

    if preview.html != sent.html:  # pragma: no cover — the failure path
        marker = next(
            (i for i, (a, b) in enumerate(zip(preview.html, sent.html, strict=False)) if a != b),
            min(len(preview.html), len(sent.html)),
        )
        pytest.fail(
            f"{code}: preview and the delivered email diverge at character {marker}.\n"
            f"  preview: …{preview.html[max(0, marker - 60) : marker + 60]}…\n"
            f"  sent   : …{sent.html[max(0, marker - 60) : marker + 60]}…"
        )
    assert preview.subject == sent.subject, f"{code}: the author previews a different subject"
    assert "{{" not in sent.subject and "{%" not in sent.subject, f"{code}: unmerged subject token"
    assert preview.preheader == sent.preheader == MERGED_PREHEADER, (
        f"{code}: preheader disagreement"
    )

    assert MERGED_PREHEADER in sent.html, (
        f"{code}: the authored preheader is on the render but never reaches the delivered HTML — "
        "the inbox preview line would be whatever the body happens to start with"
    )
    span = sent.html.index(MERGED_PREHEADER)
    assert "display:none" in sent.html[:span], f"{code}: the preheader is visible in the body"
    # Every shipped body opens with the shell's heading, so <h1> marks where the body starts.
    assert span < sent.html.index("<h1"), f"{code}: the preheader span does not come first"

    assert FIRM.name in sent.html, f"{code}: a client would not know who wrote to them"
    assert FIRM.accent in sent.html, f"{code}: the firm's accent colour did not reach the email"
    assert "Graphene" not in sent.html, f"{code}: the white-label leak"

    assert sent.html.startswith("<!DOCTYPE html>"), f"{code}: not a complete document"
    assert "<style" not in sent.html.replace(render_email.MSO_CTA_INK, ""), (
        f"{code}: a <style> block survived beyond the shell's own mso conditional"
    )
    assert "#123456" in sent.html, f"{code}: the template's own CSS was not inlined"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", CODES)
async def test_an_empty_preheader_ships_no_hidden_span_on_either_path(code: str) -> None:
    """The other half: an author who typed no preheader must not get an empty hidden block, which
    some clients render as a leading blank line in the inbox preview."""
    subject, canvas, css = _canvas(code)
    sent = await _sent(code, subject, "", canvas, css)
    assert "display:none" not in sent.html, f"{code}: empty preheader still shipped a hidden span"
    assert _preview(code, subject, "", canvas, css).html == sent.html


@pytest.mark.asyncio
async def test_no_path_html_escapes_the_subject_line() -> None:
    """A subject is a header value, not HTML, and all three paths have to agree about that.

    THIS WAS BROKEN: the override path and the preview merged the subject through the body's
    autoescaping pass, so "Ridley & Co" sending "Bonds & Gilts" got `&amp;` twice in the inbox. The
    shipped default escaped nothing (an f-string), so the two disagreed — and single-sourcing would
    have spread the defect rather than fixing it. All three use `jinja_engine.render_text` now."""
    firm = FIRM._replace(name="Ridley & Co")
    ctx = dict(email_preview.sample_context(firm, "document_sent")) | {
        "document_name": "Bonds & Gilts"
    }
    subject_template = _prefill("document_sent").subject
    expected = "Ridley & Co: Bonds & Gilts is ready for you"

    assert (
        email_render.render_default("document_sent", ctx, asset_origin=None)[0] == expected
    )  # path 2
    published = await email_render.render_published_email(  # path 3
        _published(subject_template, "", "<p>Body</p>", ""),  # type: ignore[arg-type]
        tenant_id=1,
        code="document_sent",
        context=ctx,
    )
    assert published is not None and published.subject == expected
    assert (  # path 1, on its own sampled document name
        email_preview.render_preview(
            subject=subject_template,
            preheader="",
            html="<p>Body</p>",
            css="",
            brand=firm,
            code="document_sent",
        ).subject
        == "Ridley & Co: Suitability Report is ready for you"
    )


# Path 2 — the un-overridden send, which is the path most firms are actually on


@pytest.mark.asyncio
@pytest.mark.parametrize("code", CODES)
async def test_the_shipped_default_send_is_a_complete_branded_document(code: str) -> None:
    """The un-overridden send ends in the same envelope as the other two. It used to hand the
    builder's body FRAGMENT straight to `send_email`, so the commonest path delivered no doctype, no
    charset, no base reset — and no preheader span, the same authored-then-not-delivered bug already
    fixed once for the override path. The preheader is also asserted token-free and present on the
    SPARSEST send: it renders before anything else and is the line an author least often
    re-reads."""
    subject, html = await _default_send(code)
    assert html.startswith("<!DOCTYPE html>"), f"{code}: the shipped default is not a document"
    assert "charset" in html, f"{code}: no charset declaration"
    assert 'style="' in html, f"{code}: no inline styling"
    assert "<style" not in html.replace(render_email.MSO_CTA_INK, ""), (
        f"{code}: a <style> block survived beyond the shell's own mso conditional"
    )
    assert FIRM.name in html, f"{code}: no firm brand name"
    assert "Graphene" not in html and "Graphene" not in subject, f"{code}: the white-label leak"

    preheader = email_defaults.preheader_for(code)
    assert preheader, f"{code}: no shipped preheader copy"
    assert preheader.strip() != subject.strip(), (
        f"{code}: the preheader merely repeats the subject, so the inbox shows the same words twice"
    )
    assert preheader in html, f"{code}: the shipped preheader never reached the delivered HTML"
    assert "display:none" in html[: html.index(preheader)], f"{code}: the preheader is visible"
    assert html.index(preheader) < html.index("<h1"), f"{code}: the preheader is not first"
    assert "{{" not in preheader and "{%" not in preheader, (
        f"{code}: the shipped preheader carries a merge token, so it can render blank or partial "
        f"on a sparse send: {preheader!r}"
    )
    assert preheader in (await _default_send(code, sparse=True))[1]


@pytest.mark.parametrize("code", CODES)
def test_wrapping_the_shipped_default_changes_nothing_visual(code: str) -> None:
    """Putting the shipped default in the document envelope must be ADDITIVE — proved, not
    assumed.

    Byte equality would be a false assertion: css-inline re-serialises the declaration list of any
    element a stylesheet rule matched (`color:#fff` → `color: #fff`) and normalises single-quoted
    `style='…'` to double. Neither is visual. So the claim is checked element by element: same
    elements in the same order, same attributes, same visible text, and **no declaration changed or
    removed** — every difference an ADDITION from `EMAIL_BASE_CSS`. Run with a logo brand too,
    because the `<img>` is the only element the base reset has anything to add to."""
    base_properties = {
        # NOT from EMAIL_BASE_CSS — added by `_outlook_line_height` over the finished document,
        # deliberately and only ever beside a `line-height` that was already there. It IS a visual
        # change, in Outlook alone: without it Outlook ignores the authored line-height and applies
        # its own leading. Listed here rather than exempted, so a SECOND unexplained addition still
        # fails this test.
        "mso-line-height-rule",
        "margin",
        "padding",
        "background-color",
        "border-collapse",
        "border",
        "line-height",
        "outline",
        "text-decoration",
        "-ms-interpolation-mode",
    }
    for brand in (FIRM, FIRM._replace(logo_url="https://cdn.example.test/logo.png")):
        fragment = _shipped_fragment(code, dict(email_preview.sample_context(brand, code)))
        wrapped = email_render.sendable_default(code, fragment, asset_origin=None)
        # render_email.BODY_RESET is inline, so match the <body>; then drop the preheader span.
        opening = re.search(r"<body[^>]*>", wrapped)
        assert opening is not None
        inner = wrapped[opening.end() : wrapped.rindex("</body>")]
        inner = inner[inner.index("</div>") + 6 :] if inner.startswith("<div style=") else inner

        before, after = _elements(fragment), _elements(inner)
        assert [e.tag for e in before] == [e.tag for e in after], (
            f"{code}: wrapping changed the element sequence\n"
            f"  before: {[e.tag for e in before]}\n  after : {[e.tag for e in after]}"
        )
        assert _text(fragment) == _text(inner), f"{code}: wrapping changed the visible copy"
        for old, new in zip(before, after, strict=True):
            assert old.attrs == new.attrs, f"{code}: <{old.tag}> attributes changed"
            changed = {
                k: (old.decls[k], new.decls[k])
                for k in old.decls
                if new.decls.get(k) != old.decls[k]
            }
            assert not changed, (
                f"{code}: the inliner REWROTE styling on <{old.tag}>, which is not additive: "
                f"{changed}"
            )
            added = {k for k in new.decls if k not in old.decls}
            assert added <= base_properties, (
                f"{code}: <{old.tag}> gained declarations that are not in EMAIL_BASE_CSS: "
                f"{sorted(added - base_properties)}"
            )


def test_the_request_raised_subject_pluralises_off_the_item_count() -> None:
    """Pinned at BOTH counts, which the golden module cannot do (one frozen context per
    code). It started as a divergence: the prefill flattened the builder's pluralisation to the
    one-item wording, so a firm publishing it unchanged mailed "action needed" for a request with
    five things on it.

    THE PLURALISATION MOVED, and the behaviour is what is pinned, not where it lives. The subject
    used to carry `{% if item_titles | length != 1 %}` — a raw Jinja tag in the plain text box an
    author types a subject into, which is the last place any of it survived. It is now
    `{{ request_summary }}`, decided by ``email_defaults.derived_tokens`` on the send side, so the
    context is enriched here exactly as every renderer enriches it (``email_render._with_derived``).
    The wording and both counts are unchanged; three items is added because 0/1/many is the real
    shape of the decision and "1 things" was always the bug to guard.
    """
    subject = _prefill("request_raised").subject
    assert "{%" not in subject, "the subject must stay free of Jinja tags — an author reads it"
    for titles, expected_tail in (
        ([], "action needed"),
        (["Proof of identity"], "action needed"),
        (["Proof of identity", "Proof of address"], "2 things need your attention"),
        (["One", "Two", "Three"], "3 things need your attention"),
    ):
        context = _ctx("request_raised") | {"item_titles": titles}
        context |= email_defaults.derived_tokens("request_raised", context)
        rendered = jinja_engine.render_text(subject, context)
        assert rendered == f"{FIRM.name}: {expected_tail}", f"{len(titles)} item(s): {rendered!r}"


def test_each_task_subject_names_its_own_kind_in_plain_words() -> None:
    """The other half of the same change, taken one step further than the pluralisation could go.

    The rule is the same — a subject an author reads must not contain a tag they cannot edit — and
    the first answer was the pluralisation's: `{% if is_reminder %}Reminder{% else %}New task
    {% endif %}` became `{{ notice_kind }}`, decided by `derived_tokens` on the send side. That was
    still one template being told which of two emails it was. The distinction it carried is worth
    keeping (a reminder is a RE-SEND of an email the person already had, and an inbox saying "New
    task" twice about one task reads as two tasks) — so it is now carried by there being TWO
    templates, each naming its kind outright.

    So this pins the stronger property: no tag, no merge field either, and NOTHING DERIVED. The
    kind is in the characters of the subject, which is the only form an author can edit, translate
    or delete."""
    for code, expected_kind in (("task_reminder", "Reminder"), ("task_assigned", "New task")):
        subject = _prefill(code).subject
        assert "{%" not in subject, f"{code}: a Jinja tag is back in the subject"
        assert email_defaults.derived_tokens(code, _ctx(code)) == {}, (
            f"{code}: the subject decision has crept back onto the send side — the point of two "
            "templates is that neither has one"
        )
        rendered = jinja_engine.render_text(subject, _ctx(code))
        assert rendered.startswith(f"{expected_kind}: "), rendered
        # The title still follows, so "no tokens" was not won by dropping the merge field needed.
        assert rendered.endswith(str(email_preview._SAMPLE["task_title"])), rendered


@pytest.mark.parametrize("code", CODES)
def test_a_send_with_no_portal_link_ships_no_dead_button(code: str) -> None:
    """The sparse-case defect: a CTA whose URL is absent must take its whole block with it —
    button and "paste this link into your browser" line alike — rather than rendering a button that
    goes nowhere above a sentence with nothing after it. It reached published templates on two
    codes."""
    body = _shipped_fragment(code, _ctx(code, sparse=True))
    assert 'href=""' not in body, f"{code}: a dead CTA button reached the email"
    if "paste this link into your browser" in body:
        assert "href=" in body, f"{code}: the fallback link line is present with no link after it"


# The CTA's shape: `cta_block` is the one seam behind every CTA (app/email/transport.py →
# BULLETPROOF BUTTON).

#: `display`/`padding` are the observed defect; `margin` joins them because Word drops it too.
_ANCHOR_BANNED = ("display", "padding", "margin")


def _banned_on(element: _Element) -> list[str]:
    """The banned box properties an element declares, longhands included (`padding-top`…)."""
    return sorted(
        prop
        for prop in element.decls
        if any(prop == banned or prop.startswith(f"{banned}-") for banned in _ANCHOR_BANNED)
    )


#: The shell is a card now, so only the `cta` class still names the button's cell and its link.
_CTA_CELL_AND_LINK = re.compile(rf'<td style="([^"]*)"><a class="{email_shell._CTA_CLASS}"')


@pytest.mark.parametrize("code", CODES)
def test_the_shipped_cta_is_a_table_button_not_a_padded_anchor(code: str) -> None:
    """The pill is the CELL and the anchor is only its label — for every code we ship.

    Read off the MERGED fragment rather than the source, because `{{ brand.accent }}` has to have
    become the firm's real colour on the property Outlook honours, and that is only decidable after
    the merge."""
    fragment = _shipped_fragment(code, _ctx(code))
    elements = _elements(fragment)

    anchors = [e for e in elements if e.tag.lower() == "a" and e.attrs.get("class") == "cta"]
    assert len(anchors) == 1, (
        f"{code}: expected exactly one `cta`-classed anchor (the button) in the shipped default, "
        f"found {len(anchors)} — this test can no longer tell which one is the button"
    )
    # Every other link is the footer's support address or the CTA's own paste-this-link fallback,
    # so a stray http link fails here and not later.
    #
    # THE FALLBACK IS AN ANCHOR ON PURPOSE and must point at the BUTTON'S OWN URL: it was a bare
    # `<span>` of URL text, which Gmail and Apple Mail auto-link and then paint in their own link
    # colour — the one spot of unexplained colour in the message, on the line a recipient only
    # reaches when the button has already failed them. Given an anchor of our own they leave it
    # alone. Allowed by MATCHING THE CTA rather than by exempting https, so a link to anywhere
    # else still fails.
    cta_href = anchors[0].attrs.get("href", "")
    others = [e for e in elements if e.tag.lower() == "a" and e.attrs.get("class") != "cta"]
    allowed = {"mailto:", "{{"}
    assert all(
        e.attrs.get("href", "").startswith(tuple(allowed)) or e.attrs.get("href") == cta_href
        for e in others
    ), (
        f"{code}: a shipped default carries a link that is neither the CTA, its paste-this-link "
        f"fallback, nor a support address: {[e.attrs.get('href') for e in others]}"
    )

    match = _CTA_CELL_AND_LINK.search(fragment)
    assert match, (
        f"{code}: the CTA anchor is not the first thing inside a styled <td>. The button must be a "
        "one-cell presentation table: Outlook's Word engine honours padding on a <td> and ignores "
        "it on an inline-block <a>."
    )
    cell = _Element("td", {}, _decls(match.group(1)))
    assert cell.decls.get("padding"), (
        f"{code}: the CTA cell declares no padding, so the button has no pill — {cell.decls}"
    )
    assert cell.decls.get("background-color") == FIRM.accent, (
        f"{code}: the CTA cell is not filled with the firm's accent on `background-color` — "
        f"{cell.decls}. The accent must be on the CELL (Word honours it there) and it must be the "
        "longhand: the editor's Style rail edits `background-color`, so a `background` shorthand "
        "here means a firm that recolours the button ships two competing declarations."
    )
    assert cell.decls.get("border-radius"), f"{code}: the CTA cell lost its corner radius"

    anchor = anchors[0]
    assert not _banned_on(anchor), (
        f"{code}: the CTA anchor declares {_banned_on(anchor)}. Outlook's Word engine implements "
        "neither `display:inline-block` nor margins on an <a>, so a box property here is a "
        "declaration that silently does nothing in the one client that needs it most — and a "
        "padded anchor is the collapsed, near-black pill this shape was introduced to fix. "
        f"Typography and ink only: {anchor.decls}"
    )
    assert anchor.decls.get("color") == "#ffffff", (
        f"{code}: the CTA anchor's ink is {anchor.decls.get('color')!r}. Six-digit, not `#fff`: "
        "Word is unreliable on three-digit hex. And UNFLAGGED — an inline `!important` is the "
        "declaration Word invalidates, which left the label inheriting the shell's `#0f172a`, the "
        "fill's own colour. The flag belongs in the shell's MSO block, not here."
    )
    assert anchor.decls.get("text-decoration") == "none", f"{code}: the CTA label is underlined"
    assert anchor.attrs.get("class") == "cta", (
        f"{code}: the CTA anchor has lost the `cta` class. It is the hook "
        "`app/email/document.py::MSO_CTA_INK` targets — the one place Word honours "
        "`!important` — so without it the head conditional matches nothing and layer 3 of the "
        "ink fix is dead markup."
    )

    # Matched through the anchor, so the fallback line's own span cannot stand in for a label.
    label = re.search(r"<a\b[^>]*>\s*<span\b[^>]*\sstyle=\"([^\"]*)\"", fragment)
    assert label and label.group(1) == "color:#ffffff", (
        f"{code}: the CTA label is not wrapped in a span carrying its own white ink "
        f"({label.group(1) if label else None!r}). That span is the layer Word's Hyperlink "
        "character style does not reach, and it is the one doing the work — the anchor's own "
        "colour loses to that style and cannot be flagged out of it."
    )

    assert "background" not in fragment.replace("background-color", ""), (
        f"{code}: a `background` shorthand survives in the shipped default. The editor bans it "
        "outright — its Style rail edits `background-color` only, so the shorthand is a "
        "declaration no author can clear."
    )


def _every_shipped_email() -> list[tuple[str, str]]:
    """`(where, html)` for every email this chrome produces: every catalogue code in both
    contexts, plus
    `email_shell.shell` — the flow tier's already-merged body, which has no catalogue code behind
    it and
    so no shipped default, and is therefore invisible to every per-code test above.

    The sweeps below run over this rather than per code, because the defects they check for live in
    ONE shared builder: a per-code assertion would report six failures for one cause, and would
    still miss the seventh caller."""
    sources = [
        (
            f"{code} ({'sparse' if sparse else 'full'})",
            _shipped_fragment(code, _ctx(code, sparse=sparse)),
        )
        for code in CODES
        for sparse in (False, True)
    ]
    sources.append(
        (
            "email_shell.shell",
            email_shell.shell("Heading", "<p>Body</p>", "Open your portal", "https://p.test", FIRM),
        )
    )
    return sources


def test_no_anchor_in_any_shipped_email_declares_a_box_property() -> None:
    """The sweep, over every path `app/email/transport.py` builds an anchor on."""
    sources = _every_shipped_email()
    seen = 0
    for where, html in sources:
        for anchor in (e for e in _elements(html) if e.tag.lower() == "a"):
            seen += 1
            assert not _banned_on(anchor), (
                f"{where}: an anchor declares {_banned_on(anchor)} — Outlook's Word engine applies "
                f"none of it. Move it to the enclosing cell or table: {anchor.decls}"
            )
    assert seen >= len(CODES), (
        f"only {seen} anchors found across every shipped email — the sweep has stopped seeing the "
        "CTAs it exists to check"
    )


# The card: the `align`/`width` attributes are pinned, not the CSS — only they reach Word.

#: What a writer reaching for the product's layout vocabulary would try; clients block the fetches.
_UNMAILABLE = ("display:flex", "display:grid", "display:inline-block", "@font-face", "url(")


def test_every_shipped_email_is_a_centred_card_and_closes_with_a_footer() -> None:
    """One sweep, three claims about the chrome: it is a real centred card in Word, it ends with the
    footer rather than with the paste-this-link small print, and nothing in it needs a rendering
    engine an email client does not have."""
    for where, html in _every_shipped_email():
        elements = _elements(html)

        band = [e for e in elements if e.tag.lower() == "td" and e.attrs.get("align") == "center"]
        assert len(band) == 1, (
            f'{where}: the page band has no single `align="center"` cell ({len(band)} found). '
            "That ATTRIBUTE is what centres the card in Word — `margin:0 auto` is ignored there, "
            "which is how the old shell arrived full-bleed in Outlook and correct everywhere else."
        )
        card = [e for e in elements if e.tag.lower() == "table" and e.attrs.get("width") == "600"]
        assert len(card) == 1 and card[0].decls.get("max-width") == "600px", (
            f"{where}: no 600px card table carrying BOTH the `width` attribute and `max-width` — "
            "the attribute is Word's, the CSS is everyone else's, and a card needs the pair."
        )

        assert "border-top" in html, f"{where}: the email does not close with the footer's rule"
        fallback = "paste this link into your browser"
        if fallback in html:  # absent on the sparse sends, which have no CTA to fall back from
            assert html.rindex("border-top") > html.rindex(fallback), (
                f"{where}: the footer comes BEFORE the fallback-link small print, so the email "
                "ends on a URL rather than on who sent it"
            )

        lowered = html.replace(" ", "").lower()
        found = [decl for decl in _UNMAILABLE if decl in lowered]
        assert not found, f"{where}: unmailable CSS in the shipped chrome: {found}"


def test_the_shell_is_set_in_a_font_every_client_can_resolve() -> None:
    """Word takes the FIRST family in a stack and falls back to Times New Roman when it cannot
    resolve it — it does not walk on to the next entry. So `system-ui,-apple-system,Segoe UI,…`,
    which is what the shell used to declare, set every one of these emails in a serif for the
    desktop client most advisers read their mail in, while every other client looked right.

    The first family must therefore be one Word has: pinned as a small allowlist rather than as
    "not system-ui", because the next well-meaning stack will start with something else."""
    first = email_shell.FONT_STACK.split(",")[0].strip().strip("'\"").lower()
    assert first in {"arial", "helvetica", "verdana", "tahoma", "georgia", "times new roman"}, (
        f"the shell's first font family is {first!r}. Outlook's Word engine resolves the first "
        "entry or falls back to Times New Roman; it never reads the rest of the stack."
    )
    for where, html in _every_shipped_email():
        assert email_shell.FONT_STACK in html, f"{where}: the shell declares no font family at all"


#: The editor's copy of this button, read as text: the API container ships without pkgs/ui.
_CREATE_TS = (
    Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/components/email-editor/create.ts"
)


def _editor_constant(name: str) -> str:
    """A single-line backtick constant out of create.ts — read by pattern rather than parsed,
    because the API container ships without pkgs/ui. The guard test below is what stops a rename
    passing this vacuously."""
    source = _CREATE_TS.read_text(encoding="utf-8")
    match = re.search(rf"^const {name} = `([^`]*)`;$", source, re.M)
    assert match, (
        f"could not find `const {name} = ...` in {_CREATE_TS.name}. The editor's copy of the CTA "
        "button has moved or been renamed; the cross-language pin below is now blind and must be "
        "repointed rather than deleted."
    )
    return match.group(1)


def _decls(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for declaration in text.split(";"):
        prop, sep, value = declaration.partition(":")
        if sep:
            parsed[prop.strip().lower()] = " ".join(value.split())
    return parsed


@pytest.mark.pkgs_ui_pin
def test_the_three_ink_layers_are_in_BOTH_copies_of_the_button() -> None:
    """THE CROSS-LANGUAGE PIN, and the assertion that actually matters about this fix.

    This markup exists twice, in two languages — `app/email/transport.py` behind every shipped
    default's CTA,
    and `create.ts` behind the editor palette's Button and the starter skeleton — and that
    duplication has already produced nine divergences in this feature. A fix applied to one side and
    not the other is therefore the expected failure mode, not a hypothetical one, and it is
    invisible: the shipped emails and the emails a firm authors would simply render differently in
    Outlook.

    Three claims, all three on both sides at once:

      1. the LABEL SPAN carries white ink and nothing else (layer 1, the one Word's Hyperlink
         character style does not reach);
      2. the ANCHOR carries white ink too (layer 2, the fallback for text outside the span);
      3. NEITHER carries `!important`. That is the inversion this pin used to assert the opposite
         of, and the reason is a real render: Word does not ignore an inline flag, it invalidates
         the declaration carrying it, so the flagged anchor shipped with NO colour and the label
         inherited the shell's `#0f172a` — the fill's own colour, hence black on navy. The flag
         moved to `app/email/document.py::MSO_CTA_INK`, where a stylesheet makes Word honour it.
    """
    sides = (
        ("app/email/shell.py", email_shell._CTA_LINK, email_shell._CTA_LABEL),
        ("create.ts", _editor_constant("BUTTON_LINK"), _editor_constant("BUTTON_LABEL")),
    )
    for where, anchor_style, label_style in sides:
        anchor, label = _decls(anchor_style), _decls(label_style)
        assert label == {"color": "#ffffff"}, (
            f"{where}: the CTA label span declares {label} — it must carry the white ink and only "
            "the ink. That span is the layer Word's Hyperlink character style does not reach; "
            "typography on it would be a second copy of the anchor's, free to drift."
        )
        assert anchor.get("color") == "#ffffff", (
            f"{where}: the CTA anchor's ink is {anchor.get('color')!r}, not plain `#ffffff`. It is "
            "the fallback for label text the span does not cover (an RTE retype can leave some)."
        )
        assert "!important" not in f"{anchor_style}{label_style}", (
            f"{where}: an inline `!important` is back on the CTA. Outlook 2007-2019 invalidates "
            "the declaration carrying it (hteumeuleu/email-bugs#31), so this deletes the ink "
            "rather than defending it — the black-on-navy label that took three renders to find. "
            "The flag belongs in `app/email/document.py::MSO_CTA_INK`, and only there."
        )


@pytest.mark.pkgs_ui_pin
def test_both_copies_of_the_button_carry_the_mso_class_hook() -> None:
    """The other half of layer 3: the conditional in the shell targets `a.cta`, so a copy that
    dropped the class would leave the head rule matching nothing — dead markup in every email, and
    silent, because the two files that would have to agree do not import each other."""
    selector = f"a.{email_shell._CTA_CLASS}{{"
    assert selector in render_email.MSO_CTA_INK, (
        f"the shell conditional does not target {selector!r} — "
        "`app/email/shell.py::_CTA_CLASS` and `app/email/document.py::MSO_CTA_INK` "
        "have drifted apart"
    )
    assert f'class="{email_shell._CTA_CLASS}"' in email_shell.cta_block(
        "Go", "https://p.test", "#000"
    )
    editor_class = re.search(
        r"^const BUTTON_CLASS = '([^']*)';$", _CREATE_TS.read_text("utf-8"), re.M
    )
    assert editor_class and editor_class.group(1) == email_shell._CTA_CLASS, (
        f"the editor's CTA class is {editor_class.group(1) if editor_class else None!r} and the "
        f"API's is {email_shell._CTA_CLASS!r}. One hook, one conditional: a firm's own button "
        "would "
        "silently miss the rule the shipped ones get."
    )


def test_the_publish_gate_still_reads_a_table_button_label_as_one_element_of_copy() -> None:
    """The extra table levels must not change how `app/email/content.py` reads the button.

    Both scanners walk visible text per ELEMENT: `_ContentScanner` to answer "is there anything
    here at all", `_SegmentScanner` to answer "is any of this still the editor's scaffold copy",
    where the match is on an element's WHOLE text. A label fused into a neighbouring run would fail
    that second question silently — placeholder copy would stop being caught rather than start
    being reported."""
    real = email_shell.cta_block("Set password", "https://p.test", "#0f766e")
    assert email_content.has_renderable_content(real)
    assert email_content.placeholder_copy(real) is None
    # The converse: scaffold copy is still a whole element's text, so the gate still refuses it.
    assert (
        email_content.placeholder_copy(
            email_shell.cta_block("Heading", "https://p.test", "#0f766e")
        )
        == "Heading"
    )
    for code in CODES:
        fragment = _shipped_fragment(code, _ctx(code))
        assert email_content.has_renderable_content(fragment), f"{code}: reads as a blank email"
        assert email_content.placeholder_copy(fragment) is None, (
            f"{code}: the shipped default now trips the placeholder gate, so a firm could not "
            "publish it unchanged"
        )
