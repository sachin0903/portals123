<script lang="ts">
	import { cn } from '../../../utils.js';
	import { Button } from '../button/index.js';
	import EyeIcon from '@lucide/svelte/icons/eye';

	/**
	 * Says, permanently and unmistakably, that the portal below is not yours and that you are
	 * only looking.
	 *
	 * A member of staff viewing a client's portal is looking at a screen that is designed to
	 * address the person whose account it is — "your accounts", "your adviser" — so the only
	 * thing standing between that and a genuine mistake is this bar. It is therefore never
	 * scrolled away, it names the client rather than hinting at them, and it says how long is
	 * left, because a session that lapses mid-sentence is otherwise indistinguishable from the
	 * portal breaking.
	 *
	 * The wording promises read-only because the API refuses every write from this session
	 * (`app/view_as.py::permits`). Nothing here enforces it; this says what is already true, and
	 * must not be reworded into something the enforcement does not back.
	 */
	let {
		contactName,
		secondsRemaining,
		exitAction = '/view-as/exit',
		endedHref = '/view-as/ended?left=1',
		class: className
	}: {
		/** The client whose portal this is. */
		contactName: string;
		/** Seconds left at render. Counted down here so the bar stays honest without polling. */
		secondsRemaining: number;
		/** Form target that closes the session. A POST, because leaving changes something. */
		exitAction?: string;
		/** Where to land if the tab cannot be closed — see `leave`. */
		endedHref?: string;
		class?: string;
	} = $props();

	let leaving = $state(false);

	/** End the session and close the tab.
	 *
	 * There is nothing to tell someone who has just chosen to leave, and the tab was opened for
	 * this one purpose, so it goes with the session. Focusing the opener first puts them back on
	 * the client's record rather than on whatever happened to be behind this window.
	 *
	 * A browser only lets a script close a tab a script opened. The timeout covers the case where
	 * it refuses — a bookmarked portal, say — because the alternative is leaving someone sitting
	 * on a portal whose session has just ended. Without JS the form posts on its own and the
	 * ended page says what happened, which is the same destination by a slower road.
	 */
	async function leave(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (leaving) return;
		leaving = true;
		try {
			// `manual` so the 303 to the ended page is not followed. Following it is another
			// request on this host, and the only cookie that request may touch is the view-as one.
			await fetch(exitAction, { method: 'POST', redirect: 'manual' });
		} catch {
			// The session still lapses on its own clock, and the tab is going either way.
		}
		try {
			window.opener?.focus();
		} catch {
			// A cross-origin opener cannot be focused; closing still works.
		}
		window.close();
		setTimeout(() => {
			if (!window.closed) window.location.href = endedHref;
		}, 200);
	}

	// Counted against a deadline rather than by decrementing a counter: a fresh figure from the
	// API re-anchors it with nothing to reset, and the bar cannot drift away from the server's
	// answer over a long sitting. Only the interval between ticks comes from this machine's
	// clock; how long is left is the server's figure.
	const deadline = $derived(Date.now() + secondsRemaining * 1000);
	let now = $state(Date.now());
	$effect(() => {
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});
	const remaining = $derived(Math.max(0, Math.round((deadline - now) / 1000)));

	// Minutes while there is more than a minute, then seconds — the point at which the exact
	// number starts to matter is the point at which the session is about to go.
	const left = $derived.by(() => {
		if (remaining <= 0) return 'ending now';
		if (remaining < 60) return `${remaining}s left`;
		return `${Math.ceil(remaining / 60)} min left`;
	});
</script>

<div
	data-slot="view-as-banner"
	role="status"
	class={cn(
		// Above the shell's own layers (its topbar and the mobile tab bar sit at z-20) because
		// nothing may cover the notice. Kept to one line — the name truncates, the controls do
		// not shrink — so the host can take its height as a constant.
		'bg-brand text-brand-foreground relative z-50 flex w-full items-center justify-between gap-3 px-4 py-2 text-sm',
		className
	)}
>
	<span class="flex min-w-0 items-center gap-2">
		<EyeIcon class="size-4 shrink-0" aria-hidden="true" />
		<span class="truncate">
			Read-only view of <strong class="font-semibold">{contactName}</strong>’s portal.
		</span>
	</span>
	<span class="flex shrink-0 items-center gap-3">
		<span class="tabular-nums opacity-80">{left}</span>
		<form method="POST" action={exitAction} onsubmit={leave}>
			<!-- Drawn from the bar's own foreground rather than the surface tokens: every stock
			     variant is built for the page canvas, and on this fill an outline button reads as a
			     white slab dropped onto it. -->
			<Button
				type="submit"
				variant="ghost"
				size="sm"
				disabled={leaving}
				class="border-brand-foreground/40 text-brand-foreground hover:bg-brand-foreground/15 hover:text-brand-foreground focus-visible:ring-brand-foreground/40 border bg-transparent"
			>
				Leave
			</Button>
		</form>
	</span>
</div>
