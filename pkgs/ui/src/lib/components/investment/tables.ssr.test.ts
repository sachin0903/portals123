import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AccountsTable from './accounts-table.svelte';
import ActivityTable from './activity-table.svelte';
import HoldingsFullTable from './holdings-full-table.svelte';
import type { Activity, Holding, Wrapper } from './types.js';

/**
 * SSR pins for the DataTable variants' structural fixes: container-width column collapse
 * (`hidden @…:table-cell` — a column sheds by the table's own SPAN, not the viewport),
 * the leftover-width name cell that makes `truncate` actually fire, the accessible names,
 * and the paginated-slice sort refusal. Whether the container variants FLIP at the right
 * widths needs a real layout engine — that part is browser QA; these tests pin that the
 * machinery is emitted at all, so a class-name regression fails fast.
 */

const holdings: Holding[] = [
	{
		name: 'Vanguard FTSE Developed World ex-UK Equity Index Fund',
		ticker: 'VDWXEIA',
		wrapper: 'ISA',
		units: 100,
		price: 2.5,
		value: 250,
		gainPct: 0.05
	},
	{ name: 'Cash', wrapper: 'GIA', units: 1, price: 1, value: 1, gainPct: 0 }
];

const wrappers: Wrapper[] = [
	{
		type: 'ISA',
		name: 'Stocks & Shares ISA',
		owner: 'Avery',
		value: 100,
		gain: 1,
		gainPct: 0.01,
		trend: []
	}
];

const activity: Activity[] = [
	{ date: '14 Jun 2026', type: 'deposit', description: 'Monthly contribution', amount: 500 }
];

describe('the DataTable variants (SSR)', () => {
	it('HoldingsFullTable: container-collapsed columns, truncating name cell, aria-label', () => {
		const { body } = render(HoldingsFullTable, { props: { holdings } });
		expect(body).toContain('aria-label="Holdings"');
		// The table wrapper is the size container the @… variants key off.
		expect(body).toContain('@container');
		// Low-priority columns collapse by container width, not viewport.
		expect(body).toContain('@4xl:table-cell'); // ticker
		expect(body).toContain('@2xl:table-cell'); // units
		// The name cell takes the leftover width so truncate can fire on auto layout.
		expect(body).toContain('max-w-0');
		expect(body).toContain('Vanguard FTSE Developed World ex-UK Equity Index Fund');
	});

	it('AccountsTable: labelled, container-collapsed', () => {
		const { body } = render(AccountsTable, { props: { wrappers } });
		expect(body).toContain('aria-label="Accounts"');
		expect(body).toContain('@md:table-cell'); // type
		expect(body).toContain('@lg:table-cell'); // return
	});

	// The /holdings page printed "+0.0%" on every row whose cost the custodian never sent — more
	// than half a real book — while the account record said "—" for the very same rows. A claimed
	// flat year is not the same statement as "we do not know".
	it('HoldingsFullTable: no cost basis renders "—", never a green +0.0%', () => {
		const { body } = render(HoldingsFullTable, {
			props: {
				holdings: [
					{
						name: 'Epworth UK Equity',
						wrapper: 'GIA',
						units: 1,
						price: 1,
						value: 996596.92,
						gainPct: null
					},
					{
						name: 'FP Foresight',
						wrapper: 'GIA',
						units: 1,
						price: 1,
						value: 153245.29,
						gainPct: 0.094
					}
				] satisfies Holding[]
			}
		});
		expect(body).toContain('—');
		expect(body).not.toContain('+0.0%');
		expect(body).toContain('+9.4%'); // a real return still reads as one
	});

	it('ActivityTable: labelled, container-collapsed', () => {
		const { body } = render(ActivityTable, { props: { activity } });
		expect(body).toContain('aria-label="Recent activity"');
		expect(body).toContain('@lg:table-cell'); // type
		expect(body).toContain('@2xl:table-cell'); // account
	});

	// A debit was left UNTINTED while a credit was green, so a column of movements read
	// "green, green, plain, green" — and the untinted row is taken for another credit, which is
	// the one misreading a ledger must never allow.
	it('ActivityTable: a debit is red, a credit green, zero neither', () => {
		const rows: Activity[] = [
			{ date: '14 Jun 2026', type: 'deposit', description: 'In', amount: 500 },
			{ date: '13 Jun 2026', type: 'fee', description: 'Out', amount: -874.48 },
			{ date: '12 Jun 2026', type: 'buy', description: 'Nil', amount: 0 }
		];
		const { body } = render(ActivityTable, { props: { activity: rows } });
		expect(body).toContain('text-positive');
		expect(body).toContain('text-negative');
		// The sign is rendered too, so colour never carries the meaning on its own.
		expect(body).toContain('+£500');
		expect(body).toContain('-£874');
		// Zero takes neither tint: one green and one red across the three rows.
		expect(body.split('text-positive').length - 1).toBe(1);
		expect(body.split('text-negative').length - 1).toBe(1);
	});

	// The tint has to reach the PENCE. A colour passed in `class` reaches the pounds and leaves
	// `text-muted-foreground` on the minor part, so a red total ended in grey pence — which only
	// `Money`'s own tone can fix, because the minor span is inside it.
	it('ActivityTable: the pence take the tint too, never the muted grey', () => {
		const { body } = render(ActivityTable, {
			props: {
				activity: [{ date: '13 Jun 2026', type: 'fee', description: 'Out', amount: -874.48 }]
			}
		});
		expect(body).toContain('opacity-70'); // the tinted step-back
		expect(body).not.toContain('text-muted-foreground">.48');
	});

	it("AccountsTable: a zero-change return renders neutral '0.0%', never a green '+0.0%'", () => {
		const flat: Wrapper[] = [{ ...wrappers[0]!, gain: 0, gainPct: 0 }];
		const { body } = render(AccountsTable, { props: { wrappers: flat } });
		expect(body).toContain('0.0%');
		expect(body).not.toContain('+0.0%');
		expect(body).not.toContain('text-positive');
	});

	it("AccountsTable: an implausible return renders '—' (sanePct), matching stack/cards", () => {
		const junk: Wrapper[] = [{ ...wrappers[0]!, gainPct: 12.87 }];
		const { body } = render(AccountsTable, { props: { wrappers: junk } });
		expect(body).toContain('—');
		expect(body).not.toContain('1,287');
	});

	it('sortable={false} swaps header buttons for explained, non-interactive text', () => {
		const sortable = render(HoldingsFullTable, { props: { holdings } }).body;
		const frozen = render(HoldingsFullTable, { props: { holdings, sortable: false } }).body;
		expect(sortable).toContain('<button');
		expect(sortable).not.toContain('aria-disabled');
		expect(frozen).not.toContain('<button');
		expect(frozen).toContain('aria-disabled="true"');
		expect(frozen).toContain('Sorting the full list isn’t available yet.');
	});
});
