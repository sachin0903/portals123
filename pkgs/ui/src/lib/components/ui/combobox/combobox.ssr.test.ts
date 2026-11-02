import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Combobox from './combobox.svelte';

/**
 * The box shows the SELECTION AS A PERSON READS IT.
 *
 * A value and its label are the same string only when the options are keyed by their own text.
 * Every id-keyed picker — a client, a transfer provider, a model — has them differ, and the box
 * showed the raw `value`: a client picker holding contact 22 displayed "22" above a caption
 * naming the person. `choose()` did put the label in the box, and the re-sync effect then
 * overwrote it with the value, which is what happens on every mouse pick (the click blurs the
 * field first).
 *
 * Asserted on the SERVER-rendered output deliberately: no effects run there, so this pins the
 * seeded value rather than the effect that corrects it — the state a pre-filled form is first
 * painted in.
 */
const OPTIONS = [
	{ value: '22', label: 'Craig Currie', hint: 'craig@example.test' },
	{ value: '31', label: 'Neil Peters' }
];

const box = (props: Record<string, unknown>) =>
	render(Combobox, { props: { options: OPTIONS, ...props } }).body;

describe('Combobox (SSR)', () => {
	it('shows the chosen option’s label, not its id', () => {
		const body = box({ value: '22' });
		expect(body).toContain('value="Craig Currie"');
		expect(body).not.toContain('value="22"');
	});

	it('shows nothing when nothing is chosen', () => {
		expect(box({ value: '' })).toContain('value=""');
	});

	it('falls back to the value itself for a custom entry', () => {
		// `allowCustom` accepts a typed value that is no option — it IS its own label, and a
		// register that will never be complete must not block a firm from naming what it holds.
		const body = box({ value: 'Some Ceding Scheme Ltd', allowCustom: true });
		expect(body).toContain('value="Some Ceding Scheme Ltd"');
	});

	it('shows the value when the options have not loaded yet', () => {
		// An empty option list is a real state (a picker whose data is still in flight). Showing the
		// raw value beats showing an empty box that looks like nothing was chosen.
		expect(render(Combobox, { props: { options: [], value: '22' } }).body).toContain('value="22"');
	});
});
