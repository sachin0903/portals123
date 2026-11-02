import { describe, expect, it } from 'vitest';
import {
	actionableSteps,
	assignedTo,
	assigneeLabel,
	awaitingLine,
	checksInFlight,
	clientOutstanding,
	clientOutstandingLine,
	firmWorkOwner,
	groupStageSteps,
	handoffNotice,
	identityProviderErrorLine,
	journeyTimeline,
	nextStaffForm,
	participantLine,
	reasonLine,
	sectionProgress,
	stepAction,
	stepGroupAssignee,
	stepGroupSubjects,
	stepOwnerLabel,
	stepPurpose,
	stepSubjectLine,
	withGrapheneForReview,
	workOwnerLabel,
	workOwners,
	type IdentityDocument,
	type JourneyCase,
	type JourneyExecution,
	type JourneyStep
} from './steps.js';

function step(over: Partial<JourneyStep> = {}): JourneyStep {
	return {
		step_key: 's',
		step_type: 'fill_form',
		title: null,
		assignee: 'staff',
		status: 'pending',
		participant_subject_id: 7,
		participant_name: 'Harriet Vale',
		request_item_id: 1,
		awaiting: null,
		last_event: null,
		document_uid: null,
		document_name: null,
		document_status: null,
		answers: null,
		...over
	};
}

function profileCase(over: Partial<JourneyCase> = {}): JourneyCase {
	return {
		uid: 'case-1',
		kind: 'profile',
		subject_type: 'contact',
		subject_id: 7,
		subject_uid: 'contact-7',
		subject_name: 'Harriet Vale',
		contact_type: 'individual',
		contact_email: null,
		roles: ['owner'],
		portal_status: 'none',
		portal_login_email: null,
		account_ref: null,
		platform_name: null,
		status: 'active',
		steps: [],
		...over
	};
}

describe('stepAction', () => {
	it('offers the in-person acceptance on a terms step rather than a dead "with the client"', () => {
		// The whole point of the actor split: the adviser must be able to finish the journey. The API
		// files the acknowledgement for real and stamps who attested, so this is not a bypass.
		expect(stepAction(step({ step_type: 'accept_terms', assignee: 'client' }), true)).toEqual({
			kind: 'attest'
		});
	});

	it('will not offer an acceptance before the declaration exists', () => {
		const act = stepAction(
			step({ step_type: 'accept_terms', assignee: 'client', request_item_id: null }),
			true
		);
		expect(act.kind).toBe('none');
		expect(act.kind === 'none' && act.reason).toMatch(/No declaration/);
	});

	it('does not offer the platform hand-off while the account case is blocked on KYC', () => {
		// The API refuses this (`orchestrator._assert_gate_open_for_setup`), so a live button would
		// 422 in front of whoever pressed it — and the step itself is `pending`, not `blocked`, so
		// nothing else in this function would have stopped it.
		const act = stepAction(step({ step_type: 'setup_account' }), true, 'blocked');
		expect(act.kind).toBe('none');
		expect(act.kind === 'none' && act.reason).toMatch(/KYC/);
	});

	it('offers the hand-off once the gate is open', () => {
		expect(stepAction(step({ step_type: 'setup_account' }), true, 'active')).toEqual({
			kind: 'handoff'
		});
	});

	it('does not offer the hand-off again once the opening is waiting for approval', () => {
		const held = step({
			step_type: 'setup_account',
			status: 'waiting',
			last_event: 'action_queued',
			action_run_uid: 'run1',
			action_run_status: 'awaiting_approval'
		});
		expect(stepAction(held, true, 'waiting')).toEqual({
			kind: 'none',
			reason: 'Waiting for approval'
		});
		expect(stepAction({ ...held, action_can_decide: true }, true, 'waiting')).toEqual({
			kind: 'approve'
		});
		expect(reasonLine({ ...held, action_can_decide: true })).toBe('Waiting for approval');
		expect(
			stepAction(
				step({ step_type: 'setup_account', status: 'waiting', last_event: 'action_queued' }),
				true,
				'waiting'
			)
		).toEqual({ kind: 'none', reason: 'Opening the account' });
	});

	it('reopens a staff form the write-back refused, instead of a padlock and no way out', () => {
		// The refusal comes AFTER the answers are stored, so the step is `blocked` with a fulfilled
		// item — and one of those answers is the thing to fix. This row offered nothing at all: not
		// Fill in (the status is not open), not Retry (that is `failed` only), and the API refused a
		// resubmission, so the journey could be freed only with SQL.
		expect(
			stepAction(
				step({
					status: 'blocked',
					last_event: "That fee package isn't available for this account."
				}),
				true
			)
		).toEqual({ kind: 'fill' });
	});

	it('states the refusal that blocked the step, since nothing else on the row does', () => {
		// `last_event` holds the handler's own sentence. It matched none of the known codes, so the
		// row printed nothing and the adviser had to open the activity feed to learn what was wrong.
		expect(
			reasonLine(
				step({
					status: 'blocked',
					last_event: "That fee package isn't available for this account."
				})
			)
		).toBe("That fee package isn't available for this account.");
		// An engine word is not a sentence and is not shown; a settled step never explains itself.
		expect(reasonLine(step({ status: 'blocked', last_event: 'awaiting_provider' }))).toBeNull();
		expect(reasonLine(step({ status: 'pending', last_event: 'Some prose' }))).toBeNull();
	});

	it('does not offer a client form or a blocked step of another type as fillable', () => {
		expect(stepAction(step({ status: 'blocked', assignee: 'client' }), true).kind).toBe('none');
		const other = stepAction(step({ status: 'blocked', step_type: 'upload_document' }), true);
		expect(other.kind).toBe('none');
		expect(other.kind === 'none' && other.reason).toMatch(/Blocked/);
	});

	it('still refuses to act on a step held on a prerequisite, and states the hold ONCE', () => {
		const act = stepAction(step({ step_type: 'setup_account', awaiting: ['idv'] }), true);
		expect(act.kind).toBe('none');
		// No reason here on purpose: `awaitingLine` renders the hold under the step's title, and
		// carrying it in both places printed the same sentence twice on every held row.
		expect(act.kind === 'none' && act.reason).toBeUndefined();
	});

	it('gives a client-assigned form no action — only the client can answer it', () => {
		const act = stepAction(step({ assignee: 'client' }), true);
		expect(act).toEqual({ kind: 'none', reason: 'With the client' });
	});

	it('offers nothing at all without cases:write', () => {
		expect(stepAction(step({ step_type: 'accept_terms' }), false)).toEqual({ kind: 'none' });
	});

	// A completed fact-find is finished work, except on an onboarding journey that has not yet been
	// declared and sent: there the recorded answers can still be amended, so the row has to offer a
	// way back in. The flag is the API's own rule, not one guessed from the step.
	it('reopens a completed staff form only while the journey says forms are amendable', () => {
		const done = step({ status: 'fulfilled', answers: { first_name: 'Ada' } });
		expect(stepAction(done, true, 'active', true)).toEqual({ kind: 'amend' });
		// Declared and sent, a non-onboarding flow, or a payload that predates the flag.
		expect(stepAction(done, true).kind).toBe('none');
		expect(stepAction(done, true, 'active', false).kind).toBe('none');
		// The client's own confirmation is not the adviser's to rewrite.
		expect(
			stepAction(step({ status: 'fulfilled', assignee: 'client' }), true, 'active', true).kind
		).toBe('none');
		// And it is never "what to do next": Continue still points at the form that is open.
		const cases = [
			profileCase({
				steps: [done, step({ step_key: 'next', title: 'Additional information' })]
			})
		];
		expect(nextStaffForm(cases, true)?.stepKey).toBe('next');
		expect(actionableSteps(cases, true).map((a) => a.step.step_key)).toEqual(['next']);
	});

	/** A manual identity check IS the two documents `app/idv/manual.py` asks for, and it completes
	 *  when the second one lands. Nothing is decided by hand — Pass / Refer used to sit on the row
	 *  from the moment the step opened, so the check could be recorded as passed with neither the
	 *  photo ID nor the proof of address ever supplied — and the API refuses a typed outcome on
	 *  either check (`orchestrator._assert_check_not_decided_by_hand`). */
	const evidence = (over: Partial<IdentityDocument> = {}): IdentityDocument => ({
		key: 'photo_id',
		title: 'Photo of a government-issued ID',
		guidance: 'A passport or photocard driving licence.',
		format_hint: 'JPG, PNG or PDF',
		request_uid: 'req1',
		item_uid: 'item1',
		status: 'pending',
		...over
	});
	const identityStep = (documents: IdentityDocument[]): JourneyStep =>
		step({
			step_key: 'idv',
			step_type: 'verify_identity',
			status: 'waiting',
			identity_choice: 'manual',
			identity_documents: documents
		});

	it('will not decide a manual identity check while its evidence is outstanding', () => {
		const act = stepAction(
			identityStep([evidence(), evidence({ key: 'proof_of_address', item_uid: 'item2' })]),
			true
		);
		expect(act).toEqual({
			kind: 'none',
			reason: 'Waiting for photo ID and proof of address'
		});
	});

	it('names only what is still missing', () => {
		const act = stepAction(
			identityStep([
				evidence({ status: 'fulfilled' }),
				evidence({ key: 'proof_of_address', item_uid: 'item2' })
			]),
			true
		);
		expect(act.kind === 'none' && act.reason).toBe('Waiting for proof of address');
	});

	it('never offers Pass / Refer on an identity check, evidence in or not', () => {
		// Identity is mandatory and is settled by the check itself: the provider's result, or both
		// documents landing (which completes the step server-side). A row with the evidence in and
		// the step still open is a moment between the upload and the recompute, not a decision —
		// and the API refuses the outcome anyway, so a button here would 422.
		const act = stepAction(
			identityStep([
				evidence({ status: 'fulfilled' }),
				evidence({ key: 'proof_of_address', item_uid: 'item2', status: 'fulfilled' })
			]),
			true
		);
		expect(act.kind).toBe('none');
		// A NOTE, not a reason: nothing is holding the step, so the timeline must not lift it onto
		// the stage as a blocker the way it lifts "Waiting for photo ID".
		expect(act.kind === 'none' && act.reason).toBeUndefined();
		expect(act.kind === 'none' && act.note).toBe('Completes when the identity check does');
	});

	/** A cancelled item is one the firm stopped asking for. Counting it as outstanding would hold
	 *  the step for ever with nothing able to satisfy it. */
	it('does not hold the step on an item the firm withdrew', () => {
		const act = stepAction(identityStep([evidence({ status: 'cancelled' })]), true);
		expect(act.kind).toBe('none');
		expect(act.kind === 'none' && act.reason).toBeUndefined();
	});

	/** A digital check records no evidence items. The adviser can take it onto documents. */
	it('lets an adviser provide documents for an open digital identity check', () => {
		const act = stepAction(
			step({ step_key: 'idv', step_type: 'verify_identity', status: 'waiting' }),
			true
		);
		expect(act).toEqual({ kind: 'provide' });
	});

	/** Graphene operations are reviewing the check: nothing is the adviser's or the client's. */
	it('says a digital identity check under review is with Graphene', () => {
		const reviewing = step({
			step_key: 'idv',
			step_type: 'verify_identity',
			assignee: 'client',
			status: 'waiting',
			identity_review: true
		});
		expect(stepAction(reviewing, true)).toEqual({
			kind: 'none',
			note: 'With Graphene for review'
		});
		expect(withGrapheneForReview(reviewing)).toBe(true);
		// Only an open digital check: a failed one offers its retry, and the flag means nothing on
		// any other step type.
		expect(withGrapheneForReview({ ...reviewing, status: 'failed' })).toBe(false);
		expect(stepAction({ ...reviewing, status: 'failed' }, true)).toEqual({ kind: 'retry' });
		expect(withGrapheneForReview({ ...reviewing, step_type: 'aml_check' })).toBe(false);
		expect(withGrapheneForReview({ ...reviewing, identity_review: false })).toBe(false);
	});

	/** A held check is Graphene's to decide: never the firm's or the client's "what next". */
	it('keeps an identity check held for review off every to-do list', () => {
		const reviewing = step({
			step_key: 'idv',
			step_type: 'verify_identity',
			assignee: 'client',
			status: 'waiting',
			identity_review: true
		});
		// The row keeps its note, and neither the adviser's list nor the client's counts it.
		expect(stepAction(reviewing, true)).toEqual({ kind: 'none', note: 'With Graphene for review' });
		const cases = [profileCase({ steps: [reviewing] })];
		expect(actionableSteps(cases, true)).toEqual([]);
		expect(
			actionableSteps([profileCase({ steps: [{ ...reviewing, assignee: 'staff' }] })], true)
		).toEqual([]);
		expect(clientOutstanding(cases)).toEqual([]);
	});

	it('offers a retry, not a decision, on a failed identity check', () => {
		// A provider that rejected the identity fails the step; the way forward is to run it
		// again, never to overrule it by hand.
		expect(
			stepAction(step({ step_key: 'idv', step_type: 'verify_identity', status: 'failed' }), true)
		).toEqual({ kind: 'retry' });
	});

	it('will not retry a referred check whose evidence was withdrawn and re-asked', () => {
		const act = stepAction(
			step({
				step_key: 'idv',
				step_type: 'verify_identity',
				status: 'failed',
				identity_documents: [evidence()]
			}),
			true
		);
		expect(act.kind).toBe('none');
	});

	it('gives an AML screen no control: it is automated end to end', () => {
		expect(
			stepAction(step({ step_key: 'aml', step_type: 'aml_check', status: 'waiting' }), true)
		).toEqual({ kind: 'none', note: 'With Graphene' });
	});

	it('still offers Pass / Refer on a compliance review — that step IS a person’s decision', () => {
		expect(stepAction(step({ step_key: 'offshore_review', step_type: 'review' }), true)).toEqual({
			kind: 'decide'
		});
		expect(
			stepAction(step({ step_key: 'offshore_review', step_type: 'review', status: 'failed' }), true)
		).toEqual({ kind: 'decide' });
	});

	it('says how a finished check was satisfied', () => {
		expect(
			reasonLine(step({ status: 'fulfilled', last_event: 'identity_documents_received' }))
		).toBe('Verified against the identity documents on file');
		expect(reasonLine(step({ status: 'fulfilled', last_event: 'identity_verified' }))).toBe(
			'Verified by the identity provider'
		);
		expect(reasonLine(step({ status: 'fulfilled', last_event: 'aml_auto_passed' }))).toBe(
			'Cleared automatically'
		);
	});
});

describe('handoffNotice', () => {
	it('says local activation + pending ops, before and after the hand-off is recorded', () => {
		expect(handoffNotice(step({ step_type: 'setup_account' }))).toBe(
			'Activates the draft account in Graphene and queues operations with custodian/platforms'
		);
		expect(handoffNotice(step({ step_type: 'setup_account', status: 'fulfilled' }))).toMatch(
			/active in Graphene.*pending ops|pending ops/i
		);
		expect(
			handoffNotice(
				step({ step_type: 'setup_account', status: 'waiting', last_event: 'action_queued' })
			)
		).toBeNull();
	});

	it('says nothing about any other step type', () => {
		expect(handoffNotice(step({ step_type: 'accept_terms' }))).toBeNull();
		expect(handoffNotice(step())).toBeNull();
	});
});

describe('participantLine', () => {
	it('names whose instance a fanned step is inside an account case', () => {
		// Two owners produce two identical "General declaration" rows; without the name the adviser
		// cannot tell which one they are about to action.
		expect(participantLine(step({ step_type: 'accept_terms' }), 'account')).toBe('Harriet Vale');
	});

	it('stays quiet on a profile card, which is already that person', () => {
		expect(participantLine(step(), 'profile')).toBeNull();
	});
});

describe('stepGroupSubjects', () => {
	const item = (journeyCase: JourneyCase, over: Partial<JourneyStep> = {}) => ({
		case: journeyCase,
		step: step({ participant_name: null, ...over })
	});

	it('lists the applicants a repeated form is for', () => {
		expect(
			stepGroupSubjects([
				item(profileCase({ subject_name: 'David Pavey' })),
				item(profileCase({ subject_name: 'Joan Pavey' })),
				item(profileCase({ subject_name: 'John Pavey' }))
			])
		).toBe('David Pavey, Joan Pavey, John Pavey');
	});

	// The header used the row-level subject line, so four account forms summarised as two lines of
	// "Joan Pavey · Junior Individual Savings Account, John Pavey · Self-Invested Personal
	// Pension, …". An account is named by its wrapper here; the expanded rows keep the full line.
	it('names an account by its wrapper rather than its owners and full product name', () => {
		const accountCase = (over: Partial<JourneyCase>) =>
			profileCase({ kind: 'account', subject_type: 'account', subject_name: null, ...over });
		expect(
			stepGroupSubjects([
				item(
					accountCase({
						product_type: 'JISA',
						product_name: 'Junior Individual Savings Account',
						account_parties: [{ name: 'David Pavey', role: 'owner' }]
					})
				),
				item(accountCase({ product_type: 'GIA', product_name: 'General Investment Account' })),
				item(accountCase({ product_type: 'SIPP', product_name: 'Self-Invested Personal Pension' }))
			])
		).toBe('JISA, GIA, SIPP');
	});

	it('counts the remainder once there are more subjects than a line can hold', () => {
		expect(
			stepGroupSubjects(
				['Ann', 'Bo', 'Cy', 'Di', 'Ed'].map((name) => item(profileCase({ subject_name: name })))
			)
		).toBe('Ann, Bo, Cy and 2 more');
	});
});

describe('groupStageSteps', () => {
	const item = (journeyCase: JourneyCase, over: Partial<JourneyStep> = {}) => ({
		case: journeyCase,
		step: step(over)
	});

	it('groups declarations around the client accepting them, not the declaration type', () => {
		const account = profileCase({ uid: 'account-1', kind: 'account' });
		const groups = groupStageSteps([
			item(account, {
				step_key: 'terms_general',
				step_type: 'accept_terms',
				title: 'General declaration',
				participant_subject_id: 7,
				participant_name: 'Hannah Gaunt'
			}),
			item(account, {
				step_key: 'terms_isa',
				step_type: 'accept_terms',
				title: 'ISA declaration',
				participant_subject_id: 7,
				participant_name: 'Hannah Gaunt'
			}),
			item(account, {
				step_key: 'terms_general',
				step_type: 'accept_terms',
				title: 'General declaration',
				participant_subject_id: 8,
				participant_name: 'Andy Gaunt'
			})
		]);

		expect(groups.map((group) => [group.kind, group.items.length])).toEqual([
			['client_terms', 2],
			['client_terms', 1]
		]);
	});

	it('continues to group repeated forms by step type', () => {
		const groups = groupStageSteps([
			item(profileCase({ uid: 'hannah' }), { step_key: 'core' }),
			item(profileCase({ uid: 'andy' }), { step_key: 'core' })
		]);
		expect(groups).toHaveLength(1);
		expect(groups[0]?.kind).toBe('step');
		expect(groups[0]?.items).toHaveLength(2);
	});
});

describe('firm work owner', () => {
	const item = (assignee: string, name = 'Diane Hobbis') => ({
		case: profileCase({ subject_name: name }),
		step: step({ assignee, participant_name: name })
	});

	it('gives firm work to the journey assignee, or the adviser when nobody else is assigned', () => {
		expect(firmWorkOwner({ adviser_name: 'Alex Morgan', assignee_name: null })).toBe('Alex Morgan');
		expect(firmWorkOwner({ adviser_name: 'Alex Morgan', assignee_name: 'Priya Shah' })).toBe(
			'Priya Shah'
		);
		expect(firmWorkOwner({ adviser_name: null, assignee_name: null })).toBe('Unassigned');
	});

	it('names the applicant on a client step and the firm person on a staff step', () => {
		expect(workOwnerLabel('staff', 'Alex Morgan')).toBe('Alex Morgan');
		expect(workOwnerLabel('client', 'Alex Morgan', 'Diane Hobbis')).toBe('Diane Hobbis');
		expect(workOwners([item('staff'), item('staff', 'John Hobbis')], 'Priya Shah')).toBe(
			'Priya Shah'
		);
	});

	it('says whether a section is done', () => {
		expect(sectionProgress([item('staff')])).toBe('Not started');
		expect(sectionProgress([{ step: step({ status: 'waiting' }) }])).toBe('Waiting');
		expect(sectionProgress([{ step: step({ status: 'fulfilled' }) }])).toBe('Done');
		expect(
			sectionProgress([
				{ step: step({ status: 'fulfilled' }) },
				{ step: step({ status: 'pending' }) }
			])
		).toBe('1 of 2 done');
	});

	it('offers Start until something is done, then Continue on the next open form', () => {
		const open = profileCase({
			uid: 'open',
			steps: [step({ step_key: 'core', title: 'Applicant details' })]
		});
		expect(nextStaffForm([open], true)).toMatchObject({
			title: 'Applicant details',
			who: 'Harriet Vale',
			started: false
		});
		expect(nextStaffForm([open], false)).toBeNull();
		const begun = profileCase({
			steps: [
				step({ step_key: 'core', title: 'Applicant details', status: 'fulfilled' }),
				step({ step_key: 'pension', title: 'Pension information', participant_name: 'John Hobbis' })
			]
		});
		expect(nextStaffForm([begun], true)).toMatchObject({
			stepKey: 'pension',
			who: 'John Hobbis',
			started: true
		});
	});
});

describe('stepGroupAssignee', () => {
	const item = (assignee: string) => ({ case: profileCase(), step: step({ assignee }) });

	it('states who completes the group in the adviser’s words', () => {
		expect(stepGroupAssignee([item('staff'), item('staff')])).toBe('Your team fills these in');
		expect(stepGroupAssignee([item('client'), item('client')])).toBe('Client fills these in');
	});

	it('names both sides when a group is split between them', () => {
		expect(stepGroupAssignee([item('staff'), item('client')])).toBe('Your team and Client');
	});
});

describe('stepSubjectLine', () => {
	it('names a profile step after the applicant now that the timeline is flat', () => {
		expect(
			stepSubjectLine(
				step({ participant_name: null }),
				profileCase({ subject_name: 'David Pavey' })
			)
		).toBe('David Pavey');
	});

	it('distinguishes same-wrapper accounts by their owners', () => {
		expect(
			stepSubjectLine(
				step({ participant_name: null }),
				profileCase({
					kind: 'account',
					subject_type: 'account',
					subject_name: null,
					product_name: 'Stocks & Shares ISA',
					account_parties: [{ name: 'Lisa Pavey', role: 'owner' }]
				})
			)
		).toBe('Lisa Pavey · Stocks & Shares ISA');
	});

	it('names both owners of a joint account', () => {
		expect(
			stepSubjectLine(
				step({ participant_name: null }),
				profileCase({
					kind: 'account',
					subject_type: 'account',
					subject_name: null,
					product_type: 'GIA',
					account_parties: [
						{ name: 'John Pavey', role: 'owner' },
						{ name: 'Lisa Pavey', role: 'owner' }
					]
				})
			)
		).toBe('John Pavey & Lisa Pavey · GIA');
	});

	it('states what the seeded forms collect', () => {
		expect(stepPurpose(step({ step_key: 'core' }))).toBe('Personal and contact details');
		expect(stepPurpose(step({ step_key: 'pension' }))).toBe('Pension and retirement information');
	});
});

describe('awaitingLine', () => {
	const sibs = [
		{ step_key: 'core', title: 'Applicant details', status: 'fulfilled' },
		{ step_key: 'pension', title: 'Pension information', status: 'fulfilled' },
		{ step_key: 'employer', title: 'Employer contributions', status: 'pending' },
		{ step_key: 'offshore_kyc', title: 'Additional information', status: 'pending' }
	];

	it('names only the prerequisites that are still outstanding, by their authored title', () => {
		// What the adviser used to be shown, verbatim: "Waiting on employer, pension, offshore_kyc,
		// core" — four internal keys, in no meaningful order, two of them already done.
		expect(awaitingLine(['employer', 'pension', 'offshore_kyc', 'core'], sibs)).toBe(
			'Waiting on Employer contributions and Additional information'
		);
	});

	it('is silent once every prerequisite is satisfied', () => {
		expect(awaitingLine(['core', 'pension'], sibs)).toBeNull();
		expect(awaitingLine([], sibs)).toBeNull();
		expect(awaitingLine(null, sibs)).toBeNull();
	});

	it('summarises rather than listing a long chain', () => {
		const many = ['a', 'b', 'c', 'd'].map((k) => ({
			step_key: k,
			title: k.toUpperCase(),
			status: 'pending'
		}));
		expect(awaitingLine(['a', 'b', 'c', 'd'], many)).toBe('Waiting on A and 3 earlier steps');
	});

	it('never surfaces an internal step key when the title is missing', () => {
		expect(awaitingLine(['offshore_kyc'], [])).toBe('Waiting on an earlier step');
	});

	it('resolves a fanned step against ITS OWN case, not another owner instance', () => {
		// Two owners each hold a `terms_general`; this case's is done, so nothing is holding here.
		expect(
			awaitingLine(
				['terms_general'],
				[{ step_key: 'terms_general', title: 'General declaration', status: 'fulfilled' }]
			)
		).toBeNull();
	});
});

describe('clientOutstanding', () => {
	/** A case is mostly other fields the summary never reads; only these four matter. */
	const kase = (over: Partial<JourneyCase> & { steps: JourneyStep[] }): JourneyCase =>
		({
			uid: 'c1',
			kind: 'profile',
			status: 'active',
			subject_name: 'Sam Reed',
			...over
		}) as unknown as JourneyCase;

	it('counts the client’s open steps and how many they can start now', () => {
		const rows = clientOutstanding([
			kase({
				steps: [
					step({ step_key: 'terms_a', step_type: 'accept_terms', assignee: 'client' }),
					// Held behind an earlier step: theirs, but not yet startable.
					step({
						step_key: 'terms_b',
						step_type: 'accept_terms',
						assignee: 'client',
						awaiting: ['terms_a']
					}),
					// Theirs and released, but the declaration itself does not exist yet.
					step({
						step_key: 'terms_c',
						step_type: 'accept_terms',
						assignee: 'client',
						request_item_id: null
					}),
					step({ step_key: 'core', assignee: 'staff' }),
					step({ step_key: 'done', assignee: 'client', status: 'fulfilled' })
				]
			})
		]);
		expect(rows).toEqual([{ who: 'Harriet Vale', open: 3, ready: 1 }]);
	});

	it('does not count an identity check under Graphene’s review as the client’s to do', () => {
		const rows = clientOutstanding([
			kase({
				steps: [
					step({
						step_key: 'idv',
						step_type: 'verify_identity',
						assignee: 'client',
						status: 'waiting',
						identity_review: true
					}),
					step({ step_key: 'terms', step_type: 'accept_terms', assignee: 'client' })
				]
			})
		]);
		expect(rows).toEqual([{ who: 'Harriet Vale', open: 1, ready: 1 }]);
		expect(
			clientOutstanding([
				kase({
					steps: [
						step({
							step_key: 'idv',
							step_type: 'verify_identity',
							assignee: 'client',
							status: 'waiting',
							identity_review: true
						})
					]
				})
			])
		).toEqual([]);
	});

	it('attributes an unfanned account step to nobody rather than to the account', () => {
		const rows = clientOutstanding([
			kase({
				kind: 'account',
				subject_name: 'Seccl account',
				steps: [step({ assignee: 'client', participant_name: null })]
			})
		]);
		expect(rows[0].who).toBeNull();
	});

	it('ignores a cancelled or completed case', () => {
		const steps = [step({ assignee: 'client' })];
		expect(clientOutstanding([kase({ status: 'cancelled', steps })])).toEqual([]);
		expect(clientOutstanding([kase({ status: 'completed', steps })])).toEqual([]);
	});

	it('reads as a sentence, and says so when nothing is startable', () => {
		expect(clientOutstandingLine([{ who: 'Sam Reed', open: 5, ready: 2 }])).toBe(
			'Sam Reed has 5 steps to do (2 ready now)'
		);
		// Everything startable needs no parenthetical — the count already says it.
		expect(clientOutstandingLine([{ who: 'Sam Reed', open: 1, ready: 1 }])).toBe(
			'Sam Reed has 1 step to do'
		);
		// The distinction that matters: theirs, but held on the adviser's own earlier work.
		expect(clientOutstandingLine([{ who: 'Sam Reed', open: 5, ready: 0 }])).toBe(
			'Sam Reed has 5 steps to do (0 ready now)'
		);
	});

	it('does not call one unnamed case "1 people"', () => {
		expect(clientOutstandingLine([{ who: null, open: 2, ready: 2 }])).toBe(
			'The client has 2 steps to do'
		);
		expect(clientOutstandingLine([])).toBeNull();
	});

	it('aggregates across people', () => {
		expect(
			clientOutstandingLine([
				{ who: 'Ada', open: 3, ready: 1 },
				{ who: 'Bo', open: 4, ready: 0 }
			])
		).toBe('2 people have 7 steps to do (1 ready now)');
	});
});

describe('adviser step categories', () => {
	const kase = (steps: JourneyStep[]): JourneyCase =>
		({
			uid: 'c1',
			kind: 'profile',
			status: 'active',
			subject_name: 'Sam Reed',
			steps
		}) as unknown as JourneyCase;

	it('does not call an optional in-person override work the adviser needs to do', () => {
		const clientTerms = step({
			step_key: 'terms',
			step_type: 'accept_terms',
			assignee: 'client'
		});
		expect(stepAction(clientTerms, true).kind).toBe('attest');
		expect(actionableSteps([kase([clientTerms])], true)).toEqual([]);
	});
});

describe('assigneeLabel', () => {
	it('speaks the adviser’s language, not the engine’s', () => {
		// Every step row printed the raw engine value: "Applicant details · staff".
		expect(assigneeLabel('staff')).toBe('Your team');
		expect(assigneeLabel('client')).toBe('Client');
	});

	it('shows an unknown assignee as-is rather than blanking the row', () => {
		expect(assigneeLabel('compliance')).toBe('compliance');
	});
});

describe('journeyTimeline', () => {
	const kase = (steps: JourneyStep[], over: Partial<JourneyCase> = {}): JourneyCase =>
		({
			uid: 'c1',
			kind: 'profile',
			status: 'active',
			subject_name: 'Sam Reed',
			...over,
			steps
		}) as unknown as JourneyCase;
	const ex = (
		cases: JourneyCase[],
		over: Partial<JourneyExecution> = {}
	): Pick<JourneyExecution, 'cases' | 'application_status' | 'custody' | 'status'> =>
		({
			cases,
			application_status: 'fact_find',
			custody: 'onshore',
			status: 'active',
			...over
		}) as unknown as JourneyExecution;

	it('nests each step under the stage its TYPE names', () => {
		// The whole point: the stage strip and the work list were computed from unrelated sources
		// and nothing said which stage a step belonged to.
		const t = journeyTimeline(
			ex([
				kase([
					step({ step_key: 'core', step_type: 'fill_form' }),
					step({ step_key: 'declaration', step_type: 'task' }),
					step({ step_key: 'terms_a', step_type: 'accept_terms' }),
					step({ step_key: 'idv', step_type: 'verify_identity' }),
					step({ step_key: 'aml', step_type: 'aml_check' }),
					step({ step_key: 'setup', step_type: 'setup_account' })
				])
			]),
			true
		);
		const byKey = new Map(t.stages.map((st) => [st.key, st.steps.map((o) => o.step.step_key)]));
		expect(byKey.get('fact_find')).toEqual(['core', 'declaration']);
		expect(byKey.get('terms')).toEqual(['terms_a']);
		expect(byKey.get('identity_under_review')).toEqual(['idv']);
		expect(byKey.get('aml_under_review')).toEqual(['aml']);
		expect(byKey.get('processing')).toEqual(['setup']);
		expect(t.other).toEqual([]);
	});

	it('keeps a stage the journey has no work in', () => {
		// A stage with nothing in it is a real part of the journey this application's shape has no
		// work for. Dropping it would make the timeline's length vary with the flow.
		const t = journeyTimeline(ex([kase([step({ step_key: 'core' })])]), true);
		expect(t.stages.map((st) => st.key)).toEqual([
			'draft',
			'fact_find',
			'terms',
			'identity_under_review',
			'aml_under_review',
			'processing',
			'completed'
		]);
		expect(t.stages.find((st) => st.key === 'draft')?.steps).toEqual([]);
	});

	it('adds the offshore stage only for an offshore journey', () => {
		const offshore = journeyTimeline(
			ex([kase([step({ step_key: 'offshore_review', step_type: 'review' })])], {
				custody: 'offshore'
			}),
			true
		);
		expect(offshore.stages.map((st) => st.key)).toContain('offshore_under_review');
		expect(offshore.stages.find((st) => st.key === 'offshore_under_review')?.steps.length).toBe(1);
		// Onshore has no offshore stage, so its review step has nowhere to sit and groups on its own
		// rather than being filed under a stage it has nothing to do with.
		const onshore = journeyTimeline(
			ex([kase([step({ step_key: 'offshore_review', step_type: 'review' })])]),
			true
		);
		expect(onshore.stages.map((st) => st.key)).not.toContain('offshore_under_review');
		expect(onshore.other.map((o) => o.step.step_key)).toEqual(['offshore_review']);
	});

	it('groups a step type the stage vocabulary cannot name, rather than guessing', () => {
		const t = journeyTimeline(
			ex([
				kase([
					step({ step_key: 'core' }),
					step({ step_key: 'render', step_type: 'generate_document' }),
					step({ step_key: 'proof', step_type: 'upload_document', assignee: 'client' })
				])
			]),
			true
		);
		// `orderedSteps` sorts on the definition's `order` and then the step key, so this is
		// alphabetical here — the fixtures carry no order.
		expect(t.other.map((o) => o.step.step_key)).toEqual(['proof', 'render']);
	});

	it('counts what is done and opens the first stage with work left, whatever the status says', () => {
		const t = journeyTimeline(
			ex(
				[
					kase([
						step({ step_key: 'core', status: 'fulfilled' }),
						step({ step_key: 'extra', status: 'pending' }),
						step({ step_key: 'terms_a', step_type: 'accept_terms' })
					])
				],
				{ application_status: 'terms' }
			),
			true
		);
		const factFind = t.stages.find((st) => st.key === 'fact_find');
		expect(factFind?.done).toBe(1);
		expect(factFind?.steps.length).toBe(2);
		expect(factFind?.state).toBe('current');
		expect(t.stages.find((st) => st.key === 'terms')?.state).toBe('todo');
		expect(t.openStage).toBe('fact_find');
	});

	it('marks a stage done when its work is, even while the status lags behind', () => {
		// The application status said fact-find while the terms were all accepted and identity was
		// waiting: the terms read as not started and the fact-find stage opened.
		const t = journeyTimeline(
			ex(
				[
					kase([
						step({ step_key: 'core', status: 'fulfilled' }),
						step({ step_key: 'terms_a', step_type: 'accept_terms', status: 'fulfilled' }),
						step({ step_key: 'idv', step_type: 'verify_identity', status: 'waiting' })
					])
				],
				{ application_status: 'fact_find' }
			),
			true
		);
		const state = (key: string) => t.stages.find((st) => st.key === key)?.state;
		expect(state('fact_find')).toBe('done');
		expect(state('terms')).toBe('done');
		const identity = t.stages.find((st) => st.steps.some((o) => o.step.step_key === 'idv'));
		expect(identity?.state).toBe('current');
		expect(identity?.waiting).toBe(1);
		expect(t.openStage).toBe(identity?.key);
	});

	it('never names a stage that has no work to open', () => {
		// `draft`, `processing` and `completed` are real stages no step TYPE maps to. A journey
		// sitting in one named a stage with nothing in it, so every stage rendered collapsed and
		// nothing said what to do next — and an unrecognised or null status pins the projection to
		// the first stage, `draft`, so this was the common case rather than an edge.
		for (const status of ['draft', null, 'something_unrecognised']) {
			const t = journeyTimeline(
				ex([kase([step({ step_key: 'core' })])], { application_status: status }),
				true
			);
			expect(t.openStage).toBe('fact_find');
			const open = t.stages.find((s) => s.key === t.openStage);
			expect(open?.steps.length).toBeGreaterThan(0);
		}
	});

	it('opens nothing when there is nothing to open', () => {
		const t = journeyTimeline(ex([kase([])]), true);
		expect(t.openStage).toBeNull();
	});

	it('falls back to the first unfinished stage when no stage is current', () => {
		// A stopped journey reached no stage at all, so every one is `todo`.
		const t = journeyTimeline(
			ex([kase([step({ step_key: 'terms_a', step_type: 'accept_terms' })])], {
				application_status: 'cancelled',
				status: 'cancelled'
			}),
			true
		);
		expect(t.stages.every((st) => st.state === 'todo')).toBe(true);
		expect(t.openStage).toBe('terms');
	});

	it('attaches a hold to the stage it holds, in the words the row uses', () => {
		// "Waiting on every owner's KYC" was a card of its own on the account; as a reason on the
		// stage it blocks, it explains something. Read from `stepAction`, so the timeline and the
		// row cannot disagree.
		const t = journeyTimeline(
			ex([
				kase([step({ step_key: 'setup', step_type: 'setup_account' })], {
					uid: 'c2',
					kind: 'account',
					status: 'blocked'
				})
			]),
			true
		);
		expect(t.stages.find((st) => st.key === 'processing')?.blockers).toEqual([
			"Waiting on every owner's KYC"
		]);
	});

	it('reports no blocker for finished work', () => {
		const t = journeyTimeline(
			ex([
				kase([step({ step_key: 'setup', step_type: 'setup_account', status: 'fulfilled' })], {
					uid: 'c2',
					kind: 'account',
					status: 'blocked'
				})
			]),
			true
		);
		expect(t.stages.find((st) => st.key === 'processing')?.blockers).toEqual([]);
	});
});

describe('checksInFlight', () => {
	const stepOf = (step_type: string, status: string) => ({ step_type, status });

	it('is true while an identity or AML step is still open', () => {
		expect(checksInFlight([{ steps: [stepOf('verify_identity', 'waiting')] }])).toBe(true);
		expect(checksInFlight([{ steps: [stepOf('aml_check', 'pending')] }])).toBe(true);
	});

	it('is false once the check has settled, failed, or is some other kind of step', () => {
		expect(checksInFlight([{ steps: [stepOf('verify_identity', 'fulfilled')] }])).toBe(false);
		expect(checksInFlight([{ steps: [stepOf('aml_check', 'failed')] }])).toBe(false);
		expect(checksInFlight([{ steps: [stepOf('fill_form', 'pending')] }])).toBe(false);
		expect(checksInFlight([{ steps: [stepOf('setup_account', 'waiting')] }])).toBe(false);
		expect(checksInFlight([])).toBe(false);
	});

	it('is false while Graphene is reviewing the identity photos', () => {
		expect(
			checksInFlight([
				{ steps: [{ step_type: 'verify_identity', status: 'waiting', identity_review: true }] }
			])
		).toBe(false);
		expect(
			checksInFlight([
				{
					steps: [
						{ step_type: 'verify_identity', status: 'waiting', identity_review: true },
						stepOf('aml_check', 'pending')
					]
				}
			])
		).toBe(true);
	});
});

describe('identity checks with Graphene', () => {
	it('names Graphene as the owner of a check under review, and the client otherwise', () => {
		const idv = step({
			step_key: 'idv',
			step_type: 'verify_identity',
			assignee: 'client',
			status: 'waiting'
		});
		expect(stepOwnerLabel({ ...idv, identity_review: true }, 'Ann Adviser', 'Sam Reed')).toBe(
			'Graphene'
		);
		expect(stepOwnerLabel(idv, 'Ann Adviser', 'Sam Reed')).toBe('Sam Reed');
		expect(stepOwnerLabel(step({ assignee: 'staff' }), 'Ann Adviser', 'Sam Reed')).toBe(
			'Ann Adviser'
		);
	});

	it('names Graphene as the owner of an AML screen, running or held alike', () => {
		const aml = step({
			step_key: 'aml',
			step_type: 'aml_check',
			assignee: 'staff',
			status: 'waiting'
		});
		expect(stepOwnerLabel(aml, 'Unassigned', 'Sam Reed')).toBe('Graphene');
		expect(stepAction(aml, true)).toEqual({ kind: 'none', note: 'With Graphene' });
	});

	it('prints ComplyCube’s refusal for an operator, or says it gave none', () => {
		expect(
			identityProviderErrorLine({
				message: 'Image resolution is too low.',
				status_code: 422,
				at: '2026-10-02T10:00:00+00:00'
			})
		).toBe('Image resolution is too low.');
		expect(identityProviderErrorLine({ message: null, status_code: 400, at: null })).toBe(
			'No message (HTTP 400)'
		);
		expect(identityProviderErrorLine({ message: '  ', at: null })).toBe('No message');
	});
});

describe('assignedTo', () => {
	it('says who the work is with, or just "Unassigned"', () => {
		expect(assignedTo('Olive Adviser')).toBe('Assigned to Olive Adviser');
		expect(assignedTo('Unassigned')).toBe('Unassigned');
	});
});
