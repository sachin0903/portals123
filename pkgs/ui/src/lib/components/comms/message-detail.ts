/**
 * The delivery timeline — "Sent 14:02 · Delivered 14:02 · Opened 14:31".
 *
 * Pure and tested on its own, because this is the whole point of the Activity feed and a wrong
 * ordering here is the kind of wrong nobody reports: it looks like a timeline either way.
 *
 * It is derived from the message's own STAMPS, not from the event list, and that is deliberate.
 * The stamps are what `ledger.apply_provider_event` wrote under the monotonic rule, so they are
 * already de-duplicated and already the earliest time we were told of each thing. The events add
 * one thing the stamps cannot hold — that something happened MORE THAN ONCE — and that is all they
 * are read for here.
 */

import type { CommsMessage, CommsMessageEvent, MessageStatus } from './describe.js';
import { neverSent, statusMeta } from './describe.js';
import type { StatusTone } from '../ui/status-badge/index.js';

export interface TimelineEntry {
	key: string;
	label: string;
	/** ISO instant, or null when the stamp is absent but the entry still needs showing. */
	at: string | null;
	tone: StatusTone;
	/** A repeat count, when the provider reported the same thing more than once. */
	times?: number;
	/** The reason, for an entry that explains rather than reports (suppressed / failed). */
	detail?: string | null;
}

/** stamp → the status it evidences, in rank order. The order of this list IS the fallback order of
 *  the timeline when two stamps share an instant (a delivery inside the same second as the send is
 *  ordinary, and showing "Delivered" above "Sent" would read as a clock error). */
const STAMPS: ReadonlyArray<{ field: keyof CommsMessage; status: MessageStatus }> = [
	{ field: 'sent_at', status: 'sent' },
	{ field: 'delayed_at', status: 'delayed' },
	{ field: 'delivered_at', status: 'delivered' },
	{ field: 'bounced_at', status: 'bounced' },
	{ field: 'complained_at', status: 'complained' }
];

/** The provider event type that evidences each status, for the repeat count. */
const EVENT_FOR: Record<string, string> = {
	sent: 'email.sent',
	delayed: 'email.delivery_delayed',
	delivered: 'email.delivered',
	bounced: 'email.bounced',
	complained: 'email.complained'
};

/**
 * A repeat count means "the provider told us this more than once" — a genuine redelivery.
 *
 * A `poll:` event is not that. The safety net asks for the CURRENT state, so it records what it
 * read rather than something it observed happening; when the poll wins the race and the webhook's
 * own callback then arrives, the two rows describe ONE delivery and the pane said "Delivered ·
 * 2 times". Only the provider's own callbacks are counted.
 */
function isProviderCallback(event: CommsMessageEvent): boolean {
	return !(event.provider_event_id ?? '').startsWith('poll:');
}

function countOf(events: CommsMessageEvent[], status: string): number {
	const type = EVENT_FOR[status];
	if (!type) return 0;
	return events.filter((e) => e.event_type === type && isProviderCallback(e)).length;
}

/**
 * The timeline for one message, oldest first.
 *
 * A message that never left carries a single explanatory entry instead of a set of stamps: there
 * is no delivery story to tell, and rendering an empty timeline beside "Not sent" reads as
 * missing data rather than as a deliberate outcome.
 *
 * Entries are ordered by their instant, with the rank order above breaking ties — so a delivery
 * recorded in the same second as its send still reads after it.
 */
export function timelineFor(message: CommsMessage): TimelineEntry[] {
	if (neverSent(message.status)) {
		const meta = statusMeta(message.status);
		return [
			{
				key: message.status,
				label: meta.label,
				// `created_at`, not a stamp: the row was written at the moment of the attempt, and
				// that IS when this outcome happened.
				at: message.created_at,
				tone: meta.tone,
				detail: message.error
			}
		];
	}

	const events = message.events ?? [];
	// Ranked while building, so the tie-break below needs no second lookup and `rank` never has to
	// leak into the exported shape.
	const ranked: Array<{ entry: TimelineEntry; rank: number }> = [];
	STAMPS.forEach(({ field, status }, rank) => {
		const at = message[field] as string | null;
		if (!at) return;
		const meta = statusMeta(status);
		const times = countOf(events, status);
		ranked.push({
			rank,
			entry: {
				key: status,
				label: meta.label,
				at,
				tone: meta.tone,
				// Only when it genuinely repeated. A `1` beside every row would be noise, and a
				// count of 0 (the detail was fetched without its events, as the LISTING is) must
				// not read as "never happened".
				...(times > 1 ? { times } : {})
			}
		});
	});

	return ranked
		.sort((a, b) => {
			const at = Date.parse(a.entry.at ?? '');
			const bt = Date.parse(b.entry.at ?? '');
			if (Number.isFinite(at) && Number.isFinite(bt) && at !== bt) return at - bt;
			return a.rank - b.rank;
		})
		.map(({ entry }) => entry);
}

/**
 * The single line a ROW shows: the latest thing that happened, and when.
 *
 * The row has one line to spend, so it spends it on the most recent fact rather than the first —
 * "Bounced 14:31" is what a reader scanning the feed needs, not "Sent 14:02".
 */
export function latestEntry(message: CommsMessage): TimelineEntry | null {
	const entries = timelineFor(message);
	// `?? null` rather than a length check: indexed access is `T | undefined` under the package's
	// strict settings, and the declared return is `TimelineEntry | null`.
	return entries[entries.length - 1] ?? null;
}

/**
 * The Raw tab: the JSON posted to the provider, pretty-printed.
 *
 * Prefer the stored `request` (the wire body, cid: html and attachment metadata included). Rows
 * written before that column existed reconstruct the envelope from what we still have — never a
 * second dump of the preview HTML as a raw source view.
 */
/**
 * What the ledger stores in place of a wire body identical to the preview one.
 *
 * Kept in step with `app/email/transport.py::SAME_AS_PREVIEW` by
 * `message-detail.test.ts`, which reads the Python constant off disk — the two are one decision
 * and a silent drift would show as a marker rendered literally in the Raw pane.
 */
export const SAME_AS_PREVIEW =
	'[…the body as stored on this message — the wire copy was identical…]';

export function formatRawRequest(message: CommsMessage): string | null {
	if (message.request != null) {
		// The body is stored ONCE. Where the wire copy matched the preview it was elided, so it is
		// spliced back in here: Raw shows the request as it was POSTed either way, and the row does
		// not carry the largest column on the table twice.
		const request = message.request as { body?: Record<string, unknown> } | null;
		if (request?.body && request.body.html === SAME_AS_PREVIEW && message.html) {
			return JSON.stringify({ ...request, body: { ...request.body, html: message.html } }, null, 2);
		}
		return JSON.stringify(message.request, null, 2);
	}
	if (!message.html) return null;
	// RECONSTRUCTED, and it says so. Messages sent before the ledger stored the wire request have
	// no record of it, so this is assembled from the columns that survive — which means the two
	// fields most likely to be at fault in a delivery failure, `from` and `reply_to`, are exactly the
	// ones missing. Presenting that as "the POST" would have an operator read an invented envelope
	// as evidence of what was sent.
	return JSON.stringify(
		{
			_note:
				'Reconstructed from the stored message — this send predates the ledger keeping the ' +
				'wire request, so `from`, `reply_to` and any attachments are not recorded.',
			to: [message.to_address],
			subject: message.subject,
			html: message.html
		},
		null,
		2
	);
}

/**
 * The request line as the ledger recorded it — `POST /emails`.
 *
 * The same thing Resend's own message view shows, and the shortest honest statement of "we did call
 * them": for a suppressed row that is exactly the fact in question, and the Raw tab is a poor place
 * to have to go and find it.
 *
 * The PATH only, not the host. The host is a constant of the deployment and adds a line's worth of
 * noise to a field that has to fit a quarter of a row; a reader who wants it has the Raw tab.
 *
 * `null` when there is no request — a row written before `0118_comms_request` — so the caller can
 * say "Not recorded" rather than render an empty field.
 */
export function requestLine(message: CommsMessage): string | null {
	const request = message.request as { method?: unknown; url?: unknown } | null | undefined;
	if (!request) return null;
	const method = typeof request.method === 'string' ? request.method : null;
	const url = typeof request.url === 'string' ? request.url : null;
	if (!method && !url) return null;
	let path = url;
	if (url) {
		try {
			path = new URL(url).pathname;
		} catch {
			// Not an absolute URL. Keep it verbatim rather than dropping the field: whatever was
			// recorded is more use than nothing, and this is a display string.
			path = url;
		}
	}
	return [method, path].filter(Boolean).join(' ') || null;
}
