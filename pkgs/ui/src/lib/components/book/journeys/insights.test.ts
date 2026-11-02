/**
 * The funnel's presentation arithmetic.
 *
 * Every one of these is a judgement about how a number should READ, and each has a wrong answer
 * that looks plausible: 0% for a node nobody reached, a negative drop-off into a re-convergent
 * node, "0s" for a step nobody has completed. Those are the cases here.
 */
import { describe, expect, it } from 'vitest';
import {
	barWidth,
	completionRate,
	durationLabel,
	edgesByTarget,
	lostInto,
	outstanding,
	percent,
	rangeFor,
	versionLabel,
	worstNode,
	type FunnelEdge,
	type FunnelNode,
	type FunnelVersion
} from './insights.js';

const node = (over: Partial<FunnelNode> = {}): FunnelNode => ({
	node_id: 'n1',
	kind: 'step',
	type: 'fill_form',
	title: 'Fact find',
	scope: 'participant',
	reached: 10,
	completed: 8,
	by_status: { fulfilled: 8, pending: 2 },
	instances: 10,
	median_seconds: 3600,
	p90_seconds: 86400,
	is_work: true,
	...over
});

describe('durationLabel', () => {
	it('picks the coarsest unit that still says something', () => {
		expect(durationLabel(45)).toBe('45s');
		expect(durationLabel(600)).toBe('10 min');
		expect(durationLabel(3600 * 5)).toBe('5 hours');
		expect(durationLabel(86_400 * 4)).toBe('4 days');
		expect(durationLabel(86_400 * 40)).toBe('6 weeks');
		expect(durationLabel(86_400 * 200)).toBe('7 months');
	});

	it('has no answer for a step nobody has completed', () => {
		// "0s" would claim the step is instant; the truth is that there is no measurement.
		expect(durationLabel(null)).toBeNull();
		expect(durationLabel(undefined)).toBeNull();
	});
});

describe('completionRate', () => {
	it('is a share of the cases that reached the node', () => {
		expect(completionRate(node({ reached: 10, completed: 4 }))).toBe(0.4);
	});

	it('is null — not zero — for a node nobody reached', () => {
		// 0% reads as a failure. "No journeys have got here yet" is a different statement.
		expect(completionRate(node({ reached: 0, completed: 0 }))).toBeNull();
		expect(percent(null)).toBe('—');
	});
});

describe('outstanding', () => {
	it('counts the cases stuck in the node', () => {
		expect(outstanding(node({ reached: 10, completed: 6, by_status: { fulfilled: 6 } }))).toBe(4);
	});

	it('does not count steps the firm’s own conditions correctly skipped', () => {
		// Scoring a firm down for an offshore form their onshore clients never needed would make
		// the number meaningless.
		const n = node({ reached: 10, completed: 6, by_status: { fulfilled: 6, skipped: 4 } });
		expect(outstanding(n)).toBe(0);
	});

	it('never goes negative', () => {
		expect(outstanding(node({ reached: 3, completed: 9, by_status: {} }))).toBe(0);
	});
});

describe('barWidth', () => {
	it('scales against the busiest node so volumes are comparable', () => {
		const nodes = [node({ node_id: 'a', reached: 100 }), node({ node_id: 'b', reached: 25 })];
		expect(barWidth(nodes[0]!, nodes)).toBe(100);
		expect(barWidth(nodes[1]!, nodes)).toBe(25);
	});

	it('does not divide by zero on an empty cohort', () => {
		const nodes = [node({ reached: 0 })];
		expect(barWidth(nodes[0]!, nodes)).toBe(0);
	});
});

describe('worstNode', () => {
	it('names the node with the most cases stuck in it', () => {
		const nodes = [
			node({ node_id: 'a', reached: 10, completed: 9, by_status: { fulfilled: 9 } }),
			node({ node_id: 'b', reached: 10, completed: 2, by_status: { fulfilled: 2 } })
		];
		expect(worstNode(nodes)?.node_id).toBe('b');
	});

	it('is null when nothing is stuck — a real answer, not an empty state', () => {
		expect(worstNode([node({ reached: 5, completed: 5, by_status: { fulfilled: 5 } })])).toBeNull();
	});

	it('ignores control nodes, which nobody is ever stuck "in"', () => {
		const nodes = [node({ node_id: 'branch', kind: 'branch', is_work: false, completed: 0 })];
		expect(worstNode(nodes)).toBeNull();
	});
});

describe('edge indexing', () => {
	const edges: FunnelEdge[] = [
		{ from: 'a', to: 'c', completed: 10, reached: 6, lost: 4, gained: 0, rate: 0.6 },
		{ from: 'b', to: 'c', completed: 5, reached: 6, lost: 1, gained: 1, rate: 1.2 }
	];

	it('sums the loss into a node across all its predecessors', () => {
		// A node with two ways in loses from both, and reporting one would understate it.
		expect(lostInto('c', edgesByTarget(edges))).toBe(5);
	});

	it('reports no loss into a node nothing points at', () => {
		expect(lostInto('a', edgesByTarget(edges))).toBe(0);
	});
});

describe('versionLabel', () => {
	const version = (over: Partial<FunnelVersion> = {}): FunnelVersion => ({
		uid: 'fv1',
		version: 3,
		status: 'published',
		published_at: '2026-07-01T00:00:00Z',
		case_count: 41,
		is_current: true,
		...over
	});

	it('carries the cohort size, because comparing 400 cases against 3 is the mistake', () => {
		expect(versionLabel(version())).toBe('v3 · live · 41 journeys');
		expect(versionLabel(version({ is_current: false, status: 'draft', case_count: 1 }))).toBe(
			'v3 · draft · 1 journey'
		);
	});
});

describe('rangeFor', () => {
	it('bounds the window from a caller-supplied now, so it is testable', () => {
		const now = new Date('2026-08-04T09:00:00.000Z');
		expect(rangeFor(30, now)).toEqual({
			since: '2026-07-05T09:00:00.000Z',
			until: '2026-08-04T09:00:00.000Z'
		});
	});
});
