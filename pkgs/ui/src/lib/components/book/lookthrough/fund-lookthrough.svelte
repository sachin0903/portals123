<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function, not a `$derived`, so it can be pinned in a test without rendering the
	 *  panel, which lives behind a popover shut at SSR (see `tasks-listing.svelte::taskFilterDefs`). */
	export function fundLookthroughFilterDefs(input: {
		countryOptions: FilterDef['options'];
		assetClassOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'country', label: 'Country', options: input.countryOptions },
			{ key: 'asset_class', label: 'Asset class', options: input.assetClassOptions },
			{
				key: 'weight',
				label: 'Weight',
				options: [],
				kind: 'numberrange',
				unit: '%',
				min: '0',
				max: '100'
			}
		];
	}
</script>

<script lang="ts">
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import CalendarDaysIcon from '@lucide/svelte/icons/calendar-days';
	import LayersIcon from '@lucide/svelte/icons/layers-3';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { ListingToolbar, matches, optionsFrom, search } from '../listing/index.js';
	import CashComposition from './cash-composition.svelte';
	import CurrencyHedges from './currency-hedges.svelte';
	import NavHistoryChart from './nav-history-chart.svelte';
	import RankedBars from './ranked-bars.svelte';
	import { indirectTotal, normaliseRatio, rankedBreakdown, topHoldings } from './visuals.js';
	import { series } from '../../../categorical.js';
	import { cn } from '../../../utils.js';
	import { withinRange } from '../listing/applied.js';
	import type { FundLookthroughData, LookthroughPosition } from './types.js';
	import { gainDirection } from '../../../money.js';

	let {
		data,
		backHref,
		backLabel = 'Back to holdings',
		accountHref,
		dateHref,
		onDateChange
	}: {
		data: FundLookthroughData;
		backHref?: string;
		backLabel?: string;
		/** The record of the account this holding sits in — the "Your holding" panel names it.
		 *
		 *  Separate from `backHref`, which points at that account's HOLDINGS tab: a reader who has
		 *  drilled into one fund and now wants the account itself should not have to go via the list
		 *  they came from. Absent ⇒ the name stays plain text, which is the client portal's posture. */
		accountHref?: (uid: string) => string;
		dateHref?: (date: string) => string;
		onDateChange?: (date: string) => void;
	} = $props();

	let sort = $state<DataTableSort | null>({ key: 'weight', dir: 'desc' });

	// Client-side, over the whole snapshot: the loader fetches every disclosed position in one
	// call (hundreds to low thousands of rows for a fund, not a paged book), so there is no server
	// query to hand a toolbar to — this narrows the rows already in hand.
	let query = $state('');
	let countryFilter = $state<string[]>([]);
	let assetClassFilter = $state<string[]>([]);
	let weightFilter = $state<string[]>([]);
	// Reuses `data.breakdowns` rather than re-deriving the same vocabulary from `data.positions`
	// a second time: `rankedBreakdown` is the same helper `RankedBars` below already calls on this
	// data to decide which rows are real breakdown entries (a non-empty label with a positive
	// share), so an option offered here is one that can actually be found. If the breakdown is ever
	// computed from something other than a literal `country_name`/`asset_class` match, both places
	// move together instead of drifting into two vocabularies for the same idea.
	const countryOptions = $derived(
		optionsFrom(rankedBreakdown(data.breakdowns.country), (b) => b.label)
	);
	const assetClassOptions = $derived(
		optionsFrom(rankedBreakdown(data.breakdowns.asset_class), (b) => b.label)
	);
	const filterDefs = $derived(fundLookthroughFilterDefs({ countryOptions, assetClassOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(countryFilter.length ? { country: countryFilter } : {}),
		...(assetClassFilter.length ? { asset_class: assetClassFilter } : {}),
		...(weightFilter.length ? { weight: weightFilter } : {})
	});
	function applyFilters(next: Record<string, string[]>): void {
		countryFilter = next.country ?? [];
		assetClassFilter = next.asset_class ?? [];
		weightFilter = next.weight ?? [];
	}
	const dirty = $derived(
		query.trim() !== '' ||
			countryFilter.length > 0 ||
			assetClassFilter.length > 0 ||
			weightFilter.length > 0
	);
	function clearFilters() {
		query = '';
		countryFilter = [];
		assetClassFilter = [];
		weightFilter = [];
	}
	const filteredPositions = $derived(
		data.positions.filter(
			(p) =>
				matches(countryFilter, p.country_name) &&
				matches(assetClassFilter, p.asset_class) &&
				// normaliseRatio first: custodians send weight as either a ratio or a percentage.
				withinRange(normaliseRatio(p.weight) * 100, weightFilter) &&
				search(query, p.name, p.long_name, p.isin, p.sedol)
		)
	);
	// The table's own row count must never silently drop a filter's effect — a reader narrowing to
	// one country and still being told "340 rows" would think the filter had done nothing.
	const positionsCountLabel = $derived(
		filteredPositions.length === data.positions.length
			? `${data.positions.length.toLocaleString('en-GB')} rows`
			: `${filteredPositions.length.toLocaleString('en-GB')} of ${data.positions.length.toLocaleString('en-GB')} rows`
	);
	// The kind split was right; the filter miss had no way out of itself. The snapshot branch stays
	// a plain string because there is no act that fills it — the data is the custodian's.
	const positionsEmptyMessage = $derived(
		data.positions.length === 0 ? 'No positions are available for this snapshot.' : undefined
	);

	const top = $derived(topHoldings(data.positions));
	const indirect = $derived(indirectTotal(data.positions));
	// Where a position sits, for the top-ten row: country, then asset class, whichever are known.
	const placeOf = (p: LookthroughPosition) =>
		[p.country_name ?? p.country_code, p.asset_class ?? p.asset_group]
			.filter((part): part is string => !!part)
			.join(' · ');
	const dateItems = $derived(
		data.available_dates.map((date) => ({
			value: date,
			label: new Date(`${date}T00:00:00`).toLocaleDateString('en-GB', {
				day: 'numeric',
				month: 'short',
				year: 'numeric'
			})
		}))
	);
	const columns = $derived<DataTableColumn<LookthroughPosition>[]>([
		{ key: 'name', header: 'Underlying position', sortable: true, cellClass: 'w-full max-w-0' },
		{ key: 'isin', header: 'ISIN', sortable: true, hideBelowWidth: 'xl' },
		{ key: 'country_name', header: 'Country', sortable: true, hideBelowWidth: '2xl' },
		{
			key: 'asset_class',
			header: 'Asset class',
			sortable: true,
			hideBelowWidth: '3xl'
		},
		{
			key: 'weight',
			header: 'Weight',
			sortable: true,
			align: 'right',
			defaultDir: 'desc'
		},
		{
			key: 'gain_loss',
			header: 'Gain/loss',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelowWidth: '2xl'
		},
		...(data.holding
			? [
					{
						key: 'indirect_value' as const,
						header: 'Your exposure',
						sortable: true,
						align: 'right' as const,
						defaultDir: 'desc' as const
					}
				]
			: [])
	]);
	const pct = (value: number) =>
		normaliseRatio(value).toLocaleString('en-GB', {
			style: 'percent',
			maximumFractionDigits: 1
		});
	const dateLabel = (date: string) =>
		new Date(`${date}T00:00:00`).toLocaleDateString('en-GB', {
			day: 'numeric',
			month: 'long',
			year: 'numeric'
		});
	function changeDate(date: string): void {
		if (date === data.snapshot.as_of_date) return;
		if (onDateChange) onDateChange(date);
		else if (dateHref) window.location.assign(dateHref(date));
	}
</script>

{#snippet noPositionMatches()}
	<EmptyState
		icon={SearchXIcon}
		title="No positions match your filters"
		description="Try a different country or asset class, or clear the filters to see all {data
			.positions.length} underlying positions."
		action={clearPositionFilters}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearPositionFilters()}
	<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>
{/snippet}

<div class="space-y-5">
	{#if backHref}
		<a
			href={backHref}
			class="text-muted-foreground hover:text-foreground inline-flex min-h-11 items-center gap-1.5 text-sm font-medium transition-colors"
		>
			<ArrowLeftIcon class="size-4" />
			{backLabel}
		</a>
	{/if}

	<!-- The hero band every other record screen anchors on (`hero-surface`, docs/design-language.md):
	     the product's ink, lit from the top-right, the firm's accent as the specular hairline. It was
	     a pale primary wash, which left the whole report one tone. In-hero text takes the hero inks,
	     never the page's, so a re-themed hero stays legible. -->
	<section class="hero-surface rounded-xl p-5 sm:p-7" aria-labelledby="lookthrough-title">
		<span class="hero-hairline"></span>
		<!-- A brand bloom behind the headline figure, so the band is not a flat slab. -->
		<div
			class="pointer-events-none absolute -top-24 -right-16 size-72 rounded-full opacity-60 blur-3xl"
			style="background: radial-gradient(closest-side, color-mix(in oklch, var(--hero-accent) 55%, transparent), transparent)"
			aria-hidden="true"
		></div>
		<div class="relative grid gap-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
			<div class="min-w-0">
				<div class="mb-3 flex flex-wrap items-center gap-2 text-xs font-medium">
					<span class="hero-toggle-on inline-flex items-center gap-1.5 rounded-full px-2.5 py-1">
						<LayersIcon class="size-3.5" style="color: var(--hero-accent)" />
						Fund look-through
					</span>
					{#if data.fund.source}
						<span
							class="rounded-full px-2.5 py-1 ring-1 ring-inset"
							style="color: var(--hero-muted); --tw-ring-color: color-mix(in oklch, var(--hero-foreground) 22%, transparent)"
						>
							{data.fund.source}
						</span>
					{/if}
				</div>
				<h1
					id="lookthrough-title"
					class="max-w-3xl text-2xl font-semibold tracking-tight sm:text-3xl"
				>
					{data.fund.name}
				</h1>
				<p class="mt-2 text-sm" style="color: var(--hero-muted)">
					{#if data.holding}
						{data.share_class.name ?? 'Share class'}
						{#if data.share_class.isin}
							· <span class="font-mono">{data.share_class.isin}</span>{/if}
					{:else}
						Fund code <span class="font-mono">{data.fund.fund_code ?? '—'}</span>
					{/if}
				</p>
			</div>
			<div class="lg:text-right">
				{#if data.holding}
					<p class="text-xs font-medium tracking-wide uppercase" style="color: var(--hero-muted)">
						Your holding
					</p>
					<p class="mt-1 text-3xl font-semibold tracking-tight tabular-nums sm:text-4xl">
						<Money
							amount={data.holding.value}
							currency={data.holding.currency}
							minorClass="text-[var(--hero-foreground)]/55"
						/>
					</p>
					<p class="mt-1 text-sm" style="color: var(--hero-muted)">
						{#if data.holding.account_uid && accountHref}
							<a
								href={accountHref(data.holding.account_uid)}
								class="underline decoration-[color-mix(in_oklch,var(--hero-foreground)_35%,transparent)] underline-offset-4 transition-colors hover:text-[var(--hero-foreground)]"
							>
								{data.holding.account_name ?? 'Account'}
							</a>
						{:else}
							{data.holding.account_name ?? 'Account'}
						{/if}
					</p>
				{:else}
					<p class="text-xs font-medium tracking-wide uppercase" style="color: var(--hero-muted)">
						Matching share classes
					</p>
					<p class="mt-1 text-3xl font-semibold tracking-tight tabular-nums sm:text-4xl">
						{data.share_classes.length}
					</p>
					<p class="mt-1 text-sm" style="color: var(--hero-muted)">
						ISINs recognised by the platform
					</p>
				{/if}
			</div>
		</div>
		<div
			class="relative mt-6 grid grid-cols-2 gap-3 border-t pt-4 sm:grid-cols-4"
			style="border-color: color-mix(in oklch, var(--hero-foreground) 12%, transparent)"
		>
			{#if data.holding}
				<div>
					<p class="text-xs" style="color: var(--hero-muted)">Underlying exposure</p>
					<p class="mt-1 font-semibold">
						<Money
							amount={indirect}
							currency={data.holding?.currency ?? 'GBP'}
							minorClass="text-[var(--hero-foreground)]/55"
						/>
					</p>
				</div>
			{/if}
			<div>
				<p
					class="text-xs"
					style="color: var(--hero-muted)"
					title="Share of the fund accounted for by the disclosed positions"
				>
					Disclosed
				</p>
				<p class="mt-1 font-semibold tabular-nums">{pct(data.snapshot.coverage)}</p>
			</div>
			<div>
				<p class="text-xs" style="color: var(--hero-muted)">Positions</p>
				<p class="mt-1 font-semibold tabular-nums">
					{data.snapshot.position_count.toLocaleString('en-GB')}
				</p>
			</div>
			<div>
				<p class="text-xs" style="color: var(--hero-muted)">Valuation date</p>
				<p class="mt-1 font-semibold">{dateLabel(data.snapshot.as_of_date)}</p>
			</div>
		</div>
	</section>

	{#if !data.holding}
		<Card glass>
			<CardContent>
				<div>
					<h2 class="font-semibold">Matching share-class ISINs</h2>
					<p class="text-muted-foreground mt-1 text-xs">
						A visible holding with any of these ISINs links to this fund composition.
					</p>
				</div>
				<ul class="mt-4 grid gap-2 md:grid-cols-2" aria-label="Matching share classes">
					{#each data.share_classes as shareClass (shareClass.isin)}
						<li class="bg-muted/35 rounded-lg border px-3 py-2.5">
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<p class="truncate text-xs font-medium" title={shareClass.name}>
										{shareClass.name}
									</p>
									<p class="text-muted-foreground mt-1 text-xs">
										Class {shareClass.class_code}
										{#if shareClass.sedol}
											· SEDOL {shareClass.sedol}{/if}
									</p>
								</div>
								<code class="bg-background shrink-0 rounded-md border px-2 py-1 text-xs">
									{shareClass.isin}
								</code>
							</div>
						</li>
					{/each}
				</ul>
			</CardContent>
		</Card>
	{/if}

	<section aria-labelledby="analytics-title">
		<div class="mb-3">
			<h2 id="analytics-title" class="text-lg font-semibold">Inside the fund</h2>
			<p class="text-muted-foreground mt-1 text-xs">
				Performance, liquidity, maturity and currency detail supplied in the IFSL workbook.
			</p>
		</div>
		<div class="grid gap-4 lg:grid-cols-2">
			<Card glass>
				<CardContent>
					<NavHistoryChart history={data.nav_history} />
				</CardContent>
			</Card>
			<Card glass>
				<CardContent>
					<RankedBars title="Bond maturity profile" rows={data.maturity_profile} step={2} />
					{#if !data.maturity_profile.length}
						<p class="text-muted-foreground mt-4 text-xs">
							No dated bond maturities were reported for this fund.
						</p>
					{/if}
				</CardContent>
			</Card>
			<Card glass>
				<CardContent>
					<CashComposition coverage={data.snapshot.coverage} cash={data.cash} />
				</CardContent>
			</Card>
			<Card glass>
				<CardContent>
					<CurrencyHedges contracts={data.currency_contracts} />
				</CardContent>
			</Card>
		</div>
	</section>

	<div class="flex flex-wrap items-end justify-between gap-3">
		<div>
			<h2 class="text-lg font-semibold">
				{data.holding ? 'What you own indirectly' : 'Published fund composition'}
			</h2>
			<p class="text-muted-foreground mt-1 text-xs">
				{data.holding
					? 'The fund’s published positions applied to your holding value.'
					: 'The underlying positions reported for this fund snapshot.'}
			</p>
		</div>
		{#if dateItems.length > 1}
			<div class="w-full sm:w-56">
				<label
					for="lookthrough-date"
					class="text-muted-foreground mb-1.5 flex items-center gap-1.5 text-xs font-medium"
				>
					<CalendarDaysIcon class="size-3.5" /> Historical snapshot
				</label>
				<SelectMenu
					id="lookthrough-date"
					value={data.snapshot.as_of_date}
					items={dateItems}
					onValueChange={changeDate}
				/>
			</div>
		{/if}
	</div>

	<Card glass>
		<CardContent class="space-y-4">
			<div class="flex items-start justify-between gap-4">
				<div>
					<h2 class="font-semibold">Largest underlying positions</h2>
					<p class="text-muted-foreground mt-1 text-xs">
						{#if top.rows.length}
							The top {top.rows.length} of {top.count.toLocaleString('en-GB')} positions make up
							<span class="text-foreground font-medium tabular-nums">{pct(top.share)}</span> of the
							fund{#if data.holding}
								— <Money
									amount={top.rows.reduce((sum, row) => sum + row.indirect_value, 0)}
									currency={data.holding.currency}
								/> of your holding{/if}.
						{:else}
							The fund’s biggest positions by weight.
						{/if}
					</p>
				</div>
				<LayersIcon class="text-muted-foreground size-5" />
			</div>
			{#if top.rows.length}
				<ol class="divide-y" aria-label="Largest underlying positions">
					{#each top.rows as row, index (`${row.ordinal}-${row.isin ?? row.name}`)}
						{@const place = placeOf(row)}
						<li class="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-x-3 py-2.5">
							<!-- The top three carry the brand in their rank; the rest step back. -->
							<span
								class={cn(
									'grid size-6 place-items-center rounded-full text-xs font-semibold tabular-nums',
									index < 3 ? 'text-[var(--brand-foreground)]' : 'text-muted-foreground'
								)}
								style={index < 3 ? `background:${series(index)}` : undefined}
							>
								{index + 1}
							</span>
							<div class="min-w-0">
								<div class="flex items-baseline justify-between gap-3">
									<p class="truncate text-sm font-medium" title={row.long_name ?? row.name}>
										{row.name}
									</p>
									<span class="shrink-0 text-sm font-semibold tabular-nums">{pct(row.weight)}</span>
								</div>
								<div class="mt-1 flex items-center gap-3">
									<div class="bg-muted h-1.5 min-w-0 flex-1 overflow-hidden rounded-full">
										<div
											class="h-full rounded-full"
											style:width={`${top.max ? (normaliseRatio(row.weight) / top.max) * 100 : 0}%`}
											style:background="linear-gradient(90deg, var(--primary), var(--brand))"
										></div>
									</div>
									{#if place}
										<span class="text-muted-foreground max-w-[50%] truncate text-xs" title={place}>
											{place}
										</span>
									{/if}
								</div>
							</div>
							<div class="w-24 text-right text-xs">
								{#if data.holding}
									<Money
										amount={row.indirect_value}
										currency={data.holding.currency}
										class="font-medium"
									/>
								{:else if row.isin}
									<span class="text-muted-foreground font-mono">{row.isin}</span>
								{/if}
							</div>
						</li>
					{/each}
				</ol>
			{:else}
				<p class="text-muted-foreground py-8 text-center text-xs">
					No position weights are available for this snapshot.
				</p>
			{/if}
		</CardContent>
	</Card>

	<!-- Each breakdown takes its own step of the brand ramp, so the three read as three. -->
	<div class="grid gap-4 lg:grid-cols-3">
		<Card glass
			><CardContent
				><RankedBars title="Country" rows={data.breakdowns.country} step={0} /></CardContent
			></Card
		>
		<Card glass
			><CardContent
				><RankedBars title="Currency" rows={data.breakdowns.currency} step={1} /></CardContent
			></Card
		>
		<Card glass
			><CardContent
				><RankedBars title="Asset class" rows={data.breakdowns.asset_class} step={2} /></CardContent
			></Card
		>
	</div>

	<section aria-labelledby="positions-title">
		<div class="mb-3 flex items-end justify-between gap-4">
			<div>
				<h2 id="positions-title" class="text-lg font-semibold">All underlying positions</h2>
				<p class="text-muted-foreground mt-1 text-xs">
					Full published portfolio for this snapshot.
				</p>
			</div>
			<span class="text-muted-foreground shrink-0 text-xs tabular-nums">{positionsCountLabel}</span>
		</div>
		<Card glass class="overflow-hidden">
			<CardContent class="space-y-3">
				<ListingToolbar
					bind:query
					placeholder="Search positions, ISIN or SEDOL"
					{dirty}
					onclear={clearFilters}
					{filterDefs}
					filterValue={barValue}
					onfilters={applyFilters}
				/>
				<DataTable
					{columns}
					rows={filteredPositions}
					bind:sort
					getKey={(row) => `${row.ordinal}-${row.isin ?? row.name}`}
					label="Underlying positions"
					empty={positionsEmptyMessage ?? noPositionMatches}
				>
					{#snippet cell(row, column)}
						{#if column.key === 'name'}
							<div class="min-w-0">
								<p class="truncate font-medium" title={row.long_name ?? row.name}>{row.name}</p>
								{#if row.long_name && row.long_name !== row.name}
									<p class="text-muted-foreground mt-0.5 truncate text-xs">{row.long_name}</p>
								{:else}
									<RecordId value={row.sedol} class="mt-0.5" />
								{/if}
							</div>
						{:else if column.key === 'isin'}{#if row.isin}<span
									class="text-muted-foreground font-mono text-xs">{row.isin}</span
								>{:else}<span class="text-muted-foreground">—</span>{/if}
						{:else if column.key === 'country_name'}<span
								>{row.country_name ?? row.country_code ?? '—'}</span
							>
						{:else if column.key === 'asset_class'}{#if row.asset_class ?? row.asset_group}<Badge
									variant="outline"
									class="font-normal">{row.asset_class ?? row.asset_group}</Badge
								>{:else}<span class="text-muted-foreground">—</span>{/if}
						{:else if column.key === 'weight'}<span class="tabular-nums">{pct(row.weight)}</span>
						{:else if column.key === 'gain_loss'}<Money
								amount={row.gain_loss}
								currency="GBP"
								tone="directional"
								class={gainDirection(row.gain_loss) ? 'font-medium' : undefined}
							/>
						{:else if column.key === 'indirect_value'}<Money
								amount={row.indirect_value}
								currency={data.holding?.currency ?? 'GBP'}
								class="font-medium"
							/>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	</section>

	<p class="text-muted-foreground text-xs">
		Look-through figures use the fund’s {dateLabel(data.snapshot.as_of_date)} disclosure. Residual or
		unclassified exposure: {pct(data.snapshot.residual)}.
	</p>
</div>
