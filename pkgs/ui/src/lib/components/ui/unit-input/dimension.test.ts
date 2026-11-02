import { describe, expect, it } from 'vitest';
import { defaultUnit, joinDimension, splitDimension } from './dimension';

/** Mostly the REFUSALS: values come from GrapesJS, so `auto`, a bare `0` and a `calc()` all have to survive being edited without being rewritten. */

const UNITS = ['px', 'em', '%'] as const;

describe('splitDimension', () => {
	it('splits a number from a known unit', () => {
		expect(splitDimension('600px', UNITS)).toEqual({ value: '600', unit: 'px', keyword: false });
		expect(splitDimension('1.5em', UNITS)).toEqual({ value: '1.5', unit: 'em', keyword: false });
		expect(splitDimension('50%', UNITS)).toEqual({ value: '50', unit: '%', keyword: false });
	});

	it('keeps the author’s own numeric text', () => {
		expect(splitDimension('1.50em', UNITS).value).toBe('1.50');
		expect(splitDimension('.5em', UNITS).value).toBe('.5');
		expect(splitDimension('-4px', UNITS).value).toBe('-4');
	});

	it('reads a unitless number as a number, not a keyword', () => {
		expect(splitDimension('0', UNITS)).toEqual({ value: '0', unit: null, keyword: false });
		expect(splitDimension('12', UNITS)).toEqual({ value: '12', unit: null, keyword: false });
	});

	it('holds a keyword whole', () => {
		expect(splitDimension('auto', UNITS)).toEqual({ value: 'auto', unit: null, keyword: true });
		expect(splitDimension('inherit', UNITS).keyword).toBe(true);
	});

	it('holds an expression whole rather than half-reading it', () => {
		// The leading `100%` would otherwise be split off and the rest silently dropped.
		const calc = splitDimension('calc(100% - 20px)', UNITS);
		expect(calc).toEqual({ value: 'calc(100% - 20px)', unit: null, keyword: true });
	});

	it('holds a Jinja token whole', () => {
		expect(splitDimension('{{ brand.width }}', UNITS).keyword).toBe(true);
		expect(splitDimension('{{ brand.width }}', UNITS).value).toBe('{{ brand.width }}');
	});

	it('treats a unit it was not offered as a keyword', () => {
		// The unit list is an email-safety boundary: `3vh` would be a rail value the control cannot show.
		expect(splitDimension('3vh', UNITS).keyword).toBe(true);
		expect(splitDimension('3vh', UNITS).value).toBe('3vh');
		expect(splitDimension('50%', ['px', 'em']).keyword).toBe(true);
	});

	it('reads blank as blank, not as zero', () => {
		expect(splitDimension('', UNITS)).toEqual({ value: '', unit: null, keyword: false });
		expect(splitDimension(null, UNITS).value).toBe('');
		expect(splitDimension(undefined, UNITS).value).toBe('');
	});

	it('tolerates surrounding whitespace', () => {
		expect(splitDimension('  600px  ', UNITS)).toEqual({
			value: '600',
			unit: 'px',
			keyword: false
		});
	});
});

describe('joinDimension', () => {
	it('rejoins a number and a unit', () => {
		expect(joinDimension('600', 'px')).toBe('600px');
		expect(joinDimension('1.5', 'em')).toBe('1.5em');
	});

	it('answers empty for a cleared value whatever the unit', () => {
		expect(joinDimension('', 'px')).toBe('');
		expect(joinDimension('   ', 'px')).toBe('');
	});

	it('leaves a number unitless when there is no unit', () => {
		expect(joinDimension('0', null)).toBe('0');
		expect(joinDimension('0', undefined)).toBe('0');
	});

	it('never appends a unit to a keyword', () => {
		// The bug this exists to stop is `autopx`.
		expect(joinDimension('auto', 'px')).toBe('auto');
		expect(joinDimension('calc(100% - 20px)', 'px')).toBe('calc(100% - 20px)');
		expect(joinDimension('{{ brand.width }}', 'px')).toBe('{{ brand.width }}');
	});

	it('round-trips through splitDimension', () => {
		for (const raw of ['600px', '1.5em', '50%', '0', 'auto', 'calc(100% - 20px)', '']) {
			const d = splitDimension(raw, UNITS);
			expect(joinDimension(d.value, d.unit)).toBe(raw.trim());
		}
	});
});

describe('defaultUnit', () => {
	it('offers the first declared unit', () => {
		expect(defaultUnit(UNITS)).toBe('px');
		expect(defaultUnit(['em', 'px'])).toBe('em');
	});

	it('answers null when the control offers none', () => {
		expect(defaultUnit([])).toBeNull();
	});
});

describe('a value that is not a string', () => {
	// GrapesJS answers a numeric property with a NUMBER and `(raw ?? '').trim()` threw inside a render batch, so a control in a SIBLING section never wired up ("the Visibility dropdowns will not open").
	it('reads a number as the number it is', () => {
		expect(splitDimension(12, ['px'])).toEqual({ value: '12', unit: null, keyword: false });
		expect(splitDimension(0, ['px'])).toEqual({ value: '0', unit: null, keyword: false });
	});

	it('takes a unit from a numeric value, so a cleared box is still clearable', () => {
		expect(joinDimension(12, 'px')).toBe('12px');
		expect(joinDimension(0, 'px')).toBe('0px');
	});

	it('treats null and undefined as empty rather than throwing', () => {
		expect(splitDimension(null)).toEqual({ value: '', unit: null, keyword: false });
		expect(splitDimension(undefined)).toEqual({ value: '', unit: null, keyword: false });
		expect(joinDimension(null, 'px')).toBe('');
	});
});

describe('a value that already carries a unit', () => {
	// THE BUG THIS PINS. The guard was `NUMBER.test`, and NUMBER ends `(.*)$` so it captures the tail
	// — true for anything merely STARTING with a digit. Typing 100% into a px field committed
	// '100%px', splitDimension then read '%px' as an unknown unit, and the invalid declaration
	// silently unset the property. The old tests covered auto/calc()/tokens and never this.
	it('is passed through whole, never suffixed', () => {
		expect(joinDimension('100%', 'px')).toBe('100%');
		expect(joinDimension('2em', 'px')).toBe('2em');
		expect(joinDimension('1.5rem', 'px')).toBe('1.5rem');
	});

	it('still takes a unit on a BARE number, sign and decimal included', () => {
		expect(joinDimension('100', 'px')).toBe('100px');
		expect(joinDimension('-2.5', 'em')).toBe('-2.5em');
		expect(joinDimension('.5', 'px')).toBe('.5px');
	});

	it('round-trips what it produced, which is the property that broke', () => {
		for (const raw of ['100%', '2em', '100', '-2.5', 'auto', 'calc(100% - 10px)']) {
			const split = splitDimension(raw, ['px', '%', 'em']);
			expect(joinDimension(split.value, split.unit)).toBe(raw);
		}
	});
});
