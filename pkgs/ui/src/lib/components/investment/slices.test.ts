import { describe, expect, it } from 'vitest';
import { clientDemo } from '../../fixtures/demo.js';
import {
	awaitingValuation,
	clampLimit,
	clampText,
	deriveWrapperGroups,
	resolveSlices,
	tablePagination,
	type Slices
} from './slices.js';
import type { PortalPageData, Wrapper } from './types.js';

/**
 * The leak-regression suite. The old client-home renderer decided per slice
 * (`data?.x ?? clientDemo.x`), so a real signed-in client whose payload was missing a slice saw
 * fixture documents/news/adviser presented as their own. resolveSlices is the fix: demo is
 * all-or-nothing on `data === null`, and these tests pin that a NON-NULL payload can never
 * surface a demo value, slice by slice.
 */

// Every slice key and the demo value it must NEVER equal for a real client.
const DEMO_VALUES: Record<Exclude<keyof Slices, 'demo'>, unknown> = {
	totals: clientDemo.totals,
	performance: clientDemo.performance,
	wrappers: clientDemo.wrappers,
	allocations: clientDemo.allocations.all,
	holdings: clientDemo.holdings,
	activity: clientDemo.activity,
	documents: clientDemo.documents,
	adviser: clientDemo.adviser,
	news: clientDemo.news,
	tasks: clientDemo.tasks,
	onboarding: clientDemo.onboarding,
	requestsOutstanding: clientDemo.requestsOutstanding
};
const SLICE_KEYS = Object.keys(DEMO_VALUES) as (keyof typeof DEMO_VALUES)[];

describe('resolveSlices(null) — the studio preview', () => {
	const got = resolveSlices(null, clientDemo);

	it('flags demo mode', () => {
		expect(got.demo).toBe(true);
	});

	for (const key of SLICE_KEYS)
		it(`'${key}' is the demo fixture, wholesale`, () => {
			expect(got[key]).toEqual(DEMO_VALUES[key]);
		});
});

describe('resolveSlices(non-null) — a real signed-in client', () => {
	// The worst case: a real client whose payload carries NOTHING. Every slice must resolve to
	// an empty value, and none may fall back to demo.
	const got = resolveSlices({}, clientDemo);

	it('flags real mode', () => {
		expect(got.demo).toBe(false);
	});

	for (const key of SLICE_KEYS)
		it(`'${key}' missing from the payload is NOT the demo value (the leak fix)`, () => {
			expect(got[key]).not.toEqual(DEMO_VALUES[key]);
		});

	it('missing slices resolve to empty values, not junk', () => {
		expect(got.totals).toEqual({ value: 0, gain: 0, gainPct: 0 });
		expect(got.performance).toEqual([]);
		expect(got.wrappers).toEqual([]);
		expect(got.allocations).toEqual([]);
		expect(got.holdings).toEqual([]);
		expect(got.activity).toEqual([]);
		expect(got.documents).toEqual([]);
		expect(got.adviser).toBeNull();
		expect(got.news).toEqual([]);
		expect(got.tasks).toEqual([]);
		expect(got.onboarding).toBeNull();
		// null, not 0: "no honest count" and "nothing outstanding" are different claims, and the
		// documents block renders no requests banner for either.
		expect(got.requestsOutstanding).toBeNull();
	});

	it('keeps the slices a partial payload provides (by identity)', () => {
		const data: PortalPageData = {
			holdings: [{ name: 'Real Fund', wrapper: 'ISA', units: 1, price: 1, value: 1, gainPct: 0 }],
			adviser: { name: 'Real Adviser', firm: 'Real Firm', email: 'a@b.c', phone: '0' },
			tasks: [{ uid: 't1', title: 'Real task', description: null, due_date: null }],
			onboarding: { done: 1, total: 3 }
		};
		const partial = resolveSlices(data, clientDemo);
		expect(partial.holdings).toBe(data.holdings);
		expect(partial.adviser).toBe(data.adviser);
		expect(partial.tasks).toBe(data.tasks);
		expect(partial.onboarding).toBe(data.onboarding);
		// …while the slices it did NOT provide stay empty, never demo.
		expect(partial.documents).toEqual([]);
		expect(partial.wrappers).toEqual([]);
	});

	it('news from the payload passes through; absent news stays empty, never demo', () => {
		const live = [{ ...clientDemo.news[0], headline: 'Live headline' }];
		expect(resolveSlices({ news: live }, clientDemo).news).toBe(live);
		expect(resolveSlices({}, clientDemo).news).toEqual([]);
		expect(resolveSlices({ news: [] }, clientDemo).news).toEqual([]);
	});

	it('explicit null slices behave like absent ones', () => {
		const nulled = resolveSlices(
			{ tasks: null, onboarding: null, requests_outstanding: null },
			clientDemo
		);
		expect(nulled.tasks).toEqual([]);
		expect(nulled.onboarding).toBeNull();
		expect(nulled.requestsOutstanding).toBeNull();
	});

	it('a real requests_outstanding count passes through — zero included', () => {
		expect(resolveSlices({ requests_outstanding: 5 }, clientDemo).requestsOutstanding).toBe(5);
		// 0 is a real answer ("nothing outstanding"), distinct from null ("no honest count").
		expect(resolveSlices({ requests_outstanding: 0 }, clientDemo).requestsOutstanding).toBe(0);
	});
});

describe('clampLimit — the defensive client-side mirror of the contract settingLimits', () => {
	it('passes an in-range limit through (truncated to an integer)', () => {
		expect(clampLimit('holdings', 5)).toBe(5);
		expect(clampLimit('holdings', 5.9)).toBe(5);
	});

	it('clamps to the contract bounds', () => {
		// block-contract.json: holdings.limit [1, 25], actionItems.limit [1, 10].
		expect(clampLimit('holdings', 0)).toBe(1);
		expect(clampLimit('holdings', 999)).toBe(25);
		expect(clampLimit('actionItems', 999)).toBe(10);
		expect(clampLimit('accounts', -3)).toBe(1);
		expect(clampLimit('activity', 51)).toBe(50);
		expect(clampLimit('documents', 13)).toBe(12);
	});

	it('returns undefined for unset/junk values (the caller applies its own default)', () => {
		expect(clampLimit('holdings', undefined)).toBeUndefined();
		expect(clampLimit('holdings', 'ten')).toBeUndefined();
		expect(clampLimit('holdings', NaN)).toBeUndefined();
	});

	it('returns undefined for a type with no limit setting', () => {
		expect(clampLimit('adviser', 3)).toBeUndefined();
		expect(clampLimit('message', 3)).toBeUndefined();
		expect(clampLimit('nonsense', 3)).toBeUndefined();
	});
});

describe('clampText — text settings cut to the contract max lengths', () => {
	it('passes short text through', () => {
		expect(clampText('message', 'title', 'Welcome')).toBe('Welcome');
	});

	it('cuts at the contract max (message.title 80, message.body 1000)', () => {
		expect(clampText('message', 'title', 'x'.repeat(200))).toHaveLength(80);
		expect(clampText('message', 'body', 'x'.repeat(2000))).toHaveLength(1000);
	});

	it("coerces non-strings to '' — settings ride the wire as untyped JSON", () => {
		expect(clampText('message', 'title', 42)).toBe('');
		expect(clampText('message', 'body', null)).toBe('');
		expect(clampText('message', 'body', undefined)).toBe('');
	});
});

describe('deriveWrapperGroups — the client-side summary fallback', () => {
	const wrapper = (type: string, value: number): Wrapper => ({
		type,
		name: `${type} account`,
		owner: 'Test Owner',
		value,
		gain: 0,
		gainPct: 0,
		trend: []
	});

	it('groups by product type with counts and value sums', () => {
		const groups = deriveWrapperGroups([
			wrapper('ISA', 100),
			wrapper('SIPP', 500),
			wrapper('ISA', 50)
		]);
		expect(groups).toEqual([
			{ product_type: 'SIPP', count: 1, value: 500 },
			{ product_type: 'ISA', count: 2, value: 150 }
		]);
	});

	it('conserves the input: counts and values sum to the slice totals', () => {
		const wrappers = [
			wrapper('ISA', 100.25),
			wrapper('GIA', 200.5),
			wrapper('ISA', 300.75),
			wrapper('SIPP', 400),
			wrapper('GIA', 12.34)
		];
		const groups = deriveWrapperGroups(wrappers);
		expect(groups.reduce((s, g) => s + g.count, 0)).toBe(wrappers.length);
		expect(groups.reduce((s, g) => s + g.value, 0)).toBeCloseTo(
			wrappers.reduce((s, w) => s + w.value, 0)
		);
	});

	it('sorts buckets by value, largest first', () => {
		const groups = deriveWrapperGroups([
			wrapper('JISA', 10),
			wrapper('SIPP', 9000),
			wrapper('ISA', 400)
		]);
		expect(groups.map((g) => g.product_type)).toEqual(['SIPP', 'ISA', 'JISA']);
	});

	it("files a blank/missing product type under 'Account'", () => {
		const groups = deriveWrapperGroups([
			wrapper('', 100),
			wrapper('  ', 50),
			{ ...wrapper('ISA', 10), type: undefined as unknown as string }
		]);
		expect(groups).toEqual([{ product_type: 'Account', count: 3, value: 160 }]);
	});

	it('returns [] for an empty slice', () => {
		expect(deriveWrapperGroups([])).toEqual([]);
	});

	it("the 'owner' axis buckets by holder, blank owners under 'Other accounts'", () => {
		const groups = deriveWrapperGroups(
			[
				{ ...wrapper('ISA', 100), owner: 'Daniel' },
				{ ...wrapper('SIPP', 900), owner: 'Daniel' },
				{ ...wrapper('JISA', 50), owner: 'Freya' },
				{ ...wrapper('GIA', 10), owner: '' }
			],
			'owner'
		);
		expect(groups).toEqual([
			{ product_type: 'Daniel', count: 2, value: 1000 },
			{ product_type: 'Freya', count: 1, value: 50 },
			{ product_type: 'Other accounts', count: 1, value: 10 }
		]);
	});
});

describe('awaitingValuation — the summary exceptions heuristic', () => {
	const wrapper = (value: number): Wrapper => ({
		type: 'GIA',
		name: 'GIA account',
		owner: 'Test Owner',
		value,
		gain: 0,
		gainPct: 0,
		trend: []
	});

	it('counts zero-valued wrappers when the slice is the whole book', () => {
		expect(awaitingValuation([wrapper(100), wrapper(0), wrapper(0)], undefined, null)).toBe(2);
		expect(awaitingValuation([wrapper(100), wrapper(0), wrapper(0)], 3, false)).toBe(2);
	});

	it('SKIPS the claim (null) for a summarised or paginated slice — never miscounts', () => {
		// The server summarised the book: the slice is one page, counting its zeros would state a
		// book-wide figure from partial data.
		expect(awaitingValuation([wrapper(0)], 300, true)).toBeNull();
		// wrappers_total outruns the slice even without the summarised flag.
		expect(awaitingValuation([wrapper(0), wrapper(100)], 50, null)).toBeNull();
	});

	it('a fully-valued book reports zero (the caption then renders nothing)', () => {
		expect(awaitingValuation([wrapper(100), wrapper(1)], undefined, undefined)).toBe(0);
	});
});

describe("tablePagination — the 'should this table paginate' decision", () => {
	// [total, sliceLength, pageSize, page] → { show, pages, current }.
	const cases: [
		[number | null | undefined, number, number, number],
		{ show: boolean; pages: number; current: number }
	][] = [
		// No total ⇒ the slice IS the data (preview persona / unpaginated payload): never paginate.
		[[null, 25, 25, 1], { show: false, pages: 1, current: 1 }],
		[[undefined, 40, 25, 1], { show: false, pages: 1, current: 1 }],
		// The total fits one page: no pagination UI.
		[[10, 10, 25, 1], { show: false, pages: 1, current: 1 }],
		// Total present but not beyond the slice — the server sent everything.
		[[10, 10, 5, 1], { show: false, pages: 2, current: 1 }],
		// A genuinely paginated slice.
		[[100, 25, 25, 1], { show: true, pages: 4, current: 1 }],
		[[100, 25, 25, 3], { show: true, pages: 4, current: 3 }],
		// The last page's partial slice still paginates.
		[[101, 1, 25, 5], { show: true, pages: 5, current: 5 }],
		// Clamps: a page beyond the range lands on the last; junk lands on the first.
		[[100, 25, 25, 99], { show: true, pages: 4, current: 4 }],
		[[100, 25, 25, 0], { show: true, pages: 4, current: 1 }],
		[[100, 25, 25, -3], { show: true, pages: 4, current: 1 }],
		[[100, 25, 25, NaN], { show: true, pages: 4, current: 1 }],
		// Defensive: a junk page size cannot divide by zero its way into the UI.
		[[100, 25, 0, 1], { show: false, pages: 1, current: 1 }]
	];
	for (const [[total, sliceLength, pageSize, page], expected] of cases)
		it(`(${total}, ${sliceLength}, ${pageSize}, ${page}) → ${JSON.stringify(expected)}`, () => {
			expect(tablePagination(total, sliceLength, pageSize, page)).toEqual(expected);
		});
});
