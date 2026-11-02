/**
 * A held run, read as the Reviews queue reads a case — so the run panel and the run detail can
 * draw the SAME body (`ReviewChange`) and say the same things about who decides it, instead of
 * the ops seat approving a bank change it could not see.
 */
import type { ReviewRow, ReviewStatus } from '../book/reviews/types.js';
import type { RunDetailData } from './run-types.js';

/** The queue's status word for a run: pending while it rests at its gate, the decision once
 *  made, and conflicted for a book review whose apply failed. */
export function reviewStatusOfRun(
	run: Pick<RunDetailData, 'status' | 'decision' | 'is_review'>
): ReviewStatus {
	switch (run.status) {
		case 'awaiting_approval':
			return 'pending';
		case 'rejected':
			return 'rejected';
		case 'cancelled':
			return 'cancelled';
		case 'conflicted':
			return 'conflicted';
	}
	if (run.decision === 'approved') {
		if (run.is_review && (run.status === 'failed' || run.status === 'needs_attention')) {
			return 'conflicted';
		}
		return 'approved';
	}
	return 'pending';
}

export function reviewRowFromRun(run: RunDetailData): ReviewRow {
	const account = run.account
		? [run.account.product_type, run.account.name ?? run.account.uid].filter(Boolean).join(' · ')
		: null;
	return {
		uid: run.approval.held_uid ?? run.uid,
		case_no: run.case_no ?? 0,
		action_key: run.action_key,
		label: run.name,
		summary: null,
		before_label: null,
		after_label: null,
		event_type: run.event_type ?? '',
		subject_type: run.subject_kind ?? '',
		subject_id: run.subject_uid ?? '',
		contact_uid: run.contact?.uid ?? null,
		contact_name: run.contact?.name ?? null,
		account_uid: run.account?.uid ?? null,
		account_name: account,
		justification: run.justification ?? '',
		reviewer_role_id: null,
		reviewer_role_slug: null,
		reviewer_role_name: run.approval.roles[0] ?? null,
		approver_roles: run.approval.roles,
		eligible_approvers: run.approval.eligible_approvers ?? null,
		proposed_by: run.requested_by ?? '',
		proposed_by_name: run.requested_by_name,
		proposed_at: run.approval.held_since ?? run.requested_at ?? '',
		status: reviewStatusOfRun(run),
		decided_by: run.decided_by,
		decided_by_name: run.decided_by_name,
		decided_at: run.decided_at,
		decision_note: run.decision_note,
		applied_at: null,
		is_mine: false,
		can_decide: run.approval.can_decide,
		source: run.is_review ? 'review' : 'action',
		payload: run.change?.payload ?? null,
		before: run.change?.before ?? null,
		input: run.input
	};
}
