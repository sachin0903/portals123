import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { compile } from 'svelte/compiler';
import { readFileSync } from 'node:fs';
import Skeleton from './skeleton.svelte';

/** The component's own emitted stylesheet. SSR `render()` returns no CSS — vite owns that — so the
 *  pruning question is asked of the compiler directly, exactly as `risk-badge.ssr.test.ts` does. */
function css(): string {
	const source = readFileSync(new URL('./skeleton.svelte', import.meta.url), 'utf8');
	return compile(source, { filename: 'skeleton.svelte', css: 'external' }).css?.code ?? '';
}

/**
 * The sweep is a SCOPED style selected by an attribute, and Svelte prunes a scoped rule whose
 * selector it cannot find in the markup. A pruned animation leaves a static grey block that renders
 * fine, type-checks fine and passes every other test — the exact failure `risk-badge.ssr.test.ts`
 * exists for, and the reason `nav-progress.svelte` keeps its class as a literal.
 *
 * So these assert on the COMPILER'S OWN OUTPUT: if the rule stops matching, the emitted CSS loses
 * the keyframes and this fails rather than the look quietly reverting.
 */
describe('Skeleton (SSR)', () => {
	it('keeps the data-slot hook the scoped rule selects on', () => {
		const { body } = render(Skeleton, { props: {} });
		expect(body).toContain('data-slot="skeleton"');
	});

	it('clips the sweep and gives it something to travel across', () => {
		const { body } = render(Skeleton, { props: {} });
		// `relative` + `overflow-hidden` are what keep the ::after band inside the block's radius;
		// without them the highlight escapes the rounded corners and paints over its neighbours.
		expect(body).toContain('relative');
		expect(body).toContain('overflow-hidden');
	});

	it('fills from the skeleton token, not muted', () => {
		// `--muted` is a 0.035 lightness step off `--card`, which is white in the light themes — a
		// page of bars at that contrast reads as EMPTY rather than loading, which is what was
		// reported. `--skeleton` is the token tuned against the surface, per theme and per chrome.
		const { body } = render(Skeleton, { props: {} });
		expect(body).toContain('bg-skeleton');
		expect(body).not.toContain('bg-muted');
	});

	it('is a travelling sweep, not a pulse — and the rule SURVIVES compilation', () => {
		const emitted = css();
		// The half that goes missing silently: a selector the compiler cannot match is dropped, and
		// the block still renders (grey, static) with every other test green.
		// The attribute and the pseudo-element are asserted apart: Svelte splices its scope hash
		// between them (`[data-slot='skeleton'].svelte-9pyl43::after`), and a hash is a build detail
		// this must not pin — the same reason `risk-badge.ssr.test.ts` keeps off it.
		expect(emitted).toContain("[data-slot='skeleton']");
		expect(emitted).toContain('::after');
		expect(emitted).toContain('skeleton-sweep');
		expect(emitted).toContain('translateX(100%)');
		// It must move ACROSS, not fade: the pulse this replaced animated opacity.
		expect(emitted).not.toContain('animate-pulse');
	});

	it('keeps the sweep honest in both themes and under reduced motion', () => {
		const emitted = css();
		// Tinted from the surface token, so a firm re-branding re-tints this too — and no palette
		// colour is named here, the rule `tones.ts` sets.
		expect(emitted).toContain('var(--card)');
		expect(emitted).toContain('prefers-reduced-motion');
	});

	it('honours a caller’s own classes', () => {
		const { body } = render(Skeleton, { props: { class: 'size-4 rounded-full' } });
		expect(body).toContain('size-4');
		expect(body).toContain('rounded-full');
		// The hook the scoped rule needs is not a class, so a caller's list cannot displace it.
		expect(body).toContain('data-slot="skeleton"');
	});
});
