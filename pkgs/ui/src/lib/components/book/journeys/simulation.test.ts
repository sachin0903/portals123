/**
 * The dry run's presentation layer.
 *
 * Nothing here evaluates a condition — the server does that, with the engine's own evaluator, and
 * a browser-side second opinion is exactly what the shared contract exists to prevent. What is
 * tested is the wording and the grouping: an untaken branch arm must not read like an authoring
 * mistake, a step that fans out to three owners must say so, and an ending the author did not
 * intend must be told apart from one they did.
 */
import { describe, expect, it } from 'vitest';
import {
	EXCLUSION_IS_A_PROBLEM,
	actionSummary,
	exclusionLabel,
	fanOutLabel,
	outcomeHeadline,
	outcomeIsHealthy,
	plannedWork,
	splitExclusions,
	tierLabel,
	totalDurationLabel,
	whenLabel,
	type FlowSimulation,
	type SimExcluded,
	type SimHop
} from './simulation.js';

const hop = (over: Partial<SimHop> = {}): SimHop => ({
	node_id: 'intake',
	kind: 'step',
	title: 'Fact find',
	type: 'fill_form',
	blocking: true,
	instances: [
		{ case_kind: 'profile', subject_type: 'contact', subject_id: 1, participant_subject_id: 1 }
	],
	visibility: null,
	scope: 'participant',
	assignee: 'client',
	required: true,
	arms: [],
	taken_arm: null,
	elided: [],
	resumes_at: null,
	listens_for: null,
	timeout_branch: null,
	at_day: 0,
	config: {},
	note: null,
	...over
});

const sim = (over: Partial<FlowSimulation> = {}): FlowSimulation => ({
	performed: false,
	shape: 'graph',
	path: [hop()],
	excluded: [],
	outcome: { kind: 'completed', node_id: null, detail: null, elapsed_days: 0 },
	tiers: {
		cases: 1,
		profile_cases: 1,
		account_cases: 0,
		application_cases: 0,
		step_instances: 1,
		participants: 1,
		accounts: 0
	},
	facts: {},
	flow: { uid: 'flw_1', code: 'onb', name: 'Onboarding' },
	version: { uid: 'fv_1', version: 2, status: 'published' },
	subject: { kind: 'real', contact_id: 1, label: 'Ada Lovelace', accounts: [], truncated: false },
	issues: [],
	simulated_at: '2026-08-04T09:00:00Z',
	...over
});

describe('whenLabel', () => {
	it('says how far into the journey a hop happens, in round terms', () => {
		expect(whenLabel(0)).toBe('straight away');
		expect(whenLabel(0.25)).toBe('after about 6 hours');
		expect(whenLabel(1)).toBe('after 1 day');
		expect(whenLabel(7)).toBe('after 7 days');
		expect(whenLabel(30)).toBe('after about 4 weeks');
		expect(whenLabel(120)).toBe('after about 4 months');
	});

	it('treats a nonsense clock as immediate rather than rendering NaN', () => {
		expect(whenLabel(Number.NaN)).toBe('straight away');
		expect(whenLabel(-3)).toBe('straight away');
	});
});

describe('totalDurationLabel', () => {
	it('is null when nothing in the journey waits — there is no duration to state', () => {
		expect(totalDurationLabel(sim())).toBeNull();
	});

	it('reads the accumulated clock when there are waits', () => {
		expect(
			totalDurationLabel(
				sim({ outcome: { kind: 'completed', node_id: null, detail: null, elapsed_days: 37 } })
			)
		).toBe('after about 5 weeks');
	});
});

describe('outcomes', () => {
	it('tells an ending the author meant from one they need to fix', () => {
		expect(outcomeIsHealthy('exit')).toBe(true);
		expect(outcomeIsHealthy('handed_off')).toBe(true);
		expect(outcomeIsHealthy('completed')).toBe(true);
		expect(outcomeIsHealthy('looped')).toBe(false);
		expect(outcomeIsHealthy('dangling')).toBe(false);
		expect(outcomeIsHealthy('unfinished')).toBe(false);
	});

	it('names the authored outcome on an explicit exit', () => {
		const result = sim({
			outcome: {
				kind: 'exit',
				node_id: 'stop',
				detail: null,
				elapsed_days: 0,
				outcome: 'cancelled'
			}
		});
		expect(outcomeHeadline(result)).toBe('The journey ends — cancelled');
	});

	it('has a headline for every kind, including the ones nobody wants', () => {
		expect(outcomeHeadline(sim({ outcome: { ...sim().outcome, kind: 'looped' } }))).toContain(
			'loops'
		);
	});
});

describe('fan-out and tier', () => {
	it('says nothing extra when a step runs once', () => {
		expect(fanOutLabel(hop())).toBeNull();
	});

	it('counts people when every instance is per-participant', () => {
		const three = hop({
			instances: [1, 2, 3].map((id) => ({
				case_kind: 'account',
				subject_type: 'account',
				subject_id: 10,
				participant_subject_id: id
			}))
		});
		expect(fanOutLabel(three)).toBe('3 people');
		expect(tierLabel(three)).toBe('per account');
	});

	it('says nothing about a tier for a node the plan never creates', () => {
		expect(tierLabel(hop({ instances: [] }))).toBeNull();
	});
});

describe('actionSummary', () => {
	it('names the action and the thing it targets', () => {
		expect(
			actionSummary(hop({ kind: 'action', type: 'apply_tag', config: { tag: 'onboarding' } }))
		).toBe('apply tag — onboarding');
		expect(
			actionSummary(
				hop({ kind: 'action', type: 'send_email', config: { template_code: 'welcome' } })
			)
		).toBe('send email — welcome');
	});

	it('falls back to the action’s own name when nothing identifies its target', () => {
		expect(actionSummary(hop({ kind: 'action', type: 'webhook_out', config: {} }))).toBe(
			'webhook out'
		);
	});
});

describe('exclusions', () => {
	const excluded = (over: Partial<SimExcluded> = {}): SimExcluded => ({
		node_id: 'edd',
		kind: 'step',
		title: 'Enhanced due diligence',
		reason: 'branch_not_taken',
		because_of: 'risk',
		...over
	});

	it('separates the flow working from the author’s mistakes', () => {
		// An untaken arm is the branch doing its job; an orphan is a node nobody can ever reach.
		const result = sim({
			excluded: [
				excluded(),
				excluded({ node_id: 'lost', reason: 'orphan', because_of: null }),
				excluded({ node_id: 'chase', reason: 'timeout_only', because_of: 'listen' })
			]
		});
		const { byDesign, problems } = splitExclusions(result);
		expect(byDesign.map((e) => e.node_id)).toEqual(['edd', 'chase']);
		expect(problems.map((e) => e.node_id)).toEqual(['lost']);
	});

	it('classifies every reason the server can send', () => {
		for (const reason of [
			'branch_not_taken',
			'timeout_only',
			'not_planned',
			'orphan',
			'unreached'
		] as const) {
			expect(typeof EXCLUSION_IS_A_PROBLEM[reason]).toBe('boolean');
			expect(exclusionLabel(reason)).toBeTruthy();
		}
	});
});

describe('plannedWork', () => {
	it('is the "what does this client actually get asked for" list', () => {
		const result = sim({
			path: [
				hop({ node_id: 'a' }),
				hop({ node_id: 'skipped', instances: [] }),
				hop({ node_id: 'branch', kind: 'branch', instances: [] }),
				hop({ node_id: 'act', kind: 'action', type: 'apply_tag' })
			]
		});
		expect(plannedWork(result).map((h) => h.node_id)).toEqual(['a']);
	});
});
