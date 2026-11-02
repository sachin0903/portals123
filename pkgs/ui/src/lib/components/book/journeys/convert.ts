/**
 * Converting a pre-v3 journey into a v3 graph — the one place a legacy `{steps}` document becomes
 * something the graph vocabulary can hold.
 *
 * Separate from `nodes.ts::toNodes` on purpose, and the distinction is the whole point of this
 * module: **reading is not converting.** A legacy document has no edges and no start node, so the
 * read invents neither; conversion is the moment an author reaches for something only v3 can express
 * and the document has to say out loud what the old shape said implicitly.
 *
 * Conversion is two rules, in this order:
 *
 * 1. **the prerequisites become wires** behind a minted `start` node (`wireFromPrerequisites`,
 *    mirrored by `graph.py::upgrade_legacy`, pinned by the contract's `legacyUpgradeExample`);
 * 2. **each step's `visibility` becomes a two-armed `condition` on that spine**
 *    (`conditionsFromVisibility`, mirrored by `graph.py::visibility_to_conditions`, pinned by
 *    `conditionUpgradeExample`): the condition takes the step's PLACE — every incoming edge is
 *    re-aimed at it, its `true` arm points at the step, and its `false` arm at whatever the step led
 *    to, so the two paths re-converge.
 *
 * The order is load-bearing, and so is doing both. A condition is control flow: it is evaluated when
 * a case walks into it, and the step behind it is held until that walk arrives
 * (`planning.control_gated_steps`). A document with no wires has no walk, so the gates have to be
 * minted onto a spine that already exists — and the `false` arm can only say "carry on to whatever
 * came next" once there is a next.
 *
 * **That is a reversal of what this module used to say**, and the reason is recorded here rather
 * than deleted. Until Aug 2026 a `condition` was a plan-time gate compiled into the step's
 * `visibility`, precisely because the seeded onboarding was tier-parallel with no control spine:
 * branch-gating its conditions left fourteen steps held behind nodes nothing reached. Wiring
 * everything into a spine is what removed that objection. The accepted consequence is that work a
 * tier used to offer at once is now reached one node at a time.
 *
 * The round-trip that matters is still the condition itself: a dropped one silently changes who a
 * step applies to, and in onboarding that means asking a client for things they should never have
 * been asked for — an offshore KYC form for an onshore account, a SIPP declaration for an ISA.
 */
import {
	addEdge,
	blankNode,
	conditionArms,
	incomingEdges,
	mintNodeId,
	nextIds,
	outgoing,
	retargetEdge,
	startNode,
	withStart,
	CONDITION_KIND,
	OUT_HANDLE,
	type FlowNode
} from './nodes.js';

/**
 * The stem of a minted condition node's title, mirroring `graph.py::_ONLY_WHEN`.
 *
 * A constant in both languages because a converted document has to be comparable byte-for-byte
 * across them (the contract's `conditionUpgradeExample` pins it) — and because the title is
 * structural: the condition's own SENTENCE is presentation and the canvas writes its own from the
 * automations summariser.
 */
const ONLY_WHEN = 'Only when';

/**
 * Which steps have no prerequisites (the roots the start node fans out to), and which steps hang off
 * each step. The legacy document's own statement of order, read the only way it can be read.
 */
function legacyWiring(steps: { id: string; depends_on?: string[] }[]): {
	roots: string[];
	followers: Map<string, string[]>;
} {
	const known = new Set(steps.map((s) => s.id));
	const followers = new Map<string, string[]>(steps.map((s) => [s.id, []]));
	const roots: string[] = [];
	for (const step of steps) {
		const deps = (step.depends_on ?? []).filter((d) => known.has(d));
		if (!deps.length) roots.push(step.id);
		for (const dep of deps) followers.get(dep)!.push(step.id);
	}
	return { roots, followers };
}

/**
 * Turn the authored `depends_on` prerequisites into wires, behind a start node.
 *
 * `addEdge`, never `setEdge`: a wire the author drew already is kept, and a step with several
 * followers fans out to all of them (the arity rule — a list-valued `next` means parallel). The
 * steps' `depends_on` is left in place; it is still the join an edge cannot express ("not until ALL
 * of these"), and the compiler unions the two.
 *
 * Deliberately NOT the array order the old editor drew: that was presentation, inferred at render
 * time and stored nowhere.
 */
export function wireFromPrerequisites(nodes: FlowNode[]): FlowNode[] {
	const steps = nodes.filter((n) => n.kind === 'step');
	if (!steps.length) return nodes;
	const { roots, followers } = legacyWiring(
		steps.map((s) => ({ id: s.id, depends_on: s.depends_on ?? [] }))
	);
	// `withStart` wires the minted start to the current entry, so one root is already attached; the
	// rest of the fan is added here. A draft that already has a start keeps it.
	let out = withStart(nodes);
	const start = startNode(out);
	if (start) for (const root of roots) out = addEdge(out, start.id, OUT_HANDLE, root);
	for (const step of steps) {
		for (const follower of followers.get(step.id) ?? []) {
			out = addEdge(out, step.id, OUT_HANDLE, follower);
		}
	}
	return out;
}

/** A work node whose condition still lives on the step rather than in a node of its own. */
function gatable(node: FlowNode): boolean {
	return (
		(node.kind === 'step' || node.kind === 'action') &&
		!!node.visibility &&
		typeof node.visibility === 'object'
	);
}

/**
 * Put a two-armed `condition` node in the PLACE of one node, carrying its `visibility`.
 *
 * Three moves, and each is what makes the condition control flow rather than an annotation: every
 * edge that pointed at the node is re-aimed at the condition; the `true` arm points at the node; the
 * `false` arm points at the node's own single successor — the skip, which is what "this does not
 * apply" means once a journey has a path.
 *
 * The false arm is left unwired when the node fans out to several successors. An arm is
 * single-valued by contract and picking one of several would silently drop the others, so publish
 * says so and the author draws the wire.
 *
 * The condition is TRANSFORMED, not copied: leaving it on the step as well would gate the same work
 * twice, in two places, one of them unauthorable.
 */
export function gateWithCondition(nodes: FlowNode[], nodeId: string): FlowNode[] {
	const at = nodes.findIndex((n) => n.id === nodeId);
	const target = nodes[at];
	if (!target || !target.visibility) return nodes;

	const onward = nextIds(target);
	const gate: FlowNode = {
		...blankNode(CONDITION_KIND, {
			id: mintNodeId(
				nodes.map((n) => n.id),
				`${nodeId}_when`
			),
			title: `${ONLY_WHEN} · ${target.title || nodeId}`
		}),
		config: {
			branches: conditionArms(target.visibility as Record<string, unknown>, {
				when: nodeId,
				otherwise: onward.length === 1 ? onward[0]! : null
			})
		}
	};

	// Inserted immediately BEFORE the node it gates, not appended: array order is what the derived
	// `steps` projection preserves, and a condition reads as belonging to the work below it.
	let out = [...nodes.slice(0, at), gate, { ...target, visibility: null }, ...nodes.slice(at + 1)];
	// Re-aimed one edge at a time rather than by rewriting each predecessor's `next`: a predecessor
	// may fan out, and replacing its whole fan would delete the siblings of the wire being moved.
	for (const ref of incomingEdges(nodes, nodeId)) out = retargetEdge(out, ref, gate.id);
	return out;
}

/** Every step or action whose condition still lives on the step, given a node of its own. */
export function conditionsFromVisibility(nodes: FlowNode[]): FlowNode[] {
	let out = nodes;
	for (const node of nodes.filter(gatable)) out = gateWithCondition(out, node.id);
	return out;
}

/**
 * A legacy draft, moved to the v3 shape: the prerequisites become a wired spine, and each step's
 * condition becomes a two-armed `condition` node on it.
 *
 * Called by the flow page at the conversion moment — the first edit that takes a draft out of the
 * legacy shape (`nodes.ts::isLegacyShaped`). Idempotent, because a shape transition is not always a
 * single event: a draft that is already wired keeps its wires (`addEdge`), and a condition that has
 * already moved off its step is not lifted twice.
 *
 * Mirrored by `graph.py::convert_legacy`.
 */
export function convertLegacy(nodes: FlowNode[]): FlowNode[] {
	// Wired already ⇒ leave the wiring alone. A legacy draft has no edges at all (`isLegacyShaped`),
	// so anything with one has been converted or authored, and re-running the prerequisite rule over
	// it would add edges BESIDE the author's — including ones that bypass the conditions this
	// function just minted. Idempotence, in one line.
	const wired = nodes.some((n) => outgoing(n).length > 0) ? nodes : wireFromPrerequisites(nodes);
	// Positions dropped, so the whole journey is laid out fresh. Conversion is a RE-DRAWING: a tier
	// list and a spine are not the same picture, and keeping the old coordinates would leave the
	// steps where they were with the new conditions minted into whatever gaps the grid found —
	// which is how twelve of them once landed exactly on top of the cards they gated. The layout
	// contract is untouched: a position the author sets after this is honoured, as always.
	// (This half is the canvas's; `graph.py::convert_legacy` mirrors the wiring and the conditions,
	// and `position` is UI-only.)
	return conditionsFromVisibility(wired).map((n) => ({ ...n, position: null }));
}

/** Does this draft still hold a condition that has no node of its own? True only before conversion
 *  — the builder never authors one on a step. */
export function hasUnauthoredConditions(nodes: FlowNode[]): boolean {
	return nodes.some(gatable);
}

/** Every `condition` node whose `true` arm leads to a given node, in document order. What "this work
 *  only happens when…" is the answer to, for an inspector that wants to name it. */
export function gatesFor(nodes: FlowNode[], nodeId: string): FlowNode[] {
	return nodes.filter(
		(n) =>
			n.kind === CONDITION_KIND &&
			(n.config?.branches as { conditions?: unknown; next?: string }[] | undefined)?.some(
				(b) => b?.conditions !== null && b?.next === nodeId
			)
	);
}
