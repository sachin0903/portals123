/**
 * WHOSE NAME AND LOGO THE EDITOR PREVIEWS. This surface edits ANY tenant's email, so every brand value
 * it hands the shared editor must come from the TARGET tenant — never from this portal, which wears the
 * Graphene product look and is deliberately not tenant-themed.
 *
 * WHY A SOURCE SCAN, the `mount.test.ts` idiom: `page.data.appName` is a valid expression that this
 * portal's layout simply never sets, so it type-checks, renders, and silently arrives `null`. The only
 * symptom was a masthead previewing as an empty dashed slot for every firm without a logo — which is
 * every firm until branding storage lands, and reads as "the masthead is broken" rather than as a
 * missing value.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const RAW = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
/** Comments stripped: they name the very expression being scanned for. */
const PAGE = RAW.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

describe('brand identity comes from the target tenant', () => {
	it('never reads this portal own app name or branding', () => {
		expect(PAGE).not.toContain('page.data.appName');
		expect(PAGE).not.toContain('page.data.branding');
	});

	it('gives the masthead wordmark a name to fall back to', () => {
		// The wordmark arm renders `brand.name` at send. With no name the preview paints nothing.
		expect(PAGE).toMatch(/brandName=\{senderName\}/);
		expect(PAGE).toMatch(/data\.tenant\?\.name/);
	});

	it('takes the logo from the tenant branding the loader fetched', () => {
		expect(PAGE).toMatch(/emailMastheadPreview\(data\.branding\)\.src/);
		// AND QUALIFIES IT BY TENANT. A stored logo is a bare `/_assets/{name}`, and this portal's
		// relay resolves the tenant from the request HOST — which here names the operations tenant,
		// so every firm's object 404'd and the canvas showed a broken slot with no wordmark behind
		// it (a present URL suppresses the fallback).
		expect(PAGE).toMatch(
			/tenantRelayPath\(\s*emailMastheadPreview\(data\.branding\)\.src,\s*page\.params\.uid\s*\)/
		);
		// ONE brand value, and this is it. There was a second prop here (`brandIconUrl`, feeding a
		// second block that placed `brand.icon_url`); `emailMastheadPreview` already returns the
		// firm's MARK when the firm's own `logo_variant` asks for it, so the second prop could only
		// ever agree with this one or override the firm's stated treatment.
		expect(PAGE).not.toContain('brandIconUrl');
		expect(PAGE).not.toContain('emailBrandIcon');
	});

	it('keeps the From name and the wordmark on ONE value, so they cannot disagree', () => {
		const derived = /const senderName = \$derived\(([\s\S]*?)\);/.exec(PAGE)?.[1] ?? '';
		// THE TENANT'S OWN NAME, whichever tenant it is — including the model preset, which used to
		// be special-cased to the placeholder. An operator is editing a ROW, and the masthead is
		// where the canvas says whose row it is; a placeholder there is the surface declining to say
		// what it is showing.
		expect(derived).toContain('data.tenant?.name');
		expect(derived).not.toContain('data.isModel');
		// The placeholder survives for the case it was written for: a name that is missing or blank.
		expect(derived).toContain("'Your firm'");
		// ONE value feeds both, so the From line and the masthead cannot disagree about whose email
		// this is — the property this file exists for.
		expect(PAGE).toMatch(/brandName=\{senderName\}/);
		expect(PAGE).toMatch(/\{senderName\}/);
	});
});
