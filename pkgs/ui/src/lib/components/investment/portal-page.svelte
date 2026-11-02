<script lang="ts">
	import type { BrandingBlock } from '@graphene/auth';
	import type { Component } from 'svelte';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import ChartPieIcon from '@lucide/svelte/icons/chart-pie';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import LayersIcon from '@lucide/svelte/icons/layers-3';
	import NewspaperIcon from '@lucide/svelte/icons/newspaper';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import { blockKeys, blockW, blockH } from '../branding/client-blocks.js';
	import {
		cellSpan,
		isSelfMeasurement,
		DEFAULT_GAP_PX,
		MASONRY_ROW_PX,
		type PortalGridMetrics
	} from './masonry.js';
	import { clientDemo } from '../../fixtures/demo.js';
	import * as Card from '../ui/card/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import Pagination from '../ui/pagination/pagination.svelte';
	import AllocationBars from './allocation-bars.svelte';
	import AccountCards from './account-cards.svelte';
	import AccountStack from './account-stack.svelte';
	import AccountsSummary from './accounts-summary.svelte';
	import AccountsTable from './accounts-table.svelte';
	import ActionItemsCard from './action-items-card.svelte';
	import ActivityList from './activity-list.svelte';
	import ActivityTable from './activity-table.svelte';
	import AdviserCard from './adviser-card.svelte';
	import DocumentsList from './documents-list.svelte';
	import HoldingsFullTable from './holdings-full-table.svelte';
	import HoldingsTable from './holdings-table.svelte';
	import MessageCard from './message-card.svelte';
	import NewsFeed from './news-feed.svelte';
	import OnboardingProgress from './onboarding-progress.svelte';
	import PortfolioHeader from './portfolio-header.svelte';
	import PortfolioHeaderCanvas from './portfolio-header-canvas.svelte';
	import PortfolioHeaderCompact from './portfolio-header-compact.svelte';
	import PortfolioHeaderLedger from './portfolio-header-ledger.svelte';
	import AccountsFloat from './accounts-float.svelte';
	import AccountsInk from './accounts-ink.svelte';
	import ActionItemsBanner from './action-items-banner.svelte';
	import ActivityTimeline from './activity-timeline.svelte';
	import AdviserPortrait from './adviser-portrait.svelte';
	import AdviserStrip from './adviser-strip.svelte';
	import AllocationOrbit from './allocation-orbit.svelte';
	import AllocationSpectrum from './allocation-spectrum.svelte';
	import DocumentsIndex from './documents-index.svelte';
	import DocumentsShelf from './documents-shelf.svelte';
	import HoldingsSheet from './holdings-sheet.svelte';
	import HoldingsValueBars from './holdings-value-bars.svelte';
	import AllocationDonut from '../charts/allocation-donut.svelte';
	import WidgetHeader from './widget-header.svelte';
	import MessageBanner from './message-banner.svelte';
	import MessageNote from './message-note.svelte';
	import OnboardingMilestones from './onboarding-milestones.svelte';
	import OnboardingSlim from './onboarding-slim.svelte';
	import type { BlockType } from '../branding/client-blocks.js';
	import type { ListingLinks, ListingSource } from '../branding/listing-links.js';
	import FilteredListing from './filtered-listing.svelte';
	import HoldingsGrouped from './holdings-grouped.svelte';
	import NewsLibrary from './news-library.svelte';
	import DocumentsTimeline from './documents-timeline.svelte';
	import { documentIcon } from './documents-icons.js';
	import { unreadNote, type ListingQuery } from './listing.js';
	import { resolveLook, type Finish } from './looks.js';
	import { normaliseGroupBy } from './grouping.js';
	import { normaliseActions } from './header-actions.svelte';
	import { normalisePeriod } from './periods.js';
	import { accountsOf, asAt } from './provenance.js';
	import {
		awaitingValuation,
		clampLimit,
		clampText,
		deriveWrapperGroups,
		resolveSlices,
		tablePagination
	} from './slices.js';
	import type { PortalPageData } from './types.js';

	// The generalised portal-page renderer: any composed page (home included) as a responsive
	// 12-column grid of the tenant's arranged blocks. This is the ONE renderer used both by the
	// real client app and by the branding studio's live preview, so the preview is a true
	// representation. Demo gating is resolveSlices' single decision (slices.ts): `data === null`
	// (the preview) renders demo fixtures wholesale; a real client's missing slices render each
	// block's empty state — never fixtures.
	let {
		blocks,
		data = null,
		interactive = true,
		completing = null,
		oncompletetask,
		pageSlug = 'home',
		pagination = null,
		pageHref,
		finish = null,
		listingQuery = null,
		listingAccounts = null,
		disabledActions = [],
		links,
		onlistingchange = null,
		onmeasure = null
	}: {
		/** Audience-resolved blocks (resolveAudienceLayout); this filters `visible !== false`. */
		blocks: BrandingBlock[];
		/** null ⇒ studio preview ⇒ demo wholesale (via resolveSlices). */
		data?: PortalPageData | null;
		interactive?: boolean;
		/** The action-item uid currently being marked done (pending button state). */
		completing?: string | null;
		oncompletetask?: (uid: string) => void;
		pageSlug?: string;
		/** The composed page's server-side pagination window. When a table variant's slice came
		 * paginated (its `*_total` outruns the slice), the table gets a Pagination of plain
		 * `?page={n}` links — SSR-consistent, the page's loader re-fetches on navigation. */
		pagination?: { page: number; pageSize: number } | null;
		/** Builds a page link's href. Provide one that PRESERVES the current query string (the
		 * client app builds it from page.url) — the bare `?page={n}` default drops every other
		 * search param on navigation. */
		pageHref?: (page: number) => string;
		/** The tenant's portal finish (Branding.finish) — what a block's look:'auto' follows.
		 * Absent/'card' keeps today's carded rendering byte-for-byte (see looks.ts). */
		finish?: Finish;
		/** Externally-owned listing filter state for the toolbar'd listing blocks (search text +
		 * account). Page-level like `pagination`: intended for the dedicated listing pages (one
		 * listing block), where the APP owns the query via the URL and re-fetches server-side
		 * (next wave) — several toolbar'd blocks on one page would share it. */
		listingQuery?: ListingQuery | null;
		/** The client's WHOLE account list, for the toolbars' account filter options.
		 *
		 * Belongs with `listingQuery` and matters for the same reason: under a controlled
		 * (server-answered) filter the rows on the page are ONE account's, so a toolbar left to
		 * derive its own options from them offers only that account and then hides itself for
		 * having nothing to offer — a filter in force that nothing can lift. Absent ⇒ each
		 * toolbar derives from its rows, which is correct while it owns the filtering itself. */
		listingAccounts?: (string | null | undefined)[] | null;
		/** Money actions this firm has switched off. The portfolio header omits those buttons. */
		disabledActions?: readonly string[];
		/** Where each listing card's "View all" may point (`listingLinks`, from the firm's own page
		 *  set); a null entry draws no link, because the page does not exist for this firm.
		 *  Absent only on the legacy client-home path, which has no page set to consult and keeps
		 *  the built-in slugs. */
		links?: Partial<ListingLinks> | null;
		/** Providing this switches the toolbars to CONTROLLED mode: edits are reported here (the
		 * app round-trips via the URL) and no client-side filtering happens. Absent ⇒ the
		 * toolbars filter the slice client-side (the studio preview / complete slices) — unless
		 * the slice is partial, where the search disables rather than answer wrongly. */
		onlistingchange?: ((next: ListingQuery) => void) | null;
		/** Reports the grid this renderer just laid out — the micro-row span of every cell, plus
		 * what those spans are expressed in. The branding studio feeds it back into the layout
		 * builder so the builder's tiles land on the rows the portal actually puts them on.
		 * Absent ⇒ nothing is measured for reporting and no observer is attached. */
		onmeasure?: ((metrics: PortalGridMetrics) => void) | null;
	} = $props();

	const s = $derived(resolveSlices(data, clientDemo));

	// ── Fluid (masonry) rows ──────────────────────────────────────────────────────────────────
	// One CSS grid gives every cell in a row the same track height, so each "row" cuts straight
	// across the page and blocks get stretched to their tallest neighbour. Instead, once mounted,
	// every cell spans MICRO-ROWS (8px tracks) equal to its content's own measured height — the
	// classic grid-masonry technique — so heights are natural and a neighbour tucks up under a
	// shorter card. `--rs` is per-cell; the visual row rhythm (1.5rem below each card) is baked
	// into the span because the masonry grid zeroes row-gap (a spanned item would otherwise
	// swallow one gap per micro-row). SSR/no-JS keeps the previous shared-track layout.
	// The authored `h` is a FLOOR here, not a hard track count (masonry.ts cellSpan): a block set
	// two units high is never shorter than that, and content taller than it still wins. Before
	// this, `--ch` was live only on the non-masonry fallback path, so the studio's H stepper was a
	// control that changed nothing on a hydrated portal.
	let masonry = $state(false);
	$effect(() => {
		masonry = typeof ResizeObserver !== 'undefined';
	});
	let gridEl = $state<HTMLElement>();
	/** Micro-row span per block key — the report's payload, kept in step by the cell action. */
	const measured = new Map<string, number>();

	function gridGap(grid: Element | null | undefined): number {
		// The row rhythm comes from the grid's column-gap (gap-6 → rem-based, so it follows the
		// density scale) — read live rather than hardcoded 24px.
		return grid ? parseFloat(getComputedStyle(grid).columnGap) || DEFAULT_GAP_PX : DEFAULT_GAP_PX;
	}

	let reportQueued = false;
	function queueReport() {
		if (!onmeasure || reportQueued) return;
		reportQueued = true;
		// One frame's coalescing: a page settles several cells in the same layout pass, and the
		// consumer wants the finished grid, not each cell's turn at it.
		requestAnimationFrame(() => {
			reportQueued = false;
			const grid = gridEl;
			if (!grid || !onmeasure) return;
			const style = getComputedStyle(grid);
			onmeasure({
				spans: Object.fromEntries(measured),
				gridWidth: grid.clientWidth,
				rowPx: MASONRY_ROW_PX,
				gapPx: gridGap(grid),
				// Track count, not a media query: this subtree can be re-parented into another
				// document (the studio's preview iframe), where the module's own `window` is the
				// wrong one to ask. Below md the grid is `grid-cols-1` and spans mean nothing.
				spanned: style.gridTemplateColumns.trim().split(/\s+/).length > 1
			});
		});
	}

	function masonryCell(cell: HTMLElement, param: { key: string; h: number }) {
		if (typeof ResizeObserver === 'undefined') return;
		const inner = cell.firstElementChild;
		if (!(inner instanceof HTMLElement)) return;
		let current = param;
		const apply = () => {
			// A measurement that exactly fills the cell we sized from it is our own last answer coming
			// back, not the content (masonry.ts::isSelfMeasurement) — growing on it adds one gap per
			// frame for ever. The CSS below closes the routes by which the cell's height can reach
			// this box; this catches an engine where it does anyway.
			if (isSelfMeasurement(inner.offsetHeight, cell.clientHeight, measured.has(current.key))) {
				return;
			}
			const span = cellSpan(
				inner.offsetHeight,
				current.h,
				gridGap(cell.parentElement),
				MASONRY_ROW_PX
			);
			// Written every pass, not only on a change: the property is the cell's real height and
			// anything that rewrites the style attribute must not be able to leave it stale.
			cell.style.setProperty('--rs', String(span));
			if (measured.get(current.key) === span) return;
			measured.set(current.key, span);
			queueReport();
		};
		const ro = new ResizeObserver(apply);
		ro.observe(inner);
		apply();
		return {
			update(next: { key: string; h: number }) {
				if (next.key !== current.key) measured.delete(current.key);
				current = next;
				apply();
			},
			destroy() {
				ro.disconnect();
				measured.delete(current.key);
				queueReport();
			}
		};
	}

	// The grid can change width without any cell's content changing (the studio's preview device
	// switches, the host column resizes) — and a miniature drawn from these spans needs to know.
	$effect(() => {
		const grid = gridEl;
		if (!grid || !onmeasure || typeof ResizeObserver === 'undefined') return;
		const ro = new ResizeObserver(() => queueReport());
		ro.observe(grid);
		return () => ro.disconnect();
	});

	// A block's settings.variant, treated as ALREADY-CONCRETE — the loader resolves 'auto'
	// through resolveVariant (variants.ts) before it gets here, so this only reads the name and
	// dispatches to the variant's component below. An unrecognised name falls through to the
	// type's canonical look rather than a blank block.
	function variantOf(b: BrandingBlock): string {
		const v = b.settings?.['variant'];
		return typeof v === 'string' ? v : 'auto';
	}

	// The block's concrete LOOK (looks.ts): an explicit contract-valid `look` setting wins;
	// 'auto'/junk follows the portal finish. Orthogonal to the variant — the variant is the data
	// shape, the look is the chrome. b.type is untrusted wire vocabulary, so the cast is safe
	// only because resolveLook answers 'card' for any type the contract doesn't know.
	function lookOf(b: BrandingBlock): string {
		return resolveLook(b.type as BlockType, b.settings, finish);
	}

	// The rows a data block shows: an explicit settings.limit (clamped to the contract's bounds)
	// wins; otherwise the compact home look keeps its historic caps, while a full-listing variant
	// ('table' holdings/activity, 'grouped' documents — what the default listing pages pin) shows
	// everything. Without the variant carve-out the default /holdings page would cap at five rows.
	// A block with its TOOLBAR on is a listing too, whatever its variant: the default Activity
	// page is the home card's list with search and an account filter over it, and a filter over
	// six rows is not a filter.
	function sliceCap(b: BrandingBlock, compactDefault?: number): number | undefined {
		const explicit = clampLimit(b.type, b.settings?.['limit']);
		if (explicit !== undefined) return explicit;
		const v = variantOf(b);
		const full =
			b.settings?.['toolbar'] === true ||
			(b.type === 'holdings' && (v === 'table' || v === 'grouped')) ||
			(b.type === 'activity' && v === 'table') ||
			(b.type === 'documents' && v === 'grouped') ||
			(b.type === 'news' && v === 'library');
		return full ? undefined : compactDefault;
	}

	function capped<T>(rows: T[], cap: number | undefined): T[] {
		return cap === undefined ? rows : rows.slice(0, cap);
	}

	// Blocks that would render nothing must not claim a grid cell either — an empty .client-cell
	// still spans columns and a min-height, leaving a phantom gap where the block hid itself.
	// Mirrors the three self-hiding components' own conditions (they stay authoritative when
	// consumed standalone).
	function isBlank(b: BrandingBlock): boolean {
		switch (b.type) {
			case 'actionItems':
				return s.tasks.length === 0 && b.settings?.['hide_when_empty'] !== false;
			case 'onboardingProgress':
				return s.onboarding === null || s.onboarding.done >= s.onboarding.total;
			case 'message':
				return (
					clampText('message', 'title', b.settings?.['title']).trim() === '' &&
					clampText('message', 'body', b.settings?.['body']).trim() === ''
				);
			default:
				return false;
		}
	}

	// Keys come from the FULL block list, before the visibility/blank filter, so a cell's identity
	// is the one the studio's builder derives from the same layout (blockKeys) — filtering first
	// would renumber two id-less blocks of the same type and misattribute their heights.
	const cells = $derived.by(() => {
		const keys = blockKeys(blocks);
		return blocks
			.map((b, i) => ({ b, key: keys[i] ?? `${b.type}-${i}` }))
			.filter(({ b }) => b.visible !== false && !isBlank(b));
	});

	// The book's valuation stamp, rendered on the value-bearing surfaces (headers, the accounts
	// and holdings cards). Read off `data` rather than the slices: the studio preview
	// (data === null) has no honest date to show, and the stamp must never be fabricated.
	const stamp = $derived(asAt(data?.as_of));

	/** The "View all" destination for a listing card, or null for no link. With a page set to
	 *  consult the answer is the set's; without one (legacy path) the built-in slug, except on
	 *  the page that IS that listing. */
	function viewAllHref(source: ListingSource): string | null {
		if (links !== undefined) return links?.[source] ?? null;
		return pageSlug === source ? null : `/${source}`;
	}
</script>

{#snippet emptyNote(
	title: string,
	description: string,
	Icon: Component<{ class?: string }>,
	chromeless = false
)}
	<EmptyState
		icon={Icon}
		{title}
		{description}
		compact
		{chromeless}
		class="portal-widget-empty flex min-h-28 flex-1 flex-col justify-center"
	/>
{/snippet}

<!-- The legacy /documents outstanding-requests alert as a quiet block-level strip (the documents
     block's show_requests setting): requests raised outside a journey have no other route into
     the portal. Only ever rendered for a REAL fetched count (or the studio preview's fixture) —
     resolveSlices' requestsOutstanding is null when the loader fetched none, and null renders
     nothing (the leak rule). The info tone trio keeps it brandable and legible on both grounds. -->
{#snippet requestsBanner(count: number)}
	<div
		data-requests-banner
		class="border-info-line bg-info-subtle text-info-strong flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-sm"
	>
		<span>{count} document {count === 1 ? 'request needs' : 'requests need'} your attention</span>
		{#if interactive}
			<a href="/requests" class="shrink-0 font-medium underline underline-offset-4">
				See what’s needed
			</a>
		{:else}
			<span class="shrink-0 font-medium">See what’s needed</span>
		{/if}
	</div>
{/snippet}

<!-- Pagination under a table variant whose slice came paginated: plain ?page= links, so the
     composed page's server loader re-fetches on navigation (no client-side state). -->
{#snippet pager(total: number | null | undefined, sliceLength: number)}
	{#if pagination}
		{@const p = tablePagination(total, sliceLength, pagination.pageSize, pagination.page)}
		{#if p.show}
			<Pagination
				page={p.current}
				pageCount={p.pages}
				href={pageHref ?? ((n) => `?page=${n}`)}
				class="pt-4"
			/>
		{/if}
	{/if}
{/snippet}

{#snippet block(b: BrandingBlock)}
	{#if b.type === 'portfolioHeader'}
		{@const v = variantOf(b)}
		{@const headerActions = normaliseActions(b.settings?.['actions'])}
		{@const headerPeriod = normalisePeriod(b.settings?.['default_period'])}
		{@const showChart = b.settings?.['show_chart'] !== false}
		<!-- Canvas and ledger own their show_chart behaviour (the field/statement stands, only
		     the trace goes) — an explicit look must not degrade to compact just because the
		     chart is off. Hero keeps its historic rule: chart off ⇒ the compact strip. -->
		{#if v === 'canvas'}
			<!-- No gain/gainPct: the canvas deliberately carries only total + period change. -->
			<PortfolioHeaderCanvas
				total={s.totals.value}
				performance={s.performance}
				actions={headerActions}
				{disabledActions}
				defaultPeriod={headerPeriod}
				asOf={data?.as_of ?? null}
				period={data?.performance_period ?? null}
				{showChart}
				{interactive}
			/>
		{:else if v === 'ledger'}
			<PortfolioHeaderLedger
				total={s.totals.value}
				gain={s.totals.gain}
				gainPct={s.totals.gainPct}
				performance={s.performance}
				actions={headerActions}
				{disabledActions}
				defaultPeriod={headerPeriod}
				asOf={data?.as_of ?? null}
				period={data?.performance_period ?? null}
				{showChart}
				{interactive}
			/>
		{:else if v === 'compact' || !showChart}
			<PortfolioHeaderCompact
				total={s.totals.value}
				gain={s.totals.gain}
				gainPct={s.totals.gainPct}
			/>
		{:else}
			<PortfolioHeader
				total={s.totals.value}
				gain={s.totals.gain}
				gainPct={s.totals.gainPct}
				performance={s.performance}
				actions={headerActions}
				{disabledActions}
				defaultPeriod={headerPeriod}
				asOf={data?.as_of ?? null}
				period={data?.performance_period ?? null}
				{interactive}
			/>
		{/if}
	{:else if b.type === 'accounts'}
		{@const look = lookOf(b)}
		{@const v = variantOf(b)}
		{@const groupBy = normaliseGroupBy(b.settings?.['group_by'])}
		{@const showSparklines = b.settings?.['show_sparklines'] !== false}
		<!-- The chrome-less looks replace the whole block rendering (variant included — a loose
		     chip cluster and a typographic register ARE the presentation); they own their empty
		     states, so the slice goes in as-is. The 'card' path below is today's rendering,
		     untouched. -->
		{#if look === 'float'}
			<AccountsFloat wrappers={capped(s.wrappers, sliceCap(b))} {groupBy} {interactive} />
		{:else if look === 'ink'}
			<AccountsInk wrappers={capped(s.wrappers, sliceCap(b))} {groupBy} />
		{:else}
			<!-- Server-grouped buckets when they rode the payload; otherwise derived from the wrappers
		     slice (the studio preview's personas carry no wrapper_groups) — but ONLY when that
		     slice is the whole book. When the server summarised (wrappers_summarised) and the
		     groups are missing, deriving from the partial slice would show '300 accounts · £2.1m'
		     where £2.1m is one page — an empty state is honest, an invented total is not.
		     group_by 'owner' re-axes the DERIVED groups; server groups are product-type buckets
		     and only ride summarised payloads, where the whole-book owner split isn't available —
		     they win as the only honest figures. -->
			{@const groups =
				v === 'summary'
					? data?.wrapper_groups?.length
						? data.wrapper_groups
						: !data?.wrappers_summarised
							? deriveWrapperGroups(s.wrappers, groupBy === 'owner' ? 'owner' : 'wrapper')
							: []
					: []}
			{#if v === 'summary' && groups.length > 0}
				<AccountsSummary
					{groups}
					totalAccounts={data?.wrappers_total ?? s.wrappers.length}
					awaitingValuation={awaitingValuation(
						s.wrappers,
						data?.wrappers_total,
						data?.wrappers_summarised
					)}
					showViewAll={viewAllHref('accounts') !== null}
					viewAllHref={viewAllHref('accounts') ?? '/accounts'}
					{interactive}
				/>
			{:else if s.wrappers.length > 0 && v !== 'summary'}
				{#if v === 'cards'}
					<!-- The cards are surfaces of their own, so the block's one heading row sits bare
					     above the grid: the name, and the book as data (count · total) — the figures the
					     summary variant would carry, which this page otherwise never states. The count is
					     the WHOLE book's when the server summarised; the total is the portfolio's. -->
					<div class="space-y-3">
						<WidgetHeader
							bare
							title="Accounts"
							stamp={accountsOf(data?.wrappers_total ?? s.wrappers.length)}
							stampAmount={s.totals.value}
						/>
						<AccountCards wrappers={capped(s.wrappers, sliceCap(b))} {groupBy} />
					</div>
				{:else if v === 'table'}
					{@const accountRows = capped(s.wrappers, sliceCap(b))}
					<Card.Root glass class="h-full">
						<!-- The stamp rides the heading only when the payload carried one. -->
						<WidgetHeader title="Accounts" {stamp} href={viewAllHref('accounts')} {interactive} />
						<Card.Content>
							<!-- sortable only when the table holds the whole book: sorting one page of a
						     paginated slice client-side silently sorts that page and lies. -->
							<AccountsTable
								wrappers={accountRows}
								sortable={(data?.accounts_total ?? 0) <= accountRows.length}
							/>
							{@render pager(data?.accounts_total, s.wrappers.length)}
						</Card.Content>
					</Card.Root>
				{:else}
					<AccountStack wrappers={capped(s.wrappers, sliceCap(b))} {groupBy} {showSparklines} />
				{/if}
			{:else}
				<Card.Root glass class="h-full">
					<WidgetHeader title="Accounts" />
					<Card.Content>
						{@render emptyNote(
							'Your accounts will appear here',
							'Once your accounts are open, you’ll see their values and details in this space.',
							WalletIcon
						)}
					</Card.Content>
				</Card.Root>
			{/if}
		{/if}
	{:else if b.type === 'allocation'}
		{@const look = lookOf(b)}
		{#if look === 'orbit'}
			<AllocationOrbit data={s.allocations} total={s.totals.value} {interactive} />
		{:else if look === 'spectrum'}
			<AllocationSpectrum data={s.allocations} total={s.totals.value} />
		{:else}
			{@const bars = variantOf(b) === 'bars'}
			<Card.Root glass class="h-full">
				<WidgetHeader title="Allocation" />
				<Card.Content
					class={bars
						? 'flex flex-1 flex-col justify-center'
						: 'flex flex-1 flex-col items-center justify-center'}
				>
					{#if s.allocations.length > 0}
						{#if bars}
							<AllocationBars data={s.allocations} total={s.totals.value} />
						{:else}
							<AllocationDonut data={s.allocations} total={s.totals.value} {interactive} />
						{/if}
					{:else}
						{@render emptyNote(
							'Your allocation will appear here',
							'Once your money is invested, you’ll see how it is spread across asset classes.',
							ChartPieIcon
						)}
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	{:else if b.type === 'holdings'}
		{@const look = lookOf(b)}
		<!-- Look ⊥ variant: the variant keeps deciding DENSITY (sliceCap's 'table' carve-out shows
		     everything; anything else keeps the compact-5 default), the look only re-skins. So a
		     sheet on the home block statements the same five rows the carded top-holdings showed,
		     and the /holdings page's table-variant block statements the whole book — with its
		     pagination intact. The `toolbar` setting adds the legacy /holdings page's search +
		     account filter (filtered-listing owns the state; the toolbar snippet renders inside
		     whatever chrome the look wears). -->
		<FilteredListing
			rows={s.holdings}
			toolbar={b.settings?.['toolbar'] === true}
			label="Holdings"
			placeholder="Search by name or ticker"
			searchText={(h) => [h.name, h.ticker]}
			accountOf={(h) => h.wrapper}
			accountOptions={listingAccounts}
			partial={(data?.holdings_total ?? 0) > s.holdings.length}
			query={listingQuery}
			onquerychange={onlistingchange}
			{interactive}
		>
			{#snippet children(matched, toolbar, filtered)}
				{#if look === 'sheet'}
					<div class="space-y-3">
						{@render toolbar()}
						{#if matched.length === 0 && filtered}
							{@render emptyNote(
								'No holdings match',
								'Try changing your search or account filter.',
								SearchXIcon,
								true
							)}
						{:else if s.holdings.length === 0}
							{@render emptyNote(
								'Your holdings will appear here',
								'Once your accounts are invested, you’ll see your funds and positions in this space.',
								LayersIcon,
								true
							)}
						{:else}
							<HoldingsSheet
								holdings={capped(matched, sliceCap(b, 5))}
								asOf={data?.as_of ?? null}
							/>
							{#if variantOf(b) === 'table'}
								{@render pager(data?.holdings_total, s.holdings.length)}
							{/if}
						{/if}
					</div>
				{:else if look === 'bars'}
					<div class="space-y-3">
						{@render toolbar()}
						{#if matched.length === 0 && filtered}
							{@render emptyNote(
								'No holdings match',
								'Try changing your search or account filter.',
								SearchXIcon,
								true
							)}
						{:else if s.holdings.length === 0}
							{@render emptyNote(
								'Your holdings will appear here',
								'Once your accounts are invested, you’ll see your funds and positions in this space.',
								LayersIcon,
								true
							)}
						{:else}
							<HoldingsValueBars holdings={capped(matched, sliceCap(b, 5))} />
						{/if}
					</div>
				{:else}
					<Card.Root glass class="h-full">
						<WidgetHeader title="Holdings" {stamp} href={viewAllHref('holdings')} {interactive} />
						<Card.Content class="space-y-4">
							{@render toolbar()}
							{#if matched.length === 0 && filtered}
								{@render emptyNote(
									'No holdings match',
									'Try changing your search or account filter.',
									SearchXIcon
								)}
							{:else if s.holdings.length === 0}
								{@render emptyNote(
									'Your holdings will appear here',
									'Once your accounts are invested, you’ll see your funds and positions in this space.',
									LayersIcon
								)}
							{:else if variantOf(b) === 'grouped'}
								<!-- The whole book under its asset classes, in the donut's colours — the
								     client's reading of "what do I own" (holdings-grouped.svelte). -->
								<HoldingsGrouped
									holdings={capped(matched, sliceCap(b))}
									allocations={s.allocations}
									showWrapper={new Set(s.holdings.map((h) => h.wrapper)).size > 1}
								/>
							{:else if variantOf(b) === 'table'}
								{@const holdingRows = capped(matched, sliceCap(b))}
								<!-- sortable gates on the SLICE being the whole book (not the filtered
								     subset — filtering a complete slice keeps client sort honest). -->
								<div>
									<!-- The table variant takes NO slice cap (`sliceCap` exempts it), so a long book
									     rendered every position and the page ran off the bottom — the toolbar above and
									     the pager below scrolled away with it. It is bounded here instead and scrolls its
									     own rows, which is what every other full-page table in the product does.

									     A VIEWPORT bound, and that is the whole reason it is safe: the masonry cell is
									     sized FROM this card, so a height asked of the cell closes the feedback loop the
									     147,000px note below records. `100svh` is not a percentage of anything the grid
									     computes, so the cell measures a child that has stopped growing. The floor is
									     the block's own authored `h` (4 units × 6rem), so the bound can never fight the
									     height the firm set in the studio. -->
									<HoldingsFullTable
										holdings={holdingRows}
										sortable={(data?.holdings_total ?? 0) <= s.holdings.length}
										maxHeight="max-h-[max(24rem,calc(100svh-22rem))]"
									/>
									{@render pager(data?.holdings_total, s.holdings.length)}
								</div>
							{:else}
								<HoldingsTable holdings={capped(matched, sliceCap(b, 5))} showWrapper={false} />
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}
			{/snippet}
		</FilteredListing>
	{:else if b.type === 'activity'}
		{@const look = lookOf(b)}
		<!-- The activity slice is complete by construction (the loader's 200-row window matches
		     the legacy /activity page exactly; no `activity_total` exists), so the toolbar always
		     filters client-side — no `partial` arm. -->
		<FilteredListing
			rows={s.activity}
			toolbar={b.settings?.['toolbar'] === true}
			label="Activity"
			placeholder="Search by description or type"
			searchText={(a) => [a.description, a.type]}
			accountOf={(a) => a.wrapper}
			accountOptions={listingAccounts}
			query={listingQuery}
			onquerychange={onlistingchange}
			{interactive}
		>
			{#snippet children(matched, toolbar, filtered)}
				{#if look === 'timeline'}
					<div class="space-y-3">
						{@render toolbar()}
						{#if matched.length === 0 && filtered}
							{@render emptyNote(
								'No activity matches',
								'Try changing your search or account filter.',
								SearchXIcon,
								true
							)}
						{:else if s.activity.length === 0}
							{@render emptyNote(
								'Your activity will appear here',
								'Payments, dividends, trades and fees will show here as they happen.',
								ArrowLeftRightIcon,
								true
							)}
						{:else}
							<ActivityTimeline activity={capped(matched, sliceCap(b, 6))} />
						{/if}
					</div>
				{:else}
					<Card.Root glass class="h-full">
						<WidgetHeader title="Activity" href={viewAllHref('activity')} {interactive} />
						<Card.Content class="space-y-4">
							{@render toolbar()}
							{#if matched.length === 0 && filtered}
								{@render emptyNote(
									'No activity matches',
									'Try changing your search or account filter.',
									SearchXIcon
								)}
							{:else if s.activity.length === 0}
								{@render emptyNote(
									'Your activity will appear here',
									'Payments, dividends, trades and fees will show here as they happen.',
									ArrowLeftRightIcon
								)}
							{:else if variantOf(b) === 'table'}
								<!-- Bounded and scrolled for the same reason the holdings table above is: the
								     table variant takes NO slice cap (`sliceCap` exempts it), so a long ledger
								     rendered every movement and the page ran off the bottom, taking the toolbar
								     with it.

								     A VIEWPORT bound, and that is what makes it safe: the masonry cell is sized
								     FROM this card, so a height asked of the cell closes the feedback loop the
								     147,000px note below records. `100svh` is not a percentage of anything the
								     grid computes, so the cell measures a child that has stopped growing. -->
								<ActivityTable
									activity={capped(matched, sliceCap(b))}
									maxHeight="max-h-[max(24rem,calc(100svh-22rem))]"
								/>
							{:else}
								<ActivityList activity={capped(matched, sliceCap(b, 6))} />
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}
			{/snippet}
		</FilteredListing>
	{:else if b.type === 'adviser'}
		{@const look = lookOf(b)}
		{#if s.adviser && look === 'portrait'}
			<AdviserPortrait adviser={s.adviser} />
		{:else if s.adviser && look === 'strip'}
			<AdviserStrip adviser={s.adviser} />
		{:else if s.adviser}
			<AdviserCard adviser={s.adviser} />
		{:else}
			<Card.Root glass class="h-full">
				<WidgetHeader title="Your adviser" />
				<Card.Content>
					{@render emptyNote(
						'Your adviser will appear here',
						'Their contact details will be available as soon as your firm assigns someone to you.',
						UserRoundIcon
					)}
				</Card.Content>
			</Card.Root>
		{/if}
	{:else if b.type === 'news'}
		{@const look = lookOf(b)}
		{#if variantOf(b) === 'library'}
			<!-- The whole library as a page (the Insights tab) — chrome-less, like the other
			     full-listing variants; it owns its own empty states, because "nothing matches a
			     filter" and "nothing published" must read differently. The look (card reading
			     list · magazine · journal) is the firm's: this is the page its identity lives on. -->
			<NewsLibrary items={capped(s.news, sliceCap(b))} {look} {interactive} />
		{:else if s.news.length > 0}
			<NewsFeed
				items={capped(s.news, sliceCap(b, 5))}
				{look}
				viewAllHref={viewAllHref('news')}
				{interactive}
			/>
		{:else}
			<Card.Root glass class="h-full">
				<WidgetHeader title="Market news" />
				<Card.Content>
					{@render emptyNote(
						'You’re up to date',
						'New insights and updates from your firm will appear here.',
						NewspaperIcon
					)}
				</Card.Content>
			</Card.Root>
		{/if}
	{:else if b.type === 'documents'}
		{@const look = lookOf(b)}
		{@const grouped = variantOf(b) === 'grouped'}
		{@const docToolbar = b.settings?.['toolbar'] === true}
		<!-- show_requests renders only against an HONEST count: resolveSlices returns null when
		     the loader fetched none (never a fabricated number), and 0 means nothing outstanding —
		     no banner either way. -->
		{@const requestsCount =
			b.settings?.['show_requests'] === true &&
			s.requestsOutstanding !== null &&
			s.requestsOutstanding > 0
				? s.requestsOutstanding
				: null}
		<!-- No account axis (documents don't belong to accounts) and no controlled mode: the
		     documents slice is complete, so search + the category pills stay client-side. The
		     unread note renders only when the payload carries read-state (listing.ts); the
		     timeline look marks each new row instead and carries no note. -->
		<FilteredListing
			rows={s.documents}
			toolbar={docToolbar}
			label="Documents"
			placeholder="Search documents…"
			searchText={(d) => [d.name]}
			categoryOf={(d) => d.category}
			categoryStyle={look === 'timeline' ? 'tabs' : 'pills'}
			searchScope="document"
			categoryIcon={documentIcon}
			note={docToolbar && look !== 'timeline' ? unreadNote(s.documents) : null}
			{interactive}
		>
			{#snippet children(matched, toolbar)}
				{#if look === 'timeline'}
					<!-- The closing line offers "your firm" when the firm itself is the contact (no
					     adviser assigned — `Adviser.kind`), else the person. -->
					{@const messageWhom =
						s.adviser?.kind === 'firm' ? 'Message your firm' : 'Message your adviser'}
					<!-- The Docs page as a place rather than a list — and with no heading of its own:
					     the nav named the page the reader just tapped, and a count over a list the
					     reader can see is a number they did not ask for. The categories are the
					     browse bar's tabs (the same bar the Insights library wears —
					     browse-bar.svelte; its search opens the portal's palette on documents), the
					     documents sit in recency groups (this month, last month, earlier this year,
					     each year before), and a closing line offers the adviser for what is not
					     here. Each piece earns its place by the count — the bar only with two
					     categories, its search only past eight rows (filtered-listing). -->
					<div class="space-y-4">
						{#if requestsCount !== null}{@render requestsBanner(requestsCount)}{/if}
						{@render toolbar()}
						{#if s.documents.length === 0}
							{@render emptyNote(
								'Your documents will appear here',
								'Statements, illustrations and agreements will be kept here when they’re ready.',
								FileTextIcon,
								true
							)}
						{:else if matched.length === 0}
							{@render emptyNote(
								'No documents match',
								'Try changing your search or category filter.',
								SearchXIcon,
								true
							)}
						{:else}
							<DocumentsTimeline documents={capped(matched, sliceCap(b, 3))} />
						{/if}
						<p class="text-muted-foreground px-1 text-sm">
							Looking for something that isn’t here?
							{#if interactive}
								<a
									href="/messages"
									class="text-foreground font-medium underline-offset-4 hover:underline"
								>
									{messageWhom}
								</a>
							{:else}
								<span class="text-foreground font-medium">{messageWhom}</span>
							{/if}
						</p>
					</div>
				{:else if look === 'shelf'}
					<div class="space-y-3">
						{#if requestsCount !== null}{@render requestsBanner(requestsCount)}{/if}
						{@render toolbar()}
						{#if s.documents.length === 0}
							{@render emptyNote(
								'Your documents will appear here',
								'Statements, illustrations and agreements will be kept here when they’re ready.',
								FileTextIcon,
								true
							)}
						{:else if matched.length === 0}
							{@render emptyNote(
								'No documents match',
								'Try changing your search or category filter.',
								SearchXIcon,
								true
							)}
						{:else}
							<DocumentsShelf documents={capped(matched, sliceCap(b, 3))} {interactive} />
						{/if}
					</div>
				{:else if look === 'index'}
					<div class="space-y-3">
						{#if requestsCount !== null}{@render requestsBanner(requestsCount)}{/if}
						{@render toolbar()}
						{#if s.documents.length === 0}
							{@render emptyNote(
								'Your documents will appear here',
								'Statements, illustrations and agreements will be kept here when they’re ready.',
								FileTextIcon,
								true
							)}
						{:else if matched.length === 0}
							{@render emptyNote(
								'No documents match',
								'Try changing your search or category filter.',
								SearchXIcon,
								true
							)}
						{:else}
							<DocumentsIndex documents={capped(matched, sliceCap(b, 3))} />
						{/if}
					</div>
				{:else}
					<Card.Root glass class="h-full">
						<!-- No view-all with zero documents: the link would point at a guaranteed-empty
						     page. -->
						<WidgetHeader
							title="Documents"
							href={s.documents.length > 0 ? viewAllHref('documents') : null}
							{interactive}
						/>
						<Card.Content class="space-y-4">
							{#if requestsCount !== null}{@render requestsBanner(requestsCount)}{/if}
							{@render toolbar()}
							{#if s.documents.length === 0}
								{@render emptyNote(
									'Your documents will appear here',
									'Statements, illustrations and agreements will be kept here when they’re ready.',
									FileTextIcon
								)}
							{:else if matched.length === 0}
								{@render emptyNote(
									'No documents match',
									'Try changing your search or category filter.',
									SearchXIcon
								)}
							{:else}
								<DocumentsList documents={capped(matched, sliceCap(b, 3))} {grouped} />
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}
			{/snippet}
		</FilteredListing>
	{:else if b.type === 'actionItems'}
		{#if lookOf(b) === 'banner'}
			<ActionItemsBanner
				items={s.tasks}
				limit={clampLimit('actionItems', b.settings?.['limit'])}
				hideWhenEmpty={b.settings?.['hide_when_empty'] !== false}
				{interactive}
				{completing}
				{oncompletetask}
			/>
		{:else}
			<ActionItemsCard
				items={s.tasks}
				limit={clampLimit('actionItems', b.settings?.['limit'])}
				hideWhenEmpty={b.settings?.['hide_when_empty'] !== false}
				{interactive}
				{completing}
				{oncompletetask}
			/>
		{/if}
	{:else if b.type === 'onboardingProgress'}
		<!-- 'banner' (today's component) is this type's canonical look under EITHER finish; only
		     an explicit 'slim'/'milestones' choice swaps it (looks.ts CARD_AUTO/LEDGER_AUTO). -->
		{#if lookOf(b) === 'slim'}
			<OnboardingSlim onboarding={s.onboarding} {interactive} />
		{:else if lookOf(b) === 'milestones'}
			<OnboardingMilestones onboarding={s.onboarding} {interactive} />
		{:else}
			<OnboardingProgress onboarding={s.onboarding} {interactive} />
		{/if}
	{:else if b.type === 'message'}
		{@const look = lookOf(b)}
		{#if look === 'note'}
			<MessageNote
				title={clampText('message', 'title', b.settings?.['title'])}
				body={clampText('message', 'body', b.settings?.['body'])}
			/>
		{:else if look === 'banner'}
			<MessageBanner
				title={clampText('message', 'title', b.settings?.['title'])}
				body={clampText('message', 'body', b.settings?.['body'])}
			/>
		{:else}
			<MessageCard
				title={clampText('message', 'title', b.settings?.['title'])}
				body={clampText('message', 'body', b.settings?.['body'])}
			/>
		{/if}
	{:else}
		<!-- Unknown/future block — a tasteful placeholder until its data lands. The raw type name
		     is machine vocabulary and must not be shown to a client; it rides a data attribute
		     (auto-escaped) so support can still identify the block from the DOM. -->
		<Card.Root glass class="h-full" data-unknown-block-type={b.type}>
			<Card.Content class="flex h-full flex-col justify-center">
				{@render emptyNote(
					'Nothing to show here',
					'This section will appear once there is something to show.',
					LayersIcon
				)}
			</Card.Content>
		</Card.Root>
	{/if}
{/snippet}

<!-- Below `sm` the grid takes most of ClientShell's `px-4` content gutter back (`-mx-2` leaves
     8px of it) and the tiles stack tight: a phone has no room to spend on a full gutter AND the
     card's own padding, and two insets deep everything read as smaller than it was — but a
     sheet touching the glass edge-on read as a table, so a hairline of ground stays either side
     to show the tiles are tiles. They keep their radius for the same reason. -->
<div
	bind:this={gridEl}
	class="client-home grid grid-cols-1 gap-6 max-sm:-mx-2 max-sm:gap-2 md:grid-cols-12"
	class:masonry
>
	<!-- `data-block-id` is the cell's identity on the page: what a measured height is reported
	     under, and how the studio (or support) tells two instances of one block type apart. -->
	{#each cells as { b, key } (key)}
		<!-- `style:` directives, NOT a style="" string: the string form is re-written wholesale on
		     every update, which silently wipes the `--rs` masonryCell sets imperatively. Editing a
		     block's height in the studio changed `--ch`, and the cell it had just measured dropped
		     back to the CSS fallback span. -->
		<div
			class="client-cell"
			data-block-id={key}
			style:--cw={blockW(b)}
			style:--ch={blockH(b)}
			use:masonryCell={{ key, h: blockH(b) }}
		>
			<div class="client-cell-inner">
				{@render block(b)}
			</div>
		</div>
	{/each}
</div>

<style>
	/* Column span drives width. Height is FLUID: once JS mounts (`.masonry`), every cell spans
	   micro-rows (8px) equal to its own content's natural height, so a short card's neighbour
	   tucks up beneath it instead of every grid row cutting straight across the page. The authored
	   `h` survives as a FLOOR on that span (masonry.ts cellSpan), and the widget stretches through
	   that own floor — never through a neighbouring card's height. Content taller than the floor
	   still grows naturally. Spans apply only from `md` up; on mobile every block is full width
	   while retaining its own authored minimum, so the layout stays responsive. */
	.client-home {
		/* SPARSE, deliberately — not `dense`.

		   A layout has to be the same shape for every client. `dense` back-fills earlier holes with
		   later blocks, and the size of those holes comes from the CLIENT'S OWN DATA: two accounts
		   make a shorter card than six, which changes which gaps exist, which changes which blocks
		   get pulled up into them. A firm arranging their portal would see one arrangement and
		   their clients would each get a different one — observed as an adviser card growing by one
		   row and sending `news` from a third of the way down the page to the bottom of it.

		   Sparse flow never moves the cursor backwards, so a block's COLUMN is a function of the
		   authored widths and the authored order alone. Heights stay fluid above each authored
		   floor — that is the whole point of the masonry spans above — but nothing is relocated by
		   how much content a neighbour happens to have.

		   The cost is honest: a tall block now leaves a gap beside it that nothing fills, so a
		   ragged layout stays ragged instead of tidying itself. That is the trade for predictability
		   and it was made deliberately. Do not restore `dense` to close the gaps; author narrower
		   blocks, or give the builder explicit placements. */
		grid-auto-flow: row;
	}
	.client-cell {
		grid-column: span 1;
		min-width: 0;
		/* Each cell is a size container: the block components inside key their layouts off the
		   SPAN they were given (container queries), not the viewport — the root cause of the
		   "desktop table crushed into a 350px block" defects. Inline-size only, so blocks can
		   still grow the row height. */
		container-type: inline-size;
	}
	.client-cell-inner {
		/* The authored height is the widget's usable canvas, not merely a reservation made by the
		   grid around a naturally short child. A flex column lets the rendered block occupy that
		   whole canvas while still growing when its real content needs more room. Crucially this
		   floor belongs to the MEASURED box itself and does not reference the masonry cell's
		   computed height, so it cannot create the cell → child → cell feedback loop below.
		   The floor is applied at md+ only (below): on a phone the grid is a single column, the
		   authored span is a desktop canvas, and a 12rem floor under a one-card stack or a
		   two-row list was a phantom gap before the next widget. Content sizes the phone. */
		display: flex;
		width: 100%;
		flex-direction: column;
	}
	.client-cell-inner > :global(*) {
		min-width: 0;
		width: 100%;
		flex: 1 1 auto;
	}
	.client-cell-inner :global([data-slot='card-content']) {
		/* Card roots are flex columns. Let their content use the spare authored height instead of
		   leaving a short body stranded beneath the heading. Lists remain naturally top-aligned;
		   an empty state opts into centring through the rule below. */
		flex: 1 1 auto;
	}
	.client-cell-inner :global([data-slot='card-content']:has(> [data-slot='empty-state'])) {
		display: flex;
		flex-direction: column;
	}
	@media (min-width: 768px) {
		.client-cell {
			grid-column: span var(--cw);
		}
		.client-cell-inner {
			min-height: calc(var(--ch) * 6rem);
		}
		.client-home:not(.masonry) .client-cell {
			min-height: calc(var(--ch) * 6rem);
		}
		.client-home.masonry {
			/* Micro row tracks. Row-gap goes to 0 because a spanned item would otherwise absorb
			   a gap per micro-row; the visual 1.5rem rhythm below each card is baked into the
			   measured span instead (see masonryCell). Column gap is untouched. */
			grid-auto-rows: 8px;
			row-gap: 0;
		}
		.client-home.masonry .client-cell {
			/* 24 micro-rows ≈ 192px: a sane presence for the first frame before measurement. */
			grid-row: span var(--rs, 24);
		}
		.client-home.masonry .client-cell-inner {
			/* THE MEASURED BOX MUST NOT STRETCH TO THE GRID CELL. It does carry the authored
			   min-height above, which is independent and therefore safe. `masonryCell` sizes the
			   cell FROM this element, so anything that lets it grow to the cell instead closes a feedback loop:
			   span = ceil((inner + gap) / 8) makes the cell `inner + gap` tall, a card inside that
			   fills the cell reports `inner + gap` on the next measurement, and the block gains one
			   gap PER FRAME. Observed on the market-news card (`flex flex-col h-full`, so `h-full`
			   resolved through this auto-height div to the cell's definite grid height): it passed
			   147,000px and was still climbing 24px a frame, which is what "the preview scrolls for
			   ever" was.

			   `fit-content` keeps the box content-sized, so a percentage height inside resolves
			   against the content rather than against the span we are about to compute from it. The
			   non-masonry path is untouched — there `h-full` is doing its real job of levelling
			   cards that share a row track. */
			height: fit-content;
		}
		.client-home.masonry .client-cell-inner > :global(*) {
			/* AND THE CARD MUST NOT ASK FOR A PERCENTAGE OF THE GRID CELL. Flex growth against the
			   inner box's authored min-height is safe; percentage height against the cell is not.
			   `fit-content` alone was a
			   Chromium-shaped fix: `fit-content` is `min(max-content, max(min-content, stretch))`,
			   and the `stretch` term IS the grid height we are about to compute — Firefox resolves
			   the card's `h-full` through it and the loop above ran anyway, which is what "the
			   branding preview grows for ever" was (the action-items card, and every other block
			   root carrying `h-full`: accounts summary, adviser, message, and the nine variants
			   portal-page renders inline).

			   Killing the percentage at source is engine-independent in a way that reasoning about
			   which intrinsic size an engine resolves against is not. It is also what masonry MEANS:
			   a cell is as tall as its card, so a card asking to be as tall as its cell is asking a
			   question with no answer. `height`, deliberately, not `min-height` — `min-h-*` on a
			   block root (adviser-strip) is a real floor and stays. The non-masonry path keeps
			   `h-full` and its row-levelling. */
			height: auto;
		}
	}
</style>
