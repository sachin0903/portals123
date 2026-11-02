import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import PendingReview from './pending-review.svelte';
import type { ReviewRow } from './types';

const review = (over: Partial<ReviewRow> = {}): ReviewRow => ({
	uid: 'rev_bank_1',
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
	proposed_by_name: 'Gio Matonti',
	proposed_at: '2026-09-22T10:00:00Z',
	status: 'pending',
	decided_by: null,
	decided_by_name: null,
	decided_at: null,
	decision_note: null,
	applied_at: null,
	is_mine: true,
	can_decide: false,
	payload: {
		name: 'Gio Matonti',
		sort_code: '20-00-00',
		account_number: '12345678'
	},
	before: { value: null },
	...over
});

describe('PendingReview', () => {
	it('states the case, who, why and the field-level change — not a tinted sentence', () => {
		const html = render(PendingReview, {
			props: {
				review: review(),
				href: '/reviews?open=rev_bank_1',
				oncancel: () => undefined
			}
		}).body;

		expect(html).toContain('RV-12');
		expect(html).toContain('Waiting');
		expect(html).toContain('Gio Matonti');
		expect(html).toContain('Client moved banks');
		expect(html).toContain('Now');
		expect(html).toContain('Proposed');
		expect(html).toContain('12345678');
		expect(html).toContain('20 - 00 - 00');
		expect(html).not.toContain('••••');
		expect(html).toContain('Open review');
		expect(html).toContain('Cancel request');
		expect(html).not.toContain('waiting for review');
	});
});
