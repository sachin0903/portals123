"""A JOURNEY'S EMAIL WEARS THE FIRM'S BRAND, and for a while it wore nothing at all.

`app/flows/effects.py::send_email` renders a FIRM-AUTHORED template — the same document the
email editor writes and every other send path merges — and it built its merge context from
`build_context` alone, which carries `automation.*`, `trigger.*`, `memory`, `case.*` and
`contact.*` and **no `brand`**. The masthead of every shipped default is

    {% if brand.logo_url %}<img src="{{ brand.logo_url }}" …>
    {% else %}<div …>{{ brand.name }}</div>{% endif %}

so with `brand` out of scope Jinja resolved BOTH arms to Undefined: the condition was false, so the
image never rendered, and the else arm rendered an **empty div**. The email therefore arrived with
no logo and no name — not a degraded masthead but an absent one, which reads as "the branding
feature is broken" rather than as one unset value. The brand was resolved a few lines further down
and used for the outer `email_shell.shell` chrome, so the frame around the body was on brand while
the body was not.

THE GUARD THAT SHOULD HAVE CAUGHT IT COULD NOT SEE THE SITE. `test_email_send_parity.py` builds its
population from `render_or_default` calls (`_render_or_default_sites`) and closes with
`assert checked == _render_or_default_sites()`, so it is exact over the population it knows about —
and this path calls `jinja_engine.render_with_diagnostics` directly, appearing in neither
`RELAY_SEND_SITES` nor `DYNAMIC_TEMPLATE_SITES`. Its own message promises that "every firm-authored
template renders the firm's name, logo and accent from it", which is precisely what the flow tier
renders. So this file covers the OTHER render seam, by both routes: statically, so a new path that
forgets is refused, and behaviourally, so the symptom itself is pinned.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.email import render as email_render
from app.email import transport as email_mod

API_ROOT = Path(__file__).resolve().parents[1]
APP = API_ROOT / "app"

#: The template-render seam this file polices. `render_or_default` has its own exact-population
#: guard next door; these are the calls that hand a template body straight to the engine.
RENDER_SEAMS = frozenset({"render_with_diagnostics"})

#: A function may reach the seam without owning the branding: `preview.py` is handed a resolved
#: `Brand` by its caller, and `templates_service` renders for a validation pass that sends nothing.
#: An entry here is a claim that no recipient sees the result of that render, or that the brand
#: arrives as a parameter.
SEAM_EXEMPT = frozenset(
    {
        "app/email/preview.py",
        "app/email/templates_service.py",
        "app/email/render.py",
        "app/flows/simulate.py",
        # NOT AN EMAIL. This renders a DOCUMENT template for the PDF pipeline, whose context is
        # `answers`/`datasets`/`one`/`document_name`/`date` — `brand` is not in that contract and a
        # document carries the firm's identity through its own chrome, not through a masthead. It is
        # named here rather than pattern-excluded because "the worker does not send email" is a fact
        # about today's worker: the day it grows an email path, this line is what it has to cross.
        "app/worker.py",
    }
)


def _brand() -> email_mod.Brand:
    return email_mod.Brand(
        name="Ridgeway Wealth",
        logo_url=None,
        accent="#0f172a",
        support_email="hello@ridgeway.test",
        icon_url=None,
    )


def _masthead(html: str) -> str:
    """The masthead element, whichever arm rendered."""
    found = re.search(r"<(?:img|div)[^>]*data-brand-arm[^>]*>(?:[^<]*)", html)
    return found.group(0) if found else ""


class TestTheSymptom:
    """What a recipient gets, rendered through the shipped default the journey tier resolves."""

    def test_a_brandless_context_renders_an_empty_masthead(self) -> None:
        # THE DEFECT, kept as a test rather than described in prose: this is what a journey email
        # looked like. Both arms resolve to Undefined, so the picture is absent and the name is an
        # empty div — no logo, and no wordmark either.
        _subject, html = email_render.render_default("invite", {}, asset_origin=None)
        masthead = _masthead(html)
        assert masthead, "the default has no masthead element at all — the fixture is wrong"
        assert "Ridgeway" not in html
        assert masthead.endswith(">"), masthead  # nothing between the tags: an empty element
        assert not re.search(r"data-brand-arm[^>]*>\s*\S", masthead), (
            f"expected an empty masthead for a brandless context, got {masthead!r}"
        )

    def test_the_brand_in_context_renders_the_firms_name(self) -> None:
        _subject, html = email_render.render_default(
            "invite", {"brand": email_mod.brand_tokens(_brand())}, asset_origin=None
        )
        assert "Ridgeway Wealth" in _masthead(html)

    def test_a_logo_renders_as_the_image_arm(self) -> None:
        brand = _brand()._replace(logo_url="https://cdn.test/_assets/logo-0123456789abcdef.png")
        _subject, html = email_render.render_default(
            "invite", {"brand": email_mod.brand_tokens(brand)}, asset_origin=None
        )
        masthead = _masthead(html)
        assert masthead.startswith("<img"), masthead
        assert "logo-0123456789abcdef.png" in masthead


class TestTheSendSiteBrandsItself:
    """Statically, so the next path through this seam cannot repeat it."""

    def _send_email_fn(self) -> ast.FunctionDef | ast.AsyncFunctionDef:
        tree = ast.parse((APP / "flows" / "effects.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            is_fn = isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            if is_fn and node.name == "send_email":
                return node
        raise AssertionError("effects.send_email not found")

    def test_the_journey_send_puts_brand_tokens_in_the_context_it_renders(self) -> None:
        fn = self._send_email_fn()
        writes = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Subscript)
                and isinstance(t.slice, ast.Constant)
                and t.slice.value == "brand"
                for t in n.targets
            )
        ]
        assert writes, (
            "app/flows/effects.py::send_email does not put `brand` in its merge context — "
            "the masthead of every firm-authored template reads `{{ brand.* }}`, so this send "
            "goes out with no logo and no name."
        )
        for write in writes:
            call = write.value
            name = (
                call.func.attr
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                else ""
            )
            assert name == "brand_tokens", (
                "`brand` must be `email_mod.brand_tokens(<resolved brand>)`, the one shape every "
                "template's tokens are written against — a hand-rolled dict is how a key goes "
                "missing, and a missing key renders blank."
            )

    def test_the_brand_is_resolved_for_the_tenant_not_graphene(self) -> None:
        fn = self._send_email_fn()
        source = ast.unparse(fn)
        assert "brand_for(session, ctx.tenant_id" in source, source[:400]
        assert "GRAPHENE_BRAND" not in source, (
            "a journey email dressed in Graphene's own brand is the white-label leak"
        )

    def test_every_module_at_this_seam_resolves_a_brand(self) -> None:
        """The population, so a NEW path through the seam is refused rather than discovered.

        Deliberately module-level rather than per-call: the context is built imperatively at this
        seam (a dict literal is what the sibling guard can assert on), so what is checkable is that
        the module which renders also resolves. `SEAM_EXEMPT` carries the ones that legitimately do
        not, each with its reason.
        """
        unbranded: list[str] = []
        for path in sorted(APP.rglob("*.py")):
            rel = path.relative_to(API_ROOT).as_posix().removeprefix("app/")
            rel = f"app/{rel}"
            if rel in SEAM_EXEMPT:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            hits = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in RENDER_SEAMS
            ]
            if hits and "brand_tokens" not in source:
                unbranded.append(rel)
        assert unbranded == [], (
            f"{unbranded} render a template through {sorted(RENDER_SEAMS)} without putting "
            "`brand_tokens(...)` in the context. Either brand the send or add the module to "
            "SEAM_EXEMPT with the reason no recipient sees the result."
        )
