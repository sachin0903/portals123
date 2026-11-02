<script lang="ts">
	import { cn } from '@graphene/ui';
	import { ROUTES } from '$lib/transfers/status';
	import type { Transfer } from '$lib/transfers/types';

	let { transfer }: { transfer: Transfer } = $props();

	const stages = $derived(ROUTES[transfer.route].stages);
	const broken = $derived(transfer.status === 'manual_intervention');
	const currentIdx = $derived(stages.findIndex((s) => s.status === transfer.status));
</script>

<div class="flex items-stretch gap-0 overflow-x-auto">
	{#each stages as stage, i (stage.status)}
		{@const done = !broken && currentIdx > -1 && i < currentIdx}
		{@const current = !broken && i === currentIdx}
		<div class="relative min-w-[6.5rem] flex-1 px-2 py-1.5">
			{#if i > 0}
				<span class="text-muted-foreground/40 absolute top-1/2 -left-0.5 -translate-y-1/2 text-xs"
					>→</span
				>
			{/if}
			<div class="flex items-center gap-1.5">
				<span
					class={cn(
						'size-2 rounded-full',
						done ? 'bg-success' : current ? 'bg-info' : 'bg-muted-foreground/40'
					)}
				></span>
				<span
					class={cn(
						'text-[11px] font-semibold',
						current ? 'text-info-strong' : done ? 'text-foreground' : 'text-muted-foreground'
					)}>{stage.label}</span
				>
			</div>
			<div class="text-muted-foreground/70 mt-0.5 pl-3.5 text-[9.5px]">{stage.provider}</div>
		</div>
	{/each}
	{#if broken}
		<div class="min-w-[6.5rem] flex-1 px-2 py-1.5">
			<div class="flex items-center gap-1.5">
				<span class="bg-destructive size-2 rounded-full"></span>
				<span class="text-destructive-strong text-[11px] font-semibold">Needs attention</span>
			</div>
			<div class="text-muted-foreground/70 mt-0.5 pl-3.5 text-[9.5px]">Graphene</div>
		</div>
	{/if}
</div>
