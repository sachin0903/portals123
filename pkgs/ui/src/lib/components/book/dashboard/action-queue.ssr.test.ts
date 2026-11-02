/**
 * The rail's rows, as rendered. `action-queue.ts` decides WHAT a row says; this pins that the two
 * kinds of work do not look alike — a journey wears a route, a review a stamp — and that "needs
 * you" is an unread-style dot on the right, not a chip: the row already says what it is, the dot
 * says only that the next move is yours. The chip it replaces took up a third of the row.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ActionQueue from './action-queue.svelte';
import type { RailItem } from './action-queue';

const item = (over: Partial<RailItem> = {}): RailItem => ({
	key: 'journey:exe_1',
	kind: 'journey',
	title: 'Mr Max Oakden',
	detail: 'Onboarding · Applicant details · 3 of 8',
	ball: 'staff',
	href: '/journeys/exe_1',
	rank: 0,
	started: '2026-07-01T09:00:00Z',
	...over
});

const body = (items: RailItem[]) =>
	render(ActionQueue, { props: { items, emptyDescription: 'Nothing assigned to you.' } }).body;

const DOT = /class="bg-warning size-2 shrink-0 rounded-full"/;

describe('the needs-you rail', () => {
	it('marks a journey with an open staff step with the dot, not a chip', () => {
		const html = body([item()]);
		expect(html).toContain('Mr Max Oakden');
		expect(html).toContain('href="/journeys/exe_1"');
		expect(html).toMatch(DOT);
		expect(html).toContain('<span class="sr-only">Needs you</span>');
		expect(html).toContain('text-warning');
	});

	it('shows no dot while the move is the client’s', () => {
		const html = body([item({ ball: 'client' })]);
		expect(html).not.toMatch(DOT);
		expect(html).not.toContain('Needs you');
		expect(html).toContain('text-info');
	});

	it('shows no dot while the move is Graphene’s', () => {
		expect(body([item({ ball: 'graphene' })])).not.toMatch(DOT);
	});

	it('gives a review its own icon and the dot — a review is always the reviewer’s', () => {
		const html = body([
			item({
				key: 'review:rev_1',
				kind: 'review',
				title: 'RV-1042 · Bank details',
				detail: 'Mr Max Oakden · GIA-00421',
				ball: null,
				href: '/reviews?open=rev_1'
			})
		]);
		expect(html).toContain('RV-1042 · Bank details');
		expect(html).toContain('text-brand');
		expect(html).toMatch(DOT);
	});

	it('says all clear, in the caller’s words, when there is nothing', () => {
		expect(body([])).toContain('Nothing assigned to you.');
	});
});
