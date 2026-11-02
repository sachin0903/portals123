/**
 * Slack-style day headings for the notifications archive (the full `/notifications` page).
 *
 * `describe.ts`'s `relativeTime` gives a single row its fine-grained age ("2h ago") — right for the
 * bell's short list, but a page that is now the COMPLETE history (read and dismissed rows included,
 * nothing hidden) needs a coarser way to scan a long list, so rows are grouped under a day heading
 * instead: `Today`, `Yesterday`, a weekday for the rest of the last week, else an absolute date.
 *
 * This builds on `formatDayLabel` (`../book/messages/messages.js`) for the Today/Yesterday/absolute
 * backbone rather than forking it, but does not touch that function: the chat thread it serves
 * deliberately jumps straight from "Yesterday" to the long absolute date, and inserting a weekday
 * step there would silently change what a 3-day-old message renders in every conversation. The
 * weekday step is layered on top, here, for the archive only.
 */

import { formatDayLabel } from '../book/messages/messages.js';

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** Local midnight for a `Date` — calendar-day math must run in the reader's own timezone, not
 *  UTC's: a notification at 00:30 local has to sit under "Today", and a `.toISOString().slice(0, 10)`
 *  style UTC slice reads that as still-yesterday for anyone whose local time is ahead of UTC. */
function startOfDay(date: Date): Date {
	return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/** A day heading as the archive shows it: "Today", "Yesterday", a weekday + date for the rest of
 *  the last week ("Wednesday, 9 September"), else an absolute date ("14 March 2026"). `now` is
 *  injectable so a test is deterministic. */
export function formatArchiveDayHeading(iso: string, now: Date = new Date()): string {
	const date = new Date(iso);
	const days = Math.round((startOfDay(now).getTime() - startOfDay(date).getTime()) / MS_PER_DAY);
	if (days >= 2 && days <= 6) {
		// Built from two separate `toLocaleDateString` calls, not one call with both `weekday` and
		// `month` options — the combined form's punctuation (the comma after the weekday) is an ICU
		// data detail that varies by Node build, and the joined string here is what the design asks
		// for regardless of that.
		const weekday = date.toLocaleDateString('en-GB', { weekday: 'long' });
		const dayMonth = date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long' });
		return `${weekday}, ${dayMonth}`;
	}
	return formatDayLabel(iso, now);
}

/** Group consecutive rows under one heading. Consecutive-run only, same convention as
 *  `groupMessagesByDay`/`groupByDate`: the feed is trusted to arrive newest-first, so this never
 *  needs to merge non-adjacent runs — an out-of-order input just repeats the heading rather than
 *  silently reordering the archive.
 *
 *  Recomputed fresh from whichever page is currently loaded (the feed pages by offset/limit,
 *  REPLACING its row list rather than appending to it — see `notification-feed.svelte`'s `$effect`),
 *  so a day that spans a page boundary gets its heading again at the top of the next page and never
 *  twice on the one page a reader is looking at. */
export function groupNotificationsByDay<T extends { created_at: string }>(
	items: readonly T[],
	now: Date = new Date()
): { heading: string; items: T[] }[] {
	return items.reduce<{ heading: string; items: T[] }[]>((acc, item) => {
		const heading = formatArchiveDayHeading(item.created_at, now);
		const last = acc[acc.length - 1];
		if (last && last.heading === heading) last.items.push(item);
		else acc.push({ heading, items: [item] });
		return acc;
	}, []);
}
