import { describe, expect, it } from 'vitest';
import { asAt, britishDate, periodBasis } from './provenance.js';

describe('britishDate', () => {
	it('formats an ISO date in British display form', () => {
		expect(britishDate('2026-07-29')).toBe('29 Jul 2026');
		expect(britishDate('2026-01-05')).toBe('5 Jan 2026');
	});

	it('reads a stamp that carries a time by its date part, whatever the reader’s zone', () => {
		expect(britishDate('2026-07-29T23:30:00Z')).toBe('29 Jul 2026');
	});

	it('formats in UTC — a date-only stamp must not shift a day west of Greenwich', () => {
		// Date.parse('…') on a date-only string is UTC midnight; a local-zone formatter in,
		// say, America/New_York would print 28 Jul. The suite runs in whatever zone CI has,
		// so this only genuinely bites on non-UTC machines — which is exactly the point.
		expect(britishDate('2026-07-29')).toBe('29 Jul 2026');
	});

	it("junk is '' — never a fabricated date", () => {
		expect(britishDate('yesterday')).toBe('');
		expect(britishDate('2026-99-99')).toBe('');
		expect(britishDate('')).toBe('');
		expect(britishDate(null)).toBe('');
		expect(britishDate(undefined)).toBe('');
	});

	it('accepts a full timestamp (the date part leads)', () => {
		expect(britishDate('2026-07-29T09:30:00Z')).toBe('29 Jul 2026');
	});
});

describe('asAt', () => {
	it('wraps the date in the stamp phrase', () => {
		expect(asAt('2026-07-29')).toBe('as at 29 Jul 2026');
	});

	it("is '' for junk/absent, so callers' {#if} suppresses the stamp", () => {
		expect(asAt(null)).toBe('');
		expect(asAt(undefined)).toBe('');
		expect(asAt('not a date')).toBe('');
	});
});

describe('periodBasis', () => {
	it("renders the series end — 'to 29 Jul 2026'", () => {
		expect(periodBasis({ from: '2026-01-29', to: '2026-07-29' })).toBe('to 29 Jul 2026');
	});

	it("is '' when the period is absent or its end is junk", () => {
		expect(periodBasis(null)).toBe('');
		expect(periodBasis(undefined)).toBe('');
		expect(periodBasis({ from: '2026-01-29', to: 'junk' })).toBe('');
		expect(periodBasis({ from: '2026-01-29', to: null })).toBe('');
	});
});
