import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Combobox from './combobox.svelte';

/**
 * Picking with the MOUSE, which is the path that was broken.
 *
 * An SSR assertion pins the seeded value; it cannot see this. A click blurs the field before the
 * option is chosen, and the old single piece of state was then re-synced from `value` — so the box
 * showed the id it had just been given. Only a mounted component can be clicked.
 *
 * `toFake` is narrowed so `setTimeout` stays real: faking it freezes vitest's worker heartbeat and
 * the run dies with "Timeout waiting for worker to respond", which reads as a hang rather than a
 * failure (see the pkgs/ui note in CLAUDE.md).
 */
vi.useFakeTimers({ toFake: ['Date'] });

const OPTIONS = [
	{ value: '22', label: 'Craig Currie', hint: 'craig@example.test' },
	{ value: '31', label: 'Neil Peters' }
];

function open(props: Record<string, unknown> = {}) {
	const host = document.createElement('div');
	document.body.appendChild(host);
	const component = mount(Combobox, {
		target: host,
		props: { options: OPTIONS, value: '', ...props }
	});
	const input = host.querySelector('input') as HTMLInputElement;
	return {
		host,
		input,
		rows: () => [...host.querySelectorAll('[role="option"]')] as HTMLElement[],
		done: () => {
			unmount(component);
			host.remove();
		}
	};
}

describe('Combobox (DOM)', () => {
	it('shows the name after a mouse pick, not the id', () => {
		const ui = open();
		ui.input.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		const row = ui.rows().find((r) => r.textContent?.includes('Craig Currie'));
		expect(row).toBeDefined();
		// The row commits on MOUSEDOWN (a click would blur the input first and close the list
		// before the selection landed), and the blur then follows — which is the order that broke
		// this: the pick put the label in the box, and the blur handler put the id back.
		row?.querySelector('button')?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();
		ui.input.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(ui.input.value).toBe('Craig Currie');
		ui.done();
	});

	it('shows the name again after typing a partial search and leaving', () => {
		// The buffer is discarded on blur; the selection is what the box goes back to.
		const ui = open({ value: '22' });
		expect(ui.input.value).toBe('Craig Currie');
		ui.input.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		ui.input.value = 'Cra';
		ui.input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(ui.input.value).toBe('Cra');
		ui.input.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(ui.input.value).toBe('Craig Currie');
		ui.done();
	});

	it('opens on the full list even when a selection is already showing', () => {
		// The "box still reads as the selection" test compares against the LABEL. Against the value
		// it never matched, so a filled id-keyed picker opened filtered down to nothing.
		const ui = open({ value: '22' });
		ui.input.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		expect(ui.rows().length).toBe(OPTIONS.length);
		ui.done();
	});
});

describe('Combobox (DOM) with allowCustom', () => {
	it('does not commit the label as the value when the field is merely visited', () => {
		// `allowCustom` commits free text on the way out, and the buffer is seeded with the current
		// selection's LABEL on focus — so tabbing through an id-keyed picker rewrote the value with
		// the label. StepInspector's mapping rows are exactly that: `{value: 'dob', label: 'Date of
		// birth · dob'}`, and a visit turned the stored key into the label.
		const seen: string[] = [];
		const host = document.createElement('div');
		document.body.appendChild(host);
		const component = mount(Combobox, {
			target: host,
			props: {
				options: OPTIONS,
				allowCustom: true,
				get value() {
					return '22';
				},
				set value(v: string) {
					seen.push(v);
				}
			}
		});
		const input = host.querySelector('input') as HTMLInputElement;
		input.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		input.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(seen).toEqual([]);
		unmount(component);
		host.remove();
	});

	it('still commits what the user actually typed', () => {
		const seen: string[] = [];
		const host = document.createElement('div');
		document.body.appendChild(host);
		const component = mount(Combobox, {
			target: host,
			props: {
				options: OPTIONS,
				allowCustom: true,
				get value() {
					return '';
				},
				set value(v: string) {
					seen.push(v);
				}
			}
		});
		const input = host.querySelector('input') as HTMLInputElement;
		input.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		input.value = 'Some Ceding Scheme Ltd';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		input.dispatchEvent(new FocusEvent('blur'));
		flushSync();
		expect(seen).toEqual(['Some Ceding Scheme Ltd']);
		unmount(component);
		host.remove();
	});
});
