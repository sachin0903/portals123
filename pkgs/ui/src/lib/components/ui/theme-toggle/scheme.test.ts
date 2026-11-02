import { readFileSync, readdirSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Guards for the light/dark switcher's ONE-SOURCE-OF-TRUTH rule: the `.dark` class on `<html>`
 * decides the scheme, and nothing may write the UA `color-scheme` behind the stylesheet's back.
 *
 * These are static scans, not DOM tests, on purpose — pkgs/ui runs vitest in the node environment
 * (no jsdom/happy-dom in the tree) and the two regressions worth catching are both *source* shapes:
 *
 *   1. importing `mode-watcher`. Reading its `mode.current` runs a `$derived` side effect that writes
 *      an inline `documentElement.style.colorScheme` off `prefers-color-scheme`. An inline style beats
 *      a stylesheet, so `theme.css`'s `.dark { color-scheme: dark }` lost and scrollbars, native
 *      controls and the caret rendered light on a dark page. It was imported for exactly one value
 *      (the Sonner toaster's `theme`), which `colourScheme` now supplies from the class.
 *   2. writing `style.colorScheme` from our own code, which would reintroduce the same fight.
 *
 * Plus two pins on theme.css, since a CSS change escapes every type gate: the UA scheme must be
 * declared per class, and Tailwind's `dark:` variant must stay bound to the class rather than
 * reverting to v4's default `@media (prefers-color-scheme: dark)`.
 */
const UI_SRC = join(import.meta.dirname, '..', '..', '..', '..');
const THEME_CSS = join(UI_SRC, 'styles', 'theme.css');

function sourceFiles(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...sourceFiles(full));
		else if (/\.(svelte|ts)$/.test(entry.name) && !/\.test\.ts$/.test(entry.name)) out.push(full);
	}
	return out;
}

/**
 * Comments out. These files document the very anti-patterns being scanned for, so a naive scan
 * flags the warning labels rather than the hazard.
 */
function code(text: string): string {
	return text
		.replace(/<!--[\s\S]*?-->/g, '') // Svelte markup comments
		.replace(/\/\*[\s\S]*?\*\//g, '') // block + JSDoc
		.replace(/(^|[^:])\/\/.*$/gm, '$1'); // line (the `[^:]` spares `https://`)
}

const FILES = sourceFiles(UI_SRC).map((path) => ({
	path: relative(UI_SRC, path),
	text: readFileSync(path, 'utf8'),
	code: code(readFileSync(path, 'utf8'))
}));

describe('colour-scheme sources of truth', () => {
	it('scans a non-trivial number of files (the walker itself works)', () => {
		expect(FILES.length).toBeGreaterThan(50);
	});

	it('never imports mode-watcher — it tracks the OS, not the app', () => {
		const offenders = FILES.filter((f) => /from\s+['"]mode-watcher/.test(f.code)).map(
			(f) => f.path
		);
		expect(offenders).toEqual([]);
	});

	it('never writes the UA colour scheme from script — theme.css owns it', () => {
		const write = /style\s*\.\s*colorScheme\s*=|setProperty\(\s*['"]color-scheme['"]/;
		const offenders = FILES.filter((f) => write.test(f.code)).map((f) => f.path);
		expect(offenders).toEqual([]);
	});

	it('feeds the toaster from the class-derived store', () => {
		const sonner = FILES.find((f) => f.path.endsWith(join('sonner', 'sonner.svelte')));
		expect(sonner).toBeDefined();
		expect(sonner?.text).toContain('theme={colourScheme.current}');
	});
});

describe('theme.css', () => {
	const css = readFileSync(THEME_CSS, 'utf8');

	it('declares the UA colour scheme for both schemes, keyed off the class', () => {
		expect(css).toMatch(/:root\s*\{[^}]*color-scheme:\s*light/);
		expect(css).toMatch(/\.dark\s*\{[^}]*color-scheme:\s*dark/);
	});

	it("binds Tailwind's dark: variant to the class, not prefers-color-scheme", () => {
		expect(css).toMatch(/@custom-variant\s+dark\s*\(&:where\(\.dark,\s*\.dark\s+\*\)\)/);
	});
});
