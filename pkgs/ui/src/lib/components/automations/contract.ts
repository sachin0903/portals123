/**
 * The rule tier's view of the shared contract.
 *
 * `book/journeys/flow-contract.json` is the cross-language referee for the whole Flows v3
 * programme (the Python engine mirrors it as literals and pins the mirror by test). The
 * TypeScript side has the luxury of reading it DIRECTLY, so this module does exactly that and
 * nothing else: no second copy of the trigger list, no hand-maintained action menu, no separate
 * operator table. Everything the builder renders is derived here, which is what makes
 * "add a trigger to the contract and it appears in the picker" true rather than aspirational.
 *
 * `contract.test.ts` pins the derivations (grouping, labels, ordering) so a contract edit that
 * would blank a picker fails the suite instead of shipping an empty dropdown.
 */
import contract from '../book/journeys/flow-contract.json';
import { CLIENT_TEAM_ROLE_ITEMS, DEFAULT_CLIENT_TEAM_ROLE } from '../book/clients/team.js';

// ── Triggers ────────────────────────────────────────────────────────────────────────────────

export interface TriggerSpec {
	key: string;
	label: string;
	source: string;
	event: string | null;
	subject: string;
}

const rawTriggers = contract.triggers as Record<
	string,
	{ label: string; source: string; event?: string | null; subject: string }
>;

/** Every trigger, in contract order (which is authored order: the common ones first). */
export const TRIGGERS: TriggerSpec[] = Object.entries(rawTriggers).map(([key, spec]) => ({
	key,
	label: spec.label,
	source: spec.source,
	event: spec.event ?? null,
	subject: spec.subject
}));

export const TRIGGER_BY_KEY: Record<string, TriggerSpec> = Object.fromEntries(
	TRIGGERS.map((t) => [t.key, t])
);

/**
 * How the picker groups the menu. Keyed off the trigger key's own namespace rather than a second
 * hand-written map: the keys are already `noun.verb`, so the noun IS the group, and a new trigger
 * lands in the right section with no edit here. Unknown namespaces fall through to "Other" rather
 * than vanishing.
 */
const GROUP_LABELS: Record<string, string> = {
	client: 'Clients',
	contact: 'Clients',
	document: 'Documents',
	account: 'Accounts',
	instruction: 'Instructions',
	task: 'Tasks',
	application: 'Applications',
	case: 'Journeys',
	screening: 'Compliance',
	tag: 'Tags',
	schedule: 'Schedules'
};

export interface TriggerGroup {
	label: string;
	triggers: TriggerSpec[];
}

/** The trigger menu, grouped and in a stable order (first appearance in the contract wins). */
export function triggerGroups(): TriggerGroup[] {
	const groups = new Map<string, TriggerSpec[]>();
	for (const trigger of TRIGGERS) {
		const namespace = trigger.key.split('.')[0] ?? '';
		const label = GROUP_LABELS[namespace] ?? 'Other';
		const bucket = groups.get(label);
		if (bucket) bucket.push(trigger);
		else groups.set(label, [trigger]);
	}
	return [...groups].map(([label, triggers]) => ({ label, triggers }));
}

/**
 * Sources the engine can actually fire today. Mirrors `app/flows/triggers.py::SUPPORTED_SOURCES`
 * — the contract lists what is AUTHORABLE, this says what is LIVE, and the difference is a badge
 * on the row rather than a missing menu entry. Pinned by test against the API's own answer via the
 * `supported` flag it returns per row.
 */
export const SUPPORTED_SOURCES = new Set(['action', 'schedule']);

export function isSupported(key: string): boolean {
	const spec = TRIGGER_BY_KEY[key];
	return spec !== undefined && SUPPORTED_SOURCES.has(spec.source);
}

// ── Listener deliverability ─────────────────────────────────────────────────────────────────

/**
 * `isSupported` is the RULE tier's question: can this trigger arm an automation. A Flows v3
 * `wait_for_event` node asks a narrower one — can the engine ever DELIVER this trigger to a case
 * that is already parked, waiting for it — and the two are not the same set. Using the rule-tier
 * predicate on a listener picker is what let `schedule.recurring` be offered as something to wait
 * for: a schedule has no event, so nothing releases the case and it waits for ever.
 *
 * Both sets come straight from the contract's `listeners` block, mirrored by
 * `app/flows/triggers.py::LISTENER_SOURCES`/`LISTENER_SUBJECTS` — see its `$comment_listeners` for
 * why each entry is in or out (in particular why `task` is out). The API is the boundary and
 * refuses these at publish; this is the courtesy that stops an author authoring one at all.
 */
const rawListeners = contract.listeners as {
	deliverableSources: string[];
	scopableSubjects: string[];
};

export const LISTENER_SOURCES: ReadonlySet<string> = new Set(rawListeners.deliverableSources);
export const LISTENER_SUBJECTS: ReadonlySet<string> = new Set(rawListeners.scopableSubjects);

/**
 * Why a journey cannot wait for this trigger, or `null` if it can. A sentence fragment completing
 * "Nothing can …" — the picker's own copy, deliberately not the API's refusal wording: a tooltip
 * and a publish refusal are read in different places by people asking different questions.
 */
export function listenerRefusal(key: string): string | null {
	const spec = TRIGGER_BY_KEY[key];
	if (spec === undefined) return 'this isn’t an event this engine knows';
	if (!LISTENER_SOURCES.has(spec.source)) {
		return spec.source === 'schedule'
			? 'a schedule isn’t an event, so nothing would arrive to move the journey on'
			: 'nothing delivers this event yet, so the journey would wait for ever';
	}
	if (!LISTENER_SUBJECTS.has(spec.subject)) {
		return `a journey is never about a ${spec.subject}, so there’s no ${spec.subject} of its own to watch`;
	}
	return null;
}

/** Can a `wait_for_event` node wait for this trigger at all? */
export function isListenable(key: string): boolean {
	return listenerRefusal(key) === null;
}

// ── Actions ─────────────────────────────────────────────────────────────────────────────────

export interface ActionTypeSpec {
	type: string;
	label: string;
	config: string[];
	permission: string;
}

const rawActions = contract.actionTypes as Record<string, { config: string[]; permission: string }>;

/**
 * A human label per action type. The contract carries the machine vocabulary (config keys, the
 * permission the human equivalent needs) and deliberately not display copy — that is a product
 * decision per surface, and the Python side would have no use for it. Anything missing here falls
 * back to a de-slugged key, so a new contract action is usable the day it lands.
 */
const ACTION_LABELS: Record<string, string> = {
	apply_tag: 'Apply a tag',
	remove_tag: 'Remove a tag',
	raise_task: 'Raise a task',
	send_email: 'Send an email',
	assign_adviser: 'Add to the client’s team',
	set_case_status: 'Set the journey status',
	request_document: 'Request a document',
	generate_document: 'Generate a document',
	send_document: 'Send a document',
	webhook_out: 'Call a webhook',
	lookup: 'Look up external data',
	classify: 'Read and label text (AI)',
	draft_message: 'Draft a message for review (AI)'
};

/**
 * Action types that reach a language model.
 *
 * Surfaced as a set rather than a naming convention because the builder has to say so on the
 * inspector — an author choosing one is choosing to send a slice of a client's text to a third
 * party, and that belongs on the same screen as the choice, not in the docs. Neither of them can
 * act: `classify` writes a label a firm-authored branch decides on, `draft_message` raises a task a
 * person reviews and sends. See `app/flows/ai.py` for the whole of the reasoning.
 */
export const AI_ACTIONS: ReadonlySet<string> = new Set(['classify', 'draft_message']);

export function isAIAction(type: string): boolean {
	return AI_ACTIONS.has(type);
}

/**
 * What an author is told when they pick an AI action. Two switches and one rule, in one sentence
 * each — the API's preflight is the boundary and refuses an unconfigured deployment or an
 * un-enabled firm; this is the courtesy that stops them authoring one blind.
 */
export function aiActionNote(type: string): string | null {
	if (type === 'classify')
		return 'Sends only the fields you name to a language model and writes back one of your labels. It decides nothing — a branch after it does, and publishing needs an arm for every label including “unknown”.';
	if (type === 'draft_message')
		return 'Sends only the fields you name to a language model and raises the draft as a task for a person to review. Nothing is sent to a client.';
	return null;
}

export function humaniseKey(key: string): string {
	const words = key.replace(/[_.]/g, ' ').trim();
	return words.charAt(0).toUpperCase() + words.slice(1);
}

export const ACTION_TYPES: ActionTypeSpec[] = Object.entries(rawActions).map(([type, spec]) => ({
	type,
	label: ACTION_LABELS[type] ?? humaniseKey(type),
	config: [...spec.config],
	permission: spec.permission
}));

export const ACTION_BY_TYPE: Record<string, ActionTypeSpec> = Object.fromEntries(
	ACTION_TYPES.map((a) => [a.type, a])
);

/**
 * Action types the contract lists but the executor cannot run yet.
 *
 * The gap between `flow-contract.json`'s `actionTypes` and `app/flows/effects.py::HANDLERS`.
 * The contract states what is AUTHORABLE, the executor what is LIVE, and — exactly as
 * `SUPPORTED_SOURCES` does for triggers — the difference is a badge on the menu entry rather than
 * a missing one: hiding half the vocabulary makes the picker look broken next to the docs, and an
 * author who arms a parked action should be told, not left to find out at 3am in a client's
 * journey. The API's own preflight is the backstop; this is the courtesy.
 *
 * Empty today: `lookup` was the last parked one and Phase D implemented it. It stays as the seam,
 * because the next contract action will land here before its handler does.
 */
export const PARKED_ACTIONS: ReadonlySet<string> = new Set<string>();

export function isActionAvailable(type: string): boolean {
	return type in ACTION_BY_TYPE && !PARKED_ACTIONS.has(type);
}

/** Withdrawn from authoring — offered nowhere, for any tenant. NOT `PARKED_ACTIONS`, which means
 *  "declared, no handler" and is shown WITH a badge; these work and are simply not offered yet. The
 *  AI pair is refused server-side anyway (blank key, no `flows.ai_tenants` row). Delete to re-offer. */
export const HIDDEN_ACTIONS: ReadonlySet<string> = new Set<string>(['classify', 'draft_message']);

/** The actions an author may choose. Every menu reads this, so none can survive one picker alone. */
export const OFFERED_ACTION_TYPES = ACTION_TYPES.filter((a) => !HIDDEN_ACTIONS.has(a.type));

/**
 * How the permission an action asserts reads to an author.
 *
 * Every action's `permission` names the slug a PERSON would need to do the same thing by hand —
 * the executor calls that same permissioned service layer, and publish refuses an action whose
 * firm has granted the slug to nobody. Saying so out loud is the point: an automation that sends a
 * client an email is exercising the firm's release authority, and the person authoring it should
 * see that on the same screen, not discover it in a refusal.
 */
export function actionPermissionNote(type: string): string | null {
	const spec = ACTION_BY_TYPE[type];
	if (!spec) return null;
	return `Runs with the same authority a person needs to do this by hand — “${spec.permission}”.`;
}

/**
 * How a config key is edited. The block inspector's descriptor idea, applied to action config: the
 * sheet renders fields from a DESCRIPTOR rather than a hand-built form per action type, so adding
 * `raise_task.due_in_days` to the contract adds a number field with no component change. Keys not
 * named here render as plain text, which is always a safe default (the executor parses).
 */
export type ConfigFieldKind = 'text' | 'textarea' | 'number' | 'choice';

export interface ConfigFieldSpec {
	key: string;
	label: string;
	kind: ConfigFieldKind;
	choices?: { value: string; label: string }[];
	placeholder?: string;
	/** What a blank field means. Shown as the selection, and what the executor applies when the key
	 *  is left out, so the form never shows "Choose…" over a value that is already decided. */
	defaultValue?: string;
}

const CONFIG_FIELDS: Record<string, Omit<ConfigFieldSpec, 'key'>> = {
	tag: { label: 'Tag', kind: 'text', placeholder: 'e.g. chase-sent' },
	target: {
		label: 'Applies to',
		kind: 'choice',
		choices: [
			{ value: 'contact', label: 'The client' },
			{ value: 'case', label: 'The journey' }
		]
	},
	title: { label: 'Title', kind: 'text' },
	description: { label: 'Description', kind: 'textarea' },
	assign_to: { label: 'Assign to', kind: 'text', placeholder: 'A user id, or leave blank' },
	assignee_role_slug: { label: 'Or assign to role', kind: 'text', placeholder: 'e.g. compliance' },
	category: { label: 'Category', kind: 'text' },
	priority: {
		label: 'Priority',
		kind: 'choice',
		choices: [
			{ value: 'low', label: 'Low' },
			{ value: 'normal', label: 'Normal' },
			{ value: 'high', label: 'High' }
		]
	},
	due_in_days: { label: 'Due in (days)', kind: 'number' },
	template_code: { label: 'Approved template', kind: 'text' },
	template_uid: { label: 'Template', kind: 'text' },
	dataset: { label: 'Dataset', kind: 'text' },
	document_uid: { label: 'Document', kind: 'text' },
	to: { label: 'Send to', kind: 'text' },
	// A textarea holding a mapping, as `request_mapping` is: a bespoke editor for the third map-shaped key would give an author two ways to write one shape.
	variables: {
		label: 'Fill in the template',
		kind: 'textarea',
		placeholder: '{"document_name": "{{ document.name || your statement }}"}'
	},
	// Anyone on the firm's staff can be put on a client's team; the ROLE says what they are there.
	// The same closed list as the client record's Team dialog, Adviser first and the default.
	adviser_id: { label: 'Person', kind: 'text' },
	role: {
		label: 'Role',
		kind: 'choice',
		choices: CLIENT_TEAM_ROLE_ITEMS,
		defaultValue: DEFAULT_CLIENT_TEAM_ROLE
	},
	status: { label: 'Status', kind: 'text' },
	target_contact: { label: 'Request from', kind: 'text' },
	endpoint_key: { label: 'Endpoint', kind: 'text' },
	payload_keys: { label: 'Payload keys', kind: 'text' },
	request_mapping: { label: 'Request mapping', kind: 'textarea' },
	response_mapping: { label: 'Response mapping', kind: 'textarea' },
	input_fields: {
		label: 'What the model reads',
		kind: 'textarea',
		placeholder: '{"answer": "memory.reason_for_transfer"}'
	},
	question: {
		label: 'Question',
		kind: 'textarea',
		placeholder: 'What kind of enquiry is this?'
	},
	labels: { label: 'Possible answers', kind: 'text', placeholder: 'simple, complex, complaint' },
	remember_as: { label: 'Remember the answer as', kind: 'text', placeholder: 'enquiry_kind' },
	brief: { label: 'What the draft should say', kind: 'textarea' }
};

/** The editable fields for one action type, in contract order. */
export function configFields(type: string): ConfigFieldSpec[] {
	const spec = ACTION_BY_TYPE[type];
	if (!spec) return [];
	return spec.config.map((key) => ({
		key,
		label: CONFIG_FIELDS[key]?.label ?? humaniseKey(key),
		kind: CONFIG_FIELDS[key]?.kind ?? 'text',
		...(CONFIG_FIELDS[key]?.choices ? { choices: CONFIG_FIELDS[key].choices } : {}),
		...(CONFIG_FIELDS[key]?.placeholder ? { placeholder: CONFIG_FIELDS[key].placeholder } : {}),
		...(CONFIG_FIELDS[key]?.defaultValue ? { defaultValue: CONFIG_FIELDS[key].defaultValue } : {})
	}));
}

// ── Conditions ──────────────────────────────────────────────────────────────────────────────

export const OPERATORS: string[] = [...(contract.conditions.operators as string[])];

export const FIELD_SCOPES = contract.conditions.fieldScopes as Record<string, string[]>;

/** Every closed-registry field, flattened — the picker's option list. */
export const FIELDS: string[] = Object.values(FIELD_SCOPES)
	.flat()
	.filter((f) => !f.endsWith('*'));

/** Scopes whose keys are AUTHORED rather than registered (`memory.*`, `event.subject.*`). */
export const OPEN_PREFIXES: string[] = Object.values(FIELD_SCOPES)
	.flat()
	.filter((f) => f.endsWith('*'))
	.map((f) => f.slice(0, -1));

export function isOpenField(field: string): boolean {
	return OPEN_PREFIXES.some((prefix) => field.startsWith(prefix));
}

/**
 * Operator display copy, and — the important half — its POLARITY.
 *
 * Every v3 operator ships with a negated twin (`equals`/`notEquals`, `matches`/`notMatches`…), so
 * the builder does NOT render 16 dropdown entries: it renders an is/is-not toggle beside eight
 * tests. `negate: true` marks the "is not" side and `twin` names the other, which is all a UI
 * needs to flip a row's polarity without knowing anything else about the grammar.
 */
export interface OperatorSpec {
	op: string;
	/** Lowercase on purpose: summaries put it mid-sentence; the dropdown sentence-cases it. */
	label: string;
	negate: boolean;
	twin?: string;
	/** How the value is edited — `none` renders no value field at all (isEmpty/isNotEmpty). */
	value: 'text' | 'list' | 'number' | 'days' | 'date' | 'none';
}

export const OPERATOR_SPECS: Record<string, OperatorSpec> = {
	equals: { op: 'equals', label: 'is', negate: false, twin: 'notEquals', value: 'text' },
	notEquals: { op: 'notEquals', label: 'is', negate: true, twin: 'equals', value: 'text' },
	in: { op: 'in', label: 'is one of', negate: false, twin: 'notIn', value: 'list' },
	notIn: { op: 'notIn', label: 'is one of', negate: true, twin: 'in', value: 'list' },
	contains: {
		op: 'contains',
		label: 'contains',
		negate: false,
		twin: 'notContains',
		value: 'text'
	},
	notContains: {
		op: 'notContains',
		label: 'contains',
		negate: true,
		twin: 'contains',
		value: 'text'
	},
	isEmpty: { op: 'isEmpty', label: 'is empty', negate: false, twin: 'isNotEmpty', value: 'none' },
	isNotEmpty: {
		op: 'isNotEmpty',
		label: 'is empty',
		negate: true,
		twin: 'isEmpty',
		value: 'none'
	},
	matches: { op: 'matches', label: 'matches', negate: false, twin: 'notMatches', value: 'text' },
	notMatches: {
		op: 'notMatches',
		label: 'matches',
		negate: true,
		twin: 'matches',
		value: 'text'
	},
	greaterThan: { op: 'greaterThan', label: 'is more than', negate: false, value: 'number' },
	lessThan: { op: 'lessThan', label: 'is less than', negate: false, value: 'number' },
	before: { op: 'before', label: 'is before', negate: false, value: 'date' },
	after: { op: 'after', label: 'is after', negate: false, value: 'date' },
	withinDays: {
		op: 'withinDays',
		label: 'is within the last',
		negate: false,
		twin: 'olderThanDays',
		value: 'days'
	},
	olderThanDays: {
		op: 'olderThanDays',
		label: 'is within the last',
		negate: true,
		twin: 'withinDays',
		value: 'days'
	}
};

/** The positive side of each operator pair, in contract order — the test dropdown's options. */
export const BASE_OPERATORS: OperatorSpec[] = OPERATORS.map((op) => OPERATOR_SPECS[op]).filter(
	(spec): spec is OperatorSpec => spec !== undefined && !spec.negate
);

// ── Caps + safety ───────────────────────────────────────────────────────────────────────────

export const CAPS = contract.caps as Record<string, number>;
export const SAFETY = contract.safety as Record<string, unknown>;
