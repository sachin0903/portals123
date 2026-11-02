import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CANVAS_HEIGHT, DEFAULT_ARTICLE_CSS } from './create.js';

/**
 * GrapesJS's core stylesheet is not optional chrome. It is what sizes the component badge and the
 * component toolbar, and what makes the canvas — absolutely positioned, sized off `--gjs-*-width` —
 * take the host's width. Without it the toolbar's `select parent` and `move` SVGs render at their
 * intrinsic size, as two huge black arrows over the page, and the article collapses into a narrow
 * clipped column. That is what the editor shipped as, and it read as a broken product rather than as
 * a missing stylesheet.
 *
 * It cannot be `import`ed (LightningCSS rejects GrapesJS's IE `filter` syntax), so it is served as a
 * static asset and injected as a `<link>` — which means the failure is a 404 at run time and nothing
 * a type gate or a render test can see. Hence this scan: the link, and the file it points at.
 */
const ROOT = join(import.meta.dirname, '..', '..', '..', '..', '..', '..');
const create = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');

describe('article editor chrome', () => {
	it('injects the GrapesJS core stylesheet before initialising', () => {
		expect(create).toContain("link.href = '/grapesjs.css'");
		// Same element id as the email editor's copy, so two editors in one session share one link.
		expect(create).toContain("link.id = 'gjs-css'");
		expect(create.indexOf('ensureArticleEditorStyles();')).toBeLessThan(
			create.indexOf('grapesjs.init(')
		);
	});

	it('ships the stylesheet in every app that mounts the editor', () => {
		// The content CMS is a platform-portal surface; admin is listed because it serves the same
		// asset for the email editor, so a shared-asset cleanup cannot quietly take one of them.
		for (const app of ['platform', 'admin']) {
			expect(existsSync(join(ROOT, 'apps', app, 'static', 'grapesjs.css')), app).toBe(true);
		}
	});

	it('keeps editor chrome out of the saved article', () => {
		// `canvasCss` is injected into the canvas document and is not part of `getCss()`. The sheet's
		// surround belongs there and nowhere else: anything in the stylesheet is inlined onto the
		// published article by the sanitiser, so a canvas drop-shadow would follow the article into
		// every client portal.
		expect(create).toMatch(/canvasCss: \[/);
		const articleCss = create.match(/DEFAULT_ARTICLE_CSS = `[\s\S]*?`;/)?.[0] ?? '';
		expect(articleCss, 'the DEFAULT_ARTICLE_CSS literal was not found').not.toBe('');
		expect(articleCss).not.toContain('box-shadow');
	});

	it('gives the canvas a height it can resolve', () => {
		// GrapesJS writes `config.height` as an INLINE style on the host, so a percentage resolves
		// against whatever wraps the editor — a padded `min-height` box here, i.e. nothing definite.
		// It resolved to `auto`, the absolutely-positioned canvas contributed no height, and every box
		// came out ZERO TALL: a blank grey panel with the article correctly laid out at 760x720 inside
		// an iframe nobody could see. Measured in Chromium, not deduced.
		expect(CANVAS_HEIGHT).not.toContain('%');
		expect(CANVAS_HEIGHT).toMatch(/^\d+(\.\d+)?(rem|px|em|vh)$/);
		expect(create).toContain('height: CANVAS_HEIGHT');
		// And the host must take its height from the same constant, since GrapesJS overwrites that
		// element's inline height with the configured one.
		const component = readFileSync(join(import.meta.dirname, 'article-editor.svelte'), 'utf8');
		expect(component).toContain('style:height={CANVAS_HEIGHT}');
	});

	it('never round-trips the article stylesheet through GrapesJS', () => {
		// GrapesJS re-serialises a parsed rule through the CSSOM, which expands shorthands into
		// longhands — and a shorthand carrying `var()` cannot be expanded, so it is DROPPED, silently.
		// `background: var(--background)` computed to transparent and `border-left: 3px solid
		// var(--primary)` to 0px: no sheet background, no pull-quote rule, no callout border, in the
		// editor and in the published article alike, because publish inlines the same mangled CSS.
		// The stylesheet is the design system's, not the item's, so it goes into the canvas verbatim.
		// Matched as a CALL, not as the word: the comment above the `canvasCss` block names
		// `editor.setStyle` to explain why it is not used, and a file-wide scan reads that as a
		// violation — the false positive `switch-columns.test.ts` records hitting.
		expect(create).not.toMatch(/editor\.setStyle\(/);
		expect(create).toContain('DEFAULT_ARTICLE_CSS,');
		expect(create).toContain('getCss: () => DEFAULT_ARTICLE_CSS');
		// The trap is only avoidable because the stylesheet bypasses the CSSOM; the tokens are the
		// whole point of it, so this asserts they are still there to be preserved.
		expect(DEFAULT_ARTICLE_CSS).toContain('var(--foreground)');
	});

	it('surfaces a failed init instead of showing an empty canvas', () => {
		// Without a catch a rejected init leaves the skeleton up for ever and the reason in a console
		// nobody opens, which reads as a broken feature rather than a broken load.
		const component = readFileSync(join(import.meta.dirname, 'article-editor.svelte'), 'utf8');
		expect(component).toContain('.catch(');
		expect(component).toMatch(/failure = error instanceof Error/);
	});

	it('does not stretch a small image to the column width', () => {
		// A Word document's images are mostly small graphics; `width: 100%` blew each one up to the
		// full 616px column, which is most of what made an imported document look wrong.
		const img = create.match(/\.article-shell img \{[^}]*\}/)?.[0] ?? '';
		expect(img).toContain('width: auto');
		expect(img).toContain('max-width: 100%');
	});
});
