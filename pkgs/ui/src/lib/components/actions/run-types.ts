/**
 * The shapes `GET /actions/runs` and `GET /actions/runs/{uid}` return — a run as its TREE.
 *
 * A baton run is a tree: composites call composites call leaves. The detail is the root node with
 * its children, each node carrying baton's own state (type + name + message), the one status word
 * the listing filters on, its calls (attempts, each with the engine's outcome word), its result and
 * its children. Nothing is flattened: a book read is a node with a result, a gate is a node a
 * person can decide. See apps/api/app/actions/queries.py, which these mirror.
 */

/** Baton's own state. The UI colours by `type` and labels by `name` (docs/baton.md). */
export interface RunState {
	type:
		| 'scheduled'
		| 'pending'
		| 'running'
		| 'paused'
		| 'completed'
		| 'failed'
		| 'cancelled'
		| 'crashed';
	name: string;
	message: string | null;
}

/** One try of a leaf. `outcome` is the engine's word — a wait is not a failure. */
export interface RunAttempt {
	attempt_no: number;
	outcome: 'ok' | 'skipped' | 'wait' | 'retry' | 'failed' | 'timed_out' | 'crashed';
	/** A `call` made a request to a provider; a `function` leaf (a book read) has an input and an
	 *  output in the same two slots. Absent on an older row ⇒ read off `method`/`url`. */
	kind?: 'call' | 'function';
	/** The call was read back out of the failure message because the leaf recorded none. */
	recovered?: boolean;
	method: string | null;
	url: string | null;
	status_code: number | null;
	error: string | null;
	error_type: string | null;
	duration_ms: number | null;
	started_at: string | null;
	ended_at: string | null;
	request: unknown;
	response: unknown;
}

/** The provider's refusal, read for a person: the sentence, the code, whether it is transient. */
export interface RunFailure {
	headline: string | null;
	detail: string | null;
	code: string | null;
	tracking_id: string | null;
	transient: boolean | null;
	status_code: number | null;
}

/** One node of the tree. The root is a `RunNodeData` too, extended as `RunDetailData`. */
export interface RunNodeData {
	uid: string;
	action_key: string;
	/** The action's human name ("Enrol client and account at SEI"); "Approval" for a gate. */
	name: string;
	kind: 'action' | 'gate';
	/** The provider the leaf called (`sei`, `seccl`, `morgan-lloyd`), or null for a book read. */
	provider: string | null;
	key: string | null;
	position: number | null;
	depth: number;
	after_keys: string[];
	leaf: boolean | null;
	state: RunState;
	status: string;
	/** What the parent does with this node's failure: fail · needs_attention · continue. */
	on_failure: string | null;
	retries: number | null;
	retries_used: number;
	wait_budget_seconds: number | null;
	idempotent: boolean | null;
	params: Record<string, unknown>;
	result: unknown;
	requested_at: string | null;
	started_at: string | null;
	ended_at: string | null;
	/** When a waiting or retrying node polls again. */
	scheduled_at: string | null;
	wait_started_at: string | null;
	passes: number;
	attempt_count: number;
	case_no: number | null;
	justification: string | null;
	approver_role_ids: number[];
	approver_roles: string[];
	gate_message: string | null;
	decision: 'approved' | 'rejected' | null;
	decided_by: string | null;
	decided_by_name: string | null;
	decided_at: string | null;
	decision_note: string | null;
	failure: RunFailure | null;
	attempts: RunAttempt[];
	children: RunNodeData[];
}

/** A lifecycle event from baton.events, with the actor named. */
export interface RunEvent {
	id: number;
	type: string;
	at: string | null;
	actor_id: string | null;
	actor_name: string | null;
	run_uid: string | null;
	run_name: string | null;
	payload: Record<string, unknown>;
}

export interface RunPerson {
	uid: string;
	name: string | null;
}

export interface RunAccount {
	uid: string;
	name: string | null;
	product_type: string | null;
	custodian: string | null;
}

/** The root, as `GET /actions/runs/{uid}` returns it. */
export interface RunDetailData extends RunNodeData {
	description: string;
	category: string | null;
	event_type: string | null;
	subject_kind: string | null;
	subject_uid: string | null;
	requested_by: string | null;
	requested_by_name: string | null;
	contact: RunPerson | null;
	account: RunAccount | null;
	/** The onboarding step that raised the run, when one did. */
	journey: { application_uid: string; step_title: string | null } | null;
	/** A readable snapshot of what was booked (amount, fee, …), when the door recorded one. */
	input: Record<string, unknown> | null;
	/** A held book review's command — the proposal and the snapshot it was taken against — so the
	 *  panel can draw the same now-vs-proposed body the Reviews queue does. Null otherwise. */
	change?: {
		payload: Record<string, unknown> | null;
		before: Record<string, unknown> | null;
	} | null;
	manual: boolean;
	is_review: boolean;
	approval: {
		required: boolean;
		roles: string[];
		gate_uid: string | null;
		/** The node a decision is posted with: the root at its own gate, or the gated child of a
		 *  composite. Null when nothing is held. */
		held_uid?: string | null;
		held_name?: string | null;
		held_since?: string | null;
		/** Advisory — the engine decides again on the POST. */
		can_decide: boolean;
		/** How many people other than the requester could decide; null when no role is named. */
		eligible_approvers?: number | null;
		reminded_at?: string | null;
		reminders?: number;
	};
	/** The node to point a person at: the leaf whose call was refused. */
	attention: (RunFailure & { node_uid: string; node_name: string; provider: string | null }) | null;
	timing: {
		requested_at: string | null;
		started_at: string | null;
		ended_at: string | null;
		next_poll_at: string | null;
		passes: number;
		calls: number;
		leaves: number;
	};
	can_retry: boolean;
	can_cancel: boolean;
	events: RunEvent[];
}

/** One row of the runs listing — a root run with its immediate children for the step bar. */
export interface RunListRow {
	uid: string;
	action_key: string;
	name: string;
	status: string;
	state: RunState;
	case_no: number | null;
	subject_kind: string | null;
	subject_uid: string | null;
	contact_uid: string | null;
	contact_name: string | null;
	requested_by: string | null;
	requested_by_name: string | null;
	requested_at: string | null;
	started_at: string | null;
	ended_at: string | null;
	/** The providers the run reached, by code. */
	providers: string[];
	children: { uid: string; key: string; name: string; kind: 'action' | 'gate'; status: string }[];
	can_decide: boolean;
	is_review: boolean;
	/** The gate's facts, on a root held at its own gate or a gated child's: the node to decide,
	 *  since when, who may, how many could, and the figure at stake. */
	held_uid?: string | null;
	held_since?: string | null;
	approver_role_ids?: number[];
	approver_roles?: string[];
	eligible_approvers?: number | null;
	held_amount?: number | null;
}

/** The stat-tile counts the listing returns alongside the rows. The first six are the runs
 *  seat's; the rest the approvals seat's own. */
export interface RunStats {
	running: number;
	waiting: number;
	awaiting_approval: number;
	needs_attention: number;
	failed_7d: number;
	completed_24h: number;
	held_over_day?: number;
	held_undecidable?: number;
	money_held?: number;
	decided_7d?: number;
	approved_7d?: number;
	rejected_7d?: number;
}

/** The listing envelope: a page of rows, the whole match set's size, and the facets. */
export interface RunsPage {
	items: RunListRow[];
	total: number;
	limit: number;
	offset: number;
	facets: Record<string, string[]>;
	facet_counts?: Record<string, Record<string, number>>;
	facet_labels?: Record<string, Record<string, string>>;
	stats: RunStats;
}

/** The routes the actions screens link to. Injected by the mounting app (the portal waterfall):
 *  the admin portal mounts the same components over a tenant and has different routes. */
export interface RunLinks {
	run: (uid: string) => string;
	client?: (uid: string) => string;
	account?: (uid: string) => string;
	journey?: (applicationUid: string) => string;
	/** The policy editor, deep-linked to an action. */
	policy?: (actionKey: string) => string;
}
