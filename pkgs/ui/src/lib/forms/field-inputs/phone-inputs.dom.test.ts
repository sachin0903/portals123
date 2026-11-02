/**
 * A phone field refuses nonsense AT THE KEYSTROKE, not on save.
 *
 * The API validates too, and that is not enough on its own: a box that accepts
 * `3456543132456rfhnhtbgf` and only objects when the adviser presses Save has already let them
 * type it, and what they read back is their own nonsense sitting in a field that looked like it
 * took it. That is exactly what shipped — a bare `<input type="tel">` on the client record and a
 * hand-wired one in the other-numbers rows.
 *
 * The subtle half is WHY an `oninput` formatter cannot fix it: `a` and `` both canonicalise to
 * empty, so the derived display is unchanged, Svelte leaves the DOM alone, and the letter stays on
 * screen. Only writing `node.value` back — or refusing the keystroke — clears it. Both are tested
 * here, in a real DOM, because neither is visible to an SSR render or to a unit test of the mask.
 */
import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import PhoneNumberInput from './phone-number-input.svelte';
import PhoneNumbersInput from './phone-numbers-input.svelte';
import { attachPhoneMask, formatPhoneNumber } from '../editor/masks.js';

function insert(input: HTMLInputElement, data: string, inputType = 'insertText') {
	const event = new InputEvent('beforeinput', { bubbles: true, cancelable: true, data, inputType });
	input.dispatchEvent(event);
	return event;
}

describe('attachPhoneMask', () => {
	function attach() {
		const node = document.createElement('input');
		const onchange = vi.fn();
		const stop = attachPhoneMask(node, { onchange });
		return { node, onchange, stop };
	}

	it('refuses a typed letter but allows the + a number cannot be written without', () => {
		const { node, stop } = attach();
		expect(insert(node, 'a').defaultPrevented).toBe(true);
		expect(insert(node, '+').defaultPrevented).toBe(false);
		expect(insert(node, '7').defaultPrevented).toBe(false);
		// The separators people space a number out with are typed, not pasted.
		expect(insert(node, ' ').defaultPrevented).toBe(false);
		expect(insert(node, '-').defaultPrevented).toBe(false);
		stop();
	});

	it('cleans a PASTE, which beforeinput cannot filter character by character', () => {
		const { node, onchange, stop } = attach();
		node.value = '3456543132456rfhnhtbgf';
		node.dispatchEvent(new Event('input', { bubbles: true }));
		expect(node.value).not.toMatch(/[a-z]/i);
		// The SANITISED text, not a canonical `+number`: the mask no longer rewrites what is
		// being typed, so `phoneIssue` can still see that these are bare digits.
		expect(onchange).toHaveBeenCalledWith('3456543132456');
		stop();
	});

	it('stops at E.164 fifteen digits rather than letting an essay sit in the box', () => {
		const { node, onchange, stop } = attach();
		node.value = '+23 4567 8987 654345678654565';
		node.dispatchEvent(new Event('input', { bubbles: true }));
		const digits = (onchange.mock.calls.at(-1)?.[0] as string).replace(/\D/g, '');
		expect(digits).toHaveLength(15);
		expect(node.value.replace(/\D/g, '')).toHaveLength(15);
		stop();
	});
});

describe('PhoneNumberInput', () => {
	function mountField(props: Record<string, unknown> = {}) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const onchange = vi.fn();
		mount(PhoneNumberInput, { target, props: { onchange, ...props } });
		flushSync();
		const input = target.querySelector('input');
		if (!input) throw new Error('no input rendered');
		return { input, onchange };
	}

	it('refuses a letter keystroke', () => {
		const { input } = mountField();
		expect(insert(input, 'a').defaultPrevented).toBe(true);
	});

	it('renders the number it was given on first paint, not after an effect', () => {
		// Effects do not run on the server. Seeding the display at construction is what stops an
		// SSR render showing an empty box for a client who has a number.
		const { input } = mountField({ value: '+447700900123' });
		expect(input.value).toBe(formatPhoneNumber('+447700900123'));
	});

	it('leaves what is typed alone instead of inventing a country code', () => {
		// Typing `1234` used to canonicalise to `+1234` and redisplay as `+12 34`, so the field
		// invented a country code from the first two digits of a half-entered number.
		const { input } = mountField();
		input.value = '1234';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		expect(input.value).toBe('1234');
	});

	it('says what is wrong, in the field, while it is being typed', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		mount(PhoneNumberInput, { target, props: { onchange: vi.fn() } });
		flushSync();
		const input = target.querySelector('input')!;

		// Bare digits are neither a UK number nor an international one. This is the value that
		// used to save happily and read back as `+11 1111 1`.
		input.value = '1111111';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(target.textContent).toContain('international number with its country code');
		expect(input.getAttribute('aria-invalid')).toBe('true');

		// A real one clears it.
		input.value = '07812345678';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(target.querySelector('p')).toBeNull();
		expect(input.getAttribute('aria-invalid')).toBeNull();
		expect(input.value).toBe(formatPhoneNumber('07812345678'));
	});

	it('says nothing at all while the field is empty', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		mount(PhoneNumberInput, { target, props: { onchange: vi.fn() } });
		flushSync();
		expect(target.querySelector('p')).toBeNull();
	});

	it('clears a letter that survived into the box', () => {
		const { input, onchange } = mountField();
		input.value = '07700900123abc';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		expect(input.value).not.toMatch(/[a-z]/i);
		expect(onchange).toHaveBeenCalled();
	});
});

describe('PhoneNumbersInput — a single secondary number', () => {
	it('shows its empty row rather than an "Add number" button', () => {
		// Onboarding is where most of these are first typed, and an empty field used to render
		// nothing but a button — a click between a client and the answer, revealing the one row it
		// would ever reveal. With a cap of 1 there is no second number to add.
		const host = document.createElement('div');
		document.body.append(host);
		mount(PhoneNumbersInput, { target: host, props: { value: [], onchange: () => {} } });
		flushSync();
		expect(host.querySelectorAll('input').length).toBe(1);
		expect(host.textContent).not.toContain('Add number');
		host.remove();
	});

	it('an untouched row is still NO number — a blank never reaches the answer', () => {
		const onchange = vi.fn();
		const host = document.createElement('div');
		document.body.append(host);
		mount(PhoneNumbersInput, { target: host, props: { value: [], onchange } });
		flushSync();
		// Rendering the row must not report a value; the record would otherwise hold
		// `{ label: 'home', number: '' }` and read "has a number" while the screen shows none.
		expect(onchange).not.toHaveBeenCalledWith([{ label: 'home', number: '' }]);
		host.remove();
	});

	it('offers Add number when a caller genuinely allows several', () => {
		const host = document.createElement('div');
		document.body.append(host);
		mount(PhoneNumbersInput, { target: host, props: { value: [], onchange: () => {}, max: 3 } });
		flushSync();
		expect(host.textContent).toContain('Add number');
		host.remove();
	});
});
