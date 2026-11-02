import { describe, expect, it } from 'vitest';
import { isFullyScreened, screeningRows, type ScreeningCheck } from './screening';

function check(over: Partial<ScreeningCheck> = {}): ScreeningCheck {
	return {
		kind: 'aml',
		status: 'passed',
		provider: 'complycube',
		verified_at: '2026-01-01T00:00:00Z',
		valid_until: '2027-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		...over
	};
}

describe('screeningRows', () => {
	it('always returns both rails, identity first', () => {
		const rows = screeningRows([]);
		expect(rows.map((r) => r.kind)).toEqual(['identity', 'aml']);
	});

	/** An empty card would read as "nothing required"; the truth is "not started". */
	it('reports a missing check as Not run and needing attention', () => {
		const [identity] = screeningRows([]);
		expect(identity.statusLabel).toBe('Not run');
		expect(identity.tone).toBe('neutral');
		expect(identity.needsAttention).toBe(true);
	});

	it('reports a journey/manual pass as Verified, not a provider Clear', () => {
		const rows = screeningRows([check({ kind: 'aml', provider: 'manual' })]);
		const aml = rows.find((r) => r.kind === 'aml')!;
		expect(aml.statusLabel).toBe('Verified (journey)');
		expect(aml.detail).toMatch(/journey \/ manual/);
	});

	/** Identity/AML passes no longer lapse: a stored `passed` status reads as passed regardless of
	 *  how far in the past `valid_until` sits (the API no longer writes it going forward, but old
	 *  rows still carry one). */
	it('reads a passed check as Clear even with an old valid_until in the past', () => {
		const rows = screeningRows([check({ kind: 'aml', valid_until: '2020-01-01T00:00:00Z' })]);
		const aml = rows.find((r) => r.kind === 'aml')!;
		expect(aml.statusLabel).toBe('Clear');
		expect(aml.tone).toBe('success');
		expect(aml.needsAttention).toBe(false);
	});

	it('maps pending, referred and failed to their own tones', () => {
		const tone = (status: ScreeningCheck['status']) =>
			screeningRows([check({ kind: 'aml', status, valid_until: null })]).find(
				(r) => r.kind === 'aml'
			)!;
		expect(tone('pending').statusLabel).toBe('In progress');
		expect(tone('pending').needsAttention).toBe(false);
		expect(tone('referred').tone).toBe('warning');
		expect(tone('referred').needsAttention).toBe(true);
		expect(tone('failed').tone).toBe('danger');
		expect(tone('failed').needsAttention).toBe(true);
	});

	/** `expired` is a pass of details that have changed, or a check that never finished. It
	 *  reads as not started: "Lapsed" made a re-screen look time-expired. */
	it('reports an expired check as not run', () => {
		const rows = screeningRows([check({ kind: 'aml', status: 'expired', valid_until: null })]);
		const aml = rows.find((r) => r.kind === 'aml')!;
		expect(aml.statusLabel).toBe('Not run');
		expect(aml.tone).toBe('neutral');
		expect(aml.detail).toBeNull();
		expect(aml.needsAttention).toBe(true);
	});

	/** The API returns the latest attempt per kind, but be defensive: if two of a kind ever arrive,
	 *  take the first rather than rendering a duplicate rail. */
	it('collapses duplicates of a kind', () => {
		const rows = screeningRows([
			check({ kind: 'aml', status: 'passed' }),
			check({ kind: 'aml', status: 'failed' })
		]);
		expect(rows.filter((r) => r.kind === 'aml')).toHaveLength(1);
		expect(rows.find((r) => r.kind === 'aml')!.statusLabel).toBe('Clear');
	});

	it('tolerates an absent kyc field', () => {
		expect(screeningRows(undefined)).toHaveLength(2);
	});
});

describe('isFullyScreened', () => {
	it('is true only when both rails are currently clear', () => {
		const both = screeningRows([check({ kind: 'identity' }), check({ kind: 'aml' })]);
		expect(isFullyScreened(both)).toBe(true);
		expect(isFullyScreened(screeningRows([check({ kind: 'aml' })]))).toBe(false);
	});

	it('is false when one rail has never run', () => {
		const rows = screeningRows([check({ kind: 'identity' })]);
		expect(isFullyScreened(rows)).toBe(false);
	});
});
