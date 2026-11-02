/**
 * The pure authoring rules the builder rewrites a graph with — minting, wiring, splicing, deleting
 * with edge repair — and the legacy → v3 upgrade the first save performs.
 *
 * These are tested as functions rather than through the canvas on purpose: every one of them is a
 * rule about a document ("deleting a node in the middle of a chain must not shatter it"), and a
 * rule about a document is not something to verify by looking at a screenshot.
 */
import { describe, it, expect } from 'vitest';
import contract from './flow-contract.json' with { type: 'json' };
import {
	convertLegacy,
	gatesFor,
	hasUnauthoredConditions,
	wireFromPrerequisites
} from './convert.js';
import {
	addEdge,
	blankNode,
	branchesOf,
	canonicalNext,
	conditionArmsOf,
	canSplice,
	conditionsOf,
	deleteNode,
	entryNodeId,
	incomingEdges,
	mintNodeId,
	nextIds,
	normaliseNode,
	outgoing,
	outgoingEdges,
	repairTargetsFor,
	retargetEdge,
	setEdge,
	spliceNode,
	startNode,
	unsetEdge,
	withStart,
	isLegacyShaped,
	toDefinition,
	toNodes,
	OUT_HANDLE,
	TIMEOUT_HANDLE,
	type FlowNode
} from './nodes.js';
import { ONBOARDING_SEED_STEPS } from './onboarding-seed.fixture.js';
import type { FlowDefinition } from './flows.js';

const chain = (...ids: string[]): FlowNode[] =>
	ids
		.map((id, i) =>
			blankNode('step', { id, type: 'task', title: id, position: { x: 0, y: i * 100 } })
		)
		.map((n, i) => ({ ...n, next: ids[i + 1] ?? null }));

const byId = (nodes: FlowNode[], id: string) => nodes.find((n) => n.id === id)!;

describe('minting nodes', () => {
	it('slugifies a title into a legal node id', () => {
		expect(mintNodeId([], 'Verify identity')).toBe('verify_identity');
		expect(mintNodeId([], '  ')).toBe('node');
	});

	it('never collides with an id already in the draft', () => {
		expect(mintNodeId(['wait'], 'Wait')).toBe('wait_2');
		expect(mintNodeId(['wait', 'wait_2'], 'Wait')).toBe('wait_3');
	});

	it('gives a fresh step a title the API will accept', () => {
		// `definitions._validate_step` refuses an empty title, and being told your flow is malformed
		// because you added a step is not a thing the builder may do.
		expect(blankNode('step', { id: 's', type: 'fill_form' }).title).toBeTruthy();
		expect(blankNode('action', { id: 'a', type: 'apply_tag' }).title).toBeTruthy();
		expect(blankNode('exit', { id: 'e' }).title).toBeTruthy();
	});

	it('gives a fresh branch two arms, one of them the mandatory else', () => {
		const arms = branchesOf(blankNode('branch', { id: 'b' }));
		expect(arms).toHaveLength(contract.controlNodes.branch.minBranches);
		expect(arms.filter((a) => a.conditions === null)).toHaveLength(1);
	});

	it('omits `next` on the kinds whose edges do not live there', () => {
		expect(blankNode('branch', { id: 'b' }).next).toBeUndefined();
		expect(blankNode('exit', { id: 'e' }).next).toBeUndefined();
		expect(blankNode('start_flow', { id: 'f' }).next).toBeUndefined();
		expect(blankNode('wait', { id: 'w' }).next).toBeNull();
	});
});

describe('sockets', () => {
	it('reports an unwired socket rather than hiding it — the canvas still draws the handle', () => {
		const node = blankNode('step', { id: 's' });
		expect(outgoingEdges(node)).toEqual([
			{ source: 's', handle: OUT_HANDLE, target: null, kind: 'next' }
		]);
	});

	it('gives a branch one socket per arm, labelled with the arm', () => {
		const branch = blankNode('branch', { id: 'b' });
		const sockets = outgoingEdges(branch);
		expect(sockets.map((s) => s.handle)).toEqual(['b1', 'b2']);
		expect(sockets.map((s) => s.label)).toEqual(['When…', 'Otherwise']);
	});

	it('gives wait_for_event a deadline socket alongside its ordinary one', () => {
		const node = blankNode('wait_for_event', { id: 'w' });
		expect(outgoingEdges(node).map((s) => s.handle)).toEqual([OUT_HANDLE, TIMEOUT_HANDLE]);
	});

	it('gives a terminal node no outgoing socket at all', () => {
		expect(outgoingEdges(blankNode('exit', { id: 'e' }))).toEqual([]);
		expect(outgoingEdges(blankNode('start_flow', { id: 'f' }))).toEqual([]);
	});

	it('finds the entry the way the engine does — the node nothing points at', () => {
		expect(entryNodeId(chain('a', 'b', 'c'))).toBe('a');
		expect(entryNodeId([])).toBeNull();
		// A fully cyclic graph has no unpointed node; the first one stands in (the cycle is a
		// publish issue, not a reason to have no entry).
		const loop = setEdge(chain('a', 'b'), 'b', OUT_HANDLE, 'a');
		expect(entryNodeId(loop)).toBe('a');
	});
});

describe('wiring', () => {
	it('points a linear node at another', () => {
		const nodes = setEdge(chain('a', 'b'), 'a', OUT_HANDLE, 'b');
		expect(byId(nodes, 'a').next).toBe('b');
	});

	it('unwires without deleting the socket', () => {
		const nodes = setEdge(chain('a', 'b'), 'a', OUT_HANDLE, null);
		expect(byId(nodes, 'a').next).toBeNull();
	});

	it('writes a branch arm through the arm handle, leaving its siblings alone', () => {
		const nodes = setEdge([blankNode('branch', { id: 'b' }), ...chain('x')], 'b', 'b1', 'x');
		const arms = branchesOf(byId(nodes, 'b'));
		expect(arms.map((a) => a.next)).toEqual(['x', null]);
	});

	it('writes a wait_for_event deadline into config, and removes the key when unwired', () => {
		const start = [blankNode('wait_for_event', { id: 'w' }), ...chain('x')];
		const wired = setEdge(start, 'w', TIMEOUT_HANDLE, 'x');
		expect(byId(wired, 'w').config.timeout_branch).toBe('x');
		const cleared = setEdge(wired, 'w', TIMEOUT_HANDLE, null);
		expect('timeout_branch' in byId(cleared, 'w').config).toBe(false);
	});

	it('refuses to give a terminal node an edge', () => {
		const nodes = setEdge([blankNode('exit', { id: 'e' }), ...chain('x')], 'e', OUT_HANDLE, 'x');
		expect(byId(nodes, 'e').next).toBeUndefined();
	});

	it('leaves the array it was given untouched', () => {
		const before = chain('a', 'b');
		setEdge(before, 'a', OUT_HANDLE, null);
		expect(byId(before, 'a').next).toBe('b');
	});

	it('lists what points at a node', () => {
		const nodes = chain('a', 'b', 'c');
		expect(incomingEdges(nodes, 'b')).toEqual([
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' }
		]);
		expect(incomingEdges(nodes, 'a')).toEqual([]);
	});
});

describe('deleting with edge repair', () => {
	it('closes the gap in a chain rather than shattering it', () => {
		const nodes = deleteNode(chain('a', 'b', 'c'), 'b');
		expect(nodes.map((n) => n.id)).toEqual(['a', 'c']);
		expect(byId(nodes, 'a').next).toBe('c');
	});

	it('re-points a branch arm at the deleted node s successor', () => {
		let nodes = [blankNode('branch', { id: 'b' }), ...chain('mid', 'end')];
		nodes = setEdge(nodes, 'b', 'b1', 'mid');
		nodes = setEdge(nodes, 'b', 'b2', 'end');
		const after = deleteNode(nodes, 'mid');
		expect(branchesOf(byId(after, 'b')).map((a) => a.next)).toEqual(['end', 'end']);
	});

	it('re-points a deadline edge too', () => {
		let nodes = [blankNode('wait_for_event', { id: 'w' }), ...chain('chase', 'end')];
		nodes = setEdge(nodes, 'w', TIMEOUT_HANDLE, 'chase');
		const after = deleteNode(nodes, 'chase');
		expect(byId(after, 'w').config.timeout_branch).toBe('end');
	});

	it('drops the deleted node out of every depends_on', () => {
		const nodes = chain('a', 'b').map((n) =>
			n.id === 'b' ? { ...n, depends_on: ['a', 'ghost'] } : n
		);
		expect(byId(deleteNode(nodes, 'a'), 'b').depends_on).toEqual(['ghost']);
	});

	it('leaves the tail dangling when the LAST node goes, rather than inventing a target', () => {
		const nodes = deleteNode(chain('a', 'b'), 'b');
		expect(byId(nodes, 'a').next).toBeNull();
	});

	it('hands a branch s predecessors the else arm — the one arm every case could take', () => {
		let nodes = [...chain('before'), blankNode('branch', { id: 'b' }), ...chain('yes', 'no')];
		nodes = setEdge(nodes, 'before', OUT_HANDLE, 'b');
		nodes = setEdge(nodes, 'b', 'b1', 'yes');
		nodes = setEdge(nodes, 'b', 'b2', 'no');
		expect(repairTargetsFor(nodes, 'b')).toEqual(['no']);
		expect(byId(deleteNode(nodes, 'b'), 'before').next).toBe('no');
	});
});

describe('splicing onto a wire', () => {
	it('inserts predecessor → new → old target', () => {
		const nodes = chain('a', 'b');
		const spliced = spliceNode(
			nodes,
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' },
			blankNode('wait', { id: 'pause' })
		);
		expect(byId(spliced, 'a').next).toBe('pause');
		expect(byId(spliced, 'pause').next).toBe('b');
	});

	it('inserts the new node directly after its predecessor in document order', () => {
		const spliced = spliceNode(
			chain('a', 'b', 'c'),
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' },
			blankNode('wait', { id: 'pause' })
		);
		expect(spliced.map((n) => n.id)).toEqual(['a', 'pause', 'b', 'c']);
	});

	it('splices onto a branch arm through the arm s handle', () => {
		let nodes = [blankNode('branch', { id: 'b' }), ...chain('yes')];
		nodes = setEdge(nodes, 'b', 'b1', 'yes');
		const spliced = spliceNode(
			nodes,
			{ source: 'b', handle: 'b1', target: 'yes', kind: 'branch' },
			blankNode('action', { id: 'tag', type: 'apply_tag' })
		);
		expect(branchesOf(byId(spliced, 'b')).map((a) => a.next)).toEqual(['tag', null]);
		expect(byId(spliced, 'tag').next).toBe('yes');
	});

	it('gives a spliced branch both arms pointing at the old target', () => {
		const spliced = spliceNode(
			chain('a', 'b'),
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' },
			blankNode('branch', { id: 'fork' })
		);
		expect(branchesOf(byId(spliced, 'fork')).map((x) => x.next)).toEqual(['b', 'b']);
	});

	it('refuses to splice a terminal, which would strand everything past it', () => {
		expect(canSplice('exit')).toBe(false);
		expect(canSplice('start_flow')).toBe(false);
		expect(canSplice('branch')).toBe(true);
		expect(canSplice('step')).toBe(true);
	});
});

describe('the arity rule — next may be a list, and a list means parallel', () => {
	it('reads all three authored spellings the same way', () => {
		expect(nextIds(normaliseNode({ id: 'a' }))).toEqual([]);
		expect(nextIds(normaliseNode({ id: 'a', next: null }))).toEqual([]);
		expect(nextIds(normaliseNode({ id: 'a', next: 'b' }))).toEqual(['b']);
		expect(nextIds(normaliseNode({ id: 'a', next: ['b'] }))).toEqual(['b']);
		expect(nextIds(normaliseNode({ id: 'a', next: ['b', 'c'] }))).toEqual(['b', 'c']);
		expect(nextIds(normaliseNode({ id: 'a', next: ['b', 'b'] }))).toEqual(['b']);
	});

	it('writes one edge as a bare string, so no stored v3 document churns on being opened', () => {
		expect(canonicalNext([])).toBeNull();
		expect(canonicalNext(['b'])).toBe('b');
		expect(canonicalNext(['b', 'c'])).toEqual(['b', 'c']);
		const one = toDefinition(chain('a', 'b'), []).nodes as FlowNode[];
		expect(one[0].next).toBe('b');
	});

	it('a second wire out of the same socket ADDS rather than replaces', () => {
		// The gesture that expresses parallel work must not be the gesture that destroys it.
		let nodes = [...chain('a'), ...chain('b'), ...chain('c')];
		nodes = addEdge(nodes, 'a', OUT_HANDLE, 'b');
		nodes = addEdge(nodes, 'a', OUT_HANDLE, 'c');
		expect(nextIds(byId(nodes, 'a'))).toEqual(['b', 'c']);
		expect(outgoing(byId(nodes, 'a'))).toEqual(['b', 'c']);
	});

	it('a branch arm and a deadline stay single-valued, so adding replaces there', () => {
		let nodes = [blankNode('branch', { id: 'f' }), ...chain('x'), ...chain('y')];
		nodes = addEdge(nodes, 'f', 'b1', 'x');
		nodes = addEdge(nodes, 'f', 'b1', 'y');
		expect(branchesOf(byId(nodes, 'f'))[0].next).toBe('y');
		let wfe = [blankNode('wait_for_event', { id: 'w' }), ...chain('x'), ...chain('y')];
		wfe = addEdge(wfe, 'w', TIMEOUT_HANDLE, 'x');
		wfe = addEdge(wfe, 'w', TIMEOUT_HANDLE, 'y');
		expect(byId(wfe, 'w').config.timeout_branch).toBe('y');
	});

	it('removes ONE wire and leaves its siblings alone', () => {
		let nodes = [...chain('a'), ...chain('b'), ...chain('c')];
		nodes = addEdge(addEdge(nodes, 'a', OUT_HANDLE, 'b'), 'a', OUT_HANDLE, 'c');
		nodes = unsetEdge(nodes, { source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' });
		expect(nextIds(byId(nodes, 'a'))).toEqual(['c']);
	});

	it('re-aims one wire in place, keeping its position in the fan', () => {
		let nodes = [...chain('a'), ...chain('b'), ...chain('c'), ...chain('d')];
		nodes = addEdge(addEdge(nodes, 'a', OUT_HANDLE, 'b'), 'a', OUT_HANDLE, 'c');
		nodes = retargetEdge(
			nodes,
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' },
			'd'
		);
		expect(nextIds(byId(nodes, 'a'))).toEqual(['d', 'c']);
	});

	it('draws one edge ref per target, so each is separately deletable', () => {
		const nodes = addEdge(
			addEdge([...chain('a'), ...chain('b'), ...chain('c')], 'a', OUT_HANDLE, 'b'),
			'a',
			OUT_HANDLE,
			'c'
		);
		expect(outgoingEdges(byId(nodes, 'a')).map((e) => e.target)).toEqual(['b', 'c']);
		// …and an unwired socket still yields one ref, because the canvas has a handle to draw.
		expect(outgoingEdges(byId(nodes, 'b')).map((e) => e.target)).toEqual([null]);
	});

	it('splicing onto one wire of a fan leaves the other alone', () => {
		let nodes = [...chain('a'), ...chain('b'), ...chain('c')];
		nodes = addEdge(addEdge(nodes, 'a', OUT_HANDLE, 'b'), 'a', OUT_HANDLE, 'c');
		nodes = spliceNode(
			nodes,
			{ source: 'a', handle: OUT_HANDLE, target: 'b', kind: 'next' },
			blankNode('step', { id: 'mid', type: 'task' })
		);
		expect(nextIds(byId(nodes, 'a'))).toEqual(['mid', 'c']);
		expect(nextIds(byId(nodes, 'mid'))).toEqual(['b']);
	});

	it('deleting a node hands its WHOLE fan to whatever pointed at it', () => {
		let nodes = [...chain('a'), ...chain('mid'), ...chain('b'), ...chain('c')];
		nodes = setEdge(nodes, 'a', OUT_HANDLE, 'mid');
		nodes = addEdge(addEdge(nodes, 'mid', OUT_HANDLE, 'b'), 'mid', OUT_HANDLE, 'c');
		expect(repairTargetsFor(nodes, 'mid')).toEqual(['b', 'c']);
		expect(nextIds(byId(deleteNode(nodes, 'mid'), 'a'))).toEqual(['b', 'c']);
	});
});

describe('the start node', () => {
	it('is the entry, and there can only be one', () => {
		const nodes = withStart(chain('a', 'b'));
		expect(nodes[0].kind).toBe('start');
		expect(entryNodeId(nodes)).toBe(nodes[0].id);
		// Asked twice, minted once: exactly one beginning is an invariant, not an affordance.
		expect(withStart(nodes)).toBe(nodes);
		expect(startNode(nodes)!.id).toBe(nodes[0].id);
	});

	it('is wired to the current entry, so adding one orphans nothing', () => {
		expect(nextIds(withStart(chain('a', 'b'))[0])).toEqual(['a']);
		expect(nextIds(withStart([])[0])).toEqual([]);
	});

	it('cannot be spliced onto a wire — a beginning in the middle is not a thing', () => {
		expect(canSplice('start')).toBe(false);
	});

	it('is absent from a v3 draft that never had one, and the entry stays derived', () => {
		const nodes = chain('a', 'b');
		expect(startNode(nodes)).toBeNull();
		expect(entryNodeId(nodes)).toBe('a');
	});
});

describe('reading and writing a legacy definition', () => {
	const legacy: FlowDefinition = { steps: ONBOARDING_SEED_STEPS, milestones: [] };

	it('is a SEMANTIC NO-OP: the seeded onboarding round-trips byte-for-byte', () => {
		// The guarantee the two shapes exist for. A published journey that somebody opens, looks at
		// and saves must come back out as the document that went in — no new keys, no invented
		// edges, no re-ordering — so no live onboarding can regress by being inspected.
		expect(toDefinition(toNodes(legacy), [])).toEqual({
			steps: ONBOARDING_SEED_STEPS,
			milestones: [],
			intake: null
		});
	});

	it('stays in the legacy shape — no `nodes` key until the author reaches for v3', () => {
		const out = toDefinition(toNodes(legacy), []);
		expect('nodes' in out).toBe(false);
		expect('steps' in out).toBe(true);
	});

	it('does not grow keys a step never carried', () => {
		// `toNodes` fills `position` and `depends_on` because the node model needs them uniformly;
		// writing them back where the original had neither would make an untouched save a diff.
		const steps = toDefinition(toNodes(legacy), []).steps as Record<string, unknown>[];
		expect('position' in steps[0]).toBe(false);
		expect('depends_on' in steps[0]).toBe(false);
		expect(steps.find((s) => s.id === 'idv')!.depends_on).toEqual(['core']);
	});

	it('re-reads its own output unchanged', () => {
		const once = toDefinition(toNodes(legacy), []);
		const twice = toDefinition(toNodes(once as never), []);
		expect(twice).toEqual(once);
	});

	it('wires the upgrade exactly as the contract says, which is how the two sides stay in step', () => {
		// The same worked example `graph.py::upgrade_legacy` is pinned against. The rule is
		// cross-language and there are two implementations of it, so it lives in the referee.
		const example = contract.legacyUpgradeExample;
		// Against `wireFromPrerequisites`, not `toNodes`: the read invents nothing, so the wiring rule
		// the contract pins is the CONVERSION's (graph.py::upgrade_legacy is its Python twin).
		const nodes = wireFromPrerequisites(
			toNodes({
				steps: example.legacy as never,
				milestones: []
			})
		);
		expect(
			nodes.map((n, i) =>
				Object.fromEntries(
					Object.keys(example.upgraded[i]).map((k) => [
						k,
						(n as unknown as Record<string, unknown>)[k]
					])
				)
			)
		).toEqual(example.upgraded);
	});

	it('fans the whole first tier out of the start node rather than chaining it, ON CONVERSION', () => {
		// THE defect this closes: our live onboarding asks for five `terms_*` declarations together.
		// Only once an author converts, though — the READ wires nothing, because a canvas that drew
		// this fan for an untouched legacy flow announced "13 paths at once" for work that is actually
		// spread across three tiers (see nodes.test.ts).
		const nodes = wireFromPrerequisites(toNodes(legacy));
		const roots = nextIds(nodes[0]);
		expect(nodes[0].kind).toBe('start');
		expect(roots).toEqual(
			ONBOARDING_SEED_STEPS.filter((s) => !s.depends_on?.length).map((s) => s.id)
		);
		expect(roots.length).toBeGreaterThan(1);
		// …and a step with a prerequisite hangs off it, not off the one above it in the array.
		expect(nextIds(byId(nodes, 'core'))).toContain('idv');
	});
});

describe('converting a legacy definition — a wired spine with two-armed conditions', () => {
	const legacy: FlowDefinition = { steps: ONBOARDING_SEED_STEPS, milestones: [] };
	const gated = ONBOARDING_SEED_STEPS.filter((s) => s.visibility);
	const legacyStep = (id: string, over: Record<string, unknown> = {}) =>
		({ id, type: 'task', title: id, ...over }) as never;
	const armsOf = (node: FlowNode) => conditionArmsOf(node)!;

	it('wires the prerequisites AND lifts every step condition onto the spine', () => {
		expect(gated.length).toBeGreaterThan(1); // a fixture with no conditions would test nothing
		const nodes = convertLegacy(toNodes(legacy));
		expect(nodes.filter((n) => n.kind === 'condition')).toHaveLength(gated.length);
		// The wiring is what makes the conditions mean anything: control has to be able to reach one.
		expect(nodes.some((n) => n.kind === 'start')).toBe(true);
		for (const step of gated) {
			const gate = nodes.find((n) => n.kind === 'condition' && armsOf(n).when.next === step.id)!;
			expect(conditionsOf(gate)).toEqual(step.visibility);
		}
	});

	it('puts the condition IN THE PLACE of the work it decides on', () => {
		// The difference from the plan-time gate this replaced, and the whole reason it works: nothing
		// points at the step any more except the condition's own `true` arm, so the walk cannot reach
		// the work without passing the question.
		const nodes = convertLegacy(toNodes(legacy));
		const gate = nodes.find((n) => n.kind === 'condition' && armsOf(n).when.next === 'core')!;
		expect(incomingEdges(nodes, 'core').map((e) => e.source)).toEqual([gate.id]);
		expect(incomingEdges(nodes, gate.id).map((e) => e.source)).toEqual([startNode(nodes)!.id]);
	});

	it('re-converges: the otherwise arm carries on where the skipped work would have', () => {
		// `core` leads to `idv` (its only follower), so skipping `core` means going to `idv` — which is
		// exactly what the two arms coming back together looks like.
		const nodes = convertLegacy(toNodes(legacy));
		const gate = nodes.find((n) => n.kind === 'condition' && armsOf(n).when.next === 'core')!;
		expect(nextIds(byId(nodes, 'core'))).toEqual(['idv']);
		expect(armsOf(gate).otherwise.next).toBe('idv');
	});

	it('leaves the otherwise arm unwired rather than guessing between several successors', () => {
		// An arm is single-valued by contract, so picking one of a fan would silently drop the others.
		// Publish says so and the author draws the wire.
		const nodes = convertLegacy(
			toNodes({
				steps: [
					legacyStep('a', { visibility: { field: 'x', operator: 'isNotEmpty' } }),
					legacyStep('b', { depends_on: ['a'] }),
					legacyStep('c', { depends_on: ['a'] })
				],
				milestones: []
			})
		);
		const gate = nodes.find((n) => n.kind === 'condition')!;
		expect(nextIds(byId(nodes, 'a'))).toEqual(['b', 'c']);
		expect(armsOf(gate).otherwise.next).toBeNull();
	});

	it('TRANSFORMS the condition rather than copying it — it lives in one place afterwards', () => {
		const nodes = convertLegacy(toNodes(legacy));
		// Cleared to null on a step that carried one, still absent on a step that never did — an
		// untouched step must not grow a key by being converted.
		expect(nodes.filter((n) => n.kind === 'step').every((n) => !n.visibility)).toBe(true);
		expect(gated.every((s) => byId(nodes, s.id).visibility === null)).toBe(true);
		expect(hasUnauthoredConditions(nodes)).toBe(false);
	});

	it('keeps every condition: a dropped one silently changes who a step applies to', () => {
		const nodes = convertLegacy(toNodes(legacy));
		const recovered = Object.fromEntries(
			nodes
				.filter((n) => n.kind === 'condition')
				.map((gate) => [armsOf(gate).when.next, conditionsOf(gate)])
		);
		expect(recovered).toEqual(Object.fromEntries(gated.map((s) => [s.id, s.visibility])));
	});

	it('is idempotent — a shape transition is not always a single event', () => {
		const once = convertLegacy(toNodes(legacy));
		expect(convertLegacy(once)).toEqual(once);
	});

	it('wires a definition with no conditions, and mints no conditions for it', () => {
		const plain = toNodes({ steps: [legacyStep('a'), legacyStep('b')], milestones: [] });
		const converted = convertLegacy(plain);
		expect(converted.some((n) => n.kind === 'condition')).toBe(false);
		// Both are dependency-free, so the start fans out to both: the prerequisites are the only
		// order the document states, and it states none between these two.
		expect(nextIds(startNode(converted)!)).toEqual(['a', 'b']);
	});

	it('mints the condition exactly as the contract says, which is how the two sides stay in step', () => {
		// The same worked example `graph.py::visibility_to_conditions` is pinned against — including
		// the minted title and the arm labels, because a converted document has to be comparable
		// byte-for-byte across the two languages.
		const example = contract.conditionUpgradeExample;
		const nodes = convertLegacy(example.authored.map((n) => normaliseNode(n as never)));
		expect(
			nodes.map((n, i) =>
				Object.fromEntries(
					Object.keys(example.converted[i]).map((k) => [
						k,
						(n as unknown as Record<string, unknown>)[k]
					])
				)
			)
		).toEqual(example.converted);
	});

	it('never collides with an id a step already claimed', () => {
		const nodes = convertLegacy(
			toNodes({
				steps: [
					legacyStep('a', { visibility: { field: 'x', operator: 'isNotEmpty' } }),
					legacyStep('a_when')
				],
				milestones: []
			})
		);
		expect(nodes.find((n) => n.kind === 'condition')!.id).toBe('a_when_2');
	});

	it('gatesFor names the conditions deciding on a node', () => {
		const nodes = convertLegacy(toNodes(legacy));
		const first = gated[0];
		expect(gatesFor(nodes, first.id).map((n) => conditionsOf(n))).toEqual([first.visibility]);
		expect(gatesFor(nodes, 'nothing_here')).toEqual([]);
	});
});

describe('the shape switch', () => {
	it('calls a plain step list legacy', () => {
		expect(isLegacyShaped(toNodes({ steps: ONBOARDING_SEED_STEPS, milestones: [] }))).toBe(true);
		expect(isLegacyShaped([])).toBe(true);
	});

	it('calls it a graph the moment ANY wire is drawn', () => {
		expect(isLegacyShaped(chain('a', 'b'))).toBe(false);
	});

	it('calls an untouched legacy flow legacy — no wires, so nothing to re-derive', () => {
		// The switch went back to being a plain shape test when the legacy read stopped inventing
		// edges. While it was "faithful", a legacy flow arrived fully wired and the switch had to
		// re-derive whether those wires merely restated `depends_on` — which is a lot of machinery to
		// answer a question the read should never have raised.
		const nodes = toNodes({ steps: ONBOARDING_SEED_STEPS, milestones: [] });
		expect(nodes.some((n) => outgoing(n).length > 0)).toBe(false);
		expect(isLegacyShaped(nodes)).toBe(true);
	});

	it('calls it a graph as soon as ONE wire is drawn', () => {
		const nodes = toNodes({ steps: ONBOARDING_SEED_STEPS, milestones: [] });
		const last = nodes[nodes.length - 1];
		expect(isLegacyShaped(addEdge(nodes, 'core', OUT_HANDLE, last.id))).toBe(false);
	});

	it('calls it a graph the moment a start node appears', () => {
		// Unlike the previous model, the start node alone converts the draft: it is a v3 node, the
		// legacy shape has nowhere to put it, and saving as `{steps}` would silently drop it.
		const nodes = withStart([
			blankNode('step', { id: 'a', type: 'task' }),
			blankNode('step', { id: 'b', type: 'task' })
		]);
		expect(isLegacyShaped(nodes)).toBe(false);
	});

	it('calls it a graph the moment a control or action node is added', () => {
		for (const node of [
			blankNode('branch', { id: 'b' }),
			blankNode('wait', { id: 'w' }),
			blankNode('exit', { id: 'e' }),
			blankNode('action', { id: 'a', type: 'apply_tag' })
		]) {
			expect(isLegacyShaped([blankNode('step', { id: 's' }), node]), node.kind).toBe(false);
		}
	});

	it('is not fooled by depends_on, which is not an edge', () => {
		const nodes = [
			blankNode('step', { id: 'a' }),
			{ ...blankNode('step', { id: 'b' }), depends_on: ['a'] }
		];
		expect(isLegacyShaped(nodes)).toBe(true);
	});

	it('switches the emitted shape, and the v3 shape carries no derived `steps`', () => {
		const graph = [...chain('a', 'b'), blankNode('exit', { id: 'done' })];
		const out = toDefinition(graph, []);
		expect('nodes' in out).toBe(true);
		expect('steps' in out).toBe(false);
	});

	it('keeps every node s position once the draft is a graph', () => {
		const nodes = toDefinition(chain('a', 'b'), []).nodes as FlowNode[];
		expect(nodes.map((n) => n.position)).toEqual([
			{ x: 0, y: 0 },
			{ x: 0, y: 100 }
		]);
	});
});
