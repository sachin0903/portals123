import { readFileSync, readdirSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Pins for the scrollbar treatment in theme.css. A CSS change escapes every type gate, and all
 * three of these failed silently in the tree before the pins existed:
 *
 *  1. `no-scrollbar` was asked for by two components and DEFINED NOWHERE, so the sidebar nav and
 *     the ⌘K list had been showing a bar for as long as they had existed. An undefined utility is
 *     not an error in Tailwind — it emits nothing.
 *  2. `scrollbar-slim` was defined TWICE, with different widths and colours. The first definition
 *     was dead, so the comment above it described behaviour that had not been in effect.
 *  3. The global quiet default must not use `::-webkit-scrollbar`. The pseudo-elements force a
 *     classic non-overlay bar in every WebKit engine, which takes a permanent gutter out of the
 *     content width in order to hide a bar that was already auto-hiding there.
 */
const UI_SRC = join(import.meta.dirname, '..');
/**
 * Comments stripped, because the rules below are documented in prose that names the very things
 * being scanned for — the same false positive `switch-columns.test.ts` records hitting.
 */
const css = readFileSync(join(import.meta.dirname, 'theme.css'), 'utf-8').replace(
	/\/\*[\s\S]*?\*\//g,
	''
);

/** The standard CSS properties, which appear in source as arbitrary values (`[scrollbar-gutter:stable]`). */
const PROPERTIES = new Set(['scrollbar-width', 'scrollbar-color', 'scrollbar-gutter']);

function sourceFiles(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...sourceFiles(full));
		else if (/\.svelte$/.test(entry.name)) out.push(full);
	}
	return out;
}

describe('scrollbar utilities', () => {
	const referenced = new Map<string, string[]>();
	for (const path of sourceFiles(join(UI_SRC, 'lib'))) {
		const text = readFileSync(path, 'utf-8');
		// A CLASS USAGE, not merely the characters. The bare word-boundary match also caught a CSS custom
		// property (`--scrollbar-allowance`) and a pseudo-element (`::-webkit-scrollbar-thumb`), neither of
		// which is a utility anything could define — so the email editor's own scroller styling read as
		// three missing classes. Refusing a preceding `-` or `:` is what tells a class apart from those.
		for (const [, token] of text.matchAll(
			/(?<![-:\w])(no-scrollbar|scrollbar-[a-z]+(?:-[a-z]+)*)\b/g
		)) {
			if (PROPERTIES.has(token)) continue;
			referenced.set(token, [...(referenced.get(token) ?? []), relative(UI_SRC, path)]);
		}
	}

	it('finds the classes in use (the scan itself works)', () => {
		expect([...referenced.keys()].sort()).toEqual(['no-scrollbar', 'scrollbar-slim']);
	});

	it('defines every class a component asks for', () => {
		const missing = [...referenced].filter(([token]) => !css.includes(`@utility ${token} {`));
		expect(missing).toEqual([]);
	});

	it('defines each one exactly once — a second definition silently shadows the first', () => {
		for (const token of referenced.keys()) {
			const hits = css.match(new RegExp(`@utility ${token} \\{`, 'g')) ?? [];
			expect(hits, token).toHaveLength(1);
		}
	});
});

describe('the quiet default', () => {
	// The block between the media query and the next utility: the whole of the global treatment.
	const start = css.indexOf('@media (hover: hover)');
	const quiet = css.slice(start, css.indexOf('@utility no-scrollbar'));

	it('is applied globally, since there is no selector for "an element that scrolls"', () => {
		expect(start).toBeGreaterThan(-1);
		expect(quiet).toMatch(/\*\s*\{[^}]*scrollbar-color:\s*transparent transparent/);
	});

	it('reveals the thumb on hover AND focus-within, so keyboard scrolling is not invisible', () => {
		expect(quiet).toMatch(/\*:hover,\s*\*:focus-within\s*\{[^}]*scrollbar-color:/);
	});

	it('keeps the bar wherever a hover can never happen (touch)', () => {
		// The hiding lives INSIDE `@media (hover: hover)` — a touch device keeps its UA bar.
		expect(quiet.indexOf('scrollbar-color: transparent')).toBeGreaterThan(
			quiet.indexOf('@media (hover: hover)')
		);
	});

	it('uses only the standard properties — no ::-webkit-scrollbar', () => {
		expect(quiet).not.toContain('-webkit-scrollbar');
	});
});
