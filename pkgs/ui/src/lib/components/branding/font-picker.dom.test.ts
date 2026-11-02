/**
 * @vitest-environment jsdom
 *
 * The font pickers used to be closed lists: a firm whose brand face was not one of the twelve
 * curated families had no way to set it, although the record and the head injection accepted
 * any plain family name all along. FontPicker adds "Other…" with a free-text box. Mounted here
 * because the behaviour under test is the box appearing, validating and writing back.
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let FontPicker: Component<Record<string, unknown>>;
let fontSelection: typeof import('./font-picker.svelte').fontSelection;
let OTHER_FONT: typeof import('./font-picker.svelte').OTHER_FONT;

const OPTIONS = ['Inter', 'Roboto', 'Playfair Display'];

beforeAll(async () => {
	({ mount, unmount, flushSync } = await import('svelte'));
	const mod = await import('./font-picker.svelte');
	FontPicker = mod.default as unknown as typeof FontPicker;
	({ fontSelection, OTHER_FONT } = mod);
}, 180_000);

afterEach(() => {
	document.body.innerHTML = '';
});

function mountPicker(value: string | null) {
	const changes: (string | null)[] = [];
	const target = document.createElement('div');
	document.body.append(target);
	const app = mount(FontPicker, {
		target,
		props: {
			id: 'brand-font',
			value,
			options: OPTIONS,
			defaultLabel: 'System default (Inter)',
			onchange: (next: string | null) => changes.push(next)
		}
	});
	flushSync();
	const box = () => target.querySelector<HTMLInputElement>('#brand-font-custom');
	return { target, app, changes, box };
}

function typeInto(input: HTMLInputElement, text: string) {
	input.value = text;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

describe('fontSelection', () => {
	it('maps a stored font to its list item, a typed one to Other…, unset to the default', () => {
		expect(fontSelection('Roboto', OPTIONS)).toBe('Roboto');
		expect(fontSelection('Figtree', OPTIONS)).toBe(OTHER_FONT);
		expect(fontSelection(null, OPTIONS)).toBe('');
		expect(fontSelection('', OPTIONS)).toBe('');
	});
});

describe('FontPicker, mounted', () => {
	it('shows a curated font as a plain selection, with no text box', () => {
		const { target, app, box } = mountPicker('Roboto');
		expect(target.textContent).toContain('Roboto');
		expect(box()).toBeNull();
		unmount(app);
	});

	it('opens on Other… with the stored custom family in the box', () => {
		const { target, app, box } = mountPicker('Figtree');
		expect(target.textContent).toContain('Other…');
		expect(box()?.value).toBe('Figtree');
		unmount(app);
	});

	it('writes a valid typed family back, and only a valid one', () => {
		const { app, changes, box } = mountPicker('Figtree');
		const input = box()!;

		typeInto(input, 'Space Grotesk');
		expect(changes).toEqual(['Space Grotesk']);

		// Mid-edit garbage is flagged, not saved.
		typeInto(input, "Evil'; }");
		expect(changes).toEqual(['Space Grotesk']);
		expect(input.getAttribute('aria-invalid')).toBe('true');
		expect(document.body.textContent).toContain('Letters, digits, spaces and hyphens only');

		// Trimmed on the way out, so a trailing space does not become part of the family.
		typeInto(input, ' EB Garamond ');
		expect(changes.at(-1)).toBe('EB Garamond');
		expect(input.getAttribute('aria-invalid')).toBeNull();
		unmount(app);
	});

	it('clearing the box and leaving it returns to the default', () => {
		const { app, changes, box } = mountPicker('Figtree');
		const input = box()!;
		typeInto(input, '');
		expect(changes).toEqual([]); // an empty box while typing is not a decision yet
		input.dispatchEvent(new Event('blur'));
		flushSync();
		expect(changes).toEqual([null]);
		unmount(app);
	});
});
