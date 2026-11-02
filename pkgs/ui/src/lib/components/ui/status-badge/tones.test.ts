import { describe, expect, it } from 'vitest';
import { statusToneClasses } from './tones.js';

/**
 * The tone map is the canonical place a status colour is decided for all three portals, so it is
 * worth pinning: a palette class leaking in here defeats tenant branding everywhere at once, and a
 * status silently losing its tint/ink/hairline trio is the kind of regression nothing else catches
 * (CSS never reaches svelte-check).
 */
describe('statusToneClasses', () => {
	const tones = Object.keys(statusToneClasses) as (keyof typeof statusToneClasses)[];

	it('covers every tone the product uses', () => {
		expect(tones).toEqual(['success', 'warning', 'info', 'danger', 'brand', 'primary', 'neutral']);
	});

	it.each(tones)('%s carries a surface, an ink and a hairline', (tone) => {
		const classes = statusToneClasses[tone].split(' ');
		expect(classes.some((c) => c.startsWith('bg-'))).toBe(true);
		expect(classes.some((c) => c.startsWith('text-'))).toBe(true);
		expect(classes.some((c) => c.startsWith('border-'))).toBe(true);
	});

	it.each(tones)('%s is a pure token lookup — no palette colour, no literal', (tone) => {
		const value = statusToneClasses[tone];
		// Tailwind's fixed palette (text-emerald-700 …) and raw colour literals both bypass branding.
		expect(value).not.toMatch(
			/\b(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/
		);
		expect(value).not.toMatch(/#[0-9a-f]{3}|rgba?\(|hsla?\(|oklch\(/i);
	});

	it('uses the derived subtle/strong/line trio for every status tone', () => {
		// `primary` and `neutral` are pair tokens (a foreground is defined alongside the background),
		// so they legitimately use the base tokens directly — see the note in tones.ts.
		for (const tone of tones.filter((t) => t !== 'primary' && t !== 'neutral')) {
			expect(statusToneClasses[tone]).toMatch(/bg-[a-z]+-subtle/);
			expect(statusToneClasses[tone]).toMatch(/text-[a-z]+-strong/);
			expect(statusToneClasses[tone]).toMatch(/border-[a-z]+-line/);
		}
	});

	it('maps danger onto the destructive token family', () => {
		// The tone is named for the MEANING ("danger"); the token is the design system's name for it.
		expect(statusToneClasses.danger).toContain('destructive-subtle');
	});
});
