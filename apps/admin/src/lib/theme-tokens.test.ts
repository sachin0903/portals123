// Guard: every colour in the admin app must come from a theme CSS var, because tenant branding
// only reaches what the tokens reach. A hardcoded Tailwind palette class (text-<hue>-700) or a
// hex literal is invisible to branding and to dark mode, and CSS escapes svelte-check entirely —
// so this is the only gate that catches a regression here.
//
// The class syntax is deliberately never written out literally below: this file greps the tree, and
// a real example in a comment would show up in anyone else's grep as a false positive.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { MATCH_TONE, STATUS_TONE } from './contributions/status';
import { BUCKET_TONE, ROUTE_TONE } from './transfers/status';

const SRC = fileURLToPath(new URL('..', import.meta.url));

const PALETTES =
	'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const PROPS =
	'text|bg|border|ring|ring-offset|outline|decoration|divide|placeholder|caret|accent|shadow|fill|stroke|from|via|to';
/** Covers the side-scoped forms (border-<side>-<hue>-500) that a naive `border-<hue>` grep misses. */
const PALETTE_CLASS = new RegExp(
	`\\b(${PROPS})(-(t|r|b|l|x|y|s|e|tl|tr|br|bl))?-(${PALETTES})-[0-9]{2,3}\\b`,
	'g'
);
/** Absolute white/black ink or fills bypass --foreground/--background the same way a palette does. */
const ABSOLUTE_CLASS = new RegExp(`\\b(${PROPS})(-(t|r|b|l|x|y))?-(white|black)\\b`, 'g');
const HEX_LITERAL = /#[0-9a-fA-F]{6}\b/g;
/** The 2xl/3xl radii are Tailwind FIXED sizes — they bypass --radius, so branding can't move them. */
const FIXED_RADIUS = /\brounded(-[trbl]{1,2})?-(2xl|3xl)\b/g;

function walk(dir: string): string[] {
	return readdirSync(dir).flatMap((entry) => {
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) return walk(path);
		return /\.(svelte|ts)$/.test(path) && !path.endsWith('theme-tokens.test.ts') ? [path] : [];
	});
}

const files = walk(SRC).map((path) => ({
	path: path.slice(SRC.length),
	body: readFileSync(path, 'utf8')
}));

function offenders(pattern: RegExp): string[] {
	return files.flatMap(({ path, body }) => {
		// Comments and mock prose legitimately contain things like "contribution #102".
		const hits = body.match(pattern) ?? [];
		return hits.map((hit) => `${path}: ${hit}`);
	});
}

describe('admin colours are token-driven', () => {
	it('has no Tailwind palette classes', () => {
		expect(offenders(PALETTE_CLASS)).toEqual([]);
	});

	it('has no absolute white/black colour classes', () => {
		expect(offenders(ABSOLUTE_CLASS)).toEqual([]);
	});

	it('has no fixed-size radii that bypass --radius', () => {
		expect(offenders(FIXED_RADIUS)).toEqual([]);
	});

	it('has no hex colour literals', () => {
		// `#102` in mock prose is 3 chars; only a full 6-digit hex reads as a colour.
		expect(offenders(HEX_LITERAL)).toEqual([]);
	});
});

describe('status tone maps use semantic tokens', () => {
	const TOKEN_CLASS =
		/^(bg|text|border)-(muted|muted-foreground|border|foreground|card)$|^(bg|text|border)-(success|warning|info|destructive|brand|positive|negative)(-(subtle|strong|line|foreground))?$/;

	/**
	 * The tone vocabulary `@graphene/ui`'s StatusBadge accepts (`statusToneClasses`). A status map
	 * names one of these rather than spelling out its three classes: the maps used to carry pasted
	 * copies of the shared tone triples, which is the drift `field-shell.ts` exists to stop, and a
	 * typo in a pasted class string is invisible — Tailwind emits nothing and the chip renders
	 * unstyled. Mirrored here because a `Record<K, StatusTone>` is only checked where it is
	 * annotated, and these are read by name from other modules.
	 */
	const TONES = new Set(['success', 'warning', 'info', 'danger', 'brand', 'primary', 'neutral']);

	for (const [name, map] of Object.entries({ STATUS_TONE, MATCH_TONE, BUCKET_TONE })) {
		it(`${name} names a StatusBadge tone for every status`, () => {
			const bad = Object.entries(map)
				.filter(([, tone]) => !TONES.has(tone))
				.map(([key, tone]) => `${key}: ${tone}`);
			expect(bad).toEqual([]);
		});
	}

	// ROUTE_TONE is not a status: it tints a transfer's ROUTE (which custodians the money crosses),
	// so it stays a class string on a plain Badge and is still held to the token rule.
	it('ROUTE_TONE resolves every class to a theme token', () => {
		const bad = Object.entries(ROUTE_TONE).flatMap(([key, tone]) =>
			tone
				.split(/\s+/)
				.filter((cls) => !TOKEN_CLASS.test(cls))
				.map((cls) => `${key}: ${cls}`)
		);
		expect(bad).toEqual([]);
	});

	it('gives every status its own tone', () => {
		// A missing key renders an unstyled chip, which reads as "no status" rather than as a status.
		expect(Object.values(STATUS_TONE).every(Boolean)).toBe(true);
		expect(Object.values(BUCKET_TONE).every(Boolean)).toBe(true);
	});
});
