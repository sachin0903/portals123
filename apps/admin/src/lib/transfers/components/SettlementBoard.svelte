<script lang="ts">
	import { cn, formatGBP } from '@graphene/ui';
	import { STATUS_BUCKET, STATUS_LABEL } from '$lib/transfers/status';
	import type { TransfersStore } from '$lib/transfers/store.svelte';

	let { wb }: { wb: TransfersStore } = $props();

	const bucketText: Record<string, string> = {
		idle: 'text-muted-foreground',
		live: 'text-info-strong',
		done: 'text-success-strong',
		break: 'text-destructive-strong'
	};
</script>

<div
	class="border-border/70 bg-card flex items-stretch gap-0 overflow-x-auto rounded-xl border p-1.5"
>
	{#each wb.board as cell, i (cell.status)}
		<button
			type="button"
			onclick={() => wb.toggleStage(cell.status)}
			class={cn(
				'relative min-w-[8rem] flex-1 rounded-lg px-3 py-2 text-left transition-colors',
				wb.stageFilter === cell.status ? 'bg-brand/10 ring-1 ring-brand/40' : 'hover:bg-muted/60'
			)}
		>
			{#if i > 0}
				<span class="text-muted-foreground/40 absolute top-1/2 -left-0.5 -translate-y-1/2 text-xs"
					>→</span
				>
			{/if}
			<div
				class={cn(
					'text-2xl font-semibold tracking-tight tabular-nums',
					bucketText[STATUS_BUCKET[cell.status]]
				)}
			>
				{cell.count}
			</div>
			<div class="text-muted-foreground mt-0.5 text-[11.5px] font-semibold">
				{STATUS_LABEL[cell.status]}
			</div>
			<div class="text-muted-foreground/70 font-mono text-[9.5px]">
				{cell.value ? formatGBP(cell.value) : '—'}
			</div>
		</button>
	{/each}
	<div class="relative min-w-[8rem] flex-1 rounded-lg px-3 py-2 text-left">
		<span class="text-muted-foreground/40 absolute top-1/2 -left-0.5 -translate-y-1/2 text-xs"
			>→</span
		>
		<div class="text-destructive-strong text-2xl font-semibold tracking-tight tabular-nums">
			{wb.breaks.length}
		</div>
		<div class="text-destructive mt-0.5 text-[11.5px] font-semibold">Needs attention</div>
		<div class="text-muted-foreground/70 font-mono text-[9.5px]">manual_intervention</div>
	</div>
</div>
