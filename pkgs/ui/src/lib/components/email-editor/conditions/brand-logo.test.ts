import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { BRAND_LOGO_HTML, EMAIL_BLOCKS, fallbackWordmarkImage } from '../create';
import { BRAND_LOGO_FIELD } from '../merge-fields.js';
import { compileConditionals } from './compile';
import { decodeFallback } from './attrs';
import { fieldOptions } from './fields';
import { conditionOnlyFieldsFor } from '../merge-fields';

/** The Brand logo block: the masthead as ONE image with a FALLBACK. Nothing conditional is authored;
 *  `compile.ts` expands the pair into the same two-arm Jinja `email_defaults` sends. */
const HERE = import.meta.dirname;
const create = readFileSync(join(HERE, '..', 'create.ts'), 'utf8');
// fileURLToPath, not `.pathname`: on Windows the latter yields `/C:/…`, which `join` then doubles.
const REPO_ROOT = fileURLToPath(new URL('../../../../../../../', import.meta.url));
const canvasCss = readFileSync(
	join(REPO_ROOT, 'apps', 'platform', 'static', 'gjs-email-canvas.css'),
	'utf8'
);

it('finds the repo root, so the canvas assertions cannot pass vacuously', () => {
	expect(canvasCss.length).toBeGreaterThan(0);
});

describe('the block', () => {
	it('is in the palette', () => {
		const block = EMAIL_BLOCKS.find((b) => b.id === 'brand-logo');
		expect(block, 'no brand-logo block in the palette').toBeTruthy();
		expect(block?.label).toBe('Brand logo');
	});

	it('does NOT activate on drop', () => {
		const block = EMAIL_BLOCKS.find((b) => b.id === 'brand-logo');
		expect(block?.activate).toBeUndefined();
	});

	it('is ONE image, with no condition and no hand-written Jinja', () => {
		expect(BRAND_LOGO_HTML.match(/<img/g)).toHaveLength(1);
		expect(BRAND_LOGO_HTML).not.toContain('<div');
		expect(BRAND_LOGO_HTML).not.toContain('{% if');
		expect(BRAND_LOGO_HTML).not.toContain('{% else');
		expect(BRAND_LOGO_HTML).not.toContain('data-if');
		expect(BRAND_LOGO_HTML).not.toContain('data-gjs-type="brand-logo"');
		expect(BRAND_LOGO_HTML).toContain('src="{{ brand.logo_url }}"');
	});

	it('carries its second outcome as a FALLBACK on the image', () => {
		// Decoded, not pattern-matched: an attribute that merely looked right would decode to null.
		const raw = /data-fallback="([^"]*)"/.exec(BRAND_LOGO_HTML)?.[1];
		expect(raw, 'no fallback on the image').toBeTruthy();
		const fallback = decodeFallback(raw);
		expect(fallback?.field).toBe(BRAND_LOGO_FIELD);
		expect(fallback?.text).toBe('{{ brand.name }}');
		expect(fallback?.tag).toBe('div');
		expect(fallback?.style).toContain('text-transform:uppercase');
	});

	it('marks the image so the canvas can preview whether this firm has one', () => {
		expect(BRAND_LOGO_HTML).toContain('data-brand-arm="logo"');
		expect(BRAND_LOGO_HTML).not.toContain('data-brand-arm="wordmark"');
	});

	it('asks about a field the condition picker actually offers', () => {
		// `brand.logo_url` is a URL, so not insertable — `conditionOnlyFieldsFor` is what makes it askable.
		const offered = fieldOptions([], conditionOnlyFieldsFor('invite')).map((o) => o.value);
		expect(offered).toContain(BRAND_LOGO_FIELD);
	});

	it('mirrors the Python masthead’s own inline styles', () => {
		// Mirrors `app/email/shell.py::logo_wordmark`/`WORDMARK_STYLE`; a drift is two mastheads in one product.
		expect(BRAND_LOGO_HTML).toContain('height:28px;max-width:220px;margin:0 0 28px;display:block');
		const fallback = decodeFallback(/data-fallback="([^"]*)"/.exec(BRAND_LOGO_HTML)?.[1]);
		// Read from `create.ts`'s own SMALL_SIZE rather than hardcoded: this assertion went stale
		// the moment Python's scale moved 12px → 13px, which is the pin catching a real drift and
		// then needing a hand-edit to say the same thing again.
		const small = /const SMALL_SIZE = '([^']+)'/.exec(create)?.[1];
		expect(small).toBeTruthy();
		expect(fallback?.style).toContain(`font-size:${small};font-weight:700;letter-spacing:0.09em`);
		expect(fallback?.style).toContain('text-transform:uppercase;color:#64748b');
	});
});

describe('the component type', () => {
	it('is registered BEFORE content is parsed', () => {
		// `isComponent` is consulted while parsing, and what it recognises is the LEGACY hand-written masthead.
		const brandAt = create.indexOf('registerBrandLogo(editor);');
		const blocksAt = create.indexOf('registerEmailBlocks(editor);');
		expect(brandAt).toBeGreaterThan(0);
		expect(brandAt).toBeLessThan(blocksAt);
	});

	it('locks its insides', () => {
		// Selecting the `{% else %}` text node deletes half a conditional, and the result does not render.
		const fn = create.match(/export function registerBrandLogo[\s\S]*?\n}/)?.[0] ?? '';
		expect(fn).toContain('droppable: false');
		expect(fn).toContain('editable: false');
		expect(fn).toContain('selectable: false');
		expect(fn).toContain('removable: false');
		// The lock has to re-run: a parse fills the children in after `init`.
		expect(fn).toMatch(/listenTo\?\.\(this\.components\?\.\(\), 'add reset'/);
	});
});

describe('the canvas preview', () => {
	it('keeps the masthead ON THE PAGE whether or not the firm has an image', () => {
		const armRules = canvasCss.match(/\[data-brand-arm='logo'\]\s*\{[^}]*\}/g) ?? [];
		expect(armRules.length).toBeGreaterThan(0);
		for (const rule of armRules) expect(rule).not.toMatch(/display:\s*none/);
		expect(canvasCss).not.toContain("[data-brand-arm='wordmark']");
	});

	it('keeps the masthead’s BOX and drops its broken contents', () => {
		// The box stays; `color: transparent` and a zero font size take the glyph and the alt text together.
		//
		const rule =
			/body:not\(\.has-brand-logo\) \[data-brand-arm='logo'\]\s*\{([^}]*)\}/.exec(canvasCss)?.[1] ??
			'';
		expect(rule, 'no placeholder rule').not.toBe('');
		expect(rule).toMatch(/min-width:\s*120px/);
		expect(rule).toMatch(/outline:[^;]*dashed/);
		expect(rule).toMatch(/color:\s*transparent/);
		expect(rule).toMatch(/font-size:\s*0/);
		// But NOT hidden — that is what removed the masthead from the page in the first place.
		expect(rule).not.toMatch(/display:\s*none/);

		// AND NO "HAS IT RESOLVED YET" GATE, which was added here and taken out again. It was meant
		// to stop a flash of this placeholder before `previewBrandMasthead` ran — but that function
		// runs before the masthead element exists (twice: after `grapesjs.init` returns, and inside
		// the frame's own `onload`, both ahead of `renderBody`). The window that IS real is the one
		// in which this stylesheet has not arrived — it is script-appended, so it is not
		// render-blocking — and no rule in this file can dress a window in which this file is
		// missing. What covers it is the masthead's own `src`: a transparent pixel.
		expect(canvasCss).not.toContain('brand-preview-resolved');
	});

	it('paints the real logo without touching the token src', () => {
		expect(canvasCss).toContain('--brand-logo-preview');
		const preview = create.match(/export function previewBrandMasthead[\s\S]*?\n}/)?.[0] ?? '';
		expect(preview, 'previewBrandMasthead was not found').not.toBe('');
		// The class and the property go on the CANVAS body, which `getHtml()` does not return.
		expect(preview).toMatch(/Canvas\?\.getBody\?\.\(\)/);
		expect(preview).not.toMatch(/setAttribute\('src'|\.src =/);
	});

	it('previews whatever branding holds, not only an https logo', () => {
		// NOT https-only: a deployment with no bucket inlines branding as a `data:` URI, which a canvas renders.
		const preview = create.match(/export function previewBrandMasthead[\s\S]*?\n}/)?.[0] ?? '';
		expect(preview, 'previewBrandMasthead not found').not.toBe('');
		expect(preview).not.toContain("startsWith('https://')");

		// The value lands inside a quoted CSS declaration, so a quote or a `)` would end it early.
		for (const ch of ['"', ')', ' '])
			expect(preview, `the ${JSON.stringify(ch)} guard is gone`).toContain(`includes('${ch}')`);
	});
});

describe('compiling a template that uses it', () => {
	it('compiles to the if/else the shipped default sends', () => {
		const compiled = compileConditionals(`<td>${BRAND_LOGO_HTML}</td>`);
		expect(compiled).toContain('{% if brand.logo_url %}');
		expect(compiled).toContain('{% else %}');
		expect(compiled.match(/\{% endif %\}/g)).toHaveLength(1);
		expect(compiled).not.toContain('{% if not');
		expect(compiled).not.toContain('data-fallback');
		expect(compiled).not.toContain('data-if');
		// Without `data-gjs-name`: GrapesJS's parser consumes `data-gjs-*`, so `getHtml()` never emits it.
		expect(compiled).toContain(
			'data-brand-arm="logo" src="{{ brand.logo_url }}" alt="{{ brand.name }}" ' +
				'height="28" style="height:28px;max-width:220px;margin:0 0 28px;display:block">'
		);
		expect(compiled).toMatch(
			/\{% else %\}<div style="[^"]*text-transform:uppercase[^"]*">\{\{ brand\.name \}\}<\/div>\{% endif %\}/
		);
	});
});

/** An `<img>` cannot render styled text, so the firm's name is drawn as an SVG; and it draws the broken
 *  glyph over anything painted as a BACKGROUND, so the paint must REPLACE the rendered image instead. */
describe('the masthead fallback', () => {
	it('draws the firm name as an image, because an <img> cannot render text', () => {
		const { src, width } = fallbackWordmarkImage('Ridley & Co');
		expect(src.startsWith('data:image/svg+xml;base64,')).toBe(true);
		expect(width).toBeGreaterThan(0);
		const svg = atob(src.slice('data:image/svg+xml;base64,'.length));
		// Uppercased in JS: `text-transform` does not apply inside an SVG.
		expect(svg).toContain('RIDLEY &amp; CO');
		expect(svg).toContain('font-size="12"');
		expect(svg).toContain('font-weight="700"');
		expect(svg).toContain('fill="#64748b"');
	});

	it('BASE64, because the value lands in a quoted CSS url()', () => {
		const { src } = fallbackWordmarkImage('Ridley & Co');
		for (const ch of ['"', ')', ' ']) expect(src).not.toContain(ch);
	});

	it('is inert for a firm with no name at all', () => {
		expect(fallbackWordmarkImage('')).toEqual({ src: '', width: 0 });
		expect(fallbackWordmarkImage('   ')).toEqual({ src: '', width: 0 });
	});

	it('survives a name btoa alone would throw on', () => {
		// `btoa` throws outright on a non-Latin-1 string, and a firm named in Greek must not break the preview.
		const { src } = fallbackWordmarkImage('Ελλάδα Wealth');
		expect(src.startsWith('data:image/svg+xml;base64,')).toBe(true);
	});

	it('replaces the image rather than painting behind it', () => {
		const rule =
			/body\.brand-logo-is-fallback \[data-brand-arm='logo'\]\s*\{([^}]*)\}/.exec(canvasCss)?.[1] ??
			'';
		expect(rule, 'no fallback paint rule').not.toBe('');
		expect(rule).toContain('content: var(--brand-logo-preview)');
		expect(rule).toContain('outline: none');
	});
});
