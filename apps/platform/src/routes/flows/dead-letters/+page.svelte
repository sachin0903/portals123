<script lang="ts">
	import {
		Badge,
		Button,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState,
		formatDateTime,
		LoadErrorBanner,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import RotateIcon from '@lucide/svelte/icons/rotate-ccw';
	import type { PageData } from './$types';
	import type { DeadLetter } from './+page.server';

	let { data }: { data: PageData } = $props();

	let replaying = $state<string | null>(null);
	function rowKey(kind: string, id: number): string {
		return `${kind}:${id}`;
	}

	const KIND_LABELS: Record<string, string> = {
		outbox: 'Journey action',
		render: 'Document'
	};
	const EVENT_LABELS: Record<string, string> = {
		'task.requested': 'Raise a task',
		'document.render': 'Render a document'
	};

	let sort = $state<DataTableSort | null>(null);
	const desc = { sortable: true, defaultDir: 'desc' } as const;
	const columns: DataTableColumn<DeadLetter>[] = [
		{
			key: 'action',
			header: 'Action',
			sortable: true,
			headerClass: 'w-[36%]',
			sortValue: (row) => row.summary ?? EVENT_LABELS[row.event_type] ?? row.event_type
		},
		{ key: 'status', header: 'Status', headerClass: 'w-[12%]' },
		{ ...desc, key: 'attempts', header: 'Attempts', align: 'center', headerClass: 'w-[12%]' },
		{ ...desc, key: 'created_at', header: 'Raised', headerClass: 'w-[13%]' },
		{
			...desc,
			key: 'last_attempt',
			header: 'Last tried',
			hideBelowWidth: 'sm',
			headerClass: 'w-[13%]'
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[14%] min-w-32' }
	];
</script>

{#snippet deadLetterCell(row: DeadLetter, column: DataTableColumn<DeadLetter>)}
	{#if column.key === 'action'}
		<div class="min-w-0">
			<div class="flex flex-wrap items-center gap-2">
				<span class="truncate text-sm font-medium">
					{row.summary ?? EVENT_LABELS[row.event_type] ?? row.event_type}
				</span>
				<Badge variant="secondary">{KIND_LABELS[row.kind] ?? row.kind}</Badge>
			</div>
			{#if row.last_error}
				<p class="text-destructive mt-1 text-xs break-words">{row.last_error}</p>
			{/if}
		</div>
	{:else if column.key === 'status'}
		<StatusBadge tone="danger">Failed</StatusBadge>
	{:else if column.key === 'attempts'}
		<span class="text-xs tabular-nums">{row.attempts} of {row.max_attempts}</span>
	{:else if column.key === 'created_at' || column.key === 'last_attempt'}
		<span class="text-muted-foreground text-xs">{formatDateTime(row[column.key])}</span>
	{:else if column.key === 'actions'}
		{@const key = rowKey(row.kind, row.id)}
		<form
			method="POST"
			action="?/replay"
			use:enhance={() => {
				replaying = key;
				return async ({ result, update }) => {
					replaying = null;
					if (result.type === 'success') {
						toast.success('Action started again');
						await invalidateAll();
					} else {
						toast.error(
							result.type === 'failure'
								? String(result.data?.error ?? 'Something went wrong.')
								: 'Something went wrong.'
						);
						await update();
					}
				};
			}}
		>
			<input type="hidden" name="kind" value={row.kind} />
			<input type="hidden" name="uid" value={row.uid} />
			<Button type="submit" variant="outline" size="sm" disabled={replaying === key}>
				<RotateIcon class="mr-1.5 size-3.5" />
				{replaying === key ? 'Starting…' : 'Try again'}
			</Button>
		</form>
	{/if}
{/snippet}

<svelte:head><title>Failed journey actions · Platform</title></svelte:head>

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if data.loadFailed}
		<LoadErrorBanner
			message="Failed journey actions couldn’t be loaded. Try again."
			onRetry={() => invalidateAll()}
		/>
	{:else}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				{#if data.rows.length === 0}
					<EmptyState
						icon={CircleCheckIcon}
						title="No failed actions"
						description="Every journey action completed successfully."
						chromeless
						compact
					/>
				{:else}
					<DataTable
						{columns}
						rows={data.rows}
						bind:sort
						getKey={(row) => rowKey(row.kind, row.id)}
						cell={deadLetterCell}
						label="Failed journey actions"
						scroll
						fixed
					/>
				{/if}
			</CardContent>
		</Card>
	{/if}
</div>
