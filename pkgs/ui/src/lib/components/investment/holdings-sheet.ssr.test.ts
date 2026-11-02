/**
 * FB-20260921-09: an account named at length ("TPOFFSHORE Bond – …") set the Account column's
 * width with `whitespace-nowrap` and the sheet ran past its column. The cell is capped and
 * truncated, with the full name on the title.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import HoldingsSheet from './holdings-sheet.svelte';
import type { Holding } from './types.js';

const holding = (over: Partial<Holding> = {}): Holding => ({
	name: 'Vanguard LifeStrategy 60%',
	wrapper: 'TPOFFSHORE Bond – Mr and Mrs Longname-Hyphenated',
	units: 100,
	price: 2.5,
	value: 250,
	gainPct: null,
	...over
});

describe('HoldingsSheet account column', () => {
	it('caps and truncates the account name, keeping the whole of it as the title', () => {
		const { body } = render(HoldingsSheet, { props: { holdings: [holding()] } });
		expect(body).toContain(
			'<span class="block max-w-48 truncate" title="TPOFFSHORE Bond – Mr and Mrs Longname-Hyphenated">'
		);
		expect(body).not.toMatch(/<td[^>]*whitespace-nowrap[^>]*>TPOFFSHORE/);
	});
});
