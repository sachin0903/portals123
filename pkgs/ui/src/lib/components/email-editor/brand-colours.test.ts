import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import type { Branding, BrandingColors } from '@graphene/auth';
import { COLOUR_VARS_KEYS } from '@graphene/auth';
import {
	emailBrandColours,
	emailMastheadImage,
	emailMastheadPreview,
	EMAIL_SWATCH_KEYS
} from './brand-colours';

const branded = (light: BrandingColors, dark?: BrandingColors): Branding => ({
	colors: { light, ...(dark ? { dark } : {}) }
});

describe('emailBrandColours', () => {
	it('offers the firm’s colours in swatch order, brand accent first', () => {
		expect(
			emailBrandColours(branded({ foreground: '#111111', brand: '#0f766e', primary: '#1d4ed8' }))
		).toEqual(['#0f766e', '#1d4ed8', '#111111']);
	});

	it('yields NOTHING for an unbranded tenant, rather than a row of empty swatches', () => {
		const cases = [
			null,
			undefined,
			{},
			{ colors: {} },
			branded({}),
			branded({}, { brand: '#5eead4' })
		];
		for (const branding of cases as (Branding | null | undefined)[])
			expect(emailBrandColours(branding)).toEqual([]);
	});

	it('drops blanks, whitespace and nulls, and collapses duplicates whatever their hex case', () => {
		expect(
			emailBrandColours(
				branded({
					brand: '#0f766e',
					brand_foreground: '',
					primary: null,
					muted: '   ',
					foreground: '  #222222  '
				})
			)
		).toEqual(['#0f766e', '#222222']);
		expect(
			emailBrandColours(
				branded({ brand: '#0F766E', primary: '#0f766e', muted: '#0F766E', foreground: '#111111' })
			)
		).toEqual(['#0F766E', '#111111']);
	});

	it('keeps non-hex CSS colour values, which branding also permits', () => {
		// app/branding.py accepts oklch()/rgb()/hsl()/color-mix() and bare keywords too, so a filter assuming `#` would silently drop a real colour.
		expect(
			emailBrandColours(
				branded({
					brand: 'oklch(0.55 0.12 240)',
					primary: 'rgb(15, 23, 42)',
					muted: 'midnightblue'
				})
			)
		).toEqual(['oklch(0.55 0.12 240)', 'rgb(15, 23, 42)', 'midnightblue']);
	});

	it('reads the light palette only, never the dark one', () => {
		// A swatch writes a LITERAL and an email can't switch colour scheme, so a dark-scheme colour would land in the body as a near-black background the author thought was theirs.
		expect(
			emailBrandColours(branded({ brand: '#0f766e' }, { brand: '#5eead4', background: '#0b0f14' }))
		).toEqual(['#0f766e']);
	});
});

describe('EMAIL_SWATCH_KEYS', () => {
	it('names only real branding colour tokens', () => {
		// COLOUR_VARS_KEYS is the shared contract with app/branding.py's COLOUR_VARS — a typo'd or renamed key here is a swatch that silently never appears.
		const known = new Set(COLOUR_VARS_KEYS);
		for (const key of EMAIL_SWATCH_KEYS)
			expect(known.has(key), `'${key}' is not a branding colour token`).toBe(true);
	});

	it('stays a short curated set led by the brand accent, with no app chrome or status tone', () => {
		// All fourteen tokens would bury the two or three an email is built from; positive/negative/warning/info are status tones, not a colour choice.
		expect(EMAIL_SWATCH_KEYS[0]).toBe('brand');
		expect(EMAIL_SWATCH_KEYS.length).toBeLessThanOrEqual(12);
		expect(new Set(EMAIL_SWATCH_KEYS).size).toBe(EMAIL_SWATCH_KEYS.length);
		for (const key of EMAIL_SWATCH_KEYS) {
			expect(key, `'${key}' is app chrome`).not.toMatch(/^(sidebar|popover|input|ring)/);
			expect(key, `'${key}' is a portal status tone`).not.toMatch(
				/^(success|warning|info|destructive|positive|negative)/
			);
		}
	});
});

/** THE CROSS-LANGUAGE PIN for the brand token NAMES.
 *
 *  `app/email/transport.py::brand_tokens` is the only shape every `{{ brand.* }}` token in every shipped
 *  default and every palette block is written against, and the editor names those tokens as bare
 *  strings in its markup. Nothing compared the two, so renaming a key Python-side — `support_email`
 *  is the one that matters, it carries the footer of every firm's email — left the editor emitting a
 *  token that resolves to nothing, which renders blank and fails no gate.
 *
 *  ONE DIRECTION on purpose: every name the editor emits must be one `brand_tokens` returns. The
 *  converse is not a defect — Python may offer a token no palette block has taken up yet. */
describe('the brand tokens the editor emits', () => {
	const repo = fileURLToPath(new URL('../../../../../../', import.meta.url));
	const read = (relative: string): string => readFileSync(join(repo, relative), 'utf8');

	const pythonBrandKeys = (): Set<string> => {
		const source = read('apps/api/app/email/transport.py');
		const body = source.split('def brand_tokens')[1]?.split('\ndef ')[0] ?? '';
		return new Set([...body.matchAll(/^\s+"(\w+)": brand\./gm)].map((m) => m[1]));
	};

	const editorBrandPaths = (): Set<string> => {
		const sources = ['create.ts', 'merge-fields.ts', 'scopes.ts'].map((f) =>
			read(join('pkgs/ui/src/lib/components/email-editor', f))
		);
		return new Set([...sources.join('\n').matchAll(/brand\.(\w+)/g)].map((m) => m[1]));
	};

	it('parses both sides, so the assertion below cannot pass vacuously', () => {
		expect(pythonBrandKeys().size).toBe(5);
		expect(pythonBrandKeys()).toContain('support_email');
		expect(editorBrandPaths().size).toBeGreaterThan(2);
	});

	it('names only tokens brand_tokens actually returns', () => {
		const supplied = pythonBrandKeys();
		const unresolvable = [...editorBrandPaths()].filter((p) => !supplied.has(p)).sort();
		expect(unresolvable, 'these render blank at every send').toEqual([]);
	});

	it('covers the two the masthead and the footer cannot do without', () => {
		// `logo_url` picks the masthead's arm; `support_email` is the footer's whole right-hand side.
		for (const key of ['logo_url', 'support_email']) expect(editorBrandPaths()).toContain(key);
	});
});

/** THE PIN for the masthead resolver, which declares itself a mirror of `app/email/transport.py` and had no
 *  test at all. The stated cost of divergence is previewing a different picture from the one the send
 *  resolves — and it is silent, because both answers are a plausible image. */
describe('emailMastheadImage mirrors app/email/transport.py::masthead_image', () => {
	const HTTPS_ICON = 'https://cdn.test/icon.png';
	const HTTPS_LOGO = 'https://cdn.test/logo.png';
	const DATA = 'data:image/png;base64,AAAA';

	const brand = (
		icon_url: string | null,
		logo_url: string | null,
		logo_variant?: string
	): Branding => ({ icon_url, logo_url, ...(logo_variant ? { logo_variant } : {}) }) as Branding;

	it('lets the firm’s stated treatment decide, with the other image as the fallback', () => {
		// variant `icon` -> icon first; anything else -> wordmark first. Both fall through.
		expect(emailMastheadImage(brand(HTTPS_ICON, HTTPS_LOGO, 'icon'))).toBe(HTTPS_ICON);
		expect(emailMastheadImage(brand(HTTPS_ICON, HTTPS_LOGO))).toBe(HTTPS_LOGO);
		expect(emailMastheadImage(brand(null, HTTPS_LOGO, 'icon'))).toBe(HTTPS_LOGO);
		expect(emailMastheadImage(brand(HTTPS_ICON, null))).toBe(HTTPS_ICON);
	});

	it('refuses a data: URI on BOTH arms, so an inlined logo falls through rather than arriving broken', () => {
		expect(emailMastheadImage(brand(DATA, HTTPS_LOGO, 'icon'))).toBe(HTTPS_LOGO);
		expect(emailMastheadImage(brand(HTTPS_ICON, DATA))).toBe(HTTPS_ICON);
		expect(emailMastheadImage(brand(DATA, DATA))).toBeNull();
		expect(emailMastheadImage(brand(null, null))).toBeNull();
	});

	it('resolves in the order the Python does, read from its source', () => {
		const source = readFileSync(
			join(
				fileURLToPath(new URL('../../../../../../', import.meta.url)),
				'apps/api/app/email/transport.py'
			),
			'utf8'
		);
		const body = source.split('def masthead_image')[1]?.split('\ndef ')[0] ?? '';
		expect(body).toContain('_https_only(branding.icon_url)');
		expect(body).toContain('_https_only(branding.logo_url)');
		// The whole rule on one line, so the arm order and the variant test are both pinned.
		expect(body.replace(/\s+/g, ' ')).toContain(
			'return (icon or wordmark) if branding.logo_variant == "icon" else (wordmark or icon)'
		);
		// And `logo_dark_url` is never RESOLVED: a mail client cannot reliably swap an image by
		// colour scheme, so the light one is the safe single answer. The docstring names it to say
		// so, which is why this asks about the resolution rather than the mention.
		expect(body).not.toContain('_https_only(branding.logo_dark_url)');
	});

	it('accepts a relay path, because the LIVE Python resolves one', () => {
		// `masthead_image` above is the https-only shape and is not what a send calls: `brand_for`
		// goes through `_email_asset_urls`, which resolves a stored `/_assets/{name}` against the
		// firm's portal host first. Read from the same source, so this side cannot drift into
		// reporting "no logo" for the way every firm's logo is actually stored.
		const source = readFileSync(
			join(
				fileURLToPath(new URL('../../../../../../', import.meta.url)),
				'apps/api/app/email/transport.py'
			),
			'utf8'
		);
		const live = source.split('async def _email_asset_urls')[1]?.split('\nasync def ')[0] ?? '';
		expect(live).toContain('storage.public_asset_name');
		expect(live).toContain('storage.PUBLIC_ASSET_PREFIX');

		const RELAY = '/_assets/logo-0123456789abcdef.png';
		expect(emailMastheadImage(brand(null, RELAY))).toBe(RELAY);
		expect(emailMastheadImage(brand(RELAY, null, 'icon'))).toBe(RELAY);
		// …and a path the relay would refuse is still no logo.
		expect(emailMastheadImage(brand(null, '/_assets/logo.png'))).toBeNull();
		expect(emailMastheadImage(brand(null, '/files/tenant/logo.png'))).toBeNull();
	});

	it('does not warn that a firm’s own logo will fail to arrive', () => {
		// `mailable` is what an editor surface would render a "this will not go out" notice from. It
		// said false for every relay-stored logo, which is every branded firm in the product.
		const RELAY = '/_assets/logo-0123456789abcdef.png';
		expect(emailMastheadPreview(brand(null, RELAY))).toEqual({ src: RELAY, mailable: true });
		// A data: URI still cannot arrive — shown on the canvas, and honestly flagged.
		expect(emailMastheadPreview(brand(null, DATA))).toEqual({ src: DATA, mailable: false });
	});
});
