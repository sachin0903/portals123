/** The clear affordance's place in both layouts. Rendered rather than scanned as source, because the defect was a LAYOUT one: a class-string assertion passes while the button sits in the wrong place. */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import PropertyRow from './property-row.svelte';

// No `as never` on the component: that cast makes svelte's `props` parameter `never`, so every
// property in the literal below was an error the moment this file was type-checked at all. The
// component's own prop types are what this should be checked against.
const html = (props: Record<string, unknown>) =>
	render(PropertyRow, {
		props: { label: 'Padding', set: true, onclear: () => {}, children: () => {}, ...props } as never
	}).body;

describe('PropertyRow’s clear button', () => {
	it('shares a header line with the label when stacked', () => {
		const body = html({ layout: 'stack', hint: 'The inset from the sheet’s edges.' });
		// The header row comes BEFORE the control, so the × cannot fall underneath the hint.
		const header = /<div class="flex min-w-0 items-center gap-2">([\s\S]*?)<\/div>\s*<div/.exec(
			body
		);
		expect(header, 'no header row was rendered for the stacked layout').not.toBeNull();
		expect(header?.[1]).toContain('Padding');
		expect(header?.[1]).toContain('aria-label="Clear Padding"');
	});

	it('puts the hint under the control, never between the label and the clear', () => {
		const body = html({ layout: 'stack', hint: 'A rule worth stating.' });
		expect(body.indexOf('aria-label="Clear Padding"')).toBeLessThan(
			body.indexOf('A rule worth stating.')
		);
	});

	it('keeps the trailing position in a row', () => {
		const body = html({ layout: 'row' });
		expect(body.indexOf('Padding')).toBeLessThan(body.indexOf('aria-label="Clear Padding"'));
	});

	it('holds NOTHING where there is nothing to clear, in either layout', () => {
		// It used to hold an empty 20px slot so the right edges lined up down a rail that mixed the two.
		// The style panel no longer clears anything, so that was reserved nothing on every row.
		for (const layout of ['row', 'stack'])
			expect(html({ layout, onclear: undefined }), layout).not.toContain('aria-hidden="true"');
	});

	it('offsets the button only in a row', () => {
		// On the stacked header the nudge aligns against the label, reading as the misalignment it fixes.
		expect(html({ layout: 'row' })).toMatch(/class="[^"]*mt-0\.5[^"]*"/);
		const stacked = /<button[^>]*aria-label="Clear Padding"[^>]*>/.exec(html({ layout: 'stack' }));
		expect(stacked?.[0]).not.toContain('mt-0.5');
	});
});
