<script lang="ts">
	import { cn } from '@graphene/ui';
	import { PIPELINE, type WorkbenchStore } from '$lib/contributions/store.svelte';

	let { wb }: { wb: WorkbenchStore } = $props();

	const isActive = (statuses: string[]) =>
		!!wb.stageFilter &&
		wb.stageFilter.length === statuses.length &&
		wb.stageFilter.every((s, i) => s === statuses[i]);
</script>

<div
	class="border-border/70 bg-card flex items-stretch gap-0 overflow-x-auto rounded-xl border p-1.5"
>
	{#each PIPELINE as stage, i (stage.key)}
		<button
			type="button"
			onclick={() => wb.toggleStage(stage.statuses)}
			class={cn(
				'relative flex-1 rounded-lg px-3 py-2 text-left transition-colors',
				'min-w-[7rem]',
				isActive(stage.statuses) ? 'bg-brand/10 ring-1 ring-brand/40' : 'hover:bg-muted/60'
			)}
		>
			{#if i > 0}
				<span class="text-muted-foreground/50 absolute top-1/2 -left-0.5 -translate-y-1/2 text-xs"
					>→</span
				>
			{/if}
			<div
				class={cn(
					'text-2xl font-semibold tracking-tight tabular-nums',
					stage.relief ? 'text-brand-strong' : 'text-foreground'
				)}
			>
				{wb.countForStage(stage.statuses)}
			</div>
			<div
				class={cn(
					'mt-0.5 text-[11.5px] font-semibold',
					stage.manual ? 'text-warning-strong' : 'text-muted-foreground'
				)}
			>
				{stage.name}
			</div>
			<div class="text-muted-foreground/70 text-[9.5px] font-medium">{stage.actor}</div>
		</button>
	{/each}
</div>
