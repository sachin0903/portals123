import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import OnboardingStages from './onboarding-stages.svelte';
import type { PortalOnboardingStage } from './types.js';

/** SSR pins for the bare stepper primitive — no card, no link, no self-hide: the caller (the
 * home block or the /onboarding page) owns all of that. */

// No "&" in these labels — SSR HTML-escapes it to "&amp;", which would defeat a plain
// String.indexOf/toContain match against the raw label text used elsewhere in this file.
const STAGES: PortalOnboardingStage[] = [
	{ key: 'details', label: 'Getting your details', status: 'done' },
	{ key: 'verification', label: 'Verification', status: 'current' },
	{ key: 'sign', label: 'Review and sign', status: 'upcoming' }
];

describe('OnboardingStages (SSR)', () => {
	it('renders no stepper markup for an empty stage list', () => {
		// Svelte 5 SSR always emits its own hydration/anchor comments even for an empty {#if} —
		// the real assertion is "no <ol>", not "a literally empty string".
		expect(render(OnboardingStages, { props: { stages: [] } }).body).not.toContain('<ol');
	});

	it('renders every label, in order', () => {
		const body = render(OnboardingStages, { props: { stages: STAGES } }).body;
		const positions = STAGES.map((s) => body.indexOf(s.label));
		expect(positions.every((p) => p >= 0)).toBe(true);
		expect(positions).toEqual([...positions].sort((a, b) => a - b));
	});

	it('renders two stages that share a key', () => {
		// A second onboarding repeats `details`. Keyed on the key alone, Svelte throws and the
		// portal hydrates to a blank page.
		const repeated: PortalOnboardingStage[] = [
			{ key: 'details', label: 'Getting your details', status: 'done' },
			{ key: 'details', label: 'Getting your details', status: 'current' }
		];
		const body = render(OnboardingStages, { props: { stages: repeated } }).body;
		expect(body.match(/Getting your details/g)).toHaveLength(2);
	});

	it("never wraps in a Card or a link — that is the caller's job", () => {
		const body = render(OnboardingStages, { props: { stages: STAGES } }).body;
		expect(body).not.toContain('<a');
		expect(body).not.toContain('data-slot="card"');
	});
});
