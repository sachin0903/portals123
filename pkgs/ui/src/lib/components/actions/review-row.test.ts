/**
 * A held run read as a Reviews case, so the panel, the detail and the queue draw one body and
 * say one thing about who decides it.
 */
import { describe, expect, it } from 'vitest';
import { reviewRowFromRun, reviewStatusOfRun } from './review-row';
import { run } from './run-fixtures';

describe('reviewRowFromRun', () => {
	it('maps a held book review onto the case the queue reads', () => {
		const held = run({
			status: 'awaiting_approval',
			state: { type: 'paused', name: 'AwaitingApproval', message: null },
			action_key: 'contact.bank_details_changed',
			name: 'Update client bank account',
			is_review: true,
			case_no: 4,
			justification: 'Client moved banks',
			change: {
				payload: { contact_id: 1, bank_account: { name: 'New', sort_code: '04-00-04' } },
				before: { value: { name: 'Old', sort_code: '20-00-00' } }
			},
			approval: {
				required: true,
				roles: ['Compliance'],
				gate_uid: 'gate_1',
				held_uid: 'run_1',
				held_name: 'Update client bank account',
				held_since: '2026-10-21T13:11:02.000Z',
				can_decide: true,
				eligible_approvers: 2,
				reminded_at: null,
				reminders: 0
			}
		});
		const row = reviewRowFromRun(held);
		expect(row.status).toBe('pending');
		expect(row.source).toBe('review');
		expect(row.case_no).toBe(4);
		expect(row.approver_roles).toEqual(['Compliance']);
		expect(row.eligible_approvers).toBe(2);
		expect(row.can_decide).toBe(true);
		expect(row.justification).toBe('Client moved banks');
		expect(row.payload).toEqual({
			contact_id: 1,
			bank_account: { name: 'New', sort_code: '04-00-04' }
		});
		expect(row.before).toEqual({ value: { name: 'Old', sort_code: '20-00-00' } });
		expect(row.contact_name).toBe('Ada Okonkwo');
		expect(row.account_name).toBe('GIA · acc_1');
	});

	it('posts a gated child by its own uid and reads a provider run as an action case', () => {
		const held = run({
			status: 'awaiting_approval',
			approval: {
				required: true,
				roles: ['Compliance'],
				gate_uid: 'gate_c',
				held_uid: 'child_1',
				held_name: 'Create account',
				held_since: null,
				can_decide: false
			}
		});
		const row = reviewRowFromRun(held);
		expect(row.uid).toBe('child_1');
		expect(row.source).toBe('action');
		expect(row.input).toEqual({ amount: 100, currency: 'GBP' });
		expect(row.proposed_at).toBe('2026-10-21T13:11:02.000Z');
	});

	it('folds the run status onto the queue status', () => {
		expect(
			reviewStatusOfRun({ status: 'awaiting_approval', decision: null, is_review: false })
		).toBe('pending');
		expect(reviewStatusOfRun({ status: 'rejected', decision: 'rejected', is_review: false })).toBe(
			'rejected'
		);
		expect(reviewStatusOfRun({ status: 'succeeded', decision: 'approved', is_review: true })).toBe(
			'approved'
		);
		// a provider refusal after an approval is Actions' concern, not the case's
		expect(
			reviewStatusOfRun({ status: 'needs_attention', decision: 'approved', is_review: false })
		).toBe('approved');
		// a book review whose apply failed went stale
		expect(
			reviewStatusOfRun({ status: 'needs_attention', decision: 'approved', is_review: true })
		).toBe('conflicted');
	});
});
