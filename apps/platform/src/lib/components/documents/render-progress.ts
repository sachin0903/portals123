/**
 * What to tell an operator while a document renders.
 *
 * The old screen was one spinner and one sentence for the whole wait — up to two minutes, with the
 * same words at second 1 and second 119. When the render worker was not running, that spinner was
 * indistinguishable from normal progress, so a stuck document looked like a slow one.
 *
 * EVERY STEP HERE IS A STATE THE SERVER ACTUALLY REPORTS. `documents.status` already distinguishes
 * `queued` (nobody has picked it up) from `generating` (a worker has claimed it and is rendering),
 * and those are the two halves of the wait that fail for completely different reasons — a stuck
 * `queued` means the worker is down, a stuck `generating` means the render itself is struggling.
 * Collapsing them into one spinner threw away the only diagnostic the operator had.
 *
 * There is deliberately NO time-based progress. A bar that advances on a timer while nothing
 * happens is a lie, and it is exactly the lie that made a broken worker look like a slow one.
 * Progress advances only when the server says the phase changed.
 */

/** The document statuses this flow can observe while waiting. */
export type RenderStatus = 'submitting' | 'queued' | 'generating' | 'generated' | 'failed';

export type StepState = 'done' | 'active' | 'pending';

export interface RenderStep {
	key: 'submitting' | 'queued' | 'generating' | 'generated';
	label: string;
	/** What is happening, in the operator's terms. Shown for the ACTIVE step only. */
	detail: string;
	state: StepState;
}

const ORDER: RenderStep['key'][] = ['submitting', 'queued', 'generating', 'generated'];

const COPY: Record<RenderStep['key'], { label: string; detail: string }> = {
	submitting: { label: 'Sending', detail: 'Sending your answers.' },
	queued: { label: 'Waiting', detail: 'Your document is waiting to be prepared.' },
	generating: { label: 'Preparing', detail: 'Building the PDF from your template.' },
	generated: { label: 'Ready', detail: 'Opening your document.' }
};

/**
 * How long a document may sit in one phase before the wait is worth explaining.
 *
 * `queued` is the short one ON PURPOSE: a worker that is running claims a job within a poll
 * interval, so more than a few seconds queued almost always means it is not running at all. Saying
 * so at 15 seconds rather than at the 2-minute timeout is the difference between "check the worker"
 * and two minutes of watching a spinner.
 */
export const STALL_MS: Record<'queued' | 'generating', number> = {
	queued: 15_000,
	generating: 45_000
};

/** Where the current status sits in the sequence. `failed` keeps the phase it failed in. */
function indexOf(status: RenderStatus): number {
	if (status === 'failed') return -1;
	return ORDER.indexOf(status as RenderStep['key']);
}

export function steps(status: RenderStatus): RenderStep[] {
	const at = indexOf(status);
	return ORDER.map((key, i) => ({
		key,
		label: COPY[key].label,
		detail: COPY[key].detail,
		// A failed render leaves every step pending rather than claiming any of them finished: the
		// screen that replaces this one carries the reason, and a row of ticks above a failure reads
		// as though the work completed.
		state: at < 0 ? 'pending' : i < at ? 'done' : i === at ? 'active' : 'pending'
	}));
}

/**
 * The share of the sequence completed, 0–1, for the bar.
 *
 * Derived from the phase, never from elapsed time. The active phase counts as half so the bar moves
 * when a phase STARTS as well as when it ends — a bar that only moves on completion sits still for
 * the whole of the longest step.
 */
export function fraction(status: RenderStatus): number {
	const at = indexOf(status);
	if (at < 0) return 0;
	if (status === 'generated') return 1;
	return (at + 0.5) / ORDER.length;
}

/**
 * The note to show when a phase has run long, or `null`.
 *
 * It names the likely cause rather than apologising, because in both cases there is something an
 * operator can actually do — and in the `queued` case the cause is nearly always a worker that is
 * not running, which no amount of waiting fixes.
 */
export function stallNote(status: RenderStatus, elapsedMs: number): string | null {
	if (status === 'queued' && elapsedMs >= STALL_MS.queued) {
		return 'This document has not started yet. You can leave this page and check the documents list shortly. Ask your administrator for help if it remains there.';
	}
	if (status === 'generating' && elapsedMs >= STALL_MS.generating) {
		return 'Still rendering. A long template with several charts can take a while; you can leave this page and find the document in the library when it is done.';
	}
	return null;
}

/** Whole seconds, for a readout that ticks. */
export function elapsedLabel(elapsedMs: number): string {
	const total = Math.max(0, Math.floor(elapsedMs / 1000));
	if (total < 60) return `${total}s`;
	const mins = Math.floor(total / 60);
	const secs = total % 60;
	return `${mins}m ${String(secs).padStart(2, '0')}s`;
}
