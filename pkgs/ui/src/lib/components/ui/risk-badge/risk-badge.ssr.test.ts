import { describe, expect, it } from 'vitest';
import { compile } from 'svelte/compiler';
import { readFileSync } from 'node:fs';
import { render } from 'svelte/server';
import RiskBadge from './risk-badge.svelte';

/**
 * SSR pins for the badge itself: the ramp colour reaches the element, the NUMBER is always rendered
 * (colour never carries the meaning alone), and an unratable value falls back to the neutral status
 * pill rather than being coerced onto a step.
 */
describe('RiskBadge (SSR)', () => {
	it('renders the rating as text, not only as a colour', () => {
		const body = render(RiskBadge, { props: { rating: '8' } }).body;
		expect(body).toContain('>8<');
	});

	it('carries the ramp colour as --risk on the element', () => {
		const body = render(RiskBadge, { props: { rating: '10' } }).body;
		expect(body).toContain('--risk: color-mix(in oklch shorter hue');
		expect(body).toContain('var(--destructive) 100%');
	});

	it('names the scale for anyone who cannot compare two tints', () => {
		const body = render(RiskBadge, { props: { rating: 3 } }).body;
		expect(body).toContain('title="Risk 3 of 10"');
	});

	it('low risk sits on the success end and high risk on the destructive end', () => {
		const low = render(RiskBadge, { props: { rating: 1 } }).body;
		const high = render(RiskBadge, { props: { rating: 10 } }).body;
		expect(low).toContain('var(--success), var(--warning) 0%');
		expect(high).toContain('var(--warning), var(--destructive) 100%');
	});

	it('an unset rating is a neutral pill showing a dash — never a green 0', () => {
		const body = render(RiskBadge, { props: { rating: null } }).body;
		expect(body).toContain('—');
		expect(body).not.toContain('--risk:');
		expect(body).toContain('bg-muted');
	});

	it('free text stays text', () => {
		const body = render(RiskBadge, { props: { rating: 'Medium' } }).body;
		expect(body).toContain('Medium');
		expect(body).not.toContain('--risk:');
	});

	it('honours a scale that is not out of ten', () => {
		const body = render(RiskBadge, { props: { rating: 7, of: 7 } }).body;
		expect(body).toContain('title="Risk 7 of 7"');
		expect(body).toContain('var(--destructive) 100%');
	});

	it('keeps its scoped style, and the element keeps the scope class', () => {
		// THE FRAGILE PART. The three rungs are a scoped `<style>`, and the element's class comes out
		// of `cn(...)` — a function call the compiler cannot look inside. Svelte prunes scoped CSS it
		// cannot prove is used, so a refactor that stops it matching `.risk-badge` would delete the
		// colour outright while every other test here still passed: the badge would render, carry
		// `--risk`, and be plain grey. Pinned on the compiler's own output.
		const source = readFileSync(new URL('./risk-badge.svelte', import.meta.url), 'utf8');
		const compiled = compile(source, { filename: 'risk-badge.svelte', css: 'external' });
		expect(compiled.css?.code).toContain('.risk-badge');
		expect(compiled.css?.code).toContain('oklch(from var(--risk)');
		// All three rungs, not just the surface.
		for (const rung of ['--tone-subtle-l', '--tone-strong-l', '--tone-line-l']) {
			expect(compiled.css?.code, rung).toContain(rung);
		}
		// The emitted selector is `.risk-badge.svelte-<hash>`, so BOTH classes have to be on the
		// element. The hash itself is not compared across the two compilations — it varies with the
		// compiler options, so this build's hash and vite's legitimately differ; what matters is that
		// the rendered element carries the base class AND a scope class at all.
		expect(compiled.css?.code).toMatch(/\.risk-badge\.svelte-[a-z0-9]+/);
		const rendered = render(RiskBadge, { props: { rating: 5 } }).body;
		expect(rendered).toMatch(/class="[^"]*\brisk-badge\b[^"]*\bsvelte-[a-z0-9]+\b/);
	});

	it('never emits a palette colour or a hex literal', () => {
		// The trio is derived in CSS from --risk, so the markup should carry token expressions only.
		for (const rating of [1, 4, 6, 9]) {
			const body = render(RiskBadge, { props: { rating } }).body;
			expect(body, String(rating)).not.toMatch(/#[0-9a-f]{3,8}\b/i);
			expect(body, String(rating)).not.toMatch(/\b(emerald|rose|amber|lime)-\d{3}\b/);
		}
	});
});
