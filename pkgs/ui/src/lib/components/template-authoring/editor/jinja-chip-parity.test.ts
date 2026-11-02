/**
 * The document canvas and the email canvas must mark a `{{ token }}` the same way.
 *
 * Both are classic scripts served to their editor's iframe (`grapesjs-jinja-vars.js` and
 * `gjs-email-jinja-hl.js`), so neither can import the other and nothing but a test can hold them
 * together. They already agreed on the TECHNIQUE -- an absolutely-positioned overlay measuring each
 * token's client rects, which is what keeps highlighting off the layout -- and disagreed on the
 * look: the document one washed a violet tint over the token, the same defect the email one had
 * measured and fixed (1.19:1 against its own fill, where un-chipped copy is 2.60:1).
 *
 * Regex LITERALS throughout, deliberately: a pattern built as a template literal silently loses its
 * backslashes (`\s` in a template literal is the letter `s`), and a regex that matches nothing here
 * reads as a missing declaration rather than as a broken test.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const DOC = 'grapesjs-jinja-vars.js';
const EMAIL = 'gjs-email-jinja-hl.js';

const script = (name: string): string =>
	// The canvas scripts are served by the APP, not this package — the editor moved to `pkgs/ui` so
	// the admin portal can mount it, and its GrapesJS canvas still loads these by URL from whichever
	// app is hosting it. Read from `apps/platform` because that is the app that owns them.
	readFileSync(
		fileURLToPath(new URL(`../../../../../../../apps/platform/static/${name}`, import.meta.url)),
		'utf8'
	);

const FILL = /CHIP_FILL\s*=\s*'(#[0-9a-fA-F]{3,8})'/;
const RING = /CHIP_RING\s*=\s*'(#[0-9a-fA-F]{3,8})'/;
/** The overlay root's own inline style, per script. */
const ROOT = {
	[DOC]: /hlRoot\.style\.cssText\s*=\s*'([^']*)'/,
	[EMAIL]: /layer\.style\.cssText\s*=\s*'([^']*)'/
} as const;

function one(re: RegExp, src: string, what: string): string {
	const m = re.exec(src);
	expect(m, what).not.toBeNull();
	return m![1];
}

describe('the two canvases mark a variable the same way', () => {
	it('found both scripts', () => {
		// Guards the paths: an unreadable file would make every assertion below vacuous.
		expect(script(DOC).length).toBeGreaterThan(1000);
		expect(script(EMAIL).length).toBeGreaterThan(1000);
	});

	it('uses one pair of chip colours', () => {
		for (const [label, re] of [
			['CHIP_FILL', FILL],
			['CHIP_RING', RING]
		] as const) {
			const doc = one(re, script(DOC), `${DOC} no longer declares ${label} as a hex`);
			const email = one(re, script(EMAIL), `${EMAIL} no longer declares ${label} as a hex`);
			expect(doc, `${label} drifted between the document and email canvases`).toBe(email);
		}
	});

	it('darkens rather than overprints, on both sides', () => {
		// `multiply` can only darken, so the backdrop dims and dark text does not -- which is what
		// lets the fill be a real grey instead of a near-white that says nothing.
		for (const name of [DOC, EMAIL]) {
			expect(script(name), name).toContain('mix-blend-mode:multiply');
		}
	});

	it('gives the overlay root no numeric z-index', () => {
		// THE TRAP, and it is silent: a numeric z-index makes a stacking context, `mix-blend-mode`
		// blends only within its own, so the chips composite against the overlay's transparent backdrop
		// and paint as flat OPAQUE grey over the token -- worse than no highlight at all. `z-index:
		// auto` makes no context, and the last positioned child of the body wins.
		for (const name of [DOC, EMAIL] as const) {
			const css = one(ROOT[name], script(name), `${name}: overlay root's inline style not found`);
			expect(css, `${name} sets a z-index on the overlay root`).not.toMatch(/z-index:\s*\d/);
			expect(css, name).toContain('pointer-events:none');
		}
	});
});
