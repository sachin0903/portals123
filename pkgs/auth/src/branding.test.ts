import { describe, expect, it } from 'vitest';
import {
	brandingHead,
	brandingPreviewStyle,
	googleFontHref,
	isFontFamily,
	resolveClientPortalTemplate,
	COLOUR_VARS_KEYS,
	FIXED_COLOUR_KEYS,
	CARD_FINISHES,
	isSafeCardArt,
	isCardColor,
	staffPortalBranding,
	MONO_FONT_OPTIONS,
	DISPLAY_FONT_OPTIONS,
	type Branding,
	type BrandingColors
} from './branding';

describe('resolveClientPortalTemplate', () => {
	const branding: Branding = {
		logo_url: 'https://assets.example/logo.svg',
		support: { email: 'help@example.com' },
		colors: { light: { primary: '#111111' } },
		pages: [],
		default_template_id: 'standard',
		templates: [
			{
				id: 'standard',
				name: 'Standard',
				role_slugs: ['client'],
				colors: { light: { primary: '#225588' } },
				pages: [
					{
						id: 'home',
						slug: 'home',
						label: 'Home',
						layouts: { default: [{ id: 'accounts', type: 'accounts' }] }
					}
				]
			},
			{
				id: 'family',
				name: 'Family',
				role_slugs: ['family-client', 'trustee'],
				finish: 'ledger',
				pages: [
					{
						id: 'family-home',
						slug: 'home',
						label: 'Family',
						layouts: { default: [{ id: 'allocation', type: 'allocation' }] }
					}
				]
			}
		]
	};

	it('selects one template for several assigned roles while retaining firm identity', () => {
		const resolved = resolveClientPortalTemplate(branding, 'trustee');
		expect(resolved?.finish).toBe('ledger');
		expect(resolved?.pages?.[0]?.label).toBe('Family');
		expect(resolved?.logo_url).toBe(branding.logo_url);
		expect(resolved?.support).toEqual(branding.support);
	});

	it('uses the named default for an unassigned role without inheriting legacy appearance', () => {
		const resolved = resolveClientPortalTemplate(branding, 'unassigned');
		expect(resolved?.colors?.light?.primary).toBe('#225588');
		expect(resolved?.finish).toBeUndefined();
		expect(resolved?.layout).toEqual([{ id: 'accounts', type: 'accounts' }]);
	});

	it('keeps the parent page set when a template has not composed pages of its own', () => {
		const parentPages = [
			{
				id: 'home',
				slug: 'home',
				label: 'Home',
				layouts: { default: [{ id: 'holdings', type: 'holdings' }] }
			}
		];
		const resolved = resolveClientPortalTemplate(
			{
				pages: parentPages,
				default_template_id: 'standard',
				templates: [
					{
						id: 'standard',
						name: 'Standard',
						role_slugs: ['client'],
						colors: { light: { primary: '#225588' } }
					}
				]
			},
			'client'
		);
		expect(resolved?.pages).toEqual(parentPages);
		expect(resolved?.layout).toEqual([{ id: 'holdings', type: 'holdings' }]);
		expect(resolved?.colors?.light?.primary).toBe('#225588');
	});

	it('preserves the legacy single-design behaviour when there are no templates', () => {
		const legacy: Branding = { colors: { light: { primary: '#123456' } } };
		expect(resolveClientPortalTemplate(legacy, 'client')).toBe(legacy);
		expect(resolveClientPortalTemplate(null, 'client')).toBeNull();
	});

	it('prefers a classification slug over an auth-profile role', () => {
		const withClasses: Branding = {
			...branding,
			templates: [
				{
					id: 'advised',
					name: 'Advised',
					role_slugs: ['client'],
					classification_slugs: ['advised'],
					pages: [
						{
							id: 'home',
							slug: 'home',
							label: 'Advised',
							layouts: { default: [{ id: 'accounts', type: 'accounts' }] }
						}
					]
				},
				{
					id: 'd2c',
					name: 'D2C',
					classification_slugs: ['d2c'],
					finish: 'ledger',
					pages: [
						{
							id: 'home',
							slug: 'home',
							label: 'D2C',
							layouts: { default: [{ id: 'allocation', type: 'allocation' }] }
						}
					]
				}
			]
		};
		const resolved = resolveClientPortalTemplate(withClasses, {
			classification: 'd2c',
			role: 'client'
		});
		expect(resolved?.pages?.[0]?.label).toBe('D2C');
		expect(resolved?.finish).toBe('ledger');
	});

	it('prefers the first client group that has a design, ahead of the book', () => {
		const branded: Branding = {
			...branding,
			default_template_id: 'standard',
			templates: [
				{
					id: 'workplace',
					name: 'Workplace',
					group_slugs: ['workplace'],
					pages: [
						{
							id: 'home',
							slug: 'home',
							label: 'Workplace',
							layouts: { default: [{ id: 'accounts', type: 'accounts' }] }
						}
					]
				},
				{
					id: 'advised',
					name: 'Advised',
					classification_slugs: ['advised'],
					pages: [
						{
							id: 'home',
							slug: 'home',
							label: 'Advised',
							layouts: { default: [{ id: 'allocation', type: 'allocation' }] }
						}
					]
				}
			]
		};
		const resolved = resolveClientPortalTemplate(branded, {
			groups: ['vip', 'workplace'],
			classification: 'advised',
			role: 'client'
		});
		expect(resolved?.pages?.[0]?.label).toBe('Workplace');
	});

	it('falls through a group that has no design to the book', () => {
		const branded: Branding = {
			...branding,
			templates: [
				{
					id: 'advised',
					name: 'Advised',
					classification_slugs: ['advised'],
					finish: 'ledger'
				}
			]
		};
		const resolved = resolveClientPortalTemplate(branded, {
			groups: ['vip'],
			classification: 'advised'
		});
		expect(resolved?.finish).toBe('ledger');
	});
});

describe('brandingPreviewStyle', () => {
	it('maps the basic palette + shared tokens to inline vars (1:1 token → var)', () => {
		const css = brandingPreviewStyle(
			{
				colors: {
					light: {
						primary: '#2b6cb0',
						brand: 'oklch(0.64 0.115 202)',
						muted_foreground: '#556',
						negative: '#c00'
					}
				},
				radius: '1rem',
				font: 'Inter'
			},
			'light'
		);
		expect(css).toContain('--primary:#2b6cb0');
		expect(css).toContain('--brand:oklch(0.64 0.115 202)');
		expect(css).toContain('--muted-foreground:#556');
		expect(css).toContain('--negative:#c00');
		expect(css).toContain('--radius:1rem');
		expect(css).toContain("--font-sans:'Inter'");
	});

	it('marks a themed palette so theme.css re-derives its aliases on the preview frame', () => {
		const themed = brandingPreviewStyle({ colors: { light: { card: '#fff' } } }, 'light');
		expect(themed).toContain('--palette-scope:1');
		const fixed = brandingPreviewStyle({ colors: { light: { primary: '#123' } } }, 'light', {
			surface: 'fixed'
		});
		expect(fixed).not.toContain('--palette-scope');
		expect(brandingPreviewStyle({ radius: '1rem' }, 'light')).not.toContain('--palette-scope');
	});

	it('drops retired per-token keys a stored theme may still carry', () => {
		const css = brandingPreviewStyle(
			{
				colors: {
					light: { primary: '#123', sidebar_primary: '#0af', ring: '#abc' } as BrandingColors
				}
			},
			'light'
		);
		expect(css).not.toContain('--sidebar-primary');
		expect(css).not.toContain('--ring');
	});

	it('emits glass material vars, and solid vars when glass is disabled', () => {
		const on = brandingPreviewStyle(
			{ glass: { enabled: true, opacity: 70, blur: 8, sheen: 50 } },
			'light'
		);
		expect(on).toContain('--glass-tint:70%');
		expect(on).toContain('--glass-panel-tint:86%'); // opacity + 16, capped at 100
		expect(on).toContain('--glass-blur:8px');
		expect(on).toContain('--glass-sheen-strength:0.50');

		const off = brandingPreviewStyle({ glass: { enabled: false } }, 'light');
		expect(off).toContain('--glass-tint:100%');
		expect(off).toContain('--glass-blur:0px');
		expect(off).toContain('--glass-sheen-strength:0');
	});

	it('withholds the glass material for a fixed-surface preview, matching brandingHead', () => {
		const b: Branding = {
			glass: { enabled: true, opacity: 70, blur: 8 },
			radius: '1rem',
			colors: { light: { primary: '#111111', background: '#fff0f0' } }
		};
		const preview = brandingPreviewStyle(b, 'light', { surface: 'fixed' });
		expect(preview).not.toContain('--glass-');
		expect(preview).not.toContain('--radius');
		expect(preview).not.toContain('--background');
		expect(preview).toContain('--primary:#111111');
		// Default stays 'themed' so the client-portal preview is unchanged.
		expect(brandingPreviewStyle(b, 'light')).toContain('--glass-blur:8px');
		expect(brandingPreviewStyle(b, 'light')).toContain('--background:#fff0f0');
	});

	it('reads the requested scheme', () => {
		const b = { colors: { light: { primary: '#111' }, dark: { primary: '#eee' } } };
		expect(brandingPreviewStyle(b, 'light')).toContain('--primary:#111');
		expect(brandingPreviewStyle(b, 'dark')).toContain('--primary:#eee');
	});
});

describe('brandingHead', () => {
	it('emits scoped light + dark blocks (dark under .dark)', () => {
		const head = brandingHead({
			colors: { light: { primary: '#111111' }, dark: { primary: '#eeeeee' } },
			radius: '1rem'
		});
		expect(head).toContain(':root:root{--radius:1rem;'); // shared, ahead of the hero ramp
		expect(head).toContain(':root:root:not(.dark){--primary:#111111}'); // light only
		expect(head).toContain('.dark:root:root{--primary:#eeeeee}'); // dark only
	});

	it('drops values that fail validation (no CSS injection)', () => {
		const head = brandingHead({
			colors: { light: { primary: 'red; } body{display:none}', brand: '</style><script>x' } },
			radius: '1rem; color:red',
			font: "Evil'; }"
		});
		expect(head).toBe('');
	});

	it('injects favicon and escaped title', () => {
		const head = brandingHead(
			{ colors: { light: { primary: '#123456' } }, favicon_url: 'https://cdn.example.com/f.ico' },
			'Acme & Co'
		);
		expect(head).toContain(':root:root:not(.dark){--primary:#123456}');
		expect(head).toContain('<link rel="icon" href="https://cdn.example.com/f.ico" />');
		expect(head).toContain('<title>Acme &amp; Co</title>'); // HTML-escaped
	});

	it('emits exactly one favicon: the firm image or the product fallback', () => {
		const fallback = brandingHead({}, null, { fallbackFavicon: '/favicon.svg' });
		expect(fallback.match(/rel="icon"/g)).toHaveLength(1);
		expect(fallback).toContain('href="/favicon.svg"');

		const branded = brandingHead({ favicon_url: '/_assets/favicon-0123456789abcdef.png' }, null, {
			fallbackFavicon: '/favicon.svg'
		});
		expect(branded.match(/rel="icon"/g)).toHaveLength(1);
		expect(branded).toContain('href="/_assets/favicon-0123456789abcdef.png"');
		expect(branded).not.toContain('href="/favicon.svg"');
	});

	it('rejects a non-https favicon and escapes a hostile app name', () => {
		const head = brandingHead(
			{ favicon_url: 'javascript:alert(1)' },
			'</title><script>alert(1)</script>'
		);
		expect(head).not.toContain('javascript:');
		expect(head).not.toContain('<script>alert(1)</script>');
		expect(head).toContain('&lt;script&gt;');
	});

	it('loads a custom font stylesheet (names + fetches it)', () => {
		const head = brandingHead({ font: 'Open Sans' });
		expect(head).toContain(
			'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&display=swap" />'
		);
		expect(head).toContain('rel="preconnect"');
	});

	it('emits no colour-scheme script — ./theme owns that (see the precedence suite below)', () => {
		for (const mode of ['light', 'dark', 'auto'] as const)
			expect(brandingHead({ mode })).not.toContain('<script');
	});

	it('returns empty string for empty branding', () => {
		expect(brandingHead(null)).toBe('');
		expect(brandingHead({})).toBe('');
	});
});

describe('hero — the band follows the brand, and can be set', () => {
	it('derives a deep shade of the primary, ramp and all', () => {
		const head = brandingHead({ colors: { light: { primary: 'oklch(0.45 0.12 40)' } } });
		expect(head).toContain(
			'--hero-base:oklch(from var(--primary) 0.26 clamp(0.03, calc(c * 0.65), 0.09) h)'
		);
		// The ramp is re-declared with the base. A base alone does nothing: --hero-from is
		// resolved on :root and inherited as the stock navy.
		expect(head).toContain('--hero-from:color-mix(in oklch, var(--hero-base) 82%, white 18%)');
		expect(head).toContain('--hero-foreground:oklch(from var(--hero-base)');
	});

	it('an explicit colour replaces the derivation, and a feel replaces the ramp', () => {
		const head = brandingHead({
			hero_color: '#112233',
			hero_feel: 'solid',
			colors: { light: { primary: 'oklch(0.45 0.12 40)' } }
		});
		expect(head).toContain('--hero-base:#112233');
		expect(head).not.toContain('oklch(from var(--primary) 0.26');
		expect(head).toContain('--hero-from:var(--hero-base)');
		expect(head).toContain('--hero-to:var(--hero-base)');
	});

	it('luminous pulls the brand through the band', () => {
		const head = brandingHead({
			hero_feel: 'luminous',
			colors: { light: { primary: '#112233', brand: '#aa5500' } }
		});
		expect(head).toContain('color-mix(in oklch, var(--brand) 48%, var(--hero-base))');
	});

	it('refuses an unsafe colour and leaves a staff surface on the product ink', () => {
		expect(brandingHead({ hero_color: 'red; } :root{display:none' })).toBe('');
		const fixed = brandingHead(
			{ hero_color: '#112233', colors: { light: { primary: '#112233' } } },
			null,
			{ surface: 'fixed' }
		);
		expect(fixed).not.toContain('--hero-base');
	});
});

describe('googleFontHref', () => {
	it('builds a URL-safe css2 href, or null for unset/invalid', () => {
		expect(googleFontHref('Playfair Display')).toBe(
			'https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap'
		);
		expect(googleFontHref(null)).toBeNull();
		expect(googleFontHref("Evil'; }")).toBeNull(); // fails the FONT allowlist
	});
});

describe('isFontFamily — the studio’s free-text font box applies the injection rule', () => {
	it('accepts any plain family name, curated or not, and loads it', () => {
		// A firm may type a Google Fonts family the curated lists do not carry.
		for (const f of ['Figtree', 'Space Grotesk', 'Source Sans 3', 'EB Garamond']) {
			expect(isFontFamily(f)).toBe(true);
			expect(googleFontHref(f)).not.toBeNull();
		}
		expect(brandingHead({ font: 'Figtree' })).toContain("--font-sans:'Figtree'");
	});

	it('refuses exactly what the head injection would drop', () => {
		expect(isFontFamily(null)).toBe(false);
		expect(isFontFamily('')).toBe(false);
		expect(isFontFamily("Evil'; }")).toBe(false);
		expect(isFontFamily('Noto Sans, serif')).toBe(false); // one family, no stack
		expect(isFontFamily('x'.repeat(49))).toBe(false);
	});
});

describe('surface: fixed — staff-tool surfaces are the product’s, not the firm’s', () => {
	/** The platform portal ships the sharp + chrome presets. brandingHead emits
	 *  `:root:root{…}` — doubled specificity, deliberately — so ANY material or paper var a tenant
	 *  sets would beat those stylesheets and put frosted glass, consumer corners and a generated
	 *  paper tint back into the staff tool. The fix is to not emit them for that portal. */
	const themed: Branding = {
		radius: '1rem',
		font: 'Inter',
		glass: { enabled: true, opacity: 55, blur: 20, sheen: 60 },
		colors: {
			light: {
				primary: 'oklch(0.5 0.1 200)',
				background: 'oklch(0.9 0.05 30)',
				warning: '#b45309'
			}
		}
	};

	it('emits only the two accent colours, not paper, type or material', () => {
		const head = brandingHead(themed, 'Chester Rose', { surface: 'fixed' });
		for (const v of [
			'--glass-tint',
			'--glass-panel-tint',
			'--glass-blur',
			'--glass-sheen-strength',
			'--radius',
			'--font-sans',
			'--background',
			'--warning'
		])
			expect(head, `${v} must not be emitted`).not.toContain(v);
		expect(head).toContain('--primary');
		expect(head).not.toContain('fonts.googleapis.com');
	});

	it('still emits everything for the client portal, which is the firm’s shopfront', () => {
		const head = brandingHead(themed, 'Chester Rose');
		expect(head).toContain('--glass-blur:20px');
		expect(head).toContain('--radius:1rem');
		expect(head).toContain('--background');
	});

	it('a tenant that disabled glass cannot re-enable it on a fixed surface either', () => {
		const off = { ...themed, glass: { enabled: false } };
		expect(brandingHead(off, null, { surface: 'fixed' })).not.toContain('--glass-tint');
	});

	it('does not brand warning, typefaces or density on the staff tool', () => {
		const head = brandingHead(
			{
				font: 'Inter',
				font_mono: 'IBM Plex Mono',
				density: 'comfortable',
				colors: { light: { warning: '#b45309', brand: '#17a2a2' } }
			},
			null,
			{ surface: 'fixed' }
		);
		expect(head).not.toContain('--font-mono:');
		expect(head).not.toContain('--warning');
		expect(head).not.toContain('font-size:');
		expect(head).toContain('--brand:#17a2a2');
	});
});

// ── coverage: every token a firm can set must actually reach the document ──────────────────────
describe('coverage — nothing storable is silently dropped', () => {
	/** A branding object with EVERY colour token set to a distinguishable value. */
	function fullPalette(): BrandingColors {
		const colors: Record<string, string> = {};
		COLOUR_VARS_KEYS.forEach((key, i) => {
			// Distinct, valid oklch per token, so a missing var can't be masked by a duplicate value.
			colors[key] = `oklch(0.5 0.1 ${i})`;
		});
		return colors as BrandingColors;
	}

	it('is the fourteen-token basic palette', () => {
		expect(COLOUR_VARS_KEYS).toHaveLength(14);
	});

	it('emits one CSS var for every colour key in both schemes', () => {
		const palette = fullPalette();
		const head = brandingHead({ colors: { light: palette, dark: palette } });
		for (const key of COLOUR_VARS_KEYS) {
			const cssVar = `--${key.replace(/_/g, '-')}`;
			// Each var appears twice — once in the light block, once under `.dark`.
			const hits = head.split(`${cssVar}:`).length - 1;
			expect(hits, `${cssVar} must be emitted in both schemes`).toBe(2);
		}
	});

	it('covers the base token of every derived subtle/strong/line trio', () => {
		// theme.css derives the tonal tokens by color-mix'ing these bases against
		// --background/--foreground (success/destructive alias positive/negative). The trios need no
		// branding surface of their own — but the BASE must be brandable, or the tint can never move.
		const bases = ['warning', 'info', 'brand', 'positive', 'negative'];
		for (const base of bases) expect(COLOUR_VARS_KEYS).toContain(base);
		// …and the anchors the mix resolves against.
		for (const anchor of ['background', 'foreground']) expect(COLOUR_VARS_KEYS).toContain(anchor);
	});

	it('emits and loads a mono family alongside the sans one', () => {
		const head = brandingHead({ font: 'Inter', font_mono: 'JetBrains Mono' });
		expect(head).toContain("--font-sans:'Inter'");
		expect(head).toContain("--font-mono:'JetBrains Mono',ui-monospace");
		// Both families are actually fetched — naming a font that isn't loaded is a silent no-op.
		expect(head).toContain('family=Inter:');
		expect(head).toContain('family=JetBrains+Mono:');
		expect(head.split('rel="preconnect"').length - 1).toBe(2); // preconnects emitted once
	});

	it("overrides Tailwind's document default font, not just --font-sans", () => {
		// theme.css declares --font-sans inside `@theme inline`, so Tailwind bakes its literal value
		// into --default-font-family (the var its preflight puts on `html`). Setting --font-sans alone
		// left every piece of inherited body copy on Inter — the firm's font applied to nothing.
		const head = brandingHead({ font: 'Playfair Display' });
		expect(head).toContain("--default-font-family:'Playfair Display',ui-sans-serif");
		// Mono needs no equivalent: theme.css never declares --font-mono, so Tailwind's own
		// `--default-mono-font-family: var(--font-mono, …)` picks the override up by reference.
		expect(brandingHead({ font_mono: 'Fira Code' })).not.toContain('--default-mono-font-family');
	});

	it('loads one stylesheet when sans and mono are the same family', () => {
		const head = brandingHead({ font: 'IBM Plex Mono', font_mono: 'IBM Plex Mono' });
		expect(head.split('rel="stylesheet"').length - 1).toBe(1);
	});

	it('rejects a hostile mono family rather than injecting it', () => {
		expect(brandingHead({ font_mono: "Evil'; } :root{display:none" })).toBe('');
	});

	it('offers only allowlist-safe mono options', () => {
		for (const f of MONO_FONT_OPTIONS) expect(googleFontHref(f)).not.toBeNull();
	});
});

describe('density — demographic adaptivity as one root declaration', () => {
	/** The design system is rem-based, so a single root font-size scales type, spacing and touch
	 *  targets coherently. Comfortable IS the 65+ mode; compact the professional/trust mode. Only
	 *  hardcoded numbers from the internal table are ever emitted — no injection surface. */
	it('comfortable scales the root up to 17px and exposes the --density factor', () => {
		const head = brandingHead({ density: 'comfortable' });
		expect(head).toContain('font-size:106.25%');
		expect(head).toContain('--density:1.0625');
		// It rides the shared block, so it applies in both colour schemes.
		expect(head).toContain(':root:root{');
	});

	it('compact scales the root down to 15px', () => {
		const head = brandingHead({ density: 'compact' });
		expect(head).toContain('font-size:93.75%');
		expect(head).toContain('--density:0.9375');
	});

	it('standard and unset emit nothing — 16px is the default and silence keeps it', () => {
		expect(brandingHead({ density: 'standard' })).toBe('');
		expect(brandingHead({})).toBe('');
		expect(brandingHead({ density: null })).toBe('');
	});

	it('junk density emits nothing rather than injecting (numbers-only surface)', () => {
		const hostile = { density: '200%;}body{display:none' } as unknown as Branding;
		expect(brandingHead(hostile)).toBe('');
	});

	it('the studio preview carries it, so the editor visibly rescales', () => {
		expect(brandingPreviewStyle({ density: 'comfortable' })).toContain('font-size:106.25%');
		expect(brandingPreviewStyle({ density: 'compact' })).toContain('--density:0.9375');
		expect(brandingPreviewStyle({ density: 'standard' })).toBe('');
	});

	it('does not rescale a fixed surface — density is a client-shopfront axis', () => {
		const head = brandingHead({ density: 'comfortable' }, null, { surface: 'fixed' });
		expect(head).toBe('');
	});
});

describe('finish — the portal-wide surface register (card | ledger)', () => {
	/** One token axis that re-skins every widget coherently: everything renders through Card.* +
	 *  the theme.css tokens, so re-pointing them carries the whole portal. Only hardcoded
	 *  declarations from the internal table are ever emitted — no injection surface. */
	it('ledger emits the full token set, riding the shared block', () => {
		const head = brandingHead({ finish: 'ledger' });
		expect(head).toContain(':root:root{');
		// The card becomes an open section: transparent, borderless, a bottom hairline rule, flat.
		expect(head).toContain('--surface-card:transparent');
		expect(head).toContain('--surface-card-border:0');
		expect(head).toContain('--surface-card-divider:1px solid var(--border)');
		expect(head).toContain('--surface-card-shadow:0 0 #0000');
		expect(head).toContain('--surface-card-shadow-floating:0 0 #0000');
		// Square corners — ledger owns the radius.
		expect(head).toContain('--radius:0rem');
		// Card titles become small-caps eyebrows.
		expect(head).toContain('--card-title-case:uppercase');
		expect(head).toContain('--card-title-tracking:0.08em');
		expect(head).toContain('--card-title-size:0.75rem');
		// Numerals take the display face (Money's knob; unset elsewhere so figures inherit).
		expect(head).toContain('--money-display:var(--font-display)');
		// Glass: chrome bars go solid paper, content panels open entirely, flat canvas, no gloss.
		expect(head).toContain('--glass-tint:100%');
		expect(head).toContain('--glass-panel-tint:0%');
		expect(head).toContain('--glass-panel-shadow:0 0 #0000');
		expect(head).toContain('--glass-blur:0px');
		expect(head).toContain('--glass-sheen-strength:0');
		expect(head).toContain('--glass-canvas-strength:0');
	});

	it('card, null and absent emit nothing — silence keeps the stock carded look', () => {
		expect(brandingHead({ finish: 'card' })).toBe('');
		expect(brandingHead({ finish: null })).toBe('');
		expect(brandingHead({})).toBe('');
	});

	it('junk finish emits nothing rather than injecting (table-lookup surface, like density)', () => {
		const hostile = { finish: '0;}body{display:none' } as unknown as Branding;
		expect(brandingHead(hostile)).toBe('');
	});

	it('ledger owns the corner: the firm radius slider is not emitted with it', () => {
		const head = brandingHead({ finish: 'ledger', radius: '1rem' });
		expect(head).not.toContain('--radius:1rem');
		expect(head).toContain('--radius:0rem');
		// …and without ledger the same branding emits the slider value as ever.
		expect(brandingHead({ radius: '1rem' })).toContain('--radius:1rem');
	});

	it('ledger supersedes the firm glass sliders — no duplicate knobs left to declaration order', () => {
		const head = brandingHead({ finish: 'ledger', glass: { enabled: true, opacity: 70, blur: 8 } });
		expect(head).not.toContain('--glass-tint:70%');
		expect(head).not.toContain('--glass-blur:8px');
		expect(head).toContain('--glass-panel-tint:0%'); // ledger's own suppression stands
	});

	it('is withheld on a fixed surface — staff tools keep the product look, like the glass material', () => {
		const b: Branding = { finish: 'ledger', radius: '1rem', font: 'Inter' };
		const head = brandingHead(b, null, { surface: 'fixed' });
		expect(head).not.toContain('--surface-card');
		expect(head).not.toContain('--card-title-case');
		expect(head).not.toContain('--money-display');
		expect(head).not.toContain('--radius');
		expect(head).not.toContain('--font-sans');
		expect(head).toBe('');
	});

	it('the studio preview carries it, so the editor visibly flips register', () => {
		const css = brandingPreviewStyle({ finish: 'ledger' });
		expect(css).toContain('--surface-card:transparent');
		expect(css).toContain('--card-title-case:uppercase');
		// …and the platform-portal preview withholds it, matching the real staff portal.
		expect(brandingPreviewStyle({ finish: 'ledger' }, 'light', { surface: 'fixed' })).toBe('');
	});

	it('keeps the glass emission intact under the card finish', () => {
		const head = brandingHead({ finish: 'card', glass: { enabled: true, opacity: 70 } });
		expect(head).toContain('--glass-tint:70%');
		expect(head).not.toContain('--surface-card');
	});
});

describe('font_display — the numerals/display face', () => {
	it('emits --font-display with the sans-var fallback and actually loads the face', () => {
		const head = brandingHead({ font_display: 'Fraunces' });
		expect(head).toContain("--font-display:'Fraunces',var(--font-sans)");
		// Named AND fetched — naming a font that isn't loaded is a silent no-op.
		expect(head).toContain('family=Fraunces:');
	});

	it('loads sans, mono and display together, deduping a shared family', () => {
		const head = brandingHead({
			font: 'Inter',
			font_mono: 'JetBrains Mono',
			font_display: 'Playfair Display'
		});
		expect(head).toContain('family=Inter:');
		expect(head).toContain('family=JetBrains+Mono:');
		expect(head).toContain('family=Playfair+Display:');
		// Same family for body + display ⇒ one stylesheet.
		const shared = brandingHead({ font: 'Playfair Display', font_display: 'Playfair Display' });
		expect(shared.split('rel="stylesheet"').length - 1).toBe(1);
	});

	it('the studio preview carries it', () => {
		expect(brandingPreviewStyle({ font_display: 'Cormorant Garamond' })).toContain(
			"--font-display:'Cormorant Garamond',var(--font-sans)"
		);
	});

	it('rejects a hostile display family rather than injecting it', () => {
		expect(brandingHead({ font_display: "Evil'; } :root{display:none" })).toBe('');
	});

	it('does not brand the staff tool — a fixed surface keeps the product face', () => {
		const head = brandingHead({ font_display: 'Libre Baskerville' }, null, { surface: 'fixed' });
		expect(head).toBe('');
	});

	it('offers only allowlist-safe, fetchable display options', () => {
		for (const f of DISPLAY_FONT_OPTIONS) expect(googleFontHref(f)).not.toBeNull();
	});
});

describe("colour scheme — the firm's mode has exactly one consumer", () => {
	/** brandingHead used to emit its own `classList.toggle('dark', …)` script. ./theme now resolves
	 *  the scheme (member's `gp-theme` choice > firm mode > OS) and stamps it on <html> server-side,
	 *  so a second script here could only ever disagree — it knows nothing about the member's choice.
	 *  These tests pin the boundary: branding supplies the PALETTES, theme picks WHICH one renders. */
	it('emits no script for any mode, and stays empty for mode-only branding', () => {
		for (const mode of ['light', 'dark', 'auto'] as const) {
			expect(brandingHead({ mode })).toBe('');
			expect(brandingHead({ mode })).not.toContain('classList');
		}
		expect(brandingHead({ mode: undefined })).toBe('');
	});

	it('still scopes each palette to its scheme, so themeHead only has to flip the class', () => {
		const head = brandingHead({
			mode: 'dark',
			colors: { light: { background: '#ffffff' }, dark: { background: '#111111' } }
		});
		expect(head).toContain(':root:root:not(.dark){--background:#ffffff}');
		expect(head).toContain('.dark:root:root{--background:#111111}');
	});

	it('leaves the design system’s own dark theme intact when a firm sets only a light palette', () => {
		const head = brandingHead({ colors: { light: { background: '#ffffff' } } });
		expect(head).not.toContain('.dark:root:root');
	});
});

describe('staffPortalBranding — what a platform save keeps', () => {
	it('keeps identity, mode and the accent colours; drops paper, type and material', () => {
		const out = staffPortalBranding({
			logo_url: 'https://cdn/logo.svg',
			icon_url: 'https://cdn/icon.svg',
			favicon_url: 'https://cdn/f.ico',
			mode: 'dark',
			login: { tagline: 'hello' },
			support: { email: 'a@b.c' },
			radius: '1rem',
			font: 'Inter',
			density: 'compact',
			finish: 'ledger',
			glass: { enabled: true },
			colors: {
				light: { primary: '#111111', background: '#fff0f0', brand: '#17a2a2', warning: '#b45309' }
			}
		});
		expect(out.logo_url).toBe('https://cdn/logo.svg');
		expect(out.icon_url).toBe('https://cdn/icon.svg');
		expect(out.mode).toBe('dark');
		expect(out.login?.tagline).toBe('hello');
		expect(out.colors?.light?.primary).toBe('#111111');
		expect(out.colors?.light?.brand).toBe('#17a2a2');
		expect(out.colors?.light?.background).toBeUndefined();
		expect(out.colors?.light?.warning).toBeUndefined();
		expect(out.radius).toBeUndefined();
		expect(out.font).toBeUndefined();
		expect(out.density).toBeUndefined();
		expect(out.finish).toBeUndefined();
		expect(out.glass).toBeUndefined();
	});

	it('accent keys are a closed subset of the full palette', () => {
		for (const key of FIXED_COLOUR_KEYS) expect(COLOUR_VARS_KEYS).toContain(key);
	});
});

describe('card designs', () => {
	it('belong to the design set: a template without its own falls back to the defaults', () => {
		const branding = {
			card_designs: { ISA: { finish: 'paper' as const } },
			templates: [
				{
					id: 'hnw',
					name: 'HNW',
					classification_slugs: ['hnw'],
					card_designs: { ISA: { finish: 'deep' as const } }
				},
				{ id: 'd2c', name: 'D2C', classification_slugs: ['d2c'] }
			]
		};
		expect(resolveClientPortalTemplate(branding, { classification: 'hnw' })?.card_designs).toEqual({
			ISA: { finish: 'deep' }
		});
		expect(
			resolveClientPortalTemplate(branding, { classification: 'd2c' })?.card_designs
		).toBeUndefined();
	});

	it('only lets an allowlisted artwork source into an inline style', () => {
		expect(isSafeCardArt('/_assets/card-art-0123456789abcdef.png')).toBe(true);
		expect(isSafeCardArt('https://cdn.example.com/card.png')).toBe(true);
		expect(isSafeCardArt("https://x/a.png'); } body{display:none")).toBe(false);
		expect(isSafeCardArt('javascript:alert(1)')).toBe(false);
		expect(isSafeCardArt(null)).toBe(false);
		expect(isCardColor('#112233')).toBe(true);
		expect(isCardColor('#abc')).toBe(true);
		expect(isCardColor('#112233;color:red')).toBe(false);
		expect(isCardColor(null)).toBe(false);
	});

	it('lists the five finishes', () => {
		expect(CARD_FINISHES).toEqual(['ink', 'brand', 'deep', 'tint', 'paper']);
	});
});
