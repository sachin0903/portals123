<!--
	The palette — everything an author can add to a journey, in one rail.

	Each entry is BOTH draggable and clickable, on purpose: dragging is how you aim (onto a wire to
	splice, onto empty canvas to place), clicking is how you add without aiming, and forcing the
	drag would make the builder unusable for anyone who cannot do a precise pointer gesture. The
	drag payload rides in component state rather than in `dataTransfer`, because a dragover handler
	is not allowed to read the transfer data — and the canvas needs to know what is coming while it
	is still deciding which wire to highlight.
-->
<script lang="ts" module>
	import type { FlowNodeKind } from '@graphene/ui/book/journeys';

	/** What a palette entry adds. `type` is the step/action type; absent for a control kind. */
	export interface PaletteItem {
		kind: FlowNodeKind;
		type?: string;
		label: string;
		hint?: string;
		/** The swatch colour — the same token family the node itself will wear. */
		swatch: string;
	}
</script>

<script lang="ts">
	import { Button, Collapsible } from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import {
		NODE_KIND_HINT,
		NODE_KIND_LABEL,
		STEP_TYPE_LABELS,
		type FlowStepType
	} from '@graphene/ui/book/journeys';
	import { ACTOR_STYLE, CONTROL_ACCENT, CATEGORY_STYLE, categoryOf } from './graph';

	let {
		actions,
		canManage,
		canAddStart = false,
		onadd,
		ondragstartitem,
		ondragenditem
	}: {
		/** The action types the engine can actually run — anything not implemented is left out
		 *  rather than offered and then refused at publish. */
		actions: { type: string; label: string }[];
		canManage: boolean;
		/** False once the journey has a start node. THE way an author is stopped from authoring a
		 *  second beginning: the entry is not offered rather than offered and then refused, because a
		 *  palette that can produce an invalid document is a palette that eventually will. */
		canAddStart?: boolean;
		onadd: (item: PaletteItem) => void;
		ondragstartitem: (item: PaletteItem) => void;
		ondragenditem: () => void;
	} = $props();

	const stepItems: PaletteItem[] = (Object.keys(STEP_TYPE_LABELS) as FlowStepType[]).map((t) => ({
		kind: 'step',
		type: t,
		label: STEP_TYPE_LABELS[t],
		swatch: CATEGORY_STYLE[categoryOf(t)].bg
	}));
	const actionItems = $derived<PaletteItem[]>(
		actions.map((a) => ({
			kind: 'action' as const,
			type: a.type,
			label: a.label,
			swatch: ACTOR_STYLE.system.bg
		}))
	);
	const CONTROL_KINDS: FlowNodeKind[] = ['branch', 'wait', 'wait_for_event', 'start_flow', 'exit'];
	const item = (kind: FlowNodeKind): PaletteItem => ({
		kind,
		label: NODE_KIND_LABEL[kind]!,
		hint: NODE_KIND_HINT[kind]!,
		swatch: CONTROL_ACCENT[kind]!
	});
	const controlItems = $derived<PaletteItem[]>([
		...(canAddStart ? [item('start')] : []),
		...CONTROL_KINDS.map(item)
	]);
	/** Its own group rather than an entry under Flow control, even though a condition IS a two-armed
	 *  branch (flow-contract.json → `controlNodes.condition.$comment`). Deciding whether a piece of
	 *  work happens is the commonest thing an author comes here to do, and it should not be three
	 *  clicks inside a group whose other entries are waits and endings. */
	const conditionItems: PaletteItem[] = [item('condition')];

	let openSteps = $state(true);
	// Open, like every other group. Collapsed-by-default read as an EMPTY section: a heading with
	// nothing under it, hiding all thirteen action types from anyone who did not think to click it.
	let openActions = $state(true);
	let openConditions = $state(true);
	let openControl = $state(true);
</script>

{#snippet group(title: string, items: PaletteItem[], open: boolean, toggle: (v: boolean) => void)}
	<Collapsible.Root {open} onOpenChange={toggle}>
		<Collapsible.Trigger
			class="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 px-1
				py-1 text-xs font-semibold"
		>
			<ChevronDownIcon class="size-3 transition-transform {open ? '' : '-rotate-90'}" />
			{title}
			<!-- The count so a COLLAPSED group still says what it holds, rather than looking empty. -->
			<span class="text-muted-foreground/60 ml-auto tabular-nums">{items.length}</span>
		</Collapsible.Trigger>
		<Collapsible.Content class="space-y-0.5 pb-1">
			{#each items as item (item.kind + (item.type ?? ''))}
				<Button
					variant="ghost"
					size="sm"
					class="w-full cursor-grab justify-start gap-2 font-normal active:cursor-grabbing"
					disabled={!canManage}
					draggable={canManage}
					title={item.hint ?? `Add “${item.label}”`}
					ondragstart={(e) => {
						// Firefox refuses to start a drag at all unless the transfer carries something, so
						// the label goes in even though the canvas reads the payload from state (a
						// `dragover` handler is not allowed to read `dataTransfer`).
						e.dataTransfer?.setData('text/plain', item.label);
						if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy';
						ondragstartitem(item);
					}}
					ondragend={() => ondragenditem()}
					onclick={() => onadd(item)}
				>
					<span class="size-2.5 shrink-0 rounded-full" style="background:{item.swatch}"></span>
					<span class="truncate">{item.label}</span>
				</Button>
			{/each}
		</Collapsible.Content>
	</Collapsible.Root>
{/snippet}

<div
	class="bg-background/95 max-h-full w-52 overflow-y-auto rounded-lg border p-1.5 shadow-sm
		backdrop-blur"
>
	<p class="text-muted-foreground px-1 pt-0.5 pb-1.5 text-[11px]">
		{canManage ? 'Drag onto a wire to insert, or click to add.' : 'Read-only.'}
	</p>
	{@render group('Steps', stepItems, openSteps, (v) => (openSteps = v))}
	{@render group('Actions', actionItems, openActions, (v) => (openActions = v))}
	{@render group('Conditions', conditionItems, openConditions, (v) => (openConditions = v))}
	{@render group('Flow control', controlItems, openControl, (v) => (openControl = v))}
</div>
