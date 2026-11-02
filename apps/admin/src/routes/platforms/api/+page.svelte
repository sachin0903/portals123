<script lang="ts">
	// Admin → Platforms → API. Every outgoing call to a custodian, from `audit.api_log`.
	//
	// Cross-provider rather than a tab under one platform: the question this page answers is
	// usually "what did we send anyone about this client", and an incident rarely respects the
	// boundary. The provider filter narrows it when you do know.
	//
	// Read-only by construction — the table grants SELECT and INSERT only, so there is no action
	// on a row beyond opening it.
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CELL_RAW,
		DataTable,
		LoadErrorBanner,
		PageHeader,
		sentenceCase,
		Sheet,
		TruncatedText,
		type DataTableColumn
	} from '@graphene/ui';
	import {
		ListingToolbar,
		PaginationStrip,
		rangeLabelFromOffset,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { ApiCall } from './+page.server';

	let { data } = $props();

	// The box is local state (it must accept typing before the URL changes), but the URL is the
	// truth. Without the sync, going Back leaves the box showing a query that is no longer
	// applied — the results change and the box does not.
	let query = $state('');
	let appliedQuery = $state('');
	$effect(() => {
		if (data.filters.q !== appliedQuery) {
			appliedQuery = data.filters.q;
			query = data.filters.q;
		}
	});
	// No client-side sort: `rows` is ONE PAGE of a server-paginated set, so sorting it here
	// would order the fifty on screen and present that as the order of the whole log. The API
	// returns newest-first, which is the only order this page claims.
	let selected = $state<(ApiCall & { request?: unknown; response?: unknown }) | null>(null);
	let detailOpen = $state(false);
	let detailError = $state<string | null>(null);

	// Providers come from the summary rather than a hard-coded list: a fourth custodian should
	// appear here the first time it is called, not when someone remembers to edit this file.
	const providerNames = $derived(
		new Map([...data.summary, ...data.calls].map((row) => [row.provider, row.provider_name]))
	);
	const providerName = (slug: string): string => providerNames.get(slug) ?? sentenceCase(slug);
	const providers = $derived([...new Set(data.summary.map((s) => s.provider))].sort());
	const totals = $derived({
		calls: data.summary.reduce((n, s) => n + s.calls, 0),
		failures: data.summary.reduce((n, s) => n + s.failures, 0)
	});

	/** Drives the toolbar's Clear button — true when anything is off its default. */
	const hasFilters = $derived(
		Boolean(data.filters.q) ||
			data.filters.status !== 'all' ||
			Boolean(data.filters.source) ||
			data.filters.provider.length > 0
	);

	const STATUSES = [
		{ value: 'all', label: 'All' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'ok', label: 'Succeeded' }
	] as const;

	// The first question anyone asks of this log: was that us or the cron? A nightly sync is
	// thousands of calls and an operator's action is a handful, so without this the app's traffic
	// is buried under the sync's on every page.
	const SOURCES = [
		{ value: 'all', label: 'All sources' },
		{ value: 'app', label: 'App' },
		{ value: 'sync', label: 'Sync' }
	] as const;

	const SOURCE_LABEL: Record<string, string> = { app: 'App', sync: 'Sync' };

	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'status',
			label: 'Status',
			options: STATUSES.filter((s) => s.value !== 'all').map((s) => ({ ...s })),
			single: true,
			allLabel: 'All'
		},
		{
			key: 'source',
			label: 'Source',
			options: SOURCES.filter((s) => s.value !== 'all').map((s) => ({ ...s })),
			single: true,
			allLabel: 'All sources'
		},
		...(providers.length > 1
			? [
					{
						key: 'provider',
						label: 'Provider',
						options: providers.map((p) => ({ value: p, label: providerName(p) })),
						allLabel: 'All providers'
					} satisfies FilterDef
				]
			: [])
	]);
	const filterValue = $derived<Record<string, string[]>>({
		...(data.filters.status !== 'all' ? { status: [data.filters.status] } : {}),
		...(data.filters.source ? { source: [data.filters.source] } : {}),
		...(data.filters.provider.length ? { provider: data.filters.provider } : {})
	});
	function applyFilters(next: Record<string, string[]>) {
		setFilter({
			status: next.status?.[0] ?? null,
			source: next.source?.[0] ?? null,
			provider: next.provider ?? []
		});
	}

	/** Filters live in the URL so a diagnosis can be sent as a link. A list value becomes one
	 *  repeated param per entry; empty clears the key. */
	function setFilter(patch: Record<string, string | number | string[] | null>) {
		const params = new URLSearchParams(page.url.searchParams);
		for (const [key, value] of Object.entries(patch)) {
			if (Array.isArray(value)) {
				params.delete(key);
				for (const one of value) if (one) params.append(key, one);
			} else if (value === null || value === '' || value === 'all') {
				params.delete(key);
			} else {
				params.set(key, String(value));
			}
		}
		// Any filter change invalidates the page number — page 3 of a different result set is
		// not where the user was.
		if (!('page' in patch)) params.delete('page');
		void goto(`?${params}`, { keepFocus: true, noScroll: true });
	}

	async function openCall(call: ApiCall) {
		selected = call;
		detailOpen = true;
		detailError = null;
		// Bodies are fetched on demand: the list omits them because one CTC1 request is a whole
		// XML document and fifty of them would make this table megabytes.
		const res = await fetch(`/platforms/api/${call.uid}`);
		if (!res.ok) {
			detailError = 'Could not load the request and response bodies.';
			return;
		}
		selected = await res.json();
	}

	const pageCount = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	function pageHref(pageNumber: number): string {
		const params = new URLSearchParams(page.url.searchParams);
		if (pageNumber > 1) params.set('page', String(pageNumber));
		else params.delete('page');
		const qs = params.toString();
		return qs ? `?${qs}` : page.url.pathname;
	}

	const time = new Intl.DateTimeFormat('en-GB', {
		day: '2-digit',
		month: 'short',
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit'
	});
	const asTime = (iso: string | null) => (iso ? time.format(new Date(iso)) : '—');
	const asMillis = (ms: number | null) => (ms === null ? '—' : `${ms.toLocaleString()} ms`);
	const pretty = (value: unknown) =>
		value === null || value === undefined ? '—' : JSON.stringify(value, null, 2);

	const columns: DataTableColumn<ApiCall>[] = [
		{ key: 'created', header: 'When', headerClass: 'w-[14%]' },
		{ key: 'source', header: 'Source', headerClass: 'w-[12%]' },
		{ key: 'provider', header: 'Provider', headerClass: 'w-[14%]' },
		{ key: 'operation', header: 'Operation', headerClass: 'w-[20%]' },
		{ key: 'status', header: 'Status', headerClass: 'w-[12%]' },
		{ key: 'duration', header: 'Took', align: 'right', hideBelow: 'md', headerClass: 'w-[12%]' },
		{ key: 'tenant', header: 'Tenant', hideBelow: 'lg', headerClass: 'w-[16%]' }
	];
</script>

<svelte:head><title>API log · Platforms</title></svelte:head>

<div class="space-y-5">
	<PageHeader
		eyebrow="Platforms"
		title="API log"
		description="Every outgoing call to a custodian, as sent and as answered. Append-only."
	/>

	{#if data.loadError}
		<LoadErrorBanner
			message="The API log could not be read ({data.loadError})."
			onRetry={() => invalidateAll()}
		/>
	{/if}

	<!-- 24-hour health, per provider. p95 rather than a mean: one 30-second timeout disappears
	     into a mean of hundreds, which is the case you are looking for. -->
	{#if data.summary.length}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
			<!-- One card per provider AND source: pooling them makes both figures meaningless,
			     because the sync's volume buries the app's while the app's failures vanish into
			     the sync's p95. The key carries source for the same reason — without it the two
			     rows collide and Svelte keys one card to two datasets. -->
			{#each data.summary as row (row.provider + (row.service ?? '') + row.source)}
				<Card>
					<CardContent>
						<p class="text-muted-foreground text-xs font-medium">
							{providerName(row.provider)}{row.service ? ` · ${row.service}` : ''}
							<span class="text-muted-foreground/70 normal-case">
								· {SOURCE_LABEL[row.source] ?? row.source}
							</span>
						</p>
						<p class="mt-1 text-2xl font-semibold tabular-nums">
							{row.calls.toLocaleString()}
						</p>
						<p class="text-muted-foreground text-xs">
							last 24h ·
							{#if row.failures}
								<span class="text-destructive font-medium">{row.failures} failed</span>
							{:else}
								no failures
							{/if}
							{#if row.p95_ms !== null}· p95 {asMillis(row.p95_ms)}{/if}
						</p>
					</CardContent>
				</Card>
			{/each}
		</div>
	{/if}

	<!-- The shared listing toolbar, in its server-driven mode: this log is paginated by the API, so
	     a client-side filter over one page would answer "no such call" when it means "not on this
	     page". `onsearch` debounces the typing and `setFilter` turns it into a navigation. -->
	<ListingToolbar
		bind:query
		placeholder="Search URL, operation or error"
		dirty={hasFilters}
		onsearch={(term) => setFilter({ q: term })}
		onclear={() => setFilter({ q: null, status: null, source: null, provider: null })}
		{filterDefs}
		{filterValue}
		onfilters={applyFilters}
	>
		{#snippet filters()}
			{#if totals.failures > 0 && data.filters.status !== 'failed'}
				<Button variant="ghost" size="sm" onclick={() => setFilter({ status: 'failed' })}>
					{totals.failures} failed in 24h
				</Button>
			{/if}
		{/snippet}
	</ListingToolbar>

	<Card>
		<CardContent class="p-0">
			<DataTable
				rows={data.calls}
				{columns}
				label="API calls"
				sortDisabled
				fixed
				getKey={(row) => String(row.id)}
				onRowClick={(row) => openCall(row)}
				empty="No API calls match these filters."
			>
				{#snippet cell(row, column)}
					{#if column.key === 'created'}
						<span class="tabular-nums">{asTime(row.created_at)}</span>
					{:else if column.key === 'source'}
						<!-- Neutral in both states. A sync call is not a lesser call, and tinting one
						     of them would read as a status when it is only an origin. -->
						<Badge variant="ghost" class="font-mono text-[11px]">
							{SOURCE_LABEL[row.source] ?? row.source}
						</Badge>
					{:else if column.key === 'provider'}
						<span class="font-medium">{providerName(row.provider)}</span>
						{#if row.service}
							<span class="text-muted-foreground"> · {row.service}</span>
						{/if}
					{:else if column.key === 'operation'}
						<!-- Falls back to the raw URL, which has no length bound whatsoever — and this is the
						     widest ceiling for exactly that reason: these are read rather than scanned. -->
						<TruncatedText
							text={`${row.method ?? ''} ${row.operation ?? row.url}`}
							class="{CELL_RAW} font-mono text-xs"
						/>
					{:else if column.key === 'status'}
						<!-- The badge follows `ok`, not the status code: Morgan Lloyd answers 200 with
						     responseKey=1 on failure and CTC answers 200 with an HTML error page, so the
						     code alone would call both a success. -->
						<Badge variant={row.ok ? 'outline' : 'destructive'}>
							{row.ok ? 'OK' : 'Failed'}
						</Badge>
						{#if row.status_code}
							<span class="text-muted-foreground ml-1 tabular-nums">{row.status_code}</span>
						{:else}
							<span class="text-muted-foreground ml-1">no response</span>
						{/if}
					{:else if column.key === 'duration'}
						<span class="tabular-nums">{asMillis(row.duration_ms)}</span>
					{:else if column.key === 'tenant'}
						<span class="text-muted-foreground">{row.tenant_id ?? '—'}</span>
					{/if}
				{/snippet}
			</DataTable>
		</CardContent>
	</Card>

	{#if data.total > 0}
		<PaginationStrip
			label={rangeLabelFromOffset(
				(data.filters.page - 1) * data.pageSize,
				data.calls.length,
				data.total
			)}
			page={data.filters.page}
			{pageCount}
			href={pageHref}
			class="mx-0"
		/>
	{/if}
</div>

<Sheet.Root bind:open={detailOpen}>
	<Sheet.Content class="flex w-full flex-col gap-0 overflow-hidden p-0">
		<Sheet.Header class="shrink-0 border-b px-6 pt-6 pb-4">
			<Sheet.Title>
				{selected?.method}
				{selected?.operation ?? ''}
			</Sheet.Title>
			<Sheet.Description class="font-mono text-xs break-all">{selected?.url}</Sheet.Description>
		</Sheet.Header>

		{#if selected}
			<div class="min-h-0 flex-1 space-y-4 overflow-x-hidden overflow-y-auto px-6 py-5">
				<dl class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
					{#each [['Provider', providerName(selected.provider) + (selected.service ? ` · ${selected.service}` : '')], ['Source', SOURCE_LABEL[selected.source] ?? selected.source], ['Outcome', selected.ok ? 'OK' : 'Failed'], ['Status', selected.status_code ?? 'no response'], ['Took', asMillis(selected.duration_ms)], ['When', asTime(selected.created_at)], ['Tenant', selected.tenant_id ?? '—'], ['Transport', selected.transport], ['Entity', selected.entity_type ? `${selected.entity_type} ${selected.entity_id ?? ''}` : '—']] as [label, value] (label)}
						<div>
							<dt class="text-muted-foreground text-xs">{label}</dt>
							<dd class="tabular-nums">{value}</dd>
						</div>
					{/each}
				</dl>

				{#if selected.error}
					<div class="border-destructive/40 bg-destructive/5 rounded-md border p-3">
						<p class="text-destructive text-xs font-medium">Error</p>
						<p class="text-destructive mt-1 font-mono text-xs break-all">{selected.error}</p>
					</div>
				{/if}

				{#if detailError}
					<p class="text-destructive text-sm">{detailError}</p>
				{:else}
					<div>
						<p class="mb-1 text-sm font-medium">Request</p>
						<pre
							class="bg-muted/40 max-h-80 overflow-auto rounded-md p-3 font-mono text-xs">{pretty(
								selected.request
							)}</pre>
					</div>
					<div>
						<p class="mb-1 text-sm font-medium">Response</p>
						{#if selected.response === null && selected.ok}
							<!-- Says WHY it is absent. A bare "—" reads as "the vendor answered with
							     nothing", which is a different and far more alarming fact. -->
							<p class="text-muted-foreground bg-muted/40 rounded-md p-3 text-xs">
								Not retained. Only failed responses and identifiers needed for follow-up work are
								stored.
							</p>
						{:else}
							<pre
								class="bg-muted/40 max-h-80 overflow-auto rounded-md p-3 font-mono text-xs">{pretty(
									selected.response
								)}</pre>
						{/if}
					</div>
				{/if}

				<p class="text-muted-foreground text-xs">
					Credentials are stripped before a call is recorded, so an <code>Authorization</code>
					header or a secret in a body reads as <code>[redacted]</code>.
				</p>
			</div>
		{/if}

		<div class="flex shrink-0 justify-end gap-2 border-t px-6 py-4">
			<Button variant="ghost" onclick={() => (detailOpen = false)}>Close</Button>
		</div>
	</Sheet.Content>
</Sheet.Root>
