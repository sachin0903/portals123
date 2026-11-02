<!--
	The right-hand panel: one chrome for every kind of node.

	It owns the header (what you are editing, delete, close) and the scrolling body, and dispatches
	the body itself — a step to `StepInspector`, everything else to `NodeInspector`, which fans out
	per kind. The split exists because a step's inspector predates v3 and is large; folding the two
	together is a later tidy-up, not a prerequisite (see inspectors/node-inspector.svelte).

	The header is here rather than in each inspector so that "Delete" is in the same place, does the
	same thing and reads the same way whichever node is selected — including the ones C2 is writing,
	which therefore do not have to think about it at all.
-->
<script lang="ts">
	import { Alert, AlertDescription, Button } from '@graphene/ui';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import {
		NODE_KIND_LABEL,
		STEP_TYPE_LABELS,
		type FlowNode,
		type FlowStepType,
		type FlowForm,
		type FlowDocument,
		type FlowTemplate
	} from '@graphene/ui/book/journeys';
	import NodeInspector from './inspectors/node-inspector.svelte';
	import type { FlowHandoffTarget } from './inspectors/types';
	import StepInspector from './StepInspector.svelte';

	let {
		node,
		nodes,
		forms,
		documents,
		templates,
		terms = [],
		roles = [],
		flows = [],
		issues = [],
		formFieldsEndpoint = '/flows/form-fields',
		canManage,
		onchange,
		onrestructure,
		ondelete,
		onclose
	}: {
		node: FlowNode;
		nodes: FlowNode[];
		forms: FlowForm[];
		documents: FlowDocument[];
		templates: FlowTemplate[];
		terms?: { uid: string; title: string }[];
		roles?: { slug: string; name: string }[];
		flows?: FlowHandoffTarget[];
		/** This node's publish issues, in the engine's own words. */
		issues?: string[];
		formFieldsEndpoint?: string;
		canManage: boolean;
		onchange: () => void;
		onrestructure: () => void;
		ondelete: () => void;
		onclose: () => void;
	} = $props();

	const heading = $derived(
		node.kind === 'step'
			? (STEP_TYPE_LABELS[node.type as FlowStepType] ?? 'Step')
			: NODE_KIND_LABEL[node.kind]
	);
</script>

<div class="bg-background flex h-full w-80 shrink-0 flex-col border-l">
	<div class="flex items-center justify-between border-b px-4 py-3">
		<div class="min-w-0">
			<p class="text-muted-foreground text-[11px] font-semibold">
				{heading}
			</p>
			<h2 class="truncate text-sm font-semibold">{node.title || node.id}</h2>
		</div>
		<div class="flex shrink-0 items-center gap-1">
			{#if canManage}
				<Button
					variant="ghost"
					size="icon-sm"
					class="text-muted-foreground hover:text-destructive"
					aria-label="Delete this node"
					onclick={ondelete}
				>
					<Trash2Icon class="size-4" />
				</Button>
			{/if}
			<Button
				variant="ghost"
				size="icon-sm"
				class="text-muted-foreground"
				aria-label="Close"
				onclick={onclose}
			>
				<XIcon class="size-4" />
			</Button>
		</div>
	</div>

	<div class="flex-1 overflow-y-auto p-4">
		{#if node.kind === 'step'}
			<!-- NodeInspector renders the issue list itself for the kinds it owns; the step inspector
			     predates that contract, so its issues are rendered here. -->
			{#if issues.length}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>
						<ul class="list-inside list-disc space-y-1 text-sm">
							{#each issues as issue (issue)}
								<li>{issue}</li>
							{/each}
						</ul>
					</AlertDescription>
				</Alert>
			{/if}
			<StepInspector
				{node}
				{nodes}
				{forms}
				{documents}
				{templates}
				{terms}
				{roles}
				{formFieldsEndpoint}
				{canManage}
				{onchange}
				{onrestructure}
			/>
		{:else}
			<NodeInspector
				{node}
				{nodes}
				{forms}
				{documents}
				{templates}
				{roles}
				{flows}
				{issues}
				{canManage}
				{onchange}
				{onrestructure}
			/>
		{/if}
	</div>
</div>
