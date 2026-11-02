<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ActionsEditor,
		ListingSkeleton,
		LoadErrorBanner,
		PageHeader,
		RunsListing,
		Streamed,
		type RunDetailData,
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

	function switchTab(tab: string): void {
		const next = new URL(page.url);
		next.search = '';
		if (tab !== 'runs') next.searchParams.set('tab', tab);
		goto(`${next.pathname}${next.search}`, { keepFocus: true, noScroll: true });
	}

	/** The listing asked a new question; it becomes the URL. `?run=` survives, so the panel stays. */
	function navigate(next: ListingQuery): void {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	/** Real links for the pager; the current params are passed so `?run=` survives paging. */
	const href = (n: number) => (data.query ? pageHref(data.query, n, page.url.searchParams) : '?');

	/** A row was picked (or the panel closed): the pick lives in the URL, so a reload keeps it and
	 *  the loader streams the run's detail beside the page. */
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

	// The picked run's detail arrives as a promise beside the streamed page. Hold the last one while
	// the next loads, so the panel does not blink to empty on every filter change.
	let selectedRun = $state<RunDetailData | null>(null);
	$effect(() => {
		const pending = data.selected;
		if (!pending) {
			selectedRun = null;
			return;
		}
		let live = true;
		void Promise.resolve(pending).then((run) => {
			if (live) selectedRun = run;
		});
		return () => {
			live = false;
		};
	});

	/** Injected navigation, per the portal waterfall: the components hardcode no hrefs. */
	const links: RunLinks = {
		run: (uid) => `/actions/runs/${uid}`,
		client: (uid) => `/clients/${uid}`,
		account: (uid) => `/accounts/${uid}`,
		journey: (uid) => `/journeys/${uid}`,
		policy: (key) => `/actions?tab=policies&action=${encodeURIComponent(key)}`
	};
	const runActions = {
		retry: '?/retry',
		retryStep: '?/retry',
		cancel: '?/cancel',
		decide: '?/decide',
		remind: '?/remind'
	};

	// The policies editor deep-links a chosen action with `?action=`.
	function selectAction(key: string): void {
		const next = new URL(page.url);
		next.searchParams.set('tab', 'policies');
		next.searchParams.set('action', key);
		goto(`${next.pathname}${next.search}`, { keepFocus: true, noScroll: true, replaceState: true });
	}
</script>

<svelte:head><title>Actions · Platform</title></svelte:head>

<PageHeader
	title="Actions"
	description="Every provider instruction the firm runs, as durable runs. Who may run an action, and who must approve it, sit on the action; the provider comes from the account."
/>

<!-- Line-style tabs (full-width underline): the underline spans the content and the active tab
     carries the accent. -->
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

{#if (data.tab === 'runs' || data.tab === 'approvals') && data.runs && data.query}
	<!-- `keep`: a filter, sort or page change holds the table on screen while the next page lands,
	     so the counters and the toolbar do not go grey on every click. The Approvals tab is the
	     same workbench locked to the gate, with the gate's own counters and columns. -->
	<Streamed data={data.runs} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(board, stale)}
			{#if board.loadError}
				<LoadErrorBanner
					message={data.tab === 'approvals' ? 'Couldn’t load approvals.' : 'Couldn’t load runs.'}
					onRetry={() => invalidateAll()}
				/>
			{:else}
				<RunsListing
					page={board.page}
					query={data.query}
					onquery={navigate}
					pageHref={href}
					{links}
					selected={selectedRun}
					onselect={select}
					canManage={data.canManage}
					{enhance}
					actions={runActions}
					loading={stale}
					onrefresh={() => invalidateAll()}
					variant={data.tab === 'approvals' ? 'approvals' : 'runs'}
				/>
			{/if}
		{/snippet}
	</Streamed>
{:else if data.board}
	<Streamed data={data.board} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(board)}
			{#if board.loadError}
				<LoadErrorBanner message="Couldn’t load actions." onRetry={() => invalidateAll()} />
			{:else}
				<ActionsEditor
					categories={board.categories}
					actions={board.actions}
					roles={board.roles}
					selected={board.selected}
					onSelect={selectAction}
					canManage={data.canManage}
					{enhance}
					{form}
				/>
			{/if}
		{/snippet}
	</Streamed>
{/if}
