import { describe, expect, it } from 'vitest';
import { readRisk, riskMix } from './risk.js';

/**
 * The ramp decides a colour per rating, so what is worth pinning is that it stays MONOTONIC, that
 * its three anchors land exactly on the branded tokens, and that it never emits a palette colour —
 * the same guarantee tones.test.ts makes for the static tones, and for the same reason: CSS never
 * reaches svelte-check, so a leaked literal would ship.
 */
describe('riskMix', () => {
	const steps = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

	it('anchors 1 on success, the midpoint on warning and 10 on destructive', () => {
		// Exactly 0%/100% at the ends, so the extremes ARE the tokens rather than near-misses of them.
		expect(riskMix(1)).toBe('color-mix(in oklch shorter hue, var(--success), var(--warning) 0%)');
		expect(riskMix(5.5)).toBe(
			'color-mix(in oklch shorter hue, var(--success), var(--warning) 100%)'
		);
		expect(riskMix(10)).toBe(
			'color-mix(in oklch shorter hue, var(--warning), var(--destructive) 100%)'
		);
	});

	it('travels green → amber over the lower half and amber → red over the upper', () => {
		for (const step of [1, 2, 3, 4, 5]) {
			expect(riskMix(step), String(step)).toContain('var(--success), var(--warning)');
		}
		for (const step of [6, 7, 8, 9, 10]) {
			expect(riskMix(step), String(step)).toContain('var(--warning), var(--destructive)');
		}
	});

	it('is monotonic — every step is further along the ramp than the one below it', () => {
		/** A single 0..1 position across both segments, so the two halves can be compared. */
		const position = (step: number): number => {
			const mix = riskMix(step);
			const pct = Number(/(\d+(?:\.\d+)?)%/.exec(mix)![1]);
			return mix.includes('--success') ? pct / 2 : 50 + pct / 2;
		};
		const positions = steps.map(position);
		expect(positions).toEqual([...positions].sort((a, b) => a - b));
		expect(new Set(positions).size).toBe(steps.length); // no two steps share a colour
	});

	it('only ever names branded tokens — never a palette colour or a literal', () => {
		for (const step of steps) {
			const mix = riskMix(step);
			expect(mix).not.toMatch(/#[0-9a-f]{3,8}\b/i);
			expect(mix).not.toMatch(/\b(red|green|amber|emerald|rose|yellow|orange)\b/);
			// Every colour in the expression is a var(--token).
			expect(mix.match(/var\(--[a-z-]+\)/g)?.length).toBe(2);
		}
	});

	it('respects a scale that is not out of ten', () => {
		expect(riskMix(1, 7)).toContain('var(--warning) 0%');
		expect(riskMix(7, 7)).toContain('var(--destructive) 100%');
		expect(riskMix(4, 7)).toBe(
			'color-mix(in oklch shorter hue, var(--success), var(--warning) 100%)'
		);
	});

	it('clamps rather than extrapolating past either end', () => {
		expect(riskMix(0)).toBe(riskMix(1));
		expect(riskMix(-4)).toBe(riskMix(1));
		expect(riskMix(99)).toBe(riskMix(10));
	});
});

describe('readRisk', () => {
	it('reads a numeric rating, however it is typed', () => {
		expect(readRisk('8')).toMatchObject({ kind: 'rated', step: 8, of: 10 });
		expect(readRisk(8)).toMatchObject({ kind: 'rated', step: 8 });
		expect(readRisk(' 8 ')).toMatchObject({ kind: 'rated', step: 8 });
	});

	it('treats an unset rating as unrated, not as zero', () => {
		// `platform.models.risk_rating` is nullable, and a 0 would be rendered on the green end —
		// claiming the safest possible model for one nobody has rated.
		for (const value of [null, undefined, '', '   ']) {
			expect(readRisk(value)).toEqual({ kind: 'unrated', label: '—' });
		}
	});

	it('keeps free text as text rather than coercing it onto a step', () => {
		// The column is TEXT: Seccl publishes a string and a firm may type into it. "Medium" is not
		// a 7, and guessing which step it is would be inventing the number the badge exists to show.
		expect(readRisk('Medium')).toEqual({ kind: 'unrated', label: 'Medium' });
		expect(readRisk('n/a')).toEqual({ kind: 'unrated', label: 'n/a' });
	});

	it('clamps a rating outside the scale instead of breaking the cell', () => {
		expect(readRisk('0')).toMatchObject({ kind: 'rated', step: 1 });
		expect(readRisk('42')).toMatchObject({ kind: 'rated', step: 10 });
	});
});
