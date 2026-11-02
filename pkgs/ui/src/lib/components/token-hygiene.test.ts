import { readFileSync, readdirSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A hardcoded colour in @graphene/ui defeats tenant branding in ALL THREE portals at once — the
 * client portal is otherwise clean of palette classes, so anything appearing there has leaked out of
 * this package. CSS and class strings escape every type gate (svelte-check never compiles CSS), so
 * this scan is the only automated guard: it fails the build the moment an emerald-700 ink class or a
 * `#0f766e` reappears in the shared library.
 *
 * Map a value to the semantic token that carries its MEANING (success / warning / info /
 * destructive / brand / positive / negative), not to whichever token happens to match the hue. The
 * derived trio per status lives in styles/theme.css; the canonical map is
 * components/ui/status-badge/tones.ts.
 */
const LIB = join(import.meta.dirname, '..');

const PALETTE_CLASS =
	/\b(?:text|bg|border|ring|from|to|via|fill|stroke|decoration|outline|shadow|accent|caret|divide|placeholder)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950)\b/g;

/** Hex / rgb / hsl literals. oklch() is NOT banned: theme.css speaks oklch, and a documented
 *  plain-oklch fallback beside a `oklch(from var(--token) …)` line is a deliberate pattern. */
const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g;

/**
 * Tailwind's two colour keywords. These are NOT in PALETTE_CLASS (they carry no numeric step), which
 * is how two dialog scrims kept a fixed black wash through the sweep that removed every other
 * hardcoded colour from this package. They are worth their own rule rather than a widened one,
 * because the failure they cause is scheme-specific: a fixed light or dark wash cannot be right in
 * both schemes at once, so it is always wrong in one of them. Reach for a token that is dark in both
 * (--hero-base, the ink anchor) or one that inverts (--foreground), depending on which you meant.
 */
const KEYWORD_CLASS =
	/\b(?:text|bg|border|ring|from|to|via|fill|stroke|decoration|outline|shadow|accent|caret|divide|placeholder)-(?:white|black)(?:\/\d{1,3})?\b/g;

/**
 * Files exempt from the palette-class scan, with the reason. Anything listed here is debt, not a
 * blessing.
 *
 * `templates/template-page.svelte` renders a mock A4 page for the document-template authoring
 * preview. It carries ~24 palette classes and a handful of hexes. It sits OUTSIDE this zone's
 * ownership (lib/templates, not lib/components) and is handed off rather than edited here.
 */
const PALETTE_EXEMPT = new Set(['templates/template-page.svelte']);
// Paths are relative to src/lib, so components live under `components/`.

/**
 * Files exempt from the colour-literal scan, with the reason.
 *
 * The two branding files hold hexes that are DATA, not styling: the value of an `<input
 * type="color">`, which cannot take a CSS var. `data/client-avatar.svelte` is exempt for the plain
 * fallback beside its relative-colour-syntax fill (see the comment there).
 *
 * `components/email-editor/create.ts` holds two kinds of necessarily-literal colour: the injected
 * GrapesJS canvas/panel theme (a third-party editor chrome that cannot consume the design tokens),
 * and email-safe default markup — email clients don't understand CSS custom properties or `oklch()`,
 * so email HTML must carry literal hex (mirrors `app/email/transport.py`, where `Brand.accent` is only ever a
 * hex). Neither is the shared library styling its own surfaces.
 *
 * `shell/**` and `templates/**` are other zones' files; they are listed so this test reports honest
 * coverage rather than silently passing.
 */
const LITERAL_EXEMPT = new Set([
	'components/branding/branding-editor.svelte',
	'components/email-editor/create.ts',
	'components/shell/auth-shell.svelte',
	'templates/template-page.svelte',
	'templates/template-editor.svelte',
	// Document CONTENT is rendered into PDFs without the app theme. These are the same explicit
	// exemptions the platform package owned before template authoring moved here.
	'components/template-authoring/TemplateEditor.svelte',
	'components/template-authoring/editor/blocks.ts',
	'components/template-authoring/editor/chart-preview.ts',
	'components/template-authoring/editor/component-types.ts',
	'components/template-authoring/editor/create.ts',
	'components/template-authoring/editor/page-settings.ts',
	'components/template-authoring/editor/serialize.ts'
]);

function sources(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...sources(path));
		else if (/\.(svelte|ts)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) out.push(path);
	}
	return out;
}

/**
 * Strip comments before scanning, for two reasons: a class name quoted in prose ("this used to be an
 * emerald-200 ink") shouldn't trip the scan, and — more importantly — Tailwind's own scanner reads
 * comments too, so a palette class named in a comment gets EMITTED into the bundle even after the
 * markup stopped using it. Name the colour without the utility prefix when explaining a change.
 */
function stripComments(code: string): string {
	return code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(^|[^:'"`\w])\/\/[^\n]*/g, '$1');
}

const files = sources(LIB).map((path) => ({
	// Normalise to forward slashes so the exemption sets (keyed with `/`) match on Windows too, where
	// relative() returns backslash paths — otherwise every exemption silently misses off-CI.
	rel: relative(LIB, path).replaceAll('\\', '/'),
	code: stripComments(readFileSync(path, 'utf8'))
}));

describe('shared component library colour hygiene', () => {
	it('scans the whole library', () => {
		// Guards the guard: a broken walk would make every assertion below pass vacuously.
		expect(files.length).toBeGreaterThan(100);
	});

	it('has no Tailwind palette classes outside the documented exemptions', () => {
		const offenders = files
			.filter((f) => !PALETTE_EXEMPT.has(f.rel))
			.flatMap((f) => (f.code.match(PALETTE_CLASS) ?? []).map((m) => `${f.rel}: ${m}`));
		expect(offenders).toEqual([]);
	});

	it('has no fixed white/black utility classes outside the documented exemptions', () => {
		const offenders = files
			.filter((f) => !PALETTE_EXEMPT.has(f.rel))
			.flatMap((f) => (f.code.match(KEYWORD_CLASS) ?? []).map((m) => `${f.rel}: ${m}`));
		expect(offenders).toEqual([]);
	});

	it('has no hex/rgb/hsl colour literals outside the documented exemptions', () => {
		const offenders = files
			.filter((f) => !LITERAL_EXEMPT.has(f.rel))
			.flatMap((f) => (f.code.match(COLOUR_LITERAL) ?? []).map((m) => `${f.rel}: ${m}`));
		expect(offenders).toEqual([]);
	});

	it('has no fixed-size corner radii that bypass --radius', () => {
		// rounded-sm/md/lg/xl derive from --radius; rounded-full/none are intentional. Tailwind's
		// fixed 2xl/3xl/4xl steps are FIXED rem values, so a firm's radius (and the sharp surface preset)
		// cannot reach them.
		const offenders = files
			.filter((f) => !PALETTE_EXEMPT.has(f.rel))
			.flatMap((f) =>
				(f.code.match(/\brounded(?:-[trbl]{1,2})?-(?:2xl|3xl|4xl)\b/g) ?? []).map(
					(m) => `${f.rel}: ${m}`
				)
			);
		expect(offenders).toEqual([]);
	});
});
