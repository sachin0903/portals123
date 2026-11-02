<!--
	A BRANCH — the first-class fan-out v3 added, and the node the old canvas faked.

	One outlet per arm, each sitting under its own labelled chip, because wiring an arm should be a
	drag from the arm you mean and not a dropdown in a panel: the whole point of a graph is that the
	shape you see is the shape that runs. Arms are evaluated top-to-bottom (left-to-right here) and
	the first satisfiable one wins, so the ELSE is drawn last and marked — it is the fallback
	wherever the author left it in the list, and a fan-out without one is what publish refuses.
-->
<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import GitBranchIcon from '@lucide/svelte/icons/git-branch';
	import { branchesOf } from '@graphene/ui/book/journeys';
	import { CONTROL_ACCENT, conditionSummary, type FlowNodeData } from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const d = $derived(data as FlowNodeData);

	const accent = CONTROL_ACCENT.branch;
	const arms = $derived(branchesOf(d.node));
	/** Mirrors `nodeSize` in graph.ts — the layout has to reserve exactly what this draws. */
	const width = $derived(Math.max(240, arms.length * 116));
	const armTitle = (conditions: unknown, isElse: boolean) =>
		isElse
			? 'Every case that matched nothing above'
			: (conditionSummary(conditions) ?? 'No condition set');
</script>

<div
	class="bg-background relative rounded-lg border-2 transition-shadow {selected
		? 'ring-ring/50 ring-2'
		: 'hover:shadow-md'}"
	style="width:{width}px; border-color:color-mix(in oklab, {accent} 55%, transparent)"
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
			<GitBranchIcon class="size-4" />
		</span>
		<div class="min-w-0 flex-1">
			<div class="text-[11px] font-semibold" style="color:{accent}">Branch</div>
			<div class="truncate text-sm leading-tight font-semibold">{d.node.title || d.node.id}</div>
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
				title={armTitle(arm.conditions, isElse)}
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
		{#if !arms.length}
			<p class="text-muted-foreground px-1.5 py-1 text-[11px]">No paths yet — add them below.</p>
		{/if}
	</div>
	<NodeIssueBadge show={d.hasIssues} />
</div>
