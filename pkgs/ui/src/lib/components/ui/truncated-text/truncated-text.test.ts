import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import TruncatedText from './truncated-text.svelte';
import { overflows } from './overflow.js';

describe('overflows', () => {
	it('needs more than a rounding error to call a box clipped', () => {
		// `scrollWidth`/`clientWidth` are integers rounded from fractional layout, so a box that fits
		// exactly can report 1px of overflow. Without the tolerance every cell in the table would
		// claim to be truncated and show a tooltip repeating text you can already read.
		expect(overflows(100, 100)).toBe(false);
		expect(overflows(101, 100)).toBe(false);
		expect(overflows(102, 100)).toBe(true);
	});

	it('is never true for a box wider than its content', () => {
		expect(overflows(80, 200)).toBe(false);
	});
});

describe('TruncatedText (SSR)', () => {
	it('renders the text, and clips it', () => {
		const body = render(TruncatedText, { props: { text: 'Christopher Wetherington-Smythe' } }).body;
		expect(body).toContain('Christopher Wetherington-Smythe');
		expect(body).toContain('truncate');
	});

	it('is an anchor when given an href, so a row keeps its middle-click', () => {
		const body = render(TruncatedText, { props: { text: 'Max Oakden', href: '/clients/4' } }).body;
		expect(body).toContain('href="/clients/4"');
		expect(body).toContain('<a');
	});

	it('is a span when not', () => {
		const body = render(TruncatedText, { props: { text: 'Mark Alford' } }).body;
		expect(body).not.toContain('<a ');
	});

	it('shows NOTHING on the server — the tooltip is gated on a measurement', () => {
		// The gate is the feature: a tooltip is offered only once the box is really clipping, which
		// cannot be known until layout exists. So SSR must emit the text alone; a tooltip in the
		// server output would mean the gate had been bypassed and every cell would carry one.
		const body = render(TruncatedText, { props: { text: 'Fits fine' } }).body;
		expect(body).not.toContain('data-slot="tooltip-content"');
	});

	it('does not put the summary in place of the full value', () => {
		// `full` is for a column that SUMMARISES ("Zoe Lead +2"): the point of hovering is the three
		// names, not the summary again. Both strings must survive to the client for that to work.
		const body = render(TruncatedText, {
			props: { text: 'Zoe Lead +2', full: 'Zoe Lead, Amelia Bright, Mark Alford' }
		}).body;
		expect(body).toContain('Zoe Lead +2');
	});
});
