/**
 * @vitest-environment jsdom
 *
 * Sort code and account number are digit fields. `inputmode="numeric"` only hints the keypad, so
 * a letter still lands in the box — and when the formatter's output does not change (`a` → ``),
 * Svelte leaves the letter sitting there. These tests mount the same inputs journeys and the book
 * use, and they are client-side because the defect is the event handler, not the markup.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import { attachDigitMask, canonicalSortCode, formatSortCode } from '../editor/masks.js';

type Props = {
	value?: unknown;
	onchange: (v: string) => void;
	id?: string;
	verified?: boolean;
};

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let SortCodeInput: Component<Props>;
let BankAccountNumberInput: Component<Props>;

beforeAll(async () => {
	({ mount, unmount, flushSync } = await import('svelte'));
	SortCodeInput = (await import('./sort-code-input.svelte'))
		.default as unknown as typeof SortCodeInput;
	BankAccountNumberInput = (await import('./bank-account-number-input.svelte'))
		.default as unknown as typeof BankAccountNumberInput;
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
});

function mountField(Field: Component<Props>, props: Partial<Props> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	const onchange = vi.fn();
	const app = mount(Field, { target, props: { onchange, ...props } });
	flushSync();
	const input = target.querySelector('input');
	if (!input) throw new Error('field did not render an input element');
	return { target, app, input, onchange };
}

function typeLetter(input: HTMLInputElement) {
	const before = new InputEvent('beforeinput', {
		bubbles: true,
		cancelable: true,
		data: 'a',
		inputType: 'insertText'
	});
	input.dispatchEvent(before);
	return before;
}

describe('attachDigitMask', () => {
	it('strips letters on input and blocks them on beforeinput', () => {
		const node = document.createElement('input');
		const onchange = vi.fn();
		const stop = attachDigitMask(node, {
			canonical: canonicalSortCode,
			format: formatSortCode,
			onchange
		});
		expect(typeLetter(node).defaultPrevented).toBe(true);
		node.value = '12ab34';
		node.dispatchEvent(new Event('input', { bubbles: true }));
		expect(node.value).toBe('12 - 34');
		expect(onchange).toHaveBeenCalledWith('1234');
		stop();
	});
});

describe('SortCodeInput', () => {
	it('refuses a letter keystroke', () => {
		const { app, input } = mountField(SortCodeInput);
		expect(typeLetter(input).defaultPrevented).toBe(true);
		unmount(app);
	});

	it('strips letters that arrive as a paste, rather than leaving them in the box', () => {
		const { app, input, onchange } = mountField(SortCodeInput, { value: '12' });
		input.value = '12ab34';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(input.value).toBe('12 - 34');
		expect(onchange).toHaveBeenCalledWith('1234');
		unmount(app);
	});

	it('shows a saved sort code immediately, and an empty value stays empty', () => {
		const saved = mountField(SortCodeInput, { value: '20-30-40' });
		expect(saved.input.value).toBe('20 - 30 - 40');
		expect(saved.input.value).not.toMatch(/^00/);
		unmount(saved.app);

		const empty = mountField(SortCodeInput, { value: '' });
		expect(empty.input.value).toBe('');
		expect(empty.input.placeholder).not.toMatch(/00/);
		unmount(empty.app);
	});

	it('marks a confirmed sort code with a tick, and leaves the logo for the details card', () => {
		const { app, input } = mountField(SortCodeInput, { value: '04-00-03', verified: true });
		expect(input.parentElement?.querySelector('svg')).not.toBeNull();
		expect(input.parentElement?.textContent).toContain('Bank details confirmed');
		expect(input.parentElement?.querySelector('img')).toBeNull();
		unmount(app);
	});

	it('keeps digits just typed when the saved value has not caught up', () => {
		const { app, input, onchange } = mountField(SortCodeInput, { value: '' });
		input.value = '203040';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(input.value).toBe('20 - 30 - 40');
		expect(onchange).toHaveBeenCalledWith('20-30-40');
		unmount(app);
	});
});

describe('BankAccountNumberInput', () => {
	it('refuses a letter keystroke', () => {
		const { app, input } = mountField(BankAccountNumberInput);
		expect(typeLetter(input).defaultPrevented).toBe(true);
		unmount(app);
	});

	it('strips letters that arrive as a paste, rather than leaving them in the box', () => {
		const { app, input, onchange } = mountField(BankAccountNumberInput, { value: '1234' });
		input.value = '1234abcd';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(input.value).toBe('1234');
		expect(onchange).toHaveBeenCalledWith('1234');
		unmount(app);
	});
});
