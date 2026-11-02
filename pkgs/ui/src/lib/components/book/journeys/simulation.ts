/**
 * The dry-run vocabulary — the TypeScript side of `POST /flows/{uid}/simulate`
 * (`apps/api/app/flows/simulate.py`).
 *
 * The verdict itself comes from the SERVER, evaluated by the same evaluator the engine uses. That
 * is the whole point and it is not negotiable: a browser-side re-implementation of "does this
 * branch fire" is a second semantics, and the shared contract exists to prevent exactly that. What
 * lives here is presentation — turning a hop into a sentence, a duration into "in 7 days", an
 * exclusion reason into an explanation an author can act on — plus the types.
 *
 * `explanation` nodes are the SAME `ExplainNode` shape the rule tier's dry run returns, so the
 * per-condition verdict renders identically in both tiers. An author who has read one has read
 * both, which is the point of one evaluator with one explanation format.
 */
import contract from './flow-contract.json' with { type: 'json' };
import type { ExplainNode } from '../../automations/types.js';
import type { FlowNodeKind } from './nodes.js';

export type { ExplainNode };

/** How the walk ended. Mirrors `simulate.OUTCOME_KINDS`. */
export type SimOutcomeKind =
	'exit' | 'handed_off' | 'completed' | 'unfinished' | 'dangling' | 'looped' | 'too_long';

/** Why a node is not on this subject's path. Mirrors `simulate.EXCLUSION_REASONS`. */
export type SimExclusionReason =
	'branch_not_taken' | 'timeout_only' | 'not_planned' | 'orphan' | 'unreached';

/** One concrete step row the plan would create, and which tier it runs at. */
export interface SimInstance {
	case_kind: 'profile' | 'account' | 'application' | string;
	subject_type: 'contact' | 'account' | 'group' | string;
	subject_id: number | null;
	participant_subject_id: number | null;
}

/** One arm of a branch, with its own verdict. `passed: null` means the arm was never evaluated —
 *  first-satisfiable-wins stopped before it. */
export interface SimArm {
	id: string;
	label: string;
	is_else: boolean;
	next: string | null;
	reached: boolean;
	passed: boolean | null;
	taken: boolean;
	explanation: ExplainNode | null;
}

/** One node the case enters, in order. */
export interface SimHop {
	node_id: string;
	kind: FlowNodeKind;
	title: string;
	type: string | null;
	/** True where the real engine stops and waits. The simulation walks on regardless — this is
	 *  what says so, and the panel must render it or the path reads as instantaneous. */
	blocking: boolean;
	instances: SimInstance[];
	visibility: ExplainNode | null;
	scope: string | null;
	assignee: string | null;
	required: boolean;
	arms: SimArm[];
	taken_arm: string | null;
	elided: string[];
	resumes_at: string | null;
	listens_for: string | null;
	timeout_branch: string | null;
	at_day: number;
	config: Record<string, unknown>;
	note: string | null;
}

export interface SimExcluded {
	node_id: string;
	kind: FlowNodeKind;
	title: string;
	reason: SimExclusionReason;
	because_of: string | null;
}

export interface SimTiers {
	cases: number;
	profile_cases: number;
	account_cases: number;
	application_cases: number;
	step_instances: number;
	participants: number;
	accounts: number;
}

export interface SimSubjectSummary {
	kind: 'real' | 'synthetic';
	contact_id: number | null;
	label: string;
	accounts: { id: number | null; product: string | null; status: string | null }[];
	truncated: boolean;
}

/** `POST /flows/{uid}/simulate`. */
export interface FlowSimulation {
	/** Always false, and structurally so — see the module docstring on the Python side. */
	performed: false;
	/** How to read the report. `graph` walked a path; `legacy` reports the PLAN, because a pre-v3
	 *  `{steps}` definition authored no sequence to walk. */
	shape: 'graph' | 'legacy';
	path: SimHop[];
	excluded: SimExcluded[];
	outcome: {
		kind: SimOutcomeKind;
		node_id: string | null;
		detail: string | null;
		elapsed_days: number;
		outcome?: string;
		reason?: string | null;
		flow_code?: string | null;
	};
	tiers: SimTiers;
	facts: Record<string, unknown>;
	flow: { uid: string; code: string; name: string };
	version: { uid: string; version: number; status: string } | null;
	subject: SimSubjectSummary;
	/** Publish issues on the definition that was simulated — a walk over a graph that cannot be
	 *  published is still useful, but the author needs to know before they trust it. */
	issues: string[];
	simulated_at: string;
}

// ---------------------------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------------------------

const ACTION_TYPES = contract.actionTypes as Record<string, { config: string[] }>;

/** The action vocabulary the panel can describe. Derived from the contract so a new action type is
 *  one entry there, not a silent "does something" in the dry run. */
export const KNOWN_ACTION_TYPES: readonly string[] = Object.keys(ACTION_TYPES);

const OUTCOME_HEADLINE: Record<SimOutcomeKind, string> = {
	exit: 'The journey ends here',
	handed_off: 'The journey hands over to another one',
	completed: 'The journey completes',
	unfinished: 'The journey stops without an ending',
	dangling: 'The journey points at a step that isn’t there',
	looped: 'The journey loops back on itself',
	too_long: 'The journey didn’t settle'
};

/** Is this ending something the author meant, or something to fix before publishing? */
export function outcomeIsHealthy(kind: SimOutcomeKind): boolean {
	return kind === 'exit' || kind === 'handed_off' || kind === 'completed';
}

export function outcomeHeadline(sim: FlowSimulation): string {
	const base = OUTCOME_HEADLINE[sim.outcome.kind] ?? 'The journey ends';
	if (sim.outcome.kind === 'exit' && sim.outcome.outcome) {
		return `The journey ends — ${sim.outcome.outcome}`;
	}
	return base;
}

const EXCLUSION_LABEL: Record<SimExclusionReason, string> = {
	branch_not_taken: 'On a path this client doesn’t take',
	timeout_only: 'Only if the deadline passes',
	not_planned: 'Doesn’t apply to this client',
	orphan: 'Nothing leads here',
	unreached: 'Not reached'
};

/** Grouped so the panel can show the author's own design apart from their mistakes: an untaken arm
 *  is the flow working, an orphan is a node nobody can ever get to. */
export const EXCLUSION_IS_A_PROBLEM: Record<SimExclusionReason, boolean> = {
	branch_not_taken: false,
	timeout_only: false,
	not_planned: false,
	orphan: true,
	unreached: true
};

export function exclusionLabel(reason: SimExclusionReason): string {
	return EXCLUSION_LABEL[reason] ?? 'Not on this path';
}

/** "in 7 days", "in about 3 hours", "straight away" — how far into the journey a hop happens.
 *  Rounded on purpose: an author reading a preview wants the shape, and "in 6.97 days" reads as a
 *  measurement of something that has not happened yet. */
export function whenLabel(atDay: number): string {
	if (!Number.isFinite(atDay) || atDay <= 0) return 'straight away';
	if (atDay < 1) {
		const hours = Math.max(1, Math.round(atDay * 24));
		return `after about ${hours} ${hours === 1 ? 'hour' : 'hours'}`;
	}
	const days = Math.round(atDay);
	if (days < 14) return `after ${days} ${days === 1 ? 'day' : 'days'}`;
	const weeks = Math.round(days / 7);
	if (weeks < 9) return `after about ${weeks} weeks`;
	const months = Math.round(days / 30);
	return `after about ${months} ${months === 1 ? 'month' : 'months'}`;
}

/** How long the whole simulated journey takes, or null when nothing in it waits. */
export function totalDurationLabel(sim: FlowSimulation): string | null {
	const days = sim.outcome.elapsed_days;
	if (!days) return null;
	return whenLabel(days);
}

/** How many people/accounts a step fans out to, as a phrase — or null when it is a single
 *  instance and the extra words would be noise. */
export function fanOutLabel(hop: SimHop): string | null {
	const count = hop.instances.length;
	if (count <= 1) return null;
	const perPerson = hop.instances.every((i) => i.participant_subject_id !== null);
	return `${count} ${perPerson ? 'people' : 'times'}`;
}

/** The tier a step's instances run at, in the author's words rather than the engine's. */
export function tierLabel(hop: SimHop): string | null {
	const kinds = new Set(hop.instances.map((i) => i.case_kind));
	if (!kinds.size) return null;
	if (kinds.size > 1) return 'several tiers';
	const only = [...kinds][0] ?? '';
	const words: Record<string, string> = {
		profile: 'per person',
		account: 'per account',
		application: 'once'
	};
	return words[only] ?? only;
}

/**
 * A one-line description of what an action node would do, from its authored config.
 *
 * Deliberately shallow. The contract knows an action's config KEYS, not what a nice sentence about
 * it reads like, and inventing prose per action type in three places (here, the rule tier's
 * summary, the audit log) is how three descriptions of one act drift. So: the action's own name
 * plus the config value that identifies its target, which is the part an author is checking.
 */
export function actionSummary(hop: SimHop): string {
	const type = hop.type ?? 'action';
	const label = type.replace(/_/g, ' ');
	const config = hop.config ?? {};
	const identifying = [
		'tag',
		'template_code',
		'template_uid',
		'document_uid',
		'title',
		'status',
		'endpoint_key',
		'flow_code',
		'category'
	];
	for (const key of identifying) {
		const value = config[key];
		if (typeof value === 'string' && value.trim()) return `${label} — ${value}`;
	}
	return label;
}

/** Steps the whole walk would plan, flattened — the "what this client actually gets asked for"
 *  list, which is the question behind the dry run for most authors. */
export function plannedWork(sim: FlowSimulation): SimHop[] {
	return sim.path.filter((hop) => hop.kind === 'step' && hop.instances.length > 0);
}

/** Exclusions split into "by design" and "probably a mistake". */
export function splitExclusions(sim: FlowSimulation): {
	byDesign: SimExcluded[];
	problems: SimExcluded[];
} {
	const byDesign: SimExcluded[] = [];
	const problems: SimExcluded[] = [];
	for (const item of sim.excluded) {
		(EXCLUSION_IS_A_PROBLEM[item.reason] ? problems : byDesign).push(item);
	}
	return { byDesign, problems };
}

/** A client the dry run's subject picker offers — the host page supplies the firm's own book. */
export interface DryRunClient {
	id: number;
	name: string;
}
