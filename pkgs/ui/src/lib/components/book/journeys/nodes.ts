/**
 * The v3 graph vocabulary — the TypeScript side of `flow-contract.json`'s `nodeKinds`,
 * `nodeEnvelope` and `controlNodes`, mirroring `apps/api/app/flows/graph.py`.
 *
 * A v3 definition is `{nodes, milestones, intake}`. A node is the legacy step object plus two
 * keys: `kind` (what sort of node it is) and `next` (its outgoing edge, or edges). There is no
 * separate edges array on purpose — a second home for edges is a second class of dangling
 * reference to police, and the engine already had to walk nodes.
 *
 * **`next` may be a list, and a list means parallel** (flow-contract.json →
 * `nodeEnvelope.$comment_nextArity`). Read it through `nextIds`, never directly: the wire spelling
 * is `null | string | string[]` so that a single edge stays a bare string and no existing document
 * changes shape, and every reader on both sides normalises. A branch ARM stays single-valued.
 *
 * A `condition` node **is a branch with two arms** (`true` and `false`), carrying them in
 * `config.branches` exactly as a branch does — so every reader here treats the two alike
 * (`isFanOutKind`). Both arms may point at the same node, which is the re-convergence a journey is
 * drawn around. See `flow-contract.json → controlNodes.condition.$comment`, which also records why
 * it used to be a plan-time gate and what changed.
 *
 * Legacy `{steps}` definitions are still live in production, so the reader accepts both shapes and
 * `toNodes` reads a legacy one as nodes in memory. **The legacy read invents nothing** — no start
 * node, no edges (see `toNodes`); a legacy definition has no edges of its own, and minting them is
 * a claim about the journey that the document does not make. Turning a legacy document INTO a graph
 * is a separate, explicit act (`convert.ts::convertLegacy`): it wires the prerequisites into a spine
 * and lifts each step's `visibility` onto it as a condition. `toDefinition` on the canvas emits v3,
 * and the API's `validate_definition` re-derives the `steps` projection from it, so planning,
 * projection and every existing reader see an unchanged definition.
 *
 * This module is deliberately pure — no Svelte, no xyflow. The canvas that consumes it lives in
 * apps/platform (it carries the @xyflow/svelte + dagre dependency and no other app mounts it), but
 * the *vocabulary* belongs here with the rest of the flows types, where the API contract test can
 * reach it and the run-a-case surfaces can label a node the same way the editor does.
 */
import contract from './flow-contract.json' with { type: 'json' };
import {
	STEP_TYPE_DEFAULT_ASSIGNEE,
	STEP_TYPE_DEFAULT_SCOPE,
	STEP_TYPE_LABELS,
	type FlowStep,
	type FlowStepType,
	type FlowMilestone,
	type FlowIntake,
	type FlowDefinition
} from './flows.js';

/** What sort of node this is. `step` is human work, `action` is unattended work the engine
 *  performs, and the rest are control flow. Absent ⇒ `step`, which is what makes a legacy
 *  definition load untouched. */
export type FlowNodeKind = (typeof contract.nodeKinds)[number];

export const NODE_KINDS: readonly FlowNodeKind[] = contract.nodeKinds as FlowNodeKind[];

/** The kinds that carry work (a `type` resolving against stepTypes / actionTypes respectively). */
export const WORK_KINDS = ['step', 'action'] as const;
/** The kinds with no outgoing edge at all — authoring a `next` on one is a refusal, not a no-op. */
export const TERMINAL_KINDS = ['exit', 'start_flow'] as const;
/** The graph's explicit entry. At most one per definition — see `startNode`/`withStart`. */
export const START_KIND = 'start';
/** The two-armed branch. A `condition` shares the branch representation and every rule that goes
 *  with it — read the contract's `controlNodes.condition.$comment` for what it is and what it used
 *  to be. */
export const CONDITION_KIND = 'condition';
/** Its two arm ids, in order (contract → `controlNodes.condition.armIds`). Fixed: the pair IS the
 *  node, and an arm called anything else is a branch wearing a condition's chrome. */
export const CONDITION_TRUE = 'true';
export const CONDITION_FALSE = 'false';

export function isWorkKind(kind: string): kind is 'step' | 'action' {
	return kind === 'step' || kind === 'action';
}
export function isTerminalKind(kind: string): boolean {
	return (TERMINAL_KINDS as readonly string[]).includes(kind);
}
/** True when this kind's edges live on its arms rather than on a single `next` — a branch, and the
 *  condition that is one (`graph.py::BRANCHING_KINDS`). */
export function isFanOutKind(kind: string): boolean {
	return kind === 'branch' || kind === CONDITION_KIND;
}

/** The action vocabulary — what an automation DOES. Shared with the rule tier
 *  (`@graphene/ui/automations` ACTION_TYPES), because a flow action node and a rule's action are
 *  the same executor. */
export type FlowActionType = keyof typeof contract.actionTypes;

/** One arm of a branch node. Arms are evaluated in array order and the first whose conditions pass
 *  is taken; exactly one arm must carry `conditions: null` — the else. */
export interface FlowBranch {
	id: string;
	label: string;
	/** null = the unconditional else arm. */
	conditions: Record<string, unknown> | null;
	/** The node this arm goes to; null while the author is still wiring it up. */
	next: string | null;
}

/** A canvas coordinate. Persisted and honoured: the layout pass places only nodes whose position
 *  is null (flow-contract.json → nodeEnvelope.$comment_position). */
export interface FlowPosition {
	x: number;
	y: number;
}

/** A node in a v3 definition. The work-node keys (`type`, `assignee`, `scope`…) are optional here
 *  because a control node carries none of them; `nodeIssues` and the API's validator are what
 *  refuse a node missing the keys its own kind requires. */
export interface FlowNode extends Partial<Omit<FlowStep, 'id' | 'position' | 'type'>> {
	id: string;
	kind: FlowNodeKind;
	/**
	 * The step type (`kind: 'step'`) or the action type (`kind: 'action'`); absent on a control node,
	 * whose kind is its whole identity.
	 *
	 * Widened past `FlowStepType` deliberately: `generate_document` names BOTH a staff step and an
	 * unattended action, and the contract settles which by the node's `kind` rather than by a
	 * precedence rule (flow-contract.json → `$comment_actionTypes`). Typing this as the step union
	 * alone would have made every action node a cast at the point of use, which is exactly the sort
	 * of cast that eventually gets pointed at the wrong vocabulary.
	 */
	type?: FlowStepType | FlowActionType;
	title: string;
	config: Record<string, unknown>;
	position: FlowPosition | null;
	depends_on?: string[];
	/**
	 * The outgoing edge, or edges. Absent on `branch` (edges live on its arms) and on the terminal
	 * kinds. Null on a non-terminal node is work in progress — save allows it, publish refuses it.
	 *
	 * **Read it with `nextIds`, never directly.** The three spellings mean one thing: `null` is no
	 * edge, a string is one, an array is several — and several means PARALLEL, all available at
	 * once. A single edge stays a bare string so that no v3 document written before the arity rule
	 * changes shape on its next save (flow-contract.json → `nodeEnvelope.$comment_nextArity`).
	 */
	next?: string | string[] | null;
}

/** The v3 authored definition. `steps` may still be present: the API emits it as a derived
 *  projection so every pre-v3 reader keeps working, but it is output, never input. */
export interface FlowGraphDefinition {
	nodes: FlowNode[];
	milestones: FlowMilestone[];
	intake?: FlowIntake | null;
	/** Derived by the API from `nodes` — read it, never author it. */
	steps?: FlowStep[];
}

/** True when the definition was authored as a graph (rather than a legacy ordered step list). */
export function isGraphDefinition(
	def: FlowDefinition | FlowGraphDefinition | null | undefined
): def is FlowGraphDefinition {
	return !!def && Array.isArray((def as FlowGraphDefinition).nodes);
}

/**
 * The wire spelling of a set of outgoing edges: `null` for none, a bare string for one, an array
 * for several. Mirrors `graph.py::canonical_next` — one canonical form on both sides is what lets
 * a document be compared byte-for-byte across the two, and what stops every stored v3 definition
 * churning from `next: "b"` to `next: ["b"]` the first time it is opened.
 */
export function canonicalNext(ids: readonly string[]): string | string[] | null {
	if (!ids.length) return null;
	if (ids.length === 1) return ids[0]!;
	return [...ids];
}

/** Every node id a node's `next` names, whatever spelling it was authored in. THE reader — nothing
 *  else should touch `node.next` — because the arity is exactly the thing that changed. */
export function nextIds(node: FlowNode): string[] {
	if (isFanOutKind(node.kind) || isTerminalKind(node.kind)) return [];
	const raw = node.next;
	if (typeof raw === 'string') return raw ? [raw] : [];
	if (!Array.isArray(raw)) return [];
	return [...new Set(raw.filter((x): x is string => typeof x === 'string' && x.length > 0))];
}

/** Point a node's `next` at exactly these targets, in the canonical spelling. */
function withNextIds(node: FlowNode, ids: readonly string[]): FlowNode {
	return { ...node, next: canonicalNext([...new Set(ids)]) };
}

/**
 * Read either shape as nodes.
 *
 * **A legacy `{steps}` definition mints no start node and invents no edges.** Step nodes, `next:
 * null`, `depends_on` verbatim, and nothing else — because that is the whole of what the document
 * says. Its execution order is the tier (`scope`) and then `depends_on`; the sequence the pre-v3
 * editor drew between steps was inferred at render time and stored nowhere.
 *
 * Wiring the steps in ARRAY order would be the wrong answer, and it is worth naming so it does not
 * come back: that order was the pre-v3 editor's presentation, and it says nothing about what waits
 * for what. The prerequisites are the only ordering the document states.
 *
 * Turning a legacy document INTO a graph is therefore a separate, deliberate act
 * (`convert.ts::convertLegacy`): the prerequisites become wires behind a start node, and each step's
 * `visibility` becomes a two-armed `condition` on that spine. Reading is not converting.
 */
export function toNodes(def: FlowDefinition | FlowGraphDefinition | null | undefined): FlowNode[] {
	if (!def) return [];
	if (isGraphDefinition(def)) return def.nodes.map(normaliseNode);
	const steps = def.steps ?? [];
	if (!steps.length) return [];
	return steps.map((step) => ({
		...step,
		kind: 'step' as const,
		title: step.title ?? '',
		config: step.config ?? {},
		position: step.position ?? null,
		depends_on: step.depends_on ?? [],
		next: null
	}));
}

/** The graph's start node, if it has one. At most one exists — `withStart` is the only minting
 *  path and the palette hides itself once there is one. */
export function startNode(nodes: FlowNode[]): FlowNode | null {
	return nodes.find((n) => n.kind === START_KIND) ?? null;
}

/** Fill the envelope defaults on a node read off the wire (kind defaults to `step`). */
export function normaliseNode(raw: FlowNode | (Partial<FlowNode> & { id: string })): FlowNode {
	const kind = (raw.kind ?? 'step') as FlowNodeKind;
	const node: FlowNode = {
		...raw,
		id: raw.id,
		kind,
		title: raw.title ?? '',
		config: raw.config ?? {},
		position: raw.position ?? null,
		depends_on: raw.depends_on ?? []
	};
	// A branch's edges live on its arms and a terminal has none — carrying a stray `next` on either
	// is what graph.parse refuses, so the reader drops it rather than round-tripping it back.
	if (isFanOutKind(kind) || isTerminalKind(kind)) delete node.next;
	else node.next = canonicalNext(nextIds({ ...node, next: raw.next }));
	return node;
}

/** The test a `condition` node asks — its `true` arm's conditions. Null for any other kind, and for
 *  a condition an author has not written yet (publish refuses that: a condition with no test decides
 *  nothing). One reader, so "what a condition asks" has one definition on this side too. */
export function conditionsOf(node: FlowNode): Record<string, unknown> | null {
	if (node.kind !== CONDITION_KIND) return null;
	const raw = branchesOf(node).find((b) => b.conditions !== null)?.conditions;
	return raw && Object.keys(raw).length ? raw : null;
}

/** The two arms of a `condition`, by id — `null` where the node is malformed (hand-written JSON;
 *  everything that mints one goes through `conditionArms`). */
export function conditionArmsOf(
	node: FlowNode
): { when: FlowBranch; otherwise: FlowBranch } | null {
	const arms = branchesOf(node);
	const when = arms.find((b) => b.id === CONDITION_TRUE);
	const otherwise = arms.find((b) => b.id === CONDITION_FALSE);
	return when && otherwise && arms.length === 2 ? { when, otherwise } : null;
}

/** The arms of a branch or condition node, read out of its config (empty for any other kind). */
export function branchesOf(node: FlowNode): FlowBranch[] {
	if (!isFanOutKind(node.kind)) return [];
	const raw = node.config?.branches;
	return Array.isArray(raw) ? (raw as FlowBranch[]) : [];
}

/** Every node id this node points at — its `next` plus, for a branch or condition, each arm's
 *  target. The one place edge-following is defined, so a new fan-out kind is a change here and
 *  nowhere else. */
export function outgoing(node: FlowNode): string[] {
	if (isFanOutKind(node.kind)) {
		return [
			...new Set(
				branchesOf(node)
					.map((b) => b.next)
					.filter((x): x is string => typeof x === 'string' && x.length > 0)
			)
		];
	}
	return nextIds(node);
}

/** Human labels for the kinds, for palettes, node chrome and the run-a-case surfaces. */
export const NODE_KIND_LABEL: Record<FlowNodeKind, string> = {
	start: 'Start',
	step: 'Step',
	action: 'Action',
	condition: 'Condition',
	branch: 'Branch',
	wait: 'Wait',
	wait_for_event: 'Wait for event',
	start_flow: 'Start another flow',
	exit: 'End'
};

/** A one-line description of what each kind is for — palette subtitles and empty-state copy. */
export const NODE_KIND_HINT: Record<FlowNodeKind, string> = {
	start: 'Where the journey begins. Wire it to everything that starts at once.',
	step: 'Work a person does — a form, an upload, a review.',
	action: 'Work the system does, unattended.',
	condition: 'Restrict what it points at to the people or accounts it describes.',
	branch: 'Send the journey down one of several paths.',
	wait: 'Pause for a length of time, or until a date.',
	wait_for_event: 'Pause until something happens — with a deadline.',
	start_flow: 'Hand over to another journey and finish here.',
	exit: 'End the journey with an outcome.'
};

/** The config keys each control kind accepts (mirrors controlNodes in the contract). */
export const CONTROL_CONFIG_KEYS: Record<string, readonly string[]> = Object.fromEntries(
	Object.entries(contract.controlNodes).map(([kind, spec]) => [
		kind,
		(spec as { config: string[] }).config
	])
);

export const FLOW_CAPS = contract.caps as Record<string, number>;

// =================================================================================================
// Sockets and edges
// =================================================================================================

/**
 * The single outgoing socket every linear kind has. Handle ids are what the canvas hands back when
 * an author drags a wire, so they have to name the FIELD the edge lives in, not the visual dot:
 * `out` is `next`, `timeout` is a `wait_for_event`'s `config.timeout_branch`, and a branch's
 * sockets are its arm ids. One vocabulary, so `setEdge` can write any wire without knowing which
 * component drew it.
 */
export const OUT_HANDLE = 'out';
/** A `wait_for_event`'s deadline edge — an edge that lives in config because the contract puts it
 *  there (graph.py's `_outgoing` reads it, so it is a real edge, not a parameter). */
export const TIMEOUT_HANDLE = 'timeout';

/** How an edge should read on the canvas. `next` is the ordinary flow of work; `branch` is one arm
 *  of a choice and carries the arm's label; `timeout` is the deadline escape and is drawn as the
 *  exception it is. */
export type FlowEdgeKind = 'next' | 'branch' | 'timeout';

/** One authored outgoing edge, resolved. `target: null` is a socket the author has not wired yet —
 *  returned rather than dropped, because the canvas still has to draw the handle. */
export interface FlowEdgeRef {
	source: string;
	handle: string;
	target: string | null;
	kind: FlowEdgeKind;
	label?: string;
}

/**
 * Every outgoing socket of a node, wired or not — the drawing counterpart to `outgoing()`, which
 * answers the traversal question ("where can this go?") and therefore omits the empty ones.
 */
export function outgoingEdges(node: FlowNode): FlowEdgeRef[] {
	if (isFanOutKind(node.kind)) {
		return branchesOf(node).map((b) => ({
			source: node.id,
			handle: b.id,
			target: b.next ?? null,
			kind: 'branch' as const,
			label: b.label
		}));
	}
	if (isTerminalKind(node.kind)) return [];
	// ONE REF PER TARGET. A fanned-out node has several wires leaving the same socket, and they are
	// distinct edges the author can delete or re-aim one at a time — which is why every write path
	// below takes the whole ref rather than just its handle. An unwired socket still yields one ref
	// with `target: null`, because the canvas has a handle to draw either way.
	const targets = nextIds(node);
	const edges: FlowEdgeRef[] = (targets.length ? targets : [null]).map((target) => ({
		source: node.id,
		handle: OUT_HANDLE,
		target,
		kind: 'next' as const
	}));
	if (node.kind === 'wait_for_event') {
		const timeout = node.config?.timeout_branch;
		edges.push({
			source: node.id,
			handle: TIMEOUT_HANDLE,
			target: typeof timeout === 'string' && timeout ? timeout : null,
			kind: 'timeout',
			label: 'if it times out'
		});
	}
	return edges;
}

/**
 * Where a case starts — mirrors `graph.py::_entry_for`: the `start` node if the graph has one,
 * else the first node nothing points at, falling back to the first node (a fully cyclic graph has
 * no unpointed node, and reporting the cycle is the validator's job, not a reason to have no
 * entry).
 */
export function entryNodeId(nodes: FlowNode[]): string | null {
	if (!nodes.length) return null;
	const start = startNode(nodes);
	if (start) return start.id;
	const pointedAt = new Set(nodes.flatMap(outgoing));
	return nodes.find((n) => !pointedAt.has(n.id))?.id ?? nodes[0]!.id;
}

// =================================================================================================
// Minting nodes
// =================================================================================================

const SLUG_OK = /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/;

/**
 * A node id that is a legal slug (`graph.py::_SLUG`) and unique in this draft.
 *
 * Ids are authored keys, not surrogates: they appear in `depends_on`, in milestones' `reached_when`
 * and in `flows.case_steps.step_key` for every case ever run, so a readable one ("aml_check_2")
 * beats a uuid the moment anybody has to read a database row or a publish issue.
 */
export function mintNodeId(existing: Iterable<string>, base: string): string {
	const taken = new Set(existing);
	let slug = base
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_+|_+$/g, '');
	if (!slug || !SLUG_OK.test(slug)) slug = 'node';
	if (!taken.has(slug)) return slug;
	let n = 2;
	while (taken.has(`${slug}_${n}`)) n++;
	return `${slug}_${n}`;
}

/** What a fresh branch's arms look like: a condition to fill in, and the mandatory else the
 *  publish validator insists on. Authoring the else up front is the point — a fan-out with no
 *  fallback is the failure the branch node exists to prevent, so the builder never creates one. */
function blankBranches(): FlowBranch[] {
	return [
		{
			id: 'b1',
			label: 'When…',
			conditions: { field: '', operator: 'equals', value: '' },
			next: null
		},
		{ id: 'b2', label: 'Otherwise', conditions: null, next: null }
	];
}

/**
 * A condition's two arms, in the canonical order and spelling — the TypeScript half of
 * `graph.py::condition_arms`.
 *
 * One minting point per language so the seed, both converters and the builder cannot disagree about
 * what a condition looks like on the wire, and so a converted document can be compared byte-for-byte
 * across the two (the contract's `conditionUpgradeExample` pins exactly that).
 */
export function conditionArms(
	conditions: Record<string, unknown>,
	opts: { when?: string | null; otherwise?: string | null } = {}
): FlowBranch[] {
	return [
		{ id: CONDITION_TRUE, label: 'Yes', conditions, next: opts.when ?? null },
		{ id: CONDITION_FALSE, label: 'No', conditions: null, next: opts.otherwise ?? null }
	];
}

/** The default config a fresh control node carries, so a dropped node is a valid one to save. */
const BLANK_CONTROL_CONFIG: Record<string, () => Record<string, unknown>> = {
	// `start` deliberately absent: it has no config at all (controlNodes.start.config is []), so the
	// `?.() ?? {}` fallback below is the right answer rather than an omission.
	// A fresh condition carries both arms and one empty condition row, for the same reason a fresh
	// branch carries its else: the shape the engine requires should never have to be authored, and
	// the inspector should open on the thing the author came to write.
	condition: () => ({ branches: conditionArms({ field: '', operator: 'equals', value: '' }) }),
	branch: () => ({ branches: blankBranches() }),
	wait: () => ({ duration_days: 1 }),
	wait_for_event: () => ({ trigger: '' }),
	start_flow: () => ({ flow_code: '', carry_memory: true }),
	exit: () => ({ outcome: 'completed' })
};

/**
 * A fresh node of a kind, ready to drop on the canvas.
 *
 * The title defaults to something the API will accept rather than to the empty string: a step with
 * no title is a 422 at save (`definitions._validate_step`), and an author who adds a step, types a
 * form name into it and hits save should not be told the flow is malformed. They rename it; they
 * are never blocked by it.
 */
export function blankNode(
	kind: FlowNodeKind,
	opts: { id: string; type?: string; title?: string; position?: FlowPosition | null }
): FlowNode {
	const base: FlowNode = {
		id: opts.id,
		kind,
		title: opts.title ?? '',
		config: BLANK_CONTROL_CONFIG[kind]?.() ?? {},
		position: opts.position ?? null,
		depends_on: []
	};
	if (!isFanOutKind(kind) && !isTerminalKind(kind)) base.next = null;
	if (kind === 'step') {
		const type = (opts.type ?? 'task') as FlowStepType;
		return {
			...base,
			type,
			title: base.title || STEP_TYPE_LABELS[type],
			required: true,
			assignee: STEP_TYPE_DEFAULT_ASSIGNEE[type],
			scope: STEP_TYPE_DEFAULT_SCOPE[type],
			per_participant: false,
			visibility: null,
			tasks: []
		};
	}
	if (kind === 'action') {
		const type = (opts.type ?? 'apply_tag') as FlowActionType;
		return {
			...base,
			type,
			title: base.title || humaniseSlug(type),
			required: true,
			// Not 'client': an action is nobody's to-do, and 'client' is what would put engine work in
			// a client's portal. Mirrors definitions._validate_action_node's own default.
			assignee: 'staff',
			scope: 'application',
			visibility: null
		};
	}
	return { ...base, title: base.title || (NODE_KIND_LABEL[kind] ?? kind) };
}

/** "apply_tag" → "Apply tag". Local rather than imported from `@graphene/ui/automations` so this
 *  module keeps no dependency outside the journeys folder. */
function humaniseSlug(slug: string): string {
	const words = slug.replace(/[_.]/g, ' ').trim();
	return words.charAt(0).toUpperCase() + words.slice(1);
}

// =================================================================================================
// Editing the shape (pure — every function returns a new array, the old one untouched)
// =================================================================================================

function replaceNode(
	nodes: FlowNode[],
	id: string,
	patch: (node: FlowNode) => FlowNode
): FlowNode[] {
	return nodes.map((n) => (n.id === id ? patch(n) : n));
}

/**
 * Point one socket at exactly one node (or at nothing), REPLACING whatever it pointed at.
 *
 * The single write path for every edge, which is why it takes a handle id rather than a field name
 * — the canvas knows which dot was dragged, and nothing above this function needs to know that a
 * branch keeps its edges in `config.branches` while a `wait_for_event` keeps its deadline in
 * `config.timeout_branch`.
 *
 * "Replacing" is the part to be careful with now that a socket can carry several wires: this is
 * the right primitive for a branch arm and a deadline (both single-valued by contract), and for
 * "make this node lead here and nowhere else". To add a wire to a fan-out use `addEdge`; to remove
 * or re-aim ONE of several, `unsetEdge`/`retargetEdge`, which take the whole edge ref because the
 * target is what distinguishes it from its siblings.
 */
export function setEdge(
	nodes: FlowNode[],
	source: string,
	handle: string,
	target: string | null
): FlowNode[] {
	return replaceNode(nodes, source, (node) => {
		if (handle === TIMEOUT_HANDLE) {
			const config = { ...node.config };
			if (target) config.timeout_branch = target;
			else delete config.timeout_branch;
			return { ...node, config };
		}
		if (isFanOutKind(node.kind)) {
			const branches = branchesOf(node).map((b) => (b.id === handle ? { ...b, next: target } : b));
			return { ...node, config: { ...node.config, branches } };
		}
		if (isTerminalKind(node.kind)) return node; // nothing comes back from a tail call or an end
		return withNextIds(node, target ? [target] : []);
	});
}

/**
 * Add a wire to a socket, keeping the ones already on it — the fan-out write.
 *
 * Only the ordinary `out` socket can hold more than one; a branch arm and a deadline are
 * single-valued by contract, so for those this is `setEdge`. That asymmetry is deliberate and it
 * lives here rather than in the canvas: the canvas knows which dot was dragged, not what arity the
 * contract gives it.
 */
export function addEdge(
	nodes: FlowNode[],
	source: string,
	handle: string,
	target: string
): FlowNode[] {
	const node = nodes.find((n) => n.id === source);
	if (!node || handle === TIMEOUT_HANDLE || isFanOutKind(node.kind) || isTerminalKind(node.kind)) {
		return setEdge(nodes, source, handle, target);
	}
	return replaceNode(nodes, source, (n) => withNextIds(n, [...nextIds(n), target]));
}

/** Remove ONE wire, named by the ref the canvas hands back. A sibling on the same socket survives,
 *  which is what makes deleting one arm of a fan-out possible at all. */
export function unsetEdge(nodes: FlowNode[], ref: FlowEdgeRef): FlowNode[] {
	const node = nodes.find((n) => n.id === ref.source);
	if (!node || ref.handle === TIMEOUT_HANDLE || isFanOutKind(node.kind)) {
		return setEdge(nodes, ref.source, ref.handle, null);
	}
	if (isTerminalKind(node.kind)) return nodes;
	return replaceNode(nodes, ref.source, (n) =>
		withNextIds(
			n,
			nextIds(n).filter((t) => t !== ref.target)
		)
	);
}

/** Re-aim ONE wire, in place. Position in the list is preserved — it is presentation only, but a
 *  wire that jumped to the end of the fan every time it was re-aimed would look like a different
 *  edge each time on the canvas. */
export function retargetEdge(
	nodes: FlowNode[],
	ref: FlowEdgeRef,
	target: string | null
): FlowNode[] {
	const node = nodes.find((n) => n.id === ref.source);
	if (!node || ref.handle === TIMEOUT_HANDLE || isFanOutKind(node.kind)) {
		return setEdge(nodes, ref.source, ref.handle, target);
	}
	if (isTerminalKind(node.kind)) return nodes;
	return replaceNode(nodes, ref.source, (n) => {
		const ids = nextIds(n);
		const at = ref.target ? ids.indexOf(ref.target) : -1;
		if (at === -1) return target ? withNextIds(n, [...ids, target]) : n;
		const kept = [...ids];
		kept.splice(at, 1, ...(target ? [target] : []));
		return withNextIds(n, kept);
	});
}

/**
 * What the predecessors of a node about to be deleted should point at instead.
 *
 * A linear node hands on its own targets — all of them, so deleting one out of the middle of a
 * fan-out closes the gap rather than shattering it. A branch has no such answer — its arms lead
 * several places *exclusively* — so it hands on the ELSE arm's target alone, which is the only arm
 * every case could take. (That is the difference between the two: a fan-out's targets all happen,
 * a branch's do not, so inheriting all of a branch's arms would turn a choice into a broadcast.)
 * The caller is expected to say so before it deletes (the canvas asks for confirmation naming that
 * target); this function is not the place to decide whether the author meant it.
 */
export function repairTargetsFor(nodes: FlowNode[], id: string): string[] {
	const node = nodes.find((n) => n.id === id);
	if (!node) return [];
	if (isFanOutKind(node.kind)) {
		const arms = branchesOf(node);
		const heir = arms.find((b) => b.conditions === null)?.next ?? arms[0]?.next ?? null;
		return heir ? [heir] : [];
	}
	if (isTerminalKind(node.kind)) return [];
	return nextIds(node);
}

/**
 * Delete a node and repair the wires around it: everything that pointed at it now points at
 * whatever it pointed at (`repairTargetsFor`), and it drops out of every `depends_on`.
 *
 * The repair is the whole point. Without it, deleting one step out of a fifteen-step journey
 * silently disconnects the eleven behind it, and the author finds out at publish via a wall of
 * "nothing leads to…" — having already lost the wiring that would have told them where it went.
 */
export function deleteNode(nodes: FlowNode[], id: string): FlowNode[] {
	const heirs = repairTargetsFor(nodes, id);
	const heir = heirs[0] ?? null;
	const point = (target: string | null | undefined) => (target === id ? heir : (target ?? null));
	return nodes
		.filter((n) => n.id !== id)
		.map((n) => {
			const next: FlowNode = {
				...n,
				depends_on: (n.depends_on ?? []).filter((d) => d !== id)
			};
			if (isFanOutKind(n.kind)) {
				next.config = {
					...n.config,
					branches: branchesOf(n).map((b) => ({ ...b, next: point(b.next) }))
				};
			} else if (!isTerminalKind(n.kind)) {
				// The doomed node's whole fan is inherited in its place, so a predecessor that led to
				// one node that led to three now leads to those three.
				next.next = canonicalNext([
					...new Set(nextIds(n).flatMap((t) => (t === id ? heirs : [t])))
				]);
			}
			if (n.kind === 'wait_for_event' && n.config?.timeout_branch === id) {
				const config = { ...next.config };
				if (heir) config.timeout_branch = heir;
				else delete config.timeout_branch;
				next.config = config;
			}
			return next;
		});
}

/** Can this kind be dropped ONTO a wire? A terminal has no `next`, so splicing one in would leave
 *  everything past it stranded — the author almost certainly meant to add it, not to amputate the
 *  rest of the journey. A `start` cannot be spliced either, for the opposite reason: it is where
 *  the journey begins, and a beginning in the middle of a wire is not a thing. Both are placed as
 *  free nodes instead. */
export function canSplice(kind: FlowNodeKind): boolean {
	return !isTerminalKind(kind) && kind !== START_KIND;
}

/**
 * Add the start node to a draft that has none, wired to whatever the entry currently is.
 *
 * Wired rather than left loose on purpose. A start node IS the entry (`entryNodeId`), so dropping
 * a bare one would make every other node unreachable in a single stroke and greet the author with
 * a wall of "nothing leads to…" for a graph they had just finished wiring. Attached to the current
 * entry it changes nothing at all, and the author's next act — dragging a second wire out of it —
 * is the fan-out they came for.
 *
 * Returns the draft untouched when it already has one: exactly one start node is an invariant, not
 * a rule the canvas is trusted to remember (the palette also hides the entry once one exists, but
 * that is the affordance, and this is the guarantee).
 */
export function withStart(nodes: FlowNode[], position: FlowPosition | null = null): FlowNode[] {
	if (startNode(nodes)) return nodes;
	const entry = entryNodeId(nodes);
	const start = blankNode(START_KIND, {
		id: mintNodeId(
			nodes.map((n) => n.id),
			START_KIND
		),
		position
	});
	return [withNextIds(start, entry ? [entry] : []), ...nodes];
}

/**
 * Splice a new node into an existing wire: predecessor → new node → the old target.
 *
 * The new node is inserted directly after its predecessor in the array, not appended, because
 * array order is what the derived `steps` projection preserves and therefore what every list of
 * this flow's steps reads back in.
 */
export function spliceNode(nodes: FlowNode[], edge: FlowEdgeRef, node: FlowNode): FlowNode[] {
	const wired: FlowNode = { ...node };
	if (isFanOutKind(wired.kind)) {
		// Both arms inherit the old target: a branch spliced onto a wire is a question about a path
		// that already goes somewhere, and an arm wired to nothing is a publish refusal.
		wired.config = {
			...wired.config,
			branches: branchesOf(wired).map((b) => ({ ...b, next: edge.target }))
		};
	} else if (!isTerminalKind(wired.kind)) {
		wired.next = edge.target;
	}
	const at = nodes.findIndex((n) => n.id === edge.source);
	const inserted = [...nodes.slice(0, at + 1), wired, ...nodes.slice(at + 1)];
	// `retargetEdge`, not `setEdge`: the wire being spliced may be one of several leaving the same
	// socket, and replacing the whole fan would silently delete its siblings.
	return retargetEdge(inserted, edge, wired.id);
}

/** Is there room for another arm on this branch? `caps.maxBranchesPerNode` is a publish refusal, so
 *  the builder has to stop before it, not after: an author who wires a ninth path and is told at
 *  publish has done work they now have to undo. */
export function canAddBranchArm(node: FlowNode): boolean {
	return node.kind === 'branch' && branchesOf(node).length < (FLOW_CAPS.maxBranchesPerNode ?? 8);
}

/**
 * Add an arm to a branch, respecting the cap. Returns the array unchanged when there is no room —
 * the caller reports the refusal, because only it knows how (a toast on the canvas, an inline
 * message in the inspector).
 *
 * The new arm is inserted BEFORE the else rather than appended: arms are evaluated in array order
 * and the else is the fallback, so appending after it would produce a list that reads as though the
 * new condition could never be reached. (It could — `next_node` consults the else last whatever its
 * position — but a document that has to be explained is a document that will be misread.)
 */
export function addBranchArm(nodes: FlowNode[], id: string, label?: string): FlowNode[] {
	return replaceNode(nodes, id, (node) => {
		if (!canAddBranchArm(node)) return node;
		const arms = branchesOf(node);
		const taken = new Set(arms.map((a) => a.id));
		let n = arms.length + 1;
		while (taken.has(`b${n}`)) n++;
		const arm: FlowBranch = {
			id: `b${n}`,
			label: label ?? `When…`,
			conditions: { field: '', operator: 'equals', value: '' },
			next: null
		};
		const elseAt = arms.findIndex((a) => a.conditions === null);
		const at = elseAt === -1 ? arms.length : elseAt;
		return {
			...node,
			config: { ...node.config, branches: [...arms.slice(0, at), arm, ...arms.slice(at)] }
		};
	});
}

/** Remove an arm. The wire that hung off it goes with it — the canvas re-derives its edges from the
 *  arms, so there is nothing else to clean up. */
export function removeBranchArm(nodes: FlowNode[], id: string, armId: string): FlowNode[] {
	return replaceNode(nodes, id, (node) => ({
		...node,
		config: { ...node.config, branches: branchesOf(node).filter((a) => a.id !== armId) }
	}));
}

/** Every socket that points at this node — what a delete has to repair, and what "nothing leads
 *  here" is the absence of. */
export function incomingEdges(nodes: FlowNode[], id: string): FlowEdgeRef[] {
	return nodes.flatMap((n) => outgoingEdges(n).filter((e) => e.target === id));
}

// =================================================================================================
// Writing the definition back
// =================================================================================================

/** The envelope keys a work node carries on the wire, beyond the ones every node has. */
const WORK_KEYS = [
	'type',
	'required',
	'assignee',
	'scope',
	'visibility',
	'per_participant',
	'tasks'
] as const;

/** One node, serialised to the authored wire shape (`nodeEnvelope`). Whitelisted rather than
 *  spread: a node read off a legacy definition carries keys the v3 document has no home for, and
 *  round-tripping them would quietly re-introduce whatever the last editor left behind. */
function serialiseNode(node: FlowNode): Record<string, unknown> {
	const out: Record<string, unknown> = {
		id: node.id,
		kind: node.kind,
		title: node.title ?? '',
		config: node.config ?? {},
		position: node.position ?? null,
		depends_on: node.depends_on ?? []
	};
	if (isWorkKind(node.kind)) {
		for (const key of WORK_KEYS) {
			// An action node has no fan-out and raises no tasks of its own; emitting the step-only keys
			// would put shapes in the document the action validator neither reads nor expects.
			if (node.kind === 'action' && (key === 'per_participant' || key === 'tasks')) continue;
			const value = (node as unknown as Record<string, unknown>)[key];
			if (value !== undefined) out[key] = value;
		}
	}
	if (!isFanOutKind(node.kind) && !isTerminalKind(node.kind)) {
		out.next = canonicalNext(nextIds(node));
	}
	return out;
}

/**
 * Is this draft still expressible in the pre-v3 shape?
 *
 * It is when every node is a human step and no node has a successor — which is exactly what
 * `toNodes` produces from a legacy document, because a legacy document has no edges of its own and
 * the read invents none. So "expressible as `{steps}`" is a shape test, not a re-derivation: the
 * moment anything else is on the canvas — a control node, an action node, a wire, the start node —
 * the draft is a graph and is saved as one.
 *
 * This is the switch that decides which document a save writes. Legacy in, legacy out, until the
 * author reaches for something only v3 can express; at that moment the page CONVERTS the draft
 * (`convert.ts::convertLegacy`) rather than merely re-serialising it, so the wiring and the
 * conditions arrive as authored nodes instead of being silently left behind.
 */
export function isLegacyShaped(nodes: FlowNode[]): boolean {
	return nodes.every((node) => node.kind === 'step' && nextIds(node).length === 0);
}

/** One node written back in the pre-v3 step shape — `kind` and `next` dropped, everything else as
 *  it came in. Keys whose value means the same as their absence are omitted, so a definition that
 *  never carried `position` or an empty `depends_on` does not grow them by being opened. */
function serialiseLegacyStep(node: FlowNode): Record<string, unknown> {
	const { kind: _kind, next: _next, position, depends_on, ...step } = node;
	void _kind;
	void _next;
	return {
		...step,
		...(position ? { position } : {}),
		...(depends_on?.length ? { depends_on } : {})
	};
}

/**
 * The authored definition, ready to POST — in whichever of the two shapes the draft actually is.
 *
 * **Legacy in, legacy out.** While the draft is still legacy-shaped (`isLegacyShaped`) this emits
 * `{steps, milestones, intake}`, ordering preserved, so opening a live journey and saving it is a
 * semantic no-op. That is the whole point: no published onboarding can regress because somebody
 * looked at it. The upgrade to `{nodes}` happens when the author reaches for something only v3 can
 * express, which is the first moment the new shape is carrying its own weight.
 *
 * `steps` is deliberately absent from the v3 shape. The API derives it from `nodes` on every save
 * and `graph.parse` ignores an authored one, so sending it would be sending a value that is
 * discarded at best and a second, stale source of truth at worst.
 */
export function toDefinition(
	nodes: FlowNode[],
	milestones: FlowMilestone[],
	intake?: FlowIntake | null
): Record<string, unknown> {
	if (isLegacyShaped(nodes)) {
		// `visibility` is written back verbatim: the engine still evaluates it at plan time, and every
		// live legacy journey carries one on most of its steps. What changed in the Aug 2026 pass is
		// that the BUILDER no longer authors it on the step — a condition is its own node — so this is
		// a round-trip, never an edit. The condition node itself cannot appear here: a draft holding
		// one is not legacy-shaped, and converting is what puts it there.
		return {
			steps: nodes.filter((n) => n.kind === 'step').map(serialiseLegacyStep),
			milestones,
			intake: intake ?? null
		};
	}
	return { nodes: nodes.map(serialiseNode), milestones, intake: intake ?? null };
}
