import { describe, it, expect } from 'vitest';
import type { FlowNode } from '@graphene/ui/book/journeys';
import {
	branchOrdinal,
	cfgString,
	describeWait,
	nodeLabel,
	NO_TARGET,
	PROVIDER_OPTIONS,
	providerItems,
	setCfgNumber,
	setCfgString,
	targetId,
	targetOptions,
	targetValue
} from './shared';

const node = (over: Partial<FlowNode> & { id: string }): FlowNode => ({
	kind: 'step',
	title: '',
	config: {},
	position: null,
	depends_on: [],
	...over
});

describe('naming a node', () => {
	it('uses the author’s title', () => {
		expect(nodeLabel(node({ id: 'a', title: 'Fact find' }))).toBe('Fact find');
	});

	it('falls back to the kind, never a raw slug the author never typed', () => {
		expect(nodeLabel(node({ id: 'wait_1', kind: 'wait' }))).toBe('Wait');
		expect(nodeLabel(node({ id: 'x', kind: 'branch', title: '   ' }))).toBe('Branch');
	});
});

describe('the target picker', () => {
	const nodes = [
		node({ id: 'a', title: 'Fact find' }),
		node({ id: 'b', kind: 'exit', title: 'Done' })
	];

	it('offers every node but this one, plus “nowhere”', () => {
		expect(targetOptions(nodes, 'a')).toEqual([
			{ value: NO_TARGET, label: 'Nothing yet' },
			{ value: 'b', label: 'Done' }
		]);
	});

	it('never offers a node itself — a one-node loop is a case that can never finish', () => {
		expect(targetOptions(nodes, 'a').some((o) => o.value === 'a')).toBe(false);
	});

	it('round-trips “nowhere” through the sentinel', () => {
		expect(targetValue(null)).toBe(NO_TARGET);
		expect(targetId(NO_TARGET)).toBe(null);
		expect(targetId('b')).toBe('b');
	});
});

describe('writing config', () => {
	it('deletes a key rather than storing a blank', () => {
		// The engine branches on `is not None`, so an empty string is the difference between "no
		// deadline" and "a deadline of nothing".
		const n = node({ id: 'a', config: { until_date_field: 'memory.due' } });
		setCfgString(n, 'until_date_field', '  ');
		expect('until_date_field' in n.config).toBe(false);
	});

	it('trims what it stores', () => {
		const n = node({ id: 'a' });
		setCfgString(n, 'reason', '  withdrew  ');
		expect(n.config.reason).toBe('withdrew');
	});

	it('stores a number as a number', () => {
		const n = node({ id: 'a' });
		setCfgNumber(n, 'timeout_days', '7');
		expect(n.config.timeout_days).toBe(7);
	});

	it('keeps a non-numeric entry as text so the engine’s own message is what the author reads', () => {
		const n = node({ id: 'a' });
		setCfgNumber(n, 'timeout_days', 'soon');
		expect(n.config.timeout_days).toBe('soon');
	});

	it('reads a missing key as an empty string', () => {
		expect(cfgString(node({ id: 'a' }), 'nope')).toBe('');
	});
});

describe('saying what a wait does', () => {
	it('reads as a sentence', () => {
		expect(describeWait({ duration_days: 3 })).toBe('Pauses for 3 days, then continues.');
		expect(describeWait({ duration_days: 1, duration_hours: 12 })).toBe(
			'Pauses for 1 day and 12 hours, then continues.'
		);
		expect(describeWait({ duration_hours: 1 })).toBe('Pauses for 1 hour, then continues.');
	});

	it('says so when it says nothing', () => {
		expect(describeWait({})).toContain('will not publish');
	});

	it('names the field when waiting for a date', () => {
		expect(describeWait({ until_date_field: 'memory.review_due' })).toBe(
			'Pauses until the date in memory.review_due, then continues.'
		);
	});

	it('says the duration wins when a document carries both', () => {
		// `timers.wait_until`: a duration is the more specific instruction and beats a date field.
		// Reading it the other way round would tell an author the opposite of what runs.
		expect(describeWait({ until_date_field: 'memory.review_due', duration_days: 3 })).toBe(
			'Pauses for 3 days, then continues. The date in memory.review_due is ignored while a length is set.'
		);
	});
});

describe('the verify_identity provider picker', () => {
	it('offers manual and ComplyCube when nothing retired is stored', () => {
		expect(providerItems('')).toEqual(PROVIDER_OPTIONS);
		expect(providerItems('manual')).toEqual(PROVIDER_OPTIONS);
		expect(providerItems('complycube')).toEqual(PROVIDER_OPTIONS);
	});

	it('still shows a retired vendor rather than silently rewriting it', () => {
		expect(providerItems('idenfy')).toEqual([
			...PROVIDER_OPTIONS,
			{ value: 'idenfy', label: 'idenfy (retired)' }
		]);
		expect(providerItems('creditsafe')).toEqual([
			...PROVIDER_OPTIONS,
			{ value: 'creditsafe', label: 'creditsafe (retired)' }
		]);
	});
});

describe('numbering the arms of a branch', () => {
	it('reads as prose so first-match-wins is visible', () => {
		expect(branchOrdinal(0, false)).toBe('1st · if');
		expect(branchOrdinal(1, false)).toBe('2nd · otherwise if');
		expect(branchOrdinal(2, false)).toBe('3rd · otherwise if');
		expect(branchOrdinal(-1, true)).toBe('Otherwise');
	});
});
