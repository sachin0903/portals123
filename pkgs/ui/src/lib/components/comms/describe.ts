/**
 * The vocabulary of the outbound-email feed: what a status is called, what colour it carries, and
 * how a row names its recipient and its template.
 *
 * Pure, and separate from the components for the reason `book/listing/status-tone.ts` gives: a
 * status word must look the same wherever it appears, and a map that lives inside a component is a
 * map that gets copied into the next one.
 *
 * Colours defer to `statusToneClasses` (the one place a status colour is decided) rather than
 * naming Tailwind palette classes — a firm re-theming `--success` must re-theme this too.
 */

import type { StatusTone } from '../ui/status-badge/index.js';
import { sentenceCase } from '../../sentence-case.js';

/** `comms.messages.status`, exactly — the CHECK in migration 0111. */
export type MessageStatus =
	| 'unconfigured'
	| 'suppressed'
	| 'failed'
	| 'sent'
	| 'delayed'
	| 'delivered'
	| 'bounced'
	| 'complained';

/** One provider callback, as `GET /firm/comms/messages/{uid}` returns it. */
export interface CommsMessageEvent {
	event_type: string;
	/**
	 * Which PATH learned this — Svix's own id (`msg_…`) for a provider callback, `poll:{state}`
	 * for the safety net reading the current state. Only the first is an occurrence the provider
	 * reported; the second is a state we asked for, so it must not count as a repeat.
	 */
	provider_event_id?: string | null;
	occurred_at: string | null;
	created_at: string;
}

/** A row of the ledger. `html`/`events` arrive only on the detail read. */
export interface CommsMessage {
	id: number;
	uid: string;
	status: MessageStatus;
	subject: string;
	to_address: string;
	to_domain: string;
	recipient_kind: 'contact' | 'user' | 'external';
	recipient_contact_id: number | null;
	/** The client's PUBLIC id, and the only one a link may use — a client record is addressed by
	 *  its uid, and the routes that once took the serial above are gone. */
	recipient_contact_uid?: string | null;
	recipient_name: string | null;
	template_code: string | null;
	event_type: string | null;
	rule_uid: string | null;
	category: string | null;
	source: string;
	error: string | null;
	/**
	 * RESEND'S OWN id, which is the one to paste into their dashboard. Distinct from `uid`, which is
	 * ours and is what a URL and a support ticket name — the two look alike and are not, and showing
	 * only ours is why "this id is not the one Resend has" was a reasonable thing to wonder.
	 */
	provider_message_id: string | null;
	/** The envelope as it went, read out of the stored wire request. Absent on a row recorded before
	 *  the request was kept. */
	from_address?: string | null;
	reply_to?: string | null;
	created_at: string;
	sent_at: string | null;
	delayed_at: string | null;
	delivered_at: string | null;
	bounced_at: string | null;
	complained_at: string | null;
	html?: string | null;
	/** The JSON posted to the provider. Detail read only; listing rows omit it. */
	request?: Record<string, unknown> | null;
	events?: CommsMessageEvent[];
}

/** The endpoint's filter vocabulary for this firm's recent ledger (`?facets=true`). */
export interface CommsFacets {
	statuses: string[];
	template_codes: string[];
	event_types: string[];
	categories: string[];
}

/** What the toolbar holds. Mirrors the endpoint's query parameters one for one. */
export interface CommsFilters {
	status: string[];
	templateCode: string[];
	category: string[];
	eventType: string[];
	search: string;
	since: string;
	until: string;
}

/** One fetched page: the rows, and whether the ENDPOINT says there are more behind them. */
export interface CommsPage {
	items: CommsMessage[];
	hasMore: boolean;
}

export const EMPTY_COMMS_FILTERS: CommsFilters = {
	status: [],
	templateCode: [],
	category: [],
	eventType: [],
	search: '',
	since: '',
	until: ''
};

interface StatusMeta {
	label: string;
	tone: StatusTone;
	/** One sentence of what the status actually means, for the detail pane. */
	hint: string;
}

/**
 * A status, in the words a firm would use.
 *
 * Three groups, and the distinction between them is the whole point of the feed:
 *
 *  - **In flight or arrived** — `sent` is `info`, not `success`: handing a message to Resend is
 *    not delivery, and colouring it green is the exact overclaim the ledger exists to stop.
 *  - **Refused or lost** — `danger`. A bounce is the answer to "did they get it" and it is no.
 *  - **Never sent** — `neutral`. A suppressed send is a deliberate outcome of the deployment's own
 *    guard, not a fault, and tinting a dev box's entire feed red would read as a broken system.
 */
const STATUS_META: Record<MessageStatus, StatusMeta> = {
	sent: {
		label: 'Sent',
		tone: 'info',
		hint: 'Accepted by the email provider. Delivery has not been confirmed yet.'
	},
	delayed: {
		label: 'Delayed',
		tone: 'warning',
		hint: 'The provider is still retrying. The address may be slow, greylisting, or may not exist.'
	},
	delivered: {
		label: 'Delivered',
		tone: 'success',
		hint: "Accepted by the recipient's mail server. It does not mean anyone has read it."
	},
	// NO 'opened'/'clicked'. This product does not do read receipts: an open is a tracking pixel
	// loading, which Apple Mail Privacy Protection fires for every Apple Mail user whether or not a
	// human looked, and which a client blocking images never fires however carefully it was read.
	// Delivered is the last thing we can honestly claim. See ledger.NOT_TRACKED.
	bounced: {
		label: 'Bounced',
		tone: 'danger',
		hint: 'The recipient’s mail server refused it. The address may be wrong or closed.'
	},
	complained: {
		label: 'Marked as spam',
		tone: 'danger',
		hint: 'The recipient reported it as spam. Further mail to them may be blocked.'
	},
	failed: {
		label: 'Failed',
		tone: 'danger',
		hint: 'The send itself failed. Nothing reached the provider.'
	},
	suppressed: {
		label: 'Not sent',
		tone: 'neutral',
		hint: 'Held back before sending, so nothing reached the recipient.'
	},
	unconfigured: {
		label: 'Not sent',
		tone: 'neutral',
		hint: 'Email sending is switched off, so nothing was sent.'
	}
};

/** Neutral for an unrecognised status, never a guess — `status-tone.ts`'s rule: a word we do not
 *  know is not "probably fine", and tinting it green would state something we do not know. */
export function statusMeta(status: string): StatusMeta {
	return (
		STATUS_META[status as MessageStatus] ?? {
			label: humanise(status),
			tone: 'neutral' as StatusTone,
			hint: 'This status is not one the portal recognises.'
		}
	);
}

/** True when the status says the message never left. Those rows carry a REASON rather than a
 *  timeline, and the detail pane shows the reason instead of an empty set of stamps. */
export function neverSent(status: string): boolean {
	return status === 'suppressed' || status === 'unconfigured' || status === 'failed';
}

const TEMPLATE_LABELS: Record<string, string> = {
	invite: 'Invitation',
	password_reset: 'Password reset',
	document_sent: 'Document sent',
	request_raised: 'Document request',
	task_notice: 'Task notice',
	notification: 'Notification'
};

/** `snake_case` → "Snake case". The fallback for any code or category the portal has no name for,
 *  so a template a firm adds later reads as words rather than as a key.
 *
 *  Kept under this name and this signature — an unnamed code prints as ITSELF here rather than as
 *  the book's em dash, because it lands mid-sentence — but the transformation is the shared one. */
export const humanise = sentenceCase;

export function templateLabel(code: string | null): string {
	if (!code) return 'No template';
	return TEMPLATE_LABELS[code] ?? humanise(code);
}

/**
 * Who it went to, in one line.
 *
 * The NAME when the ledger recorded a client, the address otherwise — and the address always
 * appears somewhere on the row regardless, because "did it go to the right address" is half of
 * what this feed is for and a name alone cannot answer it.
 */
export function recipientLabel(message: CommsMessage): string {
	return message.recipient_name?.trim() || message.to_address;
}
