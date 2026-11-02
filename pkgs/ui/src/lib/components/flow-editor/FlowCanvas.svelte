<script lang="ts">
	import {
		SvelteFlow,
		Background,
		BackgroundVariant,
		Controls,
		type Node,
		type Edge,
		type Connection,
		type Viewport
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { Button, toast } from '@graphene/ui';
	import LayoutGridIcon from '@lucide/svelte/icons/layout-grid';
	import {
		convertLegacy,
		isLegacyShaped,
		addEdge,
		blankNode,
		canSplice,
		mintNodeId,
		retargetEdge,
		spliceNode,
		startNode,
		unsetEdge,
		withStart,
		FLOW_CAPS,
		OUT_HANDLE,
		START_KIND,
		type FlowNode,
		type FlowEdgeRef,
		type FlowPosition
	} from '@graphene/ui/book/journeys';
	import StepNode from './StepNode.svelte';
	import ActionNode from './ActionNode.svelte';
	import BranchNode from './BranchNode.svelte';
	import ControlNode from './ControlNode.svelte';
	import EntryNode from './EntryNode.svelte';
	import TierNode from './TierNode.svelte';
	import ConditionNode from './ConditionNode.svelte';
	import FlowEdge from './FlowEdge.svelte';
	import NodePalette, { type PaletteItem } from './NodePalette.svelte';
	import { setFlowEditorContext } from './context';
	import {
		buildGraph,
		layoutPositions,
		nodeSize,
		placeAfter,
		screenToFlow,
		spliceLayout,
		toAbsolute,
		ACTOR_STYLE,
		ACTOR_LABEL,
		ENTRY_ID,
		INTAKE_ID,
		NO_CONTAINMENT,
		type Actor,
		type Containment,
		type Lookups
	} from './graph';
	import { NO_ISSUES, type GroupedIssues } from './validation';

	let {
		nodes = $bindable(),
		lookups,
		issues = NO_ISSUES,
		actionTypes,
		canManage,
		selectedId = $bindable(),
		onchange,
		onrestructure,
		ondeleterequest
	}: {
		/** The authored graph — the source of truth. The canvas is a projection of it and every edit
		 *  goes back through it, which is why it is bindable rather than a stream of events. */
		nodes: FlowNode[];
		lookups: Lookups;
		/** Publish issues, grouped by node — drives the per-node badges. */
		issues?: GroupedIssues;
		/** The action types the engine can actually run, for the palette. */
		actionTypes: { type: string; label: string }[];
		canManage: boolean;
		selectedId: string | null;
		/** A node's contents changed. */
		onchange: () => void;
		/** The graph's shape changed. */
		onrestructure: () => void;
		/** The author asked to remove these nodes; the page confirms (a branch needs a sentence about
		 *  where its arms go, so the confirmation lives with the other dialogs rather than here). */
		ondeleterequest: (ids: string[]) => void;
	} = $props();

	const nodeTypes = {
		start: EntryNode,
		step: StepNode,
		action: ActionNode,
		// A two-armed branch with its question on the card — its own component for that reason alone.
		condition: ConditionNode,
		branch: BranchNode,
		wait: ControlNode,
		wait_for_event: ControlNode,
		start_flow: ControlNode,
		exit: ControlNode,
		entry: EntryNode,
		// Chrome: the "for each X" containers the work lives inside (graph.ts::tierContainers).
		tier: TierNode
	};
	const edgeTypes = { flow: FlowEdge };
	const ACTORS: Actor[] = ['client', 'adviser', 'compliance', 'system'];

	// ── The projection ────────────────────────────────────────────────────────────────────────────
	// xyflow owns the arrays it renders (it writes node positions into them while a drag is in
	// flight), so they cannot be `$derived`. They are rebuilt from the document whenever the document
	// changes, which is now SAFE in a way it was not before v3: positions live in the document, so a
	// rebuild redraws a dragged node exactly where the author left it.
	let xyNodes = $state.raw<Node[]>([]);
	let xyEdges = $state.raw<Edge[]>([]);
	let positions = $state.raw<Record<string, FlowPosition>>({});
	/** Which tier container each node is drawn inside. The canvas needs it for exactly one reason:
	 *  xyflow reports a child's position RELATIVE to its container and the document holds absolute
	 *  coordinates, so every position read off the canvas goes back through `toAbsolute`. */
	let containment = $state.raw<Containment>(NO_CONTAINMENT);
	let viewport = $state.raw<Viewport>({ x: 0, y: 0, zoom: 1 });
	let paneEl: HTMLDivElement | null = $state(null);

	/** The palette item currently being dragged, and the wire it is hovering. `dataTransfer` cannot
	 *  be read during `dragover`, so the payload has to be here for the highlight to know what it is
	 *  about to insert. */
	let dragging = $state<PaletteItem | null>(null);
	let highlightEdgeId = $state<string | null>(null);

	/** Whether the draft was still legacy-shaped as of the last settled state — read BEFORE an edit,
	 *  which is what makes the conversion fire once on the transition instead of on every edit
	 *  afterwards (see `restructured`). Seeded from the loaded document by the projection effect. */
	let wasLegacy = $state(true);
	let shapeSeeded = $state(false);

	$effect(() => {
		const built = buildGraph(nodes, {
			lookups,
			issues,
			selectedId,
			highlightEdgeId,
			canManage
		});
		xyNodes = built.nodes;
		xyEdges = built.edges;
		positions = built.positions;
		containment = built.containment;
		if (!shapeSeeded) {
			shapeSeeded = true;
			wasLegacy = isLegacyShaped(nodes);
		}
	});

	setFlowEditorContext({
		// A getter, not a value: context is captured once at init, and reading the prop here would
		// freeze whatever `canManage` happened to be at that moment.
		get canManage() {
			return canManage;
		},
		disconnect: (ref) => {
			// The whole ref, not just its handle: a fanned-out socket carries several wires and this
			// removes the one the author clicked, leaving its siblings alone.
			nodes = unsetEdge(nodes, ref);
			restructured();
		}
	});

	/**
	 * A structural edit landed — move the draft to the v3 shape if this is the edit that took it out
	 * of the legacy one, then tell the page.
	 *
	 * Converting lifts every step's condition into a `condition` node in front of it and changes
	 * nothing else: no start node, no prerequisite wiring, no re-aimed edge. `convert.ts::convertLegacy`
	 * records why the two things it does NOT do were both wrong, and it is worth reading before
	 * changing anything here — a branch is only evaluated when control walks into it, so branch-gating
	 * a tiered flow's steps leaves them held behind nodes nothing reaches, and the only wiring that
	 * would fix that draws thirteen paths leaving one start node at once.
	 *
	 * Once, on the transition, because a shape change should never run twice over the same draft —
	 * though `convertLegacy` is idempotent, so a second call would change nothing anyway.
	 */
	function restructured(): void {
		if (wasLegacy && !isLegacyShaped(nodes)) nodes = convertLegacy(nodes);
		wasLegacy = isLegacyShaped(nodes);
		onrestructure();
	}

	// ── Placement ─────────────────────────────────────────────────────────────────────────────────

	/**
	 * Freeze the arrangement currently on screen into the document.
	 *
	 * THE PLACEMENT RULE, and the reason a mixed graph (some nodes placed, some computed) never
	 * survives a save: the moment an author places anything — a drag, a drop, a tidy-up — the whole
	 * visible layout becomes theirs. The alternative is to persist only the node they touched, and
	 * then re-run dagre over the rest on the next open: the computed nodes would shuffle around the
	 * one fixed node and the author's single deliberate act would be the thing that broke the layout.
	 *
	 * THE COORDINATE CONVERSION. A node inside a tier container is an xyflow CHILD, and a child's
	 * `position` is relative to its parent — but the document stores absolute coordinates, and the
	 * layout contract is about those. So every position read off the canvas is put back through
	 * `toAbsolute` here. An `override` is already absolute: those come from the layout functions,
	 * which work in document space throughout.
	 */
	function commitPositions(overrides: Record<string, FlowPosition> = {}) {
		const drawn = new Map(xyNodes.map((n) => [n.id, n.position]));
		nodes = nodes.map((n) => {
			const override = overrides[n.id];
			if (override) return { ...n, position: { x: override.x, y: override.y } };
			const at = drawn.get(n.id);
			return at ? { ...n, position: toAbsolute(containment, n.id, at) } : n;
		});
	}

	function tidyUp() {
		const laid = layoutPositions(nodes);
		nodes = nodes.map((n) => ({ ...n, position: laid[n.id] ?? n.position }));
		restructured();
	}

	// ── Adding ────────────────────────────────────────────────────────────────────────────────────

	/** Refuse at the point of the act rather than letting the author discover the cap at publish,
	 *  by which time they have wired up a journey they cannot ship. */
	function atNodeCap(): boolean {
		const cap = FLOW_CAPS.maxNodesPerFlow ?? 200;
		if (nodes.length < cap) return false;
		toast.error(
			`A journey can have at most ${cap} nodes. ` +
				'Split it into two, joined by “Start another journey”.'
		);
		return true;
	}

	function mint(item: PaletteItem, position: FlowPosition | null): FlowNode {
		return blankNode(item.kind, {
			id: mintNodeId(
				nodes.map((n) => n.id),
				item.type ?? item.kind
			),
			type: item.type,
			position
		});
	}

	/**
	 * Add the start node, wired to whatever the entry currently is (`withStart`).
	 *
	 * Separate from the ordinary add because a start node is not another node — it is THE entry, so
	 * dropping a loose one would orphan the whole graph in a single click. It also sits at the head
	 * of the array and takes the marker's place on the canvas, so there is nothing to place: the
	 * arrangement it inherits is the one that was already there.
	 */
	function addStart(): boolean {
		if (startNode(nodes)) return false; // one beginning, enforced — not merely offered
		commitPositions();
		nodes = withStart(nodes, positions[ENTRY_ID] ?? null);
		selectedId = startNode(nodes)?.id ?? null;
		restructured();
		return true;
	}

	/** Clicked in the palette: append below whatever is selected (or below the graph). */
	function addFromPalette(item: PaletteItem) {
		if (!canManage || atNodeCap()) return;
		if (item.kind === START_KIND) {
			addStart();
			return;
		}
		const anchor = selectedId && selectedId !== INTAKE_ID ? selectedId : null;
		const provisional = blankNode(item.kind, { id: '__sizing__', type: item.type });
		const node = mint(item, placeAfter(nodes, positions, anchor, nodeSize(provisional)));
		commitPositions();
		nodes = [...nodes, node];
		selectedId = node.id;
		restructured();
	}

	// ── Drop on the canvas, and drop on a wire ────────────────────────────────────────────────────

	/** The wires a drop could splice into — authored edges only. The entry marker's edge and the
	 *  `depends_on` edges are not sockets and cannot carry a node. */
	const spliceableEdges = $derived(
		new Map(
			xyEdges
				.map((e) => [e.id, (e.data as { ref?: FlowEdgeRef } | undefined)?.ref] as const)
				.filter((pair): pair is readonly [string, FlowEdgeRef] => !!pair[1])
		)
	);

	/** Screen distance from a point to a rendered edge, by sampling the path.
	 *
	 *  Sampled rather than computed from the two node centres because the edges are smoothstep: they
	 *  jog, sometimes a long way sideways, and "nearest straight line between the nodes" would
	 *  highlight a wire the cursor is nowhere near. Sixteen samples is enough for a 40px hit radius
	 *  and cheap enough to run for every edge on every dragover frame. */
	function distanceToPath(path: SVGPathElement, x: number, y: number): number {
		const ctm = path.getScreenCTM();
		const length = path.getTotalLength();
		if (!ctm || !length) return Number.POSITIVE_INFINITY;
		let best = Number.POSITIVE_INFINITY;
		for (let i = 0; i <= 16; i++) {
			const at = path.getPointAtLength((length * i) / 16).matrixTransform(ctm);
			best = Math.min(best, Math.hypot(at.x - x, at.y - y));
		}
		return best;
	}

	const HIT_RADIUS = 44;

	function wireUnder(x: number, y: number): string | null {
		if (!paneEl) return null;
		let best: { id: string; d: number } | null = null;
		for (const group of paneEl.querySelectorAll<SVGGElement>('.svelte-flow__edge')) {
			const id = group.dataset.id;
			if (!id || !spliceableEdges.has(id)) continue;
			const path = group.querySelector<SVGPathElement>('path.svelte-flow__edge-path');
			if (!path) continue;
			const d = distanceToPath(path, x, y);
			if (!best || d < best.d) best = { id, d };
		}
		return best && best.d <= HIT_RADIUS ? best.id : null;
	}

	function onDragOver(event: DragEvent) {
		if (!dragging || !canManage) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
		highlightEdgeId = canSplice(dragging.kind) ? wireUnder(event.clientX, event.clientY) : null;
	}

	function onDrop(event: DragEvent) {
		event.preventDefault();
		const item = dragging;
		dragging = null;
		const wire = highlightEdgeId;
		highlightEdgeId = null;
		if (!item || !canManage || !paneEl) return;
		if (atNodeCap()) return;
		// Wherever it was dropped: a start node takes the entry's place, so aiming it is meaningless.
		if (item.kind === START_KIND) {
			addStart();
			return;
		}

		const ref = wire ? spliceableEdges.get(wire) : null;
		if (ref) {
			spliceOnto(ref, item);
			return;
		}
		const rect = paneEl.getBoundingClientRect();
		const at = screenToFlow(rect, viewport, { x: event.clientX, y: event.clientY });
		const provisional = blankNode(item.kind, { id: '__sizing__', type: item.type });
		const size = nodeSize(provisional);
		commitPositions();
		const node = mint(item, { x: at.x - size.w / 2, y: at.y - size.h / 2 });
		nodes = [...nodes, node];
		selectedId = node.id;
		restructured();
	}

	/** Insert a node into an existing wire: predecessor → new node → old target, with the graph
	 *  below pushed down to make room. */
	function spliceOnto(ref: FlowEdgeRef, item: PaletteItem) {
		const source = nodes.find((n) => n.id === ref.source);
		if (!source) return;
		const provisional = blankNode(item.kind, { id: '__sizing__', type: item.type });
		const laid = spliceLayout(
			positions,
			{ source: ref.source, target: ref.target },
			{ source: nodeSize(source), inserted: nodeSize(provisional) }
		);
		const node = mint(item, laid.position);
		commitPositions(laid.positions);
		// `nodes` is re-read deliberately: commitPositions has just replaced the array, and splicing
		// into the stale one would throw away every position it wrote.
		nodes = spliceNode(nodes, ref, node);
		selectedId = node.id;
		restructured();
	}

	// ── Wiring ────────────────────────────────────────────────────────────────────────────────────

	/**
	 * A wire was drawn. `addEdge`, not `setEdge`: a second wire out of the same socket is the
	 * fan-out — both targets become available at once — and replacing the first would make the
	 * gesture that expresses parallel work the gesture that destroys it.
	 */
	function onConnect(connection: Connection) {
		if (!canManage || !connection.source || !connection.target) return;
		nodes = addEdge(
			nodes,
			connection.source,
			connection.sourceHandle ?? OUT_HANDLE,
			connection.target
		);
		restructured();
	}

	function onReconnect(oldEdge: Edge, connection: Connection) {
		if (!canManage) return;
		const ref = (oldEdge.data as { ref?: FlowEdgeRef } | undefined)?.ref;
		if (!ref) return;
		const sameSocket =
			connection.source === ref.source && (connection.sourceHandle ?? OUT_HANDLE) === ref.handle;
		if (sameSocket && connection.target) {
			// Re-aimed in place, so it keeps its position in the fan and reads as the same wire moved.
			nodes = retargetEdge(nodes, ref, connection.target);
		} else {
			let next = unsetEdge(nodes, ref);
			if (connection.source && connection.target) {
				next = addEdge(
					next,
					connection.source,
					connection.sourceHandle ?? OUT_HANDLE,
					connection.target
				);
			}
			nodes = next;
		}
		restructured();
	}

	/** A wire may not start a loop of one, and nothing may point at the entry marker (which is drawn,
	 *  not authored — the entry is derived from what nothing points at). */
	function isValidConnection(connection: Connection | Edge): boolean {
		return (
			connection.source !== connection.target &&
			connection.target !== ENTRY_ID &&
			connection.source !== ENTRY_ID
		);
	}

	/**
	 * Backspace, or the ✕ on a wire. We always return false and do the work ourselves: xyflow's own
	 * removal would delete the node out of the picture and leave the document's edges pointing at
	 * something that is no longer there, which is precisely the shattered graph `deleteNode`'s edge
	 * repair exists to prevent.
	 */
	async function onBeforeDelete({
		nodes: doomedNodes,
		edges: doomedEdges
	}: {
		nodes: Node[];
		edges: Edge[];
	}): Promise<boolean> {
		if (!canManage) return false;
		let next = nodes;
		let unwired = false;
		for (const edge of doomedEdges) {
			const ref = (edge.data as { ref?: FlowEdgeRef } | undefined)?.ref;
			if (!ref) continue;
			next = unsetEdge(next, ref);
			unwired = true;
		}
		if (unwired) {
			nodes = next;
			restructured();
		}
		const ids = doomedNodes.map((n) => n.id).filter((id) => id !== ENTRY_ID);
		if (ids.length) ondeleterequest(ids);
		return false;
	}
</script>

<!--
	Nodes ARE draggable now. The layout contract settled it (flow-contract.json →
	nodeEnvelope.$comment_position): a position is persisted and honoured, and dagre places only the
	nodes that have none. Before v3 the layout was recomputed wholesale on every edit and `position`
	was written as null unconditionally, so a dragged node snapped back — which is why dragging was
	off. Both halves of that had to change together, and they did.
-->
<!-- The palette sits BESIDE the graph, not over it. As an xyflow `Panel` it was an overlay, so it
     covered the leftmost cards — and the left is exactly where a top-down layout puts the start of
     the process. A flex column costs nothing and gives the graph its own space. -->
<div class="flex h-full w-full">
	<div class="bg-background/60 flex w-56 shrink-0 flex-col border-r p-2">
		<NodePalette
			actions={actionTypes}
			{canManage}
			canAddStart={!startNode(nodes)}
			onadd={addFromPalette}
			ondragstartitem={(item) => (dragging = item)}
			ondragenditem={() => {
				dragging = null;
				highlightEdgeId = null;
			}}
		/>
		<!-- The key and "Tidy up" live in this column rather than floating over the canvas. As an
		     xyflow Panel the legend sat on top of the FIRST rank of cards — the most important row on
		     the screen — and an overlay that covers content is the same mistake the palette itself
		     was making. -->
		<div class="mt-2 shrink-0 space-y-2 border-t pt-2">
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
				<span class="text-muted-foreground w-full text-[11px] font-medium">Who does it</span>
				{#each ACTORS as a (a)}
					<span class="flex items-center gap-1.5 text-[11px] font-medium">
						<span class="size-2.5 rounded-full" style="background:{ACTOR_STYLE[a].bg}"></span>
						{ACTOR_LABEL[a]}
					</span>
				{/each}
			</div>
			{#if canManage}
				<Button
					variant="outline"
					size="xs"
					class="w-full"
					title="Re-arrange every node from the graph's shape. Anything you have moved by hand will be moved."
					onclick={tidyUp}
				>
					<LayoutGridIcon class="size-3.5" /> Tidy up
				</Button>
			{/if}
		</div>
	</div>
	<div
		class="relative min-w-0 flex-1"
		bind:this={paneEl}
		role="application"
		aria-label="Journey canvas"
		ondragover={onDragOver}
		ondragleave={() => (highlightEdgeId = null)}
		ondrop={onDrop}
	>
		<SvelteFlow
			bind:nodes={xyNodes}
			bind:edges={xyEdges}
			bind:viewport
			{nodeTypes}
			{edgeTypes}
			fitView
			fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
			nodesDraggable={canManage}
			nodesConnectable={canManage}
			elementsSelectable
			{isValidConnection}
			onbeforedelete={onBeforeDelete}
			onconnect={onConnect}
			onreconnect={onReconnect}
			onnodedragstop={() => {
				commitPositions();
				onchange();
			}}
			proOptions={{ hideAttribution: true }}
			onnodeclick={({ node }) => (selectedId = node.id === ENTRY_ID ? INTAKE_ID : node.id)}
			onpaneclick={() => (selectedId = null)}
		>
			<Background variant={BackgroundVariant.Dots} gap={20} />
			<!-- No minimap. It sat over the account tier's cards, and what it was compensating for — "where
		     am I in this thing" — is now answered by the tier container you are inside, whose header is
		     on screen whenever its work is. An overlay that hides content has to earn it. -->
			<Controls showLock={false} />
		</SvelteFlow>
	</div>
</div>

<style>
	/* No literal fallbacks: theme.css always defines these, and a fallback hex is precisely the
	   thing that would survive a firm's re-brand. */
	:global(.svelte-flow) {
		background: var(--muted);
	}
	:global(.svelte-flow__controls-button) {
		background: var(--background);
		border-color: var(--border);
		color: var(--foreground);
	}
</style>
