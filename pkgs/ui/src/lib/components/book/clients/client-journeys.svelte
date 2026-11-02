<script lang="ts">
	// This client's journeys — the record's "Journeys" section, on the same listing frame as the
	// rest of the book: a glass card, the shared toolbar, the shared table.
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import RouteIcon from '@lucide/svelte/icons/route';
	import { formatDate, formatDateTime } from '../../../dates.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import { search } from '../listing/filters.js';
	import { stateLabel, stateTone } from '../journeys/applications.js';
	import type { ClientJourneySummary } from './client-journey.js';

	let {
		journeys,
		href,
		newJourneyHref = undefined,
		newJourneys = []
	}: {
		journeys: ClientJourneySummary[];
		/** This journey, read on this client's record. */
		href: (uid: string) => string;
		/** Where "New journey" goes when the kickoff asks for the flow itself (the admin's form). */
		newJourneyHref?: string;
		/** The published flows this reader may start for this client. Each already points at the
		 *  kickoff with the flow and the client chosen, so "New journey" is a pick, not a page. */
		newJourneys?: { code: string; name: string; href: string }[];
	} = $props();

	const canStart = $derived(newJourneys.length > 0 || !!newJourneyHref);

	let query = $state('');
	let sort = $state<DataTableSort | null>({ key: 'started', dir: 'desc' });

	function pct(j: ClientJourneySummary): number {
		return j.steps_total ? Math.round((j.steps_done / j.steps_total) * 100) : 0;
	}

	const rows = $derived(
		journeys.filter((j) => search(query, j.flow_name, j.uid, j.status, stateLabel(j.status)))
	);

	const columns: DataTableColumn<ClientJourneySummary>[] = [
		{
			key: 'journey',
			header: 'Journey',
			sortable: true,
			headerClass: 'w-[40%]',
			cellClass: 'truncate',
			sortValue: (j) => j.flow_name
		},
		{
			key: 'started',
			header: 'Started',
			sortable: true,
			defaultDir: 'desc',
			headerClass: 'w-[18%]',
			sortValue: (j) => new Date(j.created_at)
		},
		{
			key: 'progress',
			header: 'Progress',
			sortable: true,
			headerClass: 'w-[26%]',
			sortValue: pct
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[16%]',
			sortValue: (j) => stateLabel(j.status)
		}
	];
</script>

{#snippet newJourney()}
	{#if newJourneys.length}
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
				{#each newJourneys as flow (flow.code)}
					<DropdownMenu.Item>
						{#snippet child({ props })}
							<a {...props} href={flow.href}>{flow.name}</a>
						{/snippet}
					</DropdownMenu.Item>
				{/each}
			</DropdownMenu.Content>
		</DropdownMenu.Root>
	{:else}
		<Button href={newJourneyHref}>
			<PlusIcon class="size-4" /> New journey
		</Button>
	{/if}
{/snippet}

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if journeys.length === 0}
		<EmptyState
			icon={RouteIcon}
			title="No journeys yet"
			description="A journey started for this client appears here."
			action={canStart ? newJourney : undefined}
		/>
	{:else}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search journeys…"
					dirty={query.trim() !== ''}
					onclear={() => (query = '')}
					actions={canStart ? newJourney : undefined}
				/>
				<DataTable
					{columns}
					{rows}
					bind:sort
					scroll
					fixed
					getKey={(j) => j.uid}
					label="Journeys"
					empty="No journeys match your search."
				>
					{#snippet cell(j, column)}
						{#if column.key === 'journey'}
							<TruncatedText text={j.flow_name} href={href(j.uid)} class="font-medium" />
						{:else if column.key === 'started'}
							<span class="whitespace-nowrap tabular-nums" title={formatDateTime(j.created_at)}>
								{formatDate(j.created_at)}
							</span>
						{:else if column.key === 'progress'}
							{#if !j.steps_total}
								<span class="text-muted-foreground text-xs">Not started</span>
							{:else}
								<div class="flex items-center gap-2.5" title={`${pct(j)}% complete`}>
									<div
										class="bg-muted h-1.5 min-w-0 flex-1 overflow-hidden rounded-full"
										role="progressbar"
										aria-label="Journey progress"
										aria-valuemin="0"
										aria-valuemax="100"
										aria-valuenow={pct(j)}
										aria-valuetext={`${j.steps_done} of ${j.steps_total} steps · ${pct(j)}%`}
									>
										<div class="bg-primary h-full rounded-full" style:width={`${pct(j)}%`}></div>
									</div>
									<span
										class="text-muted-foreground min-w-10 shrink-0 text-right text-xs whitespace-nowrap tabular-nums"
									>
										{j.steps_done}/{j.steps_total}
									</span>
								</div>
							{/if}
						{:else if column.key === 'status'}
							<StatusBadge tone={stateTone(j.status)}>{stateLabel(j.status)}</StatusBadge>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	{/if}
</div>
