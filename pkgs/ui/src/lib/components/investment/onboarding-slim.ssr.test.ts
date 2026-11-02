import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import OnboardingSlim from './onboarding-slim.svelte';
import type { PortalOnboarding } from './types.js';

/**
 * SSR pins for the slim onboarding look: onboarding-progress's self-hide rule, the SSR-final
 * progress width, and the interactive/inert branch.
 */

const html = (onboarding: PortalOnboarding | null, interactive?: boolean): string =>
	render(OnboardingSlim, {
		props: interactive === undefined ? { onboarding } : { onboarding, interactive }
	}).body;

describe('OnboardingSlim (SSR)', () => {
	it('self-hides when progress is unknown (null)', () => {
		expect(html(null)).not.toContain('steps complete');
	});

	it('self-hides when onboarding is complete (done ≥ total)', () => {
		expect(html({ done: 5, total: 5 })).not.toContain('steps complete');
		expect(html({ done: 7, total: 5 })).not.toContain('steps complete');
	});

	it('renders the one-line summary with the SSR-final bar width', () => {
		const body = html({ done: 2, total: 5 });
		expect(body).toContain('2 of 5 steps complete');
		// The fill width is server-rendered — no zero-width bar filling in on hydration.
		expect(body).toContain('width:40%');
	});

	it('links the whole line to /onboarding when interactive', () => {
		expect(html({ done: 2, total: 5 })).toContain('href="/onboarding"');
	});

	it('interactive={false} renders the same line inert — no anchor', () => {
		const body = html({ done: 2, total: 5 }, false);
		expect(body).toContain('2 of 5 steps complete');
		expect(body).not.toContain('<a');
	});

	it('stays a slim line — no CTA panel chrome', () => {
		const body = html({ done: 2, total: 5 });
		expect(body).not.toContain('border-brand/30');
		expect(body).not.toContain('data-slot="card"');
	});
});
