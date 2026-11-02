"""Per-tenant white-label branding — the canonical structured model, validation, and storage.

Branding lives in ``auth.tenants.theme`` (jsonb) as one versioned object (see ``Branding``) plus the
firm ``name``. It's served to the portals (non-secret) via ``environments.env_config_by_host`` and
applied by overriding a handful of CSS custom properties at request time (the whole design system
keys off them). Everything here is **structured tokens only** — no raw-CSS escape hatch — and every
value that ends up inside an SSR ``<style>`` is validated against a strict allowlist, so a tenant
can never inject CSS/HTML.

The client portal itself is composable: ``Branding.pages`` is a list of firm-definable ``PageDef``s,
each carrying per-audience block lists (``PageLayouts``) of typed, per-instance ``Block``s whose
``settings`` are validated by a per-type model (``BLOCK_SETTINGS``). The block/page vocabulary is
pinned in ``pkgs/ui/src/lib/components/branding/block-contract.json`` — tests on BOTH sides assert
against that file, so the Python models and the portal registry cannot drift silently. The flat
``layout`` key is the pre-pages home layout, kept as a read-side migration source and a write-side
mirror for one release (see ``Branding._page_caps``).

Two validation regimes coexist deliberately:

* **Strict allowlists** for anything injection-shaped (colours, fonts, URLs, radius): these end up
  inside SSR ``<style>``/``<head>`` markup, so a bad value is an attack and must be rejected.
* **Clamp, never reject** for anything the no-code builder produces (spans, variants, looks,
  limits, page labels/icons/slugs): the builder is the only writer of these and its failure mode
  must be silent correction — a stale variant from an old build, a dragged-out-of-range span or a
  garbage slug must degrade (clamp or drop) rather than 422 the whole save and strand the firm's
  edits.
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage

logger = logging.getLogger(__name__)

# ---- value validators (defence against CSS/HTML injection at the SSR <style>/head) ----

# A CSS colour: hex, or oklch()/rgb()/rgba()/hsl()/hsla()/color-mix() with only safe inner chars,
# or a bare CSS keyword. No ';', '{', '}', '<', '>', '"', or url() — so it can't break out of the
# `--var: <value>;` context.
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(?:oklch|oklab|rgb|rgba|hsl|hsla|color-mix)\([0-9a-zA-Z%.,/()\s+-]{1,120}\)"
    r"|[a-zA-Z]{1,24})$"
)
_RADIUS = re.compile(r"^\d{1,3}(?:\.\d+)?(?:px|rem|em)$")
_FONT = re.compile(r"^[A-Za-z0-9 \-]{1,48}$")  # family name only; wrapped in quotes on inject
_URL = re.compile(r"^https://[^\s\"'<>]{1,2048}$")  # asset URLs are object-storage https URLs
_PROXY_ASSET = re.compile(r"^/_assets/[A-Za-z0-9-]+-[0-9a-f]{16}\.(?:svg|png|jpg|webp|ico)$")
# Or an inline base64 data URI (quick-and-dirty storage: the asset lives in the theme jsonb / DB).
_DATA_URI = re.compile(
    r"^data:image/(?:png|jpeg|svg\+xml|webp|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=]{1,1400000}$"
)
_MODES = {"auto", "light", "dark"}
# Top-left logo treatment: `icon` = the mark on its own; `wordmark` = the full lockup (the firm's
# wordmark image, or the mark + name) with NO separately-appended firm-name text.
_LOGO_VARIANTS = {"icon", "wordmark"}

# Branding colour key -> the CSS custom property it drives. The BASIC PALETTE: fourteen tokens a
# firm may set per scheme. Every other colour in the design system is derived from these in
# theme.css (`--popover` is `--card`, `--ring` is `--brand`, `--success` is `--positive`, the
# tonal ramps are colour-mixed off the four status bases), so these are the only keys injected.
# Mirror of COLOUR_VARS in pkgs/auth branding.ts.
COLOUR_VARS: dict[str, tuple[str, ...]] = {
    # neutrals
    "background": ("--background",),
    "foreground": ("--foreground",),
    "card": ("--card",),
    "muted": ("--muted",),
    "muted_foreground": ("--muted-foreground",),
    "border": ("--border",),
    # identity
    "primary": ("--primary",),
    "primary_foreground": ("--primary-foreground",),
    "brand": ("--brand",),
    "brand_foreground": ("--brand-foreground",),
    # status — each is also the BASE of a derived `-subtle`/`-strong`/`-line` trio in theme.css
    "positive": ("--positive",),
    "negative": ("--negative",),
    "warning": ("--warning",),
    "info": ("--info",),
}

# The per-token keys the palette carried before it was collapsed to the basic fourteen. A stored
# theme may still hold them; they are accepted and dropped (pydantic ignores extras), and they
# still mark a legacy FLAT colours object for ``_migrate_flat_colors``.
_RETIRED_COLOUR_KEYS = frozenset(
    {
        "popover",
        "card_foreground",
        "popover_foreground",
        "secondary",
        "secondary_foreground",
        "accent",
        "accent_foreground",
        "success",
        "success_foreground",
        "warning_foreground",
        "info_foreground",
        "destructive",
        "destructive_foreground",
        "input",
        "ring",
        "sidebar",
        "sidebar_foreground",
        "sidebar_primary",
        "sidebar_primary_foreground",
        "sidebar_accent",
        "sidebar_accent_foreground",
        "sidebar_border",
        "sidebar_ring",
    }
)


class BrandingColors(BaseModel):
    """One scheme's basic palette (see COLOUR_VARS). Every field is optional; unset ⇒ the design
    system's own value. Retired per-token keys from an older save are ignored, not rejected."""

    # neutrals
    background: str | None = None
    foreground: str | None = None
    card: str | None = None
    muted: str | None = None
    muted_foreground: str | None = None
    border: str | None = None
    # identity
    primary: str | None = None
    primary_foreground: str | None = None
    brand: str | None = None
    brand_foreground: str | None = None
    # status
    positive: str | None = None
    negative: str | None = None
    warning: str | None = None
    info: str | None = None

    @field_validator("*")
    @classmethod
    def _colour(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _COLOUR.match(v):
            raise ValueError("invalid colour value")
        return v


# Colour keys a flat (legacy) colours object carries — used to detect + migrate the old shape.
_COLOUR_KEYS = frozenset(BrandingColors.model_fields) | _RETIRED_COLOUR_KEYS


class BrandingPalettes(BaseModel):
    """Separate colour palettes per colour scheme. ``dark`` unset ⇒ the design system's own dark
    theme applies in dark mode (only the tokens a firm sets are overridden)."""

    light: BrandingColors = BrandingColors()
    dark: BrandingColors = BrandingColors()


class BrandingLogin(BaseModel):
    tagline: str | None = None
    legal: str | None = None


class BrandingSupport(BaseModel):
    email: str | None = None
    phone: str | None = None
    website: str | None = None


def _clamp(v: int | None, lo: int, hi: int) -> int | None:
    """Clamp a slider value into range (None ⇒ unset ⇒ design-system default)."""
    if v is None:
        return None
    return max(lo, min(hi, v))


class BrandingGlass(BaseModel):
    """Frosted-glass material controls (the 'liquid glass' look). Unset fields ⇒ the design
    system's own defaults. ``enabled=False`` ⇒ solid surfaces (no translucency, blur or sheen).
    ``opacity`` is the glass fill % (higher = more opaque), ``blur`` the backdrop blur in px,
    ``sheen`` the gloss strength %. All clamped, not rejected, so sliders can't produce a 422."""

    enabled: bool = True
    opacity: int | None = None
    blur: int | None = None
    sheen: int | None = None

    @field_validator("opacity")
    @classmethod
    def _opacity(cls, v: int | None) -> int | None:
        return _clamp(v, 20, 100)

    @field_validator("blur")
    @classmethod
    def _blur(cls, v: int | None) -> int | None:
        return _clamp(v, 0, 40)

    @field_validator("sheen")
    @classmethod
    def _sheen(cls, v: int | None) -> int | None:
        return _clamp(v, 0, 100)


# ---- client-portal pages + blocks (the no-code portal builder) ----
#
# The vocabulary below (block types, variants, settings keys, slugs, icons, caps) is pinned in
# pkgs/ui/src/lib/components/branding/block-contract.json; tests on both sides assert against that
# file so the API models and the portal registry cannot drift silently. Change the contract only
# together with both sides.

# Slugs a custom page may never take — they are (or shadow) real portal routes (auth, onboarding,
# API, share links…). A page carrying one is DROPPED on read, like an unknown block type: a bad
# page must degrade silently, never 422 the save or hijack a functional route.
RESERVED_SLUGS = frozenset(
    {
        "onboarding",
        "requests",
        "notifications",
        "profile",
        "adviser",
        "messages",
        "active-contact",
        "login",
        "logout",
        "mfa",
        "set-password",
        "forgot-password",
        "verify",
        "share",
        "search",
        "look-through",
        "terms",
        "transfer",
        "files",
        "_assets",
        "api",
        "page-view",
        "rum",
        "view-as",
        "i",
        "p",
    }
)

# Built-in portal pages a firm MAY override with its own definition: when a firm page carries one
# of these slugs the portal renders the firm's page in place of the built-in one.
SHADOWABLE_SLUGS = frozenset({"home", "accounts", "holdings", "activity", "documents", "insights"})

# Nav icon allowlist (lucide names the portal actually bundles). Unknown ⇒ clamped to the neutral
# "layout-grid" — an icon is cosmetic, so it must never be the reason a save fails.
PAGE_ICONS = frozenset(
    {
        "house",
        "wallet",
        "layers",
        "arrow-left-right",
        "file-text",
        "clipboard-list",
        "pie-chart",
        "receipt",
        "landmark",
        "briefcase",
        "shield",
        "users",
        "newspaper",
        "message-circle",
        "banknote",
        "chart-line",
        "folder",
        "star",
        "layout-grid",
        "info",
    }
)

# Hard ceilings so a runaway builder (or a hand-crafted payload) can't bloat the theme jsonb the
# portals fetch on every request. All enforced by truncation/degradation, never rejection.
MAX_PAGES = 12
MAX_BLOCKS_PER_PAGE = 24
MAX_TOTAL_BLOCKS = 200  # across every page and audience override together
MAX_NAV_PAGES = 6
MAX_PORTAL_TEMPLATES = 12

_SLUG = re.compile(r"^[a-z0-9-]{1,32}$")
_BLOCK_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
# C0 control characters (plus DEL) — stripped from firm-authored text so stored copy can't carry
# terminal-escape or NUL junk into logs/exports. Not the XSS defence: that's escaping at render.
_C0 = re.compile(r"[\x00-\x1f\x7f]")


def _plain_text(v: object, limit: int) -> str:
    """Clamp firm-authored display text: non-strings become empty, C0 controls are stripped and
    the result is truncated. Clamp, not reject — builder text fields must never 422 a save."""
    if not isinstance(v, str):
        return ""
    return _C0.sub("", v)[:limit]


def _clamp_setting_bool(v: object, default: bool) -> bool:
    """A settings boolean under the clamp-never-reject contract: a real bool passes, anything
    else — junk strings, numbers, None — degrades to the field's default instead of 422ing the
    save. (The pre-existing settings booleans — ``show_chart``/``show_sparklines``/
    ``hide_when_empty`` — predate this helper and still ride pydantic's lax coercion, which
    REJECTS true junk; migrating them is a separate decision because it changes what "1"/"true"
    mean on the wire.)"""
    return v if isinstance(v, bool) else default


def _clamp_setting_int(v: object, lo: int, hi: int) -> int | None:
    """Builder-slider clamp for optional integer settings: junk ⇒ None (the per-type default),
    out-of-range ⇒ pinned to the bound. See the module docstring's clamp/reject split."""
    # bool is an int subclass in Python; a True here is junk, not "limit 1".
    if v is None or isinstance(v, bool) or not isinstance(v, int | float | str):
        return None
    try:
        i = int(v)
    except (ValueError, OverflowError):  # non-numeric strings, NaN/inf floats
        return None
    return max(lo, min(hi, i))


# Per-type visual ``look`` allowlists — mirror of ``looks`` in block-contract.json (the contract-
# pin test is the drift referee, exactly as with variants). ``look`` is the block's VISUAL design,
# orthogonal to ``variant`` (its data shape); every list is headed by "auto", which follows the
# portal ``finish``. portfolioHeader deliberately carries NO look axis — no entry here and no
# ``look`` field on its settings model (its variants ARE its looks). news gained one in Oct 2026:
# the Insights page is where a firm's identity has the most room, so its library/feed take a look
# (card reading list · magazine · journal) like every other listing block.
BLOCK_LOOKS: dict[str, frozenset[str]] = {
    "accounts": frozenset({"auto", "card", "float", "ink"}),
    "allocation": frozenset({"auto", "card", "orbit", "spectrum"}),
    "holdings": frozenset({"auto", "card", "sheet", "bars"}),
    "activity": frozenset({"auto", "card", "timeline"}),
    "adviser": frozenset({"auto", "card", "portrait", "strip"}),
    "documents": frozenset({"auto", "card", "shelf", "index", "timeline"}),
    "actionItems": frozenset({"auto", "card", "banner"}),
    "onboardingProgress": frozenset({"auto", "banner", "slim", "milestones"}),
    "message": frozenset({"auto", "card", "note", "banner"}),
    "news": frozenset({"auto", "card", "magazine", "journal"}),
}


class BlockSettings(BaseModel):
    """Base for per-block-type settings. ``variant`` picks the block's presentation and clamps to
    the type's allowlist (unknown ⇒ "auto" — a variant from a newer/older portal build must never
    422 a save).

    ``extra="ignore"`` drops unknown keys from the *parsed model*, but ``Block._canon_settings``
    merges the canonical dump back OVER the raw dict, so unknown settings keys SURVIVE round-trips:
    a newer portal build may write keys an older API doesn't know yet, and a save through the older
    API must not strip them.
    """

    model_config = ConfigDict(extra="ignore")

    #: The type's presentation allowlist — mirror of ``variants`` in block-contract.json.
    _variants: ClassVar[frozenset[str]] = frozenset({"auto"})

    variant: str = "auto"

    @field_validator("variant", mode="before")
    @classmethod
    def _clamp_variant(cls, v: object) -> str:
        return v if isinstance(v, str) and v in cls._variants else "auto"


class LookedBlockSettings(BlockSettings):
    """Base for the block types that carry the visual ``look`` axis — the block's VISUAL design,
    orthogonal to ``variant`` (its data shape). Clamps to the type's allowlist exactly like
    ``variant`` (unknown/junk ⇒ "auto" — the builder must never 422); "auto" follows the portal
    ``finish``. portfolioHeader deliberately takes no look, which is why the field lives here
    rather than on ``BlockSettings``."""

    #: The type's look allowlist — sourced from ``BLOCK_LOOKS`` (the block-contract.json mirror).
    _looks: ClassVar[frozenset[str]] = frozenset({"auto"})

    look: str = "auto"

    @field_validator("look", mode="before")
    @classmethod
    def _clamp_look(cls, v: object) -> str:
        return v if isinstance(v, str) and v in cls._looks else "auto"


class PortfolioHeaderSettings(BlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "hero", "canvas", "ledger", "compact"})

    show_chart: bool = True
    #: Which action buttons the header offers. Clamped like ``variant`` (builder input, so junk —
    #: including a value from a newer/older portal build — degrades to the default, never a 422).
    actions: Literal["deposit_withdraw", "contact", "none"] = "deposit_withdraw"
    #: The performance-chart period the header opens on. Same clamp-not-reject contract.
    default_period: Literal["1M", "3M", "6M", "1Y"] = "6M"

    @field_validator("actions", mode="before")
    @classmethod
    def _actions(cls, v: object) -> str:
        if isinstance(v, str) and v in ("deposit_withdraw", "contact", "none"):
            return v
        return "deposit_withdraw"

    @field_validator("default_period", mode="before")
    @classmethod
    def _default_period(cls, v: object) -> str:
        return v if isinstance(v, str) and v in ("1M", "3M", "6M", "1Y") else "6M"


class AccountsSettings(LookedBlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "stack", "cards", "table", "summary"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["accounts"]

    limit: int | None = None
    #: The grouping axis the portal renders: 'wrapper' sections by product type, 'owner' by
    #: account holder (the family view). The old dead allowlist carried 'product_type', which
    #: nothing ever read — any stored value clamps to 'none' (today's flat look).
    group_by: str = "none"
    show_sparklines: bool = True

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 50)

    @field_validator("group_by", mode="before")
    @classmethod
    def _group_by(cls, v: object) -> str:
        return v if isinstance(v, str) and v in ("none", "wrapper", "owner") else "none"


class AllocationSettings(LookedBlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "donut", "bars"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["allocation"]


class AdviserSettings(LookedBlockSettings):
    # "auto" is its only variant (presentation); look is its whole visual range.
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["adviser"]


class NewsSettings(LookedBlockSettings):
    # ``limit`` caps the home feed. ``library`` is the whole content library as a page (the
    # Insights tab); ``feed`` the compact news card. ``look`` is the design either wears: ``card``
    # (the reading list / headline list), ``magazine`` (cover story + picture cards — the firm's
    # palette standing in where it has no photograph) or ``journal`` (type-led, chrome-less; what
    # ``auto`` resolves to under the ledger finish).
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "feed", "library"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["news"]
    limit: int | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 12)


class OnboardingProgressSettings(LookedBlockSettings):
    # "auto" is its only variant; look picks banner vs slim.
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["onboardingProgress"]


class HoldingsSettings(LookedBlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "top", "table", "grouped"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["holdings"]

    limit: int | None = None
    #: Search box + account filter above the listing (what the legacy /holdings page offered).
    #: Off by default — the compact home block stays quiet; the default full-listing pages turn
    #: it on (``default_pages()``). Junk clamps to the default (see ``_clamp_setting_bool``).
    toolbar: bool = False

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 25)

    @field_validator("toolbar", mode="before")
    @classmethod
    def _toolbar(cls, v: object) -> bool:
        return _clamp_setting_bool(v, False)


class ActivitySettings(LookedBlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "list", "table"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["activity"]

    limit: int | None = None
    #: Search box + account filter above the listing — see HoldingsSettings.toolbar.
    toolbar: bool = False

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 50)

    @field_validator("toolbar", mode="before")
    @classmethod
    def _toolbar(cls, v: object) -> bool:
        return _clamp_setting_bool(v, False)


class DocumentsSettings(LookedBlockSettings):
    _variants: ClassVar[frozenset[str]] = frozenset({"auto", "recent", "grouped"})
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["documents"]

    limit: int | None = None
    #: Search box + category filter pills (with per-category counts) above the listing — the
    #: legacy /documents page's toolbar. Off by default like the other listing toolbars.
    toolbar: bool = False
    #: The outstanding-requests banner: "N document requests need your attention → /requests".
    #: Fed by the ``requests_outstanding`` slice; absent real data renders no banner (the portal
    #: never fabricates a count). Off by default; the default documents page turns it on.
    show_requests: bool = False

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 12)

    @field_validator("toolbar", "show_requests", mode="before")
    @classmethod
    def _bools(cls, v: object) -> bool:
        return _clamp_setting_bool(v, False)


class ActionItemsSettings(LookedBlockSettings):
    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["actionItems"]

    limit: int | None = None
    hide_when_empty: bool = True

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: object) -> int | None:
        return _clamp_setting_int(v, 1, 10)


class MessageSettings(LookedBlockSettings):
    """A firm-authored notice block. ``title``/``body`` are stored as PLAIN TEXT — no markup —
    and the portal renderer escapes them on output; validation here only bounds length and strips
    C0 control characters. It is deliberately not the XSS defence (escaping at render is)."""

    _looks: ClassVar[frozenset[str]] = BLOCK_LOOKS["message"]

    title: str = ""
    body: str = ""

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v: object) -> str:
        return _plain_text(v, 80)

    @field_validator("body", mode="before")
    @classmethod
    def _body(cls, v: object) -> str:
        return _plain_text(v, 1000)


# One source of truth: a client-portal block type exists iff it has a settings model here. The
# portal's registry mirrors this via block-contract.json (pinned by tests on both sides).
BLOCK_SETTINGS: dict[str, type[BlockSettings]] = {
    "portfolioHeader": PortfolioHeaderSettings,
    "accounts": AccountsSettings,
    "allocation": AllocationSettings,
    "holdings": HoldingsSettings,
    "activity": ActivitySettings,
    "adviser": AdviserSettings,
    "news": NewsSettings,
    "documents": DocumentsSettings,
    "actionItems": ActionItemsSettings,
    "onboardingProgress": OnboardingProgressSettings,
    "message": MessageSettings,
}

# The composable client-portal blocks (the @graphene/ui investment components + notices).
# Enumerated for safety; unknown block types are dropped on read so a bad/old config can never
# break rendering.
CLIENT_BLOCK_TYPES = frozenset(BLOCK_SETTINGS)


def _known_blocks_only(v: Any) -> Any:
    """Filter a raw block list to known types. Unknown-type dict blocks are silently dropped, not
    rejected (forward/back-compat: an API reading a newer build's config must degrade to skipping
    what it can't validate, never 422 the lot). Non-dicts pass through to Block validation."""
    if isinstance(v, list):
        return [b for b in v if not isinstance(b, dict) or b.get("type") in CLIENT_BLOCK_TYPES]
    return v


class Block(BaseModel):
    """One arrangeable section of a client-portal page. ``w``/``h`` are the column/row span on the
    12-column responsive grid (1–12 / 1–6); unset ⇒ the block's per-type default. Order is the list
    order. Spans are clamped, not rejected, so the visual builder's drags can't produce a 422.
    ``id`` is the per-page instance handle (audience overrides and the builder's drag state key off
    it); missing/invalid/duplicate ids are re-minted deterministically by ``PageLayouts``."""

    id: str | None = None
    type: str
    visible: bool = True
    w: int | None = None
    h: int | None = None
    settings: dict[str, Any] = {}

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in CLIENT_BLOCK_TYPES:
            raise ValueError(f"unknown block type '{v}'")
        return v

    @field_validator("w")
    @classmethod
    def _w(cls, v: int | None) -> int | None:
        return _clamp(v, 1, 12)

    @field_validator("h")
    @classmethod
    def _h(cls, v: int | None) -> int | None:
        return _clamp(v, 1, 6)

    @model_validator(mode="after")
    def _canon_settings(self) -> Block:
        """Parse → clamp → dump the per-type settings, keeping storage a plain dict.

        The settings models use ``extra="ignore"``, so ``model_validate`` DROPS unknown keys — a
        plain ``model_dump()`` here would therefore strip settings written by a newer portal build
        whenever a save passes through an older API. Merging the canonical dump OVER the raw dict
        keeps both properties: unknown keys survive verbatim, known keys take the clamped value.
        """
        model = BLOCK_SETTINGS.get(self.type)
        if model is not None:
            self.settings = {**self.settings, **model.model_validate(self.settings).model_dump()}
        return self


def _remint_block_ids(blocks: list[Block]) -> None:
    """Give every block a stable, unique per-list id: a well-formed, not-yet-seen id is kept;
    anything missing/invalid/duplicate is re-minted as "{type}", "{type}-2", … — deterministic
    (same input ⇒ same ids on every parse), so overrides and drag state survive a round-trip."""
    seen: set[str] = set()
    for b in blocks:
        bid = b.id
        if bid is None or not _BLOCK_ID.match(bid) or bid in seen:
            bid, n = b.type, 1
            while bid in seen:
                n += 1
                bid = f"{b.type}-{n}"
            b.id = bid
        seen.add(bid)


class PageLayouts(BaseModel):
    """A page's block lists: ``default`` for everyone, plus optional audience overrides —
    ``individual`` (direct/individual clients) and ``organisation`` (corporate/trust clients).
    An unset override ⇒ that audience sees ``default``; an empty list is an explicit blank page."""

    default: list[Block] = []
    individual: list[Block] | None = None
    organisation: list[Block] | None = None

    @field_validator("default", "individual", "organisation", mode="before")
    @classmethod
    def _drop_unknown_blocks(cls, v: Any) -> Any:
        """Unknown block types are dropped (see ``_known_blocks_only``) and each list truncated to
        the per-page cap — degrade, never reject."""
        v = _known_blocks_only(v)
        if isinstance(v, list):
            return v[:MAX_BLOCKS_PER_PAGE]
        return v

    @model_validator(mode="after")
    def _mint_ids(self) -> PageLayouts:
        for blocks in (self.default, self.individual, self.organisation):
            if blocks:
                _remint_block_ids(blocks)
        return self


class PageDef(BaseModel):
    """One firm-defined client-portal page. The ``slug`` is the URL segment (``/p/{slug}`` — or a
    ``SHADOWABLE_SLUGS`` built-in it replaces); ``id`` is the stable handle a rename keeps; ``nav``
    puts it in the sidebar. Label/icon clamp (builder inputs); a slug that still fails ``_SLUG``
    after normalisation — or lands on ``RESERVED_SLUGS`` — marks the page for DROPPING at the
    ``Branding`` level, like an unknown block type: a bad page must never break the portal or 422
    the save."""

    id: str = ""
    slug: str = ""
    label: str = ""
    icon: str = "layout-grid"
    nav: bool = True
    layouts: PageLayouts = PageLayouts()

    @field_validator("slug", mode="before")
    @classmethod
    def _slug_norm(cls, v: object) -> str:
        # Normalise only (lowercase, trim); validity/reservation is judged in Branding._page_caps
        # where the page can be dropped rather than rejected.
        return v.strip().lower() if isinstance(v, str) else ""

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, v: object) -> str:
        return _plain_text(v, 24)

    @field_validator("icon", mode="before")
    @classmethod
    def _icon(cls, v: object) -> str:
        return v if isinstance(v, str) and v in PAGE_ICONS else "layout-grid"

    @model_validator(mode="after")
    def _page_id(self) -> PageDef:
        # An absent/malformed id regenerates from the slug — deterministic, so a hand-edited or
        # legacy page gets the same handle on every parse.
        if not self.id or not _BLOCK_ID.match(self.id):
            self.id = self.slug
        return self


#: The account-card FINISHES — how a product type's card is painted when it has no artwork and no
#: custom ``color``. Each is a recipe in pkgs/ui ``categorical.ts::finish`` built off the firm's
#: ``--brand`` (or the hero ink), so a finish never stores a colour of its own and re-branding
#: re-paints every card. A ``color`` replaces the recipe until it is cleared.
CARD_FINISHES = ("ink", "brand", "deep", "tint", "paper")
MAX_CARD_DESIGNS = 24


class CardDesign(BaseModel):
    """How one product type's account card is painted: a ``finish``, a ``color`` that replaces
    that finish, or an uploaded artwork (the card-issuer pattern) with the ``ink`` that reads on
    it. ``finish`` still applies under artwork, as the fill while the image loads and wherever
    it fails to, and again when a custom colour is cleared."""

    finish: Literal["ink", "brand", "deep", "tint", "paper"] | None = None
    #: A plain CSS colour overriding the finish recipe. Injection-shaped (it lands in an inline
    #: ``background-color``), so it is rejected rather than clamped.
    color: str | None = None
    art_url: str | None = None
    #: Which ink an artwork carries: ``light`` (white text, for a dark image) or ``dark``.
    ink: Literal["light", "dark"] | None = None

    @field_validator("finish", mode="before")
    @classmethod
    def _finish(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in CARD_FINISHES else None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v and not _COLOUR.match(v):
            raise ValueError("card colour must be a plain CSS colour")
        return v or None

    @field_validator("ink", mode="before")
    @classmethod
    def _ink(cls, v: object) -> str | None:
        return v if v in ("light", "dark") else None

    @field_validator("art_url")
    @classmethod
    def _art(cls, v: str | None) -> str | None:
        # Injection-shaped (it lands in an inline `background-image: url(…)`), so strict.
        if v and not (_URL.match(v) or _DATA_URI.match(v) or _PROXY_ASSET.match(v)):
            raise ValueError("card artwork must be a hosted image, proxy path or image data URI")
        return v or None


def _clamp_card_designs(v: object) -> dict[str, Any] | None:
    """Builder-produced, so clamp: non-dict ⇒ unset, blank or over-long keys and non-dict entries
    dropped, at most ``MAX_CARD_DESIGNS`` kept. The entries themselves still validate."""
    if not isinstance(v, dict):
        return None
    out: dict[str, Any] = {}
    for raw_key, entry in v.items():
        key = _plain_text(raw_key, 40).strip()
        if not key or key in out or not isinstance(entry, dict | CardDesign):
            continue
        out[key] = entry
        if len(out) >= MAX_CARD_DESIGNS:
            break
    return out


class ClientPortalTemplate(BaseModel):
    """One named client-portal design set.

    Firm identity remains on ``Branding``; a template owns the appearance and pages selected for
    one or more client groups (a business stream — checked first), client-classification slugs,
    or leftover client-role slugs. Missing appearance fields mean the product defaults, not
    values inherited from another template.
    """

    id: str = ""
    name: str = ""
    role_slugs: list[str] = []
    classification_slugs: list[str] = []
    #: Firm-assigned client groups (a business stream). Checked before the book
    #: when the portal picks a design. One slug belongs to one template.
    group_slugs: list[str] = []
    colors: BrandingPalettes = BrandingPalettes()
    radius: str | None = None
    font: str | None = None
    font_mono: str | None = None
    font_display: str | None = None
    mode: str = "auto"
    density: Literal["comfortable", "standard", "compact"] | None = None
    finish: Literal["card", "ledger"] | None = None
    #: Hero band. ``None`` derives a deep shade of the brand so the band cannot repeat the
    #: buttons; a set colour is used as-is. ``hero_feel`` paints it: gradient (None), solid
    #: or luminous.
    hero_color: str | None = None
    hero_feel: Literal["gradient", "solid", "luminous"] | None = None
    glass: BrandingGlass = BrandingGlass()
    #: Product type (``ISA``, ``SIPP``…) → how its account card is painted. Unset ⇒ the product
    #: defaults (``DEFAULT_CARD_DESIGNS`` in pkgs/ui categorical.ts).
    card_designs: dict[str, CardDesign] | None = None
    pages: list[PageDef] | None = None

    @field_validator("card_designs", mode="before")
    @classmethod
    def _card_designs(cls, v: object) -> dict[str, Any] | None:
        return _clamp_card_designs(v)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v: object) -> str:
        value = v.strip().lower() if isinstance(v, str) else ""
        return value if _BLOCK_ID.match(value) else ""

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: object) -> str:
        return _plain_text(v, 40)

    @field_validator("role_slugs", "classification_slugs", "group_slugs", mode="before")
    @classmethod
    def _slugs(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        slugs: list[str] = []
        for raw in v:
            slug = raw.strip().lower() if isinstance(raw, str) else ""
            if _BLOCK_ID.match(slug) and slug not in slugs:
                slugs.append(slug)
        return slugs[:50]

    @field_validator("radius")
    @classmethod
    def _radius(cls, v: str | None) -> str | None:
        if v and not _RADIUS.match(v):
            raise ValueError("radius must be a number with px/rem/em")
        return v or None

    @field_validator("font", "font_mono", "font_display")
    @classmethod
    def _font(cls, v: str | None) -> str | None:
        if v and not _FONT.match(v):
            raise ValueError("font must be a plain family name")
        return v or None

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in _MODES:
            raise ValueError("mode must be auto|light|dark")
        return v

    @field_validator("density", mode="before")
    @classmethod
    def _density(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("comfortable", "standard", "compact") else None

    @field_validator("finish", mode="before")
    @classmethod
    def _finish(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("card", "ledger") else None

    @field_validator("hero_color")
    @classmethod
    def _hero_color(cls, v: str | None) -> str | None:
        if v and not _COLOUR.match(v):
            raise ValueError("hero colour must be a plain CSS colour")
        return v or None

    @field_validator("hero_feel", mode="before")
    @classmethod
    def _hero_feel(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("gradient", "solid", "luminous") else None

    @model_validator(mode="after")
    def _page_caps(self) -> ClientPortalTemplate:
        if self.pages is None:
            return self
        kept: list[PageDef] = []
        seen_slugs: set[str] = set()
        total = 0
        nav_count = 0
        for page in self.pages[:MAX_PAGES]:
            if not _SLUG.match(page.slug) or page.slug in RESERVED_SLUGS or page.slug in seen_slugs:
                continue
            seen_slugs.add(page.slug)
            for audience in ("default", "individual", "organisation"):
                blocks: list[Block] | None = getattr(page.layouts, audience)
                if blocks is None:
                    continue
                room = max(0, MAX_TOTAL_BLOCKS - total)
                if len(blocks) > room:
                    blocks = blocks[:room]
                    setattr(page.layouts, audience, blocks)
                total += len(blocks)
            if page.nav:
                nav_count += 1
                if nav_count > MAX_NAV_PAGES:
                    page.nav = False
            kept.append(page)
        if kept and not any(page.nav for page in kept):
            kept[0].nav = True
        self.pages = kept
        return self


class Branding(BaseModel):
    """The tenant's white-label branding. All fields optional; unset ⇒ Graphene defaults apply.
    ``colors`` holds separate ``light``/``dark`` palettes; radius/font/logos/copy/``glass`` shared.
    ``glass`` tunes the frosted-glass material (on/off, opacity, blur, sheen). ``pages`` (client
    portal only) is the firm's composable page set for the no-code builder; unset ⇒ the built-in
    ``default_pages()``. ``layout`` is the pre-pages flat home layout, kept as a migration source
    and a one-release compatibility mirror of the home page (see ``_page_caps``)."""

    v: int = 1
    colors: BrandingPalettes = BrandingPalettes()
    radius: str | None = None
    #: UI typeface -> ``--font-sans``. ``font_mono`` -> ``--font-mono`` (code, tabular figures,
    #: reference/ID columns); ``font_display`` -> ``--font-display`` (the display/numerals face:
    #: portfolio totals, statement figures — the private-bank lever); unset ⇒ the platform's
    #: default stack for each (``--font-display`` falls back to ``--font-sans`` in theme.css).
    font: str | None = None
    font_mono: str | None = None
    font_display: str | None = None
    mode: str = "auto"
    #: Type/spacing density — demographic adaptivity as a first-class token. The portals emit a
    #: root ``font-size`` (the whole design system is rem-based, so one declaration scales type,
    #: spacing and touch targets coherently): ``comfortable`` is the larger-text mode (65+),
    #: ``compact`` the data-dense professional mode. ``None`` ⇒ standard. Unlike ``mode`` this
    #: CLAMPS (junk ⇒ None) rather than rejects: it's a builder Segmented value, not
    #: injection-shaped — the TS side only ever emits hardcoded numbers for the known values.
    density: Literal["comfortable", "standard", "compact"] | None = None
    #: Portal-wide surface FINISH — one token axis that re-skins every widget coherently (they all
    #: render through Card.* + design tokens). ``card`` (or None) is today's carded look; ``ledger``
    #: is the private-bank register: transparent sections with hairline rules instead of card
    #: chrome, square corners, no frosted glass, small-caps card titles and display-face numerals.
    #: Like ``density`` it CLAMPS (junk ⇒ None) rather than rejects — it's a builder Segmented
    #: value, not injection-shaped: the TS side emits only hardcoded token declarations for the
    #: known values, never the stored string.
    finish: Literal["card", "ledger"] | None = None
    #: Hero band colour and treatment. Client shopfront only — ``staffPortalBranding`` drops them
    #: with the other style axes. See ``ClientPortalTemplate``.
    hero_color: str | None = None
    hero_feel: Literal["gradient", "solid", "luminous"] | None = None
    logo_url: str | None = None
    logo_dark_url: str | None = None
    icon_url: str | None = None
    logo_variant: str = "wordmark"
    favicon_url: str | None = None
    login: BrandingLogin = BrandingLogin()
    support: BrandingSupport = BrandingSupport()
    glass: BrandingGlass = BrandingGlass()
    #: Product type → account-card design (client portal). See ``CardDesign``.
    card_designs: dict[str, CardDesign] | None = None
    layout: list[Block] | None = None
    pages: list[PageDef] | None = None
    templates: list[ClientPortalTemplate] | None = None
    default_template_id: str | None = None

    @field_validator("layout", mode="before")
    @classmethod
    def _drop_unknown_blocks(cls, v: Any) -> Any:
        """Ignore blocks with an unknown type (forward/back-compat), rather than reject the lot."""
        return _known_blocks_only(v)

    @field_validator("pages", mode="before")
    @classmethod
    def _drop_non_dict_pages(cls, v: Any) -> Any:
        """A garbage entry in ``pages`` is dropped, not rejected — same degradation contract as
        unknown block types (a corrupt page must never 422 the save or break the portal)."""
        if isinstance(v, list):
            return [p for p in v if isinstance(p, dict | PageDef)]
        return v

    @field_validator("card_designs", mode="before")
    @classmethod
    def _card_designs(cls, v: object) -> dict[str, Any] | None:
        return _clamp_card_designs(v)

    @field_validator("templates", mode="before")
    @classmethod
    def _drop_non_dict_templates(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [t for t in v if isinstance(t, dict | ClientPortalTemplate)]
        return v

    @field_validator("default_template_id", mode="before")
    @classmethod
    def _default_template(cls, v: object) -> str | None:
        value = v.strip().lower() if isinstance(v, str) else ""
        return value if _BLOCK_ID.match(value) else None

    @model_validator(mode="before")
    @classmethod
    def _migrate_layout_to_pages(cls, data: Any) -> Any:
        """Read-side migration (shape-sniffed, like ``_migrate_flat_colors``): a pre-pages config
        carrying only the flat home ``layout`` becomes a single home page on parse. ``layout`` is
        deliberately left in place — ``_page_caps`` mirrors the home page back into it anyway."""
        if isinstance(data, dict):
            layout = data.get("layout")
            if data.get("pages") is None and isinstance(layout, list) and layout:
                data = {
                    **data,
                    "pages": [
                        {
                            "id": "home",
                            "slug": "home",
                            "label": "Home",
                            "icon": "house",
                            "nav": True,
                            "layouts": {"default": layout},
                        }
                    ],
                }
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_flat_colors(cls, data: Any) -> Any:
        """Back-compat: an older single-palette ``colors: {primary, …}`` becomes the light palette
        (``colors: {light: {primary, …}}``), so pre-existing branding keeps working after read."""
        if isinstance(data, dict):
            colors = data.get("colors")
            if isinstance(colors, dict) and _COLOUR_KEYS.intersection(colors):
                data = {**data, "colors": {"light": colors}}
        return data

    @field_validator("radius")
    @classmethod
    def _radius(cls, v: str | None) -> str | None:
        if v and not _RADIUS.match(v):
            raise ValueError("radius must be a number with px/rem/em")
        return v or None

    @field_validator("font", "font_mono", "font_display")
    @classmethod
    def _font(cls, v: str | None) -> str | None:
        if v and not _FONT.match(v):
            raise ValueError("font must be a plain family name")
        return v or None

    @field_validator("density", mode="before")
    @classmethod
    def _density(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("comfortable", "standard", "compact") else None

    @field_validator("finish", mode="before")
    @classmethod
    def _finish(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("card", "ledger") else None

    @field_validator("hero_color")
    @classmethod
    def _hero_color(cls, v: str | None) -> str | None:
        if v and not _COLOUR.match(v):
            raise ValueError("hero colour must be a plain CSS colour")
        return v or None

    @field_validator("hero_feel", mode="before")
    @classmethod
    def _hero_feel(cls, v: object) -> str | None:
        return v if isinstance(v, str) and v in ("gradient", "solid", "luminous") else None

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in _MODES:
            raise ValueError("mode must be auto|light|dark")
        return v

    @field_validator("logo_variant", mode="before")
    @classmethod
    def _logo_variant(cls, v: object) -> str:
        if v in (None, ""):
            return "wordmark"
        if v not in _LOGO_VARIANTS:
            raise ValueError("logo_variant must be icon|wordmark")
        return str(v)

    @field_validator("logo_url", "logo_dark_url", "icon_url", "favicon_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v and not (_URL.match(v) or _DATA_URI.match(v) or _PROXY_ASSET.match(v)):
            raise ValueError("asset must be a hosted image, proxy path or image data URI")
        return v or None

    @model_validator(mode="after")
    def _page_caps(self) -> Branding:
        """Enforce the page-set invariants by degrading, never rejecting (the builder is the only
        writer of pages): drop reserved/invalid-slug pages, dedupe slugs (first definition wins),
        truncate to the page/total-block caps, clamp the nav count and guarantee the portal always
        has at least one nav entry to land on. Ends with the legacy ``layout`` mirror."""
        if self.pages is not None:
            kept: list[PageDef] = []
            seen_slugs: set[str] = set()
            for page in self.pages:
                if (
                    not _SLUG.match(page.slug)
                    or page.slug in RESERVED_SLUGS
                    or page.slug in seen_slugs
                ):
                    continue
                seen_slugs.add(page.slug)
                kept.append(page)
            kept = kept[:MAX_PAGES]

            # Total-block ceiling across the whole set: trailing pages' lists are truncated once
            # the budget runs out (page order = builder order, so the front pages win).
            total = 0
            for page in kept:
                for audience in ("default", "individual", "organisation"):
                    blocks: list[Block] | None = getattr(page.layouts, audience)
                    if blocks is None:
                        continue
                    room = max(0, MAX_TOTAL_BLOCKS - total)
                    if len(blocks) > room:
                        blocks = blocks[:room]
                        setattr(page.layouts, audience, blocks)
                    total += len(blocks)

            # At most MAX_NAV_PAGES in the sidebar: extras keep their page, lose their nav slot
            # (order preserved). But never zero — the portal needs somewhere to land.
            nav_count = 0
            for page in kept:
                if page.nav:
                    nav_count += 1
                    if nav_count > MAX_NAV_PAGES:
                        page.nav = False
            if kept and not any(p.nav for p in kept):
                kept[0].nav = True

            self.pages = kept

        # Legacy mirror (deploy skew): a portal build from before the pages model reads ONLY
        # ``layout``, so while one can still be live the home page's default layout is mirrored
        # into it on every parse. Both storage and serving hit this — ``set_branding`` persists
        # ``model_dump_json()`` and ``env_config_by_host`` serves ``model_dump()``, and each
        # serialises a parsed model, so an after-validator is the one place that covers both.
        # Remove the mirror (and ``layout`` itself) one release after every portal reads ``pages``.
        if self.pages:
            home = next((p for p in self.pages if p.slug == "home"), self.pages[0])
            self.layout = list(home.layouts.default)

        if self.templates is not None:
            kept_templates: list[ClientPortalTemplate] = []
            seen_ids: set[str] = set()
            assigned_roles: set[str] = set()
            assigned_classifications: set[str] = set()
            assigned_groups: set[str] = set()
            for template in self.templates[:MAX_PORTAL_TEMPLATES]:
                if not template.id or template.id in seen_ids:
                    continue
                seen_ids.add(template.id)
                template.role_slugs = [
                    role for role in template.role_slugs if role not in assigned_roles
                ]
                assigned_roles.update(template.role_slugs)
                template.classification_slugs = [
                    slug
                    for slug in template.classification_slugs
                    if slug not in assigned_classifications
                ]
                assigned_classifications.update(template.classification_slugs)
                template.group_slugs = [
                    slug for slug in template.group_slugs if slug not in assigned_groups
                ]
                assigned_groups.update(template.group_slugs)
                kept_templates.append(template)
            self.templates = kept_templates
            if self.default_template_id not in seen_ids:
                self.default_template_id = kept_templates[0].id if kept_templates else None
        return self


def default_pages() -> list[PageDef]:
    """The built-in client-portal page set — what a client sees before the firm customises
    anything: a short composed home page, an accounts page holding the analysis, and the two
    full-listing surfaces (activity, documents). Not stored per tenant: the
    portal falls back to this when ``pages`` is unset and the builder starts from it, so ids are
    the slugs / block types and a firm's first edit diffs cleanly against the defaults.

    Two blocks are deliberately quiet rather than absent. ``news`` ships HIDDEN so a firm that has
    not published anything does not get an empty card on every home page; the live source is
    ``GET /crm/content``. When the block is shown, an empty eligible set still collapses (same
    ``isBlank`` treatment as ``message``). ``message`` ships with no copy, which the
    renderer treats as blank and gives no grid cell at all (``portal-page.svelte::isBlank``), so it
    costs a client nothing and gives the firm a placed slot to write into.

    The ``organisation`` overrides exist because a trust or company has several signatories and no
    single personal relationship: home and accounts re-cut as a REGISTER (owner-grouped tables, a
    roll-up ahead of the accounts table, the adviser as a line rather than a portrait) while the
    individual audience keeps the personal composition. The three listing pages carry no override —
    a full table is already the right answer for both audiences.
    """

    def block(
        type_: str,
        *,
        w: int,
        h: int,
        visible: bool = True,
        settings: dict[str, Any] | None = None,
        id_: str | None = None,
    ) -> Block:
        # Block._canon_settings fills the type's canonical settings dump over this literal, so an
        # empty dict here parses identically to {"variant": "auto"} — the contract keeps its
        # entries minimal and this side mirrors that. ``id_`` is spelled out only where a page
        # holds two blocks of one type (the accounts page's organisation override).
        return Block(
            id=id_ or type_, type=type_, visible=visible, w=w, h=h, settings=settings or {}
        )

    def page(
        slug: str,
        label: str,
        icon: str,
        blocks: list[Block],
        *,
        organisation: list[Block] | None = None,
    ) -> PageDef:
        return PageDef(
            id=slug,
            slug=slug,
            label=label,
            icon=icon,
            nav=True,
            layouts=PageLayouts(default=blocks, organisation=organisation),
        )

    # Spans, order, audience overrides AND per-block `settings` objects mirror block-contract.json's
    # `defaultPages`. This CANNOT read the contract file at runtime (the API container ships without
    # pkgs/ui), so the literal lives here and test_branding.py pins it to the contract (the FULL
    # canonical settings, not just the variant — richer defaults must not diverge silently). The
    # first draft of this function invented its own spans and the two sides shipped different
    # "defaults" — hence the pin.
    # HOME IS SHORT (the TS twin's docstring carries the reasoning): the figure, what needs you,
    # the accounts, what has changed, what has arrived, who to talk to, the firm's note.
    # Allocation, holdings and news ship HIDDEN rather than absent so a firm can toggle one back
    # in the studio.
    home_blocks = [
        block("onboardingProgress", w=12, h=2, settings={"look": "milestones"}),
        block("actionItems", w=12, h=1, settings={"look": "banner", "hide_when_empty": True}),
        block("portfolioHeader", w=12, h=1, settings={"variant": "hero", "show_chart": True}),
        block(
            "accounts",
            w=6,
            h=1,
            settings={
                "variant": "stack",
                "look": "card",
                "group_by": "wrapper",
                "show_sparklines": True,
            },
        ),
        # The adviser under the accounts, above the listings (portal-pages.ts).
        block("adviser", w=6, h=1, settings={"look": "card"}),
        block("activity", w=6, h=2, settings={"variant": "list", "look": "card", "limit": 4}),
        block("documents", w=6, h=2, settings={"variant": "recent", "limit": 4}),
        block("message", w=12, h=2, settings={"look": "note"}),
        block("allocation", w=5, h=2, visible=False, settings={"variant": "donut"}),
        block("holdings", w=6, h=2, visible=False, settings={"variant": "top", "limit": 5}),
        block("news", w=4, h=2, visible=False),
    ]
    home_organisation = [
        block("onboardingProgress", w=12, h=2, settings={"look": "milestones"}),
        block("actionItems", w=12, h=1, settings={"look": "banner", "hide_when_empty": True}),
        block("portfolioHeader", w=12, h=1, settings={"variant": "hero", "show_chart": True}),
        block("accounts", w=12, h=2, settings={"variant": "table", "group_by": "owner"}),
        block("adviser", w=12, h=1, settings={"look": "strip"}),
        block("activity", w=12, h=2, settings={"variant": "table", "limit": 8}),
        block("documents", w=12, h=2, settings={"variant": "recent", "limit": 4}),
        block("message", w=12, h=2, settings={"look": "note"}),
        block(
            "allocation", w=6, h=2, visible=False, settings={"variant": "bars", "look": "spectrum"}
        ),
        block("holdings", w=6, h=2, visible=False, settings={"variant": "top", "limit": 8}),
        block("news", w=4, h=2, visible=False),
    ]

    # PORTFOLIO is the whole-book lens neither home nor an account record gives: the figure and
    # its return, how it is spread, and what it is made of — the holdings grouped under the
    # donut's asset classes. No account cards: home's deck already lists them. There is no
    # accounts or holdings page in the default set — the phone's tab bar is capped at five.
    def holdings_table() -> Block:
        return block("holdings", w=12, h=4, settings={"variant": "table", "toolbar": True})

    def portfolio_blocks() -> list[Block]:
        # A factory, not a shared list: the two audience layouts must not hold the SAME Block
        # instances — ``PageLayouts._mint_ids`` mutates blocks in place, so one shared instance
        # would let a re-mint on one audience rewrite the other's id.
        return [
            block(
                "portfolioHeader",
                w=12,
                h=1,
                settings={"variant": "compact", "show_chart": False},
            ),
            block("allocation", w=12, h=2, settings={"variant": "donut"}),
            block("holdings", w=12, h=4, settings={"variant": "grouped"}),
        ]

    # The roll-up is the ADDED block, so it takes the second id and the table keeps "accounts" —
    # exactly the ids a copy-on-write override of the default layout would itself have carried.
    accounts_organisation = [
        block("accounts", id_="accounts-2", w=12, h=2, settings={"variant": "summary"}),
        block(
            "accounts",
            w=12,
            h=4,
            settings={"variant": "table", "group_by": "wrapper", "show_sparklines": True},
        ),
        block("allocation", w=12, h=2, settings={"variant": "bars", "look": "spectrum"}),
        holdings_table(),
    ]
    # The listing pages ship their toolbars ON (search/filters — and the documents requests
    # banner), replicating what the legacy listing pages offered; the home page's compact blocks
    # deliberately stay toolbar-less.
    return [
        page("home", "Home", "house", home_blocks, organisation=home_organisation),
        page(
            "portfolio",
            "Portfolio",
            "pie-chart",
            portfolio_blocks(),
            organisation=accounts_organisation,
        ),
        # The home card's list with the toolbar on, not a table: one style for activity wherever
        # it is read, and a Date · Description · Amount table is a statement, not a feed, on a
        # phone. The portal uncaps a toolbar'd list (portal-page.svelte `sliceCap`).
        page(
            "activity",
            "Activity",
            "arrow-left-right",
            [
                block(
                    "activity",
                    w=12,
                    h=4,
                    settings={"variant": "list", "look": "card", "toolbar": True},
                )
            ],
        ),
        # "Docs" is the label the portal's nav has always shown — the page label feeds the nav
        # derivation, so changing it here would rename the tab for every un-customised firm.
        page(
            "documents",
            "Docs",
            "file-text",
            [
                block(
                    "documents",
                    w=12,
                    h=4,
                    settings={
                        "variant": "grouped",
                        "look": "timeline",
                        "toolbar": True,
                        "show_requests": True,
                    },
                )
            ],
        ),
        # Insights is a PAGE in the set, not a route the client app bolts onto the bar: a firm
        # places, renames or removes it in the studio like any other tab. The `library` news
        # variant is the whole content library (lead + list); the `/insights/{uid}` reader stays
        # a built-in route, like `/documents/{uid}`.
        page(
            "insights",
            "Insights",
            "newspaper",
            # ``magazine`` pinned: the default Insights page leads with the firm's own pictures
            # and palette — the one page where its identity has the room.
            [block("news", w=12, h=4, settings={"variant": "library", "look": "magazine"})],
        ),
    ]


Portal = Literal["client", "platform"]


class Theme(BaseModel):
    """The tenant's per-portal branding (the ``auth.tenants.theme`` jsonb). Client and platform are
    branded independently; ``client`` also carries the composable ``pages`` (+ the legacy ``layout``
    mirror)."""

    v: int = 2
    client: Branding = Branding()
    platform: Branding = Branding()


def parse_theme(theme: dict[str, Any] | None) -> Theme:
    """Parse a stored ``theme`` jsonb into a validated per-portal Theme. A legacy single-Branding
    object (no ``client``/``platform`` keys — the pre-per-portal shape) is applied to BOTH portals,
    so existing branding keeps working after the upgrade."""
    if not theme:
        return Theme()
    if "client" not in theme and "platform" not in theme:
        legacy = Branding.model_validate(theme)
        return Theme(client=legacy, platform=legacy.model_copy())
    return Theme.model_validate(theme)


def parse(theme: dict[str, Any] | None, portal: Portal) -> Branding:
    """One portal's Branding from a stored theme (with the legacy migration)."""
    t = parse_theme(theme)
    return t.client if portal == "client" else t.platform


def parse_lenient(theme: dict[str, Any] | None, portal: Portal) -> Branding:
    """Invalid stored theme → empty Branding, never raise. Writes still use ``parse``."""
    try:
        return parse(theme, portal)
    except ValidationError:
        logger.warning(
            "stored tenant theme failed validation; serving default branding",
            exc_info=True,
        )
        return Branding()


async def _theme_row(session: AsyncSession, tenant_id: int) -> tuple[str, Theme]:
    row = (
        (
            await session.execute(
                text("SELECT name, theme FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        )
        .mappings()
        .one()
    )
    return row["name"], parse_theme(row["theme"])


async def get_branding(session: AsyncSession, tenant_id: int, portal: Portal) -> dict[str, object]:
    """One portal's branding + the firm name (for the editors)."""
    name, theme = await _theme_row(session, tenant_id)
    resolved = getattr(theme, portal).model_dump()
    for field in ("logo_url", "logo_dark_url", "icon_url", "favicon_url"):
        value = resolved.get(field)
        if isinstance(value, str):
            resolved[field] = storage.public_asset_src(value)
    client_roles: list[dict[str, str]] = []
    if portal == "client":
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT slug, name FROM auth.roles "
                        "WHERE tenant_id = :t AND portal = 'client' ORDER BY name, slug"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        client_roles = [{"value": str(row["slug"]), "label": str(row["name"])} for row in rows]
    return {
        "name": name,
        "portal": portal,
        "branding": resolved,
        "client_roles": client_roles,
    }


async def stored_branding(session: AsyncSession, tenant_id: int, portal: Portal) -> Branding:
    """One portal's stored branding as a parsed model — what a PUT is about to replace. Exists
    for the ``pages:write`` diff gate on the branding endpoints: comparing parsed models (not raw
    jsonb) puts both sides through the same validators, so legacy shapes (``layout``-only, flat
    colours) canonicalise before the comparison rather than reading as a phantom change."""
    _name, theme = await _theme_row(session, tenant_id)
    return theme.client if portal == "client" else theme.platform


def pages_changed(current: Branding, incoming: Branding) -> bool:
    """Does this save alter the portal COMPOSITION (``Branding.pages``)?

    The gate behind ``pages:write``: colours/logos/copy stay ``branding:write``, but adding,
    removing or re-laying-out pages needs the extra permission. Compared on the canonical
    ``model_dump`` of the parsed pages, because the editors always submit the WHOLE branding
    object — a colour-only save echoes the stored pages back verbatim, and after both sides pass
    the same validators (id regeneration, clamps, caps) an untouched page set dumps identically,
    so the echo must never demand ``pages:write``. ``None`` (track the platform defaults) vs a
    concrete page set IS a composition change in either direction: materialising the defaults
    pins them, and clearing them re-tracks whatever the platform ships next."""

    def canon(b: Branding) -> dict[str, object]:
        return {
            "pages": None if b.pages is None else [p.model_dump() for p in b.pages],
            "default_template_id": b.default_template_id,
            "templates": None
            if b.templates is None
            else [
                {
                    "id": template.id,
                    "role_slugs": template.role_slugs,
                    "classification_slugs": template.classification_slugs,
                    "group_slugs": template.group_slugs,
                    "pages": None
                    if template.pages is None
                    else [page.model_dump() for page in template.pages],
                }
                for template in b.templates
            ],
        }

    return canon(current) != canon(incoming)


async def set_branding(
    session: AsyncSession,
    tenant_id: int,
    *,
    portal: Portal,
    name: str | None,
    branding: Branding,
) -> dict[str, object]:
    """Validate + persist ONE portal's branding into ``auth.tenants.theme`` (preserving the other
    portal), and optionally the firm ``name``. Validation already happened on the pydantic model."""
    _name, theme = await _theme_row(session, tenant_id)
    setattr(theme, portal, branding)
    await session.execute(
        text(
            "UPDATE auth.tenants SET theme = CAST(:theme AS jsonb), "
            "  name = COALESCE(:name, name) WHERE id = :t"
        ),
        {"theme": theme.model_dump_json(), "name": name, "t": tenant_id},
    )
    # THE EMAIL PATH CACHES A RESOLVED BRAND FOR FIVE MINUTES, and nothing invalidated it. So a firm
    # uploaded its logo, sent itself a test email and got the OLD masthead — most often the text
    # wordmark, the arm for a firm with no logo at all — which reads as "the logo feature does not
    # work" rather than as a stale read that comes right on its own. Worse for a rebrand: the mail
    # went out under the previous colours with no way to tell why.
    #
    # Cleared HERE rather than in the two routers, because this is the one function that writes the
    # value and a third write path would otherwise have to remember. `clear_caches` had NO caller in
    # `app/` at all — the only thing reaching for it was a dev script poking `_brand_cache` behind a
    # private-access waiver, which is the shape of a missing product call.
    #
    # Imported locally, not at module scope: `app.email.transport` imports this module for its
    # `Branding` model, so a top-level import would be a cycle.
    from app.email import transport as email_transport

    email_transport.clear_caches()
    return await get_branding(session, tenant_id, portal)
