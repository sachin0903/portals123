import { describe, expect, it } from 'vitest';
import { FIELD_WIDTH_SPAN, FIELD_WIDTHS, fieldWidthSpan } from './types';

describe('fieldWidthSpan', () => {
	it('maps each width to its 12-column span', () => {
		expect(fieldWidthSpan('full')).toBe(12);
		expect(fieldWidthSpan('1/2')).toBe(6);
		expect(fieldWidthSpan('1/3')).toBe(4);
		expect(fieldWidthSpan('2/3')).toBe(8);
		expect(fieldWidthSpan('1/4')).toBe(3);
		expect(fieldWidthSpan('3/4')).toBe(9);
	});

	it('treats the legacy "half" alias as one-half', () => {
		expect(fieldWidthSpan('half')).toBe(FIELD_WIDTH_SPAN['1/2']);
	});

	it('falls back to full width for absent/unknown values', () => {
		expect(fieldWidthSpan(undefined)).toBe(12);
		expect(fieldWidthSpan(null)).toBe(12);
		expect(fieldWidthSpan('bogus')).toBe(12);
	});

	it('accepts a numeric span, clamped/rounded to 1–12', () => {
		expect(fieldWidthSpan(7)).toBe(7);
		expect(fieldWidthSpan(1)).toBe(1);
		expect(fieldWidthSpan(12)).toBe(12);
		expect(fieldWidthSpan(0)).toBe(1); // clamp up
		expect(fieldWidthSpan(20)).toBe(12); // clamp down
		expect(fieldWidthSpan(5.4)).toBe(5); // round
	});

	it('complementary fractions sum to a full row (side-by-side)', () => {
		expect(fieldWidthSpan('2/3') + fieldWidthSpan('1/3')).toBe(12);
		expect(fieldWidthSpan('3/4') + fieldWidthSpan('1/4')).toBe(12);
		expect(fieldWidthSpan('1/2') + fieldWidthSpan('1/2')).toBe(12);
	});

	it('every picker option has a span', () => {
		for (const { value } of FIELD_WIDTHS) {
			expect(FIELD_WIDTH_SPAN[value]).toBeGreaterThan(0);
		}
	});
});
