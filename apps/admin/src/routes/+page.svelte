<script lang="ts">
	// Internal Graphene operations portal. Deploys on a separate ingress locked behind the
	// corp VPN / IP allowlist — not reachable from the public internet.
	import { invalidateAll } from '$app/navigation';
	import {
		buttonVariants,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState,
		HeroPanel,
		LoadErrorBanner,
		PageHeader,
		RecordId,
		SectionHeading,
		StatTile,
		StatusBadge
	} from '@graphene/ui';
	import { ListingToolbar, matches, search, type FilterDef } from '@graphene/ui/book/listing';
	import BuildingIcon from '@lucide/svelte/icons/building-2';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import UsersIcon from '@lucide/svelte/icons/users';
	import WrenchIcon from '@lucide/svelte/icons/wrench';
	import type { TenantRow } from './+page.server';

	let { data } = $props();

	const tenants = $derived(data.tenants);
	// A sum of per-firm login counts. Email uniqueness is per tenant, so one human with access to
	// two firms is two logins — the tile says "Logins", not "Users", for that reason.
	const userTotal = $derived(tenants.reduce((sum, t) => sum + (t.user_count ?? 0), 0));
	const isReady = (t: TenantRow) => t.has_environment && t.has_keys && t.has_orgs;
	const needsSetup = $derived(tenants.filter((t) => !isReady(t)));
	const readyCount = $derived(tenants.length - needsSetup.length);
	const activeCount = $derived(tenants.filter((t) => t.enabled).length);

	// The roster's own narrowing. Client-side and honest about it: unlike `/tenants`, this loader
	// fetches the WHOLE estate in one unpaged call, so filtering the array in hand IS filtering
	// the estate. The tiles and the "needs setup" list above deliberately keep counting every
	// firm — they answer "how is the estate doing", which a filter must not quietly rewrite.
	let query = $state('');
	let statusFilter = $state<string[]>([]);
	let setupFilter = $state<string[]>([]);

	// `single`: two-valued (active/disabled), so "Active OR Disabled" would just mean no filter.
	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'status',
			label: 'Status',
			options: [
				{ value: 'active', label: 'Active' },
				{ value: 'disabled', label: 'Disabled' }
			],
			single: true,
			allLabel: 'All statuses'
		},
		{
			key: 'setup',
			label: 'Setup',
			options: [
				{ value: 'ready', label: 'Ready' },
				{ value: 'setup_needed', label: 'Setup needed' }
			],
			single: true,
			allLabel: 'All setup states'
		}
	]);
	const filterValue = $derived<Record<string, string[]>>({
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(setupFilter.length ? { setup: setupFilter } : {})
	});
	function onFilters(next: Record<string, string[]>) {
		statusFilter = next.status ?? [];
		setupFilter = next.setup ?? [];
	}

	const dirty = $derived(statusFilter.length > 0 || setupFilter.length > 0);
	function clearFilters() {
		statusFilter = [];
		setupFilter = [];
	}

	const visibleTenants = $derived(
		tenants.filter(
			(t) =>
				matches(statusFilter, t.enabled ? 'active' : 'disabled') &&
				// The same readiness rule the Setup badge in the row uses, so the filter and the
				// column can never disagree about which firms are ready.
				matches(setupFilter, isReady(t) ? 'ready' : 'setup_needed') &&
				search(query, t.name, t.alias)
		)
	);
	// No firms ⇒ show a full bar rather than NaN%.
	const readyPct = $derived(
		tenants.length === 0 ? 100 : Math.round((readyCount / tenants.length) * 100)
	);

	/** The remaining onboarding steps for a tenant, in order. */
	function missingSteps(t: TenantRow): string[] {
		const steps: string[] = [];
		if (!t.has_environment) steps.push('Register environment');
		if (!t.has_keys) steps.push('Set keys');
		if (!t.has_orgs) steps.push('Create the firm org');
		return steps;
	}

	// Every heading here is clickable. `Setup` and `Status` are DERIVED — the row holds `enabled`
	// and the readiness facts, not the words on the badge — so each says what to order by rather
	// than falling through to a field of that name, which does not exist.
	const firmColumns: DataTableColumn<TenantRow>[] = [
		{ key: 'name', header: 'Firm', sortable: true, sortDefault: true },
		{ key: 'user_count', header: 'Users', sortable: true, align: 'right', defaultDir: 'desc' },
		{
			key: 'setup',
			header: 'Setup',
			sortable: true,
			sortValue: (t) => (isReady(t) ? 1 : 0)
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (t) => (t.enabled ? 1 : 0)
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let firmSort = $state<DataTableSort | null>(null);
</script>

<PageHeader
	eyebrow="Internal operations"
	title="Dashboard"
	description="Cross-tenant operations on the secure internal network."
>
	{#snippet actions()}
		<a href="/tenants" class={buttonVariants()}>
			View firms <ArrowRightIcon class="size-4" />
		</a>
	{/snippet}
</PageHeader>

{#if data.apiError}
	<LoadErrorBanner message={data.apiError} class="mb-6" onRetry={() => invalidateAll()} />
{/if}

<div class="space-y-6">
	<!-- The estate at a glance: the same hero band the platform portal uses, so the two staff
	     surfaces open with an identical anchor rather than admin starting on a bare table.
	     The hero carries the headline figure and onboarding readiness; the tiles below carry the
	     breakdown — deliberately no overlap, so no number is stated twice on one screen. -->
	<HeroPanel eyebrow="Firms under management" icon={BuildingIcon} value={String(tenants.length)}>
		{#snippet aside()}
			<div class="w-52">
				<div
					class="flex items-baseline justify-between text-[0.7rem] font-medium"
					style="color: var(--hero-muted)"
				>
					<span>Onboarding readiness</span>
					<span class="tabular-nums" style="color: var(--hero-foreground)">{readyPct}%</span>
				</div>
				<div
					class="mt-1.5 h-1.5 overflow-hidden rounded-full"
					style="background-color: color-mix(in oklch, var(--hero-foreground) 14%, transparent)"
					role="meter"
					aria-valuenow={readyPct}
					aria-valuemin={0}
					aria-valuemax={100}
					aria-label="Firms fully onboarded"
				>
					<div
						class="h-full rounded-full transition-all"
						style="width: {readyPct}%; background-color: var(--hero-accent)"
					></div>
				</div>
				<p class="mt-1.5 text-[0.7rem] tabular-nums" style="color: var(--hero-muted)">
					{readyCount} of {tenants.length} fully set up
				</p>
			</div>
		{/snippet}
	</HeroPanel>

	<!-- KPIs — linked, because there is a screen behind every one of these numbers. -->
	<div class="grid gap-4 sm:grid-cols-3">
		<StatTile
			href="/users"
			label="Logins"
			icon={UsersIcon}
			value={String(userTotal)}
			sub="across every firm"
		/>
		<StatTile
			href="/tenants"
			label="Active firms"
			icon={BuildingIcon}
			value={String(activeCount)}
			sub={activeCount === tenants.length ? 'all sign-in enabled' : 'some disabled'}
			tone={activeCount === tenants.length ? 'positive' : 'negative'}
		/>
		<StatTile
			href="/tenants"
			label="Awaiting setup"
			icon={WrenchIcon}
			value={String(needsSetup.length)}
			sub={needsSetup.length ? 'needs attention' : 'all firms ready'}
			tone={needsSetup.length ? 'negative' : 'positive'}
		/>
	</div>

	<!-- Needs setup — only when there's something to action -->
	{#if needsSetup.length}
		<section>
			<SectionHeading title="Needs setup" count={needsSetup.length} />
			<Card glass>
				<CardContent class="space-y-2">
					{#each needsSetup as t (t.id)}
						<a
							href="/tenants/{t.uid}"
							class="hover:bg-accent/60 focus-visible:ring-ring/50 flex items-center justify-between gap-3 rounded-lg border px-3 py-2 transition-colors focus-visible:outline-none focus-visible:ring-[3px]"
						>
							<div class="min-w-0">
								<div class="truncate font-medium">{t.name}</div>
								<div class="text-muted-foreground text-xs">{missingSteps(t).join(' · ')}</div>
							</div>
							<ArrowRightIcon class="text-muted-foreground size-4 shrink-0" />
						</a>
					{/each}
				</CardContent>
			</Card>
		</section>
	{/if}

	<!-- Firm roster -->
	<section>
		<SectionHeading title="Firms" count={tenants.length} />
		<Card glass>
			<CardContent class="space-y-3">
				{#if tenants.length === 0}
					<EmptyState
						compact
						icon={BuildingIcon}
						title="No firms yet"
						description="Create the first firm to get started."
					>
						{#snippet action()}
							<a href="/tenants" class={buttonVariants({ variant: 'secondary', size: 'sm' })}>
								<PlusIcon class="size-4" /> Create the first firm
							</a>
						{/snippet}
					</EmptyState>
				{:else}
					<ListingToolbar
						bind:query
						placeholder="Search firms by name or alias"
						{dirty}
						onclear={clearFilters}
						{filterDefs}
						{filterValue}
						onfilters={onFilters}
					/>
					<DataTable
						columns={firmColumns}
						rows={visibleTenants}
						bind:sort={firmSort}
						getKey={(t) => t.id}
						label="Firms"
						empty="No firms match. Widen the filters or clear the search."
					>
						{#snippet cell(t, column)}
							{#if column.key === 'name'}
								<a href="/tenants/{t.uid}" class="font-medium hover:underline">{t.name}</a>
								<RecordId value={t.alias} />
							{:else if column.key === 'user_count'}
								<span class="tabular-nums">{t.user_count}</span>
							{:else if column.key === 'setup'}
								<StatusBadge tone={isReady(t) ? 'success' : 'warning'}>
									{isReady(t) ? 'Ready' : 'Setup needed'}
								</StatusBadge>
							{:else if column.key === 'status'}
								<StatusBadge tone={t.enabled ? 'success' : 'danger'}>
									{t.enabled ? 'Active' : 'Disabled'}
								</StatusBadge>
							{/if}
						{/snippet}
					</DataTable>
				{/if}
			</CardContent>
		</Card>
	</section>
</div>
