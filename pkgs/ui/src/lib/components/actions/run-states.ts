/**
 * The ONE label/tone map for a baton run's state, so the runs listing, the run detail, the panel
 * and the node tree all badge a status the same way instead of each re-deciding it.
 *
 * Two vocabularies arrive from the API (`app/actions/queries.py`): the `status` word — baton's
 * (state_type, state_name) folded once, server-side, for filtering and counting — and, on a node,
 * baton's own `state` (type + name). The badge labels by the word; the tone follows baton's rule of
 * colouring by TYPE: completed is good, failed is a warning when a person can fix it and danger
 * when the run stopped, cancelled is neutral, anything still moving is info. `StatusBadge`'s tone
 * is the canonical colour map (`ui/status-badge/tones.ts`); this module only chooses which tone
 * each word takes.
 */
import type { StatusTone } from '../ui/status-badge/index.js';
import type { RunAttempt } from './run-types.js';

const LABELS: Record<string, string> = {
	running: 'Running',
	waiting: 'Waiting',
	awaiting_approval: 'Awaiting approval',
	succeeded: 'Succeeded',
	partial: 'Partly done',
	skipped: 'Skipped',
	needs_attention: 'Needs attention',
	conflicted: 'Conflicted',
	timed_out: 'Timed out',
	failed: 'Failed',
	rejected: 'Rejected',
	cancelled: 'Cancelled',
	// the legacy word the old engine minted; never produced now, kept so a stale row still reads
	queued: 'Queued'
};

/** The human label for a status word, falling back to the word itself for anything unmapped. */
export function runStatusLabel(status: string): string {
	return LABELS[status] ?? status;
}

/** The StatusBadge tone for a status word. */
export function runStatusTone(status: string): StatusTone {
	switch (status) {
		case 'succeeded':
			return 'success';
		case 'partial':
		case 'needs_attention':
		case 'awaiting_approval':
			return 'warning';
		case 'failed':
		case 'conflicted':
		case 'timed_out':
		case 'rejected':
			return 'danger';
		case 'running':
		case 'waiting':
		case 'queued':
			return 'info';
		default:
			return 'neutral';
	}
}

/** The words that mean "stopped, and a person may start it again". */
export function runCanRetry(status: string): boolean {
	return status === 'needs_attention' || status === 'failed' || status === 'timed_out';
}

/** A run the engine still has in hand or is resting on a provider, so the page keeps asking for
 *  it. Awaiting approval is a person, not the engine, so it does not count. */
export function runInFlight(status: string): boolean {
	return status === 'queued' || status === 'running' || status === 'waiting';
}

/** A terminal word: nothing further will happen to this run without a person. */
export function runSettled(status: string): boolean {
	return !runInFlight(status) && status !== 'awaiting_approval';
}

/** The fill class for one segment of the step bar, from a child's status word. */
export function stepToneClass(status: string): string {
	switch (runStatusTone(status)) {
		case 'success':
			return 'bg-success';
		case 'warning':
			return 'bg-warning';
		case 'danger':
			return 'bg-destructive';
		case 'info':
			return 'bg-info';
		default:
			return 'bg-muted-foreground/30';
	}
}

/** The badge for one attempt's outcome. A wait or a retry is a fact about timing, not a failure,
 *  and must not wear red: the old projection folded both to `ok=false`. */
export function attemptOutcomeLabel(outcome: RunAttempt['outcome']): string {
	switch (outcome) {
		case 'ok':
			return 'OK';
		case 'skipped':
			return 'Skipped';
		case 'wait':
			return 'Waited';
		case 'retry':
			return 'Retry';
		case 'failed':
			return 'Failed';
		case 'timed_out':
			return 'Timed out';
		case 'crashed':
			return 'Crashed';
	}
}

export function attemptOutcomeTone(outcome: RunAttempt['outcome']): StatusTone {
	switch (outcome) {
		case 'ok':
			return 'success';
		case 'skipped':
		case 'wait':
			return 'neutral';
		case 'retry':
			return 'info';
		case 'failed':
			return 'warning';
		case 'timed_out':
		case 'crashed':
			return 'danger';
	}
}

/** The tone for an HTTP status code chip. */
export function httpTone(code: number): StatusTone {
	if (code >= 200 && code < 300) return 'success';
	if (code >= 400 && code < 500) return 'warning';
	if (code >= 500) return 'danger';
	return 'info';
}

const PROVIDERS: Record<string, string> = {
	sei: 'SEI',
	seccl: 'Seccl',
	'morgan-lloyd': 'Morgan Lloyd',
	io: 'IO'
};

/** The provider's display name; Graphene for a node with none (a book read, a resolver). */
export function providerLabel(code: string | null | undefined): string {
	if (!code) return 'Graphene';
	return PROVIDERS[code] ?? code;
}

const EVENT_LABELS: Record<string, string> = {
	'run.created': 'Created',
	'run.held': 'Held for approval',
	'run.approved': 'Approved',
	'run.rejected': 'Rejected',
	'run.cancelled': 'Cancelled',
	'run.retry_requested': 'Retry requested',
	'run.reminded': 'Approvers reminded',
	'run.completed': 'Completed',
	'run.needs_attention': 'Needs attention',
	'run.failed': 'Failed'
};

/** The sentence for a lifecycle event type. */
export function eventLabel(type: string): string {
	return EVENT_LABELS[type] ?? type.replace(/^run\./, '').replace(/[._]/g, ' ');
}

export function eventTone(type: string): StatusTone {
	switch (type) {
		case 'run.completed':
		case 'run.approved':
			return 'success';
		case 'run.held':
		case 'run.needs_attention':
			return 'warning';
		case 'run.failed':
		case 'run.rejected':
			return 'danger';
		case 'run.cancelled':
			return 'neutral';
		default:
			return 'info';
	}
}

/** `1.8 s`, `21 ms`, `2m 02s` — one duration face across the surface. */
export function formatDuration(ms: number | null | undefined): string {
	if (ms == null || Number.isNaN(ms) || ms < 0) return '—';
	if (ms < 1000) return `${Math.round(ms)} ms`;
	const seconds = ms / 1000;
	if (seconds < 10) return `${seconds.toFixed(1)} s`;
	if (seconds < 60) return `${Math.round(seconds)} s`;
	const mins = Math.floor(seconds / 60);
	if (mins < 60) return `${mins}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`;
	const hours = Math.floor(mins / 60);
	return `${hours}h ${String(mins % 60).padStart(2, '0')}m`;
}

/** How long a run has taken, live while it is still going. */
export function runDurationMs(
	run: { started_at: string | null; ended_at: string | null },
	now: number = Date.now()
): number | null {
	if (!run.started_at) return null;
	const start = new Date(run.started_at).getTime();
	const end = run.ended_at ? new Date(run.ended_at).getTime() : now;
	const ms = end - start;
	return Number.isNaN(ms) || ms < 0 ? null : ms;
}

/** How long a NODE took. A finished node that never had its `ended_at` written (leaves completed
 *  before the engine stamped it) is read off its last attempt, so a book read that succeeded
 *  nine hours ago says "2 ms", not "9h 28m". */
export function nodeDurationMs(
	node: {
		started_at: string | null;
		ended_at: string | null;
		state: { type: string };
		attempts: { started_at: string | null; ended_at: string | null; duration_ms: number | null }[];
	},
	now: number = Date.now()
): number | null {
	if (node.ended_at || !node.started_at) return runDurationMs(node, now);
	const settled =
		node.state.type === 'completed' ||
		node.state.type === 'failed' ||
		node.state.type === 'cancelled';
	if (!settled) return runDurationMs(node, now);
	const last = node.attempts.at(-1);
	if (last?.ended_at)
		return runDurationMs({ started_at: node.started_at, ended_at: last.ended_at });
	const total = node.attempts.reduce((sum, a) => sum + (a.duration_ms ?? 0), 0);
	return node.attempts.length ? total : null;
}

/** "5h ago", "yesterday", "3d ago". */
export function relativeTime(value: string | null, now: number = Date.now()): string {
	if (!value) return '';
	const diff = now - new Date(value).getTime();
	if (Number.isNaN(diff)) return '';
	const mins = Math.floor(diff / 60000);
	const hours = Math.floor(diff / 3600000);
	const days = Math.floor(diff / 86400000);
	if (mins < 1) return 'just now';
	if (mins < 60) return `${mins}m ago`;
	if (hours < 24) return `${hours}h ago`;
	if (days === 1) return 'yesterday';
	return `${days}d ago`;
}

/** "in 9 s" / "now" — for the next poll of a waiting node. */
export function untilTime(value: string | null, now: number = Date.now()): string {
	if (!value) return '';
	const diff = new Date(value).getTime() - now;
	if (Number.isNaN(diff) || diff <= 0) return 'now';
	const seconds = Math.round(diff / 1000);
	if (seconds < 60) return `in ${seconds} s`;
	return `in ${Math.round(seconds / 60)} min`;
}

/** `21 Oct 2026, 13:11` */
export function whenLabel(value: string | null): string {
	if (!value) return '—';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return date.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}

/** `13:11:02` — the clock time, for a timeline whose day is already known. */
export function clockLabel(value: string | null): string {
	if (!value) return '';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return date.toLocaleTimeString('en-GB', { hour12: false });
}

/** The path and query of a URL, or the string as given when it is already a path. */
export function pathOf(url: string | null): string {
	if (!url) return '';
	try {
		const parsed = new URL(url);
		return `${parsed.pathname}${parsed.search}` || url;
	} catch {
		return url;
	}
}
