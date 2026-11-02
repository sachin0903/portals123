import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { FIELD_CLASS, FIELD_SHELL, FIELD_TONE_FILLED, FIELD_WRAPPER_CLASS } from './field-shell';

// Only worth having if the controls use it: one that re-declares the shell's properties fails here.

const read = (relative: string) =>
	readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8');

/** Each `cn(FIELD_…, …)` call as source text; parens counted so a nested `cn()` cannot end it. */
function shellCalls(source: string): string[] {
	const found: string[] = [];
	const opener = /cn\(\s*FIELD_[A-Z_]+/g;
	for (let hit = opener.exec(source); hit; hit = opener.exec(source)) {
		let depth = 1;
		let i = hit.index + hit[0].indexOf('(') + 1;
		for (; i < source.length && depth > 0; i++) {
			if (source[i] === '(') depth += 1;
			else if (source[i] === ')') depth -= 1;
		}
		found.push(source.slice(hit.index, i));
	}
	return found;
}

/** Every control that presents itself as "a box you type or choose in". */
const FIELDS = {
	Input: 'components/ui/input/input.svelte',
	MoneyInput: 'components/ui/money-input/money-input.svelte',
	Combobox: 'components/ui/combobox/combobox.svelte',
	MultiSelect: 'components/ui/multi-select/multi-select.svelte',
	'dynamic-form combobox': 'forms/field-inputs/combobox-input.svelte'
} as const;

describe('field shell', () => {
	it('describes the box once', () => {
		// The properties that make fields look alike: one leaves only when no field has it any more.
		for (const token of [
			'border-input',
			'dark:bg-input/30',
			'bg-transparent',
			'h-8',
			'rounded-lg',
			'px-2.5'
		]) {
			expect(FIELD_SHELL).toContain(token);
		}
	});

	it('keeps the 16px mobile type size that stops iOS zooming the page', () => {
		expect(FIELD_SHELL).toContain('text-base');
		expect(FIELD_SHELL).toContain('md:text-sm');
	});

	it.each(Object.entries(FIELDS))('%s composes the shell', (_name, path) => {
		const source = read(path);
		expect(source).toMatch(/field-shell/);
		expect(source).toMatch(/FIELD_(CLASS|WRAPPER_CLASS)/);
	});

	it.each(Object.entries(FIELDS))('%s does not re-declare the box', (_name, path) => {
		// Scoped to the `cn(FIELD_…)` calls: the panel is legitimately `bg-popover rounded-md`, and a file-wide scan would hit the comment quoting the old string (switch-columns.test.ts's false positive too).
		const calls = shellCalls(read(path));
		expect(calls.length).toBeGreaterThan(0);
		for (const call of calls) {
			// The value the combobox and multi-select carried: opaque where every other field is not.
			expect(call).not.toMatch(/\bbg-background\b/);
			// A second height or radius sits a control a size apart. `h-auto min-h-8` is allowed.
			expect(call).not.toMatch(/(?<![-\w])h-9\b/);
			expect(call).not.toMatch(/(?<![-\w])rounded-(?:md|sm|xl|none|full)\b/);
		}
	});

	it('gives a wrapper the focus-within ring, not the focus-visible one', () => {
		// `focus-visible` on a div never fires, so MoneyInput's box would have no focus state at all.
		expect(FIELD_WRAPPER_CLASS).toContain('focus-within:ring-3');
		expect(FIELD_WRAPPER_CLASS).not.toContain('focus-visible');
		expect(FIELD_CLASS).toContain('focus-visible:ring-3');
	});
});

// The inspector tone: filled in a rail, outlined on a form, from one definition an ancestor opts in.
describe('the filled inspector tone', () => {
	it('is part of the shell, so every control that composes it gets the tone', () => {
		// In `FIELD_SHELL`, not `FIELD_CLASS` alone: `MoneyInput`/`UnitInput` compose `FIELD_WRAPPER_CLASS` and would stay outlined in a rail of filled fields.
		expect(FIELD_SHELL).toContain(FIELD_TONE_FILLED);
		expect(FIELD_CLASS).toContain(FIELD_TONE_FILLED);
		expect(FIELD_WRAPPER_CLASS).toContain(FIELD_TONE_FILLED);
	});

	it('uses `in-data-*`, whose specificity a caller can still override', () => {
		// `in-*` emits `:where([attr]) .utility` — no specificity — so a `class` override still wins; the `[[data-…]_&]:` form compiles to two classes' worth and would out-rank it.
		expect(FIELD_TONE_FILLED).toMatch(/in-data-\[field-tone=filled\]:/);
		expect(FIELD_TONE_FILLED).not.toContain('[[data-field-tone');
	});

	it('is declared nowhere else — no control hand-writes the tone', () => {
		// The same rule as the shell itself: a second copy is how the two came apart last time.
		for (const [name, path] of Object.entries(FIELDS)) {
			const source = read(path);
			expect(source, `${name} must take the tone from the shell`).not.toMatch(
				/in-data-\[field-tone/
			);
		}
	});
});
