/**
 * The held change said on the card it will change: the case, what it touches, who submitted it
 * and how long ago, whose move it is, and the way to the case.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ReviewHoldBanner from './review-hold-banner.svelte';
import type { ReviewRow } from './types';

const T0 = Date.parse('2026-10-22T18:00:00Z');
const HOUR = 3_600_000;

const review = (over: Partial<ReviewRow> = {}): ReviewRow => ({
	uid: 'rev_3',
	case_no: 3,
	action_key: 'client.details_changed',
	label: 'Edit client details',
	summary: 'Edit client details',
	before_label: 'Current details',
	after_label: 'Change W-9 or W-8',
	event_type: 'client.details_updated',
	subject_type: 'contact',
	subject_id: 'con_1',
	contact_uid: 'con_1',
	contact_name: 'Mark Black',
	account_uid: null,
	account_name: null,
	justification: '',
	reviewer_role_id: 1,
	reviewer_role_slug: 'adviser',
	reviewer_role_name: 'Adviser',
	approver_roles: ['Adviser'],
	eligible_approvers: 2,
	proposed_by: 'user-1',
	proposed_by_name: 'Mark Alford',
	proposed_at: new Date(T0 - 9 * HOUR).toISOString(),
	status: 'pending',
	decided_by: null,
	decided_by_name: null,
	decided_at: null,
	decision_note: null,
	applied_at: null,
	is_mine: false,
	can_decide: false,
	payload: { contact_id: 1, fields: { fatca_status: null, us_person: false } },
	before: { fatca_status: 'w8', us_person: false },
	...over
});

describe('ReviewHoldBanner', () => {
	it('names the case, the fields it moves, who raised it, how long ago and who decides', () => {
		const html = render(ReviewHoldBanner, {
			props: { review: review(), href: '/reviews?open=rev_3', now: T0 }
		}).body;
		expect(html).toContain('Waiting for approval');
		expect(html).toContain('RV-3');
		expect(html).toContain('W-9 or W-8');
		expect(html).toContain('Mark Alford · 9h ago');
		expect(html).toContain('Waiting on Adviser · 2 people');
		expect(html).toContain('href="/reviews?open=rev_3"');
		expect(html).not.toContain('Cancel request');
	});

	it('takes a name for what it touches, and offers the submitter a withdrawal', () => {
		const html = render(ReviewHoldBanner, {
			props: {
				review: review({
					is_mine: true,
					action_key: 'client.vulnerability_updated',
					justification: 'Told us on the call'
				}),
				what: 'Vulnerability',
				now: T0,
				cancelAction: '?/cancelReview',
				enhance: (() => {}) as never
			}
		}).body;
		expect(html).toContain('Vulnerability');
		expect(html).toContain('Told us on the call');
		expect(html).toContain('action="?/cancelReview"');
		expect(html).toContain('name="review_uid" value="rev_3"');
		expect(html).toContain('Cancel request');
	});

	it('says in red when nobody can decide it', () => {
		const html = render(ReviewHoldBanner, {
			props: { review: review({ eligible_approvers: 0 }), now: T0 }
		}).body;
		expect(html).toContain('Nobody else holds Adviser');
		expect(html).toContain('text-destructive-strong');
	});
});
