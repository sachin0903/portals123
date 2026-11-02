// A single app-wide ticking clock for relative timestamps ("2m ago").
//
// A notification renders its age as `relativeTime(iso, now())`. Without a ticking `now` the label is
// computed once and then FROZEN until the component happens to re-render (a refetch, or a tab-switch
// that remounts) — so "0m ago" stayed "0m ago" until you left and came back. Reading `now()` inside a
// `$derived`/template subscribes to this shared clock, so every visible timestamp re-computes on each
// tick. One interval for the whole app, guarded to the browser so SSR renders a static value.
//
// THE CLOCK STARTS AT IMPORT, NOT ON FIRST READ — do not make it lazy again. It was lazy once, on the
// reasoning that a clock nothing reads need not run, and that is how the bug this module exists to
// prevent came back in a second costume. Nothing reads the clock until a notification ROW renders,
// and a row renders only when the bell's popover is opened — which can be hours after the page
// loaded. Until then `current` sat at its seed value, so the first open of the dropdown measured
// every notification against a stale `now`: an item that arrived an hour ago read "just now", and an
// item from the morning read the age it had when the page loaded. It self-healed one tick later,
// which is what made it look like a rendering blip rather than a wrong answer. A refresh re-seeded
// the module and the times were right — the "it only updates if I refresh" report exactly.
// `clock-start.dom.test.ts` is the tripwire.
//
// The cost of starting eagerly is one `new Date()` every TICK_MS for the life of the tab. That is
// nothing, and it is the price of the label being right the first time it is READ rather than one
// tick after.

const TICK_MS = 10_000;

let current = $state(new Date());

/** The current time as a REACTIVE value — read it in a `$derived`/template so the read re-runs each
 *  tick. Off the reactive path it is just the last tick's `Date`. */
export function now(): Date {
	return current;
}

if (typeof window !== 'undefined') {
	setInterval(() => (current = new Date()), TICK_MS);

	// An interval is not a clock. A background tab has its timers throttled to about once a minute,
	// and a sleeping machine does not run them at all — so on return the last tick can be minutes or
	// hours old, which is the same stale reading as the lazy start, just arrived at differently.
	// Re-read the wall clock the moment the tab is looked at again, rather than waiting out a tick.
	const resync = () => (current = new Date());
	document.addEventListener('visibilitychange', () => {
		if (!document.hidden) resync();
	});
	window.addEventListener('focus', resync);
}
