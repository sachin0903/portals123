/**
 * The Reviews queue as rendered: the seat switch with its counts, the columns a seat needs
 * (how long it has waited, whose move it is), the decision in the expanded row with its folded
 * body, and the words for a case nobody can decide.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ReviewsListing from './reviews-listing.svelte';
import type { ReviewRow } from './types';
import type { Enhance } from '../clients/form-enhance.js';

const T0 = Date.parse('2026-10-22T18:00:00Z');
const HOUR = 3_600_000;
const enhance = (() => {}) as Enhance;

const row = (over: Partial<ReviewRow> = {}): ReviewRow => ({
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
	approver_roles: ['Adviser', 'Administrator'],
	eligible_approvers: 3,
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
	can_decide: true,
	payload: {
		contact_id: 1,
		fields: { us_person: false, fatca_status: null, source_of_funds: 'personal_savings' }
	},
	before: { us_person: false, fatca_status: 'w8', source_of_funds: 'personal_savings' },
	...over
});

const links = {
	client: (uid: string) => `/clients/${uid}`,
	account: (uid: string) => `/accounts/${uid}`
};

const html = (reviews: ReviewRow[], over: Record<string, unknown> = {}) =>
	render(ReviewsListing, {
		props: {
			reviews,
			links,
			onfilters: () => {},
			now: T0,
			actions: { enhance, approve: '?/approve', reject: '?/reject', cancel: '?/cancel' },
			...over
		}
	}).body;

describe('the Reviews queue', () => {
	it('offers the four seats with their counts and opens on For me', () => {
		const body = html([row(), row({ uid: 'rev_4', case_no: 4, can_decide: false, is_mine: true })]);
		expect(body).toContain('For me · 1');
		expect(body).toContain('Submitted by me · 1');
		expect(body).toContain('All waiting · 2');
		expect(body).toContain('>Settled<');
		expect(body).toContain('RV-3');
		expect(body).not.toContain('RV-4');
		expect(body).toContain('1 for you to decide');
		expect(body).toContain('See the 1 waiting on others');
	});

	it('says how long a case has waited and whose move it is', () => {
		const body = html([
			row(),
			row({
				uid: 'rev_1',
				case_no: 1,
				proposed_at: new Date(T0 - 33 * HOUR).toISOString()
			})
		]);
		expect(body).toContain('>9h<');
		expect(body).toContain('>1d 9h<');
		expect(body).toContain('>You<');
		expect(body).toContain('1 waiting more than a day');
		// the late row wears the amber inset the Runs rows use
		expect(body).toContain('shadow-[inset_3px_0_0_var(--color-warning)]');
	});

	it('names who decides when it is not the reader, and when nobody can', () => {
		const body = html(
			[
				row({ uid: 'a', can_decide: false }),
				row({
					uid: 'b',
					case_no: 9,
					can_decide: false,
					eligible_approvers: 0,
					approver_roles: ['Compliance']
				})
			],
			{ filters: { submitted_by: [], action_key: '', status: '', q: '', seat: 'waiting' } }
		);
		expect(body).toContain('Adviser · Administrator');
		expect(body).toContain('Nobody can');
	});

	it('is the decision when a row is open: the folded body, the rail, then Reject and Approve', () => {
		const body = html([row()], { openUid: 'rev_3' });
		expect(body).toContain('action="?/approve"');
		expect(body).toContain('action="?/reject"');
		expect(body).toContain('>Approve<');
		expect(body).toContain('>Reject<');
		expect(body).toContain('You hold Adviser · Administrator');
		expect(body).toContain('No reason was asked for.');
		// the change comes first with its unchanged rows folded
		expect(body).toContain('W-9 or W-8');
		expect(body).toContain('Show 2 unchanged');
		expect(body).toContain('1 of 3');
		// the client is named by the row, not again by the body
		expect(body).not.toContain('<dt class="text-muted-foreground text-xs">Client</dt>');
		expect(body).toContain('Open the client record');
	});

	it('withholds the decision from someone who cannot make it, and says why when nobody can', () => {
		const body = html(
			[
				row({
					can_decide: false,
					eligible_approvers: 0,
					approver_roles: ['Compliance']
				})
			],
			{
				openUid: 'rev_3',
				filters: { submitted_by: [], action_key: '', status: '', q: '', seat: 'waiting' }
			}
		);
		expect(body).not.toContain('action="?/approve"');
		expect(body).toContain('Nobody else holds Compliance');
		expect(body).toContain('Ask an administrator to give someone that role');
	});

	it('offers the submitter a reminder and a withdrawal, never a decision', () => {
		const body = html([row({ can_decide: false, is_mine: true })], {
			openUid: 'rev_3',
			actions: {
				enhance,
				approve: '?/approve',
				reject: '?/reject',
				cancel: '?/cancel',
				remind: '?/remind'
			},
			filters: { submitted_by: [], action_key: '', status: '', q: '', seat: 'mine' }
		});
		expect(body).toContain('Remind approvers');
		expect(body).toContain('Cancel request');
		expect(body).not.toContain('action="?/approve"');
	});

	it('shows the outcome and who decided under Settled', () => {
		const body = html(
			[
				row({
					status: 'approved',
					can_decide: false,
					decided_by_name: 'Jo Reyes',
					decided_at: new Date(T0 - 7 * HOUR).toISOString()
				})
			],
			{ filters: { submitted_by: [], action_key: '', status: '', q: '', seat: 'settled' } }
		);
		expect(body).toContain('>Approved<');
		expect(body).toContain('Jo Reyes');
		expect(body).toContain('>Outcome<');
		expect(body).not.toContain('>Waiting<');
	});

	it('names the empty seat', () => {
		expect(html([])).toContain('Nothing waiting');
		expect(
			html([row({ can_decide: false })], {
				filters: { submitted_by: [], action_key: '', status: '', q: '', seat: 'for_me' }
			})
		).toContain('Nothing for you to decide');
	});
});
