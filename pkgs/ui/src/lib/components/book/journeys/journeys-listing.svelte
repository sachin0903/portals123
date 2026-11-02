<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import EllipsisVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import UsersIcon from '@lucide/svelte/icons/users';
	import { Button } from '../../ui/button/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { DataTable, sortRows } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { formatDateTime } from '../../../dates.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { StatusBadge, type StatusTone } from '../../ui/status-badge/index.js';
	import AdviserStack from '../../data/adviser-stack.svelte';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import StatTile from '../../data/stat-tile.svelte';
	import { type FilterDef } from '../listing/filter-panel.svelte';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import PaginationStrip from '../listing/pagination-strip.svelte';
	import { matches, matchesAny, optionsFrom, search } from '../listing/filters.js';
	import { PAGE_SIZE, rangeLabelFromOffset } from '../listing/query.js';
	import {
		actionOwner,
		inTile,
		isCompletedOnboarding,
		ME,
		presentedStatus,
		stateLabel,
		stateTone,
		statusLabel,
		tileInForce,
		toggleTile,
		type AppExecutionRow,
		type EngineState,
		type JourneyTile
	} from './applications.js';
	import type { JourneysListingLinks } from './types.js';

	let {
		applications,
		loadFailed = false,
		links,
		/** Re-runs the app's load (`invalidateAll`) behind the error banner's Retry. */
		onretry,
		/** The filter bar's selection on arrival. Omitted, nothing is applied. */
		filters = {}
	}: {
		applications: AppExecutionRow[];
		loadFailed?: boolean;
		links: JourneysListingLinks;
		onretry?: () => void;
		filters?: Record<string, string[]>;
	} = $props();

	let query = $state('');
	/** The filter bar's state. ONE object rather than five `$state(ALL)` strings: the bar hands back
	 *  the whole selection, and five separate values could not express "Onboarding OR Review"
	 *  anyway. This listing filters an already-loaded array, so nothing here reaches the API.
	 *
	 *  With no status chosen, stopped and completed journeys stay out: they are history, and
	 *  history is the Completed tile or the status filter rather than the first thing to scroll
	 *  past. That cut is not a filter the reader applied, so the bar stays shut and no tile is
	 *  pressed. A tile click writes into this object, and the chips then say what the table is
	 *  cut to. */
	// The prop seeds the bar; the bar then owns it.
	// svelte-ignore state_referenced_locally
	let barValue = $state<Record<string, string[]>>(filters);
	const pick = (key: string) => barValue[key] ?? [];
	const tile = $derived(tileInForce(barValue));
	let page = $state(1);
	/** `null` is the arrival order: most recently started first. A header click chooses a column. */
	let sort = $state<DataTableSort | null>(null);

	function pct(a: AppExecutionRow): number {
		return a.steps_total ? Math.round((a.steps_done / a.steps_total) * 100) : 0;
	}
	function clientName(a: AppExecutionRow): string {
		return a.client_names ?? 'Client not recorded';
	}
	function journeyName(a: AppExecutionRow): string {
		return a.flow_name ?? 'Journey not recorded';
	}
	function composition(a: AppExecutionRow): string {
		return `${a.clients} ${a.clients === 1 ? 'person' : 'people'} · ${a.accounts} ${
			a.accounts === 1 ? 'account' : 'accounts'
		}`;
	}
	function stage(a: AppExecutionRow): string {
		return statusLabel(a.application_status) || 'Not started';
	}
	function applicationPdf(a: AppExecutionRow): string | null {
		if (!links.applicationPdf || !isCompletedOnboarding(a)) return null;
		return links.applicationPdf(a.uid);
	}
	function actionOwners(a: AppExecutionRow): string[] {
		const current = actionOwner(a);
		return current === '—' ? [] : [current];
	}

	// "Me" takes the success tone: the reader's own move, distinct from a colleague's (brand),
	// the client's (warning) and Graphene's (info), so their rows are found at a glance.
	function actionOwnerTone(label: string): StatusTone {
		if (label === ME) return 'success';
		if (label === 'Graphene') return 'info';
		if (label === 'Client' || label === 'Clients') return 'warning';
		return 'brand';
	}

	const count = (key: JourneyTile) => applications.filter((a) => inTile(a, key)).length;
	const needYou = $derived(count('need-you'));
	const withClient = $derived(count('with-client'));
	const inFlight = $derived(count('in-flight'));
	const completed = $derived(count('completed'));
	const TILE_LABELS: Record<JourneyTile, string> = {
		'in-flight': 'In flight',
		'need-you': 'Need you',
		'with-client': 'With the client',
		completed: 'Completed'
	};
	const pressedTile = (key: JourneyTile) => tile === key;

	const stageOptions = $derived(optionsFrom(applications, stage));
	const journeyOptions = $derived(optionsFrom(applications, journeyName));
	// Adviser, not action owner: `actionOwners` can read "Adviser" for a row whose steps sit with
	// Graphene or with a blocked engine, so it is not the same value as `adviser_name` and cannot
	// stand in for it — a firm with three advisers still needs to see each one's own book.
	const adviserOptions = $derived(optionsFrom(applications, (a) => a.adviser_name));
	const assigneeOptions = $derived(optionsFrom(applications, (a) => a.assignee_name));
	const ownerOptions = $derived(optionsFrom(applications, actionOwners));
	// The whole vocabulary, not the states the rows happen to be in: the tiles name states
	// (`failed`, `completed`) a list may hold none of today, and a chip for a value with no
	// option prints the raw key. `stateLabel` is the badge's namer — a filter that offers
	// "Cancelled" for the rows badged "Stopped" is two words for one state.
	const STATUS_VALUES: EngineState[] = [
		'draft',
		'active',
		'waiting',
		'failed',
		'completed',
		'cancelled'
	];
	const statusOptions = STATUS_VALUES.map((value) => ({ value, label: stateLabel(value) }));
	const dirty = $derived(
		query.trim() !== '' || Object.values(barValue).some((picked) => picked.length > 0)
	);

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'journey', label: 'Journey', options: journeyOptions },
		{ key: 'stage', label: 'Stage', options: stageOptions },
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		},
		{
			key: 'assignee',
			label: 'Assigned to',
			options: assigneeOptions,
			searchPlaceholder: 'Search staff…'
		},
		{ key: 'owner', label: 'Waiting on', options: ownerOptions },
		{ key: 'status', label: 'Status', options: statusOptions }
	]);
	const columns: DataTableColumn<AppExecutionRow>[] = [
		{
			key: 'clients',
			header: 'Clients',
			sortable: true,
			headerClass: 'w-[22%]',
			cellClass: 'truncate',
			sortValue: clientName
		},
		{
			key: 'journey',
			header: 'Journey',
			sortable: true,
			headerClass: 'w-[14%]',
			cellClass: 'truncate',
			sortValue: journeyName
		},
		{
			key: 'adviser',
			header: 'Adviser',
			sortable: true,
			hideBelowWidth: '2xl',
			headerClass: 'w-[13%]',
			cellClass: 'truncate',
			sortValue: (a) => a.adviser_name
		},
		{
			key: 'assignee',
			header: 'Assigned to',
			sortable: true,
			hideBelowWidth: '2xl',
			headerClass: 'w-[13%]',
			cellClass: 'truncate',
			sortValue: (a) => a.assignee_name
		},
		{
			key: 'progress',
			header: 'Progress',
			sortable: true,
			headerClass: 'w-[14%]',
			sortValue: pct
		},
		{
			// The widest stage is "Account setup failed", which wrapped to two lines at 12% and made
			// one row taller than its neighbours — so the width goes up and the cell clips.
			key: 'stage',
			header: 'Stage',
			sortable: true,
			hideBelowWidth: 'xl',
			headerClass: 'w-[15%]',
			cellClass: 'truncate',
			sortValue: stage
		},
		{
			// "Waiting on", not "Action owner": beside "Assigned to" the two read as rivals for the
			// same fact. This one is whose TURN it is — the client, Graphene, or the assignee.
			key: 'owner',
			header: 'Waiting on',
			sortable: true,
			headerClass: 'w-[12%]',
			sortValue: actionOwner
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (a) => stateLabel(a.status),
			headerClass: 'w-[10%]'
		},
		// The download lives here, not under the status badge. An empty kebab is a dead control, so
		// the cell stays blank until a completed onboarding has a pack to offer.
		{ key: 'actions', header: '', headerClass: 'w-10' }
	];

	const filtered = $derived(
		applications.filter(
			(a) =>
				matches(pick('journey'), journeyName(a)) &&
				matches(pick('stage'), stage(a)) &&
				matches(pick('adviser'), a.adviser_name) &&
				matches(pick('assignee'), a.assignee_name) &&
				matchesAny(pick('owner'), actionOwners(a)) &&
				// No status chosen is the journeys still underway — the same rows the In flight
				// tile counts — without that reading as a filter the reader switched on.
				(pick('status').length
					? matches(pick('status'), presentedStatus(a.status))
					: inTile(a, 'in-flight')) &&
				search(
					query,
					a.client_names,
					a.flow_name,
					a.uid,
					a.adviser_name,
					a.assignee_name,
					stage(a),
					actionOwner(a),
					a.status,
					stateLabel(a.status)
				)
		)
	);
	// Sorted here, across the whole population, before the page is cut: the table is told the
	// rows arrive ordered (`onSort`), because sorting one page in isolation reorders within the
	// page and never across the list. No column chosen is most recently started first.
	const rows = $derived(
		sort
			? sortRows(filtered, columns, sort)
			: [...filtered].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
	);
	const pageCount = $derived(Math.max(1, Math.ceil(rows.length / PAGE_SIZE)));
	const displayedRows = $derived(rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE));
	const range = $derived(
		rangeLabelFromOffset((page - 1) * PAGE_SIZE, displayedRows.length, rows.length)
	);

	// Back to page one whenever the population changes — page 4 of a list that just became two
	// pages long is an empty table. `barValue` is one object now, so touching it is one read.
	$effect(() => {
		void query;
		void barValue;
		page = 1;
	});
	$effect(() => {
		if (page > pageCount) page = pageCount;
	});

	function clear() {
		query = '';
		barValue = {};
	}
</script>

{#snippet startJourney()}
	<!-- The empty-state door. The toolbar kebab lists the same flows; this stays a visible
	     button because an empty page with the act buried in a menu is a dead end. -->
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button {...props}>
					<PlusIcon class="size-4" /> New journey
					<ChevronDownIcon class="size-3.5 opacity-70" />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="min-w-52">
			{#each links.newJourneys ?? [] as flow (flow.code)}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a {...props} href={flow.href}>{flow.name}</a>
					{/snippet}
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if loadFailed}
		<LoadErrorBanner message="Journeys couldn’t be loaded. Try again." onRetry={onretry} />
	{:else if applications.length === 0}
		<!-- It said "Start a journey for a client" and offered nothing, while the header carried the
		     control that does it. Describing an act is not offering it. Gated on the same
		     `newJourneys` the header is: a read-only mount (the operations portal's) passes none,
		     and a menu with nothing in it is a dead end wearing a button.
		     The full stop went with the sentence becoming a title. -->
		<EmptyState
			icon={WorkflowIcon}
			title="No journeys yet"
			description="A journey tracks a client through onboarding, and appears here once started."
			action={links.newJourneys?.length ? startJourney : undefined}
		/>
	{:else}
		<!-- Each tile is the filter bar set to that figure: a click applies it, a click on the one
		     in force lifts it. The bar's chips below say what the table is cut to. -->
		<div class="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
			<StatTile
				label={TILE_LABELS['in-flight']}
				icon={WorkflowIcon}
				value={String(inFlight)}
				sub="journeys not yet settled"
				onclick={() => (barValue = toggleTile(barValue, 'in-flight'))}
				pressed={pressedTile('in-flight')}
			/>
			<StatTile
				label={TILE_LABELS['need-you']}
				icon={CircleAlertIcon}
				value={String(needYou)}
				sub={needYou > 0 ? 'waiting on you' : 'nothing waiting on you'}
				tone={needYou > 0 ? 'negative' : 'neutral'}
				onclick={() => (barValue = toggleTile(barValue, 'need-you'))}
				pressed={pressedTile('need-you')}
			/>
			<StatTile
				label={TILE_LABELS['with-client']}
				icon={UsersIcon}
				value={String(withClient)}
				sub="awaiting a client action"
				onclick={() => (barValue = toggleTile(barValue, 'with-client'))}
				pressed={pressedTile('with-client')}
			/>
			<StatTile
				label={TILE_LABELS.completed}
				icon={CircleCheckIcon}
				value={String(completed)}
				sub="journeys finished"
				onclick={() => (barValue = toggleTile(barValue, 'completed'))}
				pressed={pressedTile('completed')}
			/>
		</div>

		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search clients, adviser, assignee, journey or reference"
					{dirty}
					onclear={clear}
					actions={!loadFailed && links.newJourneys?.length ? startJourney : undefined}
					{filterDefs}
					filterValue={barValue}
					onfilters={(next) => (barValue = next)}
				></ListingToolbar>

				<DataTable
					{columns}
					rows={displayedRows}
					{sort}
					onSort={(next) => (sort = next)}
					scroll
					fixed
					getKey={(a) => a.uid}
					label="Journeys"
					empty="No journeys match your filters."
				>
					{#snippet cell(a, column)}
						{#if column.key === 'clients'}
							<div class="min-w-0">
								<TruncatedText
									text={clientName(a)}
									href={links.journey(a.uid)}
									class="font-medium"
								/>
								<p class="text-muted-foreground truncate text-xs">{composition(a)}</p>
							</div>
						{:else if column.key === 'journey'}
							<!-- When it began, under the name: the list is most recent first, and the
							     date is what makes that order legible. -->
							<div class="min-w-0">
								<TruncatedText text={journeyName(a)} />
								<p class="text-muted-foreground truncate text-xs tabular-nums">
									{formatDateTime(a.created_at)}
								</p>
							</div>
						{:else if column.key === 'adviser'}
							<AdviserStack names={[a.adviser_name]} />
						{:else if column.key === 'assignee'}
							{#if a.assignee_name}
								<TruncatedText text={a.assignee_name} />
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if column.key === 'progress'}
							<!-- The bar IS the percentage, so printing "2/12 · 17%" beside it said the same
							     thing three times — and the widest of them ("12/12 · 100%") did not fit the
							     4rem box it was given, so it overflowed into the Stage column and the two
							     columns read as one. The steps are the figure worth having in words (a
							     percentage of an unknown number of steps tells an adviser nothing about how
							     much is left), and the percentage stays for anyone who needs it: it is what
							     the progressbar announces and what the hover says. -->
							{#if !a.steps_total}
								<span class="text-muted-foreground text-xs">Not started</span>
							{:else}
								<div class="flex items-center gap-2.5" title={`${pct(a)}% complete`}>
									<div
										class="bg-muted h-1.5 min-w-0 flex-1 overflow-hidden rounded-full"
										role="progressbar"
										aria-label="Journey progress"
										aria-valuemin="0"
										aria-valuemax="100"
										aria-valuenow={pct(a)}
										aria-valuetext={`${a.steps_done} of ${a.steps_total} steps · ${pct(a)}%`}
									>
										<div class="bg-primary h-full rounded-full" style:width={`${pct(a)}%`}></div>
									</div>
									<!-- `min-w`, not a fixed `w`: the bars line up for the counts a journey
									     actually has, and a longer one shortens the bar instead of spilling out
									     of the column. -->
									<span
										class="text-muted-foreground min-w-10 shrink-0 text-right text-xs whitespace-nowrap tabular-nums"
									>
										{a.steps_done}/{a.steps_total}
									</span>
								</div>
							{/if}
						{:else if column.key === 'stage'}
							<TruncatedText text={stage(a)} />
						{:else if column.key === 'owner'}
							{@const currentOwner = actionOwner(a)}
							{#if currentOwner === '—'}
								<span class="text-muted-foreground">—</span>
							{:else}
								<StatusBadge tone={actionOwnerTone(currentOwner)}>{currentOwner}</StatusBadge>
							{/if}
						{:else if column.key === 'status'}
							<StatusBadge tone={stateTone(a.status)}>{stateLabel(a.status)}</StatusBadge>
						{:else if column.key === 'actions'}
							{@const pdf = applicationPdf(a)}
							{#if pdf}
								<DropdownMenu.Root>
									<DropdownMenu.Trigger>
										{#snippet child({ props })}
											<Button
												variant="ghost"
												size="icon"
												class="size-8"
												aria-label={`Actions for ${clientName(a)}`}
												{...props}
											>
												<EllipsisVerticalIcon class="size-4" />
											</Button>
										{/snippet}
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="end" class="w-52">
										<DropdownMenu.Item>
											{#snippet child({ props })}
												<a {...props} href={pdf} target="_blank" rel="noopener">
													<DownloadIcon /> Download application
												</a>
											{/snippet}
										</DropdownMenu.Item>
									</DropdownMenu.Content>
								</DropdownMenu.Root>
							{/if}
						{/if}
					{/snippet}
				</DataTable>

				{#if rows.length > 0}
					<PaginationStrip label={range} bind:page {pageCount} />
				{/if}
			</CardContent>
		</Card>
	{/if}
</div>
