<script lang="ts">
	// The notification bell — a badge-carrying trigger over a popover feed of recent notifications.
	// Shared by all three portals; kept free of $app/* so it stays portable (items link via native
	// <a>, the app injects mark-read/mark-all callbacks that POST to its own same-origin proxy).
	//
	// The badge count is the UNREAD count (matching GET /notifications/unread-count) and clears only
	// as items are read — the product decision. Kept fresh entirely by `realtimeUrl`'s push (below):
	// no fallback poll — a dropped stream relies on native `EventSource` reconnect, and the badge
	// can go stale for as long as that takes. Accepted trade-off: the alternative was a permanent
	// `countEndpoint` GET every `pollMs` on every open tab, whether or not anything ever changed.
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import BellIcon from '@lucide/svelte/icons/bell';
	import CheckCheckIcon from '@lucide/svelte/icons/check-check';
	import XIcon from '@lucide/svelte/icons/x';
	import * as Popover from '../ui/popover/index.js';
	import { cn } from '../../utils.js';
	import NotificationItemRow from './notification-item.svelte';
	import NotificationToast from './notification-toast.svelte';
	import { parseFeedItems } from './feed-refetch.js';
	import { settleWrite, type WriteOutcome } from './write-outcome.js';
	import { arrivals, MAX_TOASTS_PER_BATCH } from './arrivals.js';
	import type { NotificationItem } from './describe.js';

	let {
		items = [],
		unreadCount = 0,
		feedHref = '/notifications',
		countEndpoint,
		feedEndpoint,
		realtimeUrl,
		onMarkRead,
		onMarkAllRead,
		onDismiss,
		onDismissAll,
		max = 8,
		toastArrivals = true,
		toastDurationMs = 6000,
		showing,
		triggerClass,
		iconClass
	}: {
		items?: NotificationItem[];
		unreadCount?: number;
		feedHref?: string;
		/** Same-origin URL returning `{ count }`; when set, the bell polls it every `pollMs`. */
		countEndpoint?: string;
		/** Same-origin URL returning the recent notifications (`{ items }` or a bare array) — the
		 *  LIST behind the badge. Without it the dropdown can only ever show what the page's loader
		 *  put in `items`, and that is frozen: the root layout's `load` has no url/params dependency
		 *  and nothing invalidates it, so SvelteKit never re-runs it on a client-side navigation.
		 *  The badge was therefore live (it has its own endpoint) while the list it opened onto was
		 *  whatever existed at the last full page load — a realtime bell announcing notifications you
		 *  could not read. Refetched when the popover OPENS and whenever the stream announces a
		 *  delivery. */
		feedEndpoint?: string;
		/** Same-origin Server-Sent Events URL. The portal exchanges its server-held bearer for the
		 *  private API stream; the browser never needs an API hostname or a ticket. */
		realtimeUrl?: string;
		/** The app's write callbacks. Each may answer whether the write LANDED (`false`, or a
		 *  rejection, puts the bell back in step by re-asking the server); a `void` return keeps
		 *  the fire-and-forget behaviour. See `write-outcome.ts`. */
		onMarkRead?: (uid: string) => WriteOutcome;
		onMarkAllRead?: () => WriteOutcome;
		onDismiss?: (uid: string) => WriteOutcome;
		/**
		 * Empty the bell in one write. Absent ⇒ the control is not shown at all, rather than shown
		 * doing nothing.
		 *
		 * It dismisses EVERYTHING undismissed, not just the rows loaded here — the list is a window
		 * of `max` over a backlog that can be hundreds, and clearing only what is on screen leaves
		 * it to refill on the next refetch, which reads as a button that did not work. Nothing is
		 * lost: the all-notifications page asks for dismissed rows too.
		 */
		onDismissAll?: () => WriteOutcome;
		max?: number;
		/** Pop a card top-right when a notification arrives while the page is open (the Slack/Outlook
		 *  affordance). Needs a `<Toaster />` in the app's root layout — all three portals mount one,
		 *  and `../ui/sonner/toaster-wiring.test.ts` fails a portal that mounts this bell without it,
		 *  because the pop-up would otherwise be dropped in silence. */
		toastArrivals?: boolean;
		/** "Am I already showing what this notification is about?" — answered by the app, because
		 *  only the app knows what is on screen. A matching arrival is marked read instead of
		 *  popped: telling somebody about a message they are watching arrive is noise, and leaving
		 *  it on the badge claims something is waiting when it is not.
		 *
		 *  Absent ⇒ nothing is suppressed, which is every portal that does not opt in. */
		showing?: (item: NotificationItem) => boolean;
		/** How long a pop-up stays before it goes on its own. Sonner pauses the timer while the
		 *  pointer is over the stack, so this is dwell time, not wall-clock. */
		toastDurationMs?: number;
		/** The trigger's chrome. Unset, it is the staff tools' flat square; the client portal passes
		 *  its liquid-glass round (`CLIENT_HEADER_BUTTON_CLASS`) so the bell matches the controls
		 *  beside it. The badge stays where it is either way. */
		triggerClass?: string;
		iconClass?: string;
	} = $props();

	let open = $state(false);
	// Seeded from the loader's values (correct on first paint), then resynced by the effects below
	// and mutated locally for optimistic mark-read; untrack keeps the seed a one-time read.
	let count = $state(untrack(() => unreadCount));
	let list = $state<NotificationItem[]>(untrack(() => items));

	// Keep local state in step if the loader re-runs and passes fresh props.
	$effect(() => {
		count = unreadCount;
	});
	$effect(() => {
		list = items;
	});

	/**
	 * Paging state for "Load more".
	 *
	 * `cursor` is the uid of the oldest row in hand — the keyset the API pages from. A uid rather
	 * than the table's id because the id is not on the wire (see `list_notifications`), and an
	 * OFFSET would double-show rows the moment a new notification lands at the top while somebody
	 * is paging.
	 *
	 * `pagedIn` flips the `max` cap off: the collapsed bell shows the newest `max`, but once a
	 * reader has asked for more it would be perverse to keep hiding what they just fetched.
	 */
	let cursor = $state<string | null>(null);
	let pagedIn = $state(false);
	let exhausted = $state(false);
	let loadingMore = $state(false);

	const shown = $derived(pagedIn ? list : list.slice(0, max));
	/** Offered only once there is a full window in hand — a "Load more" under three rows is noise. */
	const canLoadMore = $derived(!!feedEndpoint && !exhausted && list.length >= max);
	const badge = $derived(count > 99 ? '99+' : String(count));

	// Every uid this bell has already dealt with. Seeded from the loader's rows so signing in does
	// not replay the backlog as a wall of cards — `untrack`, because the seed is a one-time read and
	// the effect below is what keeps it current afterwards. A plain Set on purpose: it is written
	// from inside the effect that reads `list`, and making it reactive would re-enter that effect.
	const announced = new Set<string>(untrack(() => items.map((n) => n.uid)));

	/**
	 * The sonner ids of the cards THIS bell put up.
	 *
	 * Tracked because `toast.dismiss()` with no argument clears every toast on the page, including
	 * the app's own action toasts — a "Saved" confirmation has nothing to do with the notification
	 * stack and must not vanish because somebody opened the bell or pressed Close all. Ids are
	 * removed as each card goes, so this never grows past what is on screen.
	 */
	const raised = new Set<string | number>();

	/** Close the notification cards, and only those. Display-only: no read, no dismiss, no write. */
	function closeRaised() {
		for (const id of raised) toast.dismiss(id);
		raised.clear();
	}

	// Pop the new ones. This watches `list` rather than hooking the stream, so it covers every way a
	// notification can reach the page — the realtime push, the popover open, and the loader re-running
	// on a client-side navigation — with one rule instead of three. `arrivals` owns the decision (what
	// is new, what is merely re-listed, how many of a burst) and is tested on its own.
	$effect(() => {
		if (!toastArrivals) return;
		const { toast: pop, seen, silence } = arrivals(list, announced, MAX_TOASTS_PER_BATCH, showing);
		for (const uid of seen) announced.add(uid);
		// Read, not merely un-popped: the reader is looking at the thing it is about. `markRead`
		// is the same path the toast's own dismissal takes, so the badge, the list and the server
		// all move together.
		for (const item of silence) markRead(item.uid);
		// Nothing pops while the dropdown is OPEN: the reader is looking at this very list, and a
		// card over it hides the rows it is duplicating. Deliberately AFTER `announced` is updated
		// above, so an arrival that lands while the panel is open is recorded as dealt with rather
		// than queued — returning earlier would hold them all back and fire the batch the instant
		// the panel closed.
		if (open) return;
		for (const [i, item] of pop.entries()) {
			// `pop` is oldest-first, so the LAST one raised sits at the front of the stack — that is
			// the card a reader's eye and cursor land on, and the only one that gets "Close all".
			// On a batch of one it is omitted: a bulk control beside a single card would do exactly
			// what that card's own X does.
			const fronting = pop.length > 1 && i === pop.length - 1;
			const id: string | number = toast.custom(NotificationToast, {
				componentProps: {
					item,
					onMarkRead: markRead,
					// Display-only, like the X: sonner's `toast.dismiss()` with no argument closes
					// every open card and touches neither the server nor the badge. Closing pop-ups
					// is not a claim that anything was read.
					...(fronting ? { onCloseAll: closeRaised } : {})
				},
				// Top-right whatever the app's own Toaster is set to — sonner renders a stack per
				// position, so the client portal's top-center action toasts keep their place and these
				// sit in the corner every desktop notification has used for twenty years.
				position: 'top-right',
				duration: toastDurationMs,
				// The card draws its own X; this is the swipe/escape affordance sonner adds around it.
				dismissible: true,
				// However a card goes — its X, a swipe, or its own timer — stop tracking it, so the
				// set stays the cards actually on screen.
				onDismiss: () => raised.delete(id),
				onAutoClose: () => raised.delete(id)
			});
			raised.add(id);
		}
	});

	// Opening the dropdown clears the cards it duplicates. The rows are right there, and a pop-up
	// sitting over the panel hides the very list it is announcing.
	$effect(() => {
		if (open) closeRaised();
	});

	/** Put the bell back in step with the server after a write it showed optimistically did not
	 *  land. Re-asks for both the count and the rows rather than undoing the local change: the
	 *  server is the truth, and an inverse replayed over later arrivals is how badges drift. */
	function resync() {
		void refetchCount();
		void refetchFeed();
	}

	function markRead(uid: string) {
		list = list.map((n) => (n.uid === uid ? { ...n, read_at: new Date().toISOString() } : n));
		if (count > 0) count -= 1;
		void settleWrite(onMarkRead?.(uid), resync);
	}

	function markAll() {
		const stamp = new Date().toISOString();
		list = list.map((n) => (n.read_at ? n : { ...n, read_at: stamp }));
		count = 0;
		void settleWrite(onMarkAllRead?.(), resync);
	}

	function dismiss(uid: string) {
		const gone = list.find((n) => n.uid === uid);
		if (gone && gone.read_at === null && count > 0) count -= 1; // an unread dismissal frees the badge
		list = list.filter((n) => n.uid !== uid);
		void settleWrite(onDismiss?.(uid), resync);
	}

	/** Empty the bell. The badge goes to nought with it, because dismissing stamps read — a badge
	 *  counting rows the bell can no longer show is a claim nothing on screen can answer. */
	function dismissAll() {
		list = [];
		count = 0;
		// A cursor into a list that no longer exists; the next open starts from the top again.
		cursor = null;
		exhausted = true;
		void settleWrite(onDismissAll?.(), () => {
			// The rows come back if the write did not land, so paging must be possible again.
			exhausted = false;
			resync();
		});
	}

	// Refetch the unread count from the API — the one "invalidate, don't trust the payload" seam.
	// BOTH the poll and the realtime stream call it, so a push and a tick converge on one source of
	// truth (the RLS-scoped count), never a delta the client has to keep in step.
	async function refetchCount() {
		if (!countEndpoint) return;
		try {
			const res = await fetch(countEndpoint, { headers: { accept: 'application/json' } });
			if (!res.ok) return;
			const data = (await res.json()) as { count?: number };
			if (typeof data.count === 'number') count = data.count;
		} catch {
			// A failed refetch is a stale badge, not an error to surface — the next one recovers.
		}
	}

	// Refetch the LIST — the same "ask the server, don't trust a delta" seam as `refetchCount`, for
	// the rows the badge opens onto. Accepts `{items}` or a bare array, because the app proxies this
	// from an endpoint that returns a plain list.
	//
	// It REPLACES the list rather than merging: the server's answer is the truth, and a merge would
	// have to reconcile optimistic mark-read state against it. The cost is that an in-flight optimistic
	// read can be briefly undone by a refetch landing after it — acceptable, because the server has by
	// then recorded the read anyway, so the next answer agrees. A merge would trade that for a
	// permanent divergence risk, which is worse.
	let feedFetching = false;
	async function refetchFeed() {
		if (!feedEndpoint || feedFetching) return;
		feedFetching = true;
		try {
			const res = await fetch(feedEndpoint, { headers: { accept: 'application/json' } });
			if (!res.ok) return;
			// `null` = an answer we cannot use; keep the rows we have. See feed-refetch.ts for why that
			// is not the same as an empty list, and feed-refetch.test.ts for the cases it covers.
			const next = parseFeedItems(await res.json());
			if (next) {
				list = next;
				// Back to page one, because that is what the answer IS. Leaving `pagedIn` set would
				// uncap a list that no longer has the extra pages in it.
				cursor = null;
				pagedIn = false;
				exhausted = false;
			}
		} catch {
			// A failed refetch leaves the previous rows on screen — stale beats empty, and the next
			// open (or the next push) recovers. An empty list here would read as "nothing to see".
		} finally {
			feedFetching = false;
		}
	}

	/**
	 * The next keyset page, APPENDED.
	 *
	 * Appending rather than replacing is the whole point — a reader four pages down must not be
	 * sent back to the top to reach page five. `refetchFeed` above still replaces, and still
	 * resets this paging, because a push or a re-open is a fresh answer about the newest rows and
	 * merging it into a paged list is the divergence that function's comment already refuses.
	 */
	async function loadMore() {
		if (!feedEndpoint || loadingMore || exhausted) return;
		const oldest = list.at(-1);
		if (!oldest) return;
		loadingMore = true;
		cursor = oldest.uid;
		try {
			// Built by hand rather than through `URL`: this package is mounted in three portals and
			// carries no `$app/*` or `location` assumption, and `feedEndpoint` is a same-origin path.
			const sep = feedEndpoint.includes('?') ? '&' : '?';
			const res = await fetch(`${feedEndpoint}${sep}before_uid=${encodeURIComponent(cursor)}`, {
				headers: { accept: 'application/json' }
			});
			if (!res.ok) return;
			const next = parseFeedItems(await res.json());
			// `null` is an unusable answer, not an empty one — keep what we have and let the reader
			// try again. Only a genuinely empty page means there is nothing older.
			if (!next) return;
			if (next.length === 0) {
				exhausted = true;
				return;
			}
			// Deduplicated on uid: a notification read or dismissed elsewhere can shift the window
			// between the page in hand and the one just asked for, and a repeated row in a list
			// somebody is scanning reads as a duplicate notification rather than a paging artefact.
			const have = new Set(list.map((n) => n.uid));
			list = [...list, ...next.filter((n) => !have.has(n.uid))];
			pagedIn = true;
		} catch {
			// Same rule as the refetch: a failed page leaves the rows on screen.
		} finally {
			loadingMore = false;
		}
	}

	// Opening the dropdown is the moment the rows are actually READ, so it is the cheapest correct
	// place to make sure they are current — it covers the case the stream cannot (a delivery that
	// arrived while the stream was down, or before it connected).
	$effect(() => {
		if (open) void refetchFeed();
	});

	// The realtime stream — the ONLY thing that keeps this fresh now (no fallback poll: removed
	// because a boolean/count check every `pollMs` on every open tab, whether or not anything ever
	// changed, was judged worse than the trade-off of a badge that lags for as long as a silently
	// dropped stream takes to reconnect). It is same-origin: the portal's BFF authenticates the
	// browser session, mints the private API ticket server-side, and pipes SSE back. Native
	// EventSource owns reconnects; every RECONNECT's 'open' refetches, so a gap created while the
	// connection was down is closed the moment it comes back — this is the only thing standing in
	// for what the poll used to guarantee. The INITIAL 'open' does not: the bell was just seeded
	// with the loader's rows and count, and re-asking for both on connect was two duplicate round
	// trips on every page load — the work the page's "settle" was waiting on. The cost is the
	// window between the loader's read and the stream connecting, which the next push, popover
	// open or reconnect covers. Absent `realtimeUrl` ⇒ this bell relies solely on whatever
	// `count`/`items` it was seeded with.
	$effect(() => {
		if (!realtimeUrl || typeof window === 'undefined') return;
		const events = new EventSource(realtimeUrl);
		let connected = false;
		events.addEventListener('open', () => {
			if (connected) {
				void refetchCount();
				void refetchFeed();
			}
			connected = true;
		});
		events.addEventListener('message', (ev) => {
			try {
				const payload = JSON.parse(ev.data) as { type?: string };
				if (payload?.type === 'notification' || payload?.type === 'resync') {
					// The badge always: it is on screen whether or not the dropdown is.
					void refetchCount();
					// The ROWS always too, now — which reverses the earlier rule here, on purpose.
					//
					// The push is deliberately CONTENTLESS (`app/notifications/realtime.py`: the
					// browser is told THAT something changed, never what, so a stale or duplicated
					// NOTIFY is harmless). Refetching is therefore the only way to learn what
					// arrived, and the arrival pop-up needs to know. While the dropdown was the
					// only reader this fetch was pure waste when closed and was skipped; the
					// pop-up made closed the case that MATTERS, since a card announcing a
					// notification is worth more when you are not already looking at the list.
					//
					// Still not "refetch when the COUNT changed", the other obvious rule: it misses
					// the case that matters — a notification arriving while another is read in a
					// second tab leaves the count where it was and the rows on screen wrong.
					void refetchFeed();
				}
			} catch {
				// A malformed frame is not a reason to drop the stream.
			}
		});
		return () => events.close();
	});
</script>

<Popover.Root bind:open>
	<Popover.Trigger
		aria-label={count > 0 ? `Notifications (${count} unread)` : 'Notifications'}
		class={triggerClass ??
			'text-muted-foreground hover:text-foreground hover:bg-muted focus-visible:ring-ring tap-target relative flex size-8 items-center justify-center rounded-[var(--radius-chrome,0.375rem)] transition-colors focus-visible:ring-2 focus-visible:outline-none'}
	>
		<BellIcon class={iconClass ?? 'size-4'} />
		{#if count > 0}
			<span
				class="bg-primary text-primary-foreground absolute -top-0.5 -right-0.5 flex min-w-4 items-center justify-center rounded-full px-1 text-[0.6rem] font-semibold leading-4"
			>
				{badge}
			</span>
		{/if}
	</Popover.Trigger>
	<Popover.Content align="end" class="w-[22rem] p-0">
		<div class="flex min-h-11 items-center justify-between border-b px-3">
			<span class="text-sm font-semibold">Notifications</span>
			<!-- Both controls, because they do different things: "Mark all read" empties the BADGE
			     and leaves the rows here to read; "Dismiss all" empties the LIST. Dismissing marks
			     read too, so it clears the badge as a side effect — which is why the two sit
			     together and are worded for what they leave behind, not for what they clear. -->
			<!-- text-xs controls that fill the row's 44px: an invisible extension would reach into
			     the first notification below. -->
			<span class="flex items-center gap-3">
				{#if count > 0}
					<button
						type="button"
						onclick={markAll}
						class="text-muted-foreground hover:text-foreground flex min-h-11 items-center gap-1 text-xs"
					>
						<CheckCheckIcon class="size-3.5" />
						Mark all read
					</button>
				{/if}
				{#if onDismissAll && list.length > 0}
					<button
						type="button"
						onclick={dismissAll}
						class="text-muted-foreground hover:text-foreground flex min-h-11 items-center gap-1 text-xs"
					>
						<XIcon class="size-3.5" />
						Dismiss all
					</button>
				{/if}
			</span>
		</div>

		{#if shown.length === 0}
			<div class="text-muted-foreground flex flex-col items-center gap-2 px-3 py-10 text-center">
				<BellIcon class="size-6 opacity-40" />
				<span class="text-sm">You're all caught up.</span>
			</div>
		{:else}
			<div class={cn('max-h-[24rem] overflow-y-auto p-1')}>
				{#each shown as item (item.uid)}
					<NotificationItemRow
						{item}
						onMarkRead={markRead}
						onActivate={() => (open = false)}
						onDismiss={dismiss}
					/>
				{/each}
				{#if canLoadMore}
					<button
						type="button"
						onclick={loadMore}
						disabled={loadingMore}
						class="text-muted-foreground hover:text-foreground hover:bg-muted mt-1 flex min-h-11 w-full items-center justify-center rounded px-3 py-2 text-xs disabled:opacity-60"
					>
						{loadingMore ? 'Loading…' : 'Load more'}
					</button>
				{/if}
			</div>
		{/if}

		<a
			href={feedHref}
			onclick={() => (open = false)}
			class="text-primary hover:bg-muted flex min-h-11 items-center justify-center border-t px-3 py-2 text-sm font-medium"
		>
			See all notifications
		</a>
	</Popover.Content>
</Popover.Root>
