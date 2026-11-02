import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ActivityList, { groupByDate } from './activity-list.svelte';
import type { Activity } from './types.js';

const entry = (date: string, description: string, amount = 100): Activity => ({
	date,
	type: 'deposit',
	description,
	amount
});

describe('groupByDate', () => {
	it('groups consecutive same-date entries under one heading', () => {
		const groups = groupByDate([
			entry('14 Jun 2026', 'Contribution'),
			entry('14 Jun 2026', 'Buy', -100),
			entry('11 Jun 2026', 'Dividend')
		]);
		expect(groups.map((g) => [g.date, g.items.length])).toEqual([
			['14 Jun 2026', 2],
			['11 Jun 2026', 1]
		]);
	});

	it('unsorted input (A, B, A) repeats the date group rather than reordering the ledger', () => {
		// Grouping is consecutive-only on purpose — but this shape used to produce two groups
		// with the SAME `group.date` key, and Svelte throws on duplicate each-keys (blank block).
		// The template now keys groups by index; this pins the repeated-group contract it relies on.
		const groups = groupByDate([
			entry('14 Jun 2026', 'first'),
			entry('11 Jun 2026', 'middle'),
			entry('14 Jun 2026', 'last')
		]);
		expect(groups.map((g) => g.date)).toEqual(['14 Jun 2026', '11 Jun 2026', '14 Jun 2026']);
		expect(groups.map((g) => g.items.map((i) => i.description))).toEqual([
			['first'],
			['middle'],
			['last']
		]);
	});

	it('returns [] for no activity', () => {
		expect(groupByDate([])).toEqual([]);
	});
});

describe('ActivityList (SSR)', () => {
	it('renders every group of an unsorted feed, duplicate dates included', () => {
		const body = render(ActivityList, {
			props: {
				activity: [
					entry('14 Jun 2026', 'first'),
					entry('11 Jun 2026', 'middle'),
					entry('14 Jun 2026', 'last')
				]
			}
		}).body;
		for (const description of ['first', 'middle', 'last']) expect(body).toContain(description);
		// The repeated date renders twice — two separate headings, not a merge or a crash.
		expect(body.split('14 Jun 2026').length - 1).toBe(2);
	});

	// A cash movement has no instrument, so the API titles the row by its kind. The kind must not
	// then be repeated underneath: "Payment · Payment".
	it('does not repeat the kind under a row titled by its kind', () => {
		const body = render(ActivityList, {
			props: { activity: [entry('14 Jun 2026', 'Payment'), entry('14 Jun 2026', 'Global Fund')] }
		}).body;
		expect(body.split('Payment').length - 1).toBe(2); // the title, and the fund's kind
	});

	// The wide-container columns: the custodian's words, the ISIN and a trade's units. They are
	// in the markup under container-query classes (`@2xl:`/`@3xl:`), so a narrow block hides
	// them with CSS rather than the component deciding by viewport.
	it('draws the detail, ISIN and units of a trade for a wide feed', () => {
		const body = render(ActivityList, {
			props: {
				activity: [
					{
						...entry('14 Jun 2026', 'Global Equity Fund', -1234.5),
						type: 'buy',
						detail: 'At Best',
						isin: 'GB00B3X7QG63',
						quantity: 416,
						price: 2.9675,
						wrapper: 'Stocks & Shares ISA'
					}
				]
			}
		}).body;
		expect(body).toContain('At Best');
		expect(body).toContain('GB00B3X7QG63');
		expect(body).toContain('416 units @ £2.9675');
		expect(body).toContain('@2xl:');
		expect(body).toContain('@3xl:');
	});

	// A feed of cash movements has no units and no detail, and a column of blanks is the
	// emptiness the wide layout exists to fill. Neither column is drawn until some row needs it.
	it('draws no units or detail column when no row has either', () => {
		const body = render(ActivityList, {
			props: { activity: [entry('14 Jun 2026', 'Payment'), entry('14 Jun 2026', 'Fee', -10)] }
		}).body;
		expect(body).not.toContain('@3xl:');
		expect(body).not.toContain('@2xl:w-[26%]');
	});

	it('gives every canonical kind its own icon, so interest is not drawn as a trade', () => {
		const kinds = [
			'interest',
			'transfer_in',
			'transfer_out',
			'tax',
			'corporate_action',
			'adjustment'
		];
		const body = render(ActivityList, {
			props: { activity: kinds.map((type) => ({ ...entry('14 Jun 2026', 'x'), type })) }
		}).body;
		// Six rows, six icons — none of them the generic trending-up fallback.
		expect(body.split('lucide-trending-up').length - 1).toBe(0);
		expect(body.split('lucide-percent').length - 1).toBe(1);
	});
});
