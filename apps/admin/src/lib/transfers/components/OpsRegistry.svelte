<script lang="ts">
	import { Button } from '@graphene/ui';
	import type { TransfersStore } from '$lib/transfers/store.svelte';

	let { wb }: { wb: TransfersStore } = $props();

	const providers = ['Morgan Lloyd', 'SEI', 'Seccl', 'Graphene'] as const;
	const avail = $derived(wb.selected ? wb.availOps(wb.selected) : []);
	const nextId = $derived(wb.selected ? (wb.nextOp(wb.selected)?.id ?? null) : null);
	const grouped = $derived(
		providers
			.map((p) => ({ provider: p, ops: avail.filter((o) => o.provider === p) }))
			.filter((g) => g.ops.length > 0)
	);
</script>

<div class="space-y-3">
	{#if nextId}
		{@const next = avail.find((o) => o.id === nextId)}
		{#if next}
			<div>
				<div class="text-muted-foreground mb-1 text-[11px] font-semibold">Suggested next</div>
				<Button class="w-full justify-between" onclick={() => wb.runOp(next.id)}>
					<span>{next.label}</span>
					<code class="text-background/70 text-[10px]">{next.api}</code>
				</Button>
			</div>
		{/if}
	{/if}
	{#each grouped as group (group.provider)}
		<div>
			<div class="text-muted-foreground mb-1 text-[11px] font-semibold">
				{group.provider}
			</div>
			<div class="flex flex-col gap-1.5">
				{#each group.ops as op (op.id)}
					<Button
						variant={op.id === nextId ? 'secondary' : 'outline'}
						size="sm"
						class="w-full justify-between"
						onclick={() => wb.runOp(op.id)}
					>
						<span>{op.label}</span>
						<code class="text-muted-foreground text-[10px]">{op.api}</code>
					</Button>
				{/each}
			</div>
		</div>
	{:else}
		<p class="text-muted-foreground text-xs">No provider operations available at this stage.</p>
	{/each}
</div>
