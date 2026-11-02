/**
 * The runs workbench: counters that say who has it, a server-driven queue whose step bar names
 * each child, and the panel pushed in beside it when a row is picked.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import RunsListing from './runs-listing.svelte';
import { emptyQuery } from '../book/listing/query.js';
import type { Enhance } from '../book/clients/form-enhance.js';
import type { RunLinks } from './run-types.js';
import { heldRow, refusedRun, row, run, runsPage } from './run-fixtures.js';

const noop = () => {};
const enhance = (() => {}) as Enhance;
const links: RunLinks = {
	run: (uid) => `/actions/runs/${uid}`,
	client: (uid) => `/clients/${uid}`,
	account: (uid) => `/accounts/${uid}`
};
const KEYS = ['state', 'action', 'provider', 'requested_by', 'contact'] as const;

const props = (items: ReturnType<typeof row>[]) => ({
	page: runsPage(items),
	query: emptyQuery(KEYS),
	onquery: noop,
	links
});

describe('runs listing', () => {
	it('shows the counters with their counts and who has each', () => {
		const { body } = render(RunsListing, { props: props([]) });
		expect(body).toContain('Needs attention');
		expect(body).toContain('Waiting on provider');
		expect(body).toContain('Completed · 24h');
		expect(body).toContain('48');
		expect(body).toContain('Approvers');
	});

	it('renders a run row with its action, client, provider, state and requester', () => {
		const { body } = render(RunsListing, {
			props: props([row({ case_no: 7, status: 'needs_attention' })])
		});
		expect(body).toContain('Payment');
		expect(body).toContain('transaction.payment');
		expect(body).toContain('Needs attention');
		expect(body).toContain('Ada Okonkwo');
		expect(body).toContain('href="/clients/con_1"');
		expect(body).toContain('Seccl');
		expect(body).toContain('Dale Prentice');
		expect(body).toContain('RV-7');
	});

	it('names each step-bar segment with the child and its state', () => {
		const { body } = render(RunsListing, { props: props([row()]) });
		expect(body).toContain('Seccl payment · Succeeded');
	});

	it('names the empty state', () => {
		const { body } = render(RunsListing, { props: props([]) });
		expect(body).toContain('No runs yet');
	});

	it('says so when a filter hides every run, and offers to clear it', () => {
		const query = { ...emptyQuery(KEYS), filters: { state: 'failed' } };
		const { body } = render(RunsListing, { props: { ...props([]), query } });
		expect(body).toContain('No runs match these filters');
		expect(body).toContain('Clear filters');
	});

	it('pushes the picked run in beside the queue with its one next action', () => {
		const { body } = render(RunsListing, {
			props: {
				...props([row({ status: 'needs_attention' })]),
				selected: refusedRun(),
				onselect: noop,
				canManage: true,
				enhance,
				actions: { retry: '?/retry', cancel: '?/cancel' }
			}
		});
		expect(body).toContain('aria-label="Selected run"');
		expect(body).toContain('Provided model id is either invalid');
		expect(body).toContain('>Retry<');
		expect(body).toContain('href="/actions/runs/run_1"');
		expect(body).toContain('Close panel');
	});
});

describe('the approvals seat', () => {
	const held = (over: Parameters<typeof heldRow>[0] = {}) => heldRow(over);
	const approvalsProps = (items: ReturnType<typeof row>[]) => ({
		...props(items),
		query: { ...emptyQuery(KEYS), filters: { state: 'awaiting_approval' } },
		variant: 'approvals' as const
	});

	it("shows the gate's own counters, not the runs'", () => {
		const { body } = render(RunsListing, { props: approvalsProps([]) });
		expect(body).toContain('Older than a day');
		expect(body).toContain('Nobody can decide');
		expect(body).toContain('Money held');
		expect(body).toContain('£25,000.00');
		expect(body).toContain('Decided · 7 days');
		expect(body).toContain('5 approved · 1 rejected');
		expect(body).not.toContain('Waiting on provider');
		expect(body).not.toContain('Completed · 24h');
	});

	it('says who may decide each row, how many could, and how long it has waited', () => {
		const { body } = render(RunsListing, {
			props: approvalsProps([held({ eligible_approvers: 3 })])
		});
		expect(body).toContain('>Held');
		expect(body).toContain('>Approvers<');
		expect(body).toContain('>Waiting<');
		expect(body).toContain('Compliance');
		expect(body).toContain('· 3');
		expect(body).toContain('£25,000.00');
		expect(body).toContain('RV-1');
		expect(body).not.toContain('>Progress<');
		expect(body).not.toContain('>Duration<');
	});

	it('marks a row nobody can decide in red', () => {
		const { body } = render(RunsListing, {
			props: approvalsProps([held({ eligible_approvers: 0 })])
		});
		expect(body).toContain('· 0 others');
		expect(body).toContain('border-destructive-line');
		expect(body).toContain('shadow-[inset_3px_0_0_var(--color-destructive)]');
	});

	it('names the empty seat', () => {
		const { body } = render(RunsListing, { props: approvalsProps([]) });
		expect(body).toContain('Nothing is waiting for approval.');
	});

	it('pushes a held run in with its change, who has it, and Approve · Reject · Remind', () => {
		const selected = run({
			status: 'awaiting_approval',
			state: { type: 'paused', name: 'AwaitingApproval', message: null },
			action_key: 'contact.bank_details_changed',
			name: 'Update client bank account',
			is_review: true,
			case_no: 4,
			can_cancel: true,
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
		const { body } = render(RunsListing, {
			props: {
				...approvalsProps([held()]),
				selected,
				onselect: noop,
				canManage: true,
				enhance,
				actions: { decide: '?/decide', cancel: '?/cancel', remind: '?/remind' }
			}
		});
		expect(body).toContain('aria-label="Selected run"');
		expect(body).toContain('The change');
		expect(body).toContain('>Now<');
		expect(body).toContain('>Proposed<');
		expect(body).toContain('New');
		expect(body).toContain('Old');
		expect(body).toContain('You can decide this one.');
		expect(body).toContain('2 people other than the requester');
		expect(body).toContain('action="?/decide"');
		expect(body).toContain('action="?/remind"');
		expect(body).toContain('>Approve<');
		expect(body).toContain('>Reject<');
		expect(body).toContain('Cancel run');
	});
});
