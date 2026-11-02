import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// The pure due-date phrasing lives in the component's module script (moved from apps/client's
// home page), so it imports without mounting anything — vitest's svelte plugin compiles the file.
import { dueText, overdue, sortActionItems } from './action-items-card.svelte';
import type { PortalActionItem } from './types.js';

describe('dueText', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		// Midday, so the maths cannot straddle midnight; the helper zeroes today itself.
		vi.setSystemTime(new Date('2026-07-30T12:00:00'));
	});
	afterEach(() => {
		vi.useRealTimers();
	});

	it('empty for no due date', () => {
		expect(dueText(null)).toBe('');
	});

	it("phrases today as 'Due today'", () => {
		expect(dueText('2026-07-30')).toBe('Due today');
	});

	it("phrases tomorrow as 'Due tomorrow'", () => {
		expect(dueText('2026-07-31')).toBe('Due tomorrow');
	});

	it("phrases later dates as 'Due in N days'", () => {
		expect(dueText('2026-08-04')).toBe('Due in 5 days');
	});

	it("phrases any past date as 'Overdue'", () => {
		expect(dueText('2026-07-29')).toBe('Overdue');
		expect(dueText('2026-01-01')).toBe('Overdue');
	});

	it('overdue() is true only for past dates', () => {
		expect(overdue('2026-07-29')).toBe(true);
		expect(overdue('2026-07-30')).toBe(false);
		expect(overdue('2026-08-04')).toBe(false);
		expect(overdue(null)).toBe(false);
	});

	it("says nothing for an unparseable date — junk must not shout 'Overdue'", () => {
		// NaN used to fall through every day-count comparison to the 'Overdue' branch.
		expect(dueText('not-a-date')).toBe('');
		expect(dueText('31/01/2027')).toBe('');
		expect(overdue('not-a-date')).toBe(false);
	});
});

describe('sortActionItems', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-07-30T12:00:00'));
	});
	afterEach(() => {
		vi.useRealTimers();
	});

	const item = (uid: string, due_date: string | null): PortalActionItem => ({
		uid,
		title: uid,
		description: null,
		due_date
	});

	it('orders overdue first, then soonest due, undated last', () => {
		const sorted = sortActionItems([
			item('later', '2026-08-20'),
			item('undated', null),
			item('overdue', '2026-07-01'),
			item('soon', '2026-08-01')
		]);
		expect(sorted.map((t) => t.uid)).toEqual(['overdue', 'soon', 'later', 'undated']);
	});

	it('surfaces an overdue item above a limit cut (the heading counts what the cut hides)', () => {
		// The server's order put the overdue item LAST; a limit of 2 used to hide it entirely.
		const sorted = sortActionItems([
			item('a', '2026-08-01'),
			item('b', '2026-08-02'),
			item('overdue', '2026-07-01')
		]);
		expect(sorted.slice(0, 2).map((t) => t.uid)).toEqual(['overdue', 'a']);
	});

	it('is stable for equal keys and treats unparseable dates like undated', () => {
		const sorted = sortActionItems([
			item('junk', 'not-a-date'),
			item('undated1', null),
			item('dated', '2026-08-01'),
			item('undated2', null)
		]);
		// The dated item leads; the Infinity-keyed trio keeps its incoming order (stable sort,
		// and an explicit 0 for equal keys — Infinity − Infinity is NaN).
		expect(sorted.map((t) => t.uid)).toEqual(['dated', 'junk', 'undated1', 'undated2']);
	});

	it('does not mutate its input', () => {
		const input = [item('b', '2026-08-02'), item('a', '2026-08-01')];
		sortActionItems(input);
		expect(input.map((t) => t.uid)).toEqual(['b', 'a']);
	});
});
