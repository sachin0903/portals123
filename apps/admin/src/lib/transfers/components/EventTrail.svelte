<script lang="ts">
	import { cn } from '@graphene/ui';
	import type { Transfer } from '$lib/transfers/types';

	let { transfer }: { transfer: Transfer } = $props();

	const dot: Record<string, string> = {
		'': 'bg-muted-foreground/40',
		acc: 'bg-info',
		bad: 'bg-destructive',
		good: 'bg-success'
	};
	const events = $derived([...transfer.events].reverse());
</script>

<ul class="space-y-2">
	{#each events as e, i (i)}
		<li class="flex gap-2.5">
			<span class={cn('mt-1 size-2 shrink-0 rounded-full', dot[e.kind])}></span>
			<div class="min-w-0">
				<div class="text-xs">
					<code class="text-[10.5px]">{e.code}</code>
					<span class="text-muted-foreground/60 ml-1.5">{e.ts}</span>
				</div>
				<div class="text-muted-foreground text-[11px]">{e.det}</div>
			</div>
		</li>
	{/each}
</ul>
