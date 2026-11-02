/**
 * Slice resolution — the demo-gating decision for composed portal pages, in one pure, tested
 * place. The old client-home renderer decided per slice (`data?.x ?? clientDemo.x`), which leaked
 * fixture documents/news/adviser to REAL signed-in clients whenever a slice was missing from the
 * payload. The contract here is deliberately all-or-nothing:
 *
 *  - `data === null`  ⇒ the studio preview / no live payload: every slice is the demo fixture,
 *    wholesale — the preview renders the real components with representative data.
 *  - `data !== null`  ⇒ a real signed-in client: each slice is the provided value or an EMPTY
 *    value, NEVER demo. A missing slice renders the block's empty state.
 *
 * `news` now has a live source (`GET /crm/content`); a missing key still resolves to [] rather
 * than the studio fixtures.
 *
 * slices.test.ts is the leak-regression suite: it asserts, per slice, that non-null data can
 * never surface the demo value.
 */
import contract from '../branding/block-contract.json';
import { groupWrappers } from './grouping.js';
import type { clientDemo } from '../../fixtures/demo.js';
import type {
	Activity,
	Adviser,
	ClientDocument,
	ClientHomeData,
	Holding,
	NewsItem,
	PortalActionItem,
	PortalOnboarding,
	PortalPageData,
	Wrapper,
	WrapperGroup
} from './types.js';

/** Every data slice a portal page's blocks can consume, demo-gated as one decision. */
export interface Slices {
	/** True when rendering demo fixtures (data === null) — the studio preview. */
	demo: boolean;
	totals: ClientHomeData['totals'];
	performance: ClientHomeData['performance'];
	wrappers: Wrapper[];
	allocations: ClientHomeData['allocations'];
	holdings: Holding[];
	activity: Activity[];
	documents: ClientDocument[];
	adviser: Adviser | null;
	news: NewsItem[];
	tasks: PortalActionItem[];
	onboarding: PortalOnboarding | null;
	/** Outstanding document requests for the documents block's `show_requests` banner. null ⇒
	 * no honest count exists ⇒ no banner (0 renders none either — nothing is outstanding). */
	requestsOutstanding: number | null;
}

const EMPTY_TOTALS: ClientHomeData['totals'] = { value: 0, gain: 0, gainPct: 0 };

/**
 * Resolve a page's data slices. `data === null` ⇒ demo fixtures wholesale (studio preview);
 * `data !== null` ⇒ each slice is the provided value or empty — never demo (the leak fix).
 */
export function resolveSlices(data: PortalPageData | null, demo: typeof clientDemo): Slices {
	if (data === null) {
		return {
			demo: true,
			totals: demo.totals,
			performance: demo.performance,
			wrappers: demo.wrappers,
			allocations: demo.allocations.all,
			holdings: demo.holdings,
			activity: demo.activity,
			documents: demo.documents,
			adviser: demo.adviser,
			news: demo.news,
			tasks: demo.tasks,
			onboarding: demo.onboarding,
			requestsOutstanding: demo.requestsOutstanding
		};
	}
	return {
		demo: false,
		totals: data.totals ?? EMPTY_TOTALS,
		performance: data.performance ?? [],
		wrappers: data.wrappers ?? [],
		allocations: data.allocations ?? [],
		holdings: data.holdings ?? [],
		activity: data.activity ?? [],
		documents: data.documents ?? [],
		adviser: data.adviser ?? null,
		news: data.news ?? [],
		tasks: data.tasks ?? [],
		onboarding: data.onboarding ?? null,
		// Absent ⇒ null, and the banner stays away: a count is never fabricated for a real
		// client (the demo's 2 exists only for the studio preview above).
		requestsOutstanding: data.requests_outstanding ?? null
	};
}

// The contract's per-setting bounds ("{type}.{key}" → [min, max] for numbers, max for text).
const LIMITS: Record<string, number | number[] | undefined> = contract.settingLimits;

/**
 * A block's effective `limit` setting, defensively clamped to the contract's [min, max] for the
 * type (mirrors app/branding.py's validation — a stale or hand-edited value degrades to sane).
 * Returns undefined when the type has no limit setting OR the value is unset/junk, so the caller
 * applies its own default slice.
 */
export function clampLimit(type: string, raw: unknown): number | undefined {
	const bounds = LIMITS[`${type}.limit`];
	if (!Array.isArray(bounds)) return undefined;
	if (typeof raw !== 'number' || !Number.isFinite(raw)) return undefined;
	const min = bounds[0] ?? 1;
	const max = bounds[1] ?? min;
	return Math.min(max, Math.max(min, Math.trunc(raw)));
}

/**
 * A block's text setting (message title/body), cut to the contract's max length. Non-strings
 * become '' — settings ride the wire as untyped JSON, so never trust the shape.
 */
export function clampText(type: string, key: string, raw: unknown): string {
	const s = typeof raw === 'string' ? raw : '';
	const max = LIMITS[`${type}.${key}`];
	return typeof max === 'number' ? s.slice(0, max) : s;
}

/**
 * Group a wrappers slice into the summary variant's buckets — the client-side fallback for when
 * `wrapper_groups` didn't ride the payload (the studio preview renders a persona's raw wrappers;
 * the server groups only in summary mode). The axis mirrors the block's `group_by` setting:
 * 'wrapper' (the historic default) buckets by product type, 'owner' by account holder. Buckets
 * sort by value, largest first; blank labels fall back per groupLabel (grouping.ts) — 'Account'
 * for a missing product type, 'Other accounts' for a missing owner — rather than an empty tile.
 * (`product_type` is the wire field name; on the owner axis it carries the owner label.)
 */
export function deriveWrapperGroups(
	wrappers: Wrapper[],
	axis: 'wrapper' | 'owner' = 'wrapper'
): WrapperGroup[] {
	return groupWrappers(wrappers, axis)
		.map((s) => ({ product_type: s.label, count: s.wrappers.length, value: s.subtotal }))
		.sort((a, b) => b.value - a.value);
}

/**
 * How many accounts in the slice are awaiting a valuation, for the summary's exceptions caption —
 * or null when no honest claim can be made.
 *
 * The heuristic, stated plainly: a wrapper with `value === 0` is read as "not yet valued". The
 * wire shape carries no explicit unvalued flag, and the fixtures/loaders all encode "no live
 * valuation yet" as a zero value with an empty trend — a genuinely-empty account is
 * indistinguishable and will be counted; that is the best available reading, and it errs towards
 * flagging an account as awaiting rather than asserting a £0 valuation happened.
 *
 * When the server summarised the book (`wrappers_summarised`) or `wrappers_total` outruns the
 * slice, the slice is one page — counting zeros in it would state a book-wide figure from partial
 * data, so the claim is SKIPPED (null) rather than miscounted.
 */
export function awaitingValuation(
	wrappers: Wrapper[],
	wrappersTotal: number | null | undefined,
	summarised: boolean | null | undefined
): number | null {
	if (summarised) return null;
	if (wrappersTotal != null && wrappersTotal > wrappers.length) return null;
	return wrappers.filter((w) => w.value === 0).length;
}

/** Whether (and how) a table block paginates — see tablePagination. */
export interface TablePaginationState {
	/** Render the pagination control at all. */
	show: boolean;
	/** Total page count (≥ 1). */
	pages: number;
	/** The current page, clamped into [1, pages] — a stale ?page beyond range lands on the last. */
	current: number;
}

/**
 * The "should this table paginate" decision, pure so it's table-testable: a table paginates only
 * when the server told us there are more rows than the slice it sent (`total` present and greater
 * than the slice length) AND the total doesn't fit one page. No total ⇒ the slice IS the data
 * (preview personas, unpaginated payloads) ⇒ no pagination UI.
 */
export function tablePagination(
	total: number | null | undefined,
	sliceLength: number,
	pageSize: number,
	page: number
): TablePaginationState {
	if (total == null || !Number.isFinite(total) || pageSize < 1) {
		return { show: false, pages: 1, current: 1 };
	}
	const pages = Math.max(1, Math.ceil(total / pageSize));
	const wanted = Number.isFinite(page) ? Math.trunc(page) : 1;
	const current = Math.min(Math.max(1, wanted), pages);
	return { show: pages > 1 && total > sliceLength, pages, current };
}
