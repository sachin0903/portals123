/**
 * The holding placeholder disc's two rules.
 *
 * Both exist because the obvious implementation is quietly wrong: naive initials give two different
 * Vanguard funds the same letters, and any non-deterministic tone gives one fund a different colour
 * on every render. Neither failure looks like a bug on one row — you need two rows side by side, or
 * a reload, to see it — so they are pinned here rather than left to the eye.
 */
import { describe, expect, it } from 'vitest';
import {
	INSTRUMENT_TONES,
	INSTRUMENT_TONE_CLASSES,
	instrumentInitials,
	instrumentTone,
	isCashHolding
} from './instrument-icon.js';

describe('instrumentInitials', () => {
	it('takes the first letters of the words that distinguish the holding', () => {
		expect(instrumentInitials('Global Equity Fund')).toBe('GE');
		expect(instrumentInitials('Fundsmith Equity')).toBe('FE');
	});

	it('skips the fund-name furniture, which is the whole point', () => {
		expect(instrumentInitials('Vanguard FTSE All-World UCITS ETF Acc')).toBe('VF');
		// Naively initialled these three are V+F, V+F and V+G — one disc for three holdings, which
		// is worse than no disc. Stripping the furniture first is what tells them apart.
		expect(
			new Set([
				instrumentInitials('Vanguard FTSE All-World UCITS ETF Acc'),
				instrumentInitials('Vanguard Fund Trust'),
				instrumentInitials('Vanguard Global Bond Index Fund')
			]).size
		).toBe(3);
		// ...and a noise word never gets to BE the letters while a real one is available.
		expect(instrumentInitials('Fund Trust Baillie Gifford')).toBe('BG');
	});

	it('gives a single significant word two letters rather than one lonely one', () => {
		expect(instrumentInitials('Alphabet')).toBe('AL');
		expect(instrumentInitials('Alphabet Inc')).toBe('AL');
	});

	it('ignores punctuation rather than initialling it', () => {
		expect(instrumentInitials('iShares Core MSCI World')).toBe('IC');
		expect(instrumentInitials('L&G Global Technology')).toBe('LG');
		expect(instrumentInitials('  Baillie   Gifford  ')).toBe('BG');
	});

	it('never renders an empty disc — that reads as a broken image, not a missing logo', () => {
		for (const name of ['', '   ', '---', null, undefined]) {
			expect(instrumentInitials(name).length).toBeGreaterThan(0);
		}
	});

	it('is all-noise-proof: a name with nothing left still gets its own letters', () => {
		// Falls back THROUGH the filter to the raw words rather than past them to '?'.
		expect(instrumentInitials('The Fund')).toBe('TF');
	});
});

describe('instrumentTone', () => {
	it('is stable for the same instrument — a disc that changed colour would be worse than grey', () => {
		const first = instrumentTone('Global Equity Fund', 'IE00B4L5Y983');
		for (let i = 0; i < 20; i++) {
			expect(instrumentTone('Global Equity Fund', 'IE00B4L5Y983')).toBe(first);
		}
	});

	it('keys on the ISIN when there is one, so a renamed share class keeps its disc', () => {
		expect(instrumentTone('Vanguard FTSE All-World Acc', 'IE00B3RBWM25')).toBe(
			instrumentTone('Vanguard FTSE All-World UCITS ETF', 'IE00B3RBWM25')
		);
	});

	it('falls back to the name when there is no ISIN', () => {
		expect(instrumentTone('Some Fund', null)).toBe(instrumentTone('Some Fund'));
	});

	it('spends only the tones that mean nothing else', () => {
		// success/warning/destructive carry PERFORMANCE and STATUS everywhere else in the book, so a
		// hash must not be able to reach them — a green disc would read as a gain.
		expect(INSTRUMENT_TONES).not.toContain('success');
		expect(INSTRUMENT_TONES).not.toContain('warning');
		expect(INSTRUMENT_TONES).not.toContain('danger');
	});

	it('uses the whole ramp — a hash that always returned one tone would pass every test above', () => {
		const names = Array.from({ length: 60 }, (_, i) => `Fund number ${i} Global`);
		const used = new Set(names.map((n) => instrumentTone(n)));
		expect(used.size).toBe(INSTRUMENT_TONES.length);
	});

	it('paints in TOKENS only, never a palette colour (the theme.css rule)', () => {
		for (const classes of Object.values(INSTRUMENT_TONE_CLASSES)) {
			expect(classes).not.toMatch(/\b(?:bg|text)-(?:red|green|blue|amber|slate|zinc|gray)-\d/);
		}
	});
});

describe('cash', () => {
	it('is recognised however it is cased or spaced', () => {
		expect(isCashHolding('Cash')).toBe(true);
		expect(isCashHolding(' cash ')).toBe(true);
		expect(isCashHolding('Cash Fund')).toBe(false);
	});

	it('is neutral, not a hashed colour — it is the same money in every account', () => {
		expect(instrumentTone('Cash')).toBe('neutral');
		expect(instrumentTone('Cash', 'anything')).toBe('neutral');
	});
});
