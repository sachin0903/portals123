/**
 * One run's page: the header names the action and links the records, a refused run says the
 * provider's reason once and offers ONE next action, the tree keeps its shape, and the rail
 * carries the timeline, the approval, the context and the timing.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import RunDetailView from './run-detail.svelte';
import type { Enhance } from '../book/clients/form-enhance.js';
import type { RunLinks } from './run-types.js';
import { refusedRun, run } from './run-fixtures.js';

const enhance = (() => {}) as Enhance;
const links: RunLinks = {
	run: (uid) => `/actions/runs/${uid}`,
	client: (uid) => `/clients/${uid}`,
	account: (uid) => `/accounts/${uid}`,
	journey: (uid) => `/journeys/${uid}`,
	policy: (key) => `/actions?tab=policies&action=${key}`
};
const actions = {
	retry: '?/retry',
	retryStep: '?/retryStep',
	cancel: '?/cancel',
	decide: '?/decide'
};

describe('run detail', () => {
	it('names the action and links the client and the account', () => {
		const { body } = render(RunDetailView, { props: { run: run(), links, enhance } });
		expect(body).toContain('Payment');
		expect(body).toContain('transaction.payment');
		expect(body).toContain('href="/clients/con_1"');
		expect(body).toContain('Ada Okonkwo');
		expect(body).toContain('href="/accounts/acc_1"');
		expect(body).toContain('Seccl');
		expect(body).toContain('Dale Prentice');
	});

	it('draws the tree by NAME, with a book read as a result rather than a missing address', () => {
		const { body } = render(RunDetailView, { props: { run: run(), links, enhance } });
		expect(body).toContain('Load account book');
		expect(body).toContain('Seccl payment');
		expect(body).toContain('load.account_book');
		expect(body).not.toContain('No address recorded');
		// the send's collapsed summary shows its verb, path and code
		expect(body).toContain('POST');
		expect(body).toContain('/portfoliotransactiongroup');
		expect(body).toContain('200');
	});

	it('says the provider’s reason once and offers Retry from the node that stopped', () => {
		const { body } = render(RunDetailView, {
			props: { run: refusedRun(), links, enhance, canManage: true, actions }
		});
		expect(body).toContain('Provided model id is either invalid');
		expect(body).toContain('swp.psmModelSoa.businessValidationException');
		expect(body).toContain('Not transient');
		expect(body).toContain('>Retry<');
		expect(body).toContain('action="?/retry"');
		// the refused call is badged as what it is
		expect(body).toContain('422');
		expect(body).toContain('RV-7');
	});

	it('offers no act to a reader who may not manage', () => {
		const { body } = render(RunDetailView, {
			props: { run: refusedRun(), links, enhance, canManage: false, actions }
		});
		expect(body).not.toContain('action="?/retry"');
		expect(body).not.toContain('action="?/retryStep"');
		expect(body).not.toContain('Cancel run');
	});

	it('offers Approve and Reject to an approver of a held run, and Cancel while it is open', () => {
		const held = run({
			status: 'awaiting_approval',
			state: { type: 'paused', name: 'AwaitingApproval', message: null },
			justification: 'Client confirmed by phone.',
			approval: { required: true, roles: ['Compliance'], gate_uid: 'gate_1', can_decide: true },
			can_cancel: true
		});
		const { body } = render(RunDetailView, {
			props: { run: held, links, enhance, canManage: true, actions }
		});
		expect(body).toContain('Held for approval');
		expect(body).toContain('Client confirmed by phone.');
		expect(body).toContain('action="?/decide"');
		expect(body).toContain('>Approve<');
		expect(body).toContain('>Reject<');
		expect(body).toContain('Cancel run');
		expect(body).toContain('Compliance');
	});

	it('tells a non-approver why there is nothing to press', () => {
		const held = run({
			status: 'awaiting_approval',
			state: { type: 'paused', name: 'AwaitingApproval', message: null },
			approval: { required: true, roles: ['Compliance'], gate_uid: 'gate_1', can_decide: false },
			can_cancel: true
		});
		const { body } = render(RunDetailView, {
			props: { run: held, links, enhance, canManage: true, actions }
		});
		expect(body).not.toContain('action="?/decide"');
		expect(body).toContain('Waiting for Compliance to decide');
	});

	it('shows the timeline from the events, with the actor', () => {
		const { body } = render(RunDetailView, { props: { run: run(), links, enhance } });
		expect(body).toContain('Timeline');
		expect(body).toContain('Created');
		expect(body).toContain('Completed');
	});

	it('shows what the door recorded and keeps the raw params behind a disclosure', () => {
		const { body } = render(RunDetailView, { props: { run: run(), links, enhance } });
		expect(body).toContain('what the door recorded');
		expect(body).toContain('GBP');
		expect(body).toContain('Show raw params');
	});
});
