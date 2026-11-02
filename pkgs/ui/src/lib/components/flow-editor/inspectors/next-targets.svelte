<!--
	The "then go to" editor — one picker per outgoing wire, plus one to add another.

	`next` is list-valued (flow-contract.json → nodeEnvelope.$comment_nextArity) and several targets
	mean PARALLEL, so a single picker would have quietly hidden every wire but the first the moment
	an author drew a fan-out on the canvas. Written once here rather than three times because the
	wait, wait-for-event and action inspectors all ask the same question, and three copies of an
	arity rule is three chances to get it wrong.

	The sentence under the list is not decoration. "Go to A and B" reads as a choice to most people
	and it is not one — both happen, and what orders them is a prerequisite, not the wire.
-->
<script lang="ts">
	import { Button, Label, SelectMenu } from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import { canonicalNext, nextIds, type FlowNode } from '@graphene/ui/book/journeys';
	import { NO_TARGET, targetId, targetOptions, targetValue } from './shared';

	let {
		node,
		nodes,
		canManage,
		label = 'Then go to',
		onrestructure
	}: {
		node: FlowNode;
		nodes: FlowNode[];
		canManage: boolean;
		label?: string;
		onrestructure: () => void;
	} = $props();

	const targets = $derived(targetOptions(nodes, node.id));
	const current = $derived(nextIds(node));

	function write(ids: string[]) {
		// Blanks filtered, not just de-duplicated: an empty target is a dangling edge, which publish
		// refuses and the canvas cannot draw — it would be an edge the author can neither see nor fix.
		node.next = canonicalNext([...new Set(ids.filter(Boolean))]);
		onrestructure();
	}
	/** Re-aim one wire in place; picking "nothing yet" removes it. */
	function retarget(at: number, value: string) {
		const id = targetId(value);
		const kept = [...current];
		if (id === null) kept.splice(at, 1);
		else kept[at] = id;
		write(kept);
	}
</script>

<div class="space-y-1.5 border-t pt-4">
	<Label>{label}</Label>
	{#if current.length === 0}
		<SelectMenu
			size="sm"
			items={targets}
			value={NO_TARGET}
			disabled={!canManage}
			onValueChange={(v) => retarget(0, v)}
		/>
	{:else}
		{#each current as target, i (target)}
			<div class="flex items-center gap-1.5">
				<div class="min-w-0 flex-1">
					<SelectMenu
						size="sm"
						items={targets}
						value={targetValue(target)}
						disabled={!canManage}
						onValueChange={(v) => retarget(i, v)}
					/>
				</div>
				{#if canManage && current.length > 1}
					<Button
						variant="ghost"
						size="icon-sm"
						title="Remove this path"
						onclick={() => write(current.filter((_, n) => n !== i))}
					>
						<XIcon class="size-3.5" />
					</Button>
				{/if}
			</div>
		{/each}
		{#if canManage}
			<Button
				variant="ghost"
				size="xs"
				class="text-muted-foreground"
				onclick={() => write([...current, targets.find((t) => t.value !== NO_TARGET)?.value ?? ''])}
				disabled={targets.length <= 1}
			>
				<PlusIcon class="size-3.5" /> And also go to…
			</Button>
		{/if}
	{/if}
	{#if current.length > 1}
		<p class="text-muted-foreground text-xs">
			All {current.length} happen at once. To make one wait for another, give it the other as a prerequisite.
		</p>
	{/if}
</div>
