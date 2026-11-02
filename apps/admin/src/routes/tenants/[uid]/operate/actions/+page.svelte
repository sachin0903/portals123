<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ActionsEditor,
		LoadErrorBanner,
		PageHeader,
		RunsListing,
		type RunLinks
	} from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string; action_key?: string } | null } =
		$props();

	const TABS = [
		{ value: 'runs', label: 'Runs' },
		{ value: 'approvals', label: 'Approvals' },
		{ value: 'policies', label: 'Policies' }
	];

	const base = $derived(page.url.pathname);
	const operate = $derived(`/tenants/${page.params.uid}/operate`);

	function switchTab(tab: string): void {
		const next = new URL(page.url);
		next.search = '';
		if (tab !== 'runs') next.searchParams.set('tab', tab);
		goto(`${next.pathname}${next.search}`, { keepFocus: true, noScroll: true });
	}

	function navigate(next: ListingQuery): void {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const href = (n: number) => (data.query ? pageHref(data.query, n, page.url.searchParams) : '?');

	function select(uid: string | null): void {
		const next = new URL(page.url);
		if (uid) next.searchParams.set('run', uid);
		else next.searchParams.delete('run');
		void goto(`${next.pathname}${next.search}`, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	/** This portal's own routes for the records a run names, addressed by uid. */
	const links = $derived<RunLinks>({
		run: (uid) => `${base}/runs/${uid}`,
		client: (uid) => `${operate}/clients/${uid}`,
		account: (uid) => `${operate}/accounts/${uid}`,
		policy: (key) => `${base}?tab=policies&action=${encodeURIComponent(key)}`
	});
	const runActions = {
		retry: '?/retry',
		retryStep: '?/retry',
		cancel: '?/cancel',
		decide: '?/decide',
		remind: '?/remind'
	};

	function selectAction(key: string): void {
		const next = new URL(page.url);
		next.searchParams.set('tab', 'policies');
		next.searchParams.set('action', key);
		goto(`${next.pathname}${next.search}`, { keepFocus: true, noScroll: true, replaceState: true });
	}
</script>

<svelte:head><title>Actions · {data.tenant.name}</title></svelte:head>

<PageHeader
	title="Actions"
	description="Every provider instruction this firm runs, as durable runs. Who may run an action, and who must approve it, sit on the action; the provider comes from the account."
/>

<nav class="border-border mt-2 mb-5 flex gap-6 border-b" aria-label="Actions view">
	{#each TABS as tab (tab.value)}
		<button
			type="button"
			aria-current={data.tab === tab.value ? 'page' : undefined}
			onclick={() => switchTab(tab.value)}
			class="-mb-px cursor-pointer border-b-2 px-0.5 pb-2.5 text-xs font-bold tracking-wide uppercase transition-colors {data.tab ===
			tab.value
				? 'border-primary text-foreground'
				: 'text-muted-foreground hover:text-foreground hover:border-border border-transparent'}"
		>
			{tab.label}
		</button>
	{/each}
</nav>

{#if data.loadError}
	<LoadErrorBanner message="Couldn’t load actions." onRetry={() => invalidateAll()} />
{:else if (data.tab === 'runs' || data.tab === 'approvals') && data.page && data.query}
	<!-- Approvals is the same workbench pinned to the gate, with the gate's own counters. -->
	<RunsListing
		page={data.page}
		query={data.query}
		onquery={navigate}
		pageHref={href}
		{links}
		selected={data.selectedRun ?? null}
		onselect={select}
		canManage={data.canManage}
		{enhance}
		actions={runActions}
		onrefresh={() => invalidateAll()}
		variant={data.tab === 'approvals' ? 'approvals' : 'runs'}
	/>
{:else}
	<ActionsEditor
		categories={data.categories ?? []}
		actions={data.actions ?? []}
		roles={data.roles ?? []}
		selected={data.selected ?? null}
		onSelect={selectAction}
		canManage={data.canManage}
		{enhance}
		{form}
	/>
{/if}
