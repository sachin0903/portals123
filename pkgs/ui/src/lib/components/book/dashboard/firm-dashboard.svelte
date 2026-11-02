<script lang="ts">
	// The firm dashboard: four KPI tiles, the book, and the "action required" queue.
	//
	// Shared so the platform's `/` and admin's operate-as-firm dashboard are one screen, not two
	// that drift. Each portal owns only its load (the platform with the caller's session, admin with
	// `?tenant=`), its links (`links`) and its navigation (`onquery` / `pageHref` / `onretry`).
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { ListingSkeleton } from '../../ui/listing-skeleton/index.js';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { SectionHeading } from '../../ui/section-heading/index.js';
	import { Skeleton } from '../../ui/skeleton/index.js';
	import { Streamed } from '../../ui/streamed/index.js';
	import type { DataTableSort } from '../../ui/data-table/index.js';
	import StatTile from '../../data/stat-tile.svelte';
	import { formatGBP, formatGBPCompact } from '../../../money';
	import { formatDate } from '../../../dates.js';
	import UsersIcon from '@lucide/svelte/icons/users';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import TrendingUpIcon from '@lucide/svelte/icons/trending-up';
	import UserCheckIcon from '@lucide/svelte/icons/user-check';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import { ALL, emptyQuery, isFiltered, type ListingQuery } from '../listing/index.js';
	import ActionQueue from './action-queue.svelte';
	import ClientsTable from './clients-table.svelte';
	import Scratchpad from './scratchpad.svelte';
	import { railItems } from './action-queue.js';
	import type { ContactHoldings } from '../clients/holdings.js';
	import type { Dash, DashLinks, FirmOverview } from './dash.js';

	let {
		overview,
		dash,
		holdings = null,
		query,
		links,
		canReviews,
		canJourneys,
		canManage,
		notesEndpoint = '/me/notes',
		onquery,
		pageHref,
		onretry
	}: {
		/** `GET /crm/overview` — the KPI tiles. Streamed SEPARATELY from `dash` (see `dash.ts`) so
		 *  the hero figure paints when its one query answers, not when the slowest of four does. */
		overview: Promise<FirmOverview | null> | FirmOverview | null;
		/** The book and the queue. */
		dash: Promise<Dash> | Dash;
		/** Cash and growth for the book on screen. Streamed beside `dash`, so a slow warehouse
		 *  read does not hold the list. */
		holdings?: Promise<ContactHoldings | null> | null;
		query: ListingQuery;
		links: DashLinks;
		/** Reveals the Reviews door and names reviews in the all-clear copy. */
		canReviews: boolean;
		/** Reveals the Journeys door and the "more in Journeys" pointer under the rail. */
		canJourneys: boolean;
		/** Offers "Go to clients" on an empty book. */
		canManage: boolean;
		/** The portal's proxy for the reader's notes — see `./notes.ts`. Shown only when `dash.notes`
		 *  is a list: admin's operate view has no reader to own one. */
		notesEndpoint?: string;
		onquery: (next: ListingQuery) => void;
		pageHref: (page: number) => string;
		onretry: () => void;
	} = $props();

	/** Rows the queue shows before it defers to the full inbox/requests screens. */
	const QUEUE_LIMIT = 6;

	// NULL: the table falls back to its `sortDefault` column - see `ClientsTable`'s `sort` prop.
	let sort = $state<DataTableSort | null>(null);
	let criteria = $state({ query: '', status: ALL, adviser: ALL, type: ALL });

	/** Provenance stamp for a money figure: "14 Jul 2026", or an honest admission. */
	const asAt = (iso: string | null): string => formatDate(iso, 'unknown');
</script>

{#snippet bookEmpty(d: Dash)}
	{@const narrowed = isFiltered(query)}
	{@const emptyKind = d.clientsForbidden
		? 'forbidden'
		: d.clientsError
			? 'failed'
			: narrowed
				? 'narrowed'
				: 'none'}
	<!-- NOT `chromeless`: the container is where `tone="warning"` lives, and the two failure states
	     must not wear the nothing-here look of the two that are not failures. -->
	<EmptyState
		compact
		tone={emptyKind === 'none' || emptyKind === 'narrowed' ? 'neutral' : 'warning'}
		icon={emptyKind === 'none' || emptyKind === 'narrowed' ? UsersIcon : TriangleAlertIcon}
		title={emptyKind === 'forbidden'
			? 'You can’t read this firm’s clients'
			: emptyKind === 'failed'
				? 'Your clients couldn’t be loaded'
				: emptyKind === 'narrowed'
					? 'No clients match your filters'
					: 'No clients yet'}
		description={emptyKind === 'forbidden'
			? 'Your role doesn’t include reading the client book. An administrator at your firm can add it.'
			: emptyKind === 'failed'
				? 'Could not load your clients. Check your connection and try again.'
				: emptyKind === 'narrowed'
					? 'Try a different search or filter, or clear them to see your whole book.'
					: canManage
						? 'Add your first client to start building your book.'
						: 'Clients appear here once they’re added to your book.'}
		action={emptyKind === 'failed'
			? retryBook
			: emptyKind === 'narrowed'
				? clearBookFilters
				: emptyKind === 'none' && canManage
					? addFirstClient
					: undefined}
	/>
{/snippet}

{#snippet retryBook()}
	<Button variant="outline" size="sm" onclick={() => onretry()}>Try again</Button>
{/snippet}

{#snippet clearBookFilters()}
	<Button variant="outline" size="sm" onclick={() => onquery(emptyQuery(query.keys))}
		>Clear filters</Button
	>
{/snippet}

{#snippet addFirstClient()}
	<Button size="sm" href={links.clients}>Go to clients</Button>
{/snippet}

<!-- The tiles' own shape while `GET /crm/overview` is in flight. No `role="status"` of its own:
     the book's placeholder below announces the loading page once, and this strip is hidden from
     the reader the way the book's inner `ListingSkeleton` is. -->
{#snippet tilesPending()}
	<div class="grid shrink-0 grid-cols-2 gap-4 lg:grid-cols-4" aria-hidden="true">
		{#each [1, 2, 3, 4] as i (i)}
			<div class="glass-panel flex flex-col gap-2 rounded-xl p-4">
				<Skeleton class="h-3.5 w-24" />
				<Skeleton class="h-6 w-28" />
				<Skeleton class="h-3.5 w-20" />
			</div>
		{/each}
	</div>
{/snippet}

<!-- The book and the queue's own shape while their calls are in flight — so nothing jumps
     when the real thing lands. -->
{#snippet dashPending()}
	<div
		class="grid min-h-0 flex-1 gap-4 overflow-hidden lg:grid-cols-[minmax(0,1fr)_22rem]"
		role="status"
		aria-label="Loading"
	>
		<div class="flex min-h-0 flex-col" aria-hidden="true">
			<ListingSkeleton fill={false} pager columns={7} rows={10} />
		</div>
		<section class="flex min-h-0 flex-col" aria-hidden="true">
			<div class="mb-2.5 flex items-center justify-between gap-3">
				<Skeleton class="h-5 w-28" />
				<Skeleton class="h-5 w-20" />
			</div>
			<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
				<CardContent class="flex min-w-0 flex-col gap-3 md:min-h-0 md:flex-1">
					<ul class="divide-border divide-y">
						{#each [1, 2, 3, 4, 5, 6] as row (row)}
							<li class="flex items-center gap-3 py-2.5">
								<Skeleton class="size-2 shrink-0 rounded-full" />
								<div class="flex min-w-0 flex-1 flex-col gap-1.5">
									<Skeleton class="h-3 w-3/4" />
									<Skeleton class="h-3 w-1/2" />
								</div>
								<Skeleton class="size-4 shrink-0" />
							</li>
						{/each}
					</ul>
				</CardContent>
			</Card>
		</section>
	</div>
{/snippet}

<!-- ONE SCREEN: a flex column the height of the viewport under the top bar, with the BOOK as the
     only thing that scrolls. `-m-4 md:-m-6` cancels the shell's canvas padding. The frame is
     rendered once, outside both streams, so neither landing moves the other. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-4 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The KPI tiles, on their own stream: the hero figure is the first thing worth painting and
	     it must not wait for the book. `keep`: a page cut below re-runs the load, and the tiles hold
	     rather than flashing back to bars for a number that has not changed. -->
	<Streamed data={overview} {onretry} keep>
		{#snippet pending()}
			{@render tilesPending()}
		{/snippet}
		{#snippet children(o)}
			{@const unclassified = o ? Math.max(o.clients - o.advised - o.execution_only, 0) : 0}
			{@const advisedSub = [
				`${o?.execution_only ?? 0} execution-only`,
				unclassified > 0 ? `${unclassified} unclassified` : null
			]
				.filter(Boolean)
				.join(' · ')}
			{#if o}
				<div class="grid shrink-0 grid-cols-2 gap-4 lg:grid-cols-4">
					<StatTile
						label="Assets under advice"
						icon={WalletIcon}
						value={o.valued_accounts > 0 ? formatGBPCompact(o.aum) : '—'}
						sub={o.valued_accounts > 0
							? `as at ${asAt(o.values_as_of)}`
							: 'no account holds a value'}
					/>
					<StatTile
						href={links.clients}
						label="Clients"
						icon={UserCheckIcon}
						value={String(o.clients)}
						sub={advisedSub || `${o.advised} advised`}
					/>
					<StatTile
						href={links.accounts}
						label="Accounts"
						icon={WalletIcon}
						value={String(o.accounts)}
						sub="{o.valued_accounts} hold value"
					/>
					<StatTile
						label="Avg. per client"
						icon={TrendingUpIcon}
						value={o.clients ? formatGBP(o.aum / o.clients) : '—'}
						sub="assets under advice"
					/>
				</div>
			{:else}
				<LoadErrorBanner message="Couldn’t load your firm summary." onRetry={() => onretry()} />
			{/if}
		{/snippet}
	</Streamed>

	<!-- `keep`: a page cut in the table below changes the table, not the queue beside it, so the
	     dashboard holds and the table alone shows its rows loading (`stale` → `loading`). -->
	<Streamed data={dash} {onretry} keep>
		{#snippet pending()}
			{@render dashPending()}
		{/snippet}
		{#snippet children(d, stale)}
			{@const queue = railItems(d.journeys, d.reviews, {
				journey: links.journey,
				review: links.review
			})}
			{@const allClearCopy =
				d.journeysError || d.reviewsError
					? 'Some of your work could not be loaded. See the notice above.'
					: canReviews
						? 'No onboarding assigned to you, and nothing awaiting your review.'
						: 'No onboarding assigned to you.'}
			{#if d.clientsError}
				<LoadErrorBanner
					message="Couldn’t load your clients. Try again."
					onRetry={() => onretry()}
				/>
			{/if}

			<div class="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
				<section class="flex min-h-0 flex-col">
					{#snippet tableEmpty()}
						{@render bookEmpty(d)}
					{/snippet}
					<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
						<CardContent
							class="flex min-w-0 flex-col gap-3 md:min-h-0 md:flex-1 md:overflow-hidden"
						>
							<ClientsTable
								rows={d.clients}
								bind:criteria
								bind:sort
								scroll
								empty={tableEmpty}
								listing={query}
								matchCount={d.matchCount}
								facets={d.facets}
								bounds={d.bounds}
								{onquery}
								{pageHref}
								clientHref={links.client}
								loading={stale}
								{holdings}
							/>
						</CardContent>
					</Card>
				</section>

				<section class="flex min-h-0 flex-col">
					<SectionHeading title="Needs you">
						{#snippet actions()}
							{#if canJourneys}
								<Button href={links.journeys} variant="ghost" size="sm">Journeys →</Button>
							{/if}
							{#if canReviews}
								<Button href={links.reviews} variant="ghost" size="sm">Reviews →</Button>
							{/if}
						{/snippet}
					</SectionHeading>
					<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
						<CardContent
							class="flex min-w-0 flex-col gap-3 md:min-h-0 md:flex-1 md:overflow-y-auto"
						>
							{#if d.journeysError || d.reviewsError}
								<Alert variant="destructive">
									<AlertDescription>
										Couldn’t load {d.journeysError && d.reviewsError
											? 'your onboarding or your reviews'
											: d.reviewsError
												? 'your reviews'
												: 'your onboarding'}, so this list is incomplete.
										<Button variant="link" size="sm" class="h-auto p-0" onclick={() => onretry()}>
											Try again
										</Button>
									</AlertDescription>
								</Alert>
							{/if}
							<ActionQueue items={queue} limit={QUEUE_LIMIT} emptyDescription={allClearCopy} />
							{#if queue.length > QUEUE_LIMIT && canJourneys}
								<p class="text-muted-foreground text-xs">
									The most pressing are shown. The rest are on the
									<a href={links.journeys} class="text-brand hover:underline">journeys board</a>.
								</p>
							{/if}
							<!-- The notes sit under the work, divided from it: one is the firm's, the other
							     is the reader's own — stored centrally, read by them alone. -->
							{#if d.notes}
								<div class="border-border mt-auto border-t pt-3">
									{#if d.notesError}
										<p class="text-muted-foreground text-xs">
											Couldn’t load your notes.
											<Button variant="link" size="sm" class="h-auto p-0" onclick={() => onretry()}>
												Try again
											</Button>
										</p>
									{:else}
										<Scratchpad notes={d.notes} endpoint={notesEndpoint} />
									{/if}
								</div>
							{/if}
						</CardContent>
					</Card>
				</section>
			</div>
		{/snippet}
	</Streamed>
</div>
