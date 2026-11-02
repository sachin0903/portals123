import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Pins for the floating chrome preset.
 *
 * The rule this defends is `docs/design-language.md`'s: **platform and admin are one product and
 * must be indistinguishable in chrome; only their content differs.** This file shipped to the
 * platform portal alone as a look-and-see overlay, and that is exactly how the two staff surfaces
 * came to differ — admin kept a hairline box around every card, KPI tile, panel and shell edge
 * that platform had dropped, so moving between them read as moving between two applications.
 *
 * A stylesheet import escapes every type gate: dropping one of these lines compiles, type-checks
 * and lints clean, and shows up only as "the admin portal looks wrong" weeks later. Hence a test
 * that reads the three app.css files off disk.
 */
const ROOT = join(import.meta.dirname, '..', '..', '..', '..');
const appCss = (app: string) =>
	readFileSync(join(ROOT, 'apps', app, 'src', 'app.css'), 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '');

/** The two staff portals. The client portal keeps the glass look and is asserted separately. */
const STAFF_PORTALS = ['platform', 'admin'];

describe('the floating chrome preset', () => {
	it.each(STAFF_PORTALS)('is imported by the %s portal', (app) => {
		expect(appCss(app)).toContain("@import '@graphene/ui/chrome.css';");
	});

	it.each(STAFF_PORTALS)('is imported AFTER the tokens it reassigns in the %s portal', (app) => {
		// It only reassigns tokens theme.css/sharp.css declare, so importing it first is a no-op
		// that leaves the boxed look in place — silently, and with the import plainly present.
		const css = appCss(app);
		const theme = css.indexOf('@graphene/ui/theme.css');
		const sharp = css.indexOf('@graphene/ui/sharp.css');
		const chrome = css.indexOf('@graphene/ui/chrome.css');
		expect(theme).toBeGreaterThanOrEqual(0);
		expect(sharp).toBeGreaterThanOrEqual(0);
		expect(chrome).toBeGreaterThan(theme);
		expect(chrome).toBeGreaterThan(sharp);
	});

	it('is NOT imported by the client portal, which keeps the glass look', () => {
		const css = appCss('client');
		expect(css).not.toContain('@graphene/ui/chrome.css');
		// The same reason it skips sharp.css — the two go together.
		expect(css).not.toContain('@graphene/ui/sharp.css');
	});

	it('is exported from the package, or neither app can import it', () => {
		const pkg = JSON.parse(
			readFileSync(join(import.meta.dirname, '..', '..', 'package.json'), 'utf-8')
		);
		expect(pkg.exports['./chrome.css']).toBe('./src/styles/chrome.css');
	});

	it('keeps the live toggle, which is the only way to compare the two looks', () => {
		const css = readFileSync(join(import.meta.dirname, 'chrome.css'), 'utf-8');
		expect(css).toContain("[data-chrome='carded']");
		// `:root:root:root` beats a tenant's inline `:root:root` branding block (pkgs/auth
		// brandingHead). A plain `:root` would lose to a firm that brands its border colour.
		expect(css).toContain(':root:root:root');
	});

	it('removes the card box rather than merely restyling it', () => {
		const css = readFileSync(join(import.meta.dirname, 'chrome.css'), 'utf-8').replace(
			/\/\*[\s\S]*?\*\//g,
			''
		);
		expect(css).toMatch(/--surface-card-border:\s*0;/);
		// A hair off white, chroma 0 — a hue here is a tint. Staff portals no longer emit
		// `--background`.
		expect(css).toMatch(/--background:\s*oklch\(0\.98\s+0\s+0\)/);
		expect(css).toMatch(/--glass-border:\s*transparent;/);
		expect(css).toMatch(/--sidebar-border:\s*transparent;/);
		expect(css).toMatch(/--elev-1:\s*0\s+0\s+#0000;/);
		// Dark is the same fill split, chroma 0 — not the theme's navy wash. The notch is
		// wider than light (0.12 / 0.19): two mid-charcoals collapse into one slab.
		expect(css).toMatch(
			/\.dark:not\(\[data-chrome='carded'\]\)[\s\S]*--background:\s*oklch\(0\.12\s+0\s+0\)/
		);
		expect(css).toMatch(
			/\.dark:not\(\[data-chrome='carded'\]\)[\s\S]*--card:\s*oklch\(0\.19\s+0\s+0\)/
		);
		expect(css).toMatch(
			/\.dark:not\(\[data-chrome='carded'\]\)[\s\S]*--foreground:\s*oklch\(0\.93\s+0\s+0\)/
		);
		expect(css).toMatch(
			/\.dark:not\(\[data-chrome='carded'\]\)[\s\S]*--hero-base:\s*oklch\(0\.22\s+0\s+0\)/
		);
	});

	it('defines widgets by fill, not a halo, and keeps leftover panels borderless', () => {
		const css = readFileSync(join(import.meta.dirname, 'chrome.css'), 'utf-8').replace(
			/\/\*[\s\S]*?\*\//g,
			''
		);
		// sharp.css squares corners to 0.25rem; 0.5rem lifts the sheet without a 1rem pill on a
		// listing card.
		expect(css).toMatch(/--radius:\s*0\.5rem;/);
		expect(css).toMatch(/--glass-panel-shadow:\s*var\(--elev-1\);/);
		expect(css).toContain('.glass-panel');
		expect(css).toMatch(/\[data-slot='card'\]/);
		expect(css).toMatch(/overflow:\s*visible;/);
		expect(css).toMatch(/\.bg-card\.border/);
		expect(css).toMatch(/\.table-scroll-shadows/);
		// Dark mode used to fake an edge with `inset 0 1px 0` — still a line around the widget.
		expect(css).not.toMatch(/inset\s+0\s+1px\s+0/);
	});
});
