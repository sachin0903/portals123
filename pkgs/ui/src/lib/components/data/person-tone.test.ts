import { describe, expect, it } from 'vitest';
import { personTone } from './person-tone.js';

describe('personTone', () => {
	const names = ['Mark Cole', 'Gio Rossi', 'Ana Silva', 'Tom Hughes', 'Priya Shah', 'Lee Wong'];

	it('is deterministic: one person, one tone everywhere', () => {
		for (const n of names) expect(personTone(n)).toBe(personTone(n));
	});

	it('stays in the brand hue — no hue rotation', () => {
		for (const n of names) {
			const css = personTone(n);
			expect(css).toMatch(/oklch\(from var\(--brand\) [0-9.]+ [0-9.]+ h\)/);
			expect(css).not.toMatch(/calc\(h [+-]/);
		}
	});

	it('pairs pale fills with a dark ink and deep fills with the hero ink', () => {
		for (const n of names) {
			const css = personTone(n);
			const l = Number(/var\(--brand\) ([0-9.]+)/.exec(css)?.[1]);
			if (l > 0.7) expect(css).toContain('color: oklch(from var(--brand) 0.3');
			else expect(css).toContain('color: var(--hero-foreground)');
		}
	});

	it('spreads a handful of people across more than one tone', () => {
		expect(new Set(names.map(personTone)).size).toBeGreaterThan(1);
	});
});
