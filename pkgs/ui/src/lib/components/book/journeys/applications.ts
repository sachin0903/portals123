/**
 * Types + helpers for the v2 orchestration UI (the journeys surfaces over the FastAPI
 * `/applications` endpoints). Mirror the API response models one-to-one so the surfaces work
 * directly in the API shapes. Shared here (not `$lib`) because the extracted journey components
 * render from these — the platform loaders type their fetches against them too.
 */

import { formatGBP } from '../../../money.js';
import { sentenceCase } from '../../../sentence-case.js';

/** The generic engine state (app/flows/states.py) — NOT onboarding vocabulary. */
export type EngineState =
	'draft' | 'active' | 'blocked' | 'waiting' | 'completed' | 'cancelled' | 'failed';

/** One step instance of a case (a fan-out instance for participant-scoped steps). */
export interface AppStep {
	step_key: string;
	step_type: string;
	title: string | null;
	assignee: string;
	status: string;
	participant_subject_id: number | null;
	request_item_id: number | null;
}

/** Where a profile case's client stands on portal access — `null` for a non-contact case
 * (account/application). 'none' is the ordinary state for a client an application just created
 * inline: a login is deliberately NOT minted at that point (one login can serve several
 * clients, decided at invite) — see `apps/api/app/routers/applications.py::_resolve_owner`. */
export type PortalStatus = 'none' | 'invited' | 'active' | 'revoked' | 'disabled' | null;

/** One case in the execution — a person's profile/KYC case or an account case. */
export interface AppCase {
	uid: string;
	kind: string; // 'profile' | 'account' | 'application'
	subject_type: string | null;
	subject_id: number | null;
	/** The subject contact's own uid (profile cases). `subject_id` is the database key the roles
	 *  and portal-status maps are built on; this is what a path takes — `POST
	 *  /firm/contacts/{contact_uid}/portal-invite`. */
	subject_uid: string | null;
	subject_name: string | null;
	contact_type: string | null; // 'individual' | 'organisation' | 'child' (profile cases)
	contact_email: string | null; // the CONTACT's own email on file (profile cases)
	/** The number staff ring, and the date of birth (which is what marks a child). Profile cases. */
	contact_phone?: string | null;
	dob?: string | null;
	roles: string[]; // account roles across the application (profile cases)
	portal_status: PortalStatus;
	portal_login_email: string | null; // the login's email, once one exists (may differ)
	account_ref: string | null; // account uid (account cases)
	platform_name: string | null; // platform name (account cases)
	/** Kickoff catalogue — present on account cases when the adviser set them at create. */
	product_name?: string | null;
	product_type?: string | null;
	model_name?: string | null;
	fee_package_names?: string[] | null;
	currency?: string | null;
	has_transfer?: boolean | null;
	/** Intent captured at kickoff (`intent`, `transfer_type`, `amount`, `provider_*`). */
	transfer?: Record<string, unknown> | null;
	/** JISA beneficiary on `accounts.data.jisa_child` — `{ name, dob }` when set. */
	jisa_child?: { name?: string | null; dob?: string | null } | null;
	/** Funding intent on `accounts.data.regular_contribution` — amount in major units. */
	regular_contribution?: {
		intent?: boolean;
		amount?: number | null;
		frequency?: string | null;
		expected_by?: string | null;
	} | null;
	/** Kickoff investment capacity — `advisory` | `discretionary` | `execution_only`. */
	investment_capacity?: string | null;
	/** Kickoff dividend treatment — `reinvest` | `cash` | `pay_away` when set (non-pension). */
	dividend_treatment?: string | null;
	/** People attached to an account and the role that puts them there, for contextual step labels. */
	account_parties?: { name: string | null; role: string }[];
	status: string;
	steps: AppStep[];
}

/** A semantic timeline event (flows.domain_events). */
export interface TimelineEvent {
	id: number;
	event_type: string;
	payload: Record<string, unknown>;
	created_at: string;
}

/** `GET /applications/{uid}` — the execution + its tiered cases + timeline. */
export interface AppExecution {
	uid: string;
	status: string;
	application_status: string | null;
	flow_name: string | null;
	/** The flow's code (`onboarding`, …). Absent on an older payload: the name is the fallback. */
	flow_code?: string | null;
	/** A completed staff form on this journey can be reopened and amended. True only for the
	 *  onboarding flow, and only until the adviser declares and sends it to the client — the same
	 *  rule the submit endpoint enforces. Absent (an older payload) ⇒ not amendable. */
	forms_amendable?: boolean;
	custody: string | null;
	created_at: string;
	/** Adviser chosen at kickoff. Null on a journey raised before one was recorded. */
	adviser_id: string | null;
	adviser_name: string | null;
	/** Platform staff member who owns this journey's staff tasks. Not limited to advisers. */
	assignee_user_id: string | null;
	assignee_name: string | null;
	cases: AppCase[];
	timeline: TimelineEvent[];
}

/** One row of `GET /applications`. */
export interface AppExecutionRow {
	uid: string;
	status: string;
	created_at: string;
	application_status: string | null;
	flow_name: string | null;
	/** The flow's code. Absent on an older payload: `flow_name` is the fallback. */
	flow_code?: string | null;
	/** Adviser responsible for the application; falls back to the journey creator at the API. */
	adviser_name: string | null;
	/** Platform staff member the journey's staff tasks are assigned to. */
	assignee_user_id?: string | null;
	assignee_name?: string | null;
	clients: number;
	accounts: number;
	steps_total: number;
	steps_done: number;
	client_names: string | null;
	/** Open steps assigned to the client. Optional: an older API build omits it, and `owner()`
	 *  then falls back to inferring the split from the phase. */
	open_client_steps?: number;
	/** The staff member whose move this is — the assignee, or the adviser with nobody assigned —
	 *  is the caller. Decided at the API: the portal knows the caller only by their WorkOS id. */
	owner_is_me?: boolean;
}

/** The person or team whose action moves a listing row forward.
 *
 * The engine's failed/blocked state means the adviser must intervene regardless of the projected
 * stage. Identity/AML/offshore review belongs to Graphene; an actual open client step beats the
 * phase label because flow authors decide who fills a form in. */
/** The branded application PDF is the finished onboarding, not a journey still being filled in. */
export function isCompletedOnboarding(row: {
	status: string;
	flow_code?: string | null;
	flow_name?: string | null;
}): boolean {
	if (row.status !== 'completed') return false;
	if (row.flow_code) return row.flow_code === 'onboarding';
	return row.flow_name === 'Onboarding';
}

export type OwnerKind = 'none' | 'staff' | 'client' | 'graphene';

/** Which party the next move belongs to — the fact behind `actionOwner`'s words. */
export function ownerKind(row: AppExecutionRow): OwnerKind {
	if (row.status === 'completed' || row.status === 'cancelled') return 'none';
	if (row.status === 'failed' || row.status === 'blocked') return 'staff';
	if ((row.open_client_steps ?? 0) > 0) return 'client';
	if (
		['identity_under_review', 'aml_under_review', 'offshore_under_review'].includes(
			phase(row.application_status) ?? ''
		)
	) {
		return 'graphene';
	}
	return 'staff';
}

/** The word for the caller in the "Waiting on" column — and its filter value. */
export const ME = 'Me';

/** The "Waiting on" cell. When the move is the firm's it names the ASSIGNEE — the person whose
 *  staff tasks these are (0188) — and only falls back to the adviser on a journey raised before
 *  one was recorded. It used to print the adviser regardless, so a journey assigned to a
 *  paraplanner read as waiting on the adviser, one column away from "Assigned to" saying
 *  otherwise. When that person is the reader it says "Me": a list of names the reader has to
 *  scan for their own is slower than one word that is only ever theirs. */
export function actionOwner(row: AppExecutionRow): string {
	switch (ownerKind(row)) {
		case 'none':
			return '—';
		case 'client':
			return row.clients > 1 ? 'Clients' : 'Client';
		case 'graphene':
			return 'Graphene';
		default:
			if (row.owner_is_me) return ME;
			return row.assignee_name ?? row.adviser_name ?? 'Adviser';
	}
}

/** The engine states a journey still underway can be in — what the list shows by default. A
 *  stopped or completed journey is history, and history is a filter away rather than the first
 *  thing an adviser scrolls past. `blocked` is absent because it is presented as `active`. */
export const IN_FLIGHT_STATES: readonly string[] = ['draft', 'active', 'waiting', 'failed'];

/** The four stat tiles over the journeys list. Each is the list's own filter bar set to one
 *  selection: clicking a tile applies it, clicking the tile in force lifts it, and the chips the
 *  bar then shows are the honest account of what the table is cut to. Each tile owns the keys it
 *  names and leaves the others alone — a stage filter survives a tile click. */
export const JOURNEY_TILES = ['in-flight', 'need-you', 'with-client', 'completed'] as const;
export type JourneyTile = (typeof JOURNEY_TILES)[number];

export const TILE_FILTERS: Record<JourneyTile, Record<string, string[]>> = {
	'in-flight': { status: [...IN_FLIGHT_STATES] },
	'need-you': { owner: [ME] },
	'with-client': { owner: ['Client', 'Clients'] },
	completed: { status: ['completed'] }
};

/** The keys the tiles between them set — the ones a tile click replaces. */
const TILE_KEYS = ['status', 'owner'];

function sameValues(a: string[] | undefined, b: string[]): boolean {
	if (!a || a.length !== b.length) return false;
	const want = new Set(b);
	return a.every((v) => want.has(v));
}

/** Which tile the bar's current selection IS, if any: the tile's own keys match exactly and no
 *  other tile key is set. A hand-picked "Status: Draft" is nobody's tile. */
export function tileInForce(value: Record<string, string[]>): JourneyTile | null {
	for (const tile of JOURNEY_TILES) {
		const want = TILE_FILTERS[tile];
		const keysMatch = TILE_KEYS.every((key) => {
			const wanted = want[key];
			return wanted ? sameValues(value[key], wanted) : !value[key]?.length;
		});
		if (keysMatch) return tile;
	}
	return null;
}

/** The bar's selection after a tile click: the tile's filters, or none of the tile keys when
 *  that tile was already in force. Filters that are not a tile's (journey, stage…) are kept. */
export function toggleTile(
	value: Record<string, string[]>,
	tile: JourneyTile
): Record<string, string[]> {
	const next: Record<string, string[]> = {};
	for (const [key, picked] of Object.entries(value)) {
		if (!TILE_KEYS.includes(key) && picked.length) next[key] = picked;
	}
	if (tileInForce(value) === tile) return next;
	for (const [key, picked] of Object.entries(TILE_FILTERS[tile])) next[key] = [...picked];
	return next;
}

/** Does a row fall under a tile — the tile's own filters applied to it, nothing else. */
export function inTile(row: AppExecutionRow, tile: JourneyTile): boolean {
	const want = TILE_FILTERS[tile];
	if (want.status && !want.status.includes(presentedStatus(row.status))) return false;
	if (want.owner && !want.owner.includes(actionOwner(row))) return false;
	return true;
}

/** The words for a generic engine state, beside the tone for the same vocabulary so the two
 *  cannot drift.
 *
 *  IT EXISTS BECAUSE THE RAW VALUE WAS THE LABEL. `stateTone` shipped without a sibling, so every
 *  caller coloured the badge correctly and then printed `c.status` into it — a lowercase "draft"
 *  or "blocked" sitting beside a `roleLabel`'d "Owner" in the same row of badges. `cancelled` is
 *  "Stopped", matching `BOARD_COLUMNS` and `statusLabel`: one act, one word for it everywhere.
 *  `blocked` reads "In progress" too: the engine is waiting on a step, and to an adviser that is
 *  the journey still underway. The fallback humanises rather than passing through, because a
 *  state this map has not caught up with is still a word and not a key. */
const STATE_LABELS: Record<string, string> = {
	draft: 'Draft',
	active: 'In progress',
	waiting: 'Waiting',
	blocked: 'In progress',
	completed: 'Completed',
	cancelled: 'Stopped',
	failed: 'Failed'
};
export function stateLabel(status: string | null | undefined): string {
	if (!status) return '—';
	return STATE_LABELS[status] ?? status.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** The status the journeys list filters by. `blocked` wears the same badge as `active`, so it
 *  has to be the same option — two rows both reading "In progress" would filter as two states. */
export function presentedStatus(status: string): string {
	return status === 'blocked' ? 'active' : status;
}

/** Map a generic engine state onto a StatusBadge tone. */
export function stateTone(status: string): 'success' | 'warning' | 'info' | 'danger' | 'neutral' {
	switch (status) {
		case 'completed':
			return 'success';
		case 'active':
		case 'blocked':
			// A blocked engine is the journey still underway — the badge says "In progress", the
			// same as an active one, so the tone has to agree or two blues-and-ambers say the
			// same words.
			return 'info';
		case 'waiting':
			return 'warning';
		case 'failed':
		case 'cancelled':
			return 'danger';
		default:
			return 'neutral';
	}
}

/** Map a step status onto a StatusBadge tone. */
export function stepTone(status: string): 'success' | 'warning' | 'info' | 'danger' | 'neutral' {
	switch (status) {
		case 'fulfilled':
			return 'success';
		case 'waiting':
		case 'pending':
			return 'warning';
		case 'blocked':
			return 'info';
		case 'failed':
		case 'cancelled':
			return 'danger';
		default:
			return 'neutral';
	}
}

/** The badge + call-to-action for a profile case's `portal_status` — the adviser-facing answer
 * to "has this client actually been given a login, and did the invite go anywhere". `null`
 * (a non-contact case) and `'active'` (nothing to do) render no action; every other state offers
 * one — a menu item behind the pill itself, as on the client record — worded for what it will
 * actually do (a fresh invite reads differently from a resend or a reactivation, even though all
 * three call the same idempotent endpoint). */
export function portalStatusMeta(status: PortalStatus): {
	badge: string;
	tone: 'success' | 'warning' | 'info' | 'danger' | 'neutral';
	action: string | null;
} {
	switch (status) {
		case 'active':
			return { badge: 'Portal: active', tone: 'success', action: null };
		case 'invited':
			return { badge: 'Portal: invited', tone: 'warning', action: 'Resend invite' };
		case 'revoked':
			return { badge: 'Portal: revoked', tone: 'danger', action: 'Re-invite' };
		case 'disabled':
			return { badge: 'Portal: disabled', tone: 'danger', action: 'Re-invite' };
		case 'none':
			// "access", not "login": the same fact is badged "Portal access" on the client record and
			// carried as `portal_status` / `grant_portal_access` everywhere behind it, so a second
			// noun here made one client read as lacking two different things on two screens.
			return { badge: 'No portal access', tone: 'neutral', action: 'Invite to portal' };
		default:
			return { badge: '', tone: 'neutral', action: null };
	}
}

const STEP_LABELS: Record<string, string> = {
	fill_form: 'Fill in a form',
	accept_terms: 'Accept terms',
	upload_document: 'Upload a document',
	generate_document: 'Generate a document',
	task: 'Task',
	verify_identity: 'Verify identity',
	aml_check: 'AML check',
	setup_account: 'Set up account'
};

export function stepLabel(type: string): string {
	return STEP_LABELS[type] ?? type;
}

/** Step types a staff member actions directly (a manual review, task or account open). */
const MANUAL_TYPES = new Set(['verify_identity', 'aml_check', 'task', 'setup_account']);

export function isStaffActionable(step: AppStep): boolean {
	return (
		MANUAL_TYPES.has(step.step_type) && (step.status === 'pending' || step.status === 'waiting')
	);
}

/** Kind → human heading for the tier sections. */
export const KIND_LABELS: Record<string, string> = {
	profile: 'Applicants',
	account: 'Accounts',
	application: 'Application'
};

/** Human label for an account role — every member of `crm.account_role`.
 *
 *  `registered_contact` (0155) was missing, so the adult acting on a child's JISA was badged
 *  `registered_contact` beside a `roleLabel`'d "Owner" on the same person. The fallback
 *  sentence-cases rather than passing the key through, because a role this map has not caught up
 *  with is still a word: that is what let the raw key render at all. */
const ROLE_LABELS: Record<string, string> = {
	owner: 'Owner',
	registered_contact: 'Registered contact',
	decision_maker: 'Decision maker',
	trustee: 'Trustee',
	beneficiary: 'Beneficiary',
	attorney: 'Attorney',
	other: 'Other'
};
export function roleLabel(role: string): string {
	return ROLE_LABELS[role] ?? sentenceCase(role);
}

/** The plural of each role, for the group heading on an account card. */
const ROLE_PLURALS: Record<string, string> = {
	owner: 'Owners',
	registered_contact: 'Registered contacts',
	decision_maker: 'Decision makers',
	trustee: 'Trustees',
	beneficiary: 'Beneficiaries',
	attorney: 'Attorneys',
	other: 'Also named'
};

/** Reading order for the roles on an account: the owner first, then whoever acts for them. */
const ROLE_ORDER = [
	'owner',
	'registered_contact',
	'decision_maker',
	'trustee',
	'attorney',
	'beneficiary',
	'other'
];

/**
 * Who is named on an account, grouped under the role that puts them there.
 *
 * The account cards showed nobody: the only name on one was the JISA's `jisa_child` kickoff
 * snapshot, arriving through `accountKickoffSummary`, so a joint GIA and a sole ISA were told
 * apart by their serial alone. `crm.account_contacts` is the real answer and every account has
 * rows in it, which is what `account_parties` projects.
 *
 * A role the map has not caught up with keeps its own humanised heading and sorts last, rather
 * than being dropped — somebody named on the account is on it whatever we call the role.
 */
export function accountParties(c: AppCase): { role: string; label: string; names: string }[] {
	const groups = new Map<string, string[]>();
	for (const party of c.account_parties ?? []) {
		const name = party.name?.trim();
		if (!name) continue;
		const role = (party.role ?? '').trim().toLowerCase() || 'other';
		const names = groups.get(role) ?? [];
		if (!names.includes(name)) names.push(name);
		groups.set(role, names);
	}
	const rank = (role: string) => {
		const at = ROLE_ORDER.indexOf(role);
		return at === -1 ? ROLE_ORDER.length : at;
	};
	return [...groups.entries()]
		.sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
		.map(([role, names]) => ({
			role,
			label: names.length > 1 ? (ROLE_PLURALS[role] ?? roleLabel(role)) : roleLabel(role),
			names: names.join(' & ')
		}));
}

/** The onboarding stage stepper — the projected crm.applications status mapped to a linear journey.
 *  Returns each stage with a state so the UI can render a progress stepper. */
export interface Stage {
	/** The projected `crm.applications.status` this stage stands for — what a step is grouped by. */
	key: string;
	label: string;
	state: 'done' | 'current' | 'todo';
}
const STAGE_ORDER: {
	key: string;
	label: string;
	offshoreOnly?: boolean;
	/** An OUTCOME rather than work in hand: arriving at it IS finishing, so it is never 'current'.
	 *  Declared here so the one stage this is true of is named once, beside the order itself. */
	terminal?: boolean;
}[] = [
	{ key: 'draft', label: 'Draft' },
	{ key: 'fact_find', label: 'Applicant Details' },
	// Terms before the checks — the legacy order, restored once step ordering could cross tiers.
	{ key: 'terms', label: 'Terms' },
	{ key: 'identity_under_review', label: 'Identity' },
	{ key: 'aml_under_review', label: 'AML' },
	{ key: 'offshore_under_review', label: 'Offshore', offshoreOnly: true },
	{ key: 'processing', label: 'Account setup' },
	{ key: 'completed', label: 'Completed', terminal: true }
];

/** Rows written before the projection split `with_client` into the two phases it conflated
 *  (`fact_find` / `terms`) still carry it. It sat at the fact-find position, so that is what it
 *  resolves to — every surface reads the vocabulary through here rather than each keeping its own
 *  half-remembered alias. */
function phase(status: string | null): string | null {
	return status === 'with_client' ? 'fact_find' : status;
}

export function stages(
	status: string | null,
	hasOffshore: boolean,
	engineState?: string | null
): Stage[] {
	const list = STAGE_ORDER.filter((s) => !s.offshoreOnly || hasOffshore);
	if (status === 'cancelled' || engineState === 'cancelled') {
		// A STOPPED journey reached no stage at all — it was abandoned. Marking every stage 'done'
		// (including 'Completed') drew a fully-ticked stepper directly above the "this journey was
		// stopped" alert: the UI claiming the opposite of what happened.
		return list.map((s) => ({ key: s.key, label: s.label, state: 'todo' as const }));
	}
	// `sync_failed` is not off-path: the account setup stage is where it is, and it failed there.
	const key = status === 'sync_failed' ? 'processing' : phase(status);
	const idx = Math.max(
		0,
		list.findIndex((s) => s.key === key)
	);
	// A terminal stage is DONE the moment it is reached, never 'current'. `completed` is only ever
	// `idx` when the status IS 'completed', so standing on it means the journey finished — and the
	// stepper renders a tick for 'done' and the bare step NUMBER for everything else, so a journey
	// at 100% drew "7" in the last circle while every stage behind it wore a tick.
	return list.map((s, i) => ({
		key: s.key,
		label: s.label,
		state: i < idx || (i === idx && s.terminal) ? 'done' : i === idx ? 'current' : 'todo'
	}));
}

/**
 * Which stage a step belongs to, or null when the vocabulary cannot say.
 *
 * Keyed on the step's TYPE, not its id, for the reason the projection gives for keying its own
 * gates that way (`app/flows/projection.py`): the stage vocabulary must not know one flow's step
 * ids, and any firm authoring an `accept_terms` step is making the same statement about where in
 * the journey it sits. Five of these ARE the projection's gates read backwards — a `fill_form` or
 * the application-level `task` is what `forms_ok`/`adviser_declared` aggregate over, so a stage is
 * exactly "the work the gate at this position is waiting for". `review` and `setup_account` are
 * added here because they gate on columns rather than on steps (`offshore_approved`, `synced`),
 * and both name their stage unambiguously.
 *
 * A type this does not name (`upload_document`, `generate_document`, or anything a firm authors
 * later) is deliberately NOT guessed at: it groups on its own, under its own heading, rather than
 * being filed under a stage it may have nothing to do with.
 */
const STAGE_OF_STEP_TYPE: Record<string, string> = {
	fill_form: 'fact_find',
	task: 'fact_find',
	accept_terms: 'terms',
	verify_identity: 'identity_under_review',
	aml_check: 'aml_under_review',
	review: 'offshore_under_review',
	setup_account: 'processing'
};

export function stageOfStep(stepType: string): string | null {
	return STAGE_OF_STEP_TYPE[stepType] ?? null;
}

/** The stored status as a person would say it. `crm.applications.status` is a snake_case machine
 *  value and the journey header printed it verbatim — an adviser was shown "with_client". Reads
 *  through `phase()`, so a legacy row says "Applicant Details" like everything else on the page. */
export function statusLabel(status: string | null): string {
	const key = phase(status);
	const known = STAGE_ORDER.find((s) => s.key === key);
	if (known) return known.label;
	if (key === 'sync_failed') return 'Account setup failed';
	if (key === 'cancelled') return 'Stopped';
	return (key ?? '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** Overall step progress across every case. */
export function progress(cases: AppCase[]): { done: number; total: number; pct: number } {
	let done = 0;
	let total = 0;
	for (const c of cases) {
		for (const s of c.steps) {
			if (s.status === 'skipped' || s.status === 'cancelled') continue;
			total += 1;
			if (s.status === 'fulfilled') done += 1;
		}
	}
	return { done, total, pct: total ? Math.round((done / total) * 100) : 0 };
}

/** One row of the journey's activity feed.
 *
 * `actor` is who carried it out (the adviser who filled the form, the client who accepted).
 * `subject` is whose record it was for. Both are printed on every row that knows them: an
 * adviser reading the log has to see at a glance that Mark completed Bruce's AML check, and a
 * name on its own cannot say which of those two it is. */
export interface ActivityEntry {
	id: number;
	title: string;
	/** Step type, or `started` / `stopped` for the journey itself. The row picks an icon from it. */
	kind: string | null;
	actor: string | null;
	/** The people the step was for, or how many times when the log cannot name them. */
	subject: string | null;
	note: string | null;
	/** A failure or a block, so the icon can say so. */
	attention: boolean;
	at: string;
}

/** Words worth a row of their own. Completing a step is the ordinary case and stays quiet. */
const STEP_DETAIL: Record<string, string | null> = {
	'step.completed': null,
	'step.failed': 'Referred',
	'step.blocked': 'Blocked',
	'step.waiting': 'Awaiting review'
};

const OTHER_EVENTS: Record<string, string> = {
	'execution.started': 'Journey started',
	'execution.cancelled': 'Journey stopped'
};

/** `invited=2 skipped=0 failed=0` is what the declaration handler records. Said as a sentence,
 *  and the zero counts are left out — "0 skipped" is not news. */
function inviteSentence(reason: string): string | null {
	const match = /^invited=(\d+) skipped=(\d+) failed=(\d+)$/.exec(reason);
	if (!match) return null;
	const invited = Number(match[1]);
	const skipped = Number(match[2]);
	const failed = Number(match[3]);
	const parts = [invited === 1 ? '1 client invited' : `${invited} clients invited`];
	if (skipped === 1) parts.push('1 had no email');
	else if (skipped > 1) parts.push(`${skipped} had no email`);
	if (failed === 1) parts.push('1 invitation failed');
	else if (failed > 1) parts.push(`${failed} invitations failed`);
	return parts.join(', ');
}

function said(value: unknown): string | null {
	if (typeof value !== 'string' || !value.trim()) return null;
	const invite = inviteSentence(value);
	if (invite) return invite;
	// A slug (`review_passed`, `account_activated`) is an engine word. The step row already refuses
	// to print those; the feed should too. A note a person wrote contains a space.
	if (!value.includes(' ')) return null;
	return value;
}

function andList(names: string[]): string {
	if (names.length <= 1) return names[0] ?? '';
	if (names.length === 2) return `${names[0]} and ${names[1]}`;
	return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/**
 * The activity feed as a person reads it.
 *
 * A joint journey writes one fact per applicant and per account, so the raw log is the same
 * sentence repeated: four "Account details", two "General declaration", a minute apart. Adjacent
 * facts for the same step collapse into one row, naming the people when the journey still knows
 * them and otherwise saying how many times. Rows done by different people stay separate, so the
 * log can say who actually did each one. A handler reason that is a row of counters is rewritten;
 * a note a person wrote is kept.
 */
export function activityEntries(
	timeline: TimelineEvent[],
	cases: {
		subject_type: string | null;
		subject_id: number | null;
		subject_name: string | null;
		steps: {
			step_key: string;
			step_type?: string;
			title: string | null;
			participant_subject_id: number | null;
			participant_name?: string | null;
		}[];
	}[]
): ActivityEntry[] {
	const titles = new Map<string, string>();
	const kinds = new Map<string, string>();
	const names = new Map<number, string>();
	for (const c of cases) {
		if (c.subject_type === 'contact' && c.subject_id != null && c.subject_name?.trim()) {
			names.set(c.subject_id, c.subject_name.trim());
		}
		for (const step of c.steps) {
			if (step.title && !titles.has(step.step_key)) titles.set(step.step_key, step.title);
			if (step.step_type && !kinds.has(step.step_key)) kinds.set(step.step_key, step.step_type);
			const who = step.participant_name?.trim();
			if (step.participant_subject_id != null && who) names.set(step.participant_subject_id, who);
		}
	}

	const groups: {
		id: number;
		at: string;
		eventType: string;
		stepKey: string;
		note: string | null;
		actor: string | null;
		who: string[];
		count: number;
	}[] = [];

	for (const event of timeline) {
		const stepKey = typeof event.payload?.step_key === 'string' ? event.payload.step_key : '';
		const note = said(event.payload?.note ?? event.payload?.reason);
		const actor =
			typeof event.payload?.actor_name === 'string' && event.payload.actor_name.trim()
				? event.payload.actor_name.trim()
				: null;
		const previous = groups[groups.length - 1];
		const collapses =
			stepKey !== '' &&
			event.event_type in STEP_DETAIL &&
			previous?.eventType === event.event_type &&
			previous.stepKey === stepKey &&
			previous.note === note &&
			previous.actor === actor;
		if (collapses && previous) {
			previous.count += 1;
			const participant = event.payload?.participant;
			const name = typeof participant === 'number' ? names.get(participant) : undefined;
			if (name && !previous.who.includes(name)) previous.who.push(name);
			continue;
		}
		const participant = event.payload?.participant;
		const name = typeof participant === 'number' ? names.get(participant) : undefined;
		groups.push({
			id: event.id,
			at: event.created_at,
			eventType: event.event_type,
			stepKey,
			note,
			actor,
			who: name ? [name] : [],
			count: 1
		});
	}

	return groups.map((group) => {
		const known = group.eventType in STEP_DETAIL;
		const title = known
			? titles.get(group.stepKey) || group.stepKey || 'A step'
			: (OTHER_EVENTS[group.eventType] ?? group.eventType);
		const subject = group.who.length
			? andList(group.who)
			: group.count > 1
				? `${group.count} times`
				: null;
		const outcome = known ? STEP_DETAIL[group.eventType] : null;
		const note = [outcome, group.note].filter((part): part is string => Boolean(part)).join(' · ');
		const kind = known
			? (kinds.get(group.stepKey) ?? null)
			: group.eventType === 'execution.started'
				? 'started'
				: group.eventType === 'execution.cancelled'
					? 'stopped'
					: null;
		return {
			id: group.id,
			title,
			kind,
			actor: group.actor,
			subject,
			note: note || null,
			attention: group.eventType === 'step.failed' || group.eventType === 'step.blocked',
			at: group.at
		};
	});
}

// ── Board: the pipeline the Journeys screen groups cards into. The seven projected onboarding
//    statuses collapse into five plain stages an adviser can scan; the full stepper stays on the
//    detail page. ──────────────────────────────────────────────────────────────────────────────
export interface BoardColumn {
	key: string;
	label: string;
	dot: string; // tailwind bg-* (semantic token, never a palette colour) for the stage dot
}
export const BOARD_COLUMNS: BoardColumn[] = [
	{ key: 'draft', label: 'Draft', dot: 'bg-muted-foreground/40' },
	{ key: 'fact_find', label: 'Applicant Details', dot: 'bg-warning' },
	{ key: 'review', label: 'In review', dot: 'bg-info' },
	{ key: 'terms', label: 'Terms', dot: 'bg-warning' },
	{ key: 'setup', label: 'Setting up', dot: 'bg-brand' },
	{ key: 'done', label: 'Completed', dot: 'bg-success' },
	// Its own column: a stopped journey used to be filed under "Completed", so an abandoned
	// onboarding read as a finished one on the adviser's board.
	{ key: 'stopped', label: 'Stopped', dot: 'bg-destructive/60' }
];

/** Which board column a journey sits in — its PHASE, never whose ball it is.
 *
 *  The column used to be named for an actor ("With client") while being computed from a phase, so
 *  the seeded onboarding — whose whole fact-find is staff-assigned — filed every fresh journey
 *  under the client while its own card read "Blocked — needs you". Columns are phases now and the
 *  chip (`owner`) is the only thing that speaks about people; the two can no longer disagree
 *  because they answer different questions.
 *
 *  The ENGINE state still overrides for the one case it is the truth about: a cancelled execution
 *  is 'stopped' whatever the projection last wrote. */
export function boardColumn(status: string | null, engineState?: string | null): string {
	if (status === 'cancelled' || engineState === 'cancelled') return 'stopped';
	switch (phase(status)) {
		case 'draft':
			return 'draft';
		case 'fact_find':
			return 'fact_find';
		case 'identity_under_review':
		case 'aml_under_review':
		case 'offshore_under_review':
			return 'review';
		case 'terms':
			return 'terms';
		case 'processing':
		case 'sync_failed':
			return 'setup';
		case 'completed':
			return 'done';
		default:
			return 'draft';
	}
}

/** Whose ball a journey is in — the person who needs to act next — as a chip. `you` flags the
 *  adviser's own action (drives the "needs you" highlight + count). */
export interface Owner {
	label: 'You' | 'Client' | 'Compliance' | 'Done' | 'Stopped';
	tone: 'accent' | 'client' | 'compliance' | 'done' | 'stopped';
	you: boolean;
}
export function owner(
	status: string | null,
	engineState?: string | null,
	/** Open steps assigned to the client (`open_client_steps` on the listing row). When supplied
	 *  this DECIDES the client/adviser split instead of the phase label guessing it: who fills a
	 *  fact-find in is the flow author's choice, and the seed happens to assign all five to staff.
	 *  Omitted (an older payload) ⇒ fall back to the phase, as before. */
	openClientSteps?: number
): Owner {
	// The engine's terminal/failed states override the projected label: a `cancelled` or `failed`
	// execution is not "with the client", whatever the last projection wrote.
	if (status === 'cancelled' || engineState === 'cancelled') {
		return { label: 'Stopped', tone: 'stopped', you: false };
	}
	if (status === 'completed') return { label: 'Done', tone: 'done', you: false };
	if (engineState === 'failed') return { label: 'You', tone: 'accent', you: true };
	// A BLOCKED execution needs the adviser, whatever the projection last wrote — the same override
	// `nextHint` already applies. Without it the two disagreed: a with_client journey whose engine is
	// blocked rendered the card text "Blocked — needs you" while its chip said `Client` and it was
	// excluded from the "Need you" count. The board therefore read "Need you: 0" with every journey
	// parked under "With the client", while three staff steps sat there with Fill-in buttons.
	if (engineState === 'blocked') return { label: 'You', tone: 'accent', you: true };
	// Counted, not inferred: the client owes something only if a step is actually assigned to them.
	if (openClientSteps !== undefined) {
		if (openClientSteps > 0) return { label: 'Client', tone: 'client', you: false };
		if (phase(status) === 'offshore_under_review') {
			return { label: 'Compliance', tone: 'compliance', you: false };
		}
		return { label: 'You', tone: 'accent', you: true };
	}
	switch (phase(status)) {
		case 'fact_find':
		case 'terms':
			return { label: 'Client', tone: 'client', you: false };
		case 'offshore_under_review':
			return { label: 'Compliance', tone: 'compliance', you: false };
		default:
			// draft / identity / aml / processing / sync_failed → the adviser acts.
			return { label: 'You', tone: 'accent', you: true };
	}
}

/** A plain-English "what's next" line. The projected status is the label, but a `blocked`/`failed`
 *  ENGINE state is the truth about whether the journey can move — a blocked execution reading
 *  "In review" told the adviser nothing was needed of them. The specific person/step shows when the
 *  card is opened. */
export function nextHint(
	status: string | null,
	engineState?: string | null,
	openClientSteps?: number
): string {
	if (status === 'cancelled' || engineState === 'cancelled') return 'Stopped';
	if (status === 'completed') return 'Account open';
	if (engineState === 'failed') return 'A step failed — needs you';
	if (engineState === 'blocked') return 'In progress — needs you';
	switch (phase(status)) {
		case 'draft':
			return 'Finish the setup';
		case 'fact_find':
			// The same phase reads differently depending on who owes the form — and it is the
			// adviser in every flow the seed authors.
			return openClientSteps === 0
				? 'Complete the applicant details'
				: 'Applicant details with the client';
		case 'identity_under_review':
			return 'Review identity';
		case 'aml_under_review':
			return 'Review AML';
		case 'terms':
			return 'Awaiting the client’s terms';
		case 'offshore_under_review':
			return 'Offshore compliance review';
		case 'processing':
			return 'Open the account';
		case 'sync_failed':
			return 'Retry account setup';
		case 'completed':
			return 'Account open';
		default:
			return '';
	}
}

/** A one-line summary of a profile case's KYC state (for the card subtitle). */
export function kycSummary(c: AppCase): string {
	const idv = c.steps.find((s) => s.step_type === 'verify_identity');
	const aml = c.steps.find((s) => s.step_type === 'aml_check');
	const bits: string[] = [];
	if (idv) bits.push(`ID ${idv.status === 'fulfilled' ? 'verified' : idv.status}`);
	if (aml) bits.push(`AML ${aml.status === 'fulfilled' ? 'clear' : aml.status}`);
	return bits.join(' · ');
}

/** JISA beneficiary name from kickoff account data, if any. */
export function jisaChildName(c: AppCase): string | null {
	const name = c.jisa_child?.name;
	return typeof name === 'string' && name.trim() ? name.trim() : null;
}

const FUNDING_FREQUENCY_LABEL: Record<string, string> = {
	single: 'Single',
	monthly: 'Monthly',
	quarterly: 'Quarterly',
	half_yearly: 'Half-yearly',
	annually: 'Annually'
};

/** Compact funding phrase from kickoff intent, e.g. "Monthly £1,000.00". */
export function fundingKickoffPhrase(c: AppCase): string | null {
	const rc = c.regular_contribution;
	if (!rc?.intent) return null;
	const freqKey = (rc.frequency ?? 'single').trim().toLowerCase().replace(/-/g, '_');
	const freq = FUNDING_FREQUENCY_LABEL[freqKey] ?? 'Contribution';
	if (rc.amount != null && Number.isFinite(Number(rc.amount))) {
		return `${freq} ${formatGBP(Number(rc.amount))}`;
	}
	return freq;
}

/**
 * Compact kickoff meta for an account case card — product · currency · model · fee · capacity · child · funding.
 * Empty string when nothing was captured (the card then shows only the platform title).
 */
export function accountKickoffSummary(c: AppCase): string {
	const bits: string[] = [];
	if (c.product_name?.trim()) bits.push(c.product_name.trim());
	else if (c.product_type?.trim()) bits.push(c.product_type.trim());
	if (c.currency?.trim()) bits.push(c.currency.trim());
	if (c.model_name?.trim()) bits.push(c.model_name.trim());
	if (c.fee_package_names?.length) bits.push(c.fee_package_names.join(', '));
	const capacity = capacityLabel(c.investment_capacity);
	if (capacity) bits.push(capacity);
	const dividend = dividendLabel(c.dividend_treatment);
	if (dividend) bits.push(dividend);
	const child = jisaChildName(c);
	if (child) bits.push(child);
	const funding = fundingKickoffPhrase(c);
	if (funding) bits.push(funding);
	return bits.join(' · ');
}

/** Human label for a stored investment_capacity value. */
export function capacityLabel(value: string | null | undefined): string | null {
	switch ((value ?? '').trim().toLowerCase()) {
		case 'advisory':
			return 'Advisory';
		case 'discretionary':
			return 'Discretionary';
		case 'execution_only':
			return 'Execution only';
		default:
			return null;
	}
}

/** Human label for a stored dividend_treatment value. */
export function dividendLabel(value: string | null | undefined): string | null {
	switch ((value ?? '').trim().toLowerCase()) {
		case 'reinvest':
			return 'Reinvest';
		case 'cash':
			return 'Cash';
		case 'pay_away':
			return 'Pay away';
		default:
			return null;
	}
}
