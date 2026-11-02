/**
 * @vitest-environment jsdom
 *
 * THE bug: the date field could not be typed into.
 *
 * The input carried an `onclick` that called `showPicker()` on the WHOLE control, meaning to be
 * helpful — click anywhere in the field, get a calendar. What it actually did was take the keyboard
 * away. A native date input is three focusable segments (day / month / year); clicking one focuses
 * it and typing fills it. Opening the picker on that same click puts the calendar overlay over the
 * field, so the click is consumed by the overlay rather than the segment it was aimed at, and there
 * is no way in to type at all. A date of birth decades back was then only reachable by paging a
 * calendar month by month.
 *
 * This is a mounted test rather than an SSR one because the defect lives entirely in an event
 * handler: the rendered markup was — and still is — a correct `<input type="date">`. An SSR render
 * cannot ask what a click does.
 *
 * jsdom has no calendar overlay to observe, so the proxy for "the picker hijacks the click" is
 * `showPicker` being called at all. jsdom does not implement it either, which is why the test
 * INSTALLS it on the prototype: without that, the old `e.currentTarget.showPicker?.()` would
 * short-circuit on the optional call and the bug would pass unnoticed. With it installed, this file
 * fails against the previous implementation and passes against the current one.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';

type Props = {
	value?: unknown;
	onchange: (v: string | undefined) => void;
	placeholder?: string;
	invalid?: boolean;
	id?: string;
};

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let DateInput: Component<Props>;

/** Stands in for the browser's calendar. jsdom ships no `showPicker`, so this is what makes the
 *  whole-input picker observable — see the file header. */
const showPicker = vi.fn();

beforeAll(async () => {
	({ mount, unmount, flushSync } = await import('svelte'));
	DateInput = (await import('./date-input.svelte')).default as unknown as typeof DateInput;
	// Generous: a cold vite transform of the component graph beats the 10s default on Windows.
}, 120_000);

beforeEach(() => {
	(HTMLInputElement.prototype as unknown as { showPicker: () => void }).showPicker = showPicker;
	showPicker.mockClear();
});

afterEach(() => {
	document.body.innerHTML = '';
});

function mountDateInput(props: Partial<Props> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	const onchange = vi.fn();
	const app = mount(DateInput, { target, props: { onchange, ...props } });
	flushSync();
	const input = target.querySelector('input');
	if (!input) throw new Error('date input did not render an input element');
	return { target, app, input, onchange };
}

describe('DateInput', () => {
	it('does not open the calendar when the field itself is clicked', () => {
		// The regression. A click is an attempt to reach a segment, not a request for the picker.
		const { app, input } = mountDateInput();

		input.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		flushSync();

		expect(showPicker).not.toHaveBeenCalled();
		unmount(app);
	});

	it('leaves the click alone, so the browser can focus the segment under the pointer', () => {
		// jsdom does not focus on click, so focus is established first and the assertion is that
		// nothing the component does takes it away or cancels the event the browser acts on.
		const { app, input } = mountDateInput();
		input.focus();

		const click = new MouseEvent('click', { bubbles: true, cancelable: true });
		input.dispatchEvent(click);
		flushSync();

		expect(click.defaultPrevented, 'a cancelled click never reaches the segment').toBe(false);
		expect(document.activeElement).toBe(input);
		unmount(app);
	});

	it('reports a date entered from the keyboard', () => {
		// What typing a complete date does: the segments fill, then the control fires `change`. This
		// is the path the picker was standing in front of.
		const { app, input, onchange } = mountDateInput();

		input.value = '1962-04-09';
		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(onchange).toHaveBeenCalledWith('1962-04-09');
		unmount(app);
	});

	it('reports a cleared field as no answer rather than an empty string', () => {
		const { app, input, onchange } = mountDateInput({ value: '1962-04-09' });

		input.value = '';
		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(onchange).toHaveBeenCalledWith(undefined);
		unmount(app);
	});

	it('keeps the native picker reachable from the indicator inside the field', () => {
		// Removing the whole-input handler must not cost the calendar altogether: it stays available
		// from `type="date"`'s own indicator, which the class below only styles.
		const { app, input } = mountDateInput();

		expect(input.getAttribute('type')).toBe('date');
		expect(input.className).toContain('[&::-webkit-calendar-picker-indicator]:cursor-pointer');
		unmount(app);
	});

	it('trims a stored time part down to the date the control can hold', () => {
		const { app, input } = mountDateInput({ value: '1962-04-09T00:00:00Z' });

		expect(input.getAttribute('value') ?? input.value).toBe('1962-04-09');
		unmount(app);
	});
});
