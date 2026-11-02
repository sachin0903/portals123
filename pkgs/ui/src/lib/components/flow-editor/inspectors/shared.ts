/**
 * The pure bits every per-kind inspector needs: how a node is named in a picker, how a config key
 * is read and written, and the plain-English sentences the inspectors print under their controls.
 *
 * Pure and app-local on purpose. Pure, because "what does this wait node actually do" is a
 * sentence worth testing and a component is a poor place to test one. App-local, because the flow
 * editor is the documented single-app exception to "reusable UI lives in pkgs/ui" — nothing here
 * is a component, and nothing outside the builder has a use for "1st · if…".
 */
import { NODE_KIND_LABEL, type FlowNode } from '@graphene/ui/book/journeys';

/** What to call a node in a picker or a sentence: its title, else its kind. Never a bare slug —
 *  an author never typed the slug. */
export function nodeLabel(node: FlowNode): string {
	const title = (node.title ?? '').trim();
	return title || NODE_KIND_LABEL[node.kind] || node.id;
}

/** The value a "goes to" picker uses for "nowhere yet". SelectMenu has no null, and an empty
 *  string reads as "unset" to bits-ui, so the sentinel is explicit. */
export const NO_TARGET = '__none__';

export interface TargetOption {
	value: string;
	label: string;
}

/**
 * The nodes an edge may point at: everything except the node itself.
 *
 * Self-targeting is excluded rather than merely discouraged — a one-node loop is the smallest way
 * to make a case that can never finish, and publish refuses it, so the picker should not offer it.
 * Pointing at nothing stays offered: an unfinished edge is a legitimate saved draft (the contract's
 * `$comment_terminalEdges`), it is publish that refuses it.
 */
export function targetOptions(
	nodes: FlowNode[],
	excludeId: string,
	emptyLabel = 'Nothing yet'
): TargetOption[] {
	return [
		{ value: NO_TARGET, label: emptyLabel },
		...nodes.filter((n) => n.id !== excludeId).map((n) => ({ value: n.id, label: nodeLabel(n) }))
	];
}

/** Read an edge for a picker: a stored id, or the "nowhere" sentinel. */
export function targetValue(id: string | null | undefined): string {
	return id ?? NO_TARGET;
}

/** …and back. The sentinel becomes null, which is what the wire carries. */
export function targetId(value: string): string | null {
	return value === NO_TARGET ? null : value;
}

/** A config value as a string for an input (missing ⇒ ''). */
export function cfgString(node: FlowNode, key: string): string {
	const value = node.config?.[key];
	return value === null || value === undefined ? '' : String(value);
}

/**
 * Write a config key, deleting it when the value is blank.
 *
 * Deleting rather than storing `''`/`null` matters: `graph.parse` allowlists config keys per kind
 * and the publish validators branch on `is not None`, so a key left behind as an empty string is
 * the difference between "no deadline" and "a deadline of nothing", which reads as an error the
 * author cannot see the cause of.
 */
export function setCfgString(node: FlowNode, key: string, value: string): void {
	const trimmed = value.trim();
	if (trimmed === '') delete node.config[key];
	else node.config[key] = trimmed;
}

/** The same, for a number field. A non-numeric entry is kept as text so the engine's own message
 *  ("needs a number of days") is what the author reads, rather than a silent NaN. */
export function setCfgNumber(node: FlowNode, key: string, value: string): void {
	const trimmed = value.trim();
	if (trimmed === '') {
		delete node.config[key];
		return;
	}
	const n = Number(trimmed);
	node.config[key] = Number.isFinite(n) ? n : trimmed;
}

// ── The sentences ───────────────────────────────────────────────────────────────────────────

function plural(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

/**
 * What a wait node actually does, in a sentence. Rendered under the controls, because
 * "3 / 12 / (blank)" is three numbers and "Pauses for 3 days and 12 hours, then continues" is a
 * decision an author can check.
 */
export function describeWait(config: Record<string, unknown>): string {
	const until = typeof config.until_date_field === 'string' ? config.until_date_field.trim() : '';
	const days = Number(config.duration_days ?? 0);
	const hours = Number(config.duration_hours ?? 0);
	const parts: string[] = [];
	if (Number.isFinite(days) && days > 0) parts.push(plural(days, 'day'));
	if (Number.isFinite(hours) && hours > 0) parts.push(plural(hours, 'hour'));
	if (parts.length) {
		// A DURATION WINS over a date field when a document carries both — `timers.wait_until`'s
		// rule, and the inspector must read the way the engine runs rather than the way the fields
		// happen to be laid out. (The mode toggle clears the other key, so this only shows for a
		// hand-written definition — which is exactly when an author needs telling.)
		const sentence = `Pauses for ${parts.join(' and ')}, then continues.`;
		return until ? `${sentence} The date in ${until} is ignored while a length is set.` : sentence;
	}
	if (until) return `Pauses until the date in ${until}, then continues.`;
	return 'Says nothing about how long to wait — this will not publish.';
}

/**
 * The ordinal prefix on a branch arm. Order is the whole semantics of a branch — first satisfiable
 * wins — so the list is numbered and reads as prose rather than as an unordered set of rules.
 */
export function branchOrdinal(index: number, isElse: boolean): string {
	if (isElse) return 'Otherwise';
	const ordinals = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th'];
	const nth = ordinals[index] ?? `${index + 1}th`;
	return index === 0 ? `${nth} · if` : `${nth} · otherwise if`;
}

/** `verify_identity` providers: the API refuses to publish a retired name against a new flow, but a
 *  flow saved under one must still show what it actually runs rather than being silently rewritten
 *  onto a vendor it doesn't use. `aml_check` has no provider: it is always screened by itself. */
export const PROVIDER_OPTIONS: TargetOption[] = [
	{ value: 'manual', label: 'Manual — a person checks' },
	{ value: 'complycube', label: 'ComplyCube' }
];

/** The provider picker's items: the closed list, plus the stored value itself — marked retired —
 *  when it is neither (an older `idenfy` / `creditsafe` config). Absent/blank is not retired: it
 *  is the unset default, which the picker shows as "Manual". */
export function providerItems(current: string): TargetOption[] {
	if (!current || PROVIDER_OPTIONS.some((option) => option.value === current)) {
		return PROVIDER_OPTIONS;
	}
	return [...PROVIDER_OPTIONS, { value: current, label: `${current} (retired)` }];
}

/** The exit outcomes, and what each one means for the case. */
export const EXIT_OUTCOMES: { value: string; label: string; hint: string }[] = [
	{
		value: 'completed',
		label: 'Completed',
		hint: 'The journey did what it set out to do. Anything listening for “a journey completes” fires.'
	},
	{
		value: 'cancelled',
		label: 'Cancelled',
		hint: 'Stopped on purpose — the client withdrew, or the case was superseded. Not a failure.'
	},
	{
		value: 'failed',
		label: 'Failed',
		hint: 'Stopped because something went wrong. Shows as a failure in the journey list.'
	}
];
