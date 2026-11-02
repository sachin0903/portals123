/**
 * Which notifications get a pop-up, and in what order.
 *
 * The rule is not "toast whatever the feed just returned". The feed is refetched on every push, on
 * every popover open and whenever the loader re-runs, and it returns the same recent rows each time
 * — so the decision has to be made against what has ALREADY been announced, not against what
 * changed. Getting that wrong is loud in the worst way: a page that re-pops the same five
 * notifications on every navigation is worse than one that pops none.
 *
 * Pure and separately tested, because the failure modes are all in the DECISION (a replayed
 * backlog, a duplicate, a burst of twenty) and none of them are in the rendering, which sonner owns.
 */
import type { NotificationItem } from './describe.js';

/** How many pop-ups one refresh may raise. A batch delivery (a flow completing, a nightly run) can
 *  land a dozen at once, and a dozen stacked cards is a modal dialog nobody asked for. The rest are
 *  still marked seen — the bell's badge and list are where a backlog belongs. */
export const MAX_TOASTS_PER_BATCH = 3;

export interface Arrivals {
	/** The ones to pop, OLDEST first, so the newest is raised last and sits at the front of the
	 *  stack — the same way a stack of cards reads everywhere else. */
	toast: NotificationItem[];
	/** Every uid now considered announced, INCLUDING the ones the cap dropped and the ones already
	 *  read. A uid left out here comes back as a fresh arrival on the next refetch. */
	seen: string[];
	/** Unread arrivals the caller said it is ALREADY SHOWING — to be marked read rather than
	 *  popped. Separate from `toast` because "do not pop this" and "the reader has seen this" are
	 *  different claims, and only the second one may touch the badge. */
	silence: NotificationItem[];
}

/**
 * Decide what to pop from a freshly-read feed.
 *
 * `announced` is the caller's running set of uids it has already dealt with — seeded at mount from
 * whatever the loader put on screen, so signing in does not replay the backlog as a wall of cards.
 *
 * Only UNREAD notifications pop: one read in another tab, or read here and re-listed by a refetch
 * landing after the optimistic update, is not an arrival.
 *
 * `showing` is the caller's answer to "am I already displaying the thing this is about" — a chat
 * message arriving in the thread on screen, say. Such an arrival is neither popped nor left unread:
 * a pop-up telling you about a message you are watching land is noise, and leaving it on the badge
 * would be a false claim that something is waiting. It comes back in `silence` for the caller to
 * mark read, rather than being dropped here, because marking read is a WRITE and this function is
 * pure.
 */
export function arrivals(
	items: readonly NotificationItem[],
	announced: ReadonlySet<string>,
	max: number = MAX_TOASTS_PER_BATCH,
	showing?: (item: NotificationItem) => boolean
): Arrivals {
	const fresh = items.filter((n) => !announced.has(n.uid));
	// Sorted here rather than trusted from the caller. The API answers `ORDER BY id DESC` today, but
	// this decides which of a burst get dropped by the cap, and "the wrong three" is invisible —
	// there is no symptom to notice, just the wrong notifications on screen.
	const newestFirst = [...fresh].sort(
		(a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)
	);
	const unread = newestFirst.filter((n) => n.read_at === null);
	// Partitioned BEFORE the cap, so a burst of three unrelated notifications cannot push the one
	// you are looking at out of `silence` and leave it sitting unread on the badge.
	const silence = showing ? unread.filter((n) => showing(n)) : [];
	const poppable = showing ? unread.filter((n) => !showing(n)) : unread;
	return {
		toast: poppable.slice(0, Math.max(0, max)).reverse(),
		seen: fresh.map((n) => n.uid),
		silence
	};
}
