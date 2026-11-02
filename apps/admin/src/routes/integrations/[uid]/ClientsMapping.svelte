<script lang="ts">
	// The "Clients" tab: a paginated listing of the integration's firm's clients, each with the
	// Intelliflo client it is mapped to (if any). One row per client; the client's ACCOUNTS are on
	// the Accounts tab. The box that filters it is in the page's tab header and arrives as `search`:
	// the filtering is server-side, because the listing is paginated and filtering the page the
	// browser holds would say "no client called Halloway" when it means "not on this page".
	import { page } from '$app/state';
	import { Pagination } from '@graphene/ui';
	import type { ClientsTablePage } from '$lib/api';
	import ClientsMappingRow from './ClientsMappingRow.svelte';
	import { responseMessage } from './mapping-errors';

	// `hasFirm`: whether the integration is assigned a firm. With none the listing is empty by
	// construction; with one, an empty listing means that firm has no clients, which is a different
	// thing to tell the operator.
	let {
		canWrite,
		search = '',
		hasFirm
	}: { canWrite: boolean; search?: string; hasFirm: boolean } = $props();

	const base = $derived(`/integrations/${page.params.uid}`);
	const PAGE_SIZE = 10;

	// The page number belongs to ONE term: page 3 of one result set may not exist in the next.
	// Keyed by term, so a new search reads as page 1 in the same render the fetch below sees it.
	let pageState = $state({ term: '', page: 1 });
	const currentPage = $derived(pageState.term === search ? pageState.page : 1);
	function setPage(next: number) {
		pageState = { term: search, page: next };
	}

	let items = $state<ClientsTablePage['items']>([]);
	let total = $state(0);
	let loading = $state(true);
	let loadError = $state('');
	const pageCount = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	let seq = 0;
	$effect(() => {
		const p = currentPage;
		const term = search;
		const mine = ++seq;
		loading = true;
		loadError = '';
		const params = new URLSearchParams({ page: String(p), page_size: String(PAGE_SIZE) });
		if (term) params.set('search', term);
		fetch(`${base}/clients-table?${params}`)
			.then(async (res) => {
				if (mine !== seq) return;
				if (!res.ok) throw new Error(await responseMessage(res, 'Could not load clients'));
				const data = (await res.json()) as ClientsTablePage;
				items = data.items;
				total = data.total;
			})
			.catch((err: unknown) => {
				if (mine !== seq) return;
				loadError = err instanceof Error ? err.message : 'Something went wrong.';
				items = [];
			})
			.finally(() => {
				if (mine === seq) loading = false;
			});
	});
</script>

<div class="space-y-3">
	<div class="hidden gap-2 lg:flex">
		<span class="min-w-0 flex-1 text-sm font-medium">Client</span>
		<span class="min-w-0 flex-1 text-sm font-medium">IO Client</span>
		{#if canWrite}<span class="w-20 shrink-0"></span>{/if}
	</div>

	{#if loading}
		<p class="text-muted-foreground text-sm">Loading…</p>
	{:else}
		{#if loadError}
			<p class="text-destructive text-sm">{loadError}</p>
		{:else if items.length === 0}
			<p class="text-muted-foreground text-sm">
				{#if search}
					No clients match “{search}”.
				{:else if hasFirm}
					This integration's firm has no clients yet.
				{:else}
					No clients found. Assign a firm to this integration to see its clients here.
				{/if}
			</p>
		{/if}

		{#each items as row (row.contact_uid)}
			<ClientsMappingRow {row} {canWrite} />
		{/each}

		{#if items.length > 0}
			<Pagination bind:page={() => currentPage, setPage} {pageCount} />
		{/if}
	{/if}
</div>
