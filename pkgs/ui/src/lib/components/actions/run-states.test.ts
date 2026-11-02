/**
 * The one label/tone map for a run's status, and the next-action rule the detail and the panel
 * share. Pinned because every surface defers to these rather than re-deciding a colour.
 */
import { describe, expect, it } from 'vitest';
import { nextAction } from './next-action.js';
import { refusedRun, run } from './run-fixtures.js';
import {
	attemptOutcomeTone,
	formatDuration,
	runCanRetry,
	runInFlight,
	runStatusLabel,
	runStatusTone,
	untilTime
} from './run-states.js';

describe('run states', () => {
	it('labels every word the API folds to', () => {
		for (const word of [
			'running',
			'waiting',
			'awaiting_approval',
			'succeeded',
			'partial',
			'skipped',
			'needs_attention',
			'conflicted',
			'timed_out',
			'failed',
			'rejected',
			'cancelled'
		]) {
			expect(runStatusLabel(word)).not.toBe(word);
		}
	});

	it('colours by what a person can do: a parked run is a warning, a stopped one is danger', () => {
		expect(runStatusTone('needs_attention')).toBe('warning');
		expect(runStatusTone('awaiting_approval')).toBe('warning');
		expect(runStatusTone('failed')).toBe('danger');
		expect(runStatusTone('timed_out')).toBe('danger');
		expect(runStatusTone('cancelled')).toBe('neutral');
		expect(runStatusTone('waiting')).toBe('info');
		expect(runStatusTone('succeeded')).toBe('success');
	});

	it('keeps asking while the engine or a provider has the run, not while a person does', () => {
		expect(runInFlight('running')).toBe(true);
		expect(runInFlight('waiting')).toBe(true);
		expect(runInFlight('awaiting_approval')).toBe(false);
		expect(runInFlight('needs_attention')).toBe(false);
	});

	it('offers a retry for the words that mean stopped', () => {
		expect(runCanRetry('needs_attention')).toBe(true);
		expect(runCanRetry('failed')).toBe(true);
		expect(runCanRetry('succeeded')).toBe(false);
		expect(runCanRetry('cancelled')).toBe(false);
	});

	it('never paints a wait or a retry red', () => {
		expect(attemptOutcomeTone('wait')).toBe('neutral');
		expect(attemptOutcomeTone('retry')).toBe('info');
		expect(attemptOutcomeTone('failed')).toBe('warning');
		expect(attemptOutcomeTone('crashed')).toBe('danger');
	});

	it('formats durations on one scale', () => {
		expect(formatDuration(21)).toBe('21 ms');
		expect(formatDuration(1800)).toBe('1.8 s');
		expect(formatDuration(14000)).toBe('14 s');
		expect(formatDuration(122000)).toBe('2m 02s');
		expect(formatDuration(null)).toBe('—');
	});

	it('says when a wait polls again', () => {
		const now = new Date('2026-10-21T13:11:31.000Z').getTime();
		expect(untilTime('2026-10-21T13:11:40.000Z', now)).toBe('in 9 s');
		expect(untilTime('2026-10-21T13:11:00.000Z', now)).toBe('now');
	});
});

describe('next action', () => {
	it('is Retry from the node that stopped, for a parked run', () => {
		const next = nextAction(refusedRun());
		expect(next.kind).toBe('retry');
		expect(next.label).toBe('Retry');
		expect(next.hint).toContain('nothing before it');
	});

	it('is Approve for an approver of a held run, and nothing for anyone else', () => {
		const held = run({
			status: 'awaiting_approval',
			approval: { required: true, roles: ['Compliance'], gate_uid: 'g', can_decide: true },
			can_cancel: true
		});
		expect(nextAction(held).kind).toBe('decide');
		expect(nextAction(held).canCancel).toBe(true);
		const theirs = run({
			status: 'awaiting_approval',
			approval: { required: true, roles: ['Compliance'], gate_uid: 'g', can_decide: false },
			can_cancel: true
		});
		expect(nextAction(theirs).kind).toBe('none');
		expect(nextAction(theirs).hint).toContain('Compliance');
	});

	it('is nothing for a finished run', () => {
		const next = nextAction(run());
		expect(next.kind).toBe('none');
		expect(next.canCancel).toBe(false);
	});
});

describe('node duration', () => {
	it('reads a finished leaf with no ended_at off its last attempt, not the clock', async () => {
		const { nodeDurationMs } = await import('./run-states.js');
		const { node } = await import('./run-fixtures.js');
		const now = new Date('2026-10-21T22:40:00.000Z').getTime();
		const stale = node({ ended_at: null });
		expect(nodeDurationMs(stale, now)).toBe(5);
		const live = node({
			ended_at: null,
			state: { type: 'running', name: 'Running', message: null },
			started_at: '2026-10-21T22:39:50.000Z'
		});
		expect(nodeDurationMs(live, now)).toBe(10_000);
	});
});
