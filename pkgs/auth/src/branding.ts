/**
 * Per-tenant white-label branding — shared type + the SSR `<head>` builder.
 *
 * The API validates branding on write; this builder re-validates every value before it goes into an
 * SSR `<style>`/`<head>` (defence in depth) and HTML-escapes anything placed in markup, so a tenant
 * can never inject CSS or HTML. The whole design system keys off a handful of CSS custom properties
 * (see pkgs/ui theme.css), so overriding those re-themes everything. Framework-agnostic.
 */

/** One scheme's BASIC PALETTE — fourteen tokens (mirrors app/branding.py BrandingColors). Every
 * other colour is an alias or derivation of these in pkgs/ui theme.css (`--popover` is `--card`,
 * `--ring` is `--brand`, `--success` is `--positive`…). Every field optional; unset ⇒ the design
 * system's own value. */
export interface BrandingColors {
	// neutrals
	background?: string | null;
	foreground?: string | null;
	card?: string | null;
	muted?: string | null;
	muted_foreground?: string | null;
	border?: string | null;
	// identity
	primary?: string | null;
	primary_foreground?: string | null;
	brand?: string | null;
	brand_foreground?: string | null;
	// status
	positive?: string | null;
	negative?: string | null;
	warning?: string | null;
	info?: string | null;
}

/** The account-card finishes (mirrors app/branding.py CARD_FINISHES). Recipes, not colours: each
 * is built off the firm's `--brand` or the hero ink by pkgs/ui `categorical.ts::finish`. */
export const CARD_FINISHES = ['ink', 'brand', 'deep', 'tint', 'paper'] as const;
export type CardFinish = (typeof CARD_FINISHES)[number];

/** How one product type's account card is painted (mirrors app/branding.py CardDesign): a
 * finish, a `color` that replaces that finish, or uploaded artwork plus the ink that reads on
 * it. The finish still fills under art, and under a colour when the colour is cleared. */
export interface CardDesign {
	finish?: CardFinish | null;
	/** A plain CSS colour that replaces the finish recipe. Unset ⇒ the finish paints the card. */
	color?: string | null;
	art_url?: string | null;
	ink?: 'light' | 'dark' | null;
}

/** Frosted-glass material controls (mirrors app/branding.py BrandingGlass). `enabled: false` ⇒ solid
 * surfaces. `opacity` = fill % (higher = more opaque), `blur` = backdrop blur px, `sheen` = gloss %. */
export interface BrandingGlass {
	enabled?: boolean;
	opacity?: number | null;
	blur?: number | null;
	sheen?: number | null;
}

/** One arrangeable section of a client-portal page (the visual grid layout builder). `w`/`h` are
 * the column/row span (1–12 / 1–6) on the 12-column responsive grid. `id` is the studio-minted
 * identifier, unique within a page (see pkgs/ui `mintBlockId`) — pre-pages layouts carry none. */
export interface BrandingBlock {
	id?: string;
	type: string;
	visible?: boolean;
	w?: number;
	h?: number;
	settings?: Record<string, unknown>;
}

/** Per-audience block layouts for one page. `default` always exists; `individual`/`organisation`
 * override it for that audience when set. Null/absent ⇒ the default applies; an EMPTY ARRAY is an
 * explicit "show this audience nothing" and is honoured (see pkgs/ui `resolveAudienceLayout`). */
export interface BrandingPageLayouts {
	default: BrandingBlock[];
	individual?: BrandingBlock[] | null;
	organisation?: BrandingBlock[] | null;
}

/** One client-portal page. `slug` drives the route (`/{slug}`; `home` ⇒ `/`) and must avoid the
 * contract's reserved slugs; `icon` names one of the curated lucide icons (pkgs/ui `PAGE_ICONS`);
 * `nav` puts the page in the tab bar (absent ⇒ shown). Mirrors app/branding.py Page — the
 * allowlists/caps both sides validate against live in pkgs/ui block-contract.json. */
export interface BrandingPage {
	id: string;
	slug: string;
	label: string;
	icon?: string;
	nav?: boolean;
	layouts: BrandingPageLayouts;
}

/**
 * One named client-portal design set. Firm identity (logos, favicon, login copy and support
 * details) stays on the parent Branding; the template owns the client's visual treatment and
 * page composition. A role may appear on at most one template after API canonicalisation.
 */
export interface ClientPortalTemplate {
	id: string;
	name: string;
	role_slugs?: string[];
	/** Book-level client classifications. Preferred over ``role_slugs`` when both are set. */
	classification_slugs?: string[];
	/**
	 * Firm-assigned client groups (a business stream). Checked before the book: the first
	 * of the client's groups, in catalogue order, that appears here wins.
	 */
	group_slugs?: string[];
	colors?: { light?: BrandingColors; dark?: BrandingColors };
	radius?: string | null;
	font?: string | null;
	font_mono?: string | null;
	font_display?: string | null;
	mode?: 'auto' | 'light' | 'dark';
	density?: 'comfortable' | 'standard' | 'compact' | null;
	finish?: 'card' | 'ledger' | null;
	/** Hero band colour. Unset ⇒ a deep shade of the brand (`--hero-base` derived from `--primary`),
	 * so the band cannot be a second copy of the buttons. A set colour is used as-is. */
	hero_color?: string | null;
	/** How the hero band is painted. Unset/`gradient` is the ink ramp. `solid` is flat.
	 * `luminous` pulls the brand colour through the band. */
	hero_feel?: 'gradient' | 'solid' | 'luminous' | null;
	glass?: BrandingGlass;
	card_designs?: Record<string, CardDesign> | null;
	pages?: BrandingPage[] | null;
}

export interface Branding {
	v?: number;
	/** Separate palettes per colour scheme. `dark` unset ⇒ the design system's own dark theme
	 * applies in dark mode (only the tokens a firm sets are overridden). */
	colors?: { light?: BrandingColors; dark?: BrandingColors };
	radius?: string | null;
	/** UI typeface — drives `--font-sans` (body copy, headings, every `font-sans` surface). */
	font?: string | null;
	/** Monospace typeface — drives `--font-mono` (code, tabular figures, reference/ID columns).
	 * Unset ⇒ the platform default mono stack. */
	font_mono?: string | null;
	/** Display typeface — drives `--font-display` (large numerals: portfolio totals, statement
	 * figures — the private-bank lever). Unset ⇒ `--font-display` keeps its theme.css default,
	 * `var(--font-sans)`, so display surfaces simply follow the body face. */
	font_display?: string | null;
	mode?: 'auto' | 'light' | 'dark';
	/** Type/spacing density — demographic adaptivity as one token. The design system is rem-based,
	 * so a single root `font-size` scales type, spacing and touch targets coherently: `comfortable`
	 * = 17px root (the 65+ mode — bigger text, bigger targets), `compact` = 15px (the
	 * professional/data-dense mode). Null/absent ⇒ standard (16px, nothing emitted). A `--density`
	 * scale factor (1.0625 | 1 | 0.9375) is also exposed for components wanting finer control. */
	density?: 'comfortable' | 'standard' | 'compact' | null;
	/** Hero band colour. Unset ⇒ a deep shade of `--primary` (see ClientPortalTemplate). */
	hero_color?: string | null;
	/** Hero band treatment. Unset ⇒ the ink gradient. */
	hero_feel?: 'gradient' | 'solid' | 'luminous' | null;
	/** Portal-wide surface finish — one axis that re-skins every widget coherently (they all render
	 * through Card.* + design tokens). `card` (or null/absent) is today's carded look and emits
	 * nothing. `ledger` is the private-bank register: it re-points the surface tokens theme.css
	 * declares (transparent sections, no side borders, a bottom hairline rule, square corners,
	 * small-caps card titles, display-face numerals) and suppresses the frosted-glass material —
	 * a ledger has no glass. Emitted only on `surface: 'themed'`: like the glass knobs, finish is
	 * client-portal aesthetics and staff tools keep the product look. Only hardcoded declarations
	 * from the internal table are ever emitted — no injection surface. */
	finish?: 'card' | 'ledger' | null;
	logo_url?: string | null;
	logo_dark_url?: string | null;
	/** Icon/symbol image — used when `logo_variant` is `icon` (falls back to the logo). */
	icon_url?: string | null;
	/** Top-left logo treatment: `icon` = mark only; `wordmark` = full lockup, no appended name. */
	logo_variant?: 'icon' | 'wordmark';
	favicon_url?: string | null;
	login?: { tagline?: string | null; legal?: string | null };
	support?: { email?: string | null; phone?: string | null; website?: string | null };
	/** Frosted-glass material controls (shared across schemes). */
	glass?: BrandingGlass;
	/** Product type (`ISA`, `SIPP`…) → how its account card is painted. Client branding only.
	 * Unset ⇒ pkgs/ui `DEFAULT_CARD_DESIGNS`. */
	card_designs?: Record<string, CardDesign> | null;
	/** Client-portal home layout (ordered/visible/sized blocks). Client branding only.
	 * LEGACY MIRROR: superseded by `pages` — the API keeps it equal to the home page's default
	 * layout for one release so un-migrated readers keep working; new consumers read `pages`. */
	layout?: BrandingBlock[] | null;
	/** Client-portal pages (nav + per-audience block layouts). Client branding only.
	 * Null/absent ⇒ the default page set (pkgs/ui `defaultPages()` / app/branding.py
	 * `default_pages()` — the two must mirror each other). */
	pages?: BrandingPage[] | null;
	/** Named client-portal design sets. An empty/absent list preserves the legacy single-design
	 * behaviour. A classification slug (the book fact) selects a template; `role_slugs` remain
	 * as a fallback for firms that have not moved off auth-profile roles. `default_template_id`
	 * is used when neither matches. */
	templates?: ClientPortalTemplate[] | null;
	default_template_id?: string | null;
}

const TEMPLATE_APPEARANCE_KEYS = [
	'colors',
	'radius',
	'font',
	'font_mono',
	'font_display',
	'mode',
	'density',
	'finish',
	'hero_color',
	'hero_feel',
	'glass',
	'card_designs',
	'pages'
] as const satisfies readonly (keyof ClientPortalTemplate)[];

/**
 * Resolve the authenticated client's effective branding. Templates never replace firm identity:
 * logos, favicon, login/support copy and the firm name remain shared. Within the appearance,
 * absence means the design-system default rather than inheritance from the legacy design, so two
 * templates cannot accidentally bleed into each other. Pages are the exception: a design set
 * without its own page list keeps the parent composition, because deleting then copying `null`
 * hid `Branding.pages` and sent every client back to the platform defaults.
 */
export type PortalTemplateSelector =
	| string
	| {
			/** The client's groups, already in catalogue order (first assigned design wins). */
			groups?: readonly string[] | null;
			classification?: string | null;
			role?: string | null;
	  }
	| null
	| undefined;

function selectorParts(selector: PortalTemplateSelector): {
	groups: string[];
	classification: string | null;
	role: string | null;
} {
	if (typeof selector === 'string') return { groups: [], classification: null, role: selector };
	const groups = (selector?.groups ?? []).filter(
		(slug): slug is string => typeof slug === 'string' && slug.length > 0
	);
	return {
		groups,
		classification: selector?.classification ?? null,
		role: selector?.role ?? null
	};
}

export function resolveClientPortalTemplate(
	branding: Branding | null | undefined,
	selector?: PortalTemplateSelector
): Branding | null {
	if (!branding) return null;
	const templates = branding.templates?.filter(Boolean) ?? [];
	if (templates.length === 0) return branding;

	const { groups, classification, role } = selectorParts(selector);
	const byGroup = groups
		.map((slug) => templates.find((template) => template.group_slugs?.includes(slug)))
		.find((template) => template !== undefined);
	const selected =
		byGroup ??
		(classification
			? templates.find((template) => template.classification_slugs?.includes(classification))
			: undefined) ??
		(role ? templates.find((template) => template.role_slugs?.includes(role)) : undefined) ??
		templates.find((template) => template.id === branding.default_template_id);
	if (!selected) return branding;

	const effective: Branding = { ...branding };
	for (const key of TEMPLATE_APPEARANCE_KEYS) {
		if (key === 'pages') {
			const templatePages = selected.pages;
			if (templatePages && templatePages.length > 0) effective.pages = templatePages;
			continue;
		}
		delete (effective as Record<string, unknown>)[key];
		if (key in selected) {
			(effective as Record<string, unknown>)[key] = selected[key];
		}
	}
	// The flat layout is only a deploy-skew mirror. Derive it from this template's effective pages
	// instead of leaking the parent design's home into an older reader.
	delete effective.layout;
	const pages = effective.pages;
	const home = pages?.find((page) => page.slug === 'home') ?? pages?.[0];
	if (home) effective.layout = home.layouts.default;
	return effective;
}

// Mirror of the API's validators (app/branding.py) — the injection-side gate.
const COLOUR =
	/^(#[0-9a-fA-F]{3,8}|(?:oklch|oklab|rgb|rgba|hsl|hsla|color-mix)\([0-9a-zA-Z%.,/()\s+-]{1,120}\)|[a-zA-Z]{1,24})$/;
const RADIUS = /^\d{1,3}(?:\.\d+)?(?:px|rem|em)$/;
const FONT = /^[A-Za-z0-9 -]{1,48}$/;

/**
 * Is this a family name the portal will accept and load? Letters, digits, spaces and hyphens,
 * up to 48 — the same rule `brandingHead` and the API apply, exposed so the studio's free-text
 * font box refuses exactly what the save would. A name that passes is fetched from Google Fonts
 * by `googleFontHref`; a family Google does not carry simply falls back to the stack.
 */
export function isFontFamily(font: string | null | undefined): font is string {
	return typeof font === 'string' && FONT.test(font);
}
const HTTPS = /^https:\/\/[^\s"'<>]{1,2048}$/;
const PROXY_ASSET = /^\/_assets\/[A-Za-z0-9-]+-[0-9a-f]{16}\.(?:svg|png|jpg|webp|ico)$/;
// Inline base64 image (quick-and-dirty storage: the asset lives in the branding record / DB).
const DATA_IMG =
	/^data:image\/(?:png|jpeg|svg\+xml|webp|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=]{1,1400000}$/;

// Branding colour key -> the CSS custom property it drives (1:1; must match app/branding.py COLOUR_VARS).
const COLOUR_VARS: Record<string, string[]> = {
	// neutrals
	background: ['--background'],
	foreground: ['--foreground'],
	card: ['--card'],
	muted: ['--muted'],
	muted_foreground: ['--muted-foreground'],
	border: ['--border'],
	// identity
	primary: ['--primary'],
	primary_foreground: ['--primary-foreground'],
	brand: ['--brand'],
	brand_foreground: ['--brand-foreground'],
	// status — each is also the BASE of a derived `-subtle`/`-strong`/`-line` trio in theme.css
	positive: ['--positive'],
	negative: ['--negative'],
	warning: ['--warning'],
	info: ['--info']
};

/** Whether a card artwork source is safe to put in an inline `background-image: url(…)` — the
 * same allowlist as every other branding asset. */
export function isSafeCardArt(src: string | null | undefined): src is string {
	return !!src && (HTTPS.test(src) || PROXY_ASSET.test(src) || DATA_IMG.test(src));
}

/** Whether a card colour is safe to put in an inline `background-color` — the same allowlist as
 * every other branding colour, so a hex (or an oklch) cannot break out of the declaration. */
export function isCardColor(color: string | null | undefined): color is string {
	return !!color && COLOUR.test(color);
}

/** The colour token keys (one per `BrandingColors` field). */
export const COLOUR_VARS_KEYS = Object.keys(COLOUR_VARS);

/**
 * Tokens a **fixed** (staff) surface may emit. Buttons, the active nav mark, focus rings (which
 * alias `--brand`) and in-hero accents. Everything else — paper, cards, sidebar fill, muted wells, status hues,
 * type, density, radius, glass — is the product look, not the firm's. Stored extra keys are
 * expand/contract leftovers and are ignored at inject.
 */
export const FIXED_COLOUR_KEYS = [
	'primary',
	'primary_foreground',
	'brand',
	'brand_foreground'
] as const satisfies readonly (keyof BrandingColors)[];

function pickAccentPalette(colors: BrandingColors | null | undefined): BrandingColors | undefined {
	if (!colors) return undefined;
	const out: BrandingColors = {};
	for (const key of FIXED_COLOUR_KEYS) {
		const value = colors[key];
		if (value) out[key] = value;
	}
	return Object.keys(out).length ? out : undefined;
}

/**
 * The staff-portal branding a save should persist: identity, login/support copy, colour mode,
 * and the two accent colours. Style axes (paper, type, density, radius, glass, finish, pages)
 * are the product's. Expand/contract: extra stored keys are dropped on the next studio save,
 * not by a migration.
 */
export function staffPortalBranding(b: Branding): Branding {
	const light = pickAccentPalette(b.colors?.light);
	const dark = pickAccentPalette(b.colors?.dark);
	return {
		v: b.v,
		logo_url: b.logo_url,
		logo_dark_url: b.logo_dark_url,
		icon_url: b.icon_url,
		logo_variant: b.logo_variant,
		favicon_url: b.favicon_url,
		login: b.login,
		support: b.support,
		mode: b.mode,
		colors: light || dark ? { light, dark } : undefined
	};
}

/** A curated set of monospace Google fonts for `font_mono` (the editor's mono select). All are
 * real monospace families with a full 400–700 range, so tabular/code surfaces stay aligned. */
export const MONO_FONT_OPTIONS = [
	'JetBrains Mono',
	'IBM Plex Mono',
	'Roboto Mono',
	'Source Code Pro',
	'Space Mono',
	'Fira Code',
	'DM Mono',
	'Inconsolata'
];

/** A curated set of display-capable Google fonts for `font_display` (the editor's display-font
 * select) — serif faces that make large numerals look engraved. All are verified live to serve
 * the 400–700 weight request `googleFontHref` builds (css2 substitutes the nearest real weight
 * for faces like Libre Baskerville that only ship 400/700). */
export const DISPLAY_FONT_OPTIONS = [
	'Playfair Display',
	'Cormorant Garamond',
	'Fraunces',
	'Libre Baskerville'
];

/** The fallback stack appended after a tenant's mono family (mirrors Tailwind's default). */
const MONO_FALLBACK =
	"ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono',monospace";

/** Root font-size + `--density` scale per density choice. Only these literals are ever emitted —
 * numbers from our own table, so density adds no injection surface. `standard` (and anything
 * unknown) emits nothing: 16px is the browser/theme default and silence keeps un-customised
 * tenants on it. */
const DENSITY_DECLS: Record<string, string> = {
	comfortable: 'font-size:106.25%;--density:1.0625', // 17px root — the 65+ mode
	compact: 'font-size:93.75%;--density:0.9375' // 15px root — the data-dense/professional mode
};

/** Finish-axis declarations, same contract as DENSITY_DECLS: only these literals are ever emitted
 * (numbers, keywords and var() references — no injection surface), and `card`/unknown emits
 * nothing, so an un-customised tenant keeps the stock carded look with zero output.
 *
 * `ledger` re-points the surface tokens theme.css declares (see its "finish axis" block) into the
 * private-bank register the ledger hero introduced:
 *  - cards become open sections — transparent, borderless, a bottom hairline rule per section,
 *    no drop shadow (`--surface-card*`);
 *  - square corners (`--radius:0rem` — ledger OWNS the corner, see sharedDecls);
 *  - card titles become small-caps eyebrows (`--card-title-*`);
 *  - numerals take the display face (`--money-display` — under the card finish this token stays
 *    UNSET so figures inherit their context's face; a body serif on every table would surprise);
 *  - the frosted glass material is suppressed: chrome bars go solid paper (`--glass-tint:100%`)
 *    so sticky bars stay readable over scrolling content, while content panels open up entirely
 *    (`--glass-panel-tint:0%` + shadow off) and the ambient canvas washes collapse to flat paper
 *    (`--glass-canvas-strength:0`) — hairline rules only read against a flat ground. Sheen/blur
 *    zero the same way `glass.enabled=false` does (see glassDecls). */
const FINISH_DECLS: Record<string, string> = {
	ledger: [
		'--surface-card:transparent',
		'--surface-card-border:0',
		'--surface-card-divider:1px solid var(--border)',
		'--surface-card-shadow:0 0 #0000',
		'--surface-card-shadow-floating:0 0 #0000',
		'--card-title-case:uppercase',
		'--card-title-tracking:0.08em',
		'--card-title-size:0.75rem',
		'--money-display:var(--font-display)',
		'--radius:0rem',
		'--glass-tint:100%',
		'--glass-panel-tint:0%',
		'--glass-panel-shadow:0 0 #0000',
		'--glass-blur:0px',
		'--glass-sheen-strength:0',
		'--glass-canvas-strength:0'
	].join(';')
};

/** The hero band, re-declared in full wherever it is overridden.
 *
 * theme.css defines `--hero-from` and the rest on `:root` as mixes of `--hero-base`. A custom
 * property is substituted on the element that declares it and then inherited as the resolved
 * colour, so a descendant that only overrides `--hero-base` (the studio preview's frame) keeps
 * the stock navy ramp. Emitting the whole chain on the same element as the base makes the band
 * follow. Every value here is a literal or a colour that already passed `COLOUR`. */
const HERO_RAMP = [
	'--hero-from:color-mix(in oklch, var(--hero-base) 82%, white 18%)',
	'--hero-via:var(--hero-base)',
	'--hero-to:color-mix(in oklch, var(--hero-base) 72%, black 28%)',
	'--hero-foreground:oklch(from var(--hero-base) clamp(0.16, calc((0.62 - l) * 1000), 0.985) 0.012 h)',
	'--hero-shadow:0 18px 40px -20px color-mix(in oklch, var(--hero-base) 62%, transparent)'
].join(';');

/** Feel overrides the ramp's from/via/to. Later in the same declaration, so they win. */
const HERO_FEEL: Record<string, string> = {
	solid: '--hero-from:var(--hero-base);--hero-via:var(--hero-base);--hero-to:var(--hero-base)',
	luminous: [
		'--hero-from:color-mix(in oklch, var(--brand) 48%, var(--hero-base))',
		'--hero-via:color-mix(in oklch, var(--hero-base) 62%, var(--brand))',
		'--hero-to:color-mix(in oklch, var(--hero-base) 80%, black)'
	].join(';')
};

/** Deep shade of whatever `--primary` is in force: lightness pinned, so the band stays dark when
 *  dark mode lightens the primary, and stays deeper than the buttons when the primary is already
 *  dark. The product's own navy (theme.css) stands when a firm has set no primary. */
const HERO_FROM_PRIMARY =
	'--hero-base:oklch(from var(--primary) 0.26 clamp(0.03, calc(c * 0.65), 0.09) h)';

function heroDecls(b: Branding): string {
	const out: string[] = [];
	if (b.hero_color && COLOUR.test(b.hero_color)) out.push(`--hero-base:${b.hero_color}`);
	else if (b.colors?.light?.primary && COLOUR.test(b.colors.light.primary))
		out.push(HERO_FROM_PRIMARY);
	else if (!b.hero_feel || !HERO_FEEL[b.hero_feel]) return '';
	// A feel with no base of our own still restyles the product hero.
	if (out.length) out.push(HERO_RAMP);
	const feel = b.hero_feel ? HERO_FEEL[b.hero_feel] : undefined;
	if (feel) out.push(feel);
	return out.join(';');
}

/**
 * The Google Fonts stylesheet URL for a validated family name, or null. Setting `--font-sans` alone
 * only *names* the font — the file must also be fetched, or the browser falls back to the default;
 * this loads it. The `FONT` allowlist ('[A-Za-z0-9 -]') means the family is URL-safe once spaces
 * become '+', so there's no injection surface.
 */
export function googleFontHref(font: string | null | undefined): string | null {
	if (!font || !FONT.test(font)) return null;
	const family = font.trim().replace(/ /g, '+');
	return `https://fonts.googleapis.com/css2?family=${family}:wght@400;500;600;700&display=swap`;
}

function escapeHtml(s: string): string {
	return s
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

/** Validated `--var:value` declarations for one palette (colours only). May be ''. */
function paletteDecls(
	colors: BrandingColors | null | undefined,
	surface: Surface = 'themed'
): string {
	if (!colors) return '';
	const decls: string[] = [];
	const keys = surface === 'fixed' ? FIXED_COLOUR_KEYS : COLOUR_VARS_KEYS;
	for (const key of keys) {
		const vars = COLOUR_VARS[key];
		if (!vars) continue;
		const value = (colors as Record<string, string | null | undefined>)[key];
		if (value && COLOUR.test(value)) for (const v of vars) decls.push(`${v}:${value}`);
	}
	return decls.join(';');
}

function clampInt(v: number | null | undefined, lo: number, hi: number): number | null {
	if (v === null || v === undefined) return null;
	const n = Math.round(Number(v));
	if (!Number.isFinite(n)) return null;
	return Math.max(lo, Math.min(hi, n));
}

/** Whether the tenant's branding may set the SURFACE MATERIAL (glass + radius), or only its
 * colours/font. See sharedDecls. */
export type Surface = 'themed' | 'fixed';

/** Frosted-glass material vars (numbers only, so injection-safe). `enabled:false` ⇒ solid surfaces;
 * otherwise only the knobs a firm set are emitted (the rest fall back to the design-system defaults
 * baked into the glass utilities). May be ''. */
function glassDecls(b: Branding): string {
	const g = b.glass;
	if (!g) return '';
	if (g.enabled === false)
		return '--glass-tint:100%;--glass-panel-tint:100%;--glass-blur:0px;--glass-sheen-strength:0';
	const decls: string[] = [];
	const opacity = clampInt(g.opacity, 20, 100);
	if (opacity !== null) {
		decls.push(`--glass-tint:${opacity}%`);
		decls.push(`--glass-panel-tint:${Math.min(opacity + 16, 100)}%`);
	}
	const blur = clampInt(g.blur, 0, 40);
	if (blur !== null) decls.push(`--glass-blur:${blur}px`);
	const sheen = clampInt(g.sheen, 0, 100);
	if (sheen !== null) decls.push(`--glass-sheen-strength:${(sheen / 100).toFixed(2)}`);
	return decls.join(';');
}

/** Shared (mode-independent) declarations: radius + fonts (sans/mono/display) + density + glass
 * material. May be ''.
 *
 * `surface: 'fixed'` emits **nothing** here. A staff tool's surface language is the product's, not
 * the firm's: the platform portal ships the sharp + chrome presets and a tenant must not be able
 * to put frosted glass, consumer corners, a display serif or a generated paper tint back into the
 * tool its advisers read all day. Identity (logo, favicon) and the two accent colours still
 * brand — those are what white-label means on a fixed surface. The CLIENT portal passes 'themed'
 * (the default), because that surface genuinely is the firm's shopfront. */
function sharedDecls(b: Branding, surface: Surface = 'themed'): string {
	if (surface === 'fixed') return '';
	const decls: string[] = [];
	if (b.font && FONT.test(b.font)) {
		const stack = `'${b.font}',ui-sans-serif,system-ui,sans-serif`;
		decls.push(`--font-sans:${stack}`);
		// …AND Tailwind's document-level default. theme.css declares --font-sans inside `@theme inline`,
		// which bakes its literal value into `--default-font-family` — the var Tailwind's preflight puts
		// on `html`. So overriding --font-sans alone re-themed nothing: every piece of inherited body
		// copy stayed on Inter while the firm's font sat unused in a custom property. Verified in a
		// browser (getComputedStyle(document.body).fontFamily). Harmless once theme.css stops
		// inlining the font (see the handoff): both declarations then carry the same stack.
		decls.push(`--default-font-family:${stack}`);
	}
	// Mono is a real branding axis, not a curiosity: reference/ID columns, monetary tabular figures,
	// the template editor and every `font-mono` surface key off it. Without this they stayed on the
	// OS mono stack while the rest of the portal re-themed.
	if (b.font_mono && FONT.test(b.font_mono))
		decls.push(`--font-mono:'${b.font_mono}',${MONO_FALLBACK}`);
	// The display face falls back to the firm's own sans (theme.css defaults --font-display to
	// var(--font-sans)), so an unloaded glyph never drops below the body face.
	if (b.font_display && FONT.test(b.font_display))
		decls.push(`--font-display:'${b.font_display}',var(--font-sans)`);
	// Density: a root font-size rescales the whole rem-based system (type, spacing, touch targets)
	// in one declaration. Client portal only — a fixed surface keeps the product scale.
	const density = b.density ? DENSITY_DECLS[b.density] : undefined;
	if (density) decls.push(density);
	// Finish is MATERIAL. Unknown/`card` values emit nothing.
	const finish = b.finish ? FINISH_DECLS[b.finish] : undefined;
	// Radius is branded on the client shopfront. Ledger owns the corner (`--radius:0rem` rides
	// its declaration table) — a firm's 1rem consumer radius on hairline-ruled sections would
	// break the register the firm just opted into, so the slider is not emitted with it.
	if (b.radius && RADIUS.test(b.radius) && !finish) decls.push(`--radius:${b.radius}`);
	if (finish) {
		// Ledger carries its own glass suppression, so the firm's glass sliders are moot —
		// emitting both would just leave the outcome to declaration order.
		decls.push(finish);
	} else {
		const glass = glassDecls(b);
		if (glass) decls.push(glass);
	}
	// The hero follows the brand (or an explicit colour) on the client shopfront only. A fixed
	// surface keeps the product ink — the same rule as finish and glass.
	const hero = heroDecls(b);
	if (hero) decls.push(hero);
	return decls.join(';');
}

/**
 * Flat inline `style=""` declarations for a scoped PREVIEW element rendering one colour scheme —
 * the chosen palette's colours + shared radius/font. Used by the editor's live portal preview.
 *
 * Pass `surface: 'fixed'` when previewing the PLATFORM portal, so the preview withholds paper,
 * type and material exactly as `brandingHead` does for that portal. Without it the studio shows a
 * generated re-theme that appears to work, while the real staff portal keeps the product look.
 */
export function brandingPreviewStyle(
	b: Branding | null | undefined,
	scheme: 'light' | 'dark' = 'light',
	opts?: { surface?: Surface }
): string {
	if (!b) return '';
	const surface = opts?.surface ?? 'themed';
	const palette = paletteDecls(b.colors?.[scheme], surface);
	// `--palette-scope` makes theme.css re-derive the aliases (card ink, ring, tonal ramps…) on
	// the preview element itself, against this palette rather than the product one at <html>.
	// Themed only: on a fixed surface the staff presets reassign those aliases at <html>, and
	// re-deriving them on the frame would preview a staff portal that does not exist.
	const scoped = palette && surface === 'themed' ? `--palette-scope:1;${palette}` : palette;
	const parts = [scoped, sharedDecls(b, surface)].filter(Boolean);
	return parts.join(';');
}

/**
 * The full per-tenant `<head>` fragment to inject before `%sveltekit.head%` (SSR, no flash):
 * `<style>` overriding the theme vars (separate light/dark palettes, scoped so each applies only in
 * its scheme and an unset dark palette falls back to the design system's dark theme), the Google
 * Fonts `<link>`s for the sans + mono families, a favicon `<link>` and a base `<title>`. The firm's
 * `mode` is applied by ./theme (see the note at the end), not here.
 * All values are re-validated + escaped here. Selectors use extra `:root` for specificity so the
 * override beats the app's own `:root`/`.dark` regardless of stylesheet order. Returns '' if empty.
 */
export function brandingHead(
	b: Branding | null | undefined,
	appName?: string | null,
	opts?: { surface?: Surface; fallbackFavicon?: string }
): string {
	const parts: string[] = [];

	if (b) {
		const blocks: string[] = [];
		const surface = opts?.surface ?? 'themed';
		const shared = sharedDecls(b, surface);
		const light = paletteDecls(b.colors?.light, surface);
		const dark = paletteDecls(b.colors?.dark, surface);
		// radius/font apply in both schemes; light only outside `.dark`; dark only under `.dark`
		// (so an unset dark palette leaves the design system's own dark theme intact).
		if (shared) blocks.push(`:root:root{${shared}}`);
		if (light) blocks.push(`:root:root:not(.dark){${light}}`);
		if (dark) blocks.push(`.dark:root:root{${dark}}`);
		if (blocks.length) parts.push(`<style>${blocks.join('')}</style>`);
	}

	// Load the custom fonts (not just name them), so `--font-sans`/`--font-mono`/`--font-display`
	// actually take effect. A fixed surface never names them, so it must not fetch them either.
	const fontHrefs =
		opts?.surface === 'fixed'
			? []
			: [
					...new Set([
						googleFontHref(b?.font),
						googleFontHref(b?.font_mono),
						googleFontHref(b?.font_display)
					])
				].filter((h): h is string => h !== null);
	if (fontHrefs.length) {
		parts.push('<link rel="preconnect" href="https://fonts.googleapis.com" />');
		parts.push('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />');
		for (const href of fontHrefs) parts.push(`<link rel="stylesheet" href="${href}" />`);
	}

	const brandedFavicon = b?.favicon_url;
	const favicon =
		brandedFavicon &&
		(HTTPS.test(brandedFavicon) ||
			PROXY_ASSET.test(brandedFavicon) ||
			DATA_IMG.test(brandedFavicon))
			? brandedFavicon
			: opts?.fallbackFavicon;
	if (favicon) parts.push(`<link rel="icon" href="${escapeHtml(favicon)}" />`);

	if (appName) parts.push(`<title>${escapeHtml(appName)}</title>`);

	// NB: `mode` is deliberately NOT applied here. Colour-scheme resolution lives in ./theme —
	// `themeAttributes()` stamps the firm's mode on <html> and `themeHead()`'s script settles it
	// against the member's own `gp-theme` choice and the OS preference. brandingHead used to emit a
	// competing toggle script; two scripts racing to set `.dark` could only ever disagree (this one
	// knew nothing about the member's choice), so the firm's mode now has exactly one consumer.
	return parts.join('');
}
