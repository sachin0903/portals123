<!--
	The linear control kinds — wait, wait for event, start another flow, end.

	These are drawn as OUTLINED surfaces on the canvas background rather than as the filled cards the
	work nodes use, and that distinction is load-bearing: control flow is not somebody's to-do, and
	giving a seven-day pause the same solid presence as a form the client has to fill in is exactly
	what made the pre-v3 pseudo-decision node read as work. Accent, icon and border carry the kind;
	the fill stays the page.

	`wait_for_event` gets a second outlet — the deadline — drawn dashed and labelled, because "no
	signed form in seven days → chase" is one node and the escape is the reason it exists.
-->
<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import EarIcon from '@lucide/svelte/icons/ear';
	import CornerDownRightIcon from '@lucide/svelte/icons/corner-down-right';
	import CircleStopIcon from '@lucide/svelte/icons/circle-stop';
	import {
		NODE_KIND_LABEL,
		OUT_HANDLE,
		TIMEOUT_HANDLE,
		type FlowNodeKind
	} from '@graphene/ui/book/journeys';
	import { CONTROL_ACCENT, detailForNode, type FlowNodeData } from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const d = $derived(data as FlowNodeData);

	const ICONS: Record<string, typeof ClockIcon> = {
		wait: ClockIcon,
		wait_for_event: EarIcon,
		start_flow: CornerDownRightIcon,
		exit: CircleStopIcon
	};

	const kind = $derived(d.node.kind as FlowNodeKind);
	const accent = $derived(CONTROL_ACCENT[kind] ?? 'var(--muted-foreground)');
	const Icon = $derived(ICONS[kind] ?? ClockIcon);
	const detail = $derived(detailForNode(d.node, d.lookups));
	const hasTimeout = $derived(kind === 'wait_for_event');
</script>

<div
	class="bg-background relative flex items-center gap-2.5 rounded-lg border-2 px-3 py-2
		transition-shadow {selected ? 'ring-ring/50 ring-2' : 'hover:shadow-md'}"
	class:w-56={!hasTimeout}
	class:w-60={hasTimeout}
	style="border-color:color-mix(in oklab, {accent} 55%, transparent)"
>
	<Handle
		type="target"
		position={Position.Top}
		class="!size-2.5 !border-2 !bg-background"
		style="border-color:{accent}"
	/>

	<span
		class="flex size-7 shrink-0 items-center justify-center rounded-md"
		style="background:color-mix(in oklab, {accent} 14%, transparent); color:{accent}"
	>
		<Icon class="size-4" />
	</span>
	<div class="min-w-0 flex-1">
		<div class="text-[11px] font-semibold" style="color:{accent}">
			{NODE_KIND_LABEL[kind]}
		</div>
		<div class="truncate text-sm leading-tight font-semibold">{d.node.title || d.node.id}</div>
		<div class="text-muted-foreground truncate text-[11px]" title={detail}>{detail}</div>
	</div>

	{#if kind !== 'exit' && kind !== 'start_flow'}
		<Handle
			id={OUT_HANDLE}
			type="source"
			position={Position.Bottom}
			class="!size-2.5 !border-2 !bg-background"
			style="border-color:{accent}; {hasTimeout ? 'left:33%' : ''}"
		/>
	{/if}
	{#if hasTimeout}
		<!-- The deadline outlet, kept visually apart from the ordinary one so wiring the wrong escape
		     is a mistake you can see rather than one you discover at 3am in a client's journey. -->
		<Handle
			id={TIMEOUT_HANDLE}
			type="source"
			position={Position.Bottom}
			class="!size-2.5 !border-2 !border-dashed !bg-background"
			style="border-color:var(--warning); left:75%"
			title="Where the journey goes if the deadline passes"
		/>
	{/if}
	<NodeIssueBadge show={d.hasIssues} />
</div>
