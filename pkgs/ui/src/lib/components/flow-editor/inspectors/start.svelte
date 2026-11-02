<!--
	The start inspector — where the journey begins, and everything that begins with it.

	It is almost nothing but the "goes to" list, and that is the whole node: a start carries no
	config (controlNodes.start.config is []) and exists purely so a journey can open on several
	things at once. Before it there was no node to fan out FROM, and "the first node nothing points
	at" cannot name two roots — which is why a legacy tier, where five terms declarations are offered
	together, had no v3 spelling at all.
-->
<script lang="ts">
	import { Input, Label } from '@graphene/ui';
	import NextTargets from './next-targets.svelte';
	import { nextIds } from '@graphene/ui/book/journeys';
	import type { InspectorProps } from './types';

	let { node, nodes, canManage, onchange, onrestructure }: InspectorProps = $props();

	const fan = $derived(nextIds(node).length);
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="start-title">What this beginning is called</Label>
		<Input
			id="start-title"
			value={node.title}
			disabled={!canManage}
			placeholder="Journey starts"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<p class="text-muted-foreground text-sm">
		{#if fan > 1}
			The journey opens on {fan} things at once — nobody waits for anybody.
		{:else if fan === 1}
			The journey opens on one thing. Add another to have them start together.
		{:else}
			Nothing starts here yet, so the journey would never begin.
		{/if}
	</p>

	<NextTargets {node} {nodes} {canManage} {onrestructure} label="Begins with" />
</div>
