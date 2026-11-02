import { describe, it, expect } from 'vitest';
import {
	BOARD_COLUMNS,
	accountKickoffSummary,
	accountParties,
	actionOwner,
	boardColumn,
	inTile,
	jisaChildName,
	nextHint,
	owner,
	tileInForce,
	toggleTile,
	ME,
	roleLabel,
	stageOfStep,
	stages,
	presentedStatus,
	stateLabel,
	stateTone,
	statusLabel,
	activityEntries,
	progress
} from './applications.js';
import type { AppCase, AppExecutionRow } from './applications.js';

const listingRow = (over: Partial<AppExecutionRow> = {}): AppExecutionRow => ({
	uid: 'j1',
	status: 'active',
	created_at: '2026-08-24T10:00:00Z',
	application_status: 'fact_find',
	flow_name: 'Onboarding',
	adviser_name: 'Alex Morgan',
	clients: 1,
	accounts: 1,
	steps_total: 10,
	steps_done: 2,
	client_names: 'Harriet Vale',
	open_client_steps: 0,
	...over
});

describe('roleLabel', () => {
	it('names every account role, the JISA registered contact included', () => {
		// The badge read `registered_contact` beside a "Owner" on the same person: the map had no
		// entry for 0155's role and the fallback printed the key.
		expect(roleLabel('registered_contact')).toBe('Registered contact');
		expect(roleLabel('decision_maker')).toBe('Decision maker');
		expect(roleLabel('owner')).toBe('Owner');
	});

	it('sentence-cases a role it does not know instead of printing the key', () => {
		expect(roleLabel('power_of_attorney')).toBe('Power of attorney');
	});
});

describe('accountParties', () => {
	it('groups the names under their role, owner first, pluralising the heading', () => {
		expect(
			accountParties({
				account_parties: [
					{ name: 'Lisa Pavey', role: 'registered_contact' },
					{ name: 'John Pavey', role: 'owner' },
					{ name: 'Lisa Pavey', role: 'owner' }
				]
			} as AppCase)
		).toEqual([
			{ role: 'owner', label: 'Owners', names: 'John Pavey & Lisa Pavey' },
			{ role: 'registered_contact', label: 'Registered contact', names: 'Lisa Pavey' }
		]);
	});

	it('is empty when the account names nobody, and skips a party with no name', () => {
		expect(accountParties({} as AppCase)).toEqual([]);
		expect(accountParties({ account_parties: [{ name: '  ', role: 'owner' }] } as AppCase)).toEqual(
			[]
		);
	});

	it('keeps a role the vocabulary has not caught up with, sorted last', () => {
		expect(
			accountParties({
				account_parties: [
					{ name: 'Ada Reeve', role: 'power_of_attorney' },
					{ name: 'Tom Vale', role: 'owner' }
				]
			} as AppCase)
		).toEqual([
			{ role: 'owner', label: 'Owner', names: 'Tom Vale' },
			{ role: 'power_of_attorney', label: 'Power of attorney', names: 'Ada Reeve' }
		]);
	});
});

describe('actionOwner', () => {
	it('names the responsible adviser rather than saying “You”', () => {
		expect(actionOwner(listingRow())).toBe('Alex Morgan');
	});

	// FB-20260923-05: a journey assigned to a paraplanner still read as waiting on the adviser.
	it('names the assignee ahead of the adviser when the move is the firm’s', () => {
		expect(actionOwner(listingRow({ assignee_name: 'Priya Nair' }))).toBe('Priya Nair');
		expect(actionOwner(listingRow({ assignee_name: 'Priya Nair', status: 'blocked' }))).toBe(
			'Priya Nair'
		);
		expect(actionOwner(listingRow({ assignee_name: 'Priya Nair', open_client_steps: 1 }))).toBe(
			'Client'
		);
	});

	it('says Me, in the reader’s own word, when the staff move is theirs', () => {
		expect(actionOwner(listingRow({ owner_is_me: true }))).toBe(ME);
		expect(actionOwner(listingRow({ assignee_name: 'Priya Nair', owner_is_me: true }))).toBe(ME);
		// Only when the move IS staff's: the flag never overrides the client or Graphene.
		expect(actionOwner(listingRow({ owner_is_me: true, open_client_steps: 1 }))).toBe('Client');
		expect(actionOwner(listingRow({ owner_is_me: true, status: 'completed' }))).toBe('—');
	});

	it('cuts the list by tile the way the tiles count it', () => {
		const open = listingRow();
		const mine = listingRow({ owner_is_me: true });
		const done = listingRow({ status: 'completed', application_status: 'completed' });
		const stopped = listingRow({ status: 'cancelled' });
		const client = listingRow({ open_client_steps: 1 });
		const all = [open, mine, done, stopped, client];
		expect(all.filter((r) => inTile(r, 'in-flight'))).toEqual([open, mine, client]);
		expect(all.filter((r) => inTile(r, 'need-you'))).toEqual([mine]);
		expect(all.filter((r) => inTile(r, 'with-client'))).toEqual([client]);
		expect(all.filter((r) => inTile(r, 'completed'))).toEqual([done]);
	});

	it('toggles a tile as the filter bar’s own selection, keeping the other filters', () => {
		const start = toggleTile({}, 'in-flight');
		expect(start).toEqual({ status: ['draft', 'active', 'waiting', 'failed'] });
		expect(tileInForce(start)).toBe('in-flight');
		// Another tile replaces the tile keys; a filter that is no tile's survives.
		const withStage = { ...start, stage: ['Applicant details'] };
		expect(toggleTile(withStage, 'need-you')).toEqual({
			stage: ['Applicant details'],
			owner: [ME]
		});
		expect(tileInForce(toggleTile(withStage, 'need-you'))).toBe('need-you');
		// The tile in force, clicked again, lifts its own keys and nothing else.
		expect(toggleTile(withStage, 'in-flight')).toEqual({ stage: ['Applicant details'] });
		// A hand-picked status is nobody's tile, and so is a mix of tile keys.
		expect(tileInForce({ status: ['draft'] })).toBeNull();
		expect(tileInForce({ status: ['completed'], owner: [ME] })).toBeNull();
		expect(tileInForce({})).toBeNull();
	});

	it('says Client or Clients from the actual open client-step count', () => {
		expect(actionOwner(listingRow({ open_client_steps: 1 }))).toBe('Client');
		expect(actionOwner(listingRow({ clients: 2, open_client_steps: 1 }))).toBe('Clients');
	});

	it('assigns Graphene’s review stages to Graphene', () => {
		for (const application_status of [
			'identity_under_review',
			'aml_under_review',
			'offshore_under_review'
		]) {
			expect(actionOwner(listingRow({ application_status }))).toBe('Graphene');
		}
	});

	it('puts a blocked or failed journey back with the named adviser', () => {
		expect(
			actionOwner(
				listingRow({
					status: 'blocked',
					application_status: 'aml_under_review',
					open_client_steps: 1
				})
			)
		).toBe('Alex Morgan');
	});

	it('has no action owner after the journey ends', () => {
		expect(actionOwner(listingRow({ status: 'completed' }))).toBe('—');
		expect(actionOwner(listingRow({ status: 'cancelled' }))).toBe('—');
	});
});

describe('stages', () => {
	it('marks the reached stage current and earlier ones done', () => {
		const s = stages('aml_under_review', false);
		expect(s.map((x) => x.label)).toEqual([
			'Draft',
			'Applicant Details',
			'Terms',
			'Identity',
			'AML',
			'Account setup',
			'Completed'
		]);
		expect(s.map((x) => x.state)).toEqual([
			'done',
			'done',
			'done',
			'done',
			'current',
			'todo',
			'todo'
		]);
	});

	it('runs strictly forward — the checks come AFTER terms, as the flow does', () => {
		// The legacy order, restored once step ordering could cross tiers: the client consents, then
		// identity and AML run on what they gave us. `with_client` used to stand at both the
		// fact-find and the terms position, so no linear stepper could draw either honestly.
		const s = stages('terms', false);
		expect(s.find((x) => x.label === 'Applicant Details')?.state).toBe('done');
		expect(s.find((x) => x.label === 'Terms')?.state).toBe('current');
		expect(s.find((x) => x.label === 'Identity')?.state).toBe('todo');
		expect(s.find((x) => x.label === 'AML')?.state).toBe('todo');
	});

	it('reads a legacy with_client row as the fact-find stage it named', () => {
		expect(stages('with_client', false).find((x) => x.label === 'Applicant Details')?.state).toBe(
			'current'
		);
	});

	it('hides the offshore stage unless the application is offshore', () => {
		expect(stages('draft', false).map((s) => s.label)).not.toContain('Offshore');
		expect(stages('draft', true).map((s) => s.label)).toContain('Offshore');
	});

	it('never ticks a stage for a STOPPED journey', () => {
		// The regression: 'cancelled' mapped EVERY stage to done, so an abandoned journey rendered a
		// fully-ticked stepper through 'Completed' directly above the "this journey was stopped" alert.
		for (const s of stages('cancelled', false)) expect(s.state).toBe('todo');
		for (const s of stages('with_client', false, 'cancelled')) expect(s.state).toBe('todo');
	});

	it('ticks the Completed stage itself once the journey is finished', () => {
		// The regression: every stage was 'done' BEFORE the current one and the current one was
		// always 'current', so the terminal stage — the only one that is an outcome rather than work
		// in hand — never reached 'done'. The stepper draws a tick for 'done' and the step NUMBER
		// otherwise, so a journey reading 100% showed a bare "7" in its last circle.
		const s = stages('completed', false);
		expect(s.find((x) => x.label === 'Completed')?.state).toBe('done');
		expect(s.every((x) => x.state === 'done')).toBe(true);
		// The offshore variant has one more stage, so the terminal one is found by flag, not index.
		expect(stages('completed', true).find((x) => x.label === 'Completed')?.state).toBe('done');
		expect(stages('completed', true).every((x) => x.state === 'done')).toBe(true);
	});

	it('still leaves a stopped journey untouched, terminal stage included', () => {
		// The terminal rule must not reach the cancelled branch: an abandoned journey arrived at no
		// stage at all, so 'Completed' being reachable-as-done here would re-draw the full tick run
		// the stopped-journey fix removed.
		expect(stages('cancelled', false).find((x) => x.label === 'Completed')?.state).toBe('todo');
	});

	it('keeps a failed account sync on its own stage rather than ticking everything', () => {
		const s = stages('sync_failed', false);
		expect(s.find((x) => x.label === 'Account setup')?.state).toBe('current');
		expect(s.find((x) => x.label === 'Completed')?.state).toBe('todo');
	});
});

describe('boardColumn', () => {
	it('files a stopped journey in its own column, not "Completed"', () => {
		expect(boardColumn('cancelled')).toBe('stopped');
		expect(boardColumn('with_client', 'cancelled')).toBe('stopped');
		expect(boardColumn('completed')).toBe('done');
		expect(BOARD_COLUMNS.map((c) => c.key)).toContain('stopped');
	});

	it('maps the review statuses together and defaults to draft', () => {
		expect(boardColumn('identity_under_review')).toBe('review');
		expect(boardColumn('aml_under_review')).toBe('review');
		expect(boardColumn('sync_failed')).toBe('setup');
		expect(boardColumn(null)).toBe('draft');
	});

	it('every column key is reachable', () => {
		const reached = new Set(
			[
				'draft',
				'fact_find',
				'identity_under_review',
				'terms',
				'processing',
				'completed',
				'cancelled'
			].map((s) => boardColumn(s))
		);
		expect([...reached].sort()).toEqual(BOARD_COLUMNS.map((c) => c.key).sort());
	});

	it('columns are PHASES, so no column is named for a person', () => {
		// The contradiction this replaces: the column said "With client" while every card inside it
		// said "Blocked — needs you", because a column named for an actor was computed from a phase.
		// Whose ball it is belongs to `owner()` alone.
		for (const c of BOARD_COLUMNS) {
			expect(c.label, c.key).not.toMatch(/client|you|adviser/i);
		}
	});

	it('files a legacy with_client row under Fact-find', () => {
		expect(boardColumn('with_client')).toBe('fact_find');
	});
});

describe('owner / nextHint', () => {
	it('says the adviser is needed when the ENGINE is blocked, whatever the projection says', () => {
		// A blocked execution reading "In review" told the adviser nothing was needed of them.
		expect(nextHint('identity_under_review', 'blocked')).toMatch(/needs you/i);
		expect(nextHint('with_client', 'failed')).toMatch(/needs you/i);
		expect(owner('with_client', 'failed').you).toBe(true);
		// `blocked` was asserted for nextHint but NOT for owner, which is exactly how the two drifted:
		// the card read "Blocked — needs you" while the chip said Client and the board's "Need you"
		// count excluded it, so an adviser with three Fill-in steps waiting was told 0 needed them.
		expect(owner('with_client', 'blocked')).toMatchObject({ label: 'You', you: true });
		expect(owner('identity_under_review', 'blocked').you).toBe(true);
	});

	it('keeps owner and nextHint agreeing on who is needed', () => {
		// The invariant the divergence broke: if the hint says "needs you", the chip must say You.
		for (const status of ['draft', 'with_client', 'identity_under_review', 'processing']) {
			for (const engine of [null, 'blocked', 'failed', 'running']) {
				const hint = nextHint(status, engine);
				if (/needs you/i.test(hint)) {
					expect(owner(status, engine).you, `${status}/${engine}`).toBe(true);
				}
			}
		}
	});

	it('reports a cancelled journey as stopped', () => {
		expect(owner('fact_find', 'cancelled')).toMatchObject({ label: 'Stopped', you: false });
		expect(nextHint('fact_find', 'cancelled')).toBe('Stopped');
	});

	it('leaves the ordinary projection alone', () => {
		expect(owner('terms').label).toBe('Client');
		expect(owner('offshore_under_review').label).toBe('Compliance');
		expect(owner('completed').label).toBe('Done');
		expect(nextHint('processing')).toBe('Open the account');
	});

	it('counts whose ball it is rather than inferring it from the phase', () => {
		// The bug in one line: the seeded onboarding assigns every fact-find to STAFF, so inferring
		// "fact-find ⇒ the client owes us" reported the adviser's own first task as the client's.
		expect(owner('fact_find', 'active', 0)).toMatchObject({ label: 'You', you: true });
		expect(owner('fact_find', 'active', 3)).toMatchObject({ label: 'Client', you: false });
		expect(nextHint('fact_find', 'active', 0)).toBe('Complete the applicant details');
		expect(nextHint('fact_find', 'active', 3)).toMatch(/with the client/i);
	});

	it('a settled journey is never reported as owing anyone anything', () => {
		for (const clientOpen of [undefined, 0, 5]) {
			expect(owner('completed', 'completed', clientOpen)).toMatchObject({
				label: 'Done',
				you: false
			});
		}
	});
});

describe('progress', () => {
	it('ignores skipped and cancelled steps', () => {
		const cases = [
			{
				steps: [
					{ status: 'fulfilled' },
					{ status: 'pending' },
					{ status: 'skipped' },
					{ status: 'cancelled' }
				]
			}
		] as unknown as AppCase[];
		expect(progress(cases)).toEqual({ done: 1, total: 2, pct: 50 });
	});

	it('is 0% rather than NaN with nothing to do', () => {
		expect(progress([])).toEqual({ done: 0, total: 0, pct: 0 });
	});
});

describe('statusLabel', () => {
	it('never shows the adviser a machine value', () => {
		// The journey header rendered `crm.applications.status` verbatim: "with_client".
		expect(statusLabel('with_client')).toBe('Applicant Details');
		expect(statusLabel('identity_under_review')).toBe('Identity');
		expect(statusLabel('terms')).toBe('Terms');
		expect(statusLabel('sync_failed')).toBe('Account setup failed');
		expect(statusLabel('cancelled')).toBe('Stopped');
	});

	it('de-slugs anything it does not know rather than leaking underscores', () => {
		expect(statusLabel('some_future_state')).toBe('Some future state');
		expect(statusLabel(null)).toBe('');
	});
});

describe('stateLabel', () => {
	it('names an active journey by the work underway, not an ominous account state', () => {
		expect(stateLabel('active')).toBe('In progress');
	});

	it('reads a blocked journey as in progress — the engine is waiting, the work is not stuck', () => {
		expect(stateLabel('blocked')).toBe('In progress');
		expect(stateTone('blocked')).toBe('info');
		expect(presentedStatus('blocked')).toBe('active');
		expect(presentedStatus('waiting')).toBe('waiting');
	});
});

describe('accountKickoffSummary', () => {
	it('joins the kickoff catalogue the adviser captured', () => {
		expect(
			accountKickoffSummary({
				product_name: 'Junior ISA',
				product_type: 'JISA',
				currency: 'GBP',
				model_name: 'Balanced',
				fee_package_names: ['Standard', 'Platform charge'],
				investment_capacity: 'advisory',
				dividend_treatment: 'reinvest',
				jisa_child: { name: 'Sam Junior', dob: '2018-06-15' }
			} as AppCase)
		).toBe(
			'Junior ISA · GBP · Balanced · Standard, Platform charge · Advisory · Reinvest · Sam Junior'
		);
	});

	it('names a Cash dividend choice, not just Reinvest', () => {
		expect(
			accountKickoffSummary({
				product_type: 'GIA',
				currency: 'GBP',
				dividend_treatment: 'cash'
			} as AppCase)
		).toBe('GIA · GBP · Cash');
	});

	it('falls back to product_type when the product has no display name', () => {
		expect(accountKickoffSummary({ product_type: 'ISA', currency: 'GBP' } as AppCase)).toBe(
			'ISA · GBP'
		);
	});

	it('is empty when nothing was set at kickoff', () => {
		expect(accountKickoffSummary({} as AppCase)).toBe('');
	});

	it('appends a funding phrase when regular_contribution intent is set', () => {
		expect(
			accountKickoffSummary({
				product_type: 'SIPP',
				currency: 'GBP',
				regular_contribution: {
					intent: true,
					amount: 1000,
					frequency: 'monthly',
					expected_by: null
				}
			} as AppCase)
		).toBe('SIPP · GBP · Monthly £1,000.00');
	});
});

describe('jisaChildName', () => {
	it('reads the name off accounts.data.jisa_child', () => {
		expect(jisaChildName({ jisa_child: { name: '  Sam  ' } } as AppCase)).toBe('Sam');
		expect(jisaChildName({ jisa_child: { dob: '2018-01-01' } } as AppCase)).toBeNull();
		expect(jisaChildName({} as AppCase)).toBeNull();
	});
});

describe('stageOfStep', () => {
	it('names the stage for every step type the seeded onboarding flow uses', () => {
		// Keyed on the TYPE, not the step id: the stage vocabulary must not know one flow's ids, and
		// five of these ARE the projection's own gates read backwards (app/flows/projection.py).
		expect(stageOfStep('fill_form')).toBe('fact_find');
		expect(stageOfStep('task')).toBe('fact_find');
		expect(stageOfStep('accept_terms')).toBe('terms');
		expect(stageOfStep('verify_identity')).toBe('identity_under_review');
		expect(stageOfStep('aml_check')).toBe('aml_under_review');
		expect(stageOfStep('review')).toBe('offshore_under_review');
		expect(stageOfStep('setup_account')).toBe('processing');
	});

	it('refuses to guess for a type the vocabulary cannot place', () => {
		// A guess would file work under a stage it may have nothing to do with; the caller groups
		// these on their own instead.
		expect(stageOfStep('generate_document')).toBeNull();
		expect(stageOfStep('upload_document')).toBeNull();
		expect(stageOfStep('something_a_firm_authored')).toBeNull();
	});

	it('every mapped stage is a real stage of the journey', () => {
		// The timeline groups by these keys, so one naming a stage `stages()` never emits would
		// silently drop its work into the "other" bucket.
		const keys = new Set(stages('draft', true).map((s) => s.key));
		for (const type of [
			'fill_form',
			'task',
			'accept_terms',
			'verify_identity',
			'aml_check',
			'review',
			'setup_account'
		]) {
			expect(keys).toContain(stageOfStep(type));
		}
	});
});

describe('activityEntries', () => {
	const cases = [
		{
			subject_type: 'contact',
			subject_id: 1,
			subject_name: 'Harriet Vale',
			steps: [
				{
					step_key: 'core',
					title: 'Applicant details',
					participant_subject_id: 1,
					participant_name: 'Harriet Vale'
				},
				{
					step_key: 'core',
					title: 'Applicant details',
					participant_subject_id: 2,
					participant_name: 'Jane Vale'
				},
				{
					step_key: 'declaration',
					title: 'Declare and send to the client',
					participant_subject_id: null,
					participant_name: null
				}
			]
		},
		{
			subject_type: 'account',
			subject_id: 9,
			subject_name: null,
			steps: [
				{
					step_key: 'account',
					title: 'Account details',
					participant_subject_id: null,
					participant_name: null
				}
			]
		}
	];

	const event = (
		id: number,
		event_type: string,
		payload: Record<string, unknown> = {},
		created_at = `2026-09-25T18:${String(id).padStart(2, '0')}:00Z`
	) => ({ id, event_type, payload, created_at });

	it('collapses a repeated step and names the people', () => {
		const lines = activityEntries(
			[
				event(4, 'step.completed', { step_key: 'core', participant: 1 }),
				event(3, 'step.completed', { step_key: 'core', participant: 2 }),
				event(1, 'execution.started')
			],
			cases
		);
		expect(lines.map((line) => [line.title, line.actor, line.subject])).toEqual([
			['Applicant details', null, 'Harriet Vale and Jane Vale'],
			['Journey started', null, null]
		]);
		expect(lines[1]?.kind).toBe('started');
		expect(lines).toHaveLength(2);
	});

	it('counts repeats it cannot attribute', () => {
		const lines = activityEntries(
			[
				event(4, 'step.completed', { step_key: 'account' }),
				event(3, 'step.completed', { step_key: 'account' }),
				event(2, 'step.completed', { step_key: 'account' }),
				event(1, 'step.completed', { step_key: 'account' })
			],
			cases
		);
		expect(lines).toHaveLength(1);
		expect(lines[0]?.title).toBe('Account details');
		expect(lines[0]?.subject).toBe('4 times');
	});

	it('says the declaration counters in words and drops the zeroes', () => {
		const lines = activityEntries(
			[
				event(2, 'step.completed', {
					step_key: 'declaration',
					reason: 'invited=2 skipped=0 failed=0'
				})
			],
			cases
		);
		expect(lines[0]?.title).toBe('Declare and send to the client');
		expect(lines[0]?.note).toBe('2 clients invited');
		expect(lines[0]?.note).not.toContain('invited=');
	});

	it('drops an engine slug and keeps a note a person wrote', () => {
		const passed = activityEntries(
			[
				event(2, 'step.completed', {
					step_key: 'account',
					reason: 'account_activated'
				}),
				event(1, 'step.completed', { step_key: 'core', participant: 1, reason: 'review_passed' })
			],
			cases
		);
		expect(passed.map((line) => [line.title, line.actor, line.subject, line.note])).toEqual([
			['Account details', null, null, null],
			['Applicant details', null, 'Harriet Vale', null]
		]);
		const invites = activityEntries(
			[
				event(1, 'step.completed', {
					step_key: 'declaration',
					reason: 'invited=1 skipped=2 failed=1'
				})
			],
			cases
		);
		expect(invites[0]?.note).toBe('1 client invited, 2 had no email, 1 invitation failed');
		const noted = activityEntries(
			[
				event(1, 'step.completed', {
					step_key: 'core',
					participant: 1,
					note: 'Checked the passport'
				})
			],
			cases
		);
		expect(noted[0]?.subject).toBe('Harriet Vale');
		expect(noted[0]?.note).toBe('Checked the passport');
	});

	it('names who completed the step, and does not merge two different people', () => {
		const lines = activityEntries(
			[
				event(2, 'step.completed', {
					step_key: 'core',
					participant: 1,
					actor_name: 'Gio Matonti'
				}),
				event(1, 'step.completed', {
					step_key: 'core',
					participant: 2,
					actor_name: 'Jane Vale'
				})
			],
			cases
		);
		expect(lines.map((line) => [line.actor, line.subject])).toEqual([
			['Gio Matonti', 'Harriet Vale'],
			['Jane Vale', 'Jane Vale']
		]);
	});
});
