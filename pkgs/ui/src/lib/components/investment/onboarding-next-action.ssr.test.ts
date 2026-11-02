import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import OnboardingNextAction from './onboarding-next-action.svelte';
import type { OnboardingNextStep } from './types.js';

/** The hero has three mutually exclusive states and the interesting one is WAITING: a client whose
 * work is all held behind their adviser's fact-find must be told the application is progressing,
 * because the state it replaced (an empty task list) reads as broken exactly when the journey is
 * healthy. These pin each state's copy and the absence of a control where there is no action. */

const TERMS: OnboardingNextStep = {
	step_type: 'accept_terms',
	title: 'Terms of business',
	index: 1,
	total: 5
};

describe('OnboardingNextAction (SSR)', () => {
	it('names the next step and offers the action that starts it', () => {
		const body = render(OnboardingNextAction, {
			props: { next: TERMS, done: 2, total: 7 }
		}).body;
		expect(body).toContain('Terms of business');
		// The CTA says what the step is, never a bare "Continue".
		expect(body).toContain('Review and agree');
		expect(body).toContain('2 of 7 done');
	});

	it('says how many of a run of documents are left', () => {
		const body = render(OnboardingNextAction, {
			props: { next: TERMS, done: 2, total: 7 }
		}).body;
		expect(body).toContain('Next of 5');
		expect(body).toContain('4 still to do after this');
	});

	it('reports a held application as progressing, naming the milestone', () => {
		const body = render(OnboardingNextAction, {
			props: { next: null, done: 1, total: 7, stageLabel: 'Getting your details' }
		}).body;
		expect(body).toContain('Your adviser is working');
		expect(body).toContain('Getting your details');
		// Nothing to press: a waiting client must not be offered an action that cannot work.
		expect(body).not.toContain('<button');
	});

	it('never tells a client their application is with compliance', () => {
		// `waitingWith: 'us'` is the firm-neutral wording for a staff-side hold. A client is never
		// told an identity or AML review is under way (docs/aml.md — the tipping-off control), so
		// the copy names no team and no check.
		const body = render(OnboardingNextAction, {
			props: { next: null, done: 3, total: 7, waitingWith: 'us' as const }
		}).body;
		expect(body).toContain('We’re working on your application');
		expect(body.toLowerCase()).not.toContain('compliance');
		expect(body.toLowerCase()).not.toContain('screening');
	});

	it('thanks a finished client and offers nothing further', () => {
		const body = render(OnboardingNextAction, {
			props: { next: null, done: 7, total: 7, journeyComplete: true }
		}).body;
		expect(body).toContain('You’re all set');
		expect(body).not.toContain('<button');
	});

	it('does not announce "all set" in the gap between releases', () => {
		// The trap this exists for: the client's step list GROWS as the journey advances (identity
		// is released once the terms are signed), so every released step being done is not the same
		// as being finished. Claiming otherwise then produces another task straight afterwards.
		const body = render(OnboardingNextAction, {
			props: {
				next: null,
				done: 5,
				total: 5,
				journeyComplete: false,
				stageLabel: 'Verification'
			}
		}).body;
		expect(body).not.toContain('all set');
		expect(body).toContain('Verification');
	});

	it('falls back to the counts when no journey verdict is given', () => {
		const body = render(OnboardingNextAction, {
			props: { next: null, done: 7, total: 7, journeyComplete: null }
		}).body;
		expect(body).toContain('You’re all set');
	});

	it('names what the application waits on when the caller knows, with no button', () => {
		const body = render(OnboardingNextAction, {
			props: {
				next: null,
				done: 0,
				total: 1,
				journeyComplete: false,
				stageLabel: 'Verification',
				waitingTitle: 'We’re checking your identity',
				waitingDetail: 'Thank you for sending your photos.'
			}
		}).body;
		expect(body).toContain('We’re checking your identity');
		expect(body).toContain('Thank you for sending your photos.');
		expect(body).not.toContain('working on your application');
		expect(body).not.toContain('<button');
	});

	it('renders no progress bar before there is anything to measure', () => {
		const body = render(OnboardingNextAction, { props: { next: null, done: 0, total: 0 } }).body;
		expect(body).not.toContain('role="progressbar"');
	});
});
