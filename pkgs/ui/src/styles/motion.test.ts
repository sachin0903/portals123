import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('./theme.css', import.meta.url), 'utf-8');

describe('semantic motion system', () => {
	it('defines one duration and curve per interaction meaning', () => {
		for (const token of [
			'--motion-duration-toggle: 200ms;',
			'--motion-duration-fade: 250ms;',
			'--motion-duration-reveal: 220ms;',
			'--motion-duration-expand: 500ms;',
			'--motion-duration-exit: 150ms;',
			'--motion-spring-smooth: linear(',
			'--motion-spring-bouncy: linear(',
			'--motion-ease-out: cubic-bezier(0, 0, 0.58, 1);',
			'--motion-ease-in-out: cubic-bezier(0.42, 0, 0.58, 1);'
		]) {
			expect(css).toContain(token);
		}
	});

	it('exposes behaviour-named utilities rather than parameter-named utilities', () => {
		for (const utility of [
			'.motion-menu[data-state=',
			'.motion-reveal {',
			'.motion-expand {',
			'.motion-fade {',
			'.motion-toggle {',
			'.motion-item-enter {'
		]) {
			expect(css).toContain(utility);
		}
	});

	/** The bounce is the point: at 2.5%, and then at 8%, the overshoot read as a rendering artefact
	 *  rather than a response. The smooth curve is the opposite contract and must never overshoot. */
	it('keeps the bouncy spring felt and the smooth spring monotonic', () => {
		const stops = (token: string) =>
			(css.match(new RegExp(`${token}: linear\\(([^)]*)\\)`))?.[1] ?? '')
				.split(',')
				.map((stop) => Number.parseFloat(stop.trim()))
				.filter((value) => Number.isFinite(value));

		const bouncy = stops('--motion-spring-bouncy');
		const smooth = stops('--motion-spring-smooth');

		expect(Math.max(...bouncy)).toBeGreaterThan(1.1);
		// It also rebounds back under its target rather than easing in from above.
		expect(Math.min(...bouncy.slice(bouncy.indexOf(Math.max(...bouncy))))).toBeLessThan(1);
		expect(Math.max(...smooth)).toBeLessThanOrEqual(1);
		// Both must land exactly on their target, or the animated property settles off-value.
		expect(bouncy.at(-1)).toBe(1);
		expect(smooth.at(-1)).toBe(1);
	});

	/** The palette and the region it unfolds share one spring; a fast panel around a slow list was
	 *  the shape this replaced. */
	it('drives menu reveal and region reveal from the same spring and duration', () => {
		const menu = css.match(/\.motion-menu\[data-state='open'\] \{[^}]*\}/)?.[0] ?? '';
		const reveal = css.match(/\.motion-reveal \{[^}]*\}/)?.[0] ?? '';

		for (const rule of [menu, reveal]) {
			expect(rule).toContain('var(--motion-duration-reveal)');
			expect(rule).toContain('var(--motion-spring-bouncy)');
		}
	});

	it('turns semantic geometry motion off for reduced-motion users', () => {
		const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
		expect(reduced).toContain('animation: none !important;');
		expect(reduced).toContain('transition-duration: 1ms !important;');
		expect(reduced).toContain('.motion-reveal,');
		expect(reduced).toContain('.motion-expand,');
	});
});
