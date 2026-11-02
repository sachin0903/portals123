/**
 * What staff can DO with a journey step, and why they can't.
 *
 * The audit's headline finding: only 4 of 9 step types were actionable from the journey, so the
 * seeded onboarding flow could never be finished — its six staff-assigned `fill_form` fact-finds had
 * no fulfilment surface anywhere, and `review` (the offshore compliance gate) wasn't actionable
 * either. This module is the single decision table for the journey pages: given a step, what control
 * do we render?
 *
 * It lives beside the journey surfaces rather than in `./applications.ts` deliberately — the shape
 * it returns (an action kind + an honest reason when there is none) is journey-page vocabulary.
 *
 * The actor split it assumes is the legacy app's, read off the source rather than guessed: the old
 * Next.js onboarding gave the client ONE unauthenticated page, and its only mutating public
 * endpoints were terms and identity. Every fact-find was the adviser's. So the adviser can drive a
 * journey end to end, and the two client-owned acts are handled honestly — a terms acknowledgement
 * can be recorded as taken in person (attributed), and a document only the client holds says so.
 */

import { stageOfStep, stages, stepLabel, type Stage } from './applications.js';
import type { AppCase, AppExecution } from './applications.js';

export interface IdentityDocument {
	key: string;
	title: string;
	guidance: string;
	format_hint: string;
	request_uid: string;
	item_uid: string;
	status: string;
	document_uid?: string | null;
}

/** ComplyCube refusing a client's identity upload, as an operator sees it. */
export interface IdentityProviderError {
	message: string | null;
	status_code?: number | null;
	at: string | null;
}

/** A step as `GET /applications/{uid}` returns it, plus the fields the engine now reports. */
export interface JourneyStep {
	step_key: string;
	step_type: string;
	title: string | null;
	assignee: string;
	status: string;
	participant_subject_id: number | null;
	/** Whose instance this is, when the step is fanned per participant. */
	participant_name: string | null;
	request_item_id: number | null;
	/** Prerequisite step keys this step is still held on (`depends_on`), if any. */
	awaiting: string[] | null;
	/** The last signal's reason slug (`already_verified`, `no_actor`, `render_failed: …`). */
	last_event: string | null;
	/** A recorded compliance rationale, when the outcome carried one. */
	note?: string | null;
	/** setup_account: draft→active landed in Graphene's book. */
	local_activated?: boolean | null;
	/** setup_account: custodian enrolment seam (`pending_ops` until provider open lands). */
	custodian_open?: string | null;
	/** The generated document (generate_document), once the render pipeline has a row for it. */
	document_uid: string | null;
	document_name: string | null;
	document_status: string | null;
	/** The client's submitted answers (fill_form), keyed by field_key — for staff review. */
	answers: Record<string, unknown> | null;
	/** Display-only labels for reference-valued answers. The stored ids remain in `answers`. */
	answer_labels?: Record<string, unknown> | null;
	/** Manual identity verification: the selected route and its two independently-filed pieces. */
	identity_choice?: string | null;
	identity_documents?: IdentityDocument[];
	/** Digital identity check: Graphene operations are reviewing it. Status only, never what the
	 *  check found. The step stays the client's in the engine, but nothing on it is theirs. */
	identity_review?: boolean;
	/** Why ComplyCube last refused the client's identity upload. Graphene operations only: the
	 *  API sends it to an operator in a firm's workspace, never to the firm. */
	identity_provider_error?: IdentityProviderError | null;
	/** Where this step sits in the journey (`get_application` stamps it from the definition). */
	order?: number;
	/** ``fill_form`` config.mode: ``gather``, ``review``, or ``account_preferences``. */
	mode?: string | null;
	/** setup_account: the client.setup run this step is waiting on. */
	action_run_uid?: string | null;
	/** That run's status (`awaiting_approval`, `queued`, `running`, …). */
	action_run_status?: string | null;
	/** The caller holds an approver role for that run and did not raise it. */
	action_can_decide?: boolean | null;
}

/** One step with the case it belongs to, so a flat list can still say whose it is. */
export interface OrderedStep {
	case: JourneyCase;
	step: JourneyStep;
}

export interface StageStepGroup {
	key: string;
	/** Terms are grouped around the person accepting them; other repeated work stays by step. */
	kind: 'client_terms' | 'step';
	items: OrderedStep[];
}

/**
 * Group repeated stage work around the decision-maker it belongs to.
 *
 * Forms repeat by type because an adviser typically works through one form across several records.
 * A terms acceptance is the opposite: it is evidence given by one client, so putting Hannah's
 * General/ISA/Platform declarations together is clearer than mixing Hannah and Andy under each
 * document title.
 */
export function groupStageSteps(items: readonly OrderedStep[]): StageStepGroup[] {
	const groups = new Map<string, StageStepGroup>();
	for (const item of items) {
		const isTerms = item.step.step_type === 'accept_terms';
		const subject =
			item.step.participant_subject_id ??
			item.step.participant_name ??
			(item.case.kind === 'profile' ? item.case.subject_name : null) ??
			item.case.uid;
		const key = isTerms ? `client-terms:${subject}` : `step:${item.step.step_key}`;
		const existing = groups.get(key);
		if (existing) existing.items.push(item);
		else groups.set(key, { key, kind: isTerms ? 'client_terms' : 'step', items: [item] });
	}
	return [...groups.values()];
}

/**
 * Every step of the execution in the order it RUNS.
 *
 * The page grouped steps under their case — one card per person, one per account — and within a
 * card the order was right, but across the page it was not: a person's fact-find runs first and
 * their identity and AML run after the client's account-tier terms, so the profile card listed the
 * last work in the journey above the first. No ordering OF the cards can fix that; the list has to
 * be flat.
 *
 * `order` comes from the definition (the only place it exists — `case_steps.sort_order` is never
 * set). Ties break on the step key and then the participant, so two people's instances of the same
 * fanned step keep a stable order between renders rather than shuffling.
 */
export function orderedSteps(cases: readonly JourneyCase[]): OrderedStep[] {
	return cases
		.flatMap((c) =>
			c.steps.filter((s) => s.status !== 'skipped').map((step) => ({ case: c, step }))
		)
		.sort(
			(a, b) =>
				(a.step.order ?? 0) - (b.step.order ?? 0) ||
				a.step.step_key.localeCompare(b.step.step_key) ||
				(a.step.participant_subject_id ?? 0) - (b.step.participant_subject_id ?? 0)
		);
}

/** One stage of the journey, with the work that belongs to it. */
export interface TimelineStage extends Stage {
	steps: OrderedStep[];
	/** Distinct reasons the open work here cannot proceed, in the words `stepAction` gives them. */
	blockers: string[];
	done: number;
	/** Open steps that are waiting on a result (an identity check, a screen), not on a person here. */
	waiting: number;
}

/** A journey as ONE timeline: the stages, each holding its own work. */
export interface JourneyTimeline {
	stages: TimelineStage[];
	/** Work whose step type names no stage — its own group, never filed under a guess. */
	other: OrderedStep[];
	/** The stage to open: the current one, or the first holding unfinished work. */
	openStage: string | null;
}

/**
 * The journey's stages and its work as one thing.
 *
 * The page drew a 7-stage strip AND a 23-item work list from unrelated sources — the strip
 * projected from `crm.applications.status`, the list from the case/step tables, and nothing said
 * which stage a step belonged to. `stageOfStep` is that missing edge (on the step TYPE, the way
 * the projection keys its own gates), so the stages become the headings and the work nests under
 * the stage it is part of.
 *
 * A stage with no steps is kept: it is a real part of the journey that this application's shape
 * does not happen to have work for, and dropping it would make the timeline's length vary with
 * the flow rather than with the journey.
 */
export function journeyTimeline(
	execution: Pick<JourneyExecution, 'cases' | 'application_status' | 'custody' | 'status'>,
	canManage: boolean
): JourneyTimeline {
	const list = stages(
		execution.application_status,
		execution.custody === 'offshore',
		execution.status
	);
	const byKey = new Map<string, TimelineStage>(
		list
			.map((stage) => ({ ...stage, steps: [], blockers: [], done: 0, waiting: 0 }))
			.map((s) => [s.key, s])
	);
	const other: OrderedStep[] = [];
	for (const item of orderedSteps(execution.cases)) {
		const key = stageOfStep(item.step.step_type);
		const stage = key === null ? undefined : byKey.get(key);
		if (stage === undefined) {
			other.push(item);
			continue;
		}
		stage.steps.push(item);
		if (item.step.status === 'fulfilled') stage.done += 1;
		else if (item.step.status === 'waiting') stage.waiting += 1;
		// The account case sits `blocked` until every owner's KYC lands, which holds its
		// `setup_account`. That was its own card saying so; as a reason ON the stage it holds, it
		// explains something. Read from `stepAction` so the timeline and the row cannot disagree.
		const act = stepAction(item.step, canManage, item.case.status);
		const reason = act.kind === 'none' ? act.reason : undefined;
		if (reason && item.step.status !== 'fulfilled' && !stage.blockers.includes(reason)) {
			stage.blockers.push(reason);
		}
	}
	const all = [...byKey.values()];
	// A stage with work takes its state from that work, not from the application status: the status
	// moves on its own schedule, and a stage whose every step is done read as not started while the
	// stage open for it was one the journey had already left. Done when every step is; current for
	// the first stage with work left; to do after that. A stopped journey has no current stage. A
	// stage with no work keeps the projection.
	const stopped = execution.status === 'cancelled' || execution.application_status === 'cancelled';
	let currentTaken = stopped;
	for (const stage of all) {
		if (stage.steps.length === 0) continue;
		if (stage.done === stage.steps.length) {
			stage.state = 'done';
		} else if (!currentTaken) {
			stage.state = 'current';
			currentTaken = true;
		} else {
			stage.state = 'todo';
		}
	}
	// The current stage, but only if it HAS work — `draft`, `processing` and `completed` are real
	// stages that no step type maps to, and a journey sitting in one of them named a stage with
	// nothing to open, so every stage rendered collapsed and nothing said what to do next. An
	// unrecognised or null status pins the projection to the first stage, which is `draft`, so
	// this is the common case rather than an edge.
	const openable = all.filter((s) => s.steps.length > 0);
	const current = openable.find((s) => s.state === 'current');
	const unfinished = openable.find((s) => s.steps.some((o) => o.step.status !== 'fulfilled'));
	return { stages: all, other, openStage: (current ?? unfinished ?? openable[0])?.key ?? null };
}

/** A case whose steps carry the fuller shape. `./applications.ts`' `AppStep` predates the engine
 *  reporting the dependency hold / reason / artifacts, so the journey pages narrow to this. */
export interface JourneyCase extends Omit<AppCase, 'steps'> {
	steps: JourneyStep[];
}

/** The execution as the journey detail page reads it. */
export interface JourneyExecution extends Omit<AppExecution, 'cases'> {
	cases: JourneyCase[];
}

/** What control the journey page renders for a step. */
export type StepAction =
	/** `reason` is a HOLD — something stopping the step, which the timeline also lifts onto the
	 *  stage as a blocker. `note` explains an absence that is not a hold (a check that runs on
	 *  its own), and stays on the row. */
	| { kind: 'none'; reason?: string; note?: string }
	/** Pass / Refer — a compliance decision (`review`), with a note. Identity and AML are NOT
	 *  decided this way: see `AUTOMATED_CHECK_TYPES`. */
	| { kind: 'decide' }
	/** A single "Complete" — a manual task or opening the account. */
	| { kind: 'complete' }
	/** Open the form and fill it in on the client's behalf (staff-assigned `fill_form`). */
	| { kind: 'fill' }
	/** Reopen a COMPLETED staff form and amend the recorded answers. Only while the journey says
	 *  its forms are still amendable — onboarding, before declare-and-send. Optional work, so it
	 *  is never "what to do next". */
	| { kind: 'amend' }
	/** Record that the client accepted these terms in the adviser's presence. */
	| { kind: 'attest' }
	/** Take an open digital identity check onto the two documents the adviser can file. */
	| { kind: 'provide' }
	/** Hand the account off to the platform — see `handoffNotice`. */
	| { kind: 'handoff' }
	/** Release a setup plan that is already waiting for approval. */
	| { kind: 'approve' }
	/** Re-run a failed kick-off (a render that errored). */
	| { kind: 'retry' };

const OPEN_STATUSES = new Set(['pending', 'waiting']);

/** Step types where a staff member records an outcome (pass/refer) rather than just ticking it. */
const DECISION_TYPES = new Set(['review']);

/** Mandatory checks that are never a person's verdict. An identity check is settled by the
 *  digital provider's result or by the manual path's two documents landing; an AML screen by the
 *  screening pipeline. Pass / Refer used to sit on both rows — a `cases:write` holder could pass
 *  an identity with nothing behind it — and the API now refuses a typed outcome on either
 *  (`orchestrator._assert_check_not_decided_by_hand`), so the control is not offered. What the
 *  row offers instead is the work that DOES settle it: the evidence upload. */
const AUTOMATED_CHECK_TYPES = new Set(['verify_identity', 'aml_check']);

/** An identity or AML step is still open, so the screen showing it is waiting on a result that
 *  arrives out of band. Failed, fulfilled, skipped and cancelled are not: a failure is a person's
 *  to retry, and the others are finished. A digital identity check under Graphene's review is a
 *  person too, so that wait does not keep asking. */
export function checksInFlight(
	cases: readonly {
		steps: readonly { step_type: string; status: string; identity_review?: boolean }[];
	}[]
): boolean {
	for (const journeyCase of cases) {
		for (const step of journeyCase.steps) {
			if (!AUTOMATED_CHECK_TYPES.has(step.step_type) || !OPEN_STATUSES.has(step.status)) continue;
			if (withGrapheneForReview(step)) continue;
			return true;
		}
	}
	return false;
}

/** Why an automated check has no control, in the adviser's words. An open AML screen always reads
 *  "With Graphene", whether it is running or held on a match Graphene is reviewing: the firm must
 *  not be able to tell the two apart (tipping off). */
const AUTOMATED_CHECK_LINE: Record<string, string> = {
	verify_identity: 'Completes when the identity check does',
	aml_check: 'With Graphene'
};

/** What a digital identity check under Graphene's review says on the firm's side. */
export const GRAPHENE_REVIEW_LINE = 'With Graphene for review';

/** A digital identity check held for Graphene's review. Nothing on it is the client's, and it is
 *  never the firm's next step: Graphene decides it, in the AML console. */
export function withGrapheneForReview(
	step: Pick<JourneyStep, 'step_type' | 'status' | 'identity_review'>
): boolean {
	return (
		step.step_type === 'verify_identity' &&
		step.identity_review === true &&
		OPEN_STATUSES.has(step.status)
	);
}

/** ComplyCube's refusal of an identity upload, in one line for an operator. */
export function identityProviderErrorLine(error: IdentityProviderError): string {
	const message = error.message?.trim();
	if (message) return message;
	return error.status_code ? `No message (HTTP ${error.status_code})` : 'No message';
}

/** Step types a staff member simply marks done. */
const COMPLETE_TYPES = new Set(['task']);

/** Step types whose artifact only the client can produce — a file only they hold. */
const CLIENT_ONLY_TYPES = new Set(['upload_document']);

/** Short names for the two pieces of manual identity evidence, for a one-line hold. The full
 *  titles are the client's instructions ("Photo of a government-issued ID") and read as a
 *  sentence rather than as a list. */
const EVIDENCE_SHORT: Record<string, string> = {
	photo_id: 'photo ID',
	proof_of_address: 'proof of address'
};

/**
 * The manual identity evidence this step is still waiting for.
 *
 * Keyed on the RECORDED items rather than on `identity_choice`, matching the API: the documents
 * are raised whenever the manual path is taken from either door (the client choosing it, or the
 * adviser requiring it at kickoff, which records no choice on a flow whose provider is already
 * manual), and a digital check records none.
 */
export function outstandingIdentityEvidence(step: JourneyStep): string[] {
	if (step.step_type !== 'verify_identity') return [];
	return (
		(step.identity_documents ?? [])
			// `pending`, not "anything but fulfilled": a CANCELLED item is one the firm stopped
			// asking for, and counting it as outstanding would hold the decision for ever with no
			// way to satisfy it.
			.filter((document) => document.status === 'pending')
			.map((document) => EVIDENCE_SHORT[document.key] ?? document.title)
	);
}

function andList(values: string[]): string {
	const items = values.filter(Boolean);
	if (items.length <= 1) return items[0] ?? '';
	return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/**
 * The action available on a step, or `none` WITH A REASON. Never render a dead control: a button
 * that silently does nothing is worse than an explained absence.
 */
export function stepAction(
	step: JourneyStep,
	canManage: boolean,
	/** The state of the case this step belongs to. An account case sits `blocked` while its owners'
	 *  KYC is outstanding, and `setup_account` must not be offered then — the API refuses it
	 *  (`orchestrator._assert_gate_open_for_setup`), so a live button would 422 in front of whoever
	 *  clicked it. Everything else in that case (the terms declarations) stays actionable. */
	caseStatus?: string,
	/** `forms_amendable` off the execution: a completed onboarding form stays open until the
	 *  adviser declares and sends. False (and an older payload that omits it) ⇒ a done form is
	 *  done. */
	formsAmendable = false
): StepAction {
	if (!canManage) return { kind: 'none' };
	// A completed staff form is the one fulfilled step with somewhere to go: back into the
	// recorded answers, until declare-and-send locks them. A client-assigned form is not — only
	// the client completes one, and amending it here would overwrite their confirmation.
	if (
		formsAmendable &&
		step.status === 'fulfilled' &&
		step.step_type === 'fill_form' &&
		step.assignee === 'staff' &&
		step.request_item_id != null
	) {
		return { kind: 'amend' };
	}
	if (step.status === 'fulfilled') return { kind: 'none' };
	if (step.status === 'cancelled' || step.status === 'skipped') return { kind: 'none' };

	// Held on a prerequisite: no artifact exists yet, so there is nothing to act on. Deliberately
	// no `reason` — `awaitingLine` states the hold under the step's title, in the flow's own words,
	// and returning one here printed the identical sentence twice on every held row.
	if (step.awaiting?.length) return { kind: 'none' };
	if (step.last_event === 'no_actor') {
		return {
			kind: 'none',
			reason: 'Nobody is assigned to this step — fix the flow and start a fresh journey.'
		};
	}
	if (step.step_type === 'generate_document') {
		if (step.status === 'failed') return { kind: 'retry' };
		return { kind: 'none', reason: 'Rendering — this completes on its own.' };
	}
	// A manual identity check IS its two documents: the step completes when the second one lands,
	// and until then the row says which are still missing. The upload control is on the same row,
	// so the work is where a Pass / Refer used to be. Checked BEFORE the `failed` branch so a
	// referred check whose evidence was withdrawn and re-asked shows the hold, not a retry.
	const evidence = outstandingIdentityEvidence(step);
	if (evidence.length) {
		return { kind: 'none', reason: `Waiting for ${andList(evidence)}` };
	}
	if (step.status === 'failed') {
		// A referred/failed compliance decision can be re-decided; anything else — a failed
		// provider check included — needs a retry.
		return DECISION_TYPES.has(step.step_type) ? { kind: 'decide' } : { kind: 'retry' };
	}
	// A staff form whose write-back was refused sits `blocked` with its answers stored and one of
	// them wrong (an incompatible fee package, a value the book rejects). The form is where the
	// wrong answer is, so reopening it is the way out — and the API accepts that resubmission. This
	// row used to render a lock, the refusal and NO control whatever, with the journey only
	// unstickable by SQL.
	if (step.status === 'blocked' && step.step_type === 'fill_form' && step.assignee === 'staff') {
		return step.request_item_id === null
			? { kind: 'none', reason: 'No form is attached to this step yet.' }
			: { kind: 'fill' };
	}
	if (!OPEN_STATUSES.has(step.status)) {
		// Blocked by something other than a correctable form: say so rather than showing a bare lock.
		return step.status === 'blocked'
			? { kind: 'none', reason: 'Blocked — needs you' }
			: { kind: 'none' };
	}

	if (DECISION_TYPES.has(step.step_type)) return { kind: 'decide' };
	if (AUTOMATED_CHECK_TYPES.has(step.step_type)) {
		// A digital identity check is the client's photos until the adviser takes the documents
		// that are already in front of them. A check Graphene is reviewing, and one that already
		// has its evidence items, stay as they are.
		if (
			step.step_type === 'verify_identity' &&
			!withGrapheneForReview(step) &&
			!step.identity_documents?.length
		) {
			return { kind: 'provide' };
		}
		const note = withGrapheneForReview(step)
			? GRAPHENE_REVIEW_LINE
			: AUTOMATED_CHECK_LINE[step.step_type];
		return { kind: 'none', note };
	}
	if (COMPLETE_TYPES.has(step.step_type)) return { kind: 'complete' };
	if (step.step_type === 'setup_account') {
		if (caseStatus === 'blocked') return { kind: 'none', reason: "Waiting on every owner's KYC" };
		// The hand-off already happened. Offering it again re-runs the same held plan and the
		// button comes straight back.
		const hold = setupHold(step);
		if (hold === 'approval' && step.action_can_decide && step.action_run_uid) {
			return { kind: 'approve' };
		}
		if (hold) return { kind: 'none', reason: SETUP_HOLD_LINE[hold] };
		return { kind: 'handoff' };
	}
	if (step.step_type === 'accept_terms') {
		// The client's acceptance is the evidence, and the API refuses to complete this step without
		// it (`orchestrator._assert_fulfilment_supported`). What staff CAN do is record an acceptance
		// taken in person — the acknowledgement is really filed, attributed to them.
		return step.request_item_id === null
			? { kind: 'none', reason: 'No declaration is attached to this step yet.' }
			: { kind: 'attest' };
	}
	if (step.step_type === 'fill_form') {
		if (step.assignee !== 'staff') {
			return { kind: 'none', reason: 'With the client' };
		}
		return step.request_item_id === null
			? { kind: 'none', reason: 'No form is attached to this step yet.' }
			: { kind: 'fill' };
	}
	if (CLIENT_ONLY_TYPES.has(step.step_type)) {
		return { kind: 'none', reason: 'With the client' };
	}
	return { kind: 'none' };
}

/** A step the adviser can act on right now, with the case it belongs to. */
export interface ActionableStep {
	caseUid: string;
	caseKind: string;
	step: JourneyStep;
	/** Whose instance, when the step is fanned across participants. */
	who: string | null;
	anchor: string;
}

/** A stable DOM id for one step row, so a summary can link straight to it. */
export function stepAnchor(caseUid: string, step: JourneyStep): string {
	return `step-${caseUid}-${step.step_key}-${step.participant_subject_id ?? 'all'}`;
}

/** What one person still owes on the client side of the journey. */
export interface ClientOutstanding {
	who: string | null;
	/** Open client-assigned steps for this person, held or not. */
	open: number;
	/** Of those, the ones they can start right now. */
	ready: number;
}

/** Step types completed BY PRODUCING SOMETHING: until the backing request item exists there is
 *  nothing for the client to open. Mirrors the API's `_CLIENT_REQUEST_BACKED`. */
const CLIENT_REQUEST_BACKED = new Set(['fill_form', 'accept_terms', 'upload_document']);

/**
 * What the CLIENT still owes, per person — the other half of "what happens now".
 *
 * The journey record answers the adviser's own question well (`actionableSteps` → "3 steps need
 * you") and said nothing about the client's side beyond a passive "the next steps are theirs".
 * That is the question an adviser is actually asked in front of a client — how much is left for
 * them, and can they start — and answering it from the record beats opening the client portal to
 * find out.
 *
 * `ready` mirrors the API's `_client_can_act` from the same inputs (an open status, no outstanding
 * `depends_on`, and an artifact for the types whose completion IS the artifact). It is a SUMMARY,
 * not a second authority: the client's own page is served by that function directly, so if the two
 * ever disagree the page is right. Nothing is gated on this — it is a sentence on a card.
 */
export function clientOutstanding(cases: readonly JourneyCase[]): ClientOutstanding[] {
	const byWho = new Map<string, ClientOutstanding>();
	for (const c of cases) {
		if (c.status === 'cancelled' || c.status === 'completed') continue;
		for (const step of c.steps) {
			if (step.assignee !== 'client') continue;
			if (!OPEN_STATUSES.has(step.status)) continue;
			// The client's in the engine, but with Graphene: nothing on it is theirs to do.
			if (withGrapheneForReview(step) || step.step_type === 'aml_check') continue;
			// A fanned step names its own participant; an unfanned one belongs to the case's subject.
			const who = step.participant_name ?? (c.kind === 'profile' ? c.subject_name : null);
			const key = who ?? '';
			const row = byWho.get(key) ?? { who, open: 0, ready: 0 };
			row.open += 1;
			const held = Boolean(step.awaiting?.length);
			const missingArtifact =
				CLIENT_REQUEST_BACKED.has(step.step_type) && step.request_item_id === null;
			if (!held && !missingArtifact) row.ready += 1;
			byWho.set(key, row);
		}
	}
	return [...byWho.values()].sort((a, b) => (a.who ?? '').localeCompare(b.who ?? ''));
}

/** One line for the whole client side: "Sam Reed has 5 steps to do (2 ready now)". */
export function clientOutstandingLine(rows: readonly ClientOutstanding[]): string | null {
	if (!rows.length) return null;
	const open = rows.reduce((n, r) => n + r.open, 0);
	const ready = rows.reduce((n, r) => n + r.ready, 0);
	const steps = `${open} step${open === 1 ? '' : 's'}`;
	// One named person reads best; an unnamed single case is "the client" rather than "1 people".
	const only = rows.length === 1 ? rows[0] : null;
	const subject = only ? `${only.who ?? 'The client'} has` : `${rows.length} people have`;
	// "0 ready now" is worth saying: it means the client cannot start yet, which is the difference
	// between waiting on them and waiting on the adviser's own earlier work.
	return `${subject} ${steps} to do${ready === open ? '' : ` (${ready} ready now)`}`;
}

/**
 * Every step the adviser can actually do something with, in case order.
 *
 * A journey renders as fourteen near-identical rows, and thirteen of them are typically held on a
 * prerequisite — so "what do I do next" was a scanning exercise over rows that mostly said
 * "Waiting on …". This is the same `stepAction` decision table the rows use, so the summary and the
 * controls can never disagree about what is actionable; it just answers the question up front.
 */
export function actionableSteps(
	cases: readonly JourneyCase[],
	canManage: boolean
): ActionableStep[] {
	if (!canManage) return [];
	const out: ActionableStep[] = [];
	for (const c of cases) {
		for (const step of c.steps) {
			// Client work can have an adviser override (for example, recording an acceptance taken
			// in person), but that does not make it something the adviser NEEDS to do.
			if (step.assignee === 'client') continue;
			const act = stepAction(step, canManage, c.status);
			// An amendment is a way BACK into finished work, not work that is waiting. Counting it
			// here would put every completed fact-find at the top of "what to do next".
			if (act.kind === 'none' || act.kind === 'amend') continue;
			out.push({
				caseUid: c.uid,
				caseKind: c.kind,
				step,
				who: c.kind === 'profile' ? c.subject_name : step.participant_name,
				anchor: stepAnchor(c.uid, step)
			});
		}
	}
	return out;
}

/**
 * The plain truth about the platform hand-off, or null for every other step.
 *
 * `setup_account` activates the draft account in Graphene's book (`draft` → `active`) and stamps
 * an ops-visible `custodian_open: pending_ops` marker. It does **not** call Seccl/SEI — that is
 * still an ops queue until provider enrolment lands. Saying so on the step is the difference
 * between an adviser who knows local activation happened and custodian open is pending, and one
 * who thinks either nothing happened or both legs completed.
 */
/** A setup step that has already been handed off, and what it is waiting on. */
function setupHold(step: JourneyStep): 'approval' | 'opening' | null {
	if (step.step_type !== 'setup_account' || step.status !== 'waiting') return null;
	if (step.action_run_status === 'awaiting_approval' || step.last_event === 'awaiting_approval') {
		return 'approval';
	}
	return 'opening';
}

const SETUP_HOLD_LINE = {
	approval: 'Waiting for approval',
	opening: 'Opening the account'
} as const;

export function handoffNotice(step: JourneyStep): string | null {
	if (step.step_type !== 'setup_account') return null;
	// Once the step is waiting, the hold line replaces this. Repeating the
	// "activates the draft" sentence next to a finished hand-off is how the
	// button looked like it still had work to do.
	if (step.status === 'waiting') return null;
	if (step.status === 'fulfilled') {
		return 'Account is active in Graphene. Custodian open is pending ops — no Seccl/SEI enrolment was called.';
	}
	return 'Activates the draft account in Graphene and queues operations with custodian/platforms';
}

/** Whose instance of a fanned step this is, when that isn't already obvious from the card. */
export function participantLine(step: JourneyStep, caseKind: string): string | null {
	if (caseKind === 'profile') return null; // Compatibility for older grouped profile cards.
	return step.participant_name;
}

/** The human subject of one row in the flat journey timeline.
 *
 * The timeline replaced the old case cards, so profile rows no longer sit under the person's
 * name. Returning null for them left three identical "Applicant details" rows. Account rows need
 * more than a serial too: two ISAs in one family are distinguished by their owners.
 */
export function stepSubjectLine(step: JourneyStep, journeyCase: JourneyCase): string {
	if (step.participant_name?.trim()) return step.participant_name.trim();
	if (journeyCase.kind === 'profile' && journeyCase.subject_name?.trim()) {
		return journeyCase.subject_name.trim();
	}
	if (journeyCase.kind === 'account') {
		const owners = (journeyCase.account_parties ?? [])
			.filter((party) => party.role === 'owner' && party.name?.trim())
			.map((party) => party.name!.trim());
		const owner = owners.length > 1 ? owners.join(' & ') : owners[0];
		const account =
			journeyCase.product_name?.trim() ||
			journeyCase.product_type?.trim() ||
			journeyCase.account_ref?.trim() ||
			'Account';
		return owner ? `${owner} · ${account}` : account;
	}
	return 'Whole application';
}

/** How many subjects a collapsed group names before it counts the remainder instead. */
const SUBJECT_SUMMARY_LIMIT = 3;

/** One short name for the subject of a step, for a group header rather than a row.
 *
 *  An account is named by its WRAPPER here — the same JISA/GIA/SIPP word the account cards badge
 *  — because the row-level `stepSubjectLine` answers a different question ("which one is THIS
 *  row") and summarising four account forms with it produced two lines of "Joan Pavey · Junior
 *  Individual Savings Account, John Pavey · Self-Invested Personal Pension, …". */
function shortSubject(item: OrderedStep): string {
	if (item.case.kind !== 'account') return stepSubjectLine(item.step, item.case);
	return (
		item.case.product_type?.trim() ||
		item.case.product_name?.trim() ||
		item.case.account_ref?.trim() ||
		'Account'
	);
}

/** Who a collapsed group of repeated forms is for, short enough to read at a glance. The
 *  expanded rows keep the full owner-and-product line for each one. */
export function stepGroupSubjects(items: OrderedStep[]): string {
	const names: string[] = [];
	for (const item of items) {
		const label = shortSubject(item);
		if (label && !names.includes(label)) names.push(label);
	}
	if (names.length <= SUBJECT_SUMMARY_LIMIT) return names.join(', ');
	const shown = names.slice(0, SUBJECT_SUMMARY_LIMIT).join(', ');
	return `${shown} and ${names.length - SUBJECT_SUMMARY_LIMIT} more`;
}

/** Who completes a collapsed group's forms, in the adviser's words. */
export function stepGroupAssignee(items: OrderedStep[]): string {
	const labels = [...new Set(items.map((item) => assigneeLabel(item.step.assignee)))];
	return labels.length === 1 ? `${labels[0]} fills these in` : labels.join(' and ');
}

/**
 * The person a firm-side step belongs to.
 *
 * Staff work on a journey goes to the journey's assignee when one is set, and to the adviser
 * otherwise. A client step is not redirected: it stays with that applicant.
 */
export function firmWorkOwner(journey: {
	adviser_name?: string | null;
	assignee_name?: string | null;
}): string {
	const assignee = journey.assignee_name?.trim();
	if (assignee) return assignee;
	const adviser = journey.adviser_name?.trim();
	if (adviser) return adviser;
	return 'Unassigned';
}

/** Who a step is assigned to: the firm person for staff work, the applicant for the client's. */
export function workOwnerLabel(
	assignee: string,
	firmOwner: string,
	clientName?: string | null
): string {
	if (assignee === 'staff') return firmOwner;
	if (assignee === 'client') return clientName?.trim() || 'The client';
	return assigneeLabel(assignee);
}

/** Who a step is with: `workOwnerLabel`, except that an identity check under review is with
 *  Graphene, whoever the engine assigned it to. */
export function stepOwnerLabel(
	step: JourneyStep,
	firmOwner: string,
	clientName?: string | null
): string {
	// An AML screen is always Graphene's work, never the firm's or the client's.
	if (withGrapheneForReview(step) || step.step_type === 'aml_check') return 'Graphene';
	return workOwnerLabel(step.assignee, firmOwner, clientName);
}

/** "Assigned to <owner>", or just "Unassigned" when nobody is. */
export function assignedTo(owner: string): string {
	return owner === 'Unassigned' ? 'Unassigned' : `Assigned to ${owner}`;
}

/** The distinct people a group of steps is assigned to, in run order. */
export function workOwners(items: readonly OrderedStep[], firmOwner: string): string {
	const labels: string[] = [];
	for (const item of items) {
		const label = stepOwnerLabel(
			item.step,
			firmOwner,
			item.step.participant_name ?? item.case.subject_name
		);
		if (!labels.includes(label)) labels.push(label);
	}
	return labels.join(', ');
}

/** Done, not started, or a count — the state of a section, in words. */
export function sectionProgress(items: readonly { step: { status: string } }[]): string {
	const done = items.filter((item) => item.step.status === 'fulfilled').length;
	if (items.length > 0 && done === items.length) return 'Done';
	if (done === 0 && items.some((item) => item.step.status === 'waiting')) return 'Waiting';
	if (done === 0) return 'Not started';
	return `${done} of ${items.length} done`;
}

/** The next staff form that can be opened now, and whether the journey has already begun. */
export interface NextStaffForm {
	caseUid: string;
	stepKey: string;
	participant: number | null;
	title: string;
	who: string;
	/** True once any step on the journey has been completed. */
	started: boolean;
}

export function nextStaffForm(
	cases: readonly JourneyCase[],
	canManage: boolean
): NextStaffForm | null {
	if (!canManage) return null;
	const items = orderedSteps(cases);
	const next = items.find((item) => stepAction(item.step, true, item.case.status).kind === 'fill');
	if (!next) return null;
	return {
		caseUid: next.case.uid,
		stepKey: next.step.step_key,
		participant: next.step.participant_subject_id,
		title: next.step.title ?? stepLabel(next.step.step_type),
		who: stepSubjectLine(next.step, next.case),
		started: items.some((item) => item.step.status === 'fulfilled')
	};
}

/** Why the seeded onboarding form exists, stated once above repeated applicant/account rows and
 * again on the form itself. Firm-authored titles still fall back to their own description. */
export function stepPurpose(step: JourneyStep): string | null {
	switch (step.step_key) {
		case 'core':
			return 'Personal and contact details';
		case 'jisa_child':
			return 'Details needed for the child account holder';
		case 'jisa_registered_contact':
			return 'Contact details for the adult acting for the child';
		case 'additional_info':
		case 'offshore_kyc':
			return 'Background, employment, tax and source-of-funds information';
		case 'pension':
			return 'Pension and retirement information';
		case 'employer':
			return 'Employer contribution arrangements';
		case 'account_preferences':
			return 'Investment and account choices';
		default:
			return null;
	}
}

/** Whether the journey can still be stopped (a settled one cannot). */
export function isCancellable(status: string): boolean {
	return !['completed', 'cancelled', 'failed'].includes(status);
}

/** Who does a step, in the adviser's words. The engine's own vocabulary (`staff`/`client`) was
 *  printed raw on every single row — "Applicant details · staff". Deliberately NOT the task
 *  `ASSIGNEE_KIND_LABELS`, which answers a different question (user/role/contact). */
const ASSIGNEE_LABELS: Record<string, string> = { staff: 'Your team', client: 'Client' };

export function assigneeLabel(assignee: string): string {
	return ASSIGNEE_LABELS[assignee] ?? assignee;
}

const SETTLED = new Set(['fulfilled', 'skipped', 'cancelled']);

/**
 * Why a step is held, in the flow author's own words — or null when nothing is holding it.
 *
 * `depends_on` is a machine list of step KEYS, and printing it raw gave the adviser
 * "Waiting on employer, pension, offshore_kyc, core": four internal identifiers, in no meaningful
 * order, three of which were already done. Two things fix that:
 *
 * * only prerequisites that are still OUTSTANDING are named — a satisfied one is not a hold; and
 * * they are named by their authored TITLE, falling back to a de-slugged key for a prerequisite
 *   that is not in this case (nothing in the seeded flow does that, but a hand-authored graph can).
 *
 * `siblings` is the step's own case, so a fanned step resolves against the right participant's
 * instances rather than another owner's.
 */
export function awaitingLine(
	awaiting: string[] | null | undefined,
	siblings: readonly Pick<JourneyStep, 'step_key' | 'title' | 'status'>[],
	/** Every step key in the execution → its authored title. A prerequisite may now live in another
	 *  case (ordering crosses tiers), and `siblings` is deliberately this case only — so without
	 *  this the cross-tier holds fell back to a de-slugged key: "Waiting on Terms platform". */
	titles?: ReadonlyMap<string, string>
): string | null {
	if (!awaiting?.length) return null;
	const outstanding: string[] = [];
	for (const key of awaiting) {
		const matches = siblings.filter((s) => s.step_key === key);
		if (matches.length && matches.every((s) => SETTLED.has(s.status))) continue;
		outstanding.push(matches[0]?.title || titles?.get(key) || 'an earlier step');
	}
	if (!outstanding.length) return null;
	if (outstanding.length === 1) return `Waiting on ${outstanding[0]}`;
	if (outstanding.length === 2) return `Waiting on ${outstanding[0]} and ${outstanding[1]}`;
	return `Waiting on ${outstanding[0]} and ${outstanding.length - 1} earlier steps`;
}

/** A short human line for a step's recorded reason, or null when there is nothing worth saying.
 *  The dependency hold is NOT here — `awaitingLine` owns it (it needs the sibling steps to tell an
 *  outstanding prerequisite from a satisfied one). */
export function reasonLine(step: JourneyStep): string | null {
	// The approver still needs the sentence: the button is Approve, and the hold
	// is not also parked in the button slot.
	if (setupHold(step) === 'approval' && step.action_can_decide) return 'Waiting for approval';
	switch (step.last_event) {
		case 'already_verified':
			return 'Reused an existing valid check';
		case 'identity_documents_received':
			return 'Verified against the identity documents on file';
		case 'identity_verified':
			return 'Verified by the identity provider';
		case 'aml_auto_passed':
			return 'Cleared automatically';
		case 'screened_clear':
			return 'Screened clear';
		case 'no_actor':
			return 'No one is assigned to this step';
		case 'render_queued':
			return 'Queued for rendering';
		case 'rendered':
			return null;
		case 'review_referred':
			return 'Referred for manual review';
		case 'review_failed':
			return 'Failed';
		default:
			if (step.last_event?.startsWith('render_failed')) return step.last_event;
			// A handler's refusal is free text, not one of the codes above ("That fee package isn't
			// available for this account"). It is the ONLY statement of why the step is blocked, and
			// dropping it left the row showing a padlock and nothing else — the adviser had to open
			// the activity feed to find out what was wrong. Prose only: an unrecognised snake_case
			// value is an engine word and not something to print at a person.
			if ((step.status === 'blocked' || step.status === 'failed') && step.last_event?.includes(' '))
				return step.last_event;
			return null;
	}
}
