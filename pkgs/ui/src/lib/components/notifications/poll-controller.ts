/**
 * A visibility-aware poll controller for the notification badge.
 *
 * It runs `onPoll` every `intervalMs` WHILE the tab is visible, pauses entirely when hidden, and
 * refetches immediately on becoming visible again. Extracted from the bell so this state machine is
 * unit-testable in isolation: the failure that matters is a **missed resume** — if `visible()` ever
 * failed to restart the interval, the badge would sit permanently stale — so the tests pin
 * pause → resume and guarantee the interval is never stacked (a stacked interval polls twice as
 * fast and leaks a timer).
 *
 * The controller owns only the timer; the component owns the DOM wiring (which
 * `visibilitychange`/`focus` events map to `visible()`/`hidden()`) and the fetch inside `onPoll`.
 */
export interface PollController {
	/** Begin polling without an immediate poll — used on mount, when the loader already seeded a
	 *  fresh value. Idempotent: calling it while already running does nothing (never stacks). */
	start(): void;
	/** Tab became visible / window regained focus: refetch NOW so the return is instant, then
	 *  ensure the interval is running (idempotent). This is the resume path. */
	visible(): void;
	/** Tab hidden: pause polling (resumable via `visible()`). */
	hidden(): void;
	/** Tear down for good (component destroyed). */
	stop(): void;
}

export function createPollController(intervalMs: number, onPoll: () => void): PollController {
	let id: ReturnType<typeof setInterval> | null = null;

	const start = () => {
		if (id === null) id = setInterval(onPoll, intervalMs); // idempotent — never stack intervals
	};
	const stop = () => {
		if (id !== null) {
			clearInterval(id);
			id = null;
		}
	};

	return {
		start,
		stop,
		hidden: stop,
		visible() {
			onPoll();
			start();
		}
	};
}

/**
 * How often a screen asks again while it is showing a wait whose result arrives out of band.
 * The AML console's client page is the reference: 15s while ComplyCube still has a check, 4s
 * while a screen the worker has queued is about to start.
 */
export const REFRESH_WHILE = {
	pendingCheck: 15_000,
	screenStarting: 4_000,
	identityCapture: 5_000,
	phoneCapture: 2_000,
	hitsQueue: 30_000,
	documentRender: 2_000,
	inFlightList: 5_000,
	actionRun: 5_000
} as const;

/**
 * Run `onPoll` on `intervalMs` while the tab is visible. Hidden pauses it; becoming visible
 * asks once immediately, then resumes. Returns the teardown. A caller with no `document`
 * (the server render) gets a no-op.
 */
/** Coming back to a tab fires `visibilitychange` and `focus` together. Ask once for that return. */
const RETURN_COALESCE_MS = 500;

export function pollWhileVisible(intervalMs: number, onPoll: () => void): () => void {
	if (typeof document === 'undefined') return () => {};
	const poller = createPollController(intervalMs, onPoll);
	let returnedAt = Number.NEGATIVE_INFINITY;
	const onReturn = () => {
		const now = Date.now();
		if (now - returnedAt < RETURN_COALESCE_MS) {
			poller.start();
			return;
		}
		returnedAt = now;
		poller.visible();
	};
	const onVisibility = () => {
		if (document.hidden) {
			returnedAt = Number.NEGATIVE_INFINITY;
			poller.hidden();
			return;
		}
		onReturn();
	};
	if (!document.hidden) poller.start();
	document.addEventListener('visibilitychange', onVisibility);
	window.addEventListener('focus', onReturn);
	return () => {
		poller.stop();
		document.removeEventListener('visibilitychange', onVisibility);
		window.removeEventListener('focus', onReturn);
	};
}
