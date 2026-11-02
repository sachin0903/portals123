/**
 * SSR coverage for the two Phase C analysis surfaces — the dry-run panel and the step funnel.
 *
 * What SSR can pin, and what actually matters for these two:
 *  - **the dry run states that nothing ran.** It is the whole basis on which an author points a
 *    draft at a real client, so the reassurance is asserted, not assumed;
 *  - **`actionName`/`enhance` are optional** — absent ⇒ no form, and an explanation instead of a
 *    dead button (the read-only posture the admin portal will mount under);
 *  - **hrefs are injected** — every link in the funnel comes from `links`, never a platform path,
 *    because the admin portal's routes differ and a hardcoded href is a broken page there;
 *  - **the empty states are the designed ones**, not a blank page: a firm that has just published
 *    a journey has no cases, and that is the most common first view this page ever has.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import FlowDryRun from './flow-dry-run.svelte';
import FlowFunnel from './flow-funnel.svelte';
import type { FlowSimulation, SimHop } from './simulation.js';
import type { FlowInsights, FlowFunnelLinks, FunnelNode } from './insights.js';

// Deliberately NOT the platform's routes — the assertions prove the component renders whatever the
// app injects rather than remembering where it was written.
const links: FlowFunnelLinks = {
	flow: '/x/journeys/flw_1',
	insights: ({ version, days }) => `/x/journeys/flw_1/insights?v=${version ?? ''}&d=${days ?? ''}`
};

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

const simulation = (over: Partial<FlowSimulation> = {}): FlowSimulation => ({
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

const node = (over: Partial<FunnelNode> = {}): FunnelNode => ({
	node_id: 'intake',
	kind: 'step',
	type: 'fill_form',
	title: 'Fact find',
	scope: 'participant',
	reached: 10,
	completed: 7,
	by_status: { fulfilled: 7, waiting: 3 },
	instances: 10,
	median_seconds: 7200,
	p90_seconds: 86_400,
	is_work: true,
	...over
});

const insights = (over: Partial<FlowInsights> = {}): FlowInsights => ({
	flow: { uid: 'flw_1', code: 'onb', name: 'Onboarding' },
	version: {
		uid: 'fv_1',
		version: 2,
		status: 'published',
		published_at: '2026-07-01T00:00:00Z',
		case_count: 10,
		is_current: true
	},
	versions: [
		{
			uid: 'fv_1',
			version: 2,
			status: 'published',
			published_at: '2026-07-01T00:00:00Z',
			case_count: 10,
			is_current: true
		},
		{
			uid: 'fv_0',
			version: 1,
			status: 'archived',
			published_at: '2026-05-01T00:00:00Z',
			case_count: 4,
			is_current: false
		}
	],
	range: { since: '2026-05-06T00:00:00Z', until: '2026-08-04T00:00:00Z' },
	shape: 'graph',
	cases: { total: 10, by_status: { completed: 6, active: 4 }, median_seconds: 172_800 },
	nodes: [node()],
	edges: [],
	...over
});

const noop = () => {};

describe('FlowDryRun', () => {
	it('says plainly that nothing was performed', () => {
		const { body } = render(FlowDryRun, {
			props: { result: simulation(), actionName: 'dryRun', enhance: noop }
		});
		expect(body).toContain('Nothing was performed');
		expect(body).toContain('never starts a journey');
	});

	it('posts to the app’s own action name', () => {
		const { body } = render(FlowDryRun, {
			props: { actionName: 'testIt', enhance: noop, clients: [{ id: 1, name: 'Ada' }] }
		});
		expect(body).toContain('?/testIt');
	});

	it('offers no form at all without the write grant, and explains why', () => {
		const { body } = render(FlowDryRun, { props: { result: simulation() } });
		expect(body).not.toContain('?/');
		expect(body).toContain('don’t have permission to dry-run');
	});

	it('renders the branch verdict with the value the condition actually read', () => {
		const { body } = render(FlowDryRun, {
			props: {
				result: simulation({
					path: [
						hop({
							node_id: 'risk',
							kind: 'branch',
							title: 'Offshore?',
							instances: [],
							taken_arm: 'b1',
							arms: [
								{
									id: 'b1',
									label: 'Offshore',
									is_else: false,
									next: 'edd',
									reached: true,
									passed: true,
									taken: true,
									explanation: {
										kind: 'leaf',
										field: 'account.custody',
										operator: 'equals',
										value: 'offshore',
										actual: 'offshore',
										passed: true
									}
								},
								{
									id: 'b2',
									label: 'Otherwise',
									is_else: true,
									next: 'notify',
									reached: true,
									passed: true,
									taken: false,
									explanation: null
								}
							]
						})
					]
				}),
				actionName: 'dryRun',
				enhance: noop
			}
		});
		expect(body).toContain('Offshore');
		expect(body).toContain('actual: offshore');
		expect(body).toContain('otherwise');
	});

	it('warns about an ending the author did not intend', () => {
		const { body } = render(FlowDryRun, {
			props: {
				result: simulation({
					outcome: {
						kind: 'looped',
						node_id: 'a',
						detail: 'The journey comes back to a node it has already run.',
						elapsed_days: 0
					}
				})
			}
		});
		expect(body).toContain('loops back on itself');
	});

	it('separates a node nothing leads to from an arm this client simply did not take', () => {
		const { body } = render(FlowDryRun, {
			props: {
				result: simulation({
					excluded: [
						{
							node_id: 'edd',
							kind: 'step',
							title: 'EDD',
							reason: 'branch_not_taken',
							because_of: 'risk'
						},
						{
							node_id: 'lost',
							kind: 'step',
							title: 'Orphan step',
							reason: 'orphan',
							because_of: null
						}
					]
				})
			}
		});
		expect(body).toContain('Some steps can’t be reached at all');
		expect(body).toContain('Nothing leads here');
		expect(body).toContain('On a path this client doesn’t take');
	});

	it('heads the list “What applies” for a legacy definition, and “The path” for a graph', () => {
		// Only a pre-v3 definition reports the PLAN — it authored no sequence. A v3 graph is wired, so
		// it has a path to head.
		const legacy = render(FlowDryRun, { props: { result: simulation({ shape: 'legacy' }) } });
		expect(legacy.body).toContain('What applies');
		expect(legacy.body).not.toContain('The path');
		expect(legacy.body).toContain('predates the graph builder');
		const graph = render(FlowDryRun, { props: { result: simulation({ shape: 'graph' }) } });
		expect(graph.body).toContain('The path');
		expect(graph.body).not.toContain('predates the graph builder');
	});

	it('renders an empty state when the journey does nothing for this client', () => {
		const { body } = render(FlowDryRun, {
			props: { result: simulation({ path: [], tiers: { ...simulation().tiers, cases: 0 } }) }
		});
		expect(body).toContain('Nothing happens for this client');
	});

	it('surfaces publish issues on the definition it simulated', () => {
		const { body } = render(FlowDryRun, {
			props: { result: simulation({ issues: ['Branch “Offshore?” has no fallback.'] }) }
		});
		expect(body).toContain('can’t be published yet');
		expect(body).toContain('has no fallback');
	});
});

describe('FlowFunnel', () => {
	it('takes every href from the injected links builder', () => {
		const { body } = render(FlowFunnel, { props: { insights: insights(), links } });
		expect(body).toContain('/x/journeys/flw_1');
		expect(body).toContain('/x/journeys/flw_1/insights?v=fv_1');
		// Not the platform's own path — that would be a broken link in the admin portal.
		expect(body).not.toContain('href="/flows/');
	});

	it('shows each version with its cohort size, so the two aren’t compared blind', () => {
		const { body } = render(FlowFunnel, { props: { insights: insights(), links } });
		expect(body).toContain('v2 · live · 10 journeys');
		expect(body).toContain('v1 · archived · 4 journeys');
	});

	it('renders reach, completion and the status split per node', () => {
		const { body } = render(FlowFunnel, { props: { insights: insights(), links } });
		expect(body).toContain('Fact find');
		expect(body).toContain('10');
		expect(body).toContain('70% completed');
		expect(body).toContain('Waiting');
	});

	it('names a branch’s arms with how many journeys took each', () => {
		const { body } = render(FlowFunnel, {
			props: {
				insights: insights({
					nodes: [
						node({
							node_id: 'risk',
							kind: 'branch',
							title: 'Offshore?',
							is_work: false,
							reached: 10,
							completed: 10,
							by_status: {},
							arms: [
								{ id: 'b1', label: 'Offshore', is_else: false, next: 'edd', count: 2 },
								{ id: 'b2', label: 'Otherwise', is_else: true, next: 'notify', count: 8 }
							]
						})
					]
				}),
				links
			}
		});
		expect(body).toContain('Offshore');
		expect(body).toContain('80%');
	});

	it('names the WINDOW in the empty cohort, and offers widening it', () => {
		// It used to say "No journeys started on this version yet" — an answer about the version,
		// for a page whose cohort is a version AND a window. A version last used four months ago
		// reported, under a 30-day window, that it had never run.
		const empty = insights({ cases: { total: 0, by_status: {}, median_seconds: null } });
		const { body } = render(FlowFunnel, { props: { insights: empty, links, days: 30 } });
		expect(body).toContain('No journeys started in this window');
		expect(body).toContain('in the last 30 days');
		expect(body).toContain('Look back 12 months');
	});

	it('withholds the widen action when the reader is already on the widest window', () => {
		// A control that cannot change the answer is worse than no control.
		const empty = insights({ cases: { total: 0, by_status: {}, median_seconds: null } });
		const { body } = render(FlowFunnel, { props: { insights: empty, links, days: 365 } });
		expect(body).toContain('in the last 12 months');
		expect(body).not.toContain('Look back 12 months');
	});

	it('renders an empty state, not a broken page, when the journey has no versions', () => {
		const { body } = render(FlowFunnel, { props: { insights: null, links } });
		expect(body).toContain('Nothing to report yet');
	});

	it('distinguishes a failed load from no data', () => {
		const { body } = render(FlowFunnel, { props: { insights: null, links, loadFailed: true } });
		expect(body).toContain('couldn’t load');
		expect(body).not.toContain('Nothing to report yet');
	});

	it('says a legacy version has no authored order rather than inventing drop-off', () => {
		const { body } = render(FlowFunnel, {
			props: { insights: insights({ shape: 'legacy', edges: [] }), links }
		});
		expect(body).toContain('predates the graph builder');
	});

	it('has no mutating control anywhere — this surface is a report', () => {
		const { body } = render(FlowFunnel, { props: { insights: insights(), links } });
		expect(body).not.toContain('<form');
		expect(body).not.toContain('method="POST"');
	});
});
