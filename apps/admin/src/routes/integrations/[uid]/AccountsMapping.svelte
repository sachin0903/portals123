<script lang="ts">
	// The "Accounts" tab: one row per account of a client that is already mapped to an Intelliflo
	// client, with the plan it is linked to (or a dropdown of that client's plans to link it to).
	// Accounts of unmapped clients are not listed (an Intelliflo plan only exists under an Intelliflo
	// client, and an account cannot be synced without its client mapped); they are counted, with a
	// pointer to the Clients tab. The search and the "unlinked only" filter are in the page's tab
	// header and arrive as props; both are applied by the API, because the listing is paginated.
	import { page } from '$app/state';
	import { Pagination } from '@graphene/ui';
	import type { AccountsTablePage, IoPlanSearchResult } from '$lib/api';
	import AccountsMappingRow from './AccountsMappingRow.svelte';
	import { responseMessage } from './mapping-errors';

	let {
		canWrite,
		search = '',
		unlinkedOnly = false
	}: { canWrite: boolean; search?: string; unlinkedOnly?: boolean } = $props();

	const base = $derived(`/integrations/${page.params.uid}`);
	const PAGE_SIZE = 10;

	// One term and one filter per page number (see ClientsMapping.svelte for why).
	const filterKey = $derived(`${search}\u0000${unlinkedOnly}`);
	let pageState = $state({ key: '', page: 1 });
	const currentPage = $derived(pageState.key === filterKey ? pageState.page : 1);
	function setPage(next: number) {
		pageState = { key: filterKey, page: next };
	}

	let items = $state<AccountsTablePage['items']>([]);
	let total = $state(0);
	let awaitingClient = $state(0);
	let loading = $state(true);
	let loadError = $state('');
	const pageCount = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	// The plans of one Intelliflo client, fetched once and shared by all of that client's rows: a
	// client with three accounts on a page costs one Intelliflo call, not three.
	const plansByClient = new Map<string, Promise<IoPlanSearchResult[]>>();
	function loadPlans(clientIdIo: string): Promise<IoPlanSearchResult[]> {
		let pending = plansByClient.get(clientIdIo);
		if (!pending) {
			pending = fetch(
				`${base}/io-accounts-for-client?${new URLSearchParams({ client_id_io: clientIdIo })}`
			).then(async (res) => {
				if (!res.ok) {
					throw new Error(
						await responseMessage(res, 'Could not load this Intelliflo client’s plans')
					);
				}
				return (await res.json()) as IoPlanSearchResult[];
			});
			plansByClient.set(clientIdIo, pending);
			// A failed lookup must not be remembered: the next row (or a retry) asks again.
			pending.catch(() => plansByClient.delete(clientIdIo));
		}
		return pending;
	}
	function forgetPlans(clientIdIo: string) {
		plansByClient.delete(clientIdIo);
	}

	let seq = 0;
	$effect(() => {
		const p = currentPage;
		const term = search;
		const only = unlinkedOnly;
		const mine = ++seq;
		loading = true;
		loadError = '';
		const params = new URLSearchParams({ page: String(p), page_size: String(PAGE_SIZE) });
		if (term) params.set('search', term);
		if (only) params.set('unlinked_only', 'true');
		fetch(`${base}/accounts-table?${params}`)
			.then(async (res) => {
				if (mine !== seq) return;
				if (!res.ok) throw new Error(await responseMessage(res, 'Could not load accounts'));
				const data = (await res.json()) as AccountsTablePage;
				items = data.items;
				total = data.total;
				awaitingClient = data.accounts_awaiting_client;
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
	{#if awaitingClient > 0}
		<p class="text-muted-foreground text-sm">
			{awaitingClient}
			{awaitingClient === 1 ? 'account is' : 'accounts are'} waiting for their client to be mapped to
			Intelliflo on the Clients tab, so {awaitingClient === 1 ? 'it is' : 'they are'} not listed here.
		</p>
	{/if}

	<div class="hidden gap-2 lg:flex">
		<span class="min-w-0 flex-1 text-sm font-medium">Client</span>
		<span class="min-w-0 flex-1 text-sm font-medium">Account</span>
		<span class="min-w-0 flex-1 text-sm font-medium">IO plan</span>
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
					No accounts match “{search}”.
				{:else if unlinkedOnly}
					Every listed account is linked.
				{:else}
					No accounts to map yet. Map a client to its Intelliflo client on the Clients tab first.
				{/if}
			</p>
		{/if}

		{#each items as row (row.account_uid)}
			<AccountsMappingRow {row} {canWrite} {loadPlans} {forgetPlans} />
		{/each}

		{#if items.length > 0}
			<Pagination bind:page={() => currentPage, setPage} {pageCount} />
		{/if}
	{/if}
</div>
