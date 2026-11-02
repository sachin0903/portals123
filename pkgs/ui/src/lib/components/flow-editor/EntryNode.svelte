<!--
	Where the journey begins — drawn for BOTH the derived entry marker and a real `start` node.

	One component on purpose. They are the same idea at two levels of commitment: a graph with no
	start node has an entry anyway (the node nothing points at) and the marker names it; a graph that
	fans out at the top needs that beginning to be a real node with a real socket, because a wire has
	to hang off something the document can hold. Drawing them differently would make converting one
	into the other look like a change to the journey, when it is only a change to how precisely the
	journey says where it starts.

	`real` is the difference: the marker is a label with no outlet (wiring from it would author an
	edge with no field to live in), the start node has the outlet and carries its own detail line.
-->
<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import PlayIcon from '@lucide/svelte/icons/play';
	import { OUT_HANDLE } from '@graphene/ui/book/journeys';
	import { detailForNode, type FlowNodeData, type MarkerNodeData } from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const node = $derived((data as Partial<FlowNodeData>).node ?? null);
	const title = $derived(node ? node.title || 'Journey starts' : (data as MarkerNodeData).title);
	const detail = $derived(node ? detailForNode(node, (data as FlowNodeData).lookups) : null);
</script>

<div
	class="relative flex items-center gap-2 rounded-full px-4 py-2 shadow-md transition-shadow
		{selected ? 'ring-ring/50 ring-2' : ''}"
	style="background: var(--hero-base); color: var(--hero-foreground)"
>
	<span class="flex size-5 shrink-0 items-center justify-center rounded-full bg-current/15">
		<PlayIcon class="size-3" />
	</span>
	<span class="truncate text-xs font-semibold">{title}</span>
	{#if detail}
		<span class="truncate text-[11px] opacity-70">· {detail}</span>
	{/if}
	<Handle
		id={OUT_HANDLE}
		type="source"
		position={Position.Bottom}
		class="!size-2.5 !border-2 !border-current/70 !bg-current"
	/>
	{#if node}
		<NodeIssueBadge show={(data as FlowNodeData).hasIssues} />
	{/if}
</div>
