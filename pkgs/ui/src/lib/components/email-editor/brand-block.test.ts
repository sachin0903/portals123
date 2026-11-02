/**
 * ONE brand block, and an unset one that reads as a PICTURE.
 *
 * This file replaces `brand-icon.test.ts`, which pinned a SECOND brand block into place. That block
 * placed `brand.icon_url` — the firm's square mark — beside the masthead placing `brand.logo_url`,
 * and the argument for it was wrong in a way worth recording, because it is the kind of wrong that
 * reads as a feature: `brand.logo_url` is not "the wordmark", it is **the resolved masthead**
 * (`app/email/transport.py::_email_asset_urls` — `(icon or wordmark)` when the firm's own
 * `logo_variant` says `icon`, `(wordmark or icon)` otherwise). So the mark was never unreachable;
 * the second block simply spelled it a second way, and that way IGNORED the firm's stated
 * treatment. Two palette entries for one idea, one of which quietly overrode a firm's own choice.
 *
 * What is pinned here is therefore a COUNT as much as a shape: exactly one block whose content is
 * the brand image. A future "Brand icon"/"Brand mark"/"Logo (small)" fails this file.
 *
 * The second half is the empty state. An author with no logo uploaded used to get a dashed
 * rectangle the width of a logo — which says "something is missing" and nothing about what, and
 * reads as a fault in the editor rather than as unset branding. It now paints a picture glyph.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { BRAND_LOGO_HTML, EMAIL_BLOCKS, previewBrandMasthead } from './create.js';
import { BRAND_LOGO_FIELD } from './merge-fields.js';
import { BRAND_ARM_ATTR, BRAND_ARM_LOGO, FALLBACK_ATTR } from './conditions/attrs.js';

const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');
const CANVAS_CSS = [
	'apps/platform/static/gjs-email-canvas.css',
	'apps/admin/static/gjs-email-canvas.css'
];
const repoRoot = new URL('../../../../../../', import.meta.url);

describe('one brand block', () => {
	it('is the only brand block in the palette', () => {
		const brand = EMAIL_BLOCKS.filter((b) => b.id.startsWith('brand'));
		expect(brand.map((b) => b.id)).toEqual(['brand-logo']);
		expect(brand[0]?.label).toBe('Brand logo');
		expect(brand[0]?.category).toBe('Media');
	});

	it('places the RESOLVED masthead, never `icon_url` directly', () => {
		// The whole reason one block suffices. A block on `brand.icon_url` would hand a firm that
		// asked for its wordmark the square mark instead, with no way to tell from the canvas.
		expect(BRAND_LOGO_HTML).toContain(`src="{{ ${BRAND_LOGO_FIELD} }}"`);
		expect(BRAND_LOGO_HTML).not.toContain('brand.icon_url');
		expect(EMAIL_BLOCKS.map((b) => String(b.content ?? '')).join('\n')).not.toContain(
			'brand.icon_url'
		);
	});

	it('keeps the masthead identity every stored template already carries', () => {
		// `data-gjs-name` is what an author reads in the layer tree, and `data-brand-arm` is what the
		// canvas paints behind and what `masthead-image` recognises on parse. A rename here orphans
		// the masthead in every template published before it.
		expect(BRAND_LOGO_HTML).toContain('data-gjs-name="Brand logo"');
		expect(BRAND_LOGO_HTML).toContain(`${BRAND_ARM_ATTR}="${BRAND_ARM_LOGO}"`);
	});

	it('still falls back to the firm name at SEND, as a property of the one image', () => {
		// Deleting the second block must not delete the fallback: a firm with no logo has to wear
		// something, and `data-fallback` is how that arm travels without a `{% if %}` on the canvas.
		expect(BRAND_LOGO_HTML).toContain(FALLBACK_ATTR);
	});

	it('leaves no icon arm behind in the editor or either canvas sheet', () => {
		expect(source).not.toContain('BRAND_ICON_HTML');
		expect(source).not.toContain('has-brand-icon');
		expect(source).not.toContain("id: 'brand-icon'");
		for (const rel of CANVAS_CSS) {
			const css = readFileSync(new URL(rel, repoRoot), 'utf8');
			// A COMMENT may name it (the sheets explain the deletion); a RULE may not.
			expect(css).not.toMatch(/^\s*body[^{]*has-brand-icon/m);
			expect(css).not.toMatch(/^\s*body[^{]*\[data-brand-arm='icon'\]/m);
		}
	});
});

describe('the unset brand slot', () => {
	/** The half of `previewBrandMasthead` the canvas rules key off, through a fake editor. */
	function paint(logoUrl: string | null, name: string | null) {
		const body = { classList: new Set<string>(), props: {} as Record<string, string> };
		const editor = {
			Canvas: {
				getBody: () => ({
					classList: {
						toggle: (c: string, on: boolean) =>
							on ? body.classList.add(c) : body.classList.delete(c),
						// `add` too, though nothing calls it today: a fake that implements less than the
						// DOM throws where the browser would not, and the failure reads as a bug in the
						// code rather than as a gap in the fake. That happened once already here.
						add: (c: string) => body.classList.add(c)
					},
					style: { setProperty: (k: string, v: string) => (body.props[k] = v) }
				})
			},
			on: () => {},
			off: () => {}
		};
		previewBrandMasthead(editor as never, logoUrl, name);
		return { classes: [...body.classList], props: body.props };
	}

	it('paints a firm logo when there is one', () => {
		const out = paint('/_assets/logo-0123456789abcdef.png', 'Ridgeway');
		expect(out.classes).toContain('has-brand-logo');
		expect(out.classes).not.toContain('brand-logo-is-fallback');
		expect(out.props['--brand-logo-preview']).toBe('url("/_assets/logo-0123456789abcdef.png")');
	});

	it('paints the firm name when there is no logo', () => {
		const out = paint(null, 'Ridgeway');
		expect(out.classes).toContain('has-brand-logo');
		expect(out.classes).toContain('brand-logo-is-fallback');
		expect(out.props['--brand-logo-preview']).toMatch(/^url\("data:image\/svg\+xml;base64,/);
	});

	it('paints NOTHING of the firm when it has neither — which is the state the CSS dresses', () => {
		const out = paint(null, '');
		expect(out.classes).not.toContain('has-brand-logo');
		expect(out.props['--brand-logo-preview']).toBe('none');
	});

	it('dresses that state as an image placeholder, not a bare grey box', () => {
		for (const rel of CANVAS_CSS) {
			const css = readFileSync(new URL(rel, repoRoot), 'utf8');
			const rule =
				/body:not\(\.has-brand-logo\) \[data-brand-arm='logo'\] \{([\s\S]*?)\n\}/.exec(css)?.[1] ??
				'';
			expect(rule, `no unset rule in ${rel}`).not.toBe('');
			// A PICTURE: an embedded glyph, drawn from the left like everything else on the sheet.
			expect(rule).toMatch(/background:\s*url\('data:image\/svg\+xml;base64,/);
			expect(rule).toContain('no-repeat left center');
			// The dashed hint stays — it is a placeholder, not artwork — and so does the glyph removal
			// that keeps the browser's torn-page icon out of it.
			expect(rule).toContain('outline: 1px dashed');
			expect(rule).toContain("content: ''");
		}
	});

	it('is re-applied by the caller when the resolved brand changes', () => {
		// The paint is a one-shot: its own hooks are `load` / `canvas:frame:load`, which are about
		// the FRAME's lifecycle and say nothing about the props. So a firm that rebranded while the
		// editor was open kept the old logo on the canvas until a reload. The mount call stays where
		// it is — the order in that block is load-bearing — and the effect covers changes only.
		const editor = readFileSync(new URL('./email-editor.svelte', import.meta.url), 'utf8');
		const effect = /\$effect\(\(\) => \{([\s\S]*?)\n\t\}\);/g;
		const brandEffect = [...editor.matchAll(effect)]
			.map((m) => m[1])
			.find((body) => body.includes('previewBrandMasthead'));
		expect(brandEffect, 'no effect re-applies the brand preview').toBeTruthy();
		// Guarded on the VALUES: the first run after mount must be a no-op, and a re-render that
		// changes neither half must not tear a working preview down and rebuild it.
		expect(brandEffect).toContain('appliedBrand');
		expect(brandEffect).toMatch(/key === appliedBrand/);
		// And it disposes what it replaces, or each change leaks a pair of editor listeners.
		expect(brandEffect).toContain('stopBrandPreview?.()');
	});

	it('removes the replaced image on the PAINTED arm, or the logo never shows', () => {
		// THE GREY BOX, pinned. `create.ts` writes a stand-in over the `src` of every token image
		// (a merge token is not a URL), and a replaced element paints its image on top of its own
		// background — so the firm's logo, supplied here as a `background`, was drawn and then
		// covered. All three arms of this rule need the replaced image gone; this was the only one
		// without a `content` declaration, which is why a firm WITH a logo saw a flat rectangle.
		for (const rel of CANVAS_CSS) {
			const css = readFileSync(new URL(rel, repoRoot), 'utf8');
			const rule =
				/body\.has-brand-logo \[data-brand-arm='logo'\] \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
			expect(rule, `no painted rule in ${rel}`).not.toBe('');
			expect(rule).toMatch(/\bcontent:/);
			expect(rule).toContain('background: var(--brand-logo-preview)');
			// `contain`, so a lockup keeps its aspect ratio in a 120x28 slot.
			expect(rule).toContain('/ contain');
		}
	});

	it('keeps the two canvas sheets byte-identical', () => {
		const [platform, admin] = CANVAS_CSS.map((rel) => readFileSync(new URL(rel, repoRoot), 'utf8'));
		expect(admin).toBe(platform);
	});
});
