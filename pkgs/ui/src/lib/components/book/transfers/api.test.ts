/** The real transfer vocabulary and its display rules.
 *
 * What is worth pinning here is not the arithmetic — it is the claim that this module makes
 * about the providers, because the module it sits beside (`types.ts`) makes a different one.
 * The prototype models a nine-stage cross-custodian saga taken from
 * `0003_pensions_ledger.sql`, a schema superseded by 0051 that never shipped. These tests say
 * out loud which of the two the portal is allowed to render.
 */
import { describe, expect, it } from 'vitest';
import {
	ML_TRANSFER_STATUS,
	ML_TRANSFER_STATUS_LABELS,
	OFF_SAGA,
	PENSION_TYPE_OPTIONS,
	SAGA,
	TRANSFER_CRYSTALLISATION_OPTIONS,
	TRANSFER_STATUS_LABEL,
	TRANSFER_STATUS_TONE,
	ageInDays,
	ageLabel,
	basisLabel,
	crystallisationLabel,
	formSpecFor,
	pensionTypeLabel,
	sagaIndex,
	splitForCrystallisation,
	transferValue,
	type ApiTransfer,
	type ApiTransferStatus
} from './api.js';

const TRANSFER: ApiTransfer = {
	id: 1,
	uid: 'trf_1',
	account_id: 10,
	contact_id: 5,
	expectation_id: 7,
	transaction_id: null,
	ceding_provider: 'Aviva',
	policy_number: 'AV-9912',
	transfer_type: 'pension',
	basis: 'cash',
	extent: 'full',
	crystallised_value: null,
	uncrystallised_value: null,
	total_value: 25_000,
	crystallisation: null,
	pension_type: null,
	safeguarded_benefits: null,
	currency: 'GBP',
	status: 'in_progress',
	ml_status: null,
	ml_status_label: null,
	ml_status_at: null,
	ceding_status: null,
	ml_transfer_id: null,
	ml_recorded_at: null,
	expected_date: null,
	completed_at: null,
	note: null,
	created_at: '2026-06-01T09:00:00Z',
	updated_at: '2026-06-01T09:00:00Z',
	account_name: "Sophie's SIPP",
	account_uid: 'acc_1',
	product_type: 'SIPP',
	client_name: 'Sophie Halloway',
	contact_uid: 'con_5',
	adviser: 'Dan Reeve',
	expectation_uid: 'exp_1',
	expectation_status: 'instructed',
	expectation_due_date: '2026-06-20',
	expectation_amount: 25_000,
	expectation_matched_at: null,
	matched_by_hand: null,
	transaction_amount: null,
	transaction_date: null,
	transaction_source: null,
	transaction_reference: null,
	reconciliation: { status: 'pending', legs: [], variance: null, reason: null }
};

describe('the Morgan Lloyd transfer vocabulary', () => {
	it('is the six values the vendor documents, not the prototype nine', () => {
		expect(Object.keys(ML_TRANSFER_STATUS_LABELS)).toHaveLength(6);
		expect(ML_TRANSFER_STATUS_LABELS[ML_TRANSFER_STATUS.fundsReceived]).toBe('Funds received');
		expect(ML_TRANSFER_STATUS_LABELS[ML_TRANSFER_STATUS.fundsSent]).toBe('Funds sent');
	});

	it('keeps "funds received" and "funds sent" apart', () => {
		// Collapsing them is what made a transfer look stalled while the money was in motion:
		// received is the ceding scheme paying ML, sent is ML paying the custodian.
		expect(ML_TRANSFER_STATUS.fundsReceived).not.toBe(ML_TRANSFER_STATUS.fundsSent);
		expect(SAGA.map((s) => s.status)).toContain('funds_sent');
		expect(SAGA.map((s) => s.status)).toContain('assets_received');
	});
});

describe('the saga', () => {
	it('runs raised → complete in order', () => {
		expect(SAGA.map((s) => s.status)).toEqual([
			'requested',
			'submitted',
			'in_progress',
			'assets_received',
			'funds_sent',
			'completed'
		]);
	});

	it('names an actor for every stage', () => {
		// Half the saga is waiting on somebody else; the actor is the useful signal.
		for (const stage of SAGA) expect(stage.actor).toBeTruthy();
	});

	it('puts the terminals off the rail rather than at the end of it', () => {
		expect(sagaIndex('cancelled')).toBe(-1);
		expect(sagaIndex('manual_intervention')).toBe(-1);
		expect(OFF_SAGA.has('cancelled')).toBe(true);
		expect(OFF_SAGA.has('manual_intervention')).toBe(true);
	});

	it('labels and tones every status the API can return', () => {
		const all: ApiTransferStatus[] = [
			'requested',
			'submitted',
			'in_progress',
			'assets_received',
			'funds_sent',
			'completed',
			'cancelled',
			'manual_intervention'
		];
		for (const status of all) {
			expect(TRANSFER_STATUS_LABEL[status]).toBeTruthy();
			expect(TRANSFER_STATUS_TONE[status]).toBeTruthy();
		}
	});

	it('uses only semantic tokens for tone', () => {
		// token-hygiene: a raw palette class is unreadable on a dark background AND unreachable
		// by a firm's branding.
		for (const tone of Object.values(TRANSFER_STATUS_TONE)) {
			expect(tone).not.toMatch(/\b(?:text|bg|border)-(?:red|green|amber|blue|slate|gray)-\d/);
		}
	});
});

describe('the value shown', () => {
	it('prefers what landed over what was forecast', () => {
		expect(transferValue({ ...TRANSFER, transaction_amount: 24_812.4 })).toBe(24_812.4);
	});

	it('falls back to the booked total before the expectation', () => {
		expect(transferValue(TRANSFER)).toBe(25_000);
	});

	it('is null when nothing has been advised yet', () => {
		// A transfer can be raised before the ceding scheme says what it is worth. Rendering
		// £0.00 there reads as a nil transfer rather than an unknown one.
		expect(transferValue({ ...TRANSFER, total_value: null, expectation_amount: null })).toBeNull();
	});
});

describe('age', () => {
	const now = new Date('2026-06-15T09:00:00Z');

	it('counts whole days since it was raised', () => {
		expect(ageInDays(TRANSFER, now)).toBe(14);
	});

	it.each([
		['2026-06-15T09:00:00Z', 'today'],
		['2026-06-12T09:00:00Z', '3d'],
		['2026-06-01T09:00:00Z', '2w'],
		['2026-01-15T09:00:00Z', '5mo']
	])('shortens %s to %s', (created, expected) => {
		expect(ageLabel({ ...TRANSFER, created_at: created }, now)).toBe(expected);
	});

	it('sorts on days, not the label', () => {
		// "2w" collates before "3d" as a string, which would put a fortnight-old transfer above
		// a three-day-old one in a descending sort.
		const older = { ...TRANSFER, created_at: '2026-06-01T09:00:00Z' };
		const newer = { ...TRANSFER, created_at: '2026-06-12T09:00:00Z' };
		expect(ageInDays(older, now)!).toBeGreaterThan(ageInDays(newer, now)!);
		expect(ageLabel(older, now) < ageLabel(newer, now)).toBe(true);
	});

	it('is null rather than NaN for an unparseable date', () => {
		expect(ageInDays({ ...TRANSFER, created_at: 'not a date' }, now)).toBeNull();
		expect(ageLabel({ ...TRANSFER, created_at: 'not a date' }, now)).toBe('—');
	});
});

describe('basis', () => {
	it('reads in-specie off the basis column', () => {
		expect(basisLabel({ ...TRANSFER, basis: 'in_specie' })).toBe('In-specie');
	});

	it('reads cash off the basis column', () => {
		expect(basisLabel({ ...TRANSFER, basis: 'cash' })).toBe('Cash');
	});

	it('does not treat a wrapper as cash', () => {
		// The old rule: every pension/isa/gia row was labelled Cash. That was the lie.
		expect(basisLabel({ ...TRANSFER, basis: null })).toBe('—');
	});
});

describe('formSpecFor', () => {
	it('asks extent, pension facts and the derived split of an ML-administered pension', () => {
		expect(
			formSpecFor({
				custodian: 'sei',
				scheme_administrator: 'morgan-lloyd',
				is_pension: true
			})
		).toEqual({
			askExtent: true,
			askCrystallisedSplit: true,
			askPensionFacts: true,
			askPensionType: true,
			defaultExtent: null,
			allowCash: true,
			askCedingAddress: false,
			askSeiInstruction: false
		});
	});

	it('treats a Seccl SIPP as Seccl even when the shelf names Morgan Lloyd', () => {
		expect(
			formSpecFor({
				custodian: 'seccl',
				scheme_administrator: 'morgan-lloyd',
				is_pension: true
			})
		).toEqual({
			askExtent: false,
			askCrystallisedSplit: false,
			askPensionFacts: true,
			askPensionType: false,
			defaultExtent: 'full',
			allowCash: true,
			askCedingAddress: true,
			askSeiInstruction: false
		});
	});

	it('hides extent on a Seccl-only account and stores full', () => {
		expect(
			formSpecFor({ custodian: 'seccl', scheme_administrator: null, is_pension: false })
		).toEqual({
			askExtent: false,
			askCrystallisedSplit: false,
			askPensionFacts: false,
			askPensionType: false,
			defaultExtent: 'full',
			allowCash: true,
			askCedingAddress: true,
			askSeiInstruction: false
		});
	});

	it('asks extent but not pension facts of a SEI GIA', () => {
		expect(
			formSpecFor({ custodian: 'sei', scheme_administrator: null, is_pension: false })
		).toEqual({
			askExtent: true,
			askCrystallisedSplit: false,
			askPensionFacts: false,
			askPensionType: false,
			defaultExtent: null,
			allowCash: true,
			askCedingAddress: false,
			askSeiInstruction: true
		});
	});

	it('still asks the split when SEI custodies and Morgan Lloyd administers', () => {
		// An SEI SIPP is the Morgan Lloyd case. Hiding the split because the custodian is SEI
		// would 422 the Morgan Lloyd push.
		expect(
			formSpecFor({
				custodian: 'sei',
				scheme_administrator: 'morgan-lloyd',
				is_pension: true
			}).askCrystallisedSplit
		).toBe(true);
	});

	it('asks pension facts of every pension, not only an ML-administered one', () => {
		const spec = formSpecFor({ custodian: 'sei', scheme_administrator: null, is_pension: true });
		expect(spec.askPensionFacts).toBe(true);
		expect(spec.allowCash).toBe(false);
	});
});

describe('pension transfer vocabulary', () => {
	it('names drawdown status in the three statuses the drawer offers', () => {
		expect(TRANSFER_CRYSTALLISATION_OPTIONS.map((o) => o.label)).toEqual([
			'No Drawdown',
			'Partial Crystallisation',
			'Full Crystallisation'
		]);
		expect(TRANSFER_CRYSTALLISATION_OPTIONS.map((o) => o.value)).toEqual([
			'uncrystallised',
			'partial',
			'crystallised'
		]);
		expect(crystallisationLabel('uncrystallised')).toBe('No Drawdown');
		expect(crystallisationLabel(null)).toBe('—');
	});

	it('names Morgan Lloyd cam_pensiontype 1–20', () => {
		expect(PENSION_TYPE_OPTIONS).toHaveLength(20);
		expect(PENSION_TYPE_OPTIONS[0]).toEqual({
			value: '1',
			label: 'Additional Voluntary Contribution'
		});
		expect(PENSION_TYPE_OPTIONS[17]).toEqual({
			value: '18',
			label: 'Self Invested Personal Pension'
		});
		expect(pensionTypeLabel(18)).toBe('Self Invested Personal Pension');
		expect(pensionTypeLabel(null)).toBe('—');
	});

	it('derives the crystallised split from the status so the total cannot disagree', () => {
		expect(splitForCrystallisation('uncrystallised', 25_000, null)).toEqual({
			crystallised: 0,
			uncrystallised: 25_000
		});
		expect(splitForCrystallisation('crystallised', 25_000, 25_000)).toEqual({
			crystallised: 25_000,
			uncrystallised: 0
		});
		expect(splitForCrystallisation('partial', 25_000, 10_000)).toEqual({
			crystallised: 10_000,
			uncrystallised: 15_000
		});
	});
});
