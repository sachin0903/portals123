import { describe, expect, it } from 'vitest';
import {
	hidesSettled,
	isBankReview,
	isPending,
	matchingReviews,
	REVIEW_SCOPE_LABELS,
	REVIEW_STATUS_ALL,
	REVIEW_STATUS_LABELS,
	REVIEW_STATUS_TONES,
	REVIEW_STATUS_UNSET_LABEL,
	reviewStatusOptions,
	visibleReviews,
	reviewBankProposed,
	reviewBankRows,
	reviewCaseRef,
	reviewChangeLabel,
	reviewAccount,
	reviewChangeTitle,
	reviewChangeView,
	reviewFieldDiff,
	reviewActionOptions,
	reviewDetailsDiff,
	reviewFeePackagesDiff,
	reviewInputFacts,
	reviewVulnerabilityDiff,
	reviewSubject,
	reviewSubmitters
} from './reviews';
import { reasonKeys, type ReviewPolicy, type ReviewRow } from './types';

const row = (over: Partial<ReviewRow> = {}): ReviewRow => ({
	uid: 'rev_1',
	case_no: 12,
	action_key: 'contact.bank_details_changed',
	label: "Change a client's bank details",
	summary: 'Sort code 12-34-56',
	before_label: 'No bank details',
	after_label: '12-34-56 · ****5678',
	event_type: 'client.bank_account_updated',
	subject_type: 'contact',
	subject_id: 'abc123def456',
	contact_uid: 'abc123def456',
	contact_name: 'Mr Max Oakden',
	account_uid: null,
	account_name: null,
	justification: 'Client moved banks',
	reviewer_role_id: 1,
	reviewer_role_slug: 'compliance',
	reviewer_role_name: 'Compliance',
	proposed_by: 'user-1',
	proposed_by_name: 'Ada Advisor',
	proposed_at: '2026-09-22T10:00:00Z',
	status: 'pending',
	decided_by: null,
	decided_by_name: null,
	decided_at: null,
	decision_note: null,
	applied_at: null,
	is_mine: false,
	can_decide: true,
	...over
});

describe('review vocabulary', () => {
	it('speaks a case number as RV-n, never the nanoid', () => {
		expect(reviewCaseRef(12)).toBe('RV-12');
		expect(reviewCaseRef(1)).toBe('RV-1');
		expect(reviewCaseRef(null)).toBe('RV-—');
	});

	it('names the three inbox scopes in sentences a reviewer can act from', () => {
		expect(REVIEW_SCOPE_LABELS.inbox).toBe('Awaiting my review');
		expect(REVIEW_SCOPE_LABELS.mine).toBe('Submitted by me');
		expect(REVIEW_SCOPE_LABELS.all).toBe('All');
	});

	it('treats only pending as open work', () => {
		expect(isPending(row())).toBe(true);
		expect(isPending(row({ status: 'approved' }))).toBe(false);
		expect(isPending(row({ status: 'conflicted' }))).toBe(false);
	});

	it('names the client, and falls back to the subject kind', () => {
		expect(reviewSubject(row())).toBe('Mr Max Oakden');
		expect(reviewSubject(row({ contact_name: null, subject_type: 'account' }))).toBe('Account');
		expect(reviewSubject(row({ contact_name: '  ', subject_type: 'contact' }))).toBe('Client');
	});

	it('names the account, and uses the uid when the account has no name', () => {
		expect(reviewAccount(row({ account_name: 'Oakden SIPP', account_uid: 'acc_sipp' }))).toBe(
			'Oakden SIPP'
		);
		expect(reviewAccount(row({ account_name: null, account_uid: 'acc_sipp' }))).toBe('acc_sipp');
		expect(reviewAccount(row({ account_name: null, account_uid: null }))).toBeNull();
	});

	it('prints every status as a word, never a slug', () => {
		expect(REVIEW_STATUS_LABELS.pending).toBe('Waiting');
		expect(REVIEW_STATUS_LABELS.conflicted).toBe('Conflicted');
	});

	it('gives each status a semantic tone, never a secondary badge', () => {
		expect(REVIEW_STATUS_TONES.pending).toBe('warning');
		expect(REVIEW_STATUS_TONES.approved).toBe('success');
		expect(REVIEW_STATUS_TONES.rejected).toBe('danger');
	});

	it('prints proposed bank digits in full', () => {
		const bank = {
			name: 'Cleo Client',
			sort_code: '20-00-00',
			account_number: '12345678'
		};
		expect(isBankReview(row())).toBe(true);
		expect(reviewBankProposed(row({ payload: { bank_account: bank } }))).toEqual(bank);
		expect(reviewBankProposed(row())).toBeNull();
		expect(reviewBankRows(bank)).toEqual([
			['Account name', 'Cleo Client'],
			['Sort code', '20 - 00 - 00'],
			['Account number', '12345678'],
			['Payment method', 'BACS']
		]);
		expect(
			reviewBankProposed(
				row({
					payload: {
						contact_id: 1,
						name: 'Cleo Client',
						sort_code: '20-00-00',
						account_number: '12345678'
					}
				})
			)
		).toMatchObject({ sort_code: '20-00-00', account_number: '12345678' });
	});

	it('does not print None as a change label', () => {
		expect(reviewChangeLabel('None')).toBeNull();
		expect(reviewChangeLabel('No bank details')).toBeNull();
		expect(reviewChangeLabel('20-00-00 12345678')).toBe('20-00-00 12345678');
	});

	it('lists each submitter once, named, for the filter bar', () => {
		expect(
			reviewSubmitters([
				row({ proposed_by: 'user-2', proposed_by_name: 'Zed Compliance' }),
				row({ proposed_by: 'user-1', proposed_by_name: 'Ada Advisor' }),
				row({ proposed_by: 'user-1', proposed_by_name: 'Ada Advisor' })
			])
		).toEqual([
			{ value: 'user-1', label: 'Ada Advisor' },
			{ value: 'user-2', label: 'Zed Compliance' }
		]);
	});

	it('shows only the queue until the status filter asks for settled reviews', () => {
		const waiting = row({ uid: 'w' });
		const approved = row({ uid: 'a', status: 'approved' });
		const rejected = row({ uid: 'r', status: 'rejected' });
		const cancelled = row({ uid: 'c', status: 'cancelled' });
		const conflicted = row({ uid: 'x', status: 'conflicted' });
		const all = [waiting, approved, rejected, cancelled, conflicted];
		const unset = { submitted_by: [], action_key: '', status: '' };
		expect(matchingReviews(all, unset)).toEqual([waiting]);
		expect(matchingReviews(all, { ...unset, status: REVIEW_STATUS_ALL })).toEqual(all);
		expect(matchingReviews(all, { ...unset, status: 'approved' })).toEqual([approved]);
		expect(matchingReviews(all, { ...unset, status: 'cancelled' })).toEqual([cancelled]);
		expect(hidesSettled([waiting, cancelled], unset)).toBe(true);
		expect(hidesSettled([waiting], unset)).toBe(false);
		expect(hidesSettled([waiting, approved], { status: REVIEW_STATUS_ALL })).toBe(false);
		// The resting row IS the queue and says so; Waiting is not repeated as a choice, and All
		// statuses is a real choice on the list.
		expect(REVIEW_STATUS_UNSET_LABEL).toBe('Waiting for a decision');
		expect(reviewStatusOptions().at(-1)).toEqual({ value: 'all', label: 'All statuses' });
		expect(reviewStatusOptions().map((o) => o.value)).toEqual([
			'approved',
			'rejected',
			'cancelled',
			'conflicted',
			'all'
		]);
	});

	it('still shows the case a deep link opened when the filters would hide it', () => {
		const waiting = row({ uid: 'w' });
		const approved = row({ uid: 'a', status: 'approved' });
		const unset = { submitted_by: [], action_key: '', status: '' };
		expect(visibleReviews([waiting, approved], unset, 'a')).toEqual([approved, waiting]);
		expect(visibleReviews([waiting, approved], unset, 'w')).toEqual([waiting]);
		expect(visibleReviews([waiting, approved], unset, null)).toEqual([waiting]);
		expect(visibleReviews([waiting, approved], unset, 'missing')).toEqual([waiting]);
	});

	it('shows every waiting review until a submitter filter is set', () => {
		const ada = row({ uid: 'a', proposed_by: 'user-1', proposed_by_name: 'Ada Advisor' });
		const zed = row({
			uid: 'z',
			proposed_by: 'user-2',
			proposed_by_name: 'Zed Compliance'
		});
		expect(matchingReviews([ada, zed], { submitted_by: [], action_key: '', status: '' })).toEqual([
			ada,
			zed
		]);
		expect(
			matchingReviews([ada, zed], { submitted_by: ['user-1'], action_key: '', status: '' })
		).toEqual([ada]);
		expect(
			matchingReviews([ada, zed], { submitted_by: [], action_key: '', status: '', q: 'zed' })
		).toEqual([zed]);
		expect(
			matchingReviews([ada, zed], { submitted_by: [], action_key: '', status: '', q: 'RV-12' })
		).toEqual([ada, zed]);
		expect(
			matchingReviews([ada, row({ uid: 'other', case_no: 47, contact_name: 'Other' })], {
				submitted_by: [],
				action_key: '',
				status: '',
				q: '47'
			})
		).toHaveLength(1);
		expect(
			matchingReviews([ada, row({ uid: 'sipp', account_name: 'Oakden SIPP' })], {
				submitted_by: [],
				action_key: '',
				status: '',
				q: 'sipp'
			})
		).toEqual([row({ uid: 'sipp', account_name: 'Oakden SIPP' })]);
	});

	it('builds a field-level now vs proposed for a bank change', () => {
		const diff = reviewFieldDiff(
			row({
				payload: {
					name: 'Cleo Client',
					sort_code: '20-00-00',
					account_number: '12345678'
				},
				before: { value: null }
			})
		);
		expect(diff.find((line) => line.field === 'Account number')).toEqual({
			field: 'Account number',
			now: '—',
			proposed: '12345678'
		});
	});

	it('names a model change as the two mandates, not a generic label', () => {
		const model = row({
			action_key: 'account.model_changed',
			label: "Change an account's model mandate",
			summary: 'Change the model mandate',
			before_label: 'Cautious',
			after_label: 'Balanced',
			subject_type: 'account',
			account_uid: 'acc_sipp',
			account_name: 'Oakden SIPP'
		});
		expect(reviewChangeTitle(model)).toBe('Cautious → Balanced');
		const view = reviewChangeView(model);
		expect(view.kind).toBe('diff');
		if (view.kind !== 'diff') throw new Error('expected diff');
		expect(view.rows).toEqual([{ field: 'Model', now: 'Cautious', proposed: 'Balanced' }]);
	});
});

const policy = (over: Partial<ReviewPolicy> = {}): ReviewPolicy => ({
	action_key: 'client.bank',
	label: 'Bank',
	description: '',
	family: 'client',
	event_type: 'client.bank',
	mode: 'four_eye',
	require_justification: true,
	reviewer_role_id: null,
	reviewer_role_slug: null,
	reviewer_role_name: null,
	...over
});

describe('reason keys', () => {
	it('asks for a reason on a send action the book catalogue does not list', () => {
		expect(reasonKeys([policy()], ['transaction.payment'])).toEqual([
			'client.bank',
			'transaction.payment'
		]);
	});

	it('does not ask just because approval is on', () => {
		expect(reasonKeys([policy({ require_justification: false })], [])).toEqual([]);
	});
});

describe('held provider actions', () => {
	const held = (over: Partial<ReviewRow> = {}) =>
		row({
			action_key: 'transaction.payment',
			label: 'Payment',
			summary: 'Waiting for approval before this is sent.',
			before_label: '',
			after_label: '',
			source: 'action',
			subject_type: 'account',
			account_uid: 'acc_sipp',
			account_name: 'Oakden SIPP',
			justification: '',
			payload: null,
			before: null,
			input: { kind: 'withdrawal', amount: 1000, due_date: '2026-10-30', auto_disinvest: true },
			...over
		});

	it('titles a held action by its name and the figure it is about', () => {
		expect(reviewChangeTitle(held())).toBe('Payment · £1,000.00');
		expect(
			reviewChangeTitle(held({ input: { ceding_provider: 'Aviva', total_value: 25000 } }))
		).toBe('Payment · £25,000.00');
		expect(reviewChangeTitle(held({ input: null }))).toBe('Payment');
	});

	it('shows a held action as the facts its door recorded, never a now vs proposed', () => {
		const view = reviewChangeView(held());
		expect(view.kind).toBe('facts');
		expect(view.title).toBe('Payment · £1,000.00');
		if (view.kind !== 'facts') throw new Error('expected facts');
		expect(view.caption).toBe('Waiting for approval before this is sent.');
		expect(view.rows).toEqual([
			{ label: 'Kind', value: 'Withdrawal' },
			{ label: 'Amount', value: '£1,000.00', money: { amount: 1000 } },
			{ label: 'Due', value: '30 Oct 2026' },
			{ label: 'Auto-disinvest', value: 'Yes' }
		]);
	});

	it('drops the waiting caption once the action is decided', () => {
		const view = reviewChangeView(held({ status: 'approved' }));
		if (view.kind !== 'facts') throw new Error('expected facts');
		expect(view.caption).toBeNull();
	});

	it('reads every line of a transfer form, lists one line per entry, and words the vocabulary', () => {
		const facts = reviewInputFacts({
			client_name: 'Mr Max Oakden',
			ceding_provider: 'Aviva',
			basis: 'in_specie',
			extent: 'full',
			pension_type: 18,
			pension_type_label: 'Self Invested Personal Pension',
			safeguarded_benefits: false,
			crystallised_value: 0,
			total_value: 25000,
			expected_date: '2026-11-01',
			provider_postcode: 'YO1 1AA',
			documents: [{ document_reference_id: 'DOC-9' }, { document_reference_id: 'DOC-10' }],
			note: '',
			something_new: 'kept'
		});
		const byLabel = Object.fromEntries(facts.map((fact) => [fact.label, fact.value]));
		expect(byLabel).toEqual({
			Client: 'Mr Max Oakden',
			'Ceding provider': 'Aviva',
			Basis: 'In specie',
			Extent: 'Full',
			'Pension type code': '18',
			'Pension type': 'Self Invested Personal Pension',
			'Safeguarded benefits': 'No',
			'Crystallised value': '£0.00',
			'Total value': '£25,000.00',
			Expected: '1 Nov 2026',
			Postcode: 'YO1 1AA',
			Documents: 'Document reference id: DOC-9; Document reference id: DOC-10',
			'Something new': 'kept'
		});
		expect(facts.find((fact) => fact.label === 'Total value')?.money).toEqual({ amount: 25000 });
		expect(reviewInputFacts(null)).toEqual([]);
	});

	it('offers the action filter from what is on the queue, named and sorted, once each', () => {
		expect(
			reviewActionOptions([
				held(),
				row({ action_key: 'contact.bank_details_changed', label: 'Update client bank account' }),
				held({ uid: 'p2' }),
				row({
					uid: 'x',
					action_key: 'account.model_changed',
					label: "Change an account's model mandate"
				})
			])
		).toEqual([
			{ value: 'account.model_changed', label: "Change an account's model mandate" },
			{ value: 'transaction.payment', label: 'Payment' },
			{ value: 'contact.bank_details_changed', label: 'Update client bank account' }
		]);
	});
});

describe('book review bodies read like the bank one', () => {
	it("shows a details edit as now vs proposed with the record's labels and formatting", () => {
		const details = row({
			action_key: 'client.details_changed',
			label: 'Edit client details',
			summary: 'Edit client details',
			before_label: 'Current details',
			after_label: 'Change date of birth, first name',
			payload: {
				contact_id: 43,
				fields: {
					first_name: 'Bob',
					dob: '1990-05-05',
					address: { line1: '1 High St', city: 'York', postcode: 'YO1 1AA', country: 'GB' },
					employment_status: 'self_employed',
					nationality: ['GB', 'IE'],
					us_person: true,
					relevant_uk_earnings: 42000,
					fatca_status: 'w9',
					tax_relief: 'uk_relevant_earnings',
					email: null,
					town_of_birth: 'York'
				}
			},
			before: {
				first_name: 'Ada',
				dob: '1980-01-01',
				address: null,
				employment_status: 'employed',
				nationality: ['GB'],
				us_person: false,
				relevant_uk_earnings: null,
				fatca_status: null,
				tax_relief: null,
				email: 'ada@x.io',
				town_of_birth: 'York'
			}
		});
		// the title names what MOVES: the card resent town of birth unchanged
		expect(reviewChangeTitle(details)).toBe(
			'Client details · First name, Date of birth, Address, Employment status, Nationalities, US person, Annual earnings, W-9 or W-8, Tax relief declared, Email'
		);
		const view = reviewChangeView(details);
		expect(view.kind).toBe('diff');
		expect(view.title).toBe('Client details');
		const moved = (field: string, now: string, proposed: string) => ({
			field,
			now,
			proposed,
			changed: true
		});
		expect(reviewDetailsDiff(details)).toEqual([
			moved('First name', 'Ada', 'Bob'),
			moved('Date of birth', '1 Jan 1980', '5 May 1990'),
			moved('Address', '—', '1 High St, York, YO1 1AA, United Kingdom'),
			moved('Employment status', 'Employed', 'Self-employed'),
			moved('Nationalities', 'United Kingdom', 'United Kingdom, Ireland'),
			moved('US person', 'No', 'Yes'),
			moved('Annual earnings', '—', '£42,000.00'),
			moved('W-9 or W-8', '—', 'W-9'),
			moved(
				'Tax relief declared',
				'—',
				'Has relevant UK earnings chargeable to income tax this tax year'
			),
			moved('Email', 'ada@x.io', '—'),
			{ field: 'Town of birth', now: 'York', proposed: 'York', changed: false }
		]);
	});

	it("shows a vulnerability change as the card's rows, now vs proposed", () => {
		const vulnerability = row({
			action_key: 'client.vulnerability_updated',
			label: "Update a client's vulnerability record",
			summary: 'Change the vulnerability record',
			before_label: 'Not vulnerable',
			after_label: 'Vulnerable · 2 reasons',
			payload: {
				contact_id: 43,
				vulnerability: {
					is_vulnerable: true,
					reasons: ['health', 'life_event'],
					characteristics: ['bereavement'],
					is_temporary: true,
					notes: 'Told us on the call'
				}
			},
			before: {
				is_vulnerable: false,
				vulnerability_reasons: null,
				vulnerability_characteristics: null,
				vulnerability_is_temporary: null,
				vulnerability_notes: null
			}
		});
		expect(reviewChangeTitle(vulnerability)).toBe('Vulnerability · Not vulnerable → Vulnerable');
		const rows = reviewVulnerabilityDiff(vulnerability);
		expect(rows.map((r) => r.field)).toEqual([
			'Vulnerable',
			'Drivers',
			'Characteristics',
			'Duration',
			'Notes'
		]);
		expect(rows[0]).toEqual({ field: 'Vulnerable', now: 'No', proposed: 'Yes' });
		expect(rows[1]).toEqual({ field: 'Drivers', now: '—', proposed: 'Health · Life event' });
		expect(rows[3]).toEqual({ field: 'Duration', now: '—', proposed: 'Temporary' });
		expect(rows[4]).toEqual({ field: 'Notes', now: '—', proposed: 'Told us on the call' });
		// no characteristics on either side: the row is left out, not shown as — vs —
		expect(
			reviewVulnerabilityDiff({
				payload: { vulnerability: { is_vulnerable: false } },
				before: { is_vulnerable: true, vulnerability_reasons: ['health'] }
			}).map((r) => r.field)
		).toEqual(['Vulnerable', 'Drivers', 'Duration', 'Notes']);
		expect(reviewChangeView(vulnerability).kind).toBe('diff');
	});

	it('names fee packages on both sides, and counts them when an older hold has no names', () => {
		const fees = row({
			action_key: 'account.fee_packages_changed',
			label: 'Change fee packages',
			summary: 'Change the fee packages',
			before_label: 'Platform fee',
			after_label: 'Platform fee, Adviser fee',
			payload: { account_id: 2, fee_package_ids: [1, 2] },
			before: { fee_package_ids: [1], names: { '1': 'Platform fee', '2': 'Adviser fee' } }
		});
		expect(reviewChangeTitle(fees)).toBe('Fee packages · Platform fee → Platform fee, Adviser fee');
		expect(reviewFeePackagesDiff(fees)).toEqual([
			{ field: 'Fee packages', now: 'Platform fee', proposed: 'Platform fee, Adviser fee' }
		]);
		expect(
			reviewFeePackagesDiff({
				payload: { fee_package_ids: [1, 2] },
				before: { fee_package_ids: [] }
			})
		).toEqual([{ field: 'Fee packages', now: 'None', proposed: '2 packages' }]);
	});

	it('reads a NUMERIC snapshot string as the same figure the proposal carries', () => {
		// `snapshot_contact_fields` JSON-normalises with default=str, so the column arrives as
		// "42000.00"; the card resends 42000 unchanged, and that must not read as a change.
		const rows = reviewDetailsDiff({
			payload: { fields: { relevant_uk_earnings: 42000, retirement_age: 60 } },
			before: { relevant_uk_earnings: '42000.00', retirement_age: 60 }
		});
		expect(rows).toEqual([
			{ field: 'Annual earnings', now: '£42,000.00', proposed: '£42,000.00', changed: false },
			{ field: 'Retirement age', now: '60', proposed: '60', changed: false }
		]);
	});

	it('shows a retired kind on settled history as the facts of the command it carried', () => {
		const retired = row({
			action_key: 'instruction.raised',
			label: 'Raise a money-movement instruction',
			summary: 'Raise a money-movement instruction',
			before_label: '',
			after_label: '',
			status: 'cancelled',
			payload: {
				kind: 'withdrawal',
				account_id: 2,
				amount: 1500,
				due_date: '2026-10-30',
				note: 'As discussed',
				created_by_workos: 'user_x',
				contact_uid: 'con_1',
				justification: 'Client asked'
			}
		});
		const view = reviewChangeView(retired);
		expect(view.kind).toBe('facts');
		if (view.kind !== 'facts') throw new Error('expected facts');
		expect(view.title).toBe('Raise a money-movement instruction');
		expect(view.rows).toEqual([
			{ label: 'Kind', value: 'Withdrawal' },
			{ label: 'Amount', value: '£1,500.00', money: { amount: 1500 } },
			{ label: 'Due', value: '30 Oct 2026' },
			{ label: 'Note', value: 'As discussed' }
		]);
	});

	it('still gives an unrecognised book review a now vs proposed from the describer', () => {
		const other = row({
			action_key: 'account.something_else',
			label: 'Change something',
			summary: 'Change the thing',
			before_label: 'Old',
			after_label: 'New'
		});
		expect(reviewChangeView(other)).toEqual({
			kind: 'diff',
			title: 'Change the thing',
			rows: [{ field: 'Change', now: 'Old', proposed: 'New' }]
		});
	});
});
