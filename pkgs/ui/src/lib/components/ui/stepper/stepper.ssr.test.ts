/**
 * The compliance step bar, server-rendered.
 *
 * Pins the per-state screen-reader text, that the count badge shows only for an open step with a
 * count above zero, and that steps are buttons only when the page supplies `onselect`.
 */

import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import Stepper from './stepper.svelte';
import type { Step } from './stepper.svelte';

const steps: Step[] = [
	{ key: 'identity', label: 'Identity', state: 'done' },
	{ key: 'screened', label: 'Screened', state: 'open', count: 3, hint: 'Three hits to review' },
	{ key: 'monitoring', label: 'Monitoring', state: 'advisory' },
	{ key: 'hits', label: 'Hits reviewed', state: 'waiting' },
	{ key: 'risk', label: 'Risk rating', state: 'none' }
];

describe('Stepper (SSR)', () => {
	it('renders an ordered list with one item per step', () => {
		const { body } = render(Stepper, { props: { steps } });
		expect(body).toContain('<ol');
		expect(body.match(/<li/g)?.length).toBe(5);
	});

	it('gives every state its screen-reader text', () => {
		const { body } = render(Stepper, { props: { steps } });
		expect(body).toContain(': done');
		expect(body).toContain(': needs review, 3');
		expect(body).toContain(': advisory');
		expect(body).toContain(': waiting');
		expect(body).toContain(': not started');
	});

	it('shows the count badge only for an open step with a count above zero', () => {
		const { body } = render(Stepper, { props: { steps } });
		expect(body.match(/data-slot="stepper-count"/g)?.length).toBe(1);

		const none = render(Stepper, {
			props: {
				steps: [
					{ key: 'a', label: 'A', state: 'open', count: 0 },
					{ key: 'b', label: 'B', state: 'open', count: null },
					{ key: 'c', label: 'C', state: 'done', count: 4 }
				]
			}
		});
		expect(none.body).not.toContain('data-slot="stepper-count"');
	});

	it('uses the hint as the tooltip', () => {
		const { body } = render(Stepper, { props: { steps } });
		expect(body).toContain('title="Three hits to review"');
	});

	it('marks only the active step with aria-current="step"', () => {
		const { body } = render(Stepper, { props: { steps, active: 'screened' } });
		expect(body.match(/aria-current="step"/g)?.length).toBe(1);
		const none = render(Stepper, { props: { steps } });
		expect(none.body).not.toContain('aria-current');
	});

	it('renders buttons when onselect is given', () => {
		const { body } = render(Stepper, { props: { steps, onselect: () => {} } });
		expect(body.match(/<button/g)?.length).toBe(5);
	});

	it('renders plain items, no buttons, without onselect', () => {
		const { body } = render(Stepper, { props: { steps } });
		expect(body).not.toContain('<button');
	});
});
