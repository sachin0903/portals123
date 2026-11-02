import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { personaByKey } from '../../fixtures/personas.js';
import { groupByDate } from './activity-list.svelte';
import ActivityTimeline from './activity-timeline.svelte';
import type { Activity } from './types.js';

/**
 * SSR pins for the timeline activity look: the signature spine geometry (one node per date
 * group, sharing groupByDate with activity-list so the grouping semantics can never drift
 * apart) and the credit/debit ink rule.
 */

const count = (body: string, needle: string): number => body.split(needle).length - 1;

const html = (activity: Activity[]): string =>
	render(ActivityTimeline, { props: { activity } }).body;

describe('ActivityTimeline (SSR)', () => {
	it('renders one spine node per date group (family6 persona)', () => {
		const activity = personaByKey('family6').data.activity!;
		const body = html(activity);
		expect(count(body, 'data-timeline-node')).toBe(groupByDate(activity).length);
	});

	it('unsorted input repeats the date group rather than merging it (activity-list parity)', () => {
		// The A, B, A shape groupByDate deliberately degrades on — the timeline must show
		// three nodes, not silently reorder the ledger into two.
		const body = html([
			{ date: '3 Jun 2026', type: 'deposit', description: 'First', amount: 100 },
			{ date: '2 Jun 2026', type: 'fee', description: 'Second', amount: -5 },
			{ date: '3 Jun 2026', type: 'deposit', description: 'Third', amount: 50 }
		]);
		expect(count(body, 'data-timeline-node')).toBe(3);
	});

	it('credits wear text-positive, debits text-negative, zero neither', () => {
		const body = html([
			{ date: '3 Jun 2026', type: 'dividend', description: 'Dividend received', amount: 291.6 },
			{ date: '3 Jun 2026', type: 'fee', description: 'Platform fee', amount: -38.7 },
			{ date: '3 Jun 2026', type: 'fee', description: 'Waived fee', amount: 0 }
		]);
		expect(count(body, 'text-positive')).toBe(1);
		expect(count(body, 'text-negative')).toBe(1);
		// Both figures render as signed Money.
		expect(body).toContain('£291');
		expect(body).toContain('£38');
	});

	it('shows the account chip when the entry carries a wrapper', () => {
		const body = html([
			{
				date: '3 Jun 2026',
				type: 'deposit',
				description: 'Contribution',
				amount: 100,
				wrapper: 'ISA'
			}
		]);
		expect(body).toContain('ISA');
	});

	it('renders the date as a small-caps eyebrow beside the node', () => {
		const body = html([
			{ date: '3 Jun 2026', type: 'deposit', description: 'Contribution', amount: 100 }
		]);
		expect(body).toContain('uppercase');
		expect(body).toContain('3 Jun 2026');
	});

	it('renders nothing at all for an empty feed — no orphaned spine', () => {
		const body = html([]);
		expect(body).not.toContain('data-timeline-node');
		expect(body).not.toContain('bg-border');
	});
});
