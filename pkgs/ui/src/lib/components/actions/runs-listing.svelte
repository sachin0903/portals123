<script lang="ts" module>
	/** The listing's filter keys — every one the API understands, so `paramsFromQuery` can clear a
	 *  stale one and a deep link (`?contact=…`) survives a filter change. `held` is the approvals
	 *  seat's own (`over_day` · `undecidable` · `money`). */
	export const RUN_FILTER_KEYS = [
		'state',
		'action',
		'provider',
		'requested_by',
		'contact',
		'held'
	] as const;

	/** The two seats this frame serves: every run, or the runs held at a gate. */
	export type RunsVariant = 'runs' | 'approvals';

	type SortKey = 'requested' | 'name' | 'state' | 'requested_by' | 'duration';
</script>

<script lang="ts">
	// The runs workbench — the Contributions / Transfers frame, for runs.
	//
	// Counters across the top that say what is where and WHO has it (the worker, a provider, an
	// approver, you); the queue at full width, server-paged; the panel pushed in beside it when a
	// row is picked. The listing is SERVER-DRIVEN: search, filters, sort and the page live in the
	// URL, the loader hands them to the API, and this component only asks for changes
	// (`onquery`). The app owns the navigation.
	//
	// The APPROVALS seat is the same frame locked to the gate: its counters are the gate's own
	// (how old, whether anyone can decide, how much money is waiting, what was decided), its
	// columns say who may decide and how long it has waited, and the panel carries the change.
	//
	// The table keeps the actions page's own ruled look: the toolbar in a bordered band, the rows
	// running edge to edge of the card, tracked capital column heads on a shaded band with a rule
	// above and below, and a thin footer with the range and the pager.
	import { tick } from 'svelte';
	import { REFRESH_WHILE } from '../notifications/poll-controller.js';
	import RefreshWhile from '../ui/refresh-while/refresh-while.svelte';
	import { Button } from '../ui/button/index.js';
	import UiPagination from '../ui/pagination/pagination.svelte';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { TruncatedText } from '../ui/truncated-text/index.js';
	import ListingToolbar from '../book/listing/listing-toolbar.svelte';
	import type { FilterDef } from '../book/listing/filter-panel.svelte';
	import {
		barValueFrom,
		isFiltered,
		optionsFromFacet,
		pageOf,
		rangeLabel,
		withBarValue,
		type ListingQuery
	} from '../book/listing/query.js';
	import { formatGBP } from '../../money.js';
	import { cn } from '../../utils.js';
	import type { Enhance } from '../book/clients/form-enhance.js';
	import { waitingLabel, waitingTone } from '../book/reviews/reviews.js';
	import RunPanel from './run-panel.svelte';
	import type { RunDetailActions } from './run-detail.svelte';
	import type { RunDetailData, RunLinks, RunListRow, RunsPage } from './run-types.js';
	import {
		formatDuration,
		providerLabel,
		relativeTime,
		runDurationMs,
		runInFlight,
		runStatusLabel,
		runStatusTone,
		stepToneClass,
		whenLabel
	} from './run-states.js';

	let {
		page,
		query,
		onquery,
		pageHref,
		links,
		selected = null,
		onselect,
		canManage = false,
		enhance,
		actions = {},
		onrefresh,
		lockedAction = null,
		loading = false,
		variant = 'runs'
	}: {
		/** The current page of runs, with the whole set's size, the facets and the stat counts. */
		page: RunsPage;
		query: ListingQuery;
		/** The toolbar, a column header or a tile changed the question; the app navigates. */
		onquery: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links. */
		pageHref?: (page: number) => string;
		links: RunLinks;
		/** The picked row's run, loaded by the app (`?run=`), or null for no panel. */
		selected?: RunDetailData | null;
		/** A row was picked (its uid) or the panel closed (null). The app puts it in the URL. */
		onselect?: (uid: string | null) => void;
		canManage?: boolean;
		enhance?: Enhance;
		actions?: RunDetailActions;
		/** Re-read while a run on the page is in flight, and after an act. */
		onrefresh?: () => void;
		/** The per-action page: the action filter is fixed and its facet is not offered. */
		lockedAction?: string | null;
		/** The rows are being replaced: dim them, keep the frame. */
		loading?: boolean;
		/** `approvals`: the state is locked to the gate by the app, and the frame shows the gate's own
		 *  counters and columns. */
		variant?: RunsVariant;
	} = $props();

	const now = Date.now();
	const approvals = $derived(variant === 'approvals');
	const polling = $derived(!!onrefresh && page.items.some((run) => runInFlight(run.status)));
	const selectedUid = $derived(selected?.uid ?? null);

	// The counters double as the state filter: a tile's word is the one the API filters on.
	const TILES = [
		{
			state: 'running',
			label: 'Running',
			actor: 'Worker',
			pip: 'bg-info',
			value: () => page.stats.running
		},
		{
			state: 'waiting',
			label: 'Waiting on provider',
			actor: 'SEI · Seccl · Morgan Lloyd',
			pip: 'bg-info',
			value: () => page.stats.waiting
		},
		{
			state: 'awaiting_approval',
			label: 'Awaiting approval',
			actor: 'Approvers',
			pip: 'bg-warning',
			value: () => page.stats.awaiting_approval
		},
		{
			state: 'needs_attention',
			label: 'Needs attention',
			actor: 'You',
			pip: 'bg-warning',
			value: () => page.stats.needs_attention
		},
		{
			state: 'failed',
			label: 'Failed · 7 days',
			actor: 'You',
			pip: 'bg-destructive',
			value: () => page.stats.failed_7d
		},
		{
			state: 'succeeded',
			label: 'Completed · 24h',
			actor: 'Done',
			pip: 'bg-primary',
			value: () => page.stats.completed_24h
		}
	] as const;

	// The approvals seat's counters: how many are held, how old, whether anyone can decide them,
	// how much money is waiting, and what was decided this week. The first four filter the held
	// rows (`held=`); the last is a fact.
	const HELD_TILES = $derived([
		{
			held: null,
			label: 'Awaiting approval',
			actor: 'Approvers',
			pip: 'bg-warning',
			value: String(page.stats.awaiting_approval)
		},
		{
			held: 'over_day',
			label: 'Older than a day',
			actor: 'Still waiting',
			pip: 'bg-warning',
			value: String(page.stats.held_over_day ?? 0)
		},
		{
			held: 'undecidable',
			label: 'Nobody can decide',
			actor: 'The requester is the only holder',
			pip: 'bg-destructive',
			value: String(page.stats.held_undecidable ?? 0)
		},
		{
			held: 'money',
			label: 'Money held',
			actor: 'Provider sends waiting',
			pip: 'bg-info',
			value: formatGBP(page.stats.money_held ?? 0)
		},
		{
			held: undefined,
			label: 'Decided · 7 days',
			actor: `${page.stats.approved_7d ?? 0} approved · ${page.stats.rejected_7d ?? 0} rejected`,
			pip: 'bg-success',
			value: String(page.stats.decided_7d ?? 0)
		}
	]);

	const activeState = $derived(
		Array.isArray(query.filters.state) ? query.filters.state[0] : query.filters.state
	);
	const activeHeld = $derived(
		Array.isArray(query.filters.held) ? query.filters.held[0] : query.filters.held
	);

	function toggleState(state: string): void {
		const filters = { ...query.filters };
		if (activeState === state) delete filters.state;
		else filters.state = state;
		onquery({ ...query, filters, offset: 0 });
	}

	function toggleHeld(held: string | null): void {
		const filters = { ...query.filters };
		if (held === null || activeHeld === held) delete filters.held;
		else filters.held = held;
		onquery({ ...query, filters, offset: 0 });
	}

	const barKeys = $derived(
		approvals
			? (['action', 'provider'] as const)
			: lockedAction
				? (['state', 'provider'] as const)
				: (['state', 'action', 'provider'] as const)
	);
	const filterDefs = $derived<FilterDef[]>([
		...(approvals
			? []
			: [
					{
						key: 'state',
						label: 'State',
						single: true,
						options: optionsFromFacet(page.facets, 'state', runStatusLabel, page.facet_counts)
					} satisfies FilterDef
				]),
		...(lockedAction
			? []
			: [
					{
						key: 'action',
						label: 'Action',
						options: optionsFromFacet(
							page.facets,
							'action',
							(key) => page.facet_labels?.action?.[key] ?? key,
							page.facet_counts
						),
						searchPlaceholder: 'Search actions…'
					} satisfies FilterDef
				]),
		{
			key: 'provider',
			label: 'Provider',
			options: optionsFromFacet(page.facets, 'provider', providerLabel, page.facet_counts)
		}
	]);

	// The pinned state on the approvals seat is the seat, not a filter the reader set.
	const dirty = $derived(
		approvals
			? isFiltered({ ...query, filters: { ...query.filters, state: undefined as never } })
			: isFiltered(query)
	);
	function clear(): void {
		onquery({
			...query,
			q: '',
			filters: approvals
				? { state: 'awaiting_approval' }
				: lockedAction
					? { action: lockedAction }
					: {},
			offset: 0
		});
	}

	// Server sort: a header click updates the URL and the API orders the whole set. Text columns
	// open A→Z; time and duration open newest-or-longest first — except the held column, which
	// opens oldest first, since the oldest case is the one to clear.
	const DEFAULT_DIR: Record<SortKey, 'asc' | 'desc'> = {
		requested: 'desc',
		name: 'asc',
		state: 'asc',
		requested_by: 'asc',
		duration: 'desc'
	};
	function setSort(key: SortKey): void {
		const fallback = approvals && key === 'requested' ? 'asc' : DEFAULT_DIR[key];
		const dir = query.sort === key ? (query.dir === 'asc' ? 'desc' : 'asc') : fallback;
		onquery({ ...query, sort: key, dir, offset: 0 });
	}
	function ariaSort(key: SortKey): 'ascending' | 'descending' | 'none' {
		if (query.sort !== key) return 'none';
		return query.dir === 'asc' ? 'ascending' : 'descending';
	}
	function arrow(key: SortKey): string {
		return query.sort === key ? (query.dir === 'asc' ? '↑' : '↓') : '';
	}

	function subject(run: RunListRow): string | null {
		if (!run.subject_kind || !run.subject_uid) return null;
		if (run.subject_kind === 'contact' && run.contact_name) return null;
		return `${run.subject_kind} · ${run.subject_uid}`;
	}

	/** How long a held row has waited at its gate. */
	function heldFor(run: RunListRow): { label: string; tone: 'none' | 'warning' | 'danger' } {
		const since = run.held_since ?? run.requested_at;
		if (!since) return { label: '—', tone: 'none' };
		const ms = now - new Date(since).getTime();
		return { label: waitingLabel(ms), tone: waitingTone(ms) };
	}

	const stuck = (run: RunListRow) => run.eligible_approvers === 0;

	const current = $derived(pageOf(query));
	const pageCount = $derived(Math.max(1, Math.ceil(page.total / Math.max(1, query.limit))));

	let root = $state<HTMLElement | null>(null);
	function close(): void {
		onselect?.(null);
	}

	// Opening the panel narrows the queue and folds its columns, so the row that was clicked can
	// reflow out of view. Bring it back once layout has settled.
	$effect(() => {
		if (!selectedUid) return;
		void tick().then(() =>
			requestAnimationFrame(() =>
				root?.querySelector('tr.run-selected')?.scrollIntoView({ block: 'nearest' })
			)
		);
	});

	/** Esc closes the panel unless something layered on top owns the key. */
	function onkeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !selectedUid || event.defaultPrevented) return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if (
			document.querySelector(
				'[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"]'
			)
		)
			return;
		close();
	}

	const SORT_HEAD =
		'cursor-pointer border-b px-3 py-2 text-left font-semibold tracking-wide uppercase select-none hover:text-foreground';
	const PLAIN_HEAD = 'border-b px-3 py-2 text-left font-semibold tracking-wide uppercase';
	const CELL = 'border-b px-3 py-2 align-middle';
	const TILE =
		'bg-card min-w-0 rounded-xl border p-3 text-left transition-colors border-border hover:border-border';
	const TILE_ON = 'border-ring ring-ring/40 ring-1';
</script>

<svelte:window {onkeydown} />

<RefreshWhile active={polling} interval={REFRESH_WHILE.actionRun} onpoll={() => onrefresh?.()} />

<div bind:this={root} class="flex flex-col gap-4">
	<!-- Counters: a count, what it is, who has it. Click = filter; click again = clear. -->
	{#if approvals}
		<section
			aria-label="Approval counts"
			class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
		>
			{#each HELD_TILES as tile (tile.label)}
				{@const on = tile.held === null ? !activeHeld : tile.held === activeHeld}
				{#if tile.held === undefined}
					<div class={TILE}>
						<span class="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
							<span class="size-[7px] shrink-0 rounded-full {tile.pip}"></span>
							<TruncatedText text={tile.label} class="min-w-0" />
						</span>
						<span class="mt-0.5 block text-xl font-semibold tracking-tight tabular-nums"
							>{tile.value}</span
						>
						<span class="text-muted-foreground/70 block truncate text-[10.5px] font-medium"
							>{tile.actor}</span
						>
					</div>
				{:else}
					<button
						type="button"
						aria-pressed={on}
						onclick={() => toggleHeld(tile.held)}
						class="{TILE} {on ? TILE_ON : ''}"
					>
						<span class="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
							<span class="size-[7px] shrink-0 rounded-full {tile.pip}"></span>
							<TruncatedText text={tile.label} class="min-w-0" />
						</span>
						<span class="mt-0.5 block text-xl font-semibold tracking-tight tabular-nums"
							>{tile.value}</span
						>
						<span class="text-muted-foreground/70 block truncate text-[10.5px] font-medium"
							>{tile.actor}</span
						>
					</button>
				{/if}
			{/each}
		</section>
	{:else}
		<section aria-label="Run counts" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
			{#each TILES as tile (tile.state)}
				{@const on = activeState === tile.state}
				<button
					type="button"
					aria-pressed={on}
					onclick={() => toggleState(tile.state)}
					class="{TILE} {on ? TILE_ON : ''}"
				>
					<span class="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
						<span class="size-[7px] shrink-0 rounded-full {tile.pip}"></span>
						<TruncatedText text={tile.label} class="min-w-0" />
					</span>
					<span class="mt-0.5 block text-xl font-semibold tracking-tight tabular-nums"
						>{tile.value()}</span
					>
					<span class="text-muted-foreground/70 block truncate text-[10.5px] font-medium"
						>{tile.actor}</span
					>
				</button>
			{/each}
		</section>
	{/if}

	<!-- The queue, and the panel pushed in beside it while a row is picked. -->
	<div class="flex items-start gap-4">
		<!-- The listing panel: search/filter, table and footer in one card, as on Team / Clients /
		     Accounts. The header's bottom border is the line above the column names; the state filter
		     stays on the tiles above, which double as the at-a-glance counts. -->
		<section
			class={cn(
				'border-border bg-card @container min-w-0 flex-1 overflow-hidden rounded-xl border',
				selected && 'max-xl:hidden'
			)}
		>
			<div class="border-border border-b px-4 py-3">
				<ListingToolbar
					query={query.q}
					placeholder="Search by client, account, run or case number"
					{dirty}
					onclear={clear}
					onsearch={(term) => onquery({ ...query, q: term, offset: 0 })}
					{filterDefs}
					filterValue={barValueFrom(query, barKeys)}
					onfilters={(next) => onquery(withBarValue(query, barKeys, next))}
					{onrefresh}
				/>
			</div>
			<div class="overflow-x-auto">
				<table
					class="w-full border-collapse text-sm {loading ? 'opacity-60' : ''}"
					aria-label={approvals ? 'Held runs' : 'Runs'}
					aria-busy={loading}
				>
					<thead>
						<tr class="text-muted-foreground [&>th]:border-border [&>th]:bg-muted/40 text-[10px]">
							<th
								class={SORT_HEAD}
								aria-sort={ariaSort('requested')}
								onclick={() => setSort('requested')}
							>
								{approvals ? 'Held' : 'Requested'}
								{arrow('requested')}
							</th>
							<th class={SORT_HEAD} aria-sort={ariaSort('name')} onclick={() => setSort('name')}>
								Run {arrow('name')}
							</th>
							<th class={PLAIN_HEAD}>Subject</th>
							<th class="{PLAIN_HEAD} hidden @4xl:table-cell">Provider</th>
							{#if approvals}
								<th class={PLAIN_HEAD}>Approvers</th>
								<th
									class="{SORT_HEAD} hidden @5xl:table-cell"
									aria-sort={ariaSort('requested_by')}
									onclick={() => setSort('requested_by')}
								>
									Requested by {arrow('requested_by')}
								</th>
								<th class="{PLAIN_HEAD} text-right">Waiting</th>
							{:else}
								<th class="{PLAIN_HEAD} hidden @3xl:table-cell">Progress</th>
								<th
									class={SORT_HEAD}
									aria-sort={ariaSort('state')}
									onclick={() => setSort('state')}
								>
									State {arrow('state')}
								</th>
								<th
									class="{SORT_HEAD} hidden @5xl:table-cell"
									aria-sort={ariaSort('requested_by')}
									onclick={() => setSort('requested_by')}
								>
									Requested by {arrow('requested_by')}
								</th>
								<th
									class="{SORT_HEAD} text-right"
									aria-sort={ariaSort('duration')}
									onclick={() => setSort('duration')}
								>
									Duration {arrow('duration')}
								</th>
							{/if}
						</tr>
					</thead>
					<tbody>
						{#each page.items as run (run.uid)}
							{@const attn = run.status === 'needs_attention' || run.status === 'conflicted'}
							{@const picked = run.uid === selectedUid}
							{@const held = heldFor(run)}
							{@const inset = approvals
								? stuck(run) || held.tone === 'danger'
									? 'shadow-[inset_3px_0_0_var(--color-destructive)]'
									: held.tone === 'warning'
										? 'shadow-[inset_3px_0_0_var(--color-warning)]'
										: ''
								: attn
									? 'shadow-[inset_3px_0_0_var(--color-warning)]'
									: ''}
							<tr
								class={cn(
									'hover:bg-muted/50 [&>td]:border-border',
									onselect && 'cursor-pointer',
									inset,
									picked &&
										'run-selected bg-accent/60 [&>td:first-child]:shadow-[inset_2px_0_0_0_var(--primary)]'
								)}
								aria-selected={picked}
								onclick={() => onselect?.(picked ? null : run.uid)}
							>
								<td class="{CELL} whitespace-nowrap">
									<span class="block tabular-nums"
										>{relativeTime(run.held_since ?? run.requested_at, now)}</span
									>
									<span class="text-muted-foreground block text-[10.5px] tabular-nums"
										>{whenLabel(run.held_since ?? run.requested_at)}</span
									>
								</td>
								<td class={CELL}>
									<div class="flex items-center gap-2 font-semibold">
										<a
											href={links.run(run.uid)}
											class="hover:underline"
											onclick={(e) => {
												if (onselect) {
													e.preventDefault();
													onselect(run.uid);
												}
											}}>{run.name}</a
										>
										{#if run.case_no != null}
											<span
												class="border-warning-line bg-warning-subtle text-warning-strong rounded border px-1.5 font-mono text-[10.5px] font-semibold"
												>RV-{run.case_no}</span
											>
										{/if}
									</div>
									<div class="text-muted-foreground font-mono text-[11px]">
										{run.action_key}{#if approvals && run.held_amount != null}
											<span class="font-sans tabular-nums">
												· {formatGBP(run.held_amount)}</span
											>{/if}
									</div>
								</td>
								<td class={CELL}>
									{#if run.contact_name}
										<div>
											<TruncatedText
												text={run.contact_name}
												href={links.client && run.contact_uid
													? links.client(run.contact_uid)
													: undefined}
												class="w-fit max-w-full"
											/>
										</div>
									{/if}
									{#if subject(run)}
										<div class="text-muted-foreground text-[11px]">
											{#if run.subject_kind === 'account' && links.account}
												<a
													href={links.account(run.subject_uid ?? '')}
													class="hover:underline"
													onclick={(e) => e.stopPropagation()}>{subject(run)}</a
												>
											{:else}{subject(run)}{/if}
										</div>
									{/if}
								</td>
								<td class="{CELL} hidden @4xl:table-cell">
									<span class="flex flex-wrap gap-1">
										{#each run.providers as code (code)}
											<span
												class="text-muted-foreground border-border/70 rounded border px-1.5 text-[10.5px] font-semibold tracking-wide"
												>{providerLabel(code)}</span
											>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/each}
									</span>
								</td>
								{#if approvals}
									<td class={CELL}>
										<!-- Who may decide, with how many of them could (the requester never counts).
										     Red when nobody can: the case is stuck until a role changes hands. -->
										<span class="flex flex-wrap gap-1">
											{#each run.approver_roles ?? [] as role (role)}
												<span
													class="inline-flex items-center gap-1 rounded-full border px-2 text-[11px] font-medium {stuck(
														run
													)
														? 'border-destructive-line bg-destructive-subtle text-destructive-strong'
														: 'border-border'}"
												>
													{role}
													{#if run.eligible_approvers != null}
														<span class="opacity-70 tabular-nums"
															>· {run.eligible_approvers}{stuck(run) ? ' others' : ''}</span
														>
													{/if}
												</span>
											{:else}
												<span class="text-muted-foreground text-xs">Any approver</span>
											{/each}
										</span>
									</td>
									<td class="{CELL} hidden @5xl:table-cell">{run.requested_by_name ?? 'system'}</td>
									<td
										class="{CELL} text-right whitespace-nowrap tabular-nums {held.tone === 'danger'
											? 'text-destructive-strong font-medium'
											: held.tone === 'warning'
												? 'text-warning-strong font-medium'
												: 'text-muted-foreground'}"
									>
										{held.label}
									</td>
								{:else}
									<td class="{CELL} hidden @3xl:table-cell">
										<!-- The immediate children as segments, each titled with its name and state. -->
										<span
											class="inline-flex gap-0.5"
											role="img"
											aria-label={run.children
												.map((c) => `${c.name}: ${runStatusLabel(c.status)}`)
												.join(', ')}
										>
											{#each run.children as child (child.uid)}
												<span
													class="inline-block h-[7px] w-4 rounded-sm {stepToneClass(child.status)}"
													title="{child.name} · {runStatusLabel(child.status)}"
												></span>
											{:else}
												<span
													class="bg-muted-foreground/25 inline-block h-[7px] w-4 rounded-sm"
													title="No children"
												></span>
											{/each}
										</span>
									</td>
									<td class={CELL}>
										<StatusBadge tone={runStatusTone(run.status)} pulse={run.status === 'running'}>
											{runStatusLabel(run.status)}
										</StatusBadge>
									</td>
									<td class="{CELL} hidden @5xl:table-cell">{run.requested_by_name ?? 'system'}</td>
									<td
										class="{CELL} text-muted-foreground text-right whitespace-nowrap tabular-nums"
									>
										{formatDuration(runDurationMs(run, now))}
									</td>
								{/if}
							</tr>
						{:else}
							<tr>
								<td colspan="8" class="text-muted-foreground px-3.5 py-10 text-center text-sm">
									{#if dirty}
										<span class="block"
											>{approvals
												? 'No held runs match these filters.'
												: 'No runs match these filters.'}</span
										>
										<Button variant="outline" size="sm" class="mt-3" onclick={clear}
											>Clear filters</Button
										>
									{:else if approvals}
										<span class="block">Nothing is waiting for approval.</span>
										<span class="mt-1 block text-xs"
											>A run lands here when an approver role on its action holds it.</span
										>
									{:else}
										<span class="block">No runs yet.</span>
										<span class="mt-1 block text-xs"
											>Runs start from the screen that owns the record — a client, an account, a
											journey.</span
										>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<!-- Footer: the range always shows (as on Team / Clients); the page controls only past one page. -->
			<div
				class="border-border text-muted-foreground flex items-center justify-between gap-3 border-t px-4 py-2.5 text-[11px] tabular-nums"
			>
				<span>{rangeLabel(query, page.total, page.items.length)}</span>
				<UiPagination page={current} {pageCount} href={pageHref} compact />
			</div>
		</section>

		{#if selected}
			<aside
				aria-label="Selected run"
				class="flex min-h-0 min-w-0 flex-1 flex-col xl:sticky xl:top-20 xl:w-[420px] xl:flex-none xl:max-h-[calc(100svh-6rem)]"
			>
				<RunPanel
					run={selected}
					{canManage}
					{enhance}
					{links}
					{actions}
					onclose={close}
					{onrefresh}
					{now}
				/>
			</aside>
		{/if}
	</div>
</div>
