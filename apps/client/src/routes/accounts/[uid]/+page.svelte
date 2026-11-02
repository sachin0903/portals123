<script lang="ts">
	import {
		ActivityList,
		ActivityTable,
		asAt,
		Badge,
		Card,
		CardContent,
		CardHeader,
		EmptyState,
		HoldingsGrouped,
		humanise,
		LoadErrorBanner,
		PortfolioHeaderCanvas,
		Segmented,
		StatTile,
		StatusBadge
	} from '@graphene/ui';
	import { invalidateAll, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import type { PageData } from './$types';
	import { accountFacts, accountHeaderActions } from '$lib/account-facts';
	import { toActivity } from '$lib/activity';
	import { toPortalHolding } from '$lib/holdings';

	// THE SAME BLOCKS THE HOME PAGE IS MADE OF. This page used to be hand-built: a plain card
	// with a `text-3xl` figure where the home page had the canvas header with its trace and
	// period pills, two sortable desktop `DataTable`s where the home cards had the holdings list
	// and the activity feed, bold `CardTitle`s where every other widget had dropped them. A client
	// tapping an account card arrived in a different product. Now the account wears the portfolio
	// header scoped to itself (`/crm/accounts/{uid}` carries the account's own series), and the
	// bands under it are the widgets the composed pages render, through the same projections
	// (`toPortalHolding`, `toActivity`) the composed loaders use.
	let { data }: { data: PageData } = $props();
	const a = $derived(data.account);

	const name = $derived(a.name ?? a.product_name ?? a.uid ?? `Account ${a.id}`);
	const holdingsTotal = $derived(data.holdings.reduce((s, h) => s + (h.value ?? 0), 0));
	const value = $derived(a.value ?? holdingsTotal);
	// Cash · Invested · Gain/loss for every account, then the wrapper's own facts (ISA/JISA
	// allowance, a pension's split) — `$lib/account-facts`. Never a lone tile.
	const facts = $derived(accountFacts(a, data.holdings));

	// Unordered here: the grouped list orders by asset class, then value within it.
	const holdings = $derived(data.holdings.map(toPortalHolding));
	const activity = $derived(data.transactions.map(toActivity));
	const stamp = $derived(asAt(a.value_as_of));

	// HOLDINGS AND ACTIVITY ARE ONE BAND WITH A SWITCH, not two cards stacked. A real account's
	// holdings list runs to a couple of screens on a phone, and the activity card under it was
	// where nobody scrolled to — the page gave no sign it was there. The switch sits where the
	// band begins, names both and counts them, and the chosen view is shallow-routed: it lives in
	// `page.state` (so a tab is not a navigation and no load re-runs) and in the URL (`?view=`),
	// which is what a reload or a shared link reads. `replaceState` does not update `page.url`,
	// hence the state copy.
	type View = 'holdings' | 'activity';
	const views = $derived([
		{ value: 'holdings', label: `Holdings · ${holdings.length}` },
		{ value: 'activity', label: `Activity · ${activity.length}` }
	]);
	const view = $derived<View>(
		page.state.view ?? (page.url.searchParams.get('view') === 'activity' ? 'activity' : 'holdings')
	);
	function setView(next: string) {
		const chosen: View = next === 'activity' ? 'activity' : 'holdings';
		const url = new URL(page.url);
		if (chosen === 'activity') url.searchParams.set('view', chosen);
		else url.searchParams.delete('view');
		replaceState(url, { ...page.state, view: chosen });
	}
</script>

<div class="space-y-6 max-sm:-mx-2 max-sm:space-y-2">
	<!-- The way back is the shell's (ClientShell `back`), the same on every sub page. The
	     account's type and status lead the record — the header below has no slot for a badge,
	     and they are the two facts that qualify the figure in it. On desktop the row rides up
	     into the shell's back row (`--shell-back-h`, its height) so "‹ Home" and the badges share
	     one line rather than stacking two thin rows over the header. -->
	<div
		class="flex items-center justify-end gap-2 max-sm:px-2 lg:-mt-[var(--shell-back-h,0px)] lg:h-9"
	>
		{#if a.product_type}<Badge variant="secondary">{a.product_type}</Badge>{/if}
		<StatusBadge tone={a.status === 'active' ? 'success' : 'neutral'}>
			{humanise(a.status)}
		</StatusBadge>
	</div>

	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load all of this account. Please try again."
			onRetry={() => invalidateAll()}
		/>
	{/if}

	<!-- The portfolio header, scoped to this account: its own trace, its own period change.
	     Payment and withdrawal appear only when THIS account is execution-only, even if the
	     person also holds advised accounts whose book cannot move money. -->
	<PortfolioHeaderCanvas
		total={value}
		performance={a.performance ?? []}
		label={name}
		actions={accountHeaderActions(a.service_type)}
		disabledActions={data.disabledActions}
		defaultPeriod="1Y"
		asOf={a.value_as_of ?? null}
		period={a.performance_period ?? null}
	/>

	<!-- Two columns on a phone, as many as fit from lg. An odd set (a GIA's three) lets the first
	     tile — Cash — take the whole first row rather than leave a hole beside the last. -->
	<div
		class="grid grid-cols-2 gap-4 max-sm:gap-2 lg:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]"
	>
		{#each facts as fact, i (fact.label)}
			<StatTile
				label={fact.label}
				value={fact.value}
				sub={fact.sub}
				valueTone={fact.valueTone}
				class={facts.length % 2 === 1 && i === 0 ? 'max-lg:col-span-2' : undefined}
			/>
		{/each}
	</div>

	<Card glass>
		<CardHeader class="flex flex-row items-center justify-between gap-3 space-y-0">
			<Segmented options={views} value={view} onValueChange={setView} size="lg" label="Show" />
			{#if view === 'holdings' && stamp}
				<!-- Wraps to two lines on a phone rather than truncating the year off the date. -->
				<span class="text-muted-foreground/70 text-right text-xs leading-tight tabular-nums"
					>{stamp}</span
				>
			{/if}
		</CardHeader>
		<CardContent>
			{#if view === 'holdings'}
				{#if holdings.length === 0}
					<EmptyState
						compact
						icon={LayersIcon}
						title="No holdings"
						description="This account has no investments yet."
					/>
				{:else}
					<!-- The portfolio page's rows, scoped to this account: grouped by asset class, each
					     with its mark, units · price, value and return. No owning-account name (every
					     row is this account's). -->
					<HoldingsGrouped {holdings} showWrapper={false} />
				{/if}
			{:else if activity.length === 0}
				<EmptyState
					compact
					icon={ArrowLeftRightIcon}
					title="No transactions"
					description="Activity on this account will appear here."
				/>
			{:else}
				<!-- The feed on a phone, the ledger table from md up — the same split the composed
				     activity block makes between its list and table variants. -->
				<div class="md:hidden"><ActivityList {activity} /></div>
				<div class="hidden md:block"><ActivityTable {activity} /></div>
			{/if}
		</CardContent>
	</Card>
</div>
