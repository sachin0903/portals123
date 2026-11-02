"""White-label branding: strict validation (no CSS/HTML injection), storage guard, the composable
client-portal pages model (contract-pinned against block-contract.json), and the tenant round-trip
through the DB + the per-host env-config channel."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import branding, environments
from app.branding import Branding, BrandingColors, ClientPortalTemplate
from app.provisioning import provision_tenant


def test_valid_branding_parses_light_dark() -> None:
    b = Branding.model_validate(
        {
            "colors": {"light": {"primary": "#2b6cb0"}, "dark": {"primary": "#8ab4f8"}},
            "radius": "0.75rem",
            "font": "Inter",
            "mode": "dark",
            "logo_url": "https://cdn.example.com/logo.svg",
            "login": {"tagline": "Wealth, considered."},
        }
    )
    assert b.colors.light.primary == "#2b6cb0"
    assert b.colors.dark.primary == "#8ab4f8"
    assert b.mode == "dark"


def test_legacy_flat_colors_migrate_to_light() -> None:
    """Pre-existing single-palette branding is read as the light palette (back-compat)."""
    b = Branding.model_validate({"colors": {"primary": "#123456", "brand": "#abcdef"}})
    assert b.colors.light.primary == "#123456"
    assert b.colors.light.brand == "#abcdef"
    assert b.colors.dark.primary is None


def test_layout_keeps_known_blocks_and_drops_unknown() -> None:
    """The client home layout accepts enumerated block types and silently drops unknown ones,
    so a stale/garbage config can never break rendering."""
    b = Branding.model_validate(
        {
            "layout": [
                {"type": "portfolioHeader", "visible": True},
                {"type": "allocation", "visible": False},
                {"type": "totally-made-up", "visible": True},
            ]
        }
    )
    assert b.layout is not None
    kept = [(blk.type, blk.visible) for blk in b.layout]
    assert kept == [("portfolioHeader", True), ("allocation", False)]


@pytest.mark.parametrize(
    "payload",
    [
        {"colors": {"primary": "red; } body { display:none"}},  # CSS break-out
        {"colors": {"primary": "</style><script>alert(1)</script>"}},  # HTML injection
        {"colors": {"brand": "url(https://evil)"}},  # url() not allowed
        {"radius": "0.75rem; color:red"},  # radius break-out
        {"radius": "10"},  # missing unit
        {"font": "Inter'; }"},  # font break-out
        {"mode": "neon"},  # bad enum
        {"logo_url": "javascript:alert(1)"},  # non-https asset
        {"favicon_url": "http://insecure/x.ico"},  # non-https
    ],
)
def test_injection_and_bad_values_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Branding.model_validate(payload)


def test_full_palette_tokens_parse() -> None:
    """The basic palette accepts each of its fourteen tokens, each optional and validated."""
    b = Branding.model_validate(
        {
            "colors": {
                "light": {
                    "card": "#ffffff",
                    "muted_foreground": "oklch(0.5 0.02 256)",
                    "positive": "#0a0",
                    "negative": "#900",
                    "warning": "#b45309",
                    "info": "oklch(0.58 0.12 245)",
                }
            }
        }
    )
    assert b.colors.light.card == "#ffffff"
    assert b.colors.light.muted_foreground == "oklch(0.5 0.02 256)"
    assert b.colors.light.positive == "#0a0"
    assert b.colors.light.negative == "#900"
    assert b.colors.light.warning == "#b45309"
    assert b.colors.light.info == "oklch(0.58 0.12 245)"


def test_basic_palette_is_fourteen_tokens() -> None:
    """Every other colour is an alias in theme.css; growing this set re-opens per-token branding."""
    assert len(BrandingColors.model_fields) == 14


def test_retired_palette_keys_are_dropped_not_rejected() -> None:
    """A theme saved before the palette collapsed still loads: the per-token keys it carries
    (sidebar, popover, the status foregrounds…) are ignored rather than 422ing the firm."""
    b = Branding.model_validate(
        {
            "colors": {
                "light": {
                    "primary": "#123456",
                    "sidebar_primary": "#0af",
                    "destructive": "#900",
                    "warning_foreground": "#fffbeb",
                }
            }
        }
    )
    assert b.colors.light.primary == "#123456"
    dumped = b.colors.light.model_dump(exclude_none=True)
    assert dumped == {"primary": "#123456"}


def test_retired_keys_still_mark_a_legacy_flat_palette() -> None:
    """A flat colours object holding only retired keys is still recognised as the old shape."""
    b = Branding.model_validate({"colors": {"ring": "#0af", "brand": "#0af"}})
    assert b.colors.light.brand == "#0af"


def test_colour_vars_mirror_palette_fields() -> None:
    """Every colour field maps to a CSS var (1:1) and vice-versa — no orphaned keys either side."""
    assert set(branding.COLOUR_VARS) == set(BrandingColors.model_fields)


def test_derived_tonal_bases_are_all_brandable() -> None:
    """theme.css derives the tonal tokens (`-subtle`/`-strong`/`-line`) by color-mix'ing BASE
    tokens against --background/--foreground. `success`/`destructive` alias `positive`/`negative`,
    so the brandable bases are these five plus the two anchors every mix resolves against."""
    bases = {"warning", "info", "brand", "positive", "negative"}
    assert bases <= set(branding.COLOUR_VARS)
    assert {"background", "foreground"} <= set(branding.COLOUR_VARS)


def test_mono_font_is_a_branding_axis() -> None:
    """Updating "all fonts" includes the mono one: reference/ID columns, tabular figures and the
    template editor all key off --font-mono, which had no branding surface at all."""
    b = Branding.model_validate({"font": "Inter", "font_mono": "IBM Plex Mono"})
    assert b.font == "Inter"
    assert b.font_mono == "IBM Plex Mono"
    assert Branding().font_mono is None  # unset ⇒ the platform default stack


@pytest.mark.parametrize(
    "payload",
    [
        {"font_mono": "Mono'; } :root{display:none"},  # CSS break-out
        {"font_mono": "</style><script>x"},  # HTML injection
        {"font_display": "Serif'; } :root{display:none"},  # display face, same gate
        {"font_display": "</style><script>x"},
        {"colors": {"warning": "red; } body{display:none"}},  # new token, same gate
        {"colors": {"info": "url(https://evil)"}},
    ],
)
def test_new_fields_share_the_injection_gate(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Branding.model_validate(payload)


def test_display_font_is_a_branding_axis() -> None:
    """The display/numerals face (--font-display) is brandable like the sans and mono ones —
    portfolio totals and statement figures are the private-bank differentiation lever."""
    b = Branding.model_validate({"font": "Inter", "font_display": "Playfair Display"})
    assert b.font_display == "Playfair Display"
    assert Branding().font_display is None  # unset ⇒ --font-display falls back to --font-sans
    # The serif display faces the editor offers all pass the family-name allowlist.
    for family in ("Playfair Display", "Cormorant Garamond", "Fraunces", "Libre Baskerville"):
        assert Branding.model_validate({"font_display": family}).font_display == family


def test_density_clamps_not_rejects() -> None:
    """Density is a builder Segmented value, not injection-shaped (the head builder emits only
    hardcoded numbers for the known values), so junk DROPS to None — never a 422. None ⇒ the
    standard scale."""
    assert Branding().density is None
    for v in ("comfortable", "standard", "compact"):
        assert Branding.model_validate({"density": v}).density == v
    assert Branding.model_validate({"density": "cosy"}).density is None  # junk string
    assert Branding.model_validate({"density": 1.0625}).density is None  # junk type
    assert Branding.model_validate({"density": None}).density is None


def test_finish_clamps_not_rejects() -> None:
    """The finish axis (card | ledger) is a builder Segmented value like density: junk DROPS to
    None — never a 422. None ⇒ the carded default. Only the two known literals are storable, and
    the TS side emits hardcoded token declarations for them, never the stored string."""
    assert Branding().finish is None
    for v in ("card", "ledger"):
        assert Branding.model_validate({"finish": v}).finish == v
    assert Branding.model_validate({"finish": "engraved"}).finish is None  # junk string
    assert Branding.model_validate({"finish": 1}).finish is None  # junk type
    assert Branding.model_validate({"finish": ["ledger"]}).finish is None  # junk shape
    assert Branding.model_validate({"finish": None}).finish is None


def test_finish_round_trips() -> None:
    """A stored ledger finish survives parse → dump → parse (the studio's save path serialises a
    parsed model, so an after-validator or serialiser regression would silently lose the axis)."""
    b = Branding.model_validate({"finish": "ledger", "font_display": "Fraunces"})
    again = Branding.model_validate(json.loads(b.model_dump_json()))
    assert again.finish == "ledger"
    assert again.font_display == "Fraunces"


def test_hero_colour_and_feel() -> None:
    """The hero band is a client-shopfront axis: a plain colour, and a closed set of feels.
    Junk feel drops; an injection-shaped colour is refused."""
    b = Branding.model_validate({"hero_color": "#112233", "hero_feel": "luminous"})
    assert b.hero_color == "#112233"
    assert b.hero_feel == "luminous"
    assert Branding.model_validate({"hero_feel": "sparkle"}).hero_feel is None
    assert Branding.model_validate({"hero_color": ""}).hero_color is None
    with pytest.raises(ValidationError):
        Branding.model_validate({"hero_color": "red; } :root{display:none"})
    template = ClientPortalTemplate.model_validate(
        {"id": "trustees", "name": "Trustees", "hero_color": "#abcdef", "hero_feel": "solid"}
    )
    assert template.hero_color == "#abcdef"
    assert template.hero_feel == "solid"


def test_legacy_theme_without_new_axes_validates_unchanged() -> None:
    """Read-side tolerance: a stored theme written before density/font_display/finish existed
    parses with all unset and round-trips without acquiring junk values."""
    legacy = {"colors": {"light": {"primary": "#123456"}}, "font": "Inter", "mode": "light"}
    b = Branding.model_validate(legacy)
    assert (b.density, b.font_display, b.finish, b.hero_color, b.hero_feel) == (
        None,
        None,
        None,
        None,
        None,
    )
    again = Branding.model_validate(json.loads(b.model_dump_json()))
    assert (again.density, again.font_display, again.finish, again.hero_color, again.hero_feel) == (
        None,
        None,
        None,
        None,
        None,
    )
    assert again.font == "Inter"


def test_glass_controls_clamp_not_reject() -> None:
    """Glass sliders are clamped into range so the editor can never produce a 422."""
    b = Branding.model_validate(
        {"glass": {"enabled": False, "opacity": 5, "blur": 999, "sheen": -10}}
    )
    assert b.glass.enabled is False
    assert b.glass.opacity == 20  # clamped up to the floor
    assert b.glass.blur == 40  # clamped down to the ceiling
    assert b.glass.sheen == 0  # clamped up to the floor
    # Defaults: glass on, everything unset (design-system defaults apply).
    assert Branding().glass.enabled is True
    assert Branding().glass.opacity is None


def test_block_spans_clamp() -> None:
    """Block column/row spans clamp to 1–12 / 1–6; order is list order."""
    b = Branding.model_validate(
        {
            "layout": [
                {"type": "portfolioHeader", "w": 99, "h": 0},
                {"type": "allocation", "w": -3, "h": 9},
            ]
        }
    )
    assert b.layout is not None
    assert (b.layout[0].w, b.layout[0].h) == (12, 1)
    assert (b.layout[1].w, b.layout[1].h) == (1, 6)


@pytest.mark.asyncio
async def test_set_and_get_branding_round_trip(service_session: AsyncSession) -> None:
    tenant = await provision_tenant(
        service_session, name="Brand Co", alias="brand-co", from_model=False
    )
    tid = int(tenant["id"])
    await service_session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
            "VALUES (:t, 'trustee', 'Trustee', 'client')"
        ),
        {"t": tid},
    )
    b = Branding.model_validate(
        {
            "colors": {"light": {"primary": "#123456"}, "dark": {"primary": "#abcdef"}},
            "radius": "1rem",
            "mode": "light",
        }
    )
    await branding.set_branding(
        service_session, tid, portal="client", name="Rebranded Co", branding=b
    )

    result = await branding.get_branding(service_session, tid, "client")
    assert result["name"] == "Rebranded Co"
    stored = result["branding"]
    assert isinstance(stored, dict)
    assert stored["colors"]["light"]["primary"] == "#123456"
    assert stored["colors"]["dark"]["primary"] == "#abcdef"
    assert stored["radius"] == "1rem"
    assert stored["mode"] == "light"
    assert result["client_roles"] == [{"value": "trustee", "label": "Trustee"}]


@pytest.mark.asyncio
async def test_branding_is_per_portal(service_session: AsyncSession) -> None:
    """Editing one portal's branding leaves the other untouched."""
    tenant = await provision_tenant(
        service_session, name="Two Portal Co", alias="two-portal", from_model=False
    )
    tid = int(tenant["id"])
    await branding.set_branding(
        service_session,
        tid,
        portal="client",
        name=None,
        branding=Branding.model_validate({"colors": {"light": {"primary": "#c11e11"}}}),
    )
    await branding.set_branding(
        service_session,
        tid,
        portal="platform",
        name=None,
        branding=Branding.model_validate({"colors": {"light": {"primary": "#0b5ed7"}}}),
    )
    client = await branding.get_branding(service_session, tid, "client")
    platform = await branding.get_branding(service_session, tid, "platform")
    assert client["branding"]["colors"]["light"]["primary"] == "#c11e11"
    assert platform["branding"]["colors"]["light"]["primary"] == "#0b5ed7"


@pytest.mark.asyncio
async def test_legacy_theme_applies_to_both_portals(service_session: AsyncSession) -> None:
    """A pre-per-portal (single-Branding) theme migrates to both portals on read."""
    suffix = uuid.uuid4().hex[:8]
    tid = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, theme) VALUES "
                    "(:uid, :alias, 'Legacy Co', CAST(:theme AS jsonb)) RETURNING id"
                ),
                {
                    "uid": f"ten_{suffix}",
                    "alias": f"legacy-{suffix}",
                    "theme": '{"colors": {"primary": "#abcdef"}}',
                },
            )
        ).scalar_one()
    )
    client = await branding.get_branding(service_session, tid, "client")
    platform = await branding.get_branding(service_session, tid, "platform")
    assert client["branding"]["colors"]["light"]["primary"] == "#abcdef"
    assert platform["branding"]["colors"]["light"]["primary"] == "#abcdef"


@pytest.mark.asyncio
async def test_env_config_by_host_returns_branding(service_session: AsyncSession) -> None:
    """The per-host channel the portals hit every request carries branding + app name."""
    suffix = uuid.uuid4().hex[:8]
    tid = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, theme) VALUES "
                    "(:uid, :alias, 'Host Brand Co', CAST(:theme AS jsonb)) RETURNING id"
                ),
                {
                    "uid": f"ten_{suffix}",
                    "alias": f"host-brand-{suffix}",
                    "theme": '{"colors": {"primary": "#abcdef"}}',
                },
            )
        ).scalar_one()
    )
    host = f"clients.{suffix}.example.com"
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
            "  client_portal_host) VALUES (:t, 'production', 'client_x', :host)"
        ),
        {"t": tid, "host": host},
    )
    cfg = await environments.env_config_by_host(service_session, host)
    assert cfg is not None
    assert cfg["app_name"] == "Host Brand Co"
    # stored in the legacy flat shape → migrated to the light palette on read
    assert cfg["branding"]["colors"]["light"]["primary"] == "#abcdef"


def test_data_uri_asset_accepted() -> None:
    """A data-URI logo/favicon passes branding validation (quick-and-dirty DB storage)."""
    uri = "data:image/png;base64,iVBORw0KGgo="
    b = Branding.model_validate({"logo_url": uri, "favicon_url": uri})
    assert b.logo_url == uri
    assert b.favicon_url == uri


def test_proxy_asset_path_accepted() -> None:
    src = "/_assets/logo-a02d18bf9b52c496.png"
    b = Branding.model_validate({"logo_url": src, "icon_url": src})
    assert b.logo_url == src
    assert branding.parse({"v": 2, "client": {"logo_url": src}}, "client").logo_url == src


def test_parse_lenient_degrades_an_unparseable_theme() -> None:
    assert (
        branding.parse_lenient({"client": {"logo_url": "javascript:alert(1)"}}, "client").logo_url
        is None
    )
    with pytest.raises(ValidationError):
        branding.parse({"client": {"logo_url": "javascript:alert(1)"}}, "client")


# ---- the pages/blocks contract (block-contract.json is the single cross-language pin) ----

# tests/ -> apps/api -> apps -> the repo root.
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "branding"
    / "block-contract.json"
)
_DRIFT = "the Python side drifted from block-contract.json — change them together"


def _contract() -> dict[str, Any]:
    data = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.pkgs_ui_pin
def test_contract_block_types_match() -> None:
    assert branding.CLIENT_BLOCK_TYPES == set(_contract()["blocks"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_variant_allowlists_match() -> None:
    for block_type, spec in _contract()["blocks"].items():
        model = branding.BLOCK_SETTINGS[block_type]
        assert model._variants == set(spec["variants"]), f"{block_type}: {_DRIFT}"


@pytest.mark.pkgs_ui_pin
def test_contract_look_allowlists_match() -> None:
    """The `looks` lists are pinned per type, like variants: a type with `looks` in the contract
    must carry the same allowlist in BLOCK_LOOKS *and* on its settings model; a type without
    (portfolioHeader — deliberately) must have neither a BLOCK_LOOKS entry nor a `look`
    field."""
    contract_blocks = _contract()["blocks"]
    for block_type, spec in contract_blocks.items():
        model = branding.BLOCK_SETTINGS[block_type]
        if "looks" in spec:
            assert branding.BLOCK_LOOKS[block_type] == set(spec["looks"]), f"{block_type}: {_DRIFT}"
            assert model._looks == set(spec["looks"]), f"{block_type}: {_DRIFT}"
            assert "look" in model.model_fields, f"{block_type}: {_DRIFT}"
            assert spec["looks"][0] == "auto", f"{block_type}: every looks list is headed by auto"
        else:
            assert "look" not in model.model_fields, f"{block_type}: {_DRIFT}"
    # No orphaned Python-side entries either: BLOCK_LOOKS covers exactly the contract's set.
    assert set(branding.BLOCK_LOOKS) == {
        t for t, spec in contract_blocks.items() if "looks" in spec
    }, _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_settings_keys_covered() -> None:
    """Every settings key the contract declares exists as a model field (the model may know more —
    fields the portal hasn't surfaced yet — but never less)."""
    for block_type, spec in _contract()["blocks"].items():
        fields = set(branding.BLOCK_SETTINGS[block_type].model_fields)
        assert fields >= set(spec["settings"]), f"{block_type}: {_DRIFT}"


@pytest.mark.pkgs_ui_pin
def test_contract_slugs_icons_and_caps_match() -> None:
    contract = _contract()
    assert branding.RESERVED_SLUGS == set(contract["reservedSlugs"]), _DRIFT
    assert branding.SHADOWABLE_SLUGS == set(contract["shadowableSlugs"]), _DRIFT
    assert branding.PAGE_ICONS == set(contract["pageIcons"]), _DRIFT
    caps = contract["caps"]
    assert (
        branding.MAX_PAGES,
        branding.MAX_BLOCKS_PER_PAGE,
        branding.MAX_TOTAL_BLOCKS,
        branding.MAX_NAV_PAGES,
    ) == (
        caps["maxPages"],
        caps["maxBlocksPerPage"],
        caps["maxTotalBlocks"],
        caps["maxNavPages"],
    ), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_setting_limits_enforced() -> None:
    """Every limit in the contract's settingLimits is enforced behaviourally: numeric ranges clamp
    to both bounds, text limits truncate."""
    for key, limit in _contract()["settingLimits"].items():
        block_type, field = key.split(".")
        model = branding.BLOCK_SETTINGS[block_type]
        if isinstance(limit, list):
            lo, hi = limit
            assert getattr(model.model_validate({field: hi + 100}), field) == hi, f"{key}: {_DRIFT}"
            assert getattr(model.model_validate({field: lo - 100}), field) == lo, f"{key}: {_DRIFT}"
        else:
            over = "x" * (limit + 50)
            assert getattr(model.model_validate({field: over}), field) == "x" * limit, (
                f"{key}: {_DRIFT}"
            )


# ---- pages: migration, degradation (drop/clamp/truncate) and round-trips ----


def test_legacy_layout_migrates_to_home_page() -> None:
    """A pre-pages config carrying only the flat home ``layout`` reads as a single home page, and
    the mirror keeps ``layout`` populated on dump for older portal builds."""
    b = Branding.model_validate({"layout": [{"type": "portfolioHeader"}, {"type": "accounts"}]})
    assert b.pages is not None and len(b.pages) == 1
    home = b.pages[0]
    assert (home.slug, home.icon, home.nav) == ("home", "house", True)
    assert [blk.type for blk in home.layouts.default] == ["portfolioHeader", "accounts"]
    dumped = b.model_dump()
    assert [blk["type"] for blk in dumped["layout"]] == ["portfolioHeader", "accounts"]


def test_layout_mirror_tracks_the_home_page() -> None:
    """Whenever pages exist, ``layout`` reflects the home page's default layout (slug=="home",
    else the first page) — the one-release deploy-skew mirror for pre-pages portal builds."""
    b = Branding.model_validate(
        {
            "pages": [
                {"slug": "extra", "layouts": {"default": [{"type": "message"}]}},
                {"slug": "home", "layouts": {"default": [{"type": "accounts"}]}},
            ]
        }
    )
    assert b.layout is not None
    assert [blk.type for blk in b.layout] == ["accounts"]
    # No page called home ⇒ the first page stands in.
    b2 = Branding.model_validate(
        {"pages": [{"slug": "welcome", "layouts": {"default": [{"type": "adviser"}]}}]}
    )
    assert b2.layout is not None
    assert [blk.type for blk in b2.layout] == ["adviser"]


def test_unknown_settings_keys_survive_round_trip() -> None:
    """A newer portal build may write settings keys this API doesn't know; a save through it must
    clamp what it knows and pass through what it doesn't (extra="ignore" alone would strip them —
    Block._canon_settings merges the canonical dump over the raw dict on purpose)."""
    b = Branding.model_validate(
        {
            "pages": [
                {
                    "slug": "home",
                    "layouts": {
                        "default": [
                            {
                                "type": "accounts",
                                "settings": {
                                    "variant": "cards",
                                    "limit": 999,
                                    "future_flag": True,
                                },
                            }
                        ]
                    },
                }
            ]
        }
    )
    assert b.pages is not None
    settings = b.pages[0].layouts.default[0].settings
    assert settings["future_flag"] is True  # survived
    assert settings["limit"] == 50  # clamped
    assert settings["variant"] == "cards"  # kept
    # …and through the full storage cycle set_branding performs (dump json → parse).
    theme = branding.Theme(client=b)
    again = branding.parse_theme(json.loads(theme.model_dump_json()))
    assert again.client.pages is not None
    stored = again.client.pages[0].layouts.default[0].settings
    assert stored["future_flag"] is True
    assert stored["limit"] == 50


def test_reserved_invalid_and_duplicate_slugs_drop() -> None:
    """A reserved or (post-normalisation) invalid slug drops the page — never a 422, never a
    shadowed functional route — and duplicate slugs keep the first definition."""
    b = Branding.model_validate(
        {
            "pages": [
                {"slug": "login"},  # reserved — a real auth route
                {"slug": "My Page!!"},  # invalid even after lowercase/strip
                {"slug": "  Reports ", "label": "First"},  # normalises to a valid slug
                {"slug": "reports", "label": "Second"},  # duplicate — first wins
            ]
        }
    )
    assert b.pages is not None
    assert [(p.slug, p.label) for p in b.pages] == [("reports", "First")]


def test_page_label_icon_and_id_clamp() -> None:
    """Builder inputs degrade: label truncates (24) and sheds C0 controls, an unknown icon clamps
    to layout-grid, and a malformed id regenerates from the slug."""
    b = Branding.model_validate(
        {
            "pages": [
                {"slug": "notes", "id": "Bad Id!", "label": "A" * 60 + "\x07B", "icon": "sparkles"}
            ]
        }
    )
    assert b.pages is not None
    page = b.pages[0]
    assert page.label == "A" * 24
    assert page.icon == "layout-grid"
    assert page.id == "notes"


def test_page_count_truncates_to_max() -> None:
    b = Branding.model_validate({"pages": [{"slug": f"page-{i}"} for i in range(20)]})
    assert b.pages is not None
    assert len(b.pages) == branding.MAX_PAGES


def test_blocks_per_page_truncate() -> None:
    b = Branding.model_validate(
        {"pages": [{"slug": "wall", "layouts": {"default": [{"type": "message"}] * 30}}]}
    )
    assert b.pages is not None
    assert len(b.pages[0].layouts.default) == branding.MAX_BLOCKS_PER_PAGE


def test_total_block_cap_truncates_trailing_pages() -> None:
    """The set-wide block budget is spent front to back: the front pages keep their blocks and the
    trailing pages' lists are truncated once the budget runs out."""
    pages = [
        {"slug": f"p{i}", "layouts": {"default": [{"type": "message"}] * 24}} for i in range(10)
    ]
    b = Branding.model_validate({"pages": pages})
    assert b.pages is not None
    counts = [len(p.layouts.default) for p in b.pages]
    assert sum(counts) == branding.MAX_TOTAL_BLOCKS
    assert counts == [24] * 8 + [8, 0]


def test_nav_pages_clamp_in_order() -> None:
    """Only MAX_NAV_PAGES may sit in the sidebar; extras keep their page but flip to nav=False,
    order preserved."""
    b = Branding.model_validate({"pages": [{"slug": f"p{i}", "nav": True} for i in range(10)]})
    assert b.pages is not None
    assert [p.nav for p in b.pages] == [True] * branding.MAX_NAV_PAGES + [False] * 4


def test_at_least_one_nav_page_forced() -> None:
    """An all-hidden nav would strand the portal with nowhere to land, so pages[0] is forced in."""
    b = Branding.model_validate(
        {"pages": [{"slug": "a", "nav": False}, {"slug": "b", "nav": False}]}
    )
    assert b.pages is not None
    assert [p.nav for p in b.pages] == [True, False]


def test_block_ids_minted_and_deduped() -> None:
    """Missing/invalid/duplicate block ids re-mint deterministically as {type}, {type}-2, … while
    well-formed unique ids are kept — instance identity survives a round-trip."""
    b = Branding.model_validate(
        {
            "pages": [
                {
                    "slug": "x",
                    "layouts": {
                        "default": [
                            {"type": "message"},  # missing → "message"
                            {"type": "message", "id": "message"},  # duplicate → "message-2"
                            {"type": "message", "id": "Bad Id"},  # invalid → "message-3"
                            {"type": "accounts", "id": "kept-one"},  # valid + unique → kept
                        ]
                    },
                }
            ]
        }
    )
    assert b.pages is not None
    ids = [blk.id for blk in b.pages[0].layouts.default]
    assert ids == ["message", "message-2", "message-3", "kept-one"]
    # Deterministic: the same config parses to the same ids again.
    again = Branding.model_validate(b.model_dump())
    assert again.pages is not None
    assert [blk.id for blk in again.pages[0].layouts.default] == ids


def test_audience_overrides_validate_like_default() -> None:
    """The individual/organisation overrides get the same treatment as default: unknown types
    dropped, ids minted; an unset override stays None (that audience sees default)."""
    b = Branding.model_validate(
        {
            "pages": [
                {
                    "slug": "x",
                    "layouts": {
                        "default": [{"type": "message"}],
                        "individual": [{"type": "message"}, {"type": "totally-made-up"}],
                    },
                }
            ]
        }
    )
    assert b.pages is not None
    layouts = b.pages[0].layouts
    assert layouts.individual is not None
    assert [(blk.type, blk.id) for blk in layouts.individual] == [("message", "message")]
    assert layouts.organisation is None


def test_garbage_page_entries_drop_not_422() -> None:
    """Corrupt entries in pages (non-dicts, a page with no slug) degrade to drops — a bad page
    must never 422 the save or break the portal."""
    b = Branding.model_validate({"pages": ["junk", 42, {"slug": "ok"}, {"label": "no slug"}]})
    assert b.pages is not None
    assert [p.slug for p in b.pages] == ["ok"]


def test_settings_clamp_variant_and_group_by() -> None:
    """Per-type settings clamp, never reject: unknown (even non-string) variants → "auto", junk
    group_by → "none" — the builder's failure mode is silent correction."""
    a = branding.AccountsSettings.model_validate({"variant": "neon", "group_by": "junk"})
    assert a.variant == "auto"
    assert a.group_by == "none"
    assert a.show_sparklines is True
    # The real grouping axes parse; the pre-wiring dead value ('product_type', which nothing
    # ever read) and non-strings clamp to 'none' like any other junk.
    for axis in ("none", "wrapper", "owner"):
        assert branding.AccountsSettings.model_validate({"group_by": axis}).group_by == axis
    assert branding.AccountsSettings.model_validate({"group_by": "product_type"}).group_by == "none"
    assert branding.AccountsSettings.model_validate({"group_by": 7}).group_by == "none"
    off = branding.AccountsSettings.model_validate({"show_sparklines": False})
    assert off.show_sparklines is False
    assert branding.PortfolioHeaderSettings.model_validate({"variant": 7}).variant == "auto"
    assert branding.HoldingsSettings.model_validate({"variant": "table"}).variant == "table"
    # Junk limits fall back to None (the per-type default), not a 422.
    assert branding.ActivitySettings.model_validate({"limit": "lots"}).limit is None


def test_listing_toolbar_settings_default_off_and_clamp() -> None:
    """The listing toolbars (holdings/activity/documents ``toolbar``, documents
    ``show_requests``) default OFF — the compact home blocks must stay quiet, only the default
    listing pages turn them on — and clamp junk to the default like every enum-ish setting (the
    builder must never 422). Only a real JSON boolean flips them: the canonical store dumps real
    booleans, so lax string/number encodings are junk here, not alternatives (NOTE the older
    settings booleans — show_chart/show_sparklines/hide_when_empty — predate the clamp helper
    and still lax-coerce; see ``_clamp_setting_bool``)."""
    assert branding.HoldingsSettings.model_validate({}).toolbar is False
    assert branding.ActivitySettings.model_validate({}).toolbar is False
    assert branding.DocumentsSettings.model_validate({}).toolbar is False
    assert branding.DocumentsSettings.model_validate({}).show_requests is False
    for model in (
        branding.HoldingsSettings,
        branding.ActivitySettings,
        branding.DocumentsSettings,
    ):
        assert model.model_validate({"toolbar": True}).toolbar is True
        assert model.model_validate({"toolbar": False}).toolbar is False
        # Junk — wrong type, junk string, None — clamps to the default, never rejects.
        assert model.model_validate({"toolbar": "banana"}).toolbar is False
        assert model.model_validate({"toolbar": 1}).toolbar is False
        assert model.model_validate({"toolbar": None}).toolbar is False
    assert branding.DocumentsSettings.model_validate({"show_requests": True}).show_requests is True
    assert branding.DocumentsSettings.model_validate({"show_requests": "yes"}).show_requests is (
        False
    )
    # …and the pair round-trips through the full storage cycle alongside variant/look.
    b = Branding.model_validate(
        {
            "pages": [
                {
                    "slug": "docs",
                    "layouts": {
                        "default": [
                            {
                                "type": "documents",
                                "settings": {
                                    "variant": "grouped",
                                    "toolbar": True,
                                    "show_requests": True,
                                },
                            }
                        ]
                    },
                }
            ]
        }
    )
    theme = branding.Theme(client=b)
    again = branding.parse_theme(json.loads(theme.model_dump_json()))
    assert again.client.pages is not None
    stored = again.client.pages[0].layouts.default[0].settings
    assert stored["toolbar"] is True
    assert stored["show_requests"] is True
    assert stored["variant"] == "grouped"


def test_look_clamps_not_rejects() -> None:
    """The per-block `look` (the block's VISUAL design, orthogonal to `variant`, its data shape)
    clamps exactly like `variant`: every allowlisted value parses, junk — wrong value, wrong type,
    None — clamps to "auto" (which follows the portal `finish`), and absent defaults to "auto".
    The builder must never 422."""
    for block_type, allowed in branding.BLOCK_LOOKS.items():
        model = branding.BLOCK_SETTINGS[block_type]
        assert model.model_validate({}).look == "auto", block_type  # absent ⇒ auto
        for look in allowed:
            assert model.model_validate({"look": look}).look == look, block_type
        assert model.model_validate({"look": "holographic"}).look == "auto", block_type
        assert model.model_validate({"look": 7}).look == "auto", block_type
        assert model.model_validate({"look": None}).look == "auto", block_type
        assert model.model_validate({"look": ["card"]}).look == "auto", block_type
    # The allowlist is per-class, not global: a look valid for one type is junk for another.
    assert branding.ActivitySettings.model_validate({"look": "timeline"}).look == "timeline"
    assert branding.AccountsSettings.model_validate({"look": "timeline"}).look == "auto"
    assert branding.AllocationSettings.model_validate({"look": "orbit"}).look == "orbit"
    assert branding.OnboardingProgressSettings.model_validate({"look": "card"}).look == "auto"


def test_look_round_trips_through_theme() -> None:
    """A set look survives the full storage cycle set_branding performs (Theme dump json → parse),
    alongside the variant it is orthogonal to."""
    b = Branding.model_validate(
        {
            "pages": [
                {
                    "slug": "home",
                    "layouts": {
                        "default": [
                            {"type": "accounts", "settings": {"look": "ink"}},
                            {
                                "type": "allocation",
                                "settings": {"variant": "bars", "look": "spectrum"},
                            },
                        ]
                    },
                }
            ]
        }
    )
    theme = branding.Theme(client=b)
    again = branding.parse_theme(json.loads(theme.model_dump_json()))
    assert again.client.pages is not None
    blocks = again.client.pages[0].layouts.default
    assert blocks[0].settings["look"] == "ink"
    assert blocks[1].settings["look"] == "spectrum"
    assert blocks[1].settings["variant"] == "bars"


def test_portfolio_header_takes_no_look() -> None:
    """portfolioHeader deliberately carries no look axis. Its settings model uses
    ``extra="ignore"``, so a stray `look` key is IGNORED (dropped from the parsed model, no field,
    never a 422 — rejection would need ``extra="forbid"``, which would break the builder's
    clamp-never-reject contract). Through ``Block._canon_settings``' merge the raw key still
    survives the stored settings dict verbatim, like any unknown future key — but it never becomes
    a validated field."""
    model = branding.PortfolioHeaderSettings
    assert "look" not in model.model_fields
    parsed = model.model_validate({"look": "card"})  # ignored, not rejected
    assert not hasattr(parsed, "look")
    blk = branding.Block(type="portfolioHeader", settings={"look": "card"})
    assert blk.settings["look"] == "card"  # survives verbatim as an unknown key


def test_news_takes_a_look() -> None:
    """news gained the look axis (Oct 2026): card · magazine · journal, clamped like every other
    looked block — a stale value falls to auto, never a 422 — and the default Insights page pins
    `magazine` so the firm's library leads with its own pictures and palette."""
    news = branding.NewsSettings
    assert news._looks == frozenset({"auto", "card", "magazine", "journal"})
    assert news.model_validate({"look": "magazine"}).look == "magazine"
    assert news.model_validate({"look": "shelf"}).look == "auto"
    insights = next(p for p in branding.default_pages() if p.slug == "insights")
    blk = insights.layouts.default[0]
    assert blk.type == "news"
    assert blk.settings["variant"] == "library"
    assert blk.settings["look"] == "magazine"


def test_portfolio_header_variants_include_canvas_and_ledger() -> None:
    """The contract delta: portfolioHeader gained the canvas + ledger presentations. Both parse;
    a stale/unknown variant still clamps to auto."""
    ph = branding.PortfolioHeaderSettings
    assert ph._variants == frozenset({"auto", "hero", "canvas", "ledger", "compact"})
    assert ph.model_validate({"variant": "canvas"}).variant == "canvas"
    assert ph.model_validate({"variant": "ledger"}).variant == "ledger"
    assert ph.model_validate({"variant": "holographic"}).variant == "auto"


def test_portfolio_header_actions_and_period_clamp() -> None:
    """`actions` and `default_period` are builder inputs, so they clamp to their defaults on junk
    (the builder must never 422) and accept every allowlisted value."""
    ph = branding.PortfolioHeaderSettings
    # Defaults.
    assert ph.model_validate({}).actions == "deposit_withdraw"
    assert ph.model_validate({}).default_period == "6M"
    # Every allowlisted value parses.
    for action in ("deposit_withdraw", "contact", "none"):
        assert ph.model_validate({"actions": action}).actions == action
    for period in ("1M", "3M", "6M", "1Y"):
        assert ph.model_validate({"default_period": period}).default_period == period
    # Junk — wrong value or wrong type — clamps to the default, never rejects.
    assert ph.model_validate({"actions": "buy-crypto"}).actions == "deposit_withdraw"
    assert ph.model_validate({"actions": 7}).actions == "deposit_withdraw"
    assert ph.model_validate({"default_period": "5Y"}).default_period == "6M"
    assert ph.model_validate({"default_period": None}).default_period == "6M"


def test_message_text_truncates_and_strips_control_chars() -> None:
    """Message copy is plain text: C0 controls are stripped and title/body truncate to the
    contract's limits. Escaping stays the renderer's job — this is not the XSS defence."""
    s = branding.MessageSettings.model_validate({"title": "Hi\x00 there\x1b!", "body": "b" * 2000})
    assert s.title == "Hi there!"
    assert len(s.body) == 1000
    assert branding.MessageSettings.model_validate({"title": 42}).title == ""


#: The contract's audience keys on a ``defaultPages`` entry, paired with the ``PageLayouts`` field
#: each one feeds. ``blocks`` IS the default layout; the other two are optional overrides.
_AUDIENCE_KEYS = (
    ("blocks", "default"),
    ("individual", "individual"),
    ("organisation", "organisation"),
)


@pytest.mark.pkgs_ui_pin
def test_default_pages_pin_to_the_contract() -> None:
    """``default_pages()`` must equal the contract's ``defaultPages`` — slug, label, icon, and
    every block's id/type/span/visibility AND its FULL settings object, in order, for EVERY
    audience layout the entry declares.

    This is not belt-and-braces: the Python and TS ``defaultPages`` are independent literals (the
    API container ships without pkgs/ui, so Python cannot read the contract at runtime), and the
    first drafts of the two shipped DIFFERENT spans for the same blocks. The contract entry is the
    referee; the TS registry test pins its side to the same section.

    The settings comparison is deliberately the whole canonical dump, not just the variant: the
    contract's per-block ``settings`` object (minimal — empty entries omitted) is validated
    through the type's settings model, and the block must carry exactly that dump. A richer
    default (holdings toolbar on, the documents requests banner) that only one side shipped
    fails here instead of shipping two different "defaults".
    """
    spec = _contract()["defaultPages"]
    pages = branding.default_pages()
    assert [p.slug for p in pages] == [s["slug"] for s in spec], _DRIFT
    assert [p.label for p in pages] == [s["label"] for s in spec], _DRIFT
    assert [p.icon for p in pages] == [s["icon"] for s in spec], _DRIFT
    assert [p.id for p in pages] == [p.slug for p in pages]
    assert all(p.nav for p in pages)
    for page, page_spec in zip(pages, spec, strict=True):
        for spec_key, field in _AUDIENCE_KEYS:
            blocks: list[branding.Block] | None = getattr(page.layouts, field)
            if spec_key not in page_spec:
                # An entry that declares no override must not carry one on this side either:
                # an audience fork only one side ships is exactly the drift the pin exists for.
                assert blocks is None, f"{_DRIFT} (page {page.slug!r}, unexpected {field} override)"
                continue
            assert blocks is not None, f"{_DRIFT} (page {page.slug!r}, missing {field} layout)"
            for blk, s in zip(blocks, page_spec[spec_key], strict=True):
                # A contract block's id is optional and defaults to the type name — spelled out
                # only where a page holds two blocks of one type.
                assert (blk.id, blk.type, blk.w, blk.h, blk.visible) == (
                    s.get("id", s["type"]),
                    s["type"],
                    s["w"],
                    s["h"],
                    s["visible"],
                ), f"{_DRIFT} (page {page.slug!r}, {field})"
                # Block._canon_settings stores the full canonical dump, so the expectation is the
                # contract's (minimal) settings object validated through the same model.
                model = branding.BLOCK_SETTINGS[blk.type]
                want = model.model_validate(s.get("settings", {})).model_dump()
                assert blk.settings == want, (
                    f"{_DRIFT} (page {page.slug!r}, {field}, block {blk.id!r})"
                )
    # The rework's headline defaults, pinned explicitly so a contract edit that dropped them
    # would still read as a deliberate decision here: the listing pages ship their toolbars on
    # (and documents its requests banner); the home page's compact blocks stay toolbar-less.
    by_slug = {p.slug: p.layouts.default for p in pages}
    # No accounts or holdings PAGE: the phone's tab bar is capped at five, and the accounts are
    # home's deck. Portfolio holds the whole book, grouped under the donut's asset classes.
    assert "holdings" not in by_slug
    assert "accounts" not in by_slug
    portfolio = [blk.type for blk in by_slug["portfolio"]]
    assert portfolio == ["portfolioHeader", "allocation", "holdings"]
    assert by_slug["portfolio"][2].settings["variant"] == "grouped"
    assert by_slug["activity"][0].settings["toolbar"] is True
    assert by_slug["documents"][0].settings["toolbar"] is True
    assert by_slug["documents"][0].settings["show_requests"] is True
    for blk in by_slug["home"]:
        assert blk.settings.get("toolbar") is not True, f"home {blk.type} must stay toolbar-less"
    home = pages[0]
    assert [blk.id for blk in home.layouts.default] == [blk.type for blk in home.layouts.default]
    # Every default slug but Portfolio is shadowable (a static route renders its page); Portfolio
    # has no static route and is served by the composed `[slug]` route like any firm page.
    assert {p.slug for p in pages} - {"portfolio"} <= branding.SHADOWABLE_SLUGS
    # The set survives its own validation untouched (defaults are already canonical).
    b = Branding.model_validate({"pages": [p.model_dump() for p in pages]})
    assert b.pages is not None
    assert [p.slug for p in b.pages] == [p.slug for p in pages]
    assert b.pages[0].layouts.default[0].id == "onboardingProgress"
    # Ids survive the round-trip on an override that holds two blocks of one type: a re-mint here
    # would mean the roll-up and the table had swapped identities behind the studio's back.
    accounts = next(p for p in b.pages if p.slug == "portfolio")
    assert accounts.layouts.organisation is not None
    assert [blk.id for blk in accounts.layouts.organisation] == [
        "accounts-2",
        "accounts",
        "allocation",
        "holdings",
    ]


@pytest.mark.pkgs_ui_pin
def test_default_pages_only_name_contract_members() -> None:
    """Every block type, variant, look and settings key the defaults name must be a member of the
    contract's list for that type, and every span/limit inside its bounds.

    The pin above proves the two literals AGREE; this proves what they agree on is legal. Without
    it a contract edit that dropped a look could leave both sides pointing at it — the API would
    clamp the value to 'auto' on the way in and the default would quietly stop doing what it says.
    """
    contract = _contract()
    blocks_spec = contract["blocks"]
    limits = contract["settingLimits"]
    for page in branding.default_pages():
        for _spec_key, field in _AUDIENCE_KEYS:
            blocks: list[branding.Block] | None = getattr(page.layouts, field)
            if blocks is None:
                continue
            ids = [blk.id for blk in blocks]
            assert len(set(ids)) == len(ids), f"{page.slug} {field}: duplicate block ids"
            assert len(blocks) <= branding.MAX_BLOCKS_PER_PAGE
            for blk in blocks:
                spec = blocks_spec.get(blk.type)
                assert spec is not None, f"{page.slug} {field}: unknown block type {blk.type!r}"
                assert blk.w is not None and 1 <= blk.w <= 12
                assert blk.h is not None and 1 <= blk.h <= 6
                # The canonical dump carries every field of the type's model, so compare only the
                # keys the contract publishes as settable — and only where the default DIFFERS
                # from the model default, which is what the literals actually assert.
                model = branding.BLOCK_SETTINGS[blk.type]
                untouched = model().model_dump()
                for key, value in blk.settings.items():
                    if untouched.get(key) == value:
                        continue
                    assert key in spec["settings"], f"{page.slug} {field} {blk.type}.{key}"
                    if key == "variant":
                        assert value in spec["variants"], f"{page.slug} {field} {blk.type}: {value}"
                    if key == "look":
                        assert value in spec.get("looks", []), (
                            f"{page.slug} {field} {blk.type}: {value}"
                        )
                    bound = limits.get(f"{blk.type}.{key}")
                    if isinstance(bound, list):
                        assert bound[0] <= value <= bound[1], (
                            f"{page.slug} {field} {blk.type}.{key}"
                        )
    # And the whole set stays inside the structural ceilings.
    total = sum(
        len(getattr(p.layouts, field) or [])
        for p in branding.default_pages()
        for _k, field in _AUDIENCE_KEYS
    )
    assert total <= branding.MAX_TOTAL_BLOCKS
    assert len(branding.default_pages()) <= branding.MAX_PAGES
    assert sum(p.nav for p in branding.default_pages()) <= branding.MAX_NAV_PAGES


def test_client_portal_templates_keep_many_roles_but_one_template_per_role() -> None:
    parsed = Branding.model_validate(
        {
            "default_template_id": "missing",
            "templates": [
                {
                    "id": "standard",
                    "name": "Standard clients",
                    "role_slugs": ["client", "joint-client", "client"],
                    "colors": {"light": {"primary": "#123456"}},
                    "pages": [
                        {
                            "id": "home",
                            "slug": "home",
                            "label": "Home",
                            "layouts": {"default": [{"type": "accounts"}]},
                        }
                    ],
                },
                {
                    "id": "trusts",
                    "name": "Trusts",
                    "role_slugs": ["joint-client", "trustee"],
                    "finish": "ledger",
                },
            ],
        }
    )

    assert parsed.templates is not None
    assert parsed.default_template_id == "standard"
    assert parsed.templates[0].role_slugs == ["client", "joint-client"]
    assert parsed.templates[1].role_slugs == ["trustee"]
    assert parsed.templates[0].classification_slugs == []
    assert parsed.templates[1].classification_slugs == []
    assert parsed.templates[0].pages is not None
    assert parsed.templates[0].pages[0].layouts.default[0].id == "accounts"


def test_template_role_assignment_and_pages_are_composition_changes() -> None:
    current = Branding.model_validate(
        {
            "templates": [
                {"id": "standard", "name": "Standard", "role_slugs": ["client"], "pages": None}
            ]
        }
    )
    recoloured = current.model_copy(deep=True)
    assert recoloured.templates is not None
    recoloured.templates[0].colors.light.primary = "#123456"
    assert branding.pages_changed(current, recoloured) is False

    reassigned = current.model_copy(deep=True)
    assert reassigned.templates is not None
    reassigned.templates[0].role_slugs = ["trustee"]
    assert branding.pages_changed(current, reassigned) is True


def test_client_portal_templates_keep_one_template_per_classification() -> None:
    parsed = Branding.model_validate(
        {
            "default_template_id": "advised",
            "templates": [
                {
                    "id": "advised",
                    "name": "Advised",
                    "classification_slugs": ["advised", "d2c", "advised"],
                },
                {
                    "id": "d2c",
                    "name": "D2C",
                    "classification_slugs": ["d2c", "execution-only"],
                },
            ],
        }
    )
    assert parsed.templates is not None
    assert parsed.templates[0].classification_slugs == ["advised", "d2c"]
    assert parsed.templates[1].classification_slugs == ["execution-only"]


def test_client_portal_templates_keep_one_template_per_group() -> None:
    parsed = Branding.model_validate(
        {
            "templates": [
                {"id": "workplace", "name": "Workplace", "group_slugs": ["workplace", "charity"]},
                {"id": "charity", "name": "Charity", "group_slugs": ["charity", "workplace"]},
            ]
        }
    )
    assert parsed.templates is not None
    assert parsed.templates[0].group_slugs == ["workplace", "charity"]
    assert parsed.templates[1].group_slugs == []


def test_assigning_a_group_to_a_design_is_a_composition_change() -> None:
    current = Branding.model_validate(
        {"templates": [{"id": "standard", "name": "Standard", "group_slugs": []}]}
    )
    assigned = current.model_copy(deep=True)
    assert assigned.templates is not None
    assigned.templates[0].group_slugs = ["workplace"]
    assert branding.pages_changed(current, assigned) is True


def test_card_designs_parse_on_branding_and_templates() -> None:
    """A product type maps to a finish, or to artwork plus the ink that reads on it."""
    b = Branding.model_validate(
        {
            "card_designs": {
                "ISA": {"finish": "brand"},
                "SIPP": {"art_url": "/_assets/card-art-0123456789abcdef.png", "ink": "light"},
            },
            "templates": [{"id": "hnw", "card_designs": {"GIA": {"finish": "paper"}}}],
        }
    )
    assert b.card_designs is not None
    assert b.card_designs["ISA"].finish == "brand"
    coloured = Branding.model_validate(
        {"card_designs": {"ISA": {"finish": "ink", "color": "#112233"}}}
    )
    assert coloured.card_designs is not None
    assert coloured.card_designs["ISA"].color == "#112233"
    cleared = Branding.model_validate({"card_designs": {"ISA": {"color": ""}}})
    assert cleared.card_designs is not None
    assert cleared.card_designs["ISA"].color is None
    assert b.card_designs["SIPP"].art_url == "/_assets/card-art-0123456789abcdef.png"
    assert b.card_designs["SIPP"].ink == "light"
    assert b.templates is not None
    assert b.templates[0].card_designs is not None
    assert b.templates[0].card_designs["GIA"].finish == "paper"


def test_card_designs_clamp_builder_junk() -> None:
    """Builder-shaped values degrade rather than 422: unknown finish/ink ⇒ unset, blank keys and
    non-object entries dropped, the map capped."""
    many = {f"T{i}": {"finish": "ink"} for i in range(40)}
    b = Branding.model_validate(
        {"card_designs": {"ISA": {"finish": "rainbow", "ink": "grey"}, "": {}, "GIA": 3, **many}}
    )
    assert b.card_designs is not None
    assert b.card_designs["ISA"].finish is None
    assert b.card_designs["ISA"].ink is None
    assert "" not in b.card_designs and "GIA" not in b.card_designs
    assert len(b.card_designs) == branding.MAX_CARD_DESIGNS
    assert Branding.model_validate({"card_designs": "nope"}).card_designs is None


@pytest.mark.parametrize(
    "color", ["#112233;color:red", "red; } :root{display:none", "expression(1)"]
)
def test_card_colour_is_a_plain_colour(color: str) -> None:
    """A card colour lands in an inline background-color, so it is injection-shaped and rejected."""
    with pytest.raises(ValidationError):
        Branding.model_validate({"card_designs": {"ISA": {"color": color}}})


@pytest.mark.parametrize(
    "art", ["javascript:alert(1)", "url(https://evil)", "https://x/a.png'); } body{display:none"]
)
def test_card_art_is_an_allowlisted_url(art: str) -> None:
    """Artwork lands in an inline background-image, so it is injection-shaped and rejected."""
    with pytest.raises(ValidationError):
        Branding.model_validate({"card_designs": {"ISA": {"art_url": art}}})
