<script lang="ts">
	// A decision's own box inside a card: what was decided (or that nothing has been yet) beside the
	// acts that decide it. One treatment for a match and for an identity check, so an operator finds
	// the decision in the same place on both.
	import type { Snippet } from 'svelte';
	import { StatusBadge, type StatusTone } from '@graphene/ui';

	let {
		label,
		tone,
		children
	}: {
		label: string;
		tone: StatusTone;
		children?: Snippet;
	} = $props();
</script>

<div class="bg-muted/50 space-y-2.5 rounded-md p-3">
	<div class="flex items-center gap-2">
		<span class="text-muted-foreground text-xs font-medium">Decision</span>
		<StatusBadge {tone}>{label}</StatusBadge>
	</div>
	{@render children?.()}
</div>
