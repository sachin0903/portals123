<!--
	A CONDITION — one question, two paths.

	`kind: 'condition'` is a `branch` with a fixed pair of arms (flow-contract.json →
	`controlNodes.condition`), so it is drawn the way a branch is: an outlet per arm, each under its
	own labelled chip, because wiring a path should be a drag from the path you mean. What it adds
	over a branch is that the question itself is on the card — that is the whole reason the node
	exists as its own kind rather than as a two-armed branch an author has to name.

	It was drawn as a chrome-less annotation stacked on top of the work it gated while it WAS one — a
	plan-time gate nothing walked through. It is on the path now, so it has the presence of a
	decision, and the two arms are the paths the user asked to see converge.
-->
<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import FilterIcon from '@lucide/svelte/icons/filter';
	import { branchesOf, conditionsOf } from '@graphene/ui/book/journeys';
	import { CONTROL_ACCENT, conditionSummary, type FlowNodeData } from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const d = $derived(data as FlowNodeData);

	const accent = CONTROL_ACCENT.condition;
	const arms = $derived(branchesOf(d.node));
	const summary = $derived(conditionSummary(conditionsOf(d.node)) ?? 'No conditions yet');
</script>

<div
	class="bg-background relative w-58 rounded-lg border-2 transition-shadow {selected
		? 'ring-ring/50 ring-2'
		: 'hover:shadow-md'}"
	style="border-color:color-mix(in oklab, {accent} 55%, transparent)"
	title="{d.node.title || d.node.id} — {summary}"
>
	<Handle
		type="target"
		position={Position.Top}
		class="!size-2.5 !border-2 !bg-background"
		style="border-color:{accent}"
	/>

	<div class="flex items-center gap-2.5 px-3 pt-2 pb-1.5">
		<span
			class="flex size-7 shrink-0 items-center justify-center rounded-md"
			style="background:color-mix(in oklab, {accent} 14%, transparent); color:{accent}"
		>
			<FilterIcon class="size-4" />
		</span>
		<div class="min-w-0 flex-1">
			<div class="text-[11px] font-semibold" style="color:{accent}">Condition</div>
			<div class="truncate text-sm leading-tight font-semibold">{d.node.title || d.node.id}</div>
			<div class="text-muted-foreground truncate text-[11px] leading-tight">{summary}</div>
		</div>
	</div>

	<div class="flex items-stretch gap-1 px-1.5 pb-2">
		{#each arms as arm, i (arm.id)}
			{@const isElse = arm.conditions === null}
			<div
				class="min-w-0 flex-1 rounded px-1.5 py-1 text-center text-[10px] font-semibold"
				style="background:color-mix(in oklab, {isElse
					? 'var(--muted-foreground)'
					: accent} 12%, transparent); color:{isElse ? 'var(--muted-foreground)' : accent}"
				title={isElse ? 'When the condition does not hold' : 'When the condition holds'}
			>
				<span class="block truncate">{arm.label}</span>
			</div>
			<Handle
				id={arm.id}
				type="source"
				position={Position.Bottom}
				class="!size-2.5 !border-2 !bg-background"
				style="border-color:{isElse ? 'var(--muted-foreground)' : accent}; left:{((i + 0.5) /
					arms.length) *
					100}%"
				title={arm.label}
			/>
		{/each}
	</div>
	<NodeIssueBadge show={d.hasIssues} />
</div>
