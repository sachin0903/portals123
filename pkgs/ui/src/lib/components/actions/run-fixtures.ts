/**
 * Test fixtures: a run as `GET /actions/runs/{uid}` returns it, built node by node so each test
 * says only what it changes. Shared by the detail, panel, node and listing tests.
 */
import type { RunDetailData, RunListRow, RunNodeData, RunsPage } from './run-types.js';

export function node(over: Partial<RunNodeData> = {}): RunNodeData {
	return {
		uid: 'node_1',
		action_key: 'load.account_book',
		name: 'Load account book',
		kind: 'action',
		provider: null,
		key: 'load.account_book#0',
		position: 0,
		depth: 1,
		after_keys: [],
		leaf: true,
		state: { type: 'completed', name: 'Completed', message: null },
		status: 'succeeded',
		on_failure: 'fail',
		retries: 0,
		retries_used: 0,
		wait_budget_seconds: null,
		idempotent: true,
		params: { account_id: 42 },
		result: { custodian: 'seccl', scheme_administrator: null },
		requested_at: '2026-10-21T13:11:02.000Z',
		started_at: '2026-10-21T13:11:02.000Z',
		ended_at: '2026-10-21T13:11:02.005Z',
		scheduled_at: null,
		wait_started_at: null,
		passes: 1,
		attempt_count: 1,
		case_no: null,
		justification: null,
		approver_role_ids: [],
		approver_roles: [],
		gate_message: null,
		decision: null,
		decided_by: null,
		decided_by_name: null,
		decided_at: null,
		decision_note: null,
		failure: null,
		attempts: [
			{
				attempt_no: 1,
				outcome: 'ok',
				method: null,
				url: null,
				status_code: null,
				error: null,
				error_type: null,
				duration_ms: 5,
				started_at: '2026-10-21T13:11:02.000Z',
				ended_at: '2026-10-21T13:11:02.005Z',
				request: { account_id: 42 },
				response: { custodian: 'seccl' }
			}
		],
		children: [],
		...over
	};
}

/** The refused SEI send from the design canvas: one attempt, 422, the parsed reason. */
export function refusedNode(over: Partial<RunNodeData> = {}): RunNodeData {
	return node({
		uid: 'node_send',
		action_key: 'sei.attach_model',
		name: 'Attach SEI model',
		provider: 'sei',
		key: 'sei.attach_model#0',
		position: 1,
		state: { type: 'failed', name: 'NeedsAttention', message: 'SEI did not assign the model' },
		status: 'needs_attention',
		on_failure: 'needs_attention',
		result: null,
		ended_at: '2026-10-21T13:13:07.000Z',
		failure: {
			headline:
				'Provided model id is either invalid or cannot be associated with the portfolio : 90269',
			detail: 'Business Exception',
			code: 'swp.psmModelSoa.businessValidationException',
			tracking_id: '09d61668-df8d-4704-86b4-75e49fe16fa9',
			transient: false,
			status_code: 422
		},
		attempts: [
			{
				attempt_no: 1,
				outcome: 'failed',
				method: 'POST',
				url: 'https://sei.example/pmemodelassignmentservices/PSM/model/assignment/investment',
				status_code: 422,
				error: 'SEI did not assign the model',
				error_type: 'Permanent',
				duration_ms: 1800,
				started_at: '2026-10-21T13:13:05.000Z',
				ended_at: '2026-10-21T13:13:07.000Z',
				request: { accountId: 'TAVI00070', modelId: 90269 },
				response: { error: { code: 'swp.psmModelSoa.businessValidationException' } }
			}
		],
		...over
	});
}

export function run(over: Partial<RunDetailData> = {}): RunDetailData {
	const children = over.children ?? [
		node(),
		node({
			uid: 'node_pay',
			action_key: 'seccl.payment',
			name: 'Seccl payment',
			provider: 'seccl',
			position: 1,
			result: { id: 'PTG-1' },
			attempts: [
				{
					attempt_no: 1,
					outcome: 'ok',
					method: 'POST',
					url: 'https://api.example/portfoliotransactiongroup',
					status_code: 200,
					error: null,
					error_type: null,
					duration_ms: 318,
					started_at: '2026-10-21T13:11:02.000Z',
					ended_at: '2026-10-21T13:11:02.318Z',
					request: { amount: 100 },
					response: { id: 'PTG-1' }
				}
			]
		})
	];
	return {
		...node({
			uid: 'run_1',
			action_key: 'transaction.payment',
			name: 'Payment',
			key: null,
			position: null,
			depth: 0,
			leaf: false,
			result: { id: 'PTG-1' },
			attempts: [],
			ended_at: '2026-10-21T13:11:03.000Z',
			passes: 2,
			attempt_count: 0
		}),
		description: 'Take a payment or contribution into the account.',
		category: 'money_movement',
		event_type: 'payment.one_off_instructed',
		subject_kind: 'account',
		subject_uid: 'acc_1',
		requested_by: 'u_1',
		requested_by_name: 'Dale Prentice',
		contact: { uid: 'con_1', name: 'Ada Okonkwo' },
		account: { uid: 'acc_1', name: null, product_type: 'GIA', custodian: 'seccl' },
		journey: null,
		input: { amount: 100, currency: 'GBP' },
		manual: false,
		is_review: false,
		change: null,
		approval: {
			required: false,
			roles: [],
			gate_uid: null,
			held_uid: null,
			held_name: null,
			held_since: null,
			can_decide: false,
			eligible_approvers: null,
			reminded_at: null,
			reminders: 0
		},
		attention: null,
		timing: {
			requested_at: '2026-10-21T13:11:02.000Z',
			started_at: '2026-10-21T13:11:02.000Z',
			ended_at: '2026-10-21T13:11:03.000Z',
			next_poll_at: null,
			passes: 2,
			calls: 2,
			leaves: 2
		},
		can_retry: false,
		can_cancel: false,
		events: [
			{
				id: 1,
				type: 'run.created',
				at: '2026-10-21T13:11:02.000Z',
				actor_id: 'u_1',
				actor_name: 'Dale Prentice',
				run_uid: 'run_1',
				run_name: 'Payment',
				payload: {}
			},
			{
				id: 2,
				type: 'run.completed',
				at: '2026-10-21T13:11:03.000Z',
				actor_id: null,
				actor_name: null,
				run_uid: 'run_1',
				run_name: 'Payment',
				payload: {}
			}
		],
		...over,
		children
	};
}

/** The same run, refused at the send: needs attention, with the problem named. */
export function refusedRun(over: Partial<RunDetailData> = {}): RunDetailData {
	const send = refusedNode();
	return run({
		state: { type: 'failed', name: 'NeedsAttention', message: 'SEI did not assign the model' },
		status: 'needs_attention',
		result: null,
		case_no: 7,
		attention: { node_uid: send.uid, node_name: send.name, provider: 'sei', ...send.failure! },
		can_retry: true,
		can_cancel: false,
		events: [
			{
				id: 1,
				type: 'run.created',
				at: '2026-10-21T13:11:02.000Z',
				actor_id: 'u_1',
				actor_name: 'Dale Prentice',
				run_uid: 'run_1',
				run_name: 'Payment',
				payload: {}
			},
			{
				id: 2,
				type: 'run.needs_attention',
				at: '2026-10-21T13:13:07.000Z',
				actor_id: null,
				actor_name: null,
				run_uid: 'run_1',
				run_name: 'Payment',
				payload: {}
			}
		],
		children: [node(), send],
		...over
	});
}

export function row(over: Partial<RunListRow> = {}): RunListRow {
	return {
		uid: 'run_1',
		action_key: 'transaction.payment',
		name: 'Payment',
		status: 'succeeded',
		state: { type: 'completed', name: 'Completed', message: null },
		case_no: null,
		subject_kind: 'account',
		subject_uid: 'acc_1',
		contact_uid: 'con_1',
		contact_name: 'Ada Okonkwo',
		requested_by: 'u_1',
		requested_by_name: 'Dale Prentice',
		requested_at: '2026-10-14T08:41:00.000Z',
		started_at: '2026-10-14T08:41:00.000Z',
		ended_at: '2026-10-14T08:41:01.200Z',
		providers: ['seccl'],
		children: [
			{
				uid: 'c1',
				key: 'load.account_book',
				name: 'Load account book',
				kind: 'action',
				status: 'succeeded'
			},
			{
				uid: 'c2',
				key: 'seccl.payment',
				name: 'Seccl payment',
				kind: 'action',
				status: 'succeeded'
			}
		],
		can_decide: false,
		is_review: false,
		held_uid: null,
		held_since: null,
		approver_role_ids: [],
		approver_roles: [],
		eligible_approvers: null,
		held_amount: null,
		...over
	};
}

/** The same row, resting at its gate: the facts the approvals seat reads. */
export function heldRow(over: Partial<RunListRow> = {}): RunListRow {
	return row({
		uid: 'run_held',
		status: 'awaiting_approval',
		state: { type: 'paused', name: 'AwaitingApproval', message: null },
		case_no: 1,
		ended_at: null,
		providers: ['sei'],
		children: [
			{ uid: 'g1', key: 'approval', name: 'Approval', kind: 'gate', status: 'awaiting_approval' }
		],
		can_decide: true,
		held_uid: 'run_held',
		held_since: '2026-10-21T00:32:00.000Z',
		approver_role_ids: [7],
		approver_roles: ['Compliance'],
		eligible_approvers: 1,
		held_amount: 25000,
		...over
	});
}

export function runsPage(items: RunListRow[], over: Partial<RunsPage> = {}): RunsPage {
	return {
		items,
		total: items.length,
		limit: 25,
		offset: 0,
		facets: {
			state: [...new Set(items.map((r) => r.status))],
			action: [...new Set(items.map((r) => r.action_key))],
			provider: [...new Set(items.flatMap((r) => r.providers))]
		},
		facet_labels: { action: { 'transaction.payment': 'Payment' }, provider: { seccl: 'Seccl' } },
		stats: {
			running: 3,
			waiting: 1,
			awaiting_approval: 2,
			needs_attention: 1,
			failed_7d: 0,
			completed_24h: 48,
			held_over_day: 1,
			held_undecidable: 1,
			money_held: 25000,
			decided_7d: 6,
			approved_7d: 5,
			rejected_7d: 1
		},
		...over
	};
}
