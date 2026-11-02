import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import OnboardingMilestones from './onboarding-milestones.svelte';
import type { PortalOnboarding } from './types.js';

/**
 * SSR pins for the 'milestones' onboarding look: the self-hide rule (shared with the other two
 * looks), the stage-stepper rendering when `stages` is present, and the flat-bar fallback when
 * it is not (an older cached response).
 */

// No "&" in these labels — SSR HTML-escapes it, which would defeat a plain toContain match.
const STAGES: PortalOnboarding['stages'] = [
	{ key: 'details', label: 'Getting your details', status: 'done' },
	{ key: 'verification', label: 'Verification', status: 'current' },
	{ key: 'sign', label: 'Review and sign', status: 'upcoming' },
	{ key: 'setup', label: 'Account setup', status: 'upcoming' },
	{ key: 'complete', label: 'Complete', status: 'upcoming' }
];

const html = (onboarding: PortalOnboarding | null, interactive?: boolean): string =>
	render(OnboardingMilestones, {
		props: interactive === undefined ? { onboarding } : { onboarding, interactive }
	}).body;

describe('OnboardingMilestones (SSR)', () => {
	it('self-hides when progress is unknown (null)', () => {
		expect(html(null)).not.toContain('onboarding journey');
	});

	it('self-hides when onboarding is complete (done ≥ total)', () => {
		expect(html({ done: 5, total: 5, stages: STAGES })).not.toContain('onboarding journey');
		expect(html({ done: 7, total: 5, stages: STAGES })).not.toContain('onboarding journey');
	});

	it('renders every stage label and a check for the done one', () => {
		const body = html({ done: 1, total: 5, stages: STAGES });
		for (const s of STAGES!) expect(body).toContain(s.label);
		// The 'done' stage gets a check icon (an <svg>, not its ordinal number "1").
		expect(body).toContain('<svg');
	});

	it('falls back to the flat bar when stages is absent (older cached response)', () => {
		const body = html({ done: 2, total: 5 });
		expect(body).toContain('2 of 5 steps complete');
		expect(body).toContain('width:40%');
	});

	it('falls back to the flat bar when stages is an empty array', () => {
		const body = html({ done: 2, total: 5, stages: [] });
		expect(body).toContain('2 of 5 steps complete');
	});

	it('calls the client to action only on steps they can actually do', () => {
		// `available` — not `total - done`, most of which is typically the adviser's own work.
		const summoned = html({ done: 1, total: 5, stages: STAGES, available: 2 });
		expect(summoned).toContain('2 steps need you');
		const quiet = html({ done: 1, total: 5, stages: STAGES, available: 0 });
		expect(quiet).not.toContain('need you');
		// An older response carries no count at all: describe progress, summon nobody.
		expect(html({ done: 1, total: 5, stages: STAGES })).not.toContain('need you');
	});

	it('says "1 step needs you" for a single outstanding step', () => {
		expect(html({ done: 4, total: 5, stages: STAGES, available: 1 })).toContain('1 step needs you');
	});

	it('links to /onboarding when interactive, renders inert otherwise', () => {
		expect(html({ done: 2, total: 5, stages: STAGES })).toContain('href="/onboarding"');
		const inert = html({ done: 2, total: 5, stages: STAGES }, false);
		expect(inert).not.toContain('<a');
		expect(inert).toContain('Getting your details');
	});
});
