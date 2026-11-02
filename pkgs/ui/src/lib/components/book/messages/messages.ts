// Types + pure presentation helpers for the real client<->adviser chat (apps/api's `messaging`
// schema, app/messaging.py). "Mine" here means a STAFF-sent message — the mirror of
// apps/client's same-named helpers, where "mine" means client-sent.
import type { Component } from 'svelte';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import FileSpreadsheetIcon from '@lucide/svelte/icons/file-spreadsheet';
import FileImageIcon from '@lucide/svelte/icons/file-image';
import PresentationIcon from '@lucide/svelte/icons/presentation';

export type ApiAttachment = {
	id: number;
	/** What a download path names (0112_messaging_uids) — `id` stays the schema's own key. */
	uid: string;
	filename: string;
	content_type: string;
	size_bytes: number;
};

export type ApiRequestItem = {
	uid: string;
	title: string;
	action: 'upload' | 'agree' | 'acknowledge' | 'complete_form';
	status: 'pending' | 'fulfilled' | 'cancelled';
};

export type ApiMessage = {
	id: number;
	conversation_id: number;
	/** The conversation's public handle — what a link to it is built from (0091). */
	conversation_uid: string;
	sender_kind: 'client' | 'staff';
	sender_user_id: string | null;
	/** The staff sender acted from Graphene Operations into the firm's targeted workspace. */
	sender_is_support?: boolean;
	// Resolved server-side (auth.users/auth.organisation_memberships/auth.roles) for the
	// Slack-style header — null for a client-sent message, which has no auth.users row at all;
	// the frontend uses the conversation's own contact_name for that case instead.
	sender_name: string | null;
	/** Whether the CALLER sent this — the server's answer, since a browser holds a WorkOS id and
	 *  the row holds an `auth.users` uuid. Optional because a live push deliberately omits it
	 *  (`live_push.push`): the flag is a statement about one reader and a broadcast has many,
	 *  so its absence must read as "not mine". */
	sent_by_me?: boolean;
	sender_role: string | null;
	// Nullable since 0081_messaging_attachments — a message may be attachment-only.
	body: string | null;
	created_at: string;
	/** Every file on this message, in the order they were sent. A message may carry SEVERAL
	 *  (0123 drops the one-per-message UNIQUE), so a note and the documents it is about arrive
	 *  as one thing rather than as one bare message per file. Empty when it carries none. */
	attachments: ApiAttachment[];
	// Set since 0083_messaging_request_items when this message announces a document request —
	// read LIVE off documents.request_items (never denormalised), so `status` always reflects
	// reality even if the item was fulfilled some other way (the Requests page).
	request_item: ApiRequestItem | null;
};

// Allowed attachment types (accept attr + validation error copy) — kept in sync with
// app/storage.py's `_ATTACHMENT_EXT` allowlist by hand; there is no shared contract file for
// this (unlike block-contract.json/flow-contract.json) because the allowlist is a fixed, rarely
// changing set, not an authored/extensible registry.
export const ATTACHMENT_ACCEPT =
	'.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,application/pdf,application/msword,' +
	'application/vnd.openxmlformats-officedocument.wordprocessingml.document,' +
	'application/vnd.ms-excel,' +
	'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,' +
	'application/vnd.ms-powerpoint,' +
	'application/vnd.openxmlformats-officedocument.presentationml.presentation,text/plain';

/**
 * The GLYPH for an attachment's kind — a document, a sheet, a slide deck or a picture.
 *
 * What a file row shows. "PDF" set in a disc was a second filename beside the filename, in a
 * typeface small enough that the kind it named took longer to read than the icon does; and the
 * extension is on the name behind it anyway. The four kinds are the ones the uploader accepts.
 */
export function attachmentIcon(attachment: ApiAttachment): Component {
	const type = attachment.content_type;
	if (type.startsWith('image/')) return FileImageIcon as unknown as Component;
	if (type.includes('spreadsheet') || type.includes('excel')) {
		return FileSpreadsheetIcon as unknown as Component;
	}
	if (type.includes('presentation') || type.includes('powerpoint')) {
		return PresentationIcon as unknown as Component;
	}
	return FileTextIcon as unknown as Component;
}

export type ApiConversation = {
	id: number;
	/** What a `/chats/{uid}` path names (0112_messaging_uids); docs/api.md → "Addressing". */
	uid: string;
	tenant_id: number;
	contact_id: number;
	/** The client's own public handle. What a surface holding only a conversation needs to ask the
	 *  book anything about them — `GET /crm/contacts/{contact_uid}` is uid-addressed, so without
	 *  this the inbox could not read its own client's advisers. */
	contact_uid: string;
	contact_name: string | null;
	adviser_user_id: string | null;
	client_last_read_at: string | null;
	staff_last_read_at: string | null;
	created_at: string;
	last_message: string | null;
	last_message_at: string | null;
	unread: boolean;
};

// Groups (schema `messaging`, app/messaging_groups.py) — a STAFF-ONLY saved shortcut to a set of
// related clients (households/joint accounts), never a new kind of conversation. A client has no
// visibility that grouping exists at all: sending "to a group" fans out into each member's own
// real conversation via the ordinary send path, so `ApiGroup` never appears anywhere client-side.
// No `unread`: every group-tagged message is staff-sent, so there's nothing analogous to a
// client's unread reply to flag, and a group carries no read-receipt watermark of its own.
export type ApiGroup = {
	id: number;
	/** What a `/chats/groups/{uid}` path names (0091). */
	uid: string;
	name: string;
	created_at: string;
	last_message: string | null;
	last_message_at: string | null;
};

// One row of a group's feed — one SEND, not one recipient: a message fanned out to N members
// collapses into a single entry tagged with every recipient (`recipients`), rather than N
// identical-looking bubbles. USUALLY staff-sent (a text/attachment send, or a request raised
// against the group) — the one exception is a CLIENT'S fulfilment of a request that was itself
// raised via the group, which is group-tagged the same way and surfaces here too, tagged with
// just that one client (`recipients.length === 1`). `conversation_id` is one representative
// recipient's own copy — every member's row is identical content, so any one of them can serve
// the attachment download via the ordinary per-conversation attachment route. `request_item` is
// read live off `documents.request_items`, same as `ApiMessage`'s — a multi-member request's
// own status is inherently ambiguous (one member might have fulfilled, another not), so treat it
// as "at least one recipient's view", not an aggregate.
export type ApiGroupFeedMessage = {
	id: number;
	conversation_id: number;
	conversation_uid: string;
	sender_kind: 'client' | 'staff';
	sender_user_id: string | null;
	sender_is_support?: boolean;
	// Same resolution as ApiMessage's — null for a client-sent fulfilment, which uses
	// `recipients[0].contact_name` instead.
	sender_name: string | null;
	/** Whether the CALLER sent this — see `ApiMessage`. The group feed has its own projection, so
	 *  the flag is added there as well; without it a group message read as nobody's. */
	sent_by_me?: boolean;
	sender_role: string | null;
	body: string | null;
	created_at: string;
	/** Every file on this message, in the order they were sent. A message may carry SEVERAL
	 *  (0123 drops the one-per-message UNIQUE), so a note and the documents it is about arrive
	 *  as one thing rather than as one bare message per file. Empty when it carries none. */
	attachments: ApiAttachment[];
	request_item: ApiRequestItem | null;
	recipients: { contact_id: number; contact_name: string | null }[];
};

/** The conversation pane's two views: what was said, and what was sent. Everything else is about
 *  the CLIENT rather than the conversation and lives in the rail. */
export type ConversationTab = 'chat' | 'files';

/** WHO THE CONVERSATION IS WITH, at the head of the inbox's rail — the one question the rail
 *  answered nothing about while answering every other question about the thread.
 *
 *  Served by `GET /chats/rail/{contact_uid}`, which already reads the contact detail for the
 *  advisers below, so it costs no extra request. Null for a reader without `contacts:read`, and
 *  never sent for a GROUP: that is several clients, and one name would be a false statement about
 *  who is on the other end. */
export type RailClient = {
	name: string | null;
	email?: string | null;
	phone?: string | null;
	/** Which disc to draw — a building for a trust or a company. Carried for the GLYPH only: the
	 *  panel states no "Type" row, because "Individual" beside a person's own name and face says
	 *  nothing a reader did not already know. */
	kind?: 'individual' | 'organisation';
	/** The facts an adviser would otherwise open the record for, each already NAMED by the helper
	 *  that owns its vocabulary (`serviceTypeLabel`) — this panel states them, it does not spell
	 *  them. NO REFERENCE and NO STATUS: a provider reference is a filing code for talking to a
	 *  custodian, and "Active" is the one fact nobody opens a thread to read. */
	serviceType?: string | null;
	dob?: string | null;
	/** The postal line WITHOUT the country — see the endpoint's own note on why the record keeps
	 *  the full one. */
	address?: string | null;
};

/** One of the client's accounts, as the rail summarises it: the wrapper and what it is worth.
 *  Served by the same read as the rest of the panel, so the band costs no request of its own. */
export type RailAccount = {
	uid: string;
	name: string | null;
	productType: string | null;
	value: number | null;
	/** What the account has made or lost. Stated beside the value because "is this going the right
	 *  way" is the second question anyone asks of a balance. */
	growth: number | null;
};

/** One note the firm has kept on this client, as the rail summarises it. */
export type RailNote = {
	uid: string;
	body: string;
	author: string | null;
	createdAt: string;
};

/** One person on the rail's People panel. `role` is the relationship on THIS conversation, as
 *  words — "Client", a team member's role on the client's own record (Adviser, Paraplanner,
 *  Administrator or Support, named by `clientTeamRoleLabel` in `book/clients/team.ts`), or a
 *  group member — and never a job title: the record says one thing about a person and a second
 *  vocabulary would disagree. */
export type RailPerson = {
	name: string | null;
	role: string | null;
	/** The CLIENT, rather than one of the firm's people around them: they wear the muted client
	 *  disc and everybody else wears their own colour. Said outright rather than inferred from
	 *  `role`, which is a display label and could be anything a group calls its members. */
	isClient?: boolean;
};

// What the THREAD RENDERER needs off a message — the fields `ApiMessage` and
// `ApiGroupFeedMessage` have in common. `MessageThread` draws a direct conversation and a group's
// merged feed with one implementation, and the two are otherwise unrelated types: a direct message
// names its own conversation, a feed row names one representative recipient's copy of a fan-out.
// Anything only one of them carries (a group row's `recipients`) reaches the thread through its
// `subline` snippet, never from here.
export type ThreadMessage = {
	id: number;
	conversation_uid: string;
	sender_kind: 'client' | 'staff';
	sender_user_id: string | null;
	sender_is_support?: boolean;
	sender_name: string | null;
	sent_by_me?: boolean;
	sender_role: string | null;
	body: string | null;
	created_at: string;
	attachments: ApiAttachment[];
	request_item: ApiRequestItem | null;
};

/** A day heading as the thread shows it: "Today", "Yesterday", or "14 March 2026". */
export function formatDayLabel(iso: string, now: Date = new Date()): string {
	const date = new Date(iso);
	const days = Math.round(
		(startOfDay(now).getTime() - startOfDay(date).getTime()) / (24 * 60 * 60 * 1000)
	);
	if (days === 0) return 'Today';
	if (days === 1) return 'Yesterday';
	return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
}

function startOfDay(date: Date): Date {
	return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/** A message time as "14:45". */
export function formatMessageTime(iso: string): string {
	return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

export function staffSenderLabel(
	message: {
		sender_name: string | null;
		sender_is_support?: boolean;
	},
	/** For the rare row the API could not name. "Staff" on the book; a client reads "Your firm" —
	 *  the true thing, rather than the assigned adviser's name on words they may not have written. */
	fallback = 'Staff'
): string {
	const name = message.sender_name ?? fallback;
	return message.sender_is_support ? `Graphene support · ${name}` : name;
}

/** A conversation-list row time: "14:45" today, "Yesterday", or "12 Mar". */
/** How recent a last message has to be to read "Now" rather than a clock time. */
const NOW_WINDOW_MS = 5 * 60 * 1000;

/** How far AHEAD of the reader's clock a timestamp may be and still count as just now.
 *
 *  A message is stamped by the SERVER and read against the browser's own clock, which ticks every
 *  ten seconds (`now.svelte.ts`) and is never exactly the server's. So a message sent this second
 *  routinely arrives "in the future" by a few seconds, and a floor at zero made the row show a
 *  clock time for one tick before flipping to "Now" — visible on every send. A minute swallows
 *  the tick and ordinary skew while still refusing a clock that is genuinely hours out. */
const CLOCK_SKEW_MS = 60 * 1000;

export function formatListTime(iso: string | null, now: Date = new Date()): string {
	if (!iso) return '';
	const date = new Date(iso);
	// JUST NOW reads as "now". A clock time on a message that landed thirty seconds ago makes the
	// reader do the arithmetic to find out whether it is new; five minutes is the same window the
	// thread already uses to decide two messages are one turn (`RUN_GAP_MS`).
	const age = now.getTime() - date.getTime();
	if (age > -CLOCK_SKEW_MS && age < NOW_WINDOW_MS) return 'Now';
	const days = Math.round(
		(startOfDay(now).getTime() - startOfDay(date).getTime()) / (24 * 60 * 60 * 1000)
	);
	if (days === 0) return formatMessageTime(iso);
	if (days === 1) return 'Yesterday';
	return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

/** Group consecutive messages under a day heading — index-keyed downstream (see
 *  @graphene/ui's activity-list `groupByDate`): a repeated label between non-adjacent days must
 *  not merge, so the input is trusted to be date-sorted and grouped by consecutive run only.
 *  Generic over the message shape so it serves both a conversation's `ApiMessage[]` and a
 *  group's merged `ApiGroupFeedMessage[]` — the two are otherwise unrelated types. */
/** How far apart two messages from the same person may be and still read as one turn. Five
 *  minutes is the convention every chat client settles on: long enough that a burst of short
 *  replies stays one block, short enough that a reply an hour later gets its own timestamp. */
const RUN_GAP_MS = 5 * 60 * 1000;

/** Consecutive messages from the SAME sender, within {@link RUN_GAP_MS}, as one run.
 *
 *  Without this, a thread repeats the sender's avatar, name, role and time on every single message
 *  — five short replies in the same minute rendered as five identical headers with one word under
 *  each, and the eye has to re-read who is speaking every time. A run draws that furniture once.
 *
 *  Grouped on `sender_kind` AND `sender_user_id`, not on the display name: two staff members can
 *  share a name, and merging their messages would attribute one person's words to another. A null
 *  user id is the client (a client has no `auth.users` row), which `sender_kind` already separates.
 *
 *  Typed as NON-EMPTY runs (`[T, ...T[]]`), which is what a caller reads: the thread keys its
 *  `{#each}` on the run's first message and draws that message's name, role and time above the
 *  whole run. A plain `T[][]` makes `run[0]` optional under `noUncheckedIndexedAccess` and pushes
 *  a `?.` (or a non-null assertion) into markup for a case the reducer cannot produce — every run
 *  is created as `[item]`.
 */
export function groupMessagesIntoRuns<
	T extends { created_at: string; sender_kind: string; sender_user_id: string | null }
>(messages: T[]): [T, ...T[]][] {
	return messages.reduce<[T, ...T[]][]>((runs, item) => {
		const run = runs[runs.length - 1];
		const last = run?.[run.length - 1];
		const sameSender =
			last !== undefined &&
			last.sender_kind === item.sender_kind &&
			last.sender_user_id === item.sender_user_id;
		const closeEnough =
			last !== undefined && Date.parse(item.created_at) - Date.parse(last.created_at) <= RUN_GAP_MS;
		// An unparseable date is NOT close enough: NaN comparisons are false, so a bad timestamp
		// starts its own run rather than silently joining whatever came before it.
		if (run && sameSender && closeEnough) run.push(item);
		else runs.push([item]);
		return runs;
	}, []);
}

export function groupMessagesByDay<T extends { created_at: string }>(
	messages: T[],
	now: Date = new Date()
): { label: string; items: T[] }[] {
	return messages.reduce<{ label: string; items: T[] }[]>((acc, item) => {
		const label = formatDayLabel(item.created_at, now);
		const last = acc[acc.length - 1];
		if (last && last.label === label) last.items.push(item);
		else acc.push({ label, items: [item] });
		return acc;
	}, []);
}
