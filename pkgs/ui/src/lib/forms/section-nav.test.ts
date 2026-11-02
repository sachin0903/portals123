import { describe, expect, it } from 'vitest';
import { SECTION_NAV_MIN_FIELDS, wantsSectionNav } from './section-nav.js';

const fields = (n: number, section: string | null, hidden = false) =>
	Array.from({ length: n }, () => ({ section, validation: hidden ? { hidden: true } : null }));

describe('wantsSectionNav', () => {
	it('wants it for a form of several sections that opens a screen long', () => {
		expect(wantsSectionNav([...fields(9, 'Personal details'), ...fields(11, 'Address')])).toBe(
			true
		);
	});

	it('does not want it for a short form, however many sections it has', () => {
		// The SIPP illustration: six sections, but it opens with six fields. Scrolling is quicker.
		const short = [1, 2, 3, 4, 5, 6].flatMap((i) => fields(1, `Section ${i}`));
		expect(wantsSectionNav(short)).toBe(false);
	});

	it('draws the line at SECTION_NAV_MIN_FIELDS', () => {
		const at = (n: number) => [...fields(1, 'A'), ...fields(n - 1, 'B')];
		expect(wantsSectionNav(at(SECTION_NAV_MIN_FIELDS))).toBe(true);
		expect(wantsSectionNav(at(SECTION_NAV_MIN_FIELDS - 1))).toBe(false);
	});

	it('does not want it for one section, however long', () => {
		// A list with one destination navigates nowhere.
		expect(wantsSectionNav(fields(40, 'Personal details'))).toBe(false);
		expect(wantsSectionNav(fields(40, null))).toBe(false);
	});

	it('counts an optional section only when it opens switched on', () => {
		const form = [...fields(6, 'Plan'), ...fields(10, 'Contributions')];
		expect(wantsSectionNav(form, { Contributions: { optional: true } })).toBe(false);
		expect(wantsSectionNav(form, { Contributions: { optional: true, defaultOn: true } })).toBe(
			true
		);
		expect(wantsSectionNav(form)).toBe(true);
	});

	it('ignores hidden sidecar fields, which render nothing', () => {
		expect(wantsSectionNav([...fields(2, 'A'), ...fields(20, 'B', true)])).toBe(false);
	});
});
