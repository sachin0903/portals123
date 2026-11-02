import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import ReviewChange from './review-change.svelte';
import type { ReviewRow } from './types';

const base = (over: Partial<ReviewRow> = {}): ReviewRow => ({
	uid: 'rev_1',
	case_no: 12,
	action_key: 'account.model_changed',
	label: 'Change model',
	summary: 'Change the model mandate',
	before_label: 'Cautious',
	after_label: 'Balanced',
	event_type: 'account.model_updated',
	subject_type: 'account',
	subject_id: 'acc_sipp',
	contact_uid: 'con_max',
	contact_name: 'Mr Max Oakden',
	account_uid: 'acc_sipp',
	account_name: 'Oakden SIPP',
	justification: 'As discussed',
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
	payload: { account_id: 2, model_id: 9, model_name: 'Balanced' },
	before: { model_id: 3, model_name: 'Cautious' },
	...over
});

describe('ReviewChange', () => {
	it('names the client and the account, and shows a model change as the two mandates', () => {
		const html = render(ReviewChange, {
			props: {
				review: base(),
				clientHref: '/clients/con_max',
				accountHref: '/accounts/acc_sipp'
			}
		}).body;
		expect(html).toContain('Client');
		expect(html).toContain('Mr Max Oakden');
		expect(html).toContain('href="/clients/con_max"');
		expect(html).toContain('Account');
		expect(html).toContain('Oakden SIPP');
		expect(html).toContain('href="/accounts/acc_sipp"');
		expect(html).toContain('Model mandate');
		expect(html).toContain('Cautious');
		expect(html).toContain('Balanced');
		expect(html).toContain('>Now<');
		expect(html).toContain('>Proposed<');
	});

	it('shows a held provider action as the form it recorded, not a now vs proposed', () => {
		const html = render(ReviewChange, {
			props: {
				review: base({
					action_key: 'transaction.transfer',
					label: 'Create transfer',
					summary: 'Waiting for approval before this is sent.',
					source: 'action',
					subject_type: 'account',
					payload: null,
					input: {
						ceding_provider: 'Aviva',
						policy_number: 'AV-123',
						basis: 'in_specie',
						total_value: 25000,
						expected_date: '2026-11-01',
						safeguarded_benefits: false,
						documents: [{ document_reference_id: 'DOC-9' }]
					}
				})
			}
		}).body;
		expect(html).toContain('Create transfer');
		expect(html).toContain('Waiting for approval before this is sent.');
		expect(html).toContain('Ceding provider');
		expect(html).toContain('Aviva');
		expect(html).toContain('In specie');
		expect(html).toMatch(/£25,000<span[^>]*>\.00<\/span>/);
		expect(html).toContain('1 Nov 2026');
		expect(html).toContain('Safeguarded benefits');
		expect(html).toContain('DOC-9');
		expect(html).not.toContain('>Now<');
		expect(html).not.toContain('>Proposed<');
	});

	it('says so when a held action recorded no input', () => {
		const html = render(ReviewChange, {
			props: {
				review: base({
					action_key: 'client.setup',
					label: 'Set up client',
					source: 'action',
					payload: null,
					input: null
				})
			}
		}).body;
		expect(html).toContain('No further detail was recorded.');
	});

	it('keeps now vs proposed for a bank edit', () => {
		const html = render(ReviewChange, {
			props: {
				review: base({
					action_key: 'contact.bank_details_changed',
					label: "Change a client's bank details",
					account_uid: null,
					account_name: null,
					subject_type: 'contact',
					payload: {
						name: 'Cleo Client',
						sort_code: '20-00-00',
						account_number: '12345678'
					},
					before: { value: null }
				})
			}
		}).body;
		expect(html).toContain('Now');
		expect(html).toContain('Proposed');
		expect(html).toContain('12345678');
	});
});
