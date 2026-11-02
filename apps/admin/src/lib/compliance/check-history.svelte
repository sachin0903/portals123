<script lang="ts">
	// A check's own trail — when it started, every console act on it, every ComplyCube event — folded
	// away under its card: read when a decision needs its background, not on every visit.
	import { Collapsible } from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { dateTime, type AmlCheckDetail } from '$lib/aml';

	let { history }: { history: AmlCheckDetail['history'] } = $props();
</script>

<Collapsible.Root class="group/history">
	<Collapsible.Trigger
		class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex cursor-pointer items-center gap-1 rounded-md text-xs font-medium outline-none focus-visible:ring-2"
	>
		History <span class="tabular-nums">({history.length})</span>
		<ChevronDownIcon
			class="size-3.5 motion-toggle transition-transform group-data-[state=closed]/history:-rotate-90"
		/>
	</Collapsible.Trigger>
	<Collapsible.Content>
		{#if history.length === 0}
			<p class="text-muted-foreground mt-3 text-sm">Nothing recorded yet.</p>
		{:else}
			<ol class="mt-3 space-y-3 text-sm">
				{#each history as entry, i (i)}
					<li class="flex flex-wrap gap-x-3">
						<span class="text-muted-foreground w-36 shrink-0 tabular-nums">
							{dateTime(entry.at)}
						</span>
						<span class="min-w-0 flex-1">
							<span class="font-medium">{entry.action}</span>
							{#if entry.actor}<span class="text-muted-foreground"> · {entry.actor}</span>{/if}
							{#if entry.detail}<span class="text-muted-foreground block">{entry.detail}</span>{/if}
						</span>
					</li>
				{/each}
			</ol>
		{/if}
	</Collapsible.Content>
</Collapsible.Root>
