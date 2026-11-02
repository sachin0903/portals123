<script lang="ts">
	// Run history from the interlace daemon.
	//
	// The old table was a flat list of ids and statuses, which answers none of the questions you
	// actually have: did the last run work, what is slow, and what failed and why. So: status
	// filters with counts, the failure reason on the row itself, and rows that expand to their
	// per-model step timings (which the daemon publishes on run.succeeded).
	import {
		Badge,
		Button,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		humanise,
		Segmented,
		StatusBadge
	} from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { usePlatformStore } from '$lib/platforms/context';
	import { RUN_TRIGGER_LABEL, STATUS_PULSES, STATUS_TONE } from '$lib/platforms/status';
	import type { Run } from '$lib/platforms/types';

	const holder = usePlatformStore();
	const store = $derived(holder.store);
	const runs = $derived(store.data.runs);

	let filter = $state('all');
	let sort = $state<DataTableSort | null>({ key: 'started', dir: 'desc' });
	// Which rows are expanded. A Set so several can be open at once.
	let open = $state(new Set<string>());

	let cancelling = $state<string | null>(null);

	async function cancel(id: string) {
		if (cancelling) return;
		cancelling = id;
		try {
			const daemonId = id.replace(/^r_/, '');
			const res = await fetch(`/platforms/${store.data.meta.provider}/runs/${daemonId}/cancel`, {
				method: 'POST'
			});
			if (!res.ok) return;
			const body = (await res.json()) as { state?: string };
			const run = store.data.runs.find((r) => r.id === id);
			if (run && body.state === 'cancelled') run.status = 'cancelled';
			else if (run && body.state === 'cancelling') run.status = 'cancelling';
		} finally {
			cancelling = null;
		}
	}

	function toggle(id: string) {
		const next = new Set(open);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		open = next;
	}

	const inFlight = (run: Run) => run.status === 'running' || run.status === 'queued';
	const stale = (run: Run) => (run.unknownModels?.length ?? 0) > 0;
	const counts = $derived({
		all: runs.length,
		failed: runs.filter((r) => r.status === 'failed').length,
		running: runs.filter(inFlight).length,
		stale: runs.filter(stale).length
	});
	const rows = $derived(
		runs.filter((r) =>
			filter === 'all'
				? true
				: filter === 'failed'
					? r.status === 'failed'
					: filter === 'stale'
						? stale(r)
						: inFlight(r)
		)
	);

	/** "1m 2s" / "5.3s" -> seconds, so the column sorts by magnitude rather than by string. */
	function durationSeconds(label: string | null): number | null {
		if (!label) return null;
		const minutes = /(\d+)m/.exec(label);
		const seconds = /([\d.]+)s/.exec(label);
		return (minutes ? Number(minutes[1]) * 60 : 0) + (seconds ? Number(seconds[1]) : 0);
	}

	const columns: DataTableColumn<Run>[] = [
		{ key: 'expand', header: '', headerClass: 'w-8' },
		{ key: 'status', header: 'Status', sortable: true, headerClass: 'w-[14%]' },
		{
			key: 'models',
			header: 'Models',
			sortable: true,
			sortValue: (r) => r.models[0] ?? '',
			headerClass: 'w-[24%]'
		},
		{ key: 'trigger', header: 'Trigger', sortable: true, hideBelow: 'md', headerClass: 'w-[14%]' },
		{
			key: 'started',
			header: 'Started',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (r) => r.startedAt ?? r.started,
			headerClass: 'w-[18%]'
		},
		{
			key: 'duration',
			header: 'Duration',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (r) => durationSeconds(r.duration),
			headerClass: 'w-[16%]'
		},
		{
			key: 'steps',
			header: 'Steps',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (r) => r.steps.length,
			align: 'right',
			hideBelow: 'md',
			headerClass: 'w-[14%]'
		}
	];
</script>

<div class="space-y-5">
	<div class="flex flex-wrap items-center gap-2">
		<Segmented
			bind:value={filter}
			options={[
				{ value: 'all', label: `All ${counts.all}` },
				{ value: 'running', label: `In flight ${counts.running}` },
				{ value: 'failed', label: `Failed ${counts.failed}` },
				{ value: 'stale', label: `Built nothing ${counts.stale}` }
			]}
		/>
		<span class="text-muted-foreground ml-auto text-sm">{rows.length} of {runs.length} runs</span>
	</div>

	<Card glass>
		<CardContent>
			<DataTable
				{columns}
				{rows}
				bind:sort
				getKey={(r) => r.id}
				fixed
				empty={runs.length === 0
					? 'No runs have started for this platform.'
					: 'No runs match this filter.'}
				rowClass={(r) =>
					r.status === 'failed' ? 'bg-destructive/5' : stale(r) ? 'bg-warning/5' : undefined}
			>
				{#snippet cell(run, column)}
					{#if column.key === 'expand'}
						{#if run.steps.length}
							<Button
								variant="ghost"
								size="icon-sm"
								onclick={() => toggle(run.id)}
								aria-label="Show steps for run {run.id}"
							>
								{#if open.has(run.id)}
									<ChevronDownIcon class="size-4" />
								{:else}
									<ChevronRightIcon class="size-4" />
								{/if}
							</Button>
						{/if}
					{:else if column.key === 'status'}
						<span class="inline-flex items-center gap-2">
							<StatusBadge tone={STATUS_TONE[run.status]} pulse={STATUS_PULSES.has(run.status)}
								>{humanise(run.status)}</StatusBadge
							>
							{#if inFlight(run)}
								<Button
									variant="outline"
									size="sm"
									disabled={cancelling === run.id}
									onclick={() => cancel(run.id)}
								>
									{cancelling === run.id ? 'Cancelling…' : 'Cancel'}
								</Button>
							{/if}
						</span>
					{:else if column.key === 'models'}
						<span class="block min-w-0">
							<span class="flex min-w-0 items-baseline gap-1.5">
								{#if run.models.length > 1 && run.models.length === store.data.models.length}
									<span class="truncate text-xs">all {run.models.length} models</span>
								{:else}
									<span class="truncate font-mono text-xs">{run.models[0] ?? '—'}</span>
									{#if run.models.length > 1}
										<span class="text-muted-foreground shrink-0 text-xs"
											>+{run.models.length - 1} more</span
										>
									{/if}
								{/if}
								{#if (run.attempts ?? 1) > 1}
									<span class="text-muted-foreground shrink-0 text-xs"
										>· {run.attempts} attempts</span
									>
								{/if}
							</span>
							{#if run.error}
								<!-- The failure reason belongs on the row, not behind a click. -->
								<span class="text-destructive mt-0.5 block text-xs">{run.error}</span>
							{/if}
							{#if stale(run)}
								<!-- Green but empty: the selector names nothing the daemon builds. -->
								<span class="text-warning-strong mt-0.5 block text-xs">
									No matching data set: {run.unknownModels?.join(', ')}
								</span>
							{/if}
						</span>
					{:else if column.key === 'trigger'}
						<Badge variant="outline" class="font-normal">{RUN_TRIGGER_LABEL[run.trigger]}</Badge>
					{:else if column.key === 'started'}
						<span class="whitespace-nowrap tabular-nums">{run.started}</span>
					{:else if column.key === 'duration'}
						<span class="tabular-nums">{run.duration ?? '—'}</span>
					{:else if column.key === 'steps'}
						<span class="text-muted-foreground tabular-nums">{run.steps.length}</span>
					{/if}
				{/snippet}
			</DataTable>

			<!-- Steps render below the table rather than as injected rows: DataTable owns one row per
			     item, so an expanded panel keyed by run id keeps that contract intact. -->
			{#each rows.filter((r) => open.has(r.id)) as run (run.id)}
				<div class="bg-muted/30 mt-3 rounded-lg border p-3">
					<p class="text-muted-foreground mb-2 text-xs font-medium">
						Run {run.id} · steps
					</p>
					<ul class="space-y-1">
						{#each run.steps as step (step.model)}
							<li class="flex items-center gap-2 text-xs">
								<StatusBadge tone={STATUS_TONE[step.status]} pulse={STATUS_PULSES.has(step.status)}
									>{humanise(step.status)}</StatusBadge
								>
								<span class="min-w-0 flex-1 truncate font-mono">
									{step.model}
									{#if step.known === false}
										<span class="text-warning-strong">· unknown model</span>
									{/if}
								</span>
								{#if step.output}
									<Badge variant="outline" class="font-mono font-normal">{step.output}</Badge>
								{/if}
								{#if step.strategy}
									<span class="text-muted-foreground w-24 truncate font-mono">{step.strategy}</span>
								{/if}
								<span class="text-muted-foreground hidden max-w-64 truncate font-mono md:inline">
									{step.dependsOn?.length ? step.dependsOn.join(', ') : '—'}
								</span>
								<span class="w-16 text-right tabular-nums">{step.duration ?? '—'}</span>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</CardContent>
	</Card>
</div>
