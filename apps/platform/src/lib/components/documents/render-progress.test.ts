import { describe, expect, it } from 'vitest';
import {
	STALL_MS,
	elapsedLabel,
	fraction,
	stallNote,
	steps,
	type RenderStatus
} from './render-progress';

describe('render progress steps', () => {
	it('marks earlier phases done, the current one active and later ones pending', () => {
		const s = steps('generating');
		expect(s.map((x) => x.state)).toEqual(['done', 'done', 'active', 'pending']);
	});

	it('has every phase finished once the document is generated', () => {
		expect(steps('generated').map((x) => x.state)).toEqual(['done', 'done', 'done', 'active']);
		expect(fraction('generated')).toBe(1);
	});

	it('claims no phase finished on a failure', () => {
		// A row of ticks above a failure reads as though the work completed. The screen that
		// replaces this one carries the reason.
		expect(steps('failed').every((x) => x.state === 'pending')).toBe(true);
		expect(fraction('failed')).toBe(0);
	});

	it('advances the bar when a phase STARTS, not only when it ends', () => {
		// A bar that moves only on completion sits still through the whole of the longest step.
		const submitting = fraction('submitting');
		const queued = fraction('queued');
		const generating = fraction('generating');
		expect(submitting).toBeGreaterThan(0);
		expect(queued).toBeGreaterThan(submitting);
		expect(generating).toBeGreaterThan(queued);
		expect(generating).toBeLessThan(1);
	});

	it('never derives progress from elapsed time', () => {
		// The whole point: a bar that advances on a timer made a dead worker look like a slow one.
		// `fraction` takes no time argument at all, so this is a statement about the signature.
		expect(fraction.length).toBe(1);
		expect(fraction('queued')).toBe(fraction('queued'));
	});
});

describe('stall notes', () => {
	it('says nothing while a phase is still within its normal time', () => {
		expect(stallNote('queued', 0)).toBeNull();
		expect(stallNote('queued', STALL_MS.queued - 1)).toBeNull();
		expect(stallNote('generating', STALL_MS.generating - 1)).toBeNull();
	});

	it('gives the user a next step when a document has not started', () => {
		// The failure this exists for: with the worker down, `queued` never moves, and the old
		// screen said nothing for two minutes.
		const note = stallNote('queued', STALL_MS.queued);
		expect(note).toContain('check the documents list');
	});

	it('warns about a long queue sooner than a long render', () => {
		// A worker that is running claims a job within a poll interval, so seconds spent queued mean
		// something different from seconds spent rendering.
		expect(STALL_MS.queued).toBeLessThan(STALL_MS.generating);
	});

	it('tells the operator they can leave the page in both cases', () => {
		expect(stallNote('queued', STALL_MS.queued)).toContain('leave this page');
		expect(stallNote('generating', STALL_MS.generating)).toContain('leave this page');
	});

	it('says nothing for a phase that cannot stall', () => {
		for (const status of ['submitting', 'generated', 'failed'] as RenderStatus[]) {
			expect(stallNote(status, 10 * 60_000)).toBeNull();
		}
	});
});

describe('elapsed label', () => {
	it('counts seconds under a minute', () => {
		expect(elapsedLabel(0)).toBe('0s');
		expect(elapsedLabel(1_500)).toBe('1s');
		expect(elapsedLabel(59_999)).toBe('59s');
	});

	it('switches to minutes and pads the seconds', () => {
		expect(elapsedLabel(60_000)).toBe('1m 00s');
		expect(elapsedLabel(65_000)).toBe('1m 05s');
		expect(elapsedLabel(125_000)).toBe('2m 05s');
	});

	it('never shows a negative time', () => {
		// Clock skew between the page and the server should not print "-3s".
		expect(elapsedLabel(-5_000)).toBe('0s');
	});
});
