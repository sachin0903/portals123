<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		DataTable,
		type DataTableColumn,
		EmptyState,
		PageHeader,
		Label,
		Pagination,
		StatusBadge,
		Switch,
		Tabs,
		sentenceCase
	} from '@graphene/ui';
	import { ListingToolbar } from '@graphene/ui/book/listing';
	import ClockIcon from '@lucide/svelte/icons/history';
	import type { IntegrationStatus, JobRun } from '$lib/api';
	import type { PageData } from './$types';
	import AccountsMapping from './AccountsMapping.svelte';
	import ClientsMapping from './ClientsMapping.svelte';
	import ExpandableMessage from './ExpandableMessage.svelte';
	import IntegrationEditSheet from '../IntegrationEditSheet.svelte';

	let { data }: { data: PageData } = $props();

	const STATUS_TONE: Record<IntegrationStatus, 'success' | 'warning' | 'danger'> = {
		approved: 'success',
		pending: 'warning',
		rejected: 'danger'
	};

	// --- Edit ---------------------------------------------------------------------------------

	let editOpen = $state(false);

	// --- Summary -------------------------------------------------------------------------------

	// `code` is the each-block's key (not the label): two features can share a label, and `code`
	// is the one thing on this row guaranteed unique and non-empty.
	const featureLabels = $derived(
		data.integration.feature_ids.map((code) => {
			const description = data.features.find((f) => f.code === code)?.description;
			return { code, label: description || code };
		})
	);
	// From the detail response, not `data.runs[0]` -- that's only the first row of whichever page/
	// search the Job Runs tab currently shows, not necessarily this integration's actual latest run.
	const lastRun = $derived(data.lastRun);

	// --- Tabs -----------------------------------------------------------------------------------

	// Controlled, because each tab's search lives in the tab HEADER (beside the tab buttons, not
	// inside the tab's content) and only makes sense while that tab is the one showing.
	let tab = $state('runs');

	// --- Clients and Accounts tabs: their searches ---------------------------------------------

	// Held here rather than in the tabs' own components: each box is in this page's header row, and
	// the listing it filters is a child. `*Query` is what the box shows (every keystroke); `*Search`
	// is the debounced, trimmed term the listing is actually filtered by.
	let clientsQuery = $state('');
	let clientsSearch = $state('');
	let accountsQuery = $state('');
	let accountsSearch = $state('');
	/** Hide accounts that already have a plan, to see what is left to map. */
	let unlinkedOnly = $state(false);

	function onClientsSearch(term: string) {
		clientsSearch = term.trim();
	}
	function onAccountsSearch(term: string) {
		accountsSearch = term.trim();
	}

	// --- Job Runs tab ---------------------------------------------------------------------------

	const runColumns: DataTableColumn<JobRun>[] = [
		{ key: 'feature', header: 'Feature', headerClass: 'w-[14%]' },
		{ key: 'run_date', header: 'Run date', headerClass: 'w-[15%]' },
		{ key: 'data_date', header: 'Data date', headerClass: 'w-[10%]' },
		{ key: 'status', header: 'Status', headerClass: 'w-[9%]' },
		{ key: 'records', header: 'Records', align: 'right', headerClass: 'w-[12%]' },
		{ key: 'error_message', header: 'Message', headerClass: 'w-[40%]' }
	];
	const runsPageCount = $derived(Math.max(1, Math.ceil(data.runsTotal / 20)));
	// Seeded once, then owned by the input — see the same pattern on `routes/tenants/+page.svelte`.
	// svelte-ignore state_referenced_locally
	let runsQuery = $state(data.runsQ);

	function onRunsSearch(term: string) {
		const url = new URL(page.url);
		if (term.trim()) url.searchParams.set('runsQ', term.trim());
		else url.searchParams.delete('runsQ');
		url.searchParams.delete('runsPage');
		goto(url, { keepFocus: true, noScroll: true, replaceState: true });
	}
	function onRunsPage(next: number) {
		if (next === data.runsPage) return;
		const url = new URL(page.url);
		url.searchParams.set('runsPage', String(next));
		goto(url, { noScroll: true });
	}

	function when(value: string | null): string {
		if (!value) return '—';
		try {
			return new Date(value).toLocaleString();
		} catch {
			return value;
		}
	}
</script>

<svelte:head
	><title>{data.integration.platform ?? 'Integration'} · Internal operations</title></svelte:head
>

<PageHeader
	eyebrow={data.integration.tenant_name}
	title={data.integration.platform ?? 'Integration'}
	back={{ href: '/integrations', label: 'Integrations' }}
>
	{#snippet actions()}
		<StatusBadge tone={STATUS_TONE[data.integration.status]}>
			{sentenceCase(data.integration.status)}
		</StatusBadge>
		{#if data.canWrite}
			<Button size="sm" variant="outline" onclick={() => (editOpen = true)}>Edit</Button>
		{/if}
	{/snippet}
</PageHeader>

<div class="space-y-5">
	<Card glass class="gap-2">
		<CardHeader><CardTitle>Summary</CardTitle></CardHeader>
		<CardContent class="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
			<div>
				<p class="text-muted-foreground text-xs">Firm</p>
				<p>{data.integration.firm_description ?? '—'}</p>
			</div>
			<div>
				<p class="text-muted-foreground text-xs">Features</p>
				{#if featureLabels.length === 0}
					<p>—</p>
				{:else}
					<div class="mt-1 flex flex-wrap gap-1">
						{#each featureLabels as feature (feature.code)}
							<Badge variant="secondary">{feature.label}</Badge>
						{/each}
					</div>
				{/if}
			</div>
			<div>
				<p class="text-muted-foreground text-xs">Last run</p>
				{#if lastRun}
					<p>
						{when(lastRun.run_date)}
						<StatusBadge tone={lastRun.status === 'success' ? 'success' : 'danger'} class="ml-1">
							{sentenceCase(lastRun.status)}
						</StatusBadge>
					</p>
				{:else}
					<p>—</p>
				{/if}
			</div>
		</CardContent>
	</Card>

	<Tabs.Root bind:value={tab}>
		<!-- The tab buttons and the search of whichever tab is showing: one row, search at the right.
		     It wraps under the tabs on a narrow screen rather than squeezing them. -->
		<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
			<!-- The shared trigger has only `px-1.5` and the list no gap, which sits three tabs tight
			     against each other: more padding and a gap, here, rather than changing every page. -->
			<Tabs.List class="gap-1">
				<Tabs.Trigger value="runs" class="px-4">Job Runs</Tabs.Trigger>
				<Tabs.Trigger value="clients" class="px-4">Clients</Tabs.Trigger>
				<Tabs.Trigger value="accounts" class="px-4">Accounts</Tabs.Trigger>
			</Tabs.List>
			{#if tab === 'runs'}
				<ListingToolbar
					bind:query={runsQuery}
					placeholder="Search feature or error message…"
					dirty={runsQuery.trim() !== ''}
					onsearch={onRunsSearch}
					onclear={() => onRunsSearch('')}
					class="w-full sm:w-auto"
				/>
			{:else if tab === 'clients'}
				<ListingToolbar
					bind:query={clientsQuery}
					placeholder="Search client name…"
					onsearch={onClientsSearch}
					class="w-full sm:w-auto"
				/>
			{:else if tab === 'accounts'}
				<div class="flex w-full flex-wrap items-center gap-x-4 gap-y-2 sm:w-auto">
					<div class="flex items-center gap-2">
						<Switch id="unlinked-only" bind:checked={unlinkedOnly} />
						<Label for="unlinked-only" class="font-normal">Unlinked only</Label>
					</div>
					<ListingToolbar
						bind:query={accountsQuery}
						placeholder="Search client, account name or number…"
						onsearch={onAccountsSearch}
						class="w-full sm:w-auto"
					/>
				</div>
			{/if}
		</div>

		<Tabs.Content value="runs" class="space-y-4 pt-4">
			{#if data.runsError}
				<Alert variant="destructive"><AlertDescription>{data.runsError}</AlertDescription></Alert>
			{/if}
			{#if data.runsError}
				<!-- The Alert above already says why nothing is showing -- a load failure is not
				     "no job runs", and the table seeds `runs` to [] to render that failure, not to
				     report an integration that has never run. -->
			{:else if data.runs.length === 0}
				<EmptyState
					compact
					icon={ClockIcon}
					title={data.runsQ.trim() ? 'No job runs match that search' : 'No job runs'}
					description={data.runsQ.trim()
						? 'Try part of a feature name or error message, or clear the search to see every run.'
						: "This integration's feature syncs have not run yet."}
				/>
			{:else}
				<Card glass>
					<CardContent class="p-0">
						<DataTable
							columns={runColumns}
							rows={data.runs}
							getKey={(r) => r.uid}
							label="Job runs"
							sortDisabled
							fixed
							empty="No job runs match."
						>
							{#snippet cell(row, column)}
								{#if column.key === 'feature'}
									<span class="font-medium">{row.feature}</span>
								{:else if column.key === 'run_date'}
									<span class="whitespace-nowrap">{when(row.run_date)}</span>
								{:else if column.key === 'data_date'}
									<span class="text-muted-foreground whitespace-nowrap">
										{row.data_date ?? '—'}
									</span>
								{:else if column.key === 'status'}
									<StatusBadge tone={row.status === 'success' ? 'success' : 'danger'}>
										{sentenceCase(row.status)}
									</StatusBadge>
								{:else if column.key === 'records'}
									<span class="tabular-nums">
										{row.newly_added ?? 0}/{row.record_count ?? 0}
										{#if row.failed}
											<span class="text-destructive"> ({row.failed} failed)</span>
										{/if}
									</span>
								{:else if column.key === 'error_message'}
									<ExpandableMessage text={row.error_message} />
								{/if}
							{/snippet}
						</DataTable>
					</CardContent>
				</Card>
				<Pagination bind:page={() => data.runsPage, onRunsPage} pageCount={runsPageCount} />
			{/if}
		</Tabs.Content>

		<Tabs.Content value="clients" class="space-y-4 pt-4">
			<Card glass>
				<CardContent>
					<!-- Only mounted while the tab is showing, like the Accounts tab below: bits-ui keeps a
					     tab's content mounted while hidden, and a client row reads its linked-account count
					     once, so a Clients tab that outlived a link or unlink on the Accounts tab would offer
					     a Link the API refuses (409) or keep saying "unlink the accounts first". Opening the
					     tab loads the clients afresh. -->
					{#if tab === 'clients'}
						<ClientsMapping
							canWrite={data.canWrite}
							search={clientsSearch}
							hasFirm={data.integration.firm_id !== null}
						/>
					{/if}
				</CardContent>
			</Card>
		</Tabs.Content>

		<Tabs.Content value="accounts" class="space-y-4 pt-4">
			<Card glass>
				<CardContent>
					<!-- Only mounted while the tab is showing, so opening it loads the accounts afresh:
					     a client mapped on the Clients tab shows its accounts without a page refresh. -->
					{#if tab === 'accounts'}
						<AccountsMapping canWrite={data.canWrite} search={accountsSearch} {unlinkedOnly} />
					{/if}
				</CardContent>
			</Card>
		</Tabs.Content>
	</Tabs.Root>
</div>

<IntegrationEditSheet
	bind:open={editOpen}
	integration={data.integration}
	firms={data.firms}
	features={data.features}
	canWrite={data.canWrite}
	formAction="?/updateIntegration"
	onSaved={() => void invalidateAll()}
/>
