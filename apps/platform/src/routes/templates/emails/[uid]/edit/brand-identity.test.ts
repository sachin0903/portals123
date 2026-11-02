/**
 * WHOSE NAME THE EDITOR PREVIEWS. The From name and the masthead wordmark are the same claim — whose
 * name is on this email — so this page hands the shared editor ONE value for both. A firm's name is
 * `appName`, resolved per host by the API off `auth.tenants.name`; the portal branding payload carries
 * no name at all, so a `branding?.name` read is always undefined and only misleads the next reader.
 *
 * WHY A SOURCE SCAN: `App.PageData` is commented out in `app.d.ts`, so `page.data` is loose and a
 * missing key type-checks as `any` and arrives `undefined`. The only symptom of a split was the From
 * name showing the placeholder beside a masthead that painted nothing — which reads as "the masthead
 * is broken" rather than as a missing value.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const RAW = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
/** Comments stripped: they name the very expression being scanned for. */
const PAGE = RAW.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

describe('brand identity is one value', () => {
	it('keeps the From name and the wordmark on ONE value, so they cannot disagree', () => {
		const derived = /const senderName = \$derived\(([\s\S]*?)\);/.exec(PAGE)?.[1] ?? '';
		expect(derived).toContain('page.data.appName');
		expect(derived).toContain("'Your firm'");
		expect(PAGE).toMatch(/brandName=\{senderName\}/);
	});

	it('never reads a name off the branding payload, which has none', () => {
		expect(PAGE).not.toContain('branding?.name');
	});

	it('takes the logo from the branding the layout resolved', () => {
		expect(PAGE).toMatch(/brandLogoUrl=\{emailMastheadPreview\(data\.branding\)\.src\}/);
	});
});
