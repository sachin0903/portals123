<script lang="ts">
	import { Badge } from '../ui/badge/index.js';
	import Sparkline from '../charts/sparkline.svelte';
	import { formatGBP, formatPct, gainTone } from '../../money.js';
	import { cn } from '../../utils.js';
	import type { Wrapper } from './types.js';

	let { wrapper }: { wrapper: Wrapper } = $props();

	const tone = $derived(gainTone(wrapper.gainPct == null ? null : wrapper.gain));
	const allowancePct = $derived(
		wrapper.allowanceLimit
			? Math.min(100, Math.round(((wrapper.allowanceUsed ?? 0) / wrapper.allowanceLimit) * 100))
			: null
	);
</script>

<a
	href="/holdings?wrapper={wrapper.type}"
	class="glass-panel hover:border-ring/40 block rounded-xl p-5 transition-colors"
>
	<div class="flex items-center justify-between">
		<Badge variant="secondary" class="font-medium">{wrapper.type}</Badge>
		<span class={cn('inline-block', tone)}>
			<Sparkline data={wrapper.trend} class="h-5 w-16" />
		</span>
	</div>
	<p class="text-muted-foreground mt-3 text-sm">{wrapper.name}</p>
	<p class="text-xl font-semibold tabular-nums">{formatGBP(wrapper.value)}</p>
	<p class={cn('text-sm font-medium tabular-nums', tone)}>
		{wrapper.gainPct == null ? '—' : formatPct(wrapper.gainPct)}
	</p>
	{#if allowancePct !== null}
		<div class="mt-4">
			<div class="text-muted-foreground flex justify-between text-xs">
				<span>{wrapper.type} allowance</span>
				<span>{allowancePct}%</span>
			</div>
			<div class="bg-muted mt-1 h-1.5 overflow-hidden rounded-full">
				<div class="bg-brand h-full rounded-full" style="width:{allowancePct}%"></div>
			</div>
		</div>
	{/if}
</a>
