import { describe, expect, it } from 'vitest';
import type { ApiTransfer } from './api.js';
import {
	cancelStep,
	cancellationRequested,
	nextStep,
	custodianFilterOptions,
	railStages,
	reconciliationColumns,
	statusLabel
} from './stages.js';
import { stepRefusal, transferActionPath } from './step-path.js';

function row(over: Partial<ApiTransfer> = {}): ApiTransfer {
	return {
		id: 1,
		uid: 'trf000000001',
		account_id: 1,
		contact_id: 1,
		contact_uid: 'ctc000000001',
		expectation_id: 1,
		transaction_id: null,
		ceding_provider: 'Aviva',
		policy_number: 'AV-1',
		transfer_type: 'pension',
		basis: 'cash',
		extent: 'full',
		crystallisation: null,
		pension_type: null,
		safeguarded_benefits: null,
		crystallised_value: null,
		uncrystallised_value: null,
		total_value: 1000,
		currency: 'GBP',
		status: 'requested',
		ml_status: null,
		ml_status_label: null,
		ml_status_at: null,
		ceding_status: null,
		ml_transfer_id: null,
		ml_recorded_at: null,
		expected_date: null,
		completed_at: null,
		note: null,
		created_at: '2026-06-01T00:00:00Z',
		updated_at: '2026-06-01T00:00:00Z',
		account_name: 'SIPP',
		account_uid: 'acc000000001',
		client_name: 'Sophie Halloway',
		adviser: null,
		expectation_uid: null,
		expectation_status: 'expected',
		expectation_due_date: null,
		expectation_amount: 1000,
		expectation_matched_at: null,
		matched_by_hand: null,
		transaction_amount: null,
		transaction_date: null,
		transaction_source: null,
		transaction_reference: null,
		reconciliation: { status: 'pending', legs: [], variance: null, reason: null },
		product_type: null,
		...over
	} as ApiTransfer;
}

describe('the next step', () => {
	it('submits a raised Morgan Lloyd transfer and does not lodge SEI yet', () => {
		expect(nextStep(row({ route: 'ml_sei' }))?.id).toBe('submit_ml');
		expect(nextStep(row({ route: 'ml_sei', status: 'funds_sent' }))?.id).toBe('lodge_sei');
		expect(nextStep(row({ route: 'ml_sei', status: 'in_progress' }))?.id).toBe('refresh_ml');
	});

	it('places a Seccl transfer and will not refresh a place that came back without an id', () => {
		expect(nextStep(row({ route: 'seccl' }))?.id).toBe('place_seccl');
		expect(
			nextStep(row({ route: 'seccl', status: 'submitted', seccl_transfer_id: '00009BN1G' }))?.id
		).toBe('refresh_seccl');
		expect(
			nextStep(
				row({
					route: 'seccl',
					status: 'manual_intervention',
					seccl_placed: true,
					provider_error: 'Seccl accepted the transfer but returned no id.'
				})
			)
		).toBeNull();
		expect(cancelStep(row({ route: 'seccl', ceding_status: 'With Previous Provider' }))?.id).toBe(
			'cancel_seccl'
		);
		expect(cancelStep(row({ route: 'seccl', ceding_status: 'Onboarding Assets' }))).toBeNull();
	});

	it('shows a cancellation request as itself, not as with the ceding scheme', () => {
		const pending = row({
			route: 'seccl',
			status: 'submitted',
			ceding_status: 'Pending Cancellation'
		});
		expect(cancellationRequested(pending)).toBe(true);
		expect(statusLabel(pending)).toBe('Cancelling');
		expect(cancelStep(pending)).toBeNull();
		expect(statusLabel(row({ status: 'in_progress', ceding_status: 'Onboarding Assets' }))).toBe(
			'At ceding scheme'
		);
	});

	it('cancels a feed row Seccl still has with the previous provider', () => {
		expect(
			cancelStep(
				row({
					uid: 'detected:7',
					detected: true,
					route: 'seccl',
					status: 'submitted',
					ceding_status: 'With Previous Provider'
				})
			)?.id
		).toBe('cancel_seccl');
		expect(
			cancelStep(
				row({
					uid: 'detected:8',
					detected: true,
					route: 'seccl',
					status: 'submitted',
					ceding_status: null
				})
			)
		).toBeNull();
	});

	it('lodges SEI cash and submits an in-specie transfer in', () => {
		expect(nextStep(row({ route: 'sei' }))?.id).toBe('lodge_sei');
		expect(nextStep(row({ route: 'sei', basis: 'in_specie' }))?.id).toBe('submit_sei');
		expect(
			nextStep(
				row({ route: 'sei', basis: 'in_specie', status: 'submitted', sei_activity_id: 'ACT1' })
			)?.id
		).toBe('poll_sei');
		// A cash-receipt id is not a pending transferIn. Polling it 422s.
		expect(
			nextStep(row({ route: 'sei', status: 'submitted', sei_activity_id: 'ACR1' }))
		).toBeNull();
		expect(nextStep(row({ route: 'sei', status: 'submitted' }))).toBeNull();
		expect(cancelStep(row({ route: 'sei', status: 'submitted' }))).toBeNull();
	});

	it('polls an SEI transfer that already has an activity id', () => {
		expect(
			nextStep(
				row({
					route: 'sei',
					basis: 'in_specie',
					status: 'manual_intervention',
					sei_activity_id: 'ACT1'
				})
			)?.id
		).toBe('poll_sei');
		expect(
			nextStep(row({ route: 'sei', basis: 'in_specie', status: 'manual_intervention' }))?.id
		).toBe('submit_sei');
		expect(
			nextStep(
				row({
					route: 'sei',
					basis: 'in_specie',
					status: 'manual_intervention',
					sei_accepted: true
				})
			)
		).toBeNull();
	});

	it('offers nothing on a detected row', () => {
		expect(nextStep(row({ uid: 'detected:7', status: 'funds_sent' }))).toBeNull();
		expect(cancelStep(row({ uid: 'detected:7', status: 'funds_sent' }))).toBeNull();
	});

	it('drops At administrator from the Seccl and SEI rails', () => {
		expect(railStages('seccl').map((stage) => stage.status)).not.toContain('assets_received');
		expect(railStages('sei').map((stage) => stage.status)).not.toContain('assets_received');
		expect(railStages('ml_seccl').map((stage) => stage.status)).not.toContain('assets_received');
		expect(railStages('ml_sei').map((stage) => stage.status)).toContain('assets_received');
		expect(railStages('seccl').map((stage) => stage.status)).not.toContain('completed');
		expect(railStages('ml_sei').map((stage) => stage.status)).not.toContain('completed');
		expect(railStages('ml_sei').find((stage) => stage.status === 'assets_received')?.label).toBe(
			'At administrator'
		);
	});

	it('generates an anticipated receipt once Morgan Lloyd has completed the transfer', () => {
		expect(nextStep(row({ route: 'ml_sei', status: 'assets_received' }))?.id).toBe('refresh_ml');
		expect(nextStep(row({ route: 'ml_sei', status: 'funds_sent' }))).toEqual({
			id: 'lodge_sei',
			label: 'Generate ACR with custodian'
		});
		expect(
			nextStep(row({ route: 'ml_sei', status: 'funds_sent', expectation_status: 'instructed' }))?.id
		).toBe('refresh_ml');
		expect(
			nextStep(row({ route: 'ml_sei', status: 'funds_sent', sei_activity_id: 'ACR1' }))?.id
		).toBe('refresh_ml');
		expect(
			nextStep(
				row({
					route: 'ml_sei',
					status: 'funds_sent',
					basis: 'in_specie',
					sei_activity_id: 'FR1'
				})
			)?.id
		).toBe('refresh_ml');
		expect(
			nextStep(
				row({
					route: 'ml_sei',
					status: 'funds_sent',
					basis: 'in_specie',
					sei_accepted: true
				})
			)?.id
		).toBe('refresh_ml');
		expect(
			nextStep(row({ route: 'ml_sei', status: 'assets_received', basis: 'in_specie' }))?.id
		).toBe('refresh_ml');
	});
});

describe('reconciliation columns', () => {
	it('is requested against received for a custodian-only transfer', () => {
		const open = reconciliationColumns(
			row({ route: 'seccl', status: 'submitted', transaction_amount: 1000 })
		);
		expect(open.map((c) => c.heading)).toEqual(['Requested', 'Received']);
		expect(open[0].amount).toBe(1000);
		expect(open[1].amount).toBeNull();
		expect(
			reconciliationColumns(
				row({ route: 'seccl', status: 'funds_sent', transaction_amount: 900 })
			)[1]
		).toMatchObject({
			amount: 900,
			party: 'Custodian'
		});
	});

	it('adds the scheme administrator for Morgan Lloyd', () => {
		const columns = reconciliationColumns(
			row({ route: 'ml_sei', status: 'assets_received', ml_status: 3, transaction_amount: null })
		);
		expect(columns.map((c) => c.heading)).toEqual(['Requested', 'Received', 'Transferred']);
		expect(columns.map((c) => c.party)).toEqual(['Ceding provider', 'Scheme admin', 'Custodian']);
		expect(columns[1].amount).toBe(1000);
		expect(columns[2].amount).toBeNull();
	});
});

describe('the custodian filter', () => {
	it('is offered only when the firm has more than one custodian switched on', () => {
		expect(custodianFilterOptions([{ code: 'sei', name: 'SEI' }])).toBeNull();
		expect(custodianFilterOptions([])).toBeNull();
		expect(
			custodianFilterOptions([
				{ code: 'seccl', name: 'Seccl' },
				{ code: 'sei', name: 'SEI' }
			])
		).toEqual([
			{ value: 'seccl', label: 'Seccl' },
			{ value: 'sei', label: 'SEI' }
		]);
	});
});

describe('a footer action path', () => {
	it('is one of the transfer calls, and nothing inherited from Object', () => {
		expect(transferActionPath('place_seccl', 'trf000000001')).toBe(
			'/transfers/trf000000001/record-to-seccl'
		);
		expect(transferActionPath('submit_ml', 'trf000000001')).toBe(
			'/transfers/trf000000001/record-to-morgan-lloyd'
		);
		expect(transferActionPath('constructor', 'evil.example')).toBeNull();
		expect(transferActionPath('__proto__', 'evil.example')).toBeNull();
		expect(transferActionPath('toString', 'evil.example')).toBeNull();
	});

	it('encodes the uid so it cannot become a userinfo host', () => {
		expect(transferActionPath('poll_sei', 'a@evil.example')).toBe(
			'/transfers/a%40evil.example/refresh-sei'
		);
	});

	it('does not treat a standing SEI hold as a failed Morgan Lloyd refresh', () => {
		const hold = 'SEI accepted this instruction but did not return an activity id';
		expect(stepRefusal('refresh_ml', hold)).toBeNull();
		expect(stepRefusal('lodge_sei', hold)).toBe(hold);
		expect(stepRefusal('place_seccl', '')).toBeNull();
		expect(stepRefusal('submit_sei', null)).toBeNull();
	});
});
