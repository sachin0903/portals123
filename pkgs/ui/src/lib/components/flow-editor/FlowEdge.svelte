<!--
	An authored wire.

	Three things a plain edge cannot do, all of which the builder needs:
	  • RECONNECT — drag either end onto a different node. The anchors are xyflow's own, which means
	    a re-pointed edge arrives as `onreconnect` with both the old edge and the new connection, so
	    the canvas can move the authored socket rather than deleting and re-adding one.
	  • DISCONNECT — an explicit ✕ on the selected edge. Backspace works too, but a wire you can see
	    should be a wire you can cut without knowing a keyboard shortcut.
	  • A readable LABEL — a branch arm's name, or "if it times out", sat on the wire rather than
	    inside the node, because the label belongs to the path and not to the question.
-->
<script lang="ts">
	import {
		BaseEdge,
		EdgeLabel,
		EdgeReconnectAnchor,
		getSmoothStepPath,
		type EdgeProps
	} from '@xyflow/svelte';
	import { Button } from '@graphene/ui';
	import XIcon from '@lucide/svelte/icons/x';
	import type { FlowEdgeRef } from '@graphene/ui/book/journeys';
	import { flowEditorContext } from './context';

	let {
		sourceX,
		sourceY,
		targetX,
		targetY,
		sourcePosition,
		targetPosition,
		markerEnd,
		style,
		label,
		selected,
		data
	}: EdgeProps = $props();

	const ctx = flowEditorContext();
	const ref = $derived((data as { ref?: FlowEdgeRef } | undefined)?.ref ?? null);

	const geometry = $derived(
		getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
	);
	const path = $derived(geometry[0]);
	const labelX = $derived(geometry[1]);
	const labelY = $derived(geometry[2]);
</script>

<BaseEdge {path} {markerEnd} {style} interactionWidth={18} />

{#if label}
	<EdgeLabel x={labelX} y={labelY} class="pointer-events-none">
		<span
			class="bg-background text-muted-foreground rounded border px-1.5 py-0.5 text-[10px]
				font-semibold"
		>
			{label}
		</span>
	</EdgeLabel>
{/if}

{#if ctx.canManage}
	<EdgeReconnectAnchor type="source" position={{ x: sourceX, y: sourceY }} />
	<EdgeReconnectAnchor type="target" position={{ x: targetX, y: targetY }} />
	{#if selected && ref}
		<EdgeLabel x={labelX} y={labelY + (label ? 18 : 0)}>
			<Button
				variant="outline"
				size="icon-xs"
				class="text-destructive hover:bg-destructive hover:text-destructive-foreground
					rounded-full shadow-sm"
				aria-label="Disconnect"
				title="Disconnect"
				onclick={() => ctx.disconnect(ref)}
			>
				<XIcon class="size-3" />
			</Button>
		</EdgeLabel>
	{/if}
{/if}
