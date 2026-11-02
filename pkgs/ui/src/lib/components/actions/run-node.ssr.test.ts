/**
 * One node of the tree in each state baton can leave it in: a wait is a wait, a skip says why,
 * a gate says who may decide, a composite opens onto its children.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import RunNode from './run-node.svelte';
import type { Enhance } from '../book/clients/form-enhance.js';
import { node, refusedNode } from './run-fixtures.js';

const enhance = (() => {}) as Enhance;

describe('run node', () => {
	it('badges a wait attempt as Waited, not Failed', () => {
		const waited = node({
			uid: 'n_wait',
			action_key: 'sei.pay_to',
			name: 'Create SEI pay-to profile',
			provider: 'sei',
			attempt_count: 2,
			attempts: [
				{
					attempt_no: 1,
					outcome: 'wait',
					method: null,
					url: null,
					status_code: null,
					error: 'SEI account TAVI00070 is Pre Open. The pay-to was not sent.',
					error_type: 'NotReady',
					duration_ms: 12000,
					started_at: '2026-10-21T13:11:17.000Z',
					ended_at: '2026-10-21T13:11:29.000Z',
					request: null,
					response: null
				},
				{
					attempt_no: 2,
					outcome: 'ok',
					method: 'POST',
					url: 'https://sei.example/createpaytorecipientforaccount2',
					status_code: 200,
					error: null,
					error_type: null,
					duration_ms: 7100,
					started_at: '2026-10-21T13:13:05.000Z',
					ended_at: '2026-10-21T13:13:12.000Z',
					request: { accountId: 'TAVI00070' },
					response: { ok: true }
				}
			]
		});
		const { body } = render(RunNode, { props: { node: waited, expandAll: true } });
		expect(body).toContain('Waited');
		expect(body).toContain('Pre Open');
		expect(body).toContain('2 attempts');
		expect(body).toContain('Succeeded');
		expect(body).not.toContain('>Failed<');
	});

	it('says a book read decided something, never that no address was recorded', () => {
		const { body } = render(RunNode, { props: { node: node() } });
		expect(body).toContain('Load account book');
		expect(body).toContain('Graphene');
		expect(body).toContain('custodian seccl');
		expect(body).not.toContain('No address recorded');
	});

	it('shows a waiting node’s next poll and its wait budget', () => {
		const waiting = node({
			uid: 'n_w',
			action_key: 'sei.enrol',
			name: 'Enrol client and account at SEI',
			provider: 'sei',
			state: { type: 'scheduled', name: 'Waiting', message: 'account is Pre Open' },
			status: 'waiting',
			scheduled_at: '2026-10-21T13:11:40.000Z',
			wait_started_at: '2026-10-21T13:11:17.000Z',
			wait_budget_seconds: 1800,
			ended_at: null,
			attempts: []
		});
		const { body } = render(RunNode, {
			props: { node: waiting, now: new Date('2026-10-21T13:11:31.000Z').getTime() }
		});
		expect(body).toContain('Waiting');
		expect(body).toContain('account is Pre Open');
		expect(body).toContain('polling again in 9 s');
	});

	it('says why a node was skipped', () => {
		const skipped = node({
			uid: 'n_s',
			action_key: 'sei.pay_to',
			name: 'Create SEI pay-to profile',
			state: {
				type: 'completed',
				name: 'Skipped',
				message: 'No bank details on file; no pay-to to create.'
			},
			status: 'skipped',
			result: null,
			attempts: []
		});
		const { body } = render(RunNode, { props: { node: skipped } });
		expect(body).toContain('Skipped');
		expect(body).toContain('No bank details on file');
	});

	it('offers Retry on a refused leaf only to someone who may manage, and shows the reason', () => {
		const withAct = render(RunNode, {
			props: { node: refusedNode(), canManage: true, enhance, retryAction: '?/retryStep' }
		});
		expect(withAct.body).toContain('action="?/retryStep"');
		expect(withAct.body).toContain('name="step_id" value="node_send"');
		expect(withAct.body).toContain('Provided model id is either invalid');
		expect(withAct.body).toContain('422');
		const readOnly = render(RunNode, { props: { node: refusedNode(), canManage: false } });
		expect(readOnly.body).not.toContain('action="?/retryStep"');
	});

	it('opens a composite onto its children, each by name', () => {
		const composite = node({
			uid: 'n_c',
			action_key: 'account.create',
			name: 'Open account',
			leaf: false,
			attempts: [],
			result: null,
			children: [node({ uid: 'n_c1' }), refusedNode({ uid: 'n_c2' })]
		});
		const { body } = render(RunNode, { props: { node: composite } });
		expect(body).toContain('Open account');
		expect(body).toContain('2 children');
		expect(body).toContain('Load account book');
		expect(body).toContain('Attach SEI model');
	});

	it('shows who may decide a held gate, and Approve/Reject only to them', () => {
		const gate = node({
			uid: 'g_1',
			action_key: 'baton.gate',
			name: 'Approval',
			kind: 'gate',
			state: { type: 'paused', name: 'AwaitingApproval', message: null },
			status: 'awaiting_approval',
			approver_role_ids: [7],
			approver_roles: ['Compliance'],
			justification: 'Client confirmed by phone.',
			result: null,
			attempts: []
		});
		const mine = render(RunNode, {
			props: {
				node: gate,
				canManage: true,
				enhance,
				decideAction: '?/decide',
				decidable: new Set(['g_1'])
			}
		});
		expect(mine.body).toContain('Approvable by Compliance');
		expect(mine.body).toContain('Client confirmed by phone.');
		expect(mine.body).toContain('action="?/decide"');
		const theirs = render(RunNode, {
			props: { node: gate, canManage: true, enhance, decideAction: '?/decide' }
		});
		expect(theirs.body).not.toContain('action="?/decide"');
		expect(theirs.body).toContain('Only Compliance can decide this.');
	});

	it('folds a healthy node to one line under “only problems”', () => {
		const { body } = render(RunNode, { props: { node: node(), onlyProblems: true } });
		expect(body).toContain('Load account book');
		expect(body).not.toContain('custodian seccl');
	});
});
