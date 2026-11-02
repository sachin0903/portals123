/**
 * Branding can only be total if nothing in the app hardcodes a colour. Two rules, enforced
 * mechanically because CSS and class strings escape every type gate:
 *
 *   1. No Tailwind palette class anywhere (text-emerald-N, bg-amber-N and friends — spelled without
 *      a number so a plain grep over the tree stays clean too). Every colour
 *      comes from a semantic token — the solid `--success`/`--warning`/`--info`/`--destructive`
 *      family or its derived `-subtle`/`-strong`/`-line` steps, which resolve per colour scheme
 *      so a `dark:` variant per colour is never needed.
 *   2. No hex literal outside the document-template layer. Those files are the exception on
 *      purpose: their colours are baked into the PDF/HTML the DocumentWorker renders, where the
 *      app's CSS custom properties do not exist, and several are `<input type="color">` values
 *      which must be hex by spec.
 *
 * Both rules are asserted over the real tree rather than a fixture, so a regression anywhere in
 * the app fails here rather than shipping a surface a firm's branding cannot reach.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// fileURLToPath, not `.pathname`: on Windows the latter yields `/C:/Users/…`, which `join` then
// turns into `C:\C:\Users\…` and readdirSync throws ENOENT — so this whole suite died at import
// and its rules silently went unchecked locally while still failing on Linux CI.
const SRC = fileURLToPath(new URL('..', import.meta.url)).replace(/[\\/]$/, '');

const PALETTES =
	'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const PALETTE_CLASS = new RegExp(
	`\\b(?:text|bg|border|ring|from|to|via|fill|stroke|divide|shadow|outline|decoration|accent|caret)-(?:${PALETTES})-[0-9]{2,3}\\b`,
	'g'
);
const HEX = /#[0-9a-fA-F]{6}\b/g;

/** `bg-white`, `text-black`, `border-white/70`… Absolute black and white are not tokens: they do
 *  not re-theme and they do not flip between schemes, so they are how a surface ends up white-on-
 *  white the moment a page is viewed in dark mode. The legitimate needs each have an answer:
 *   • on-node/on-hero ink → `*-current`, which inherits the surface's own foreground token;
 *   • a modal scrim → derive from `--hero-base`, which is dark in BOTH schemes (a themed
 *     `bg-foreground/40` inverts to a white wash and destroys the sense of modality);
 *   • the printed page → `bg-doc-paper` (declared in app.css), which is white ON PURPOSE and
 *     documented as such, so "why is this white?" has a single answer.
 *  Kept as a separate rule from PALETTE_CLASS because `white`/`black` carry no numeric step. */
const ABSOLUTE_BW =
	/\b(?:text|bg|border|ring|from|to|via|fill|stroke|divide|shadow|outline|decoration|accent|caret)-(?:white|black)\b/g;

/** Tailwind's own FIXED radii — they bypass `--radius`, so a tenant's corner setting can't reach
 *  them. `rounded-sm|md|lg|xl` all derive from the token and are fine; `rounded-full`/`none` are
 *  shape, not scale. */
const FIXED_RADIUS = /\brounded-(?:2xl|3xl|4xl)\b/g;

/** Template content moved to `@graphene/ui/template-authoring`; that package owns its exemptions. */
const DOCUMENT_LAYER: string[] = [];

/** Files whose colours are content in a sent or printed artefact, not app chrome.
 *
 *  There is no email layer on this side any more: `email-editor/create.ts` moved to pkgs/ui when the
 *  editor was shared (and pkgs/ui's token-hygiene carries the equivalent exemption), and the last
 *  file here — the dev-only `@graphene/email` preview route — went with that package. An email body
 *  is still exempt WHEREVER it is authored, for a reason a token cannot survive: it is sent to a mail
 *  client, where `var(--…)` does not resolve and there is no stylesheet of ours, so a token would
 *  ship a literal `var(--brand)` to Outlook. Add the list back here if a body is ever authored in
 *  this app again — do not widen DOCUMENT_LAYER to cover it.
 *
 *  The one printed artefact authored here: the terms print sheet is served as a complete HTML
 *  document with no app stylesheet, for the browser's print dialog — ink on paper, where a token
 *  has nothing to resolve against. */
const CONTENT_LAYER = [...DOCUMENT_LAYER, 'routes/journeys/[uid]/terms/printable.ts'];

function walk(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry.startsWith('.')) continue;
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) walk(full, out);
		else if (/\.(svelte|ts)$/.test(entry)) out.push(full);
	}
	return out;
}

// Paths are normalised to forward slashes: `relative()` yields backslashes on Windows, so every
// exemption list below (and the `files.find` in the last test) silently failed to match there —
// the lists are written with `/`. Normalising keeps one spelling on both platforms.
const files = walk(SRC).map((f) => ({
	path: relative(SRC, f).replace(/\\/g, '/'),
	text: readFileSync(f, 'utf8')
}));

/** `file:line — the offending match`, so a failure names the exact spot to fix. */
function offenders(pattern: RegExp, skip: (path: string) => boolean = () => false): string[] {
	const hits: string[] = [];
	for (const { path, text } of files) {
		if (path === 'lib/token-hygiene.test.ts' || skip(path)) continue;
		text.split('\n').forEach((line, i) => {
			for (const m of line.match(new RegExp(pattern.source, 'g')) ?? []) {
				hits.push(`${path}:${i + 1} — ${m}`);
			}
		});
	}
	return hits;
}

describe('token hygiene — every colour, radius and font comes from a branded CSS var', () => {
	it('uses no Tailwind palette colour class', () => {
		expect(offenders(PALETTE_CLASS)).toEqual([]);
	});

	it('hardcodes no hex outside the document-template layer', () => {
		expect(offenders(HEX, (p) => CONTENT_LAYER.includes(p))).toEqual([]);
	});

	it('uses no absolute black or white colour utility', () => {
		// The document layer is NOT exempt here. Its hexes are baked into the PDF, but a Tailwind
		// class only ever styles the editor's own chrome in the browser, so it has to re-theme.
		expect(offenders(ABSOLUTE_BW)).toEqual([]);
	});

	it('uses no radius class that bypasses --radius', () => {
		expect(offenders(FIXED_RADIUS)).toEqual([]);
	});

	it('keeps every exempt file listed, so the exemption stays deliberate', () => {
		// If an exempt file stops carrying hexes, drop it from the list rather than letting the
		// exemption quietly widen to cover a future app-chrome colour in the same file.
		// A fresh regex per file: a shared /g one carries `lastIndex` between `.test()` calls.
		const stillNeeded = CONTENT_LAYER.filter((p) => {
			const f = files.find((x) => x.path === p);
			return !!f && new RegExp(HEX.source).test(f.text);
		});
		expect(stillNeeded.sort()).toEqual([...CONTENT_LAYER].sort());
	});
});
