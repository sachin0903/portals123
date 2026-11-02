import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { existingSippTotal } from './client';
import { ageOn } from './client-panel.svelte';

/**
 * Age was computed twice — once in the drawer, once in the full-page composer that hosted the same
 * form — with the "birthday not yet reached this year" correction written out longhand in both. It is
 * one function now, and this pins the boundary, because the projection term is measured from it.
 */
describe('ageOn', () => {
	const on = (iso: string) => new Date(`${iso}T12:00:00Z`);

	it('counts a birthday already passed this year', () => {
		expect(ageOn('1970-03-15', on('2026-08-04'))).toBe(56);
	});

	it('does not count a birthday still to come this year', () => {
		expect(ageOn('1970-11-15', on('2026-08-04'))).toBe(55);
	});

	it('counts the birthday itself', () => {
		expect(ageOn('1970-08-04', on('2026-08-04'))).toBe(56);
	});

	it('does not count the day before', () => {
		expect(ageOn('1970-08-05', on('2026-08-04'))).toBe(55);
	});

	it('returns null rather than NaN for an absent or unparseable date', () => {
		// The panel prints this straight into a <dd>, and the drawer blocks on it — "NaN" as a
		// client's age on a projection is worse than an em dash.
		expect(ageOn(null)).toBeNull();
		expect(ageOn(undefined)).toBeNull();
		expect(ageOn('')).toBeNull();
		expect(ageOn('not a date')).toBeNull();
	});
});

describe('the illustration client panel', () => {
	const source = readFileSync(new URL('./client-panel.svelte', import.meta.url), 'utf8');

	it('shows the SIPP total in large type, and only when the client has one', () => {
		expect(source).toContain('{#if existingSippTotal != null}');
		expect(source).toContain('Existing SIPPs');
		expect(source).toContain('<Money amount={existingSippTotal} />');
		expect(source).toContain('text-2xl');
	});

	it('states a recorded marital status and does not print a retirement age', () => {
		// A marital status the record already holds is text. Printing a retirement age here is how
		// the form looked already answered; that age is an empty field on the form below.
		expect(source).not.toContain('client.retirement_age');
		expect(source).toContain('termNote');
		expect(source).toContain('maritalLabel(client.marital_status)');
		expect(source).not.toContain('<Input');
		expect(source).not.toContain('<SelectMenu');
		expect(source).not.toContain('fetch(');
		expect(source).not.toContain('setRetirementAge');
		expect(source).not.toContain('setMaritalStatus');
	});
});

describe('existingSippTotal', () => {
	it('sums SIPP accounts and ignores every other wrapper', () => {
		expect(
			existingSippTotal([
				{ product_type: 'SIPP', value: 10000 },
				{ product_type: 'sipp', value: '2500.5' },
				{ product_type: 'ISA', value: 99999 },
				{ product_type: 'PENSION', value: 4000 }
			])
		).toBe(12500.5);
	});

	it('is null when the client has no SIPP, and zero when a SIPP is worth nothing', () => {
		expect(existingSippTotal([])).toBeNull();
		expect(existingSippTotal(null)).toBeNull();
		expect(existingSippTotal([{ product_type: 'GIA', value: 10 }])).toBeNull();
		expect(existingSippTotal([{ product_type: 'SIPP', value: null }])).toBe(0);
	});
});
