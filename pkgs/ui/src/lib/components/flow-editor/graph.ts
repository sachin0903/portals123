/**
 * The authored graph ↔ xyflow projection for the flow builder.
 *
 * Before v3 this module built a *derived* picture: it grouped steps into tiers, inferred sequence
 * edges from that grouping, inserted gate markers between tiers and rendered a step's `visibility`
 * as a pseudo "decision" node. None of that was in the document — it was the editor's reading of a
 * flat step list, which is why nothing on the canvas could be authored and why a dragged node
 * snapped back on the next render.
 *
 * v3 has real nodes and real edges, so this module is now a projection and nothing more: one
 * xyflow node per `FlowNode`, one edge per authored socket (`outgoingEdges`), plus the emphasised
 * `depends_on` edges. Every rule that rewrites the graph — connect, splice, delete-with-repair —
 * lives in `@graphene/ui/book/journeys` (`nodes.ts`) where it is pure and unit-tested; this file
 * owns only what needs xyflow or dagre: geometry, chrome and the layout contract.
 *
 * **The layout contract** (flow-contract.json → `nodeEnvelope.$comment_position`): positions are
 * persisted and honoured, and the dagre pass places only the nodes whose position is null. The
 * canvas commits the whole visible arrangement the first time an author places anything (see
 * `FlowCanvas`), so a half-placed graph — the one state where "some saved, some computed" would
 * produce overlaps — never survives a save.
 *
 * **Positions in the document are ABSOLUTE.** xyflow gives a node inside a container a position
 * relative to that container, so the two are converted at this boundary (`toRelative` on the way
 * out, `toAbsolute` on the way back in) and the stored coordinate system never changes. Getting
 * that wrong is invisible until somebody drags something.
 */
import type { Node, Edge } from '@xyflow/svelte';
import { MarkerType } from '@xyflow/svelte';
import dagre from '@dagrejs/dagre';
import { ACTION_BY_TYPE, TRIGGER_BY_KEY, humaniseKey } from '@graphene/ui/automations';
import {
	branchesOf,
	conditionArmsOf,
	conditionsOf,
	entryNodeId,
	isWorkKind,
	outgoing,
	outgoingEdges,
	startNode,
	CONDITION_KIND,
	type FlowNode,
	type FlowNodeKind,
	type FlowEdgeRef,
	type FlowPosition,
	type FlowStep,
	type FlowStepType,
	type FlowScope,
	type FlowForm,
	type FlowDocument,
	type FlowTemplate
} from '@graphene/ui/book/journeys';
import { nodeHasIssues, type GroupedIssues } from './validation';

export type Lookups = { forms: FlowForm[]; documents: FlowDocument[]; templates: FlowTemplate[] };

/** The synthetic "starts here" marker, drawn only when the graph has NO `start` node. It is not a
 *  node in the document — the entry is derived, exactly as `graph.py::_entry_for` derives it — but
 *  it is the handle an author clicks to edit what the journey collects before it begins (the
 *  intake block). A graph that HAS a start node draws that instead: same place, same look, but a
 *  real node with a real socket, which is what a fan-out needs to hang off. */
export const ENTRY_ID = '__entry__';
/** What `selectedId` becomes when the entry marker is clicked. */
export const INTAKE_ID = '__intake__';

/** Visual categories → Crisp-style solid colours (self-contained white-text nodes). */
export type Category = 'client' | 'gate' | 'action';
const CATEGORY_OF: Record<FlowStepType, Category> = {
	fill_form: 'client',
	accept_terms: 'client',
	upload_document: 'client',
	verify_identity: 'gate',
	aml_check: 'gate',
	review: 'gate',
	generate_document: 'action',
	setup_account: 'action',
	task: 'action'
};
/** A graph node is a small dark surface carrying near-white ink, so its fill takes the semantic
 *  token's HUE and CHROMA but pins its lightness — `oklch(from … L c h)` relative colour syntax.
 *  A firm re-branding `--info` re-hues the node, while the node can never drift light enough to
 *  lose the ink (a raw `var(--info)` is L 0.72 in dark mode and kills white text). `head` is a
 *  darker step of the same hue for the header band; `sub` is the on-dark muted ink taken off
 *  `--hero-foreground` rather than a literal white.
 *
 *  Do NOT reach for `color-mix(… var(--hero-base))` here: mixing in oklch interpolates HUE, and
 *  hero-base is a blue ink, so an amber token lands on red and a red token on magenta — verified
 *  in the browser. Relative syntax cannot drift.  Consumed as inline `style="background:…"`, where
 *  var() resolves normally — these are real CSS values, not a palette. */
const onHero = (pct: number) => `color-mix(in oklab, var(--hero-foreground) ${pct}%, transparent)`;
const nodeFill = (token: string, sub = 74) => ({
	bg: `oklch(from var(${token}) 0.48 c h)`,
	head: `oklch(from var(${token}) 0.38 c h)`,
	sub: onHero(sub)
});

export const CATEGORY_STYLE: Record<Category, { bg: string; head: string; sub: string }> = {
	client: nodeFill('--info', 72),
	gate: nodeFill('--destructive', 72),
	// The plain "we do it" category has no hue of its own — it IS the ink surface.
	action: {
		bg: 'var(--hero-base)',
		head: 'oklch(from var(--hero-base) 0.19 c h)',
		sub: onHero(65)
	}
};
export function categoryOf(type: FlowStepType): Category {
	return CATEGORY_OF[type] ?? 'action';
}

/** WHO performs a node's work — the thing an adviser most needs to see at a glance. Colour + label
 *  the node by this so the client's part is obviously distinct from the adviser's, compliance's and
 *  the engine's own. `system` is v3's addition: an action node is nobody's to-do. */
export type Actor = 'client' | 'adviser' | 'compliance' | 'system';
const COMPLIANCE_TYPES = new Set<FlowStepType>(['aml_check', 'review']);
export function actorOf(step: FlowStep | FlowNode): Actor {
	if ((step as FlowNode).kind === 'action') return 'system';
	if (step.assignee === 'client') return 'client';
	if (COMPLIANCE_TYPES.has(step.type as FlowStepType)) return 'compliance';
	return 'adviser';
}
/** WHO the node belongs to, as four token families that stay distinguishable under any branding:
 *  the client is advisory blue (`--info`), the adviser is the house accent (`--brand`), compliance is
 *  the caution hue (`--warning`), and the engine itself is the hue-less ink surface (`--hero-base`)
 *  — which is the right answer rather than a fifth colour, because "the system does this" is the
 *  absence of an owner, not another owner. A categorical scale with no good/bad meaning, so if a
 *  `--cat-*` ramp ever lands in theme.css these should move onto it.
 *
 *  DECISION (tokens, not literals). A graph does need distinguishable actors, and it was tempting to
 *  hardcode three picked hues. Measured in-browser at the default brand, these resolve to blue 203°,
 *  teal 181° and amber 33° — distinguishable, and each node pins its own lightness (L 0.48 / head
 *  0.38) so the fills never invert or wash out under `.dark`.
 *
 *  The honest weakness: `--info` and `--brand` sit only ~22° apart, the tightest pair, and a firm
 *  whose brand is itself blue will close that gap further. That is acceptable ONLY because hue is a
 *  redundant third channel here — StepNode labels every node with the actor's icon AND its name in
 *  text, so an actor is never identified by colour alone (which is also what makes it safe for
 *  colour-blind users). Do not remove the icon or the label on the assumption the fill carries it.
 *  If actors ever need to be told apart by fill alone, that is the point to add a real categorical
 *  ramp with enforced separation rather than to reach back for literals. */
export const ACTOR_STYLE: Record<Actor, { bg: string; head: string; sub: string }> = {
	client: nodeFill('--info'),
	adviser: nodeFill('--brand'),
	compliance: nodeFill('--warning'),
	system: CATEGORY_STYLE.action
};
export const ACTOR_LABEL: Record<Actor, string> = {
	client: 'Client',
	adviser: 'Adviser',
	compliance: 'Compliance',
	system: 'Automatic'
};

/**
 * The control kinds' accents.
 *
 * Control nodes are drawn as OUTLINED surfaces on the canvas background rather than as the filled
 * cards the work nodes use, and that is the load-bearing distinction: a branch is not somebody's
 * to-do, and giving it the same solid presence as a form the client has to fill in is what made the
 * pre-v3 pseudo-decision node read as work. So these are accents — an icon, a border, a title tint
 * — never a fill, and every one of them resolves from a semantic token so a re-brand reaches them.
 */
export const CONTROL_ACCENT: Record<string, string> = {
	start: 'var(--hero-base)',
	// A condition IS a branch, so it wears the branch accent: the two decide the same kind of thing
	// and only differ in how many answers they have. What tells them apart on the canvas is the icon
	// and the question printed on the card, not a second colour nobody would learn.
	condition: 'var(--brand)',
	branch: 'var(--brand)',
	wait: 'var(--muted-foreground)',
	wait_for_event: 'var(--muted-foreground)',
	start_flow: 'var(--info)',
	exit: 'var(--success)'
};

export const STEP_TYPE_LABEL: Record<FlowStepType, string> = {
	fill_form: 'Form',
	accept_terms: 'Terms',
	upload_document: 'Upload',
	generate_document: 'Document',
	verify_identity: 'Identity',
	aml_check: 'AML',
	review: 'Review',
	setup_account: 'Account',
	task: 'Task'
};
export const SCOPE_SHORT: Record<FlowScope, string> = {
	participant: 'Per person',
	account: 'Per account',
	application: 'Application'
};

/** The one-line config detail under a node's title (Crisp's "State: online" style). */
export function detailFor(step: FlowStep, lk: Lookups): string {
	const c = step.config ?? {};
	const name = (arr: { uid: string; name: string }[], uid: unknown) =>
		arr.find((x) => x.uid === uid)?.name ?? null;
	switch (step.type) {
		case 'fill_form':
			return name(lk.forms, c.form_uid) ?? 'No form chosen';
		case 'accept_terms':
			return (
				name(lk.documents, c.document_uid) ??
				(typeof c.content === 'string' && c.content.trim()
					? 'Written declaration'
					: 'No declaration chosen')
			);
		case 'generate_document':
			return name(lk.templates, c.template_uid) ?? 'No template chosen';
		case 'verify_identity':
			return `Provider: ${c.provider ?? 'manual'}`;
		case 'aml_check':
			return 'Screened automatically';
		case 'review':
			return c.gate ? `Raises: ${String(c.gate).replace(/_/g, ' ')}` : 'Compliance decision';
		case 'setup_account':
			return 'Open the account with the provider';
		case 'upload_document':
			return String(c.title ?? 'Client uploads a file');
		case 'task':
			return String(c.description ?? 'Manual to-do');
		default:
			return '';
	}
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`;

/**
 * The one-line detail for a node of ANY kind — what its config amounts to, in a phrase.
 *
 * A control node with nothing chosen says so ("No event chosen") rather than showing an empty line:
 * an unconfigured `wait_for_event` is the failure with no symptom (the case waits forever for an
 * event nobody sends), so the canvas names it before publish has to.
 */
export function detailForNode(node: FlowNode, lk: Lookups): string {
	const c = node.config ?? {};
	switch (node.kind) {
		case 'start': {
			const fan = outgoing(node).length;
			return fan > 1
				? `${plural(fan, 'path')} at once`
				: fan
					? 'The journey begins'
					: 'Leads nowhere';
		}
		case 'step':
			return detailFor(node as FlowStep, lk);
		case 'action':
			return ACTION_BY_TYPE[node.type ?? '']?.label ?? humaniseKey(node.type ?? 'Action');
		case 'condition':
			// The condition IS the detail here — the node's title names what it gates, and what an
			// author needs to read off the canvas is the test itself.
			return conditionSummary(conditionsOf(node)) ?? 'No conditions yet';
		case 'branch': {
			const arms = branchesOf(node);
			return arms.length ? plural(arms.length, 'path') : 'No paths yet';
		}
		case 'wait': {
			const days = Number(c.duration_days ?? 0);
			const hours = Number(c.duration_hours ?? 0);
			if (typeof c.until_date_field === 'string' && c.until_date_field)
				return `Until ${c.until_date_field}`;
			if (days) return plural(days, 'day') + (hours ? `, ${plural(hours, 'hour')}` : '');
			if (hours) return plural(hours, 'hour');
			return 'No duration set';
		}
		case 'wait_for_event': {
			const trigger = typeof c.trigger === 'string' ? c.trigger : '';
			return TRIGGER_BY_KEY[trigger]?.label ?? (trigger ? humaniseKey(trigger) : 'No event chosen');
		}
		case 'start_flow':
			return c.flow_code ? `Hands over to ${c.flow_code}` : 'No journey chosen';
		case 'exit':
			return `Ends as ${c.outcome ?? 'completed'}`;
		default:
			return '';
	}
}

const FIELD_SHORT: Record<string, string> = {
	'applicant.type': 'Applicant',
	'application.custody': 'Custody',
	'application.has_pension_product': 'Pension product',
	'application.has_sipp_product': 'SIPP',
	'application.has_transfer': 'Transfer',
	'account.product': 'Product',
	'account.custody': 'Custody',
	'account.has_transfer': 'Transfer'
};
const OP_SHORT: Record<string, string> = {
	equals: '=',
	notEquals: '≠',
	in: 'in',
	notIn: 'not in',
	contains: 'has',
	notContains: 'lacks',
	isEmpty: 'empty',
	isNotEmpty: 'set'
};
const VALUELESS = new Set(['isEmpty', 'isNotEmpty']);
function condText(c: { field?: string; operator?: string; value?: unknown }): string {
	const f = FIELD_SHORT[c.field ?? ''] ?? c.field ?? '';
	const op = OP_SHORT[c.operator ?? 'equals'] ?? c.operator ?? '';
	const v = VALUELESS.has(c.operator ?? '') ? '' : ` ${c.value ?? ''}`;
	return `${f} ${op}${v}`.trim();
}
/** A compact human summary of a condition object — a step's plan-time visibility, or a branch arm's
 *  runtime test. Both are the same shape, evaluated by the same evaluator. */
export function conditionSummary(visibility: unknown): string | null {
	if (!visibility || typeof visibility !== 'object') return null;
	const v = visibility as Record<string, unknown>;
	if (Array.isArray(v.conditions)) {
		const list = v.conditions as { field?: string; operator?: string; value?: unknown }[];
		if (!list.length) return null;
		const join = String(v.logic).toLowerCase() === 'or' ? ' or ' : ' and ';
		return list.map(condText).join(join) || null;
	}
	if ('field' in v)
		return condText(v as { field?: string; operator?: string; value?: unknown }) || null;
	return null;
}

// =================================================================================================
// Geometry
// =================================================================================================

export type FlowNodeData = { node: FlowNode; lookups: Lookups; hasIssues: boolean };
export type MarkerNodeData = { title: string; sub?: string };
/** A scope band's chrome. Just the words and whether the scope repeats — the `sub` line and the
 *  gate note went with the containers, which had a header strip to put them in; a band is an
 *  outline drawn around work that is already placed, and it must not reserve room for anything. */
export type TierNodeData = { label: string; repeats: boolean };

// =================================================================================================
// Tiers — the OTHER ordering, and the one the graph forgot
// =================================================================================================
// The engine has always ordered work two ways: by TIER (`scope`) and then by prerequisites within a
// tier. v3 modelled prerequisites as wires and, for a while, modelled tiers not at all — so a legacy
// journey whose thirteen dependency-free steps are spread across three tiers rendered as thirteen
// paths leaving one start node "at once". They cannot start at once, and drawing them that way was a
// lie the canvas told about the engine.
//
// So a tier is a CONTAINER: a real xyflow parent node, with its work genuinely inside it
// (`parentId` + `extent: 'parent'`) rather than sitting on a tinted rectangle. That is the "for each
// X" block — "for each client", "for each account" — and it is derived entirely from `scope`, which
// every work node already carries. `scope` IS the engine's per-person / per-account fan-out, so a
// second spelling in the document would only drift from it: a container is chrome, nothing authors
// one, it cannot be selected, dragged or deleted, and a definition gains nothing.
//
// `extent: 'parent'` is a statement, not a nicety: a node's tier is its `scope`, so it must not be
// possible to drag work out of the block that says how often it happens.

export const TIER_ORDER: FlowScope[] = ['participant', 'account', 'application'];

const TIER_PAD = 30;

export function tierId(scope: FlowScope): string {
	return `__tier_${scope}`;
}

// =================================================================================================
// Condition nodes — a two-armed branch, drawn on the spine
// =================================================================================================
// A `condition` is a `branch` with two arms (flow-contract.json → `controlNodes.condition`), so it
// needs nothing special here: it is a node with two labelled outlets, dagre ranks it like any other,
// and its arms are ordinary edges. It used to be drawn as a chrome-less annotation stacked on top of
// the card it gated, because it used to BE one — a plan-time gate nothing walked through. It is on
// the path now, and the picture says so.

/**
 * The populated tiers, in engine order, with the nodes that live in each.
 *
 * A work node belongs to its `scope`. A CONDITION belongs to the tier of the work its `true` arm
 * leads to — it has no scope of its own, but "only when the client is offshore" is asked exactly as
 * often as the work it decides on, so housing it anywhere else would strand it outside the block
 * that says how often it applies. (Its `false` arm usually re-converges into the next tier along,
 * which is why the arm the question is ABOUT is the one consulted.)
 *
 * Everything else — a wait, a branch, an ending — is housed nowhere and sits below the containers. A
 * control node's tier is genuinely unknown: it is reached by a wire, and a wire may cross tiers, so
 * guessing one would put it in a phase the engine never said it was in.
 */
export function tierGroups(nodes: FlowNode[]): { scope: FlowScope; nodes: FlowNode[] }[] {
	const scopeOf = new Map<string, FlowScope>();
	for (const node of nodes) {
		if (isWorkKind(node.kind)) scopeOf.set(node.id, (node.scope ?? 'application') as FlowScope);
	}
	for (const node of nodes) {
		if (node.kind !== CONDITION_KIND) continue;
		const decides = conditionArmsOf(node)?.when.next ?? outgoing(node)[0];
		if (decides && scopeOf.has(decides)) scopeOf.set(node.id, scopeOf.get(decides)!);
	}
	return TIER_ORDER.map((scope) => ({
		scope,
		nodes: nodes.filter((n) => scopeOf.get(n.id) === scope)
	})).filter((group) => group.nodes.length > 0);
}

/** The scope each node is asked at, including a condition's — which has none of its own but is
 *  asked exactly as often as the work its "yes" arm decides on. */
function scopeByNode(nodes: FlowNode[]): Map<string, FlowScope> {
	const scopeOf = new Map<string, FlowScope>();
	for (const node of nodes) {
		if (isWorkKind(node.kind)) scopeOf.set(node.id, (node.scope ?? 'application') as FlowScope);
	}
	for (const node of nodes) {
		if (node.kind !== CONDITION_KIND) continue;
		const decides = conditionArmsOf(node)?.when.next ?? outgoing(node)[0];
		if (decides && scopeOf.has(decides)) scopeOf.set(node.id, scopeOf.get(decides)!);
	}
	return scopeOf;
}

/** The user's own words for a scope. "For each client" rather than "participant": the band answers
 *  "how many times does this happen", and that is a sentence, not a field name. */
const SCOPE_WORDS: Record<FlowScope, string> = {
	participant: 'For each client',
	account: 'For each account',
	application: 'Once for the application'
};

/** Padding around a band's contents. Generous at the top so the label sits clear of the first card
 *  without the band having to reserve a header strip — nothing may move to make room for chrome. */
const BAND_PAD = { x: 20, top: 30, bottom: 16 };

export interface ScopeBand {
	scope: FlowScope;
	ids: string[];
	at: FlowPosition;
	size: { w: number; h: number };
}

/**
 * Dotted bands around RUNS of neighbouring work at the same scope.
 *
 * Strictly descriptive: computed from wherever the layout already put things, so it can never move
 * a node. That is the whole difference from the containers this replaces — those grouped by `scope`
 * and then laid each group out as a block, which is what made a tier-crossing spine zigzag between
 * boxes. A band only forms where the flow ALREADY runs several same-scope steps back to back, and
 * where it does not, there is simply no band.
 *
 * Consecutive in LAYOUT order (top to bottom), not in document order: what an author sees adjacent
 * is what gets drawn together. A node with no scope of its own — a wait, a branch, an ending —
 * breaks the run rather than being absorbed into a phase the engine never put it in. A run of one
 * gets no band: a box around a single card says nothing its own "Per person" line does not.
 */
export function scopeBands(
	nodes: FlowNode[],
	positions: Record<string, FlowPosition>
): ScopeBand[] {
	const scopeOf = scopeByNode(nodes);
	// The beginning is not work: it carries no `scope` of its own, and letting it default to one
	// drew the first band up over the start marker (top edge above the canvas origin).
	const begins = startNode(nodes)?.id;
	const placed = nodes
		.filter((n) => positions[n.id] && n.id !== begins)
		.sort(
			(a, b) => positions[a.id]!.y - positions[b.id]!.y || positions[a.id]!.x - positions[b.id]!.x
		);

	const runs: { scope: FlowScope; members: FlowNode[] }[] = [];
	for (const node of placed) {
		const scope = scopeOf.get(node.id);
		const last = runs[runs.length - 1];
		if (scope && last && last.scope === scope && last.members.length) last.members.push(node);
		else if (scope) runs.push({ scope, members: [node] });
		// A node with no scope of its own ends the run rather than being absorbed into a phase the
		// engine never put it in. The empty marker is never drawn, and `members.length` above stops
		// the next same-scope node reopening the run it just closed.
		else runs.push({ scope: 'application', members: [] });
	}

	return runs
		.filter((run) => run.members.length > 1)
		.map((run) => {
			const boxes = run.members.map((n) => ({ at: positions[n.id]!, size: nodeSize(n) }));
			const left = Math.min(...boxes.map((b) => b.at.x));
			const top = Math.min(...boxes.map((b) => b.at.y));
			const right = Math.max(...boxes.map((b) => b.at.x + b.size.w));
			const bottom = Math.max(...boxes.map((b) => b.at.y + b.size.h));
			return {
				scope: run.scope,
				ids: run.members.map((n) => n.id),
				at: { x: left - BAND_PAD.x, y: top - BAND_PAD.top },
				size: {
					w: right - left + BAND_PAD.x * 2,
					h: bottom - top + BAND_PAD.top + BAND_PAD.bottom
				}
			};
		});
}

/** A branch grows sideways with its arms, because each arm needs its own outlet along the bottom
 *  edge and a labelled outlet needs room to be read. Everything else is a fixed card. */
const BRANCH_ARM_W = 116;
const DIMS = {
	entry: { w: 190, h: 44 },
	// A real start node is the entry marker with a socket, so it is the same size — the canvas must
	// not visibly jump when an author converts one into the other.
	start: { w: 190, h: 44 },
	step: { w: 240, h: 96 },
	action: { w: 240, h: 76 },
	branch: { w: 240, h: 92 },
	wait: { w: 224, h: 64 },
	wait_for_event: { w: 240, h: 72 },
	start_flow: { w: 224, h: 64 },
	exit: { w: 200, h: 56 },
	// Two labelled outlets, same as a two-armed branch — because that is what it is. Slightly
	// narrower than a work card so the question reads as a question and the work reads as the work.
	condition: { w: 232, h: 84 }
} as const satisfies Record<string, { w: number; h: number }>;

/** The rendered size of a node — dagre needs it, and so does every placement rule. */
export function nodeSize(node: FlowNode): { w: number; h: number } {
	const base = DIMS[node.kind as keyof typeof DIMS] ?? DIMS.step;
	if (node.kind !== 'branch') return base;
	return { w: Math.max(base.w, branchesOf(node).length * BRANCH_ARM_W), h: base.h };
}

/* Edge chrome is token-driven: xyflow applies `markerEnd.color` as an inline `style:stroke`/`fill`
   on the arrowhead polyline and quotes the reference (`url('#…')`), so a var() is safe in both. */
const ARROW = {
	type: MarkerType.ArrowClosed,
	width: 16,
	height: 16,
	color: 'var(--muted-foreground)'
};

/** How far apart the layout puts ranks and neighbours. Exported because the splice rule has to know
 *  how much room a node needs before it can decide whether to push the graph apart. */
export const RANK_GAP = 60;
/** How wide a tier is allowed to get before a rank of parallel work wraps onto another row. Four
 *  cards and their gaps — wide enough to read a set at a glance, narrow enough to stay on screen. */
const MAX_TIER_WIDTH = 4 * 240 + 3 * 48;
const NODE_GAP = 48;

function edgeFor(ref: FlowEdgeRef, opts: { highlighted: boolean; interactive: boolean }): Edge {
	const emphasis = opts.highlighted
		? 'stroke:var(--brand);stroke-width:3'
		: ref.kind === 'timeout'
			? // A deadline is not the ordinary flow of work — it is the escape from waiting for one.
				'stroke:var(--muted-foreground);stroke-width:1.5;stroke-dasharray:5 4'
			: 'stroke:var(--muted-foreground);stroke-width:1.5';
	return {
		id: `${ref.source}:${ref.handle}->${ref.target}`,
		source: ref.source,
		sourceHandle: ref.handle,
		target: ref.target!,
		type: 'flow',
		label: ref.label,
		data: { ref: ref as unknown as Record<string, unknown>, interactive: opts.interactive },
		markerEnd: ARROW,
		style: emphasis,
		labelStyle: 'fill:var(--muted-foreground);font-weight:600;font-size:11px',
		selectable: opts.interactive,
		deletable: opts.interactive
	};
}

/** An authored `depends_on` edge — a REAL prerequisite the engine enforces, so it is drawn solid and
 *  emphasised, distinct from the flow edges. It is not draggable: a prerequisite is a join ("not
 *  until ALL of these"), which a single wire cannot express, so it is authored in the inspector. */
function dependencyEdge(source: string, target: string): Edge {
	return {
		id: `dep:${source}->${target}`,
		source,
		target,
		type: 'smoothstep',
		label: 'after',
		markerEnd: { ...ARROW, color: 'var(--primary)' },
		style: 'stroke:var(--primary);stroke-width:2',
		labelStyle: 'fill:var(--primary);font-weight:600;font-size:11px',
		selectable: false,
		deletable: false,
		focusable: false
	};
}

// =================================================================================================
// Layout
// =================================================================================================

/** True when no node is WIRED to another — the shape a legacy document reads as. Such a graph has
 *  no authored sequence at all.
 *
 *  What it decides is one thing: whether the entry marker draws an arrow. It does not decide the
 *  layout, because the layout is the tiers either way.
 *
 *  `depends_on` deliberately does not count — a prerequisite is drawn as the emphasised "after" edge
 *  it has always been, and it ranks the tree, but it is not a wire an author drew. */
function isUnwired(nodes: FlowNode[]): boolean {
	return nodes.every((n) => outgoing(n).length === 0);
}

/**
 * One tier's own subgraph, laid out as a VERTICAL TREE — top-down over its real edges, with
 * `depends_on` added as ranking edges, and every rank CENTRED on one axis.
 *
 * The centring is what makes a wired journey read as a spine: a rank of one sits under the middle of
 * the rank above it, and a condition's two arms sit side by side beneath it and re-converge on the
 * node below. Packing rows from the left instead would step the whole journey sideways every time a
 * fork opened.
 *
 * The distinction that keeps this honest: a `depends_on` edge is handed to dagre for RANK ONLY. It
 * is a prerequisite the engine already enforces, so it is legitimate for the picture to say "this
 * comes after that" — but nothing here writes an edge into the document, and two members with no
 * ordering between them land at the same rank, SIDE BY SIDE.
 *
 * A wide rank still WRAPS (`MAX_TIER_WIDTH`): same rank logically, several rows visually, so a fan
 * of parallel work stays roughly a screen wide instead of becoming a sprawl you have to pan. In a
 * wired spine that should almost never fire — it is the safety valve for the fan-outs, not the shape
 * of the normal case.
 *
 * Returns LOCAL coordinates (origin at the subgraph's top-left) plus the box they occupy.
 */
function treeLayout(members: FlowNode[]): {
	at: Record<string, FlowPosition>;
	w: number;
	h: number;
} {
	const at: Record<string, FlowPosition> = {};
	if (!members.length) return { at, w: 0, h: 0 };

	const g = new dagre.graphlib.Graph();
	g.setGraph({ rankdir: 'TB', ranksep: RANK_GAP, nodesep: NODE_GAP, marginx: 0, marginy: 0 });
	g.setDefaultEdgeLabel(() => ({}));
	const inside = new Set(members.map((n) => n.id));
	// dagre's own vocabulary is `width`/`height`; ours is `w`/`h`. Passing our shape straight in is
	// silently accepted and every node lands on the default size, which collapses the layout into
	// one overlapping heap — so the translation is explicit.
	for (const node of members) {
		const size = nodeSize(node);
		g.setNode(node.id, { width: size.w, height: size.h });
	}
	for (const node of members) {
		for (const ref of outgoingEdges(node)) {
			if (ref.target && inside.has(ref.target)) g.setEdge(node.id, ref.target);
		}
		for (const dep of node.depends_on ?? []) {
			if (inside.has(dep) && dep !== node.id) g.setEdge(dep, node.id);
		}
	}
	dagre.layout(g);

	// Group the nodes into ranks by dagre's y, then stack the ranks OURSELVES. dagre's x ORDER within
	// a rank is kept (it puts a chain's nodes under their parent, and a fork's arms in arm order), but
	// its coordinates are discarded: we wrap, and we centre.
	const ranks = [...new Set(members.map((n) => Math.round(g.node(n.id)?.y ?? 0)))].sort(
		(a, b) => a - b
	);
	const rows: FlowNode[][] = [];
	for (const key of ranks) {
		const rank = members
			.filter((n) => Math.round(g.node(n.id)?.y ?? 0) === key)
			.sort((a, b) => (g.node(a.id)?.x ?? 0) - (g.node(b.id)?.x ?? 0));
		let row: FlowNode[] = [];
		let rowW = 0;
		for (const node of rank) {
			const width = nodeSize(node).w;
			if (row.length && rowW + NODE_GAP + width > MAX_TIER_WIDTH) {
				rows.push(row);
				row = [];
				rowW = 0;
			}
			row.push(node);
			rowW += (rowW ? NODE_GAP : 0) + width;
		}
		if (row.length) rows.push(row);
	}

	const widthOf = (row: FlowNode[]) =>
		row.reduce((sum, n, i) => sum + nodeSize(n).w + (i ? NODE_GAP : 0), 0);
	const w = rows.length ? Math.max(...rows.map(widthOf)) : 0;
	let h = 0;
	let cursorY = 0;
	for (const row of rows) {
		// Card TOPS are aligned across a row: siblings on one row are work that happens together, and
		// reading that off one line means reading one line.
		let x = (w - widthOf(row)) / 2;
		let tallest = 0;
		for (const node of row) {
			const size = nodeSize(node);
			at[node.id] = { x, y: cursorY };
			x += size.w + NODE_GAP;
			tallest = Math.max(tallest, size.h);
		}
		h = Math.max(h, cursorY + tallest);
		cursorY += tallest + RANK_GAP;
	}
	return { at, w, h };
}

/**
 * Where every node goes when nothing is placed: a tree per tier, the tiers stacked in engine order,
 * and whatever has no tier below them all.
 *
 * The two orderings the engine actually has, drawn as the two things they are — the containers are
 * the tiers, and the tree inside each is `depends_on`. Deterministic for a given document, which is
 * what lets an unplaced node be laid out the same way on every open instead of wandering.
 */
function tieredLayout(nodes: FlowNode[]): Record<string, FlowPosition> {
	const out: Record<string, FlowPosition> = {};
	const start = startNode(nodes);
	// ONE tree over the whole graph, in the order it runs. `treeLayout` ranks on the wires AND
	// `depends_on`, so this is the execution order and nothing else.
	const laid = treeLayout(nodes.filter((n) => n.id !== start?.id));
	const top = DIMS.entry.h + RANK_GAP;
	for (const [id, local] of Object.entries(laid.at)) {
		out[id] = { x: TIER_PAD + local.x, y: top + local.y };
	}
	out[ENTRY_ID] = { x: TIER_PAD, y: 0 };
	if (start) out[start.id] = { x: TIER_PAD, y: 0 };
	return out;
}

/** Which container each node lives in, and where each container's origin is. The bridge between the
 *  document's ABSOLUTE coordinates and the RELATIVE ones xyflow gives a child node. */
export interface Containment {
	/** node id → container id. Absent for anything housed nowhere. */
	parentOf: Record<string, string>;
	/** container id → its absolute top-left. */
	origins: Record<string, FlowPosition>;
}

export const NO_CONTAINMENT: Containment = { parentOf: {}, origins: {} };

/** A child's position as the DOCUMENT holds it, from the relative one xyflow reports. */
export function toAbsolute(c: Containment, id: string, at: FlowPosition): FlowPosition {
	const parent = c.parentOf[id];
	const origin = parent ? c.origins[parent] : undefined;
	return origin ? { x: at.x + origin.x, y: at.y + origin.y } : at;
}

/** A child's position as XYFLOW wants it, from the absolute one the document holds. */
export function toRelative(c: Containment, id: string, at: FlowPosition): FlowPosition {
	const parent = c.parentOf[id];
	const origin = parent ? c.origins[parent] : undefined;
	return origin ? { x: at.x - origin.x, y: at.y - origin.y } : at;
}

/**
 * The tier containers, sized around wherever their members ACTUALLY ended up.
 *
 * Chrome, not content: computed from positions, so a dragged node takes its container's edge with
 * it. The containers share a left edge and a width so the tiers read as one stack rather than three
 * ragged boxes, and each carries `TIER_SLACK` of room beyond its work — see the constant for why
 * `extent: 'parent'` makes that room load-bearing rather than decorative.
 */
export function tierContainers(
	nodes: FlowNode[],
	positions: Record<string, FlowPosition>
): { containers: Node[]; containment: Containment } {
	// Bands, not containers. The distinction is the whole point and it is worth stating: a container
	// grouped by `scope` and then LAID OUT each group, which is what made a tier-crossing spine
	// zigzag between boxes (measured on the seeded flow: y = 1228 -> 1580 -> 2856 -> 1892 -> 328).
	// A band is drawn around wherever the layout already put things and can never move anything.
	//
	// So `containment` stays EMPTY: no `parentId`, no `extent: 'parent'`. A parented child is
	// clamped to its parent's box, which is exactly the power a band must not have. That also keeps
	// `toAbsolute`/`toRelative` the identity, so `FlowCanvas` needs no branch.
	const containers = scopeBands(nodes, positions).map((band) => ({
		id: `__band_${band.scope}_${band.ids[0]}`,
		type: 'tier',
		position: band.at,
		width: band.size.w,
		height: band.size.h,
		draggable: false,
		selectable: false,
		deletable: false,
		connectable: false,
		focusable: false,
		// Behind the cards, and out of the way of every pointer: chrome must never eat a click meant
		// for the work sitting on top of it.
		zIndex: -1,
		data: { label: SCOPE_WORDS[band.scope], repeats: band.scope !== 'application' }
	})) as unknown as Node[];
	return { containers, containment: NO_CONTAINMENT };
}

export function layoutPositions(nodes: FlowNode[]): Record<string, FlowPosition> {
	return tieredLayout(nodes);
}

/**
 * Every node's position on the canvas: what the author placed, and dagre for the rest.
 *
 * THE LAYOUT CONTRACT. Auto-layout is the fallback for an unplaced node, never an authority that
 * overwrites a placement — which is the whole reason dragging can exist at all now. The dagre pass
 * is skipped entirely when every node is placed, so typing in the inspector does not re-run a
 * layout over two hundred nodes on every keystroke.
 */
export function resolvePositions(nodes: FlowNode[]): Record<string, FlowPosition> {
	const unplaced = nodes.some((n) => !n.position);
	const laid = unplaced || !nodes.length ? layoutPositions(nodes) : {};
	const out: Record<string, FlowPosition> = {};
	if (laid[ENTRY_ID]) out[ENTRY_ID] = laid[ENTRY_ID];
	for (const node of nodes) out[node.id] = node.position ?? laid[node.id] ?? { x: 0, y: 0 };
	// A real start node IS the marker, so it needs no spot of its own.
	if (startNode(nodes)) return out;
	if (!out[ENTRY_ID]) {
		// Every node was placed, so the marker has no laid-out spot: sit it above the entry node.
		const entry = entryNodeId(nodes);
		const anchor = entry ? out[entry] : null;
		out[ENTRY_ID] = anchor
			? { x: anchor.x + 25, y: anchor.y - DIMS.entry.h - RANK_GAP }
			: { x: 0, y: 0 };
	}
	return out;
}

/**
 * Where to drop a node the author added without aiming at anything — below the node they had
 * selected, or below the whole graph. Anywhere but (0, 0), which in a laid-out graph is on top of
 * the first node.
 */
export function placeAfter(
	nodes: FlowNode[],
	positions: Record<string, FlowPosition>,
	afterId: string | null,
	size: { w: number; h: number }
): FlowPosition {
	const anchor = afterId ? nodes.find((n) => n.id === afterId) : null;
	if (anchor && positions[anchor.id]) {
		const at = positions[anchor.id]!;
		const anchorSize = nodeSize(anchor);
		return { x: at.x + anchorSize.w / 2 - size.w / 2, y: at.y + anchorSize.h + RANK_GAP };
	}
	const placed = nodes
		.map((n) => positions[n.id])
		.filter((position): position is FlowPosition => position !== undefined);
	if (!placed.length) return { x: 0, y: 0 };
	const lowest = placed.reduce((a, b) => (b.y > a.y ? b : a));
	return { x: lowest.x, y: lowest.y + DIMS.step.h + RANK_GAP };
}

/**
 * Make room for a node spliced onto a wire, and say where it goes.
 *
 * The pushing-apart is the difference between a splice that feels like an insertion and one that
 * feels like a collision: dropping a node between two neighbours a rank apart would otherwise land
 * it on top of the one below. Everything at or below the old target moves down by exactly the
 * shortfall, so the rest of the arrangement keeps its shape.
 */
export function spliceLayout(
	positions: Record<string, FlowPosition>,
	edge: { source: string; target: string | null },
	sizes: { source: { w: number; h: number }; inserted: { w: number; h: number } }
): { positions: Record<string, FlowPosition>; position: FlowPosition } {
	const from = positions[edge.source];
	const to = edge.target ? positions[edge.target] : null;
	if (!from) return { positions, position: { x: 0, y: 0 } };
	const top = from.y + sizes.source.h + RANK_GAP;
	const position = {
		x: from.x + sizes.source.w / 2 - sizes.inserted.w / 2,
		y: top
	};
	if (!to) return { positions, position };
	const needed = top + sizes.inserted.h + RANK_GAP;
	const shortfall = needed - to.y;
	if (shortfall <= 0) return { positions, position };
	const moved: Record<string, FlowPosition> = {};
	for (const [id, at] of Object.entries(positions)) {
		moved[id] = at.y >= to.y ? { x: at.x, y: at.y + shortfall } : at;
	}
	return { positions: moved, position };
}

/**
 * Client coordinates → flow coordinates. The same arithmetic `useSvelteFlow().screenToFlowPosition`
 * does, kept as a pure function because the drop handler lives on the wrapper element (outside the
 * SvelteFlow component, so the hook is not available there) and because a drop landing in the wrong
 * place is exactly the kind of bug a test should catch.
 */
export function screenToFlow(
	rect: { left: number; top: number },
	viewport: { x: number; y: number; zoom: number },
	client: { x: number; y: number }
): FlowPosition {
	return {
		x: (client.x - rect.left - viewport.x) / viewport.zoom,
		y: (client.y - rect.top - viewport.y) / viewport.zoom
	};
}

// =================================================================================================
// The projection
// =================================================================================================

export interface GraphViewOptions {
	lookups: Lookups;
	issues: GroupedIssues;
	selectedId: string | null;
	/** The wire a palette drag is currently hovering, drawn thick so the author can see where the
	 *  node would land. */
	highlightEdgeId?: string | null;
	/** False for a read-only viewer: no reconnect anchors, no edge deletion. */
	canManage: boolean;
}

/** The authored graph as xyflow sees it, the ABSOLUTE positions it was drawn at (which the canvas
 *  commits back to the document the moment the author places anything), and which container each
 *  node sits in — because a child's `position` on the canvas is relative to its container, and the
 *  document's is not. */
export interface GraphView {
	nodes: Node[];
	edges: Edge[];
	positions: Record<string, FlowPosition>;
	containment: Containment;
}

export function buildGraph(nodes: FlowNode[], opts: GraphViewOptions): GraphView {
	const positions = resolvePositions(nodes);
	const entry = entryNodeId(nodes);
	const known = new Set(nodes.map((n) => n.id));
	// One beginning on the canvas, never two: an authored start node replaces the derived marker
	// rather than sitting beneath it.
	const marker = startNode(nodes) ? null : entry;

	// Containers FIRST: xyflow requires a parent to appear before its children in the array.
	const { containers, containment } = tierContainers(nodes, positions);
	const view: Node[] = [...containers];
	if (marker) {
		view.push({
			id: ENTRY_ID,
			type: 'entry',
			position: positions[ENTRY_ID]!,
			deletable: false,
			// The entry is DERIVED (the node nothing points at), so its marker is a label, not a
			// socket: wiring from it would author an edge with no field to live in.
			connectable: false,
			selected: opts.selectedId === INTAKE_ID,
			data: { title: 'Journey starts' } as MarkerNodeData
		});
	}
	for (const node of nodes) {
		const parentId = containment.parentOf[node.id];
		view.push({
			id: node.id,
			type: node.kind,
			// RELATIVE to the container, absolute outside one. The document keeps absolute
			// coordinates and the layout contract is untouched; the conversion happens here and in
			// `FlowCanvas.commitPositions`, and nowhere else.
			position: toRelative(containment, node.id, positions[node.id]!),
			...(parentId ? { parentId, extent: 'parent' as const } : {}),
			selected: opts.selectedId === node.id,
			data: {
				node,
				lookups: opts.lookups,
				hasIssues: nodeHasIssues(opts.issues, node.id)
			} as FlowNodeData
		});
	}

	const edges: Edge[] = [];
	// The marker wires to the first node only when there IS a sequence to join. In an unwired graph
	// the entry is whichever node happened to be first in the list, and drawing an arrow to it would
	// single one card out of a set as "the one that starts" — untrue, and the same class of invented
	// edge that made this canvas claim thirteen paths began at once.
	if (marker && !isUnwired(nodes)) {
		edges.push({
			id: `${ENTRY_ID}->${marker}`,
			source: ENTRY_ID,
			target: marker,
			type: 'smoothstep',
			markerEnd: ARROW,
			style: 'stroke:var(--muted-foreground);stroke-width:1.5',
			selectable: false,
			deletable: false,
			focusable: false
		});
	}
	for (const node of nodes) {
		for (const ref of outgoingEdges(node)) {
			// A dangling target is reported by publish, not drawn: an edge to a node that is not here
			// has no second end to draw it to, and xyflow drops it anyway.
			if (!ref.target || !known.has(ref.target)) continue;
			edges.push(
				edgeFor(ref, {
					highlighted: opts.highlightEdgeId === `${ref.source}:${ref.handle}->${ref.target}`,
					interactive: opts.canManage
				})
			);
		}
		for (const dep of node.depends_on ?? []) {
			if (!known.has(dep) || dep === node.id) continue;
			// A flow edge already joining the same pair says the same thing and says it better: since
			// the legacy read wires `depends_on` as real edges, drawing both would double every wire
			// in an untouched legacy journey. The prerequisite is still visible in the inspector.
			if (edges.some((e) => e.source === dep && e.target === node.id)) continue;
			edges.push(dependencyEdge(dep, node.id));
		}
	}

	return { nodes: view, edges, positions, containment };
}

/** The kinds a palette drag may splice into a wire, and the node types the canvas registers — kept
 *  here so `FlowCanvas` reads as wiring rather than as a list of literals. */
export const CANVAS_NODE_KINDS: readonly FlowNodeKind[] = [
	'start',
	'step',
	'action',
	'condition',
	'branch',
	'wait',
	'wait_for_event',
	'start_flow',
	'exit'
];
