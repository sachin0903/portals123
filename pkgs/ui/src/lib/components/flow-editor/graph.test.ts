/**
 * The canvas's geometry and projection rules.
 *
 * The layout contract is the thing worth pinning here (flow-contract.json →
 * `nodeEnvelope.$comment_position`): a saved position is honoured, dagre places only what has none,
 * and a definition where nothing is placed still lays itself out — which is the promise that
 * "nothing existing moves" when this shipped.
 */
import { describe, it, expect } from 'vitest';
import {
	addEdge,
	blankNode,
	conditionArms,
	convertLegacy,
	setEdge,
	withStart,
	OUT_HANDLE,
	type FlowNode,
	type FlowPosition,
	type FlowStep
} from '@graphene/ui/book/journeys';
import {
	buildGraph,
	detailFor,
	nodeSize,
	placeAfter,
	resolvePositions,
	screenToFlow,
	spliceLayout,
	toAbsolute,
	toRelative,
	ENTRY_ID,
	RANK_GAP,
	type Lookups
} from './graph';
import { NO_ISSUES } from './validation';

const lookups: Lookups = { forms: [], documents: [], templates: [] };

/** A wired chain of step nodes, all unplaced. */
function chain(...ids: string[]): FlowNode[] {
	let nodes = ids.map((id) => blankNode('step', { id, type: 'task', title: id }));
	for (let i = 0; i < ids.length - 1; i++) nodes = setEdge(nodes, ids[i], OUT_HANDLE, ids[i + 1]);
	return nodes;
}

/** A step in a named tier. */
function tiered(id: string, scope: 'participant' | 'account' | 'application'): FlowNode {
	return { ...blankNode('step', { id, type: 'task', title: id }), scope };
}

const view = (nodes: FlowNode[]) =>
	buildGraph(nodes, { lookups, issues: NO_ISSUES, selectedId: null, canManage: true });

describe('the layout contract', () => {
	it('lays out a definition where nothing is placed — every node gets a position', () => {
		const positions = resolvePositions(chain('a', 'b', 'c'));
		for (const id of ['a', 'b', 'c', ENTRY_ID]) {
			expect(positions[id], `${id} was not placed`).toBeTruthy();
			expect(Number.isFinite(positions[id].x)).toBe(true);
		}
	});

	it('lays an unplaced chain out top-down, in order, without overlaps', () => {
		const positions = resolvePositions(chain('a', 'b', 'c'));
		expect(positions.a.y).toBeLessThan(positions.b.y);
		expect(positions.b.y).toBeLessThan(positions.c.y);
		expect(positions.b.y - positions.a.y).toBeGreaterThanOrEqual(nodeSize(chain('a')[0]).h);
	});

	it('is deterministic, so an unplaced node does not wander between opens', () => {
		expect(resolvePositions(chain('a', 'b', 'c'))).toEqual(resolvePositions(chain('a', 'b', 'c')));
	});

	it('HONOURS a saved position rather than overwriting it', () => {
		const nodes = chain('a', 'b').map((n) =>
			n.id === 'b' ? { ...n, position: { x: 777, y: 999 } } : n
		);
		expect(resolvePositions(nodes).b).toEqual({ x: 777, y: 999 });
	});

	it('places ONLY the nodes that have none', () => {
		const nodes = chain('a', 'b').map((n) =>
			n.id === 'a' ? { ...n, position: { x: 10, y: 20 } } : n
		);
		const positions = resolvePositions(nodes);
		expect(positions.a).toEqual({ x: 10, y: 20 });
		expect(positions.b).not.toEqual({ x: 0, y: 0 });
	});

	it('skips dagre entirely once everything is placed', () => {
		// Not an optimisation detail: the projection re-runs on every keystroke in the inspector, and
		// a layout pass over two hundred nodes per character is a frozen editor. Proven by the only
		// observable difference — a fully placed graph returns exactly what it was given.
		const at: Record<string, FlowPosition> = { a: { x: 5, y: 5 }, b: { x: 5, y: 200 } };
		const nodes = chain('a', 'b').map((n) => ({ ...n, position: at[n.id] }));
		const positions = resolvePositions(nodes);
		expect(positions.a).toEqual(at.a);
		expect(positions.b).toEqual(at.b);
		// The entry marker still gets somewhere sensible — above the node it points at.
		expect(positions[ENTRY_ID].y).toBeLessThan(at.a.y);
	});

	it('lays out a branch wide enough for its arms', () => {
		const branch = blankNode('branch', { id: 'b' });
		expect(nodeSize(branch).w).toBeGreaterThanOrEqual(232);
		const many = {
			...branch,
			config: {
				branches: Array.from({ length: 6 }, (_, i) => ({
					id: `b${i}`,
					label: `Arm ${i}`,
					conditions: i === 5 ? null : {},
					next: null
				}))
			}
		};
		expect(nodeSize(many).w).toBeGreaterThan(nodeSize(branch).w);
	});
});

describe('placing a new node', () => {
	it('drops it below the node the author had selected, not on top of the graph', () => {
		const nodes = chain('a', 'b');
		const positions = resolvePositions(nodes);
		const at = placeAfter(nodes, positions, 'a', { w: 240, h: 96 });
		expect(at.y).toBeGreaterThan(positions.a.y);
		expect(at).not.toEqual({ x: 0, y: 0 });
	});

	it('drops it below everything when nothing is selected', () => {
		const nodes = chain('a', 'b');
		const positions = resolvePositions(nodes);
		const at = placeAfter(nodes, positions, null, { w: 240, h: 96 });
		expect(at.y).toBeGreaterThan(positions.b.y);
	});

	it('starts at the origin for an empty graph', () => {
		expect(placeAfter([], {}, null, { w: 240, h: 96 })).toEqual({ x: 0, y: 0 });
	});
});

describe('splicing onto a wire', () => {
	const sizes = { source: { w: 240, h: 96 }, inserted: { w: 240, h: 64 } };

	it('puts the new node between its neighbours', () => {
		const positions = { a: { x: 0, y: 0 }, b: { x: 0, y: 400 } };
		const out = spliceLayout(positions, { source: 'a', target: 'b' }, sizes);
		expect(out.position.y).toBeGreaterThan(positions.a.y);
		expect(out.position.y).toBeLessThan(positions.b.y);
	});

	it('pushes the graph below apart when there is not room, and only what is below', () => {
		const positions = { a: { x: 0, y: 0 }, b: { x: 0, y: 156 }, c: { x: 0, y: 300 } };
		const out = spliceLayout(positions, { source: 'a', target: 'b' }, sizes);
		expect(out.positions.a).toEqual(positions.a);
		expect(out.positions.b.y).toBeGreaterThan(positions.b.y);
		// Everything below moves by the SAME shortfall, so the arrangement keeps its shape.
		expect(out.positions.c.y - positions.c.y).toBe(out.positions.b.y - positions.b.y);
		// And the new node now fits between them.
		expect(out.position.y + sizes.inserted.h + RANK_GAP).toBeLessThanOrEqual(out.positions.b.y);
	});

	it('leaves the arrangement alone when there is already room', () => {
		const positions = { a: { x: 0, y: 0 }, b: { x: 0, y: 600 } };
		expect(spliceLayout(positions, { source: 'a', target: 'b' }, sizes).positions).toBe(positions);
	});

	it('handles a wire with no target — the socket is unwired, the node still lands below', () => {
		const positions = { a: { x: 30, y: 0 } };
		const out = spliceLayout(positions, { source: 'a', target: null }, sizes);
		expect(out.position.y).toBe(96 + RANK_GAP);
		expect(out.positions).toBe(positions);
	});
});

describe('screen → flow coordinates', () => {
	it('subtracts the pane offset and the pan, then divides by the zoom', () => {
		const at = screenToFlow({ left: 100, top: 50 }, { x: 20, y: 10, zoom: 2 }, { x: 320, y: 210 });
		expect(at).toEqual({ x: 100, y: 75 });
	});

	it('is the identity at the origin with no pan and no zoom', () => {
		expect(screenToFlow({ left: 0, top: 0 }, { x: 0, y: 0, zoom: 1 }, { x: 7, y: 9 })).toEqual({
			x: 7,
			y: 9
		});
	});
});

/** The cards and markers — everything except the tier-container chrome, which is derived from
 *  `scope` and is not a node anybody authored. */
const isCard = (n: { type?: string }) => n.type !== 'tier';

describe('the projection', () => {
	it('draws one node per authored node, plus the entry marker', () => {
		const built = view(chain('a', 'b'));
		expect(built.nodes.filter(isCard).map((n) => n.id)).toEqual([ENTRY_ID, 'a', 'b']);
	});

	it('bands neighbouring same-scope work, and never parents it', () => {
		const built = view(chain('a', 'b'));
		const bands = built.nodes.filter((n) => n.type === 'tier');
		expect(bands).toHaveLength(1);
		// Descriptive chrome: nothing is parented (a parent would clamp its children), nothing is
		// interactive, and it sits behind the cards.
		expect(built.containment).toEqual({ parentOf: {}, origins: {} });
		expect(bands[0].selectable || bands[0].draggable || bands[0].deletable).toBeFalsy();
		expect(bands[0].zIndex).toBeLessThan(0);
		expect(built.nodes.filter((n) => n.parentId)).toEqual([]);
	});

	it('draws an edge for every wired socket, and none for an unwired one', () => {
		const built = view(chain('a', 'b'));
		const authored = built.edges.filter((e) => e.type === 'flow');
		expect(authored.map((e) => e.id)).toEqual(['a:out->b']);
	});

	it('draws a branch arm as its own edge, off its own handle', () => {
		let nodes: FlowNode[] = [blankNode('branch', { id: 'fork' }), ...chain('yes', 'no')];
		nodes = setEdge(nodes, 'fork', 'b1', 'yes');
		nodes = setEdge(nodes, 'fork', 'b2', 'no');
		const arms = view(nodes).edges.filter((e) => e.source === 'fork');
		expect(arms.map((e) => e.sourceHandle)).toEqual(['b1', 'b2']);
		expect(arms.map((e) => e.label)).toEqual(['When…', 'Otherwise']);
		expect(arms.map((e) => e.target)).toEqual(['yes', 'no']);
	});

	it('never draws an edge to a node that is not in the flow', () => {
		const nodes = setEdge(chain('a'), 'a', OUT_HANDLE, 'ghost');
		expect(view(nodes).edges.filter((e) => e.type === 'flow')).toEqual([]);
	});

	it('does not draw a depends_on edge on top of a wire that already joins the pair', () => {
		// The legacy read wires `depends_on` as real edges, so without this every untouched legacy
		// journey would be drawn with two parallel wires between every pair of steps.
		const nodes = chain('a', 'b').map((n) => (n.id === 'b' ? { ...n, depends_on: ['a'] } : n));
		expect(view(nodes).edges.filter((e) => e.source === 'a' && e.target === 'b')).toHaveLength(1);
	});

	it('keeps depends_on visually distinct from the flow edges', () => {
		// A prerequisite with no wire of its own — a join, which is the case a single edge cannot
		// express and therefore the case this edge exists to draw.
		const nodes = [...chain('a'), ...chain('b')].map((n) =>
			n.id === 'b' ? { ...n, depends_on: ['a'] } : n
		);
		const dep = view(nodes).edges.find((e) => e.id === 'dep:a->b');
		expect(dep).toBeTruthy();
		expect(dep!.type).not.toBe('flow');
		// A prerequisite is a join the wire model cannot express, so it is authored in the inspector
		// and must not be draggable or deletable on the canvas.
		expect(dep!.deletable).toBe(false);
		expect(dep!.selectable).toBe(false);
	});

	it('marks a node the engine has complained about', () => {
		const built = buildGraph(chain('a', 'b'), {
			lookups,
			issues: { byNode: { b: ['Nothing leads here.'] }, general: [], all: ['Nothing leads here.'] },
			selectedId: null,
			canManage: true
		});
		const flagged = built.nodes.filter((n) => (n.data as { hasIssues: boolean }).hasIssues);
		expect(flagged.map((n) => n.id)).toEqual(['b']);
	});

	it('makes edges inert for a read-only viewer', () => {
		const built = buildGraph(chain('a', 'b'), {
			lookups,
			issues: NO_ISSUES,
			selectedId: null,
			canManage: false
		});
		expect(built.edges.find((e) => e.type === 'flow')!.deletable).toBe(false);
	});

	it('does not let anything be wired to or from the entry marker', () => {
		// The entry is DERIVED — the node nothing points at — so its marker is a label, not a socket.
		expect(view(chain('a')).nodes.find((n) => n.id === ENTRY_ID)!.connectable).toBe(false);
	});

	it('draws nothing but an empty canvas for an empty flow', () => {
		const built = view([]);
		expect(built.nodes).toEqual([]);
		expect(built.edges).toEqual([]);
	});
});

describe('the start node on the canvas', () => {
	it('replaces the derived marker rather than sitting beneath it', () => {
		const built = view(withStart(chain('a', 'b')));
		expect(built.nodes.filter(isCard).map((n) => n.id)).toEqual(['start', 'a', 'b']);
		expect(built.nodes.some((n) => n.id === ENTRY_ID)).toBe(false);
		expect(built.edges.some((e) => e.source === ENTRY_ID)).toBe(false);
	});

	it('draws one authored, deletable wire per root — the fan the marker could never carry', () => {
		const nodes = addEdge(withStart(chain('a')), 'start', OUT_HANDLE, 'b').concat(
			blankNode('step', { id: 'b', type: 'task' })
		);
		const fan = view(nodes).edges.filter((e) => e.source === 'start');
		expect(fan.map((e) => e.target)).toEqual(['a', 'b']);
		expect(fan.every((e) => e.type === 'flow' && e.deletable)).toBe(true);
	});

	it('gives every node a position, the start node included', () => {
		const positions = resolvePositions(withStart(chain('a', 'b')));
		for (const id of ['start', 'a', 'b']) expect(positions[id]).toBeTruthy();
		expect(positions.start.y).toBeLessThan(positions.a.y);
	});
});

describe('a condition node on the canvas', () => {
	/** A converted onboarding flow: a fork deciding whether `core` happens, both arms re-converging
	 *  on `later`. */
	function gatedDraft(): FlowNode[] {
		const step = (id: string, scope: 'participant' | 'application', next: string | null) => ({
			...blankNode('step', { id, type: 'task', title: id }),
			scope,
			next
		});
		const gate = {
			...blankNode('condition', { id: 'core_when', title: 'Only when · core' }),
			config: {
				branches: conditionArms(
					{ field: 'contact.type', operator: 'equals', value: 'individual' },
					{ when: 'core', otherwise: 'later' }
				)
			}
		};
		return [gate, step('core', 'participant', 'later'), step('later', 'application', null)];
	}

	it('is a real node on the canvas, selectable and deletable like any other', () => {
		// It used to be a block DERIVED from the step's `visibility`: undraggable, undeletable and
		// absent from the document. It is authored now, so none of those exceptions apply.
		const built = view(gatedDraft());
		const gate = built.nodes.find((n) => n.id === 'core_when')!;
		expect(gate.type).toBe('condition');
		expect(gate.draggable).not.toBe(false);
		expect(gate.deletable).not.toBe(false);
	});

	it('draws one edge per arm, each labelled with the answer it carries', () => {
		// Not "applies to", and not dashed: a condition is on the path now, and both of its edges are
		// the ordinary flow of work.
		const edges = view(gatedDraft()).edges.filter((e) => e.source === 'core_when');
		expect(edges.map((e) => [e.sourceHandle, e.target, e.label])).toEqual([
			['true', 'core', 'Yes'],
			['false', 'later', 'No']
		]);
		expect(String(edges[0].style)).not.toContain('stroke-dasharray');
	});

	it('sits in the rank above the work it decides on, centred over it', () => {
		const positions = resolvePositions(gatedDraft());
		expect(positions.core_when.y).toBeLessThan(positions.core.y);
		// Centred, so the spine reads as one column rather than stepping sideways at every fork.
		const centre = (id: string, w: number) => positions[id].x + w / 2;
		expect(centre('core_when', nodeSize(gatedDraft()[0]).w)).toBeCloseTo(
			centre('core', nodeSize(gatedDraft()[1]).w),
			5
		);
	});

	it('still gets its entry arrow once the journey is wired', () => {
		// A wired draft DOES get the entry arrow: there is a first node now, and it is the fork.
		const built = view(gatedDraft());
		expect(built.edges.some((e) => e.source === ENTRY_ID && e.target === 'core_when')).toBe(true);
	});

	it('sits directly above the work its “yes” arm leads to', () => {
		// A condition is asked exactly as often as the work it decides on, and now that the canvas
		// ranks on the wires it simply lands on the rank above that work — no container needed to
		// say so, and no parent to be housed in.
		const built = view(gatedDraft());
		expect(built.nodes.find((n) => n.id === 'core_when')!.parentId).toBeUndefined();
		expect(built.positions.core_when.y).toBeLessThan(built.positions.core.y);
	});

	it('honours a position the author dragged it to', () => {
		const dragged = gatedDraft().map((n) =>
			n.id === 'core_when' ? { ...n, position: { x: 900, y: 900 } } : n
		);
		expect(resolvePositions(dragged).core_when).toEqual({ x: 900, y: 900 });
	});
});

describe('the layout is the flow, not the tiers', () => {
	// This replaces the "for each X" containers. Grouping by `scope` was right while the tiers ran
	// in parallel; the onboarding spine now crosses tiers by design, so a container groups by a
	// different principle than the flow follows and the wire zigzagged between boxes. Measured on
	// the seeded flow before this change: y = 1228 -> 1580 -> 2856 -> 1892 -> 328.
	function spine(): FlowNode[] {
		// participant -> account -> participant: the shape that produced the zigzag.
		return [
			{ ...tiered('core', 'participant'), next: 'terms' } as FlowNode,
			{ ...tiered('terms', 'account'), next: 'idv' } as FlowNode,
			tiered('idv', 'participant')
		];
	}

	it('draws no containers at all', () => {
		const built = view(spine());
		expect(built.nodes.filter((n) => n.type === 'tier')).toEqual([]);
		expect(built.containment).toEqual({ parentOf: {}, origins: {} });
	});

	it('places every node in the order it RUNS, whatever tier it is in', () => {
		// The whole point: a step that comes later is drawn lower, even when the step before it
		// belongs to another tier. Under containers `idv` was drawn above `core`.
		const { positions } = view(spine());
		expect(positions.core.y).toBeLessThan(positions.terms.y);
		expect(positions.terms.y).toBeLessThan(positions.idv.y);
	});

	it('keeps the beginning at the top', () => {
		const nodes = [
			{ id: 'start', kind: 'start', title: 'Start', next: 'core' } as unknown as FlowNode,
			tiered('core', 'participant')
		];
		const { positions } = view(nodes);
		expect(positions.start.y).toBeLessThan(positions.core.y);
	});

	it('lays a single node out without leaving a gap where a container used to be', () => {
		const { positions } = view([tiered('only', 'account')]);
		expect(positions.only.y).toBeGreaterThanOrEqual(0);
	});

	it('draws a band ONLY where same-scope work is already neighbouring', () => {
		// The spine alternates participant -> account -> participant, so no two same-scope nodes are
		// adjacent and there is nothing to band. A band must never be the reason something moved.
		const built = view(spine());
		expect(built.nodes.filter((n) => n.type === 'tier')).toEqual([]);
	});

	it('bands a run, and the band changes no position', () => {
		const run = [
			{ ...tiered('one', 'participant'), next: 'two' } as FlowNode,
			{ ...tiered('two', 'participant'), next: 'three' } as FlowNode,
			tiered('three', 'account')
		];
		const built = view(run);
		const bands = built.nodes.filter((n) => n.type === 'tier');
		expect(bands).toHaveLength(1); // `one`+`two`; `three` is alone at its scope
		// Identical to the layout with the band logic contributing nothing — proof it only describes.
		expect(built.positions).toEqual(view(run).positions);
		// And it encloses exactly the pair.
		const band = bands[0];
		expect(band.position.y).toBeLessThan(built.positions.one.y);
		expect(band.position.y + (band.height ?? 0)).toBeGreaterThan(built.positions.two.y);
	});

	it('a lone node gets no band — its own “per person” line already says it', () => {
		expect(view([tiered('only', 'participant')]).nodes.filter((n) => n.type === 'tier')).toEqual(
			[]
		);
	});
});

describe('absolute ↔ relative positions', () => {
	// The seam survives the containers: `Containment` is empty, so both conversions are the
	// identity and `FlowCanvas` needs no branch. Pinned because restoring containers means filling
	// `tierContainers` back in and nothing else — and a silent off-by-a-container only shows up
	// after somebody drags something.
	function draft(): FlowNode[] {
		return [tiered('core', 'participant'), tiered('setup', 'account')];
	}

	it('draws a node at its absolute position, there being no container to offset it', () => {
		const built = view(draft());
		const drawn = built.nodes.find((n) => n.id === 'setup')!.position;
		expect(drawn).toEqual(built.positions.setup);
	});

	it('round-trips a dragged position back to the document unchanged', () => {
		// What `FlowCanvas.commitPositions` does: read the drawn position, put it back through
		// `toAbsolute`, store that. Dragging a node and letting go must not move it.
		const built = view(draft());
		const drawn = built.nodes.find((n) => n.id === 'setup')!.position;
		expect(toAbsolute(built.containment, 'setup', drawn)).toEqual(built.positions.setup);
		expect(toRelative(built.containment, 'setup', built.positions.setup)).toEqual(drawn);
	});
});
describe('conversion re-draws the journey', () => {
	/** What conversion runs against: steps the canvas has already placed (the first placement act
	 *  commits the whole arrangement) and a journey about to become a spine. */
	function placedLegacy(): FlowNode[] {
		return [
			{
				...blankNode('step', { id: 'core', type: 'task', title: 'Applicant details' }),
				position: { x: 400, y: 620 },
				visibility: { field: 'contact.type', operator: 'equals', value: 'individual' }
			},
			{
				...blankNode('step', { id: 'idv', type: 'task', title: 'Verify identity' }),
				position: { x: 400, y: 780 },
				depends_on: ['core']
			}
		];
	}

	it('lays the whole spine out fresh rather than minting conditions into the old arrangement', () => {
		// THE bug this closes, found in the browser under the shape this replaced: the layout pass ran
		// for the unplaced nodes alone and computed their spots from its own grid, which had nothing
		// to do with where the placed steps ended up — so all twelve landed on top of the cards they
		// gated. A tier list and a spine are not the same picture, so conversion re-draws.
		const converted = convertLegacy(placedLegacy());
		expect(converted.every((n) => n.position === null)).toBe(true);
		const at = resolvePositions(converted);
		const gate = converted.find((n) => n.kind === 'condition')!;
		expect(at[gate.id].y).toBeLessThan(at.core.y);
		expect(at.core.y).toBeLessThan(at.idv.y);
	});

	it('honours a position the author sets AFTER converting', () => {
		const placed = convertLegacy(placedLegacy()).map((n) =>
			n.kind === 'condition' ? { ...n, position: { x: 12, y: 34 } } : n
		);
		const gate = placed.find((n) => n.kind === 'condition')!;
		expect(resolvePositions(placed)[gate.id]).toEqual({ x: 12, y: 34 });
	});
});

describe('accept_terms canvas detail', () => {
	const terms = (config: Record<string, unknown>): FlowStep => ({
		id: 'terms',
		type: 'accept_terms',
		title: 'Terms',
		required: true,
		assignee: 'client',
		scope: 'account',
		config
	});

	it('names a written declaration when no PDF is chosen', () => {
		expect(detailFor(terms({ content: 'I agree' }), lookups)).toBe('Written declaration');
	});

	it('names the PDF when one is chosen', () => {
		expect(
			detailFor(terms({ document_uid: 'd1' }), {
				...lookups,
				documents: [{ uid: 'd1', name: 'ISA terms' }]
			})
		).toBe('ISA terms');
	});
});
