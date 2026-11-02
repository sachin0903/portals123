import { describe, expect, it } from 'vitest';
import { accountStatusChip, accountStatusTone } from './account-status.js';
import {
	ALL,
	accountItems,
	categoryCounts,
	filterListing,
	matchesText,
	unreadNote
} from './listing.js';
import type { ClientDocument, Holding } from './types.js';

/**
 * The listing toolbar's pure rules — the legacy listing pages' search/filter semantics, kept
 * table-testable outside the components (widget-toolbar / filtered-listing render these).
 */

describe('matchesText — the legacy pages’ case-insensitive substring rule', () => {
	it('matches any of the given texts, case-insensitively', () => {
		expect(matchesText(['Vanguard FTSE Global All Cap', 'VAFTGAG'], 'ftse')).toBe(true);
		expect(matchesText(['Vanguard FTSE Global All Cap', 'VAFTGAG'], 'vaft')).toBe(true);
		expect(matchesText(['Vanguard FTSE Global All Cap', 'VAFTGAG'], 'fundsmith')).toBe(false);
	});

	it('a blank or whitespace query matches everything', () => {
		expect(matchesText(['anything'], '')).toBe(true);
		expect(matchesText(['anything'], '   ')).toBe(true);
		expect(matchesText([], '')).toBe(true);
	});

	it('null/undefined texts never match a real query, and never throw', () => {
		expect(matchesText([null, undefined], 'x')).toBe(false);
		expect(matchesText([null, 'axe'], 'x')).toBe(true);
	});

	it('the query is trimmed before matching (legacy parity)', () => {
		expect(matchesText(['Fundsmith Equity'], '  equity ')).toBe(true);
	});
});

describe('accountItems — the account filter options', () => {
	it("leads with 'All accounts' then distinct labels in appearance order", () => {
		expect(accountItems(['ISA', 'SIPP', 'ISA', 'GIA'])).toEqual([
			{ value: ALL, label: 'All accounts' },
			{ value: 'ISA', label: 'ISA' },
			{ value: 'SIPP', label: 'SIPP' },
			{ value: 'GIA', label: 'GIA' }
		]);
	});

	it('drops blank/absent labels rather than offering an unselectable ghost', () => {
		expect(accountItems(['', '  ', null, undefined, 'ISA'])).toHaveLength(2);
	});

	// The dead end this parameter exists to prevent: under a SERVER-side account filter the rows
	// in hand are one account's, so options derived from them name only that account, the control
	// hides itself for having under two, and the filter can never be lifted. The caller passes
	// the client's WHOLE list instead — so every other account stays one click away.
	it('offers every account it is given, not merely the ones in the filtered rows', () => {
		expect(accountItems(['ISA', 'SIPP', 'GIA'], 'SIPP').map((o) => o.value)).toEqual([
			ALL,
			'ISA',
			'SIPP',
			'GIA'
		]);
	});

	// A renamed or closed account, or a pasted link naming one that no longer resolves. The
	// filter IS in force, so the value has to be visible and switchable off — an option nobody
	// can select is how a filtered listing comes to read as an empty book.
	it('appends the active filter when the label list does not carry it', () => {
		expect(accountItems(['ISA', 'SIPP'], 'Closed GIA')).toEqual([
			{ value: ALL, label: 'All accounts' },
			{ value: 'ISA', label: 'ISA' },
			{ value: 'SIPP', label: 'SIPP' },
			{ value: 'Closed GIA', label: 'Closed GIA' }
		]);
	});

	it('never appends a duplicate, and never appends the no-filter sentinel', () => {
		expect(accountItems(['ISA', 'SIPP'], 'ISA')).toHaveLength(3);
		expect(accountItems(['ISA', 'SIPP'], ALL)).toHaveLength(3);
		expect(accountItems(['ISA'], '  ')).toHaveLength(2);
	});
});

describe('categoryCounts — the documents pills', () => {
	it('enumerates the categories actually in the data, with counts, in appearance order', () => {
		expect(categoryCounts(['Statements', 'Tax', 'Statements', 'Agreements'])).toEqual([
			{ value: 'Statements', count: 2 },
			{ value: 'Tax', count: 1 },
			{ value: 'Agreements', count: 1 }
		]);
	});

	it('returns [] for no documents (the toolbar hides the pill row)', () => {
		expect(categoryCounts([])).toEqual([]);
	});
});

describe('unreadNote — honest or absent', () => {
	const doc = (id: string, unread?: boolean): ClientDocument => ({
		id,
		name: `Doc ${id}`,
		category: 'Statements',
		date: '1 Jun 2026',
		size: '1 KB',
		format: 'PDF',
		...(unread === undefined ? {} : { unread })
	});

	it('counts unread documents when the payload carries read-state', () => {
		expect(unreadNote([doc('a', true), doc('b', false), doc('c', true)])).toBe('2 unread');
	});

	it('renders NOTHING when no document carries the field — a loader that does not know must not imply "all read"', () => {
		expect(unreadNote([doc('a'), doc('b')])).toBeNull();
	});

	it('renders nothing for zero unread (legacy showed the badge only for a positive count)', () => {
		expect(unreadNote([doc('a', false), doc('b', false)])).toBeNull();
	});

	it('a mixed payload (some rows flagged, some not) still counts only the true flags', () => {
		expect(unreadNote([doc('a', true), doc('b')])).toBe('1 unread');
	});
});

describe('filterListing — the axes AND together', () => {
	const holding = (name: string, wrapper: string, ticker?: string): Holding => ({
		name,
		ticker,
		wrapper,
		units: 1,
		price: 1,
		value: 1,
		gainPct: 0
	});
	const rows = [
		holding('Vanguard FTSE Global All Cap', 'ISA', 'VAFTGAG'),
		holding('Fundsmith Equity', 'GIA', 'FEET'),
		holding('iShares Core MSCI World', 'SIPP', 'SWDA')
	];
	const accessors = {
		searchText: (h: Holding) => [h.name, h.ticker],
		accountOf: (h: Holding) => h.wrapper
	};

	it('filters by text over the declared fields (name OR ticker — legacy /holdings parity)', () => {
		expect(filterListing(rows, { q: 'feet', account: ALL, category: ALL }, accessors)).toEqual([
			rows[1]
		]);
	});

	it('filters by account', () => {
		expect(filterListing(rows, { q: '', account: 'SIPP', category: ALL }, accessors)).toEqual([
			rows[2]
		]);
	});

	it('ANDs the axes', () => {
		expect(filterListing(rows, { q: 'core', account: 'ISA', category: ALL }, accessors)).toEqual(
			[]
		);
	});

	it('a missing accessor skips its axis instead of dropping every row', () => {
		expect(
			filterListing(rows, { q: '', account: 'ISA', category: 'Statements' }, accessors)
		).toEqual([rows[0]]);
	});
});

describe('accountStatusTone / accountStatusChip — the legacy /accounts mapping, shared', () => {
	it('active reads as success, anything else neutral', () => {
		expect(accountStatusTone('active')).toBe('success');
		expect(accountStatusTone('Active ')).toBe('success');
		expect(accountStatusTone('pending')).toBe('neutral');
		expect(accountStatusTone('closed')).toBe('neutral');
	});

	it('chips only NON-active statuses (an all-active book stays chip-less)', () => {
		expect(accountStatusChip('active')).toBeNull();
		expect(accountStatusChip('Active')).toBeNull();
		// The LABEL is named here, sentence case, rather than left raw for each widget to CSS its
		// way out of: `capitalize` at the call site Title-Cases a two-word status and cannot touch
		// the underscore in one. The ink look still lowercases in CSS — that is a typographic
		// register, and it composes over a real label.
		expect(accountStatusChip('pending')).toEqual({ label: 'Pending', tone: 'neutral' });
		expect(accountStatusChip('pre_close')).toEqual({ label: 'Pre close', tone: 'neutral' });
	});

	it('absent/blank status renders nothing — a state is surfaced, never assumed', () => {
		expect(accountStatusChip(null)).toBeNull();
		expect(accountStatusChip(undefined)).toBeNull();
		expect(accountStatusChip('  ')).toBeNull();
	});
});
