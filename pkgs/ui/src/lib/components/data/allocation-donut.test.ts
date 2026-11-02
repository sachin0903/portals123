import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AllocationDonut from './allocation-donut.svelte';
import type { AllocationSegment } from '../book/clients/allocation.js';

const SOURCE = readFileSync(join(import.meta.dirname, 'allocation-donut.svelte'), 'utf8');

const segments: AllocationSegment[] = [
	{ label: 'Equities', value: 2_876_551.04, pct: 80.7, color: '#c9a227', offset: 25 },
	{ label: 'Fixed income', value: 603_730.61, pct: 16.9, color: '#6b8cae', offset: -55.7 },
	{ label: 'Alternatives', value: 70_182, pct: 2.0, color: '#c4b59a', offset: -72.6 },
	{ label: 'Cash', value: 15_671.15, pct: 0.4, color: '#7a9e7e', offset: -74.6 }
];

describe('AllocationDonut', () => {
	it('does not truncate a class name to make room for the figures', () => {
		// "Equities" became "Equ…" because the label wore `truncate` against two shrink-0 figure
		// columns and a 10rem ring. The name wraps in the middle column; the figures sit to
		// its right.
		expect(SOURCE).not.toContain('truncate');
		expect(SOURCE).toContain('WRAP_NAME');
		expect(SOURCE).toContain('minmax(0,1fr)');
	});

	it('keeps the ring on the left and the key on the right, even when it prints the money', () => {
		const { body } = render(AllocationDonut, {
			props: { segments, size: 'lg', value: (seg) => `£${seg.value.toFixed(2)}` }
		});
		expect(body).toContain('items-center');
		expect(body).not.toContain('flex-col');
		expect(body).toContain('size-24');
		expect(body).toContain('Equities');
		expect(body).toContain('Fixed income');
		expect(body).toContain('Alternatives');
		expect(body).toContain('£2876551.04');
	});

	it('splits a legend amount into pounds and pence when given `amount`', () => {
		const { body } = render(AllocationDonut, {
			props: { segments, size: 'lg', amount: (seg) => seg.value, currency: 'GBP' }
		});
		expect(body).toContain('£2,876,551');
		expect(body).toMatch(/£2,876,551<span[^>]*>\.04<\/span>/);
	});

	it('keeps the compact ring in the client rail, where there is no money column', () => {
		const { body } = render(AllocationDonut, { props: { segments } });
		expect(body).toContain('size-20');
		expect(body).toContain('Equities');
		expect(body).toContain('80.7%');
	});

	it('lights a class on the ring and its key row together', () => {
		expect(SOURCE).toContain('hovered');
		expect(SOURCE).toContain('pointer-events: stroke');
		expect(SOURCE).toContain('onmouseenter');
	});

	it('centres a caption under the empty ring, rather than a second widget beside it', () => {
		const { body } = render(AllocationDonut, { props: { segments: [], size: 'lg' } });
		expect(body).toContain('size-24');
		expect(body).toContain('flex-col items-center');
		expect(body).toContain('No allocation yet');
		expect(body).not.toContain('Shares appear once holdings land.');
		expect(body).not.toContain('data-slot="empty-state"');
		expect(body).not.toContain('Equities');
	});
});
