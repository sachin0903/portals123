import { describe, expect, it } from 'vitest';
import {
	formatGBPCompact,
	formatMoney,
	formatSignedMoney,
	formatUnitPrice,
	formatPct,
	formatPctPlain,
	gainDirection,
	gainTone,
	minorDigits,
	PCT_DIGITS,
	sanePct,
	splitGBP,
	splitMoney,
	splitStatFigure
} from './money';

describe('splitStatFigure', () => {
	it('sets the pence of a formatted sum apart from the pounds', () => {
		expect(splitStatFigure('£1,240.50')).toEqual({ major: '£1,240', minor: '.50' });
		expect(splitStatFigure('+£2,610.00')).toEqual({ major: '+£2,610', minor: '.00' });
		expect(splitStatFigure('-$99.99')).toEqual({ major: '-$99', minor: '.99' });
	});

	it('leaves a count, a dash and a compact figure whole', () => {
		expect(splitStatFigure('12')).toBeNull();
		expect(splitStatFigure('—')).toBeNull();
		expect(splitStatFigure('£1.2m')).toBeNull();
		expect(splitStatFigure('£15,000.00 left')).toBeNull();
	});

	it('splits a figure in any currency the formatter prints', () => {
		for (const code of ['CHF', 'SEK', 'PLN', 'ZAR', 'CZK', 'USD']) {
			const figure = formatMoney(1240.5, code);
			expect(splitStatFigure(figure)?.minor, figure).toBe('.50');
			const signed = formatSignedMoney(2610, code);
			expect(splitStatFigure(signed)?.minor, signed).toBe('.00');
		}
		expect(splitStatFigure(formatMoney(5, 'CHF'))?.major).toBe(formatMoney(5, 'CHF').slice(0, -3));
	});
});

describe('splitGBP', () => {
	it('splits pounds from pence', () => {
		expect(splitGBP(1240.5)).toEqual({ major: '£1,240', minor: '.50' });
	});

	it('keeps the grouping separators on the major part', () => {
		expect(splitGBP(1234567.89).major).toBe('£1,234,567');
	});

	it('always emits two pence digits', () => {
		expect(splitGBP(12).minor).toBe('.00');
		expect(splitGBP(0.4).minor).toBe('.40');
	});

	it('keeps the sign with the pounds for negatives', () => {
		expect(splitGBP(-99.99)).toEqual({ major: '-£99', minor: '.99' });
	});

	it('adds an explicit + when signed', () => {
		expect(splitGBP(2610, { signed: true })).toEqual({ major: '+£2,610', minor: '.00' });
		expect(splitGBP(-2610, { signed: true }).major).toBe('-£2,610');
	});

	it('rejoins to the same string the plain formatter produces', () => {
		// The split is presentational only — it must never change the figure.
		for (const amount of [0, 0.005, 7.125, -1234.56, 98765.4]) {
			const { major, minor } = splitGBP(amount);
			expect(major + minor).toBe(
				new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(amount)
			);
		}
	});
});

describe('splitMoney', () => {
	it('uses the reference-table symbol, not Intl’s disambiguated glyph', () => {
		expect(splitMoney(1240.5, { currency: 'USD' })).toEqual({ major: '$1,240', minor: '.50' });
		expect(splitMoney(1240.5, { currency: 'EUR' }).major).toBe('€1,240');
		expect(splitMoney(1240.5, { currency: 'GBP' })).toEqual(splitGBP(1240.5));
	});

	it('falls back to GBP for a malformed code rather than throwing', () => {
		expect(splitMoney(10, { currency: 'X' }).major).toBe(splitGBP(10).major);
		expect(() => splitMoney(10, { currency: 'not-a-code' })).not.toThrow();
	});
});

describe('formatMoney', () => {
	it('prints the reference-table symbol, never Intl’s US$', () => {
		expect(formatMoney(25000, 'USD')).toBe('$25,000.00');
		expect(formatMoney(25000, 'USD')).not.toContain('US$');
		expect(formatMoney(1240.5, 'AUD')).toBe('$1,240.50');
		expect(formatMoney(1240.5, 'HKD')).toBe('$1,240.50');
		expect(formatMoney(1000, 'JPY')).toBe('¥1,000');
	});
});

describe('formatUnitPrice', () => {
	it('keeps the dealing places and the reference symbol', () => {
		expect(formatUnitPrice(4.6399, 'GBP')).toBe('£4.6399');
		expect(formatUnitPrice(209, 'GBP')).toBe('£209.00');
		expect(formatUnitPrice(139.23, 'USD')).toBe('$139.23');
		expect(formatUnitPrice(139.23, 'USD')).not.toContain('US$');
	});
});

describe('formatPctPlain', () => {
	it('never signs — zero is "0.0%", where formatPct claims "+0.0%"', () => {
		expect(formatPctPlain(0)).toBe('0.0%');
		expect(formatPct(0)).toBe('+0.0%'); // the very presentation the neutral state avoids
	});

	it('keeps the magnitude formatting of its signed sibling', () => {
		expect(formatPctPlain(0.042)).toBe('4.2%');
		expect(formatPctPlain(-0.034)).toBe('-3.4%');
	});
});

describe('formatGBPCompact', () => {
	it('always compacts at the default threshold', () => {
		expect(formatGBPCompact(1_240_000)).toBe('£1.2m');
		expect(formatGBPCompact(860_000)).toBe('£860k');
		expect(formatGBPCompact(450)).toBe('£450');
	});

	it('keeps the exact figure below a caller-supplied threshold', () => {
		expect(formatGBPCompact(130_549_201, 10_000_000)).toBe('£130.5m');
		expect(formatGBPCompact(9_500_000, 10_000_000)).toBe('£9,500,000.00');
	});

	it('lower-cases the Intl-emitted suffix', () => {
		expect(formatGBPCompact(1_000_000)).not.toMatch(/M/);
	});

	it('formats billions with 2dp, keeping Intl\'s own "bn" suffix', () => {
		expect(formatGBPCompact(1_130_000_000)).toBe('£1.13bn');
		expect(formatGBPCompact(11_300_000_000)).toBe('£11.3bn');
		expect(formatGBPCompact(1_000_000_000)).toBe('£1bn');
	});

	it('keeps 1dp for millions right below the billion boundary', () => {
		expect(formatGBPCompact(999_999_999)).toBe('£1bn'); // rounds up into the billions
		expect(formatGBPCompact(950_000_000)).toBe('£950m');
	});
});

describe('sanePct', () => {
	it('passes plausible fractions through', () => {
		expect(sanePct(0.078)).toBe(0.078);
		expect(sanePct(-0.5)).toBe(-0.5);
		expect(sanePct(0)).toBe(0);
	});

	it('rejects junk cost-basis artefacts (±1000% default cap)', () => {
		expect(sanePct(12.87)).toBeNull();
		expect(sanePct(-11)).toBeNull();
		expect(sanePct(10)).toBe(10); // the cap itself is inclusive
	});

	it('rejects the unrenderable', () => {
		expect(sanePct(null)).toBeNull();
		expect(sanePct(undefined)).toBeNull();
		expect(sanePct(NaN)).toBeNull();
		expect(sanePct(Infinity)).toBeNull();
	});

	it('honours a caller-supplied cap', () => {
		expect(sanePct(3, 2)).toBeNull();
		expect(sanePct(1.5, 2)).toBe(1.5);
	});
});

describe('gainTone', () => {
	it('is muted with no figure', () => {
		expect(gainTone(null)).toBe('text-muted-foreground');
		expect(gainTone(undefined)).toBe('text-muted-foreground');
		expect(gainTone(Number.NaN)).toBe('text-muted-foreground');
	});

	it('is muted for a float residue', () => {
		expect(gainTone(5.55e-17)).toBe('text-muted-foreground');
	});

	it('is muted for money that rounds to zero pence', () => {
		expect(gainTone(-0.004)).toBe('text-muted-foreground');
	});

	it('is muted for a percentage that rounds to 0.0%', () => {
		expect(gainTone(0.0004, { digits: PCT_DIGITS })).toBe('text-muted-foreground');
		expect(gainTone(0.0006, { digits: PCT_DIGITS })).toBe('text-positive');
	});

	it('tints a positive and a negative', () => {
		expect(gainTone(12.5)).toBe('text-positive');
		expect(gainTone(-0.01)).toBe('text-negative');
	});

	it('uses the directional family when asked', () => {
		expect(gainTone(1, { family: 'directional' })).toBe('text-success-strong');
		expect(gainTone(-1, { family: 'directional' })).toBe('text-destructive');
	});
});

describe('gainDirection', () => {
	it('honours the currency precision', () => {
		expect(minorDigits('GBP')).toBe(2);
		expect(minorDigits('JPY')).toBe(0);
		expect(gainDirection(0.4, minorDigits('JPY'))).toBeNull();
		expect(gainDirection(0.4, minorDigits('GBP'))).toBe('up');
	});
});
