// The bell's feed refetch, reduced to its one decidable question: given a response body, are these
// rows to show, or is this an answer to ignore?
//
// Extracted for the same reason `poll-controller.ts` was — the logic that decides whether the
// dropdown shows anything cannot be reached by an SSR test, and getting it wrong fails in the
// direction nobody notices: an empty dropdown reads as "you have no notifications", which is
// indistinguishable from a healthy quiet inbox. So the rule is pinned here instead.

import type { NotificationItem } from './describe.js';

/**
 * The rows in a feed response, or ``null`` when the body says nothing usable.
 *
 * ``null`` is NOT an empty list, and the distinction is the whole point: the caller leaves its
 * existing rows alone on ``null`` and only replaces them for a real array. A malformed or failed
 * answer therefore degrades to STALE rows, never to a dropdown that claims the inbox is empty.
 *
 * Two shapes are accepted because the API returns a bare list from ``GET /notifications`` while the
 * apps' proxies wrap it as ``{items}`` — normalising here means neither side has to know which it is
 * talking to, and a proxy that changes its mind does not silently blank the bell.
 *
 * An empty ARRAY is honoured as an empty list: "you have nothing" is a legitimate answer that the
 * server is entitled to give, and refusing it would leave dismissed rows on screen for ever.
 */
export function parseFeedItems(body: unknown): NotificationItem[] | null {
	if (Array.isArray(body)) return body as NotificationItem[];
	if (body && typeof body === 'object') {
		const items = (body as { items?: unknown }).items;
		if (Array.isArray(items)) return items as NotificationItem[];
	}
	return null;
}
