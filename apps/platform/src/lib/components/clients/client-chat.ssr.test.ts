/**
 * The client record's message thread — its WIDTH, which is the only thing here SSR can see and the
 * only thing that was wrong.
 *
 * THE DEFECT. Each row is a flex pair with `flex-1` on the content column, and a staff message is
 * `flex-row-reverse` + `items-end`. Nothing capped the column, and this route deliberately cancels
 * the shell's padding to run the card edge to edge — so on a wide screen the client's message sat
 * hard against the far left and the reply hard against the far right, a metre of empty card between
 * them, with nothing tying the two together as one conversation. The composer underneath was
 * `w-1/2`, so the box you typed into was half the width of the messages it answered.
 *
 * Asserted on the classes because that IS the fix: there is no behaviour to drive, and a layout
 * constraint removed by a later refactor would look fine in every other test.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ClientChat from './client-chat.svelte';
import type { ApiConversation, ApiMessage } from '@graphene/ui/book/messages';
// Types only: the BUILDERS moved with the cases that used them, but `RailProps` still names these.
import type { ApiRequest } from '@graphene/ui/book/documents';
import type { TaskRow } from '@graphene/ui/book/tasks';
import type { ContactAdviser } from '$lib/api';

const conversation: ApiConversation = {
	id: 1,
	uid: 'cnv_9b2e',
	tenant_id: 1,
	contact_id: 7,
	contact_uid: '7f3a2b1c9d4e',
	contact_name: 'Simple Accumulation',
	adviser_user_id: null,
	client_last_read_at: null,
	staff_last_read_at: null,
	created_at: '2026-08-24T08:57:00Z',
	last_message: 'hello',
	last_message_at: '2026-08-24T08:59:00Z',
	unread: false
};

/** A message from the SIGNED-IN adviser — what `sent_by_me` means on the wire. */
const mineMsg = (over: Partial<ApiMessage> = {}): ApiMessage =>
	message({
		sender_kind: 'staff',
		sender_user_id: 'u-me',
		sender_name: 'Craig Currie',
		sender_role: 'Administrator',
		sent_by_me: true,
		...over
	});

/** A message from a COLLEAGUE — staff, but not mine. The case the old `sender_kind === 'staff'`
 *  rule got wrong in both directions: drawn on my side, and labelled with the client's name. */
const colleagueMsg = (over: Partial<ApiMessage> = {}): ApiMessage =>
	message({
		sender_kind: 'staff',
		sender_user_id: 'u-sachin',
		sender_name: 'Sachin Sharma',
		sender_role: 'Adviser',
		sent_by_me: false,
		...over
	});

const message = (over: Partial<ApiMessage> = {}): ApiMessage => ({
	id: 1,
	conversation_id: 1,
	conversation_uid: 'cnv_9b2e',
	sender_kind: 'client',
	sender_user_id: null,
	sender_name: null,
	sender_role: null,
	body: 'hello',
	created_at: '2026-08-24T08:57:00Z',
	attachments: [],
	request_item: null,
	...over
});

type RailProps = {
	advisers?: ContactAdviser[];
	requests?: ApiRequest[];
	tasks?: TaskRow[];
	links?: { request: (uid: string) => string; task: (uid: string) => string };
};

const html = (
	messages: ApiMessage[] = [message()],
	rail: RailProps = {},
	over: Partial<ApiConversation> = {}
): string =>
	render(ClientChat, {
		props: { conversation: { ...conversation, ...over }, initialMessages: messages, ...rail }
	}).body;

const adviser = (over: Partial<ContactAdviser> = {}): ContactAdviser => ({
	adviser_id: 'u-1',
	role: 'adviser',
	name: 'Craig Currie',
	email: null,
	...over
});

describe('the client record thread', () => {
	it('keeps MY messages on the right and everyone else’s on the left', () => {
		// The rule is authorship, not role. The ROW is mirrored and my column pulls its bubbles to
		// the right edge, so a run of replies of different lengths shares one edge instead of a
		// ragged one. What must NOT follow is right-aligned PROSE: the bubble stays `items-start`
		// and its text is never `text-right`, which was the original defect — a reply whose own
		// words ran right-to-left against nothing.
		const body = html([message(), mineMsg({ id: 2 })]);
		expect(body).toContain('flex-row-reverse');
		expect(body).toContain('items-start');
		expect(body).not.toContain('text-right');
		expect(body).toContain('Simple Accumulation');
		expect(body).toContain('Craig Currie');
	});

	it('puts a COLLEAGUE’s message on the left, under their own name', () => {
		// The defect this rule replaced, in both of its halves. `sender_kind === 'staff'` drew every
		// colleague's reply on my side — a thread two advisers had both worked read as one person
		// talking to themselves — and, because the NAME followed the same flag, labelled it with the
		// client's name. Side and name are separate questions: not mine, but still staff.
		// Scoped to the THREAD: the rail's Team panel names the client on every render, so a
		// whole-page assertion would pass for the wrong reason.
		const body = html([colleagueMsg()]);
		const thread = body.slice(0, body.indexOf('aria-label="Client details"'));
		expect(thread).not.toContain('flex-row-reverse');
		expect(thread).toContain('Sachin Sharma');
		expect(thread).toContain('Adviser');
		expect(thread).not.toContain('Simple Accumulation');
	});

	it('treats a message with NO flag as not mine', () => {
		// A live push omits `sent_by_me` on purpose — it is a statement about one reader
		// and a broadcast has many (`live_push.push`). Absent must therefore read as "not mine",
		// or every adviser on the tenant would see a colleague's message drawn as their own.
		const body = html([message({ sender_kind: 'staff', sender_name: 'Sachin Sharma' })]);
		expect(body).not.toContain('flex-row-reverse');
	});

	it('keeps authored line breaks inside a bubble', () => {
		// Enter in the composer inserts a newline (Ctrl+Enter sends). Without `whitespace-pre-wrap`
		// those breaks collapse in the bubble, so a two-line message reads as one run-on sentence.
		const body = html([message({ body: 'Line one\nLine two' })]);
		expect(body).toContain('whitespace-pre-wrap');
		expect(body).toContain('Line one');
		expect(body).toContain('Line two');
	});

	it('puts the WHOLE message in the bubble — who, when and what', () => {
		// The name/role/time used to float outside the bubble, so a tinted box began mid-row under a
		// header that belonged to nothing.
		const body = html([message({ body: 'hello' })]);
		const bubble = body.slice(body.indexOf('items-start gap-3'));
		expect(bubble).toContain('Simple Accumulation');
		expect(bubble).toContain('Client');
		expect(bubble).toContain('hello');
	});

	it('tints the bubble by sender, using branded tokens', () => {
		const body = html([message(), mineMsg({ id: 2 })]);
		expect(body).toContain('bg-muted');
		expect(body).toContain('bg-primary/10');
		// The radius comes from `--radius`, so a tenant's corner setting reaches the bubble. Which
		// classes bypass it is `token-hygiene.test.ts`'s job firm-wide — and naming one here would
		// put the offending string in a file that scan also reads, failing it from the test that
		// asserts the rule (the false positive `switch-columns.test.ts` records hitting).
		expect(body).toContain('rounded-xl');
	});

	it('gives the conversation the whole pane', () => {
		// REVERSED AGAIN, and this is the settled answer. A capped, centred column left a channel of
		// empty card down each side of a wide pane and floated the firm's own replies off the edge
		// they are anchored to. Line length is not the container's problem — each bubble carries its
		// own `max-w-[42rem]`, so a wide monitor gets more conversation rather than longer lines.
		const body = html();
		expect(body).not.toContain('max-w-3xl');
		expect(body).toContain('max-w-[42rem]');
	});

	it('gives each bubble a tail on its sender’s side', () => {
		// The un-rounded corner is what points a bubble at whoever sent it. It is the same
		// background simply not curving away, so it needs no pseudo-element and re-brands with the
		// tint — but it is also invisible to every other test, hence this one.
		const body = html([message(), mineMsg({ id: 2 })]);
		expect(body).toContain('rounded-tl-none');
		expect(body).toContain('rounded-tr-none');
	});

	it('takes SEVERAL files, and says so on the control', () => {
		// Several files ride on ONE message since 0123 dropped 0081's `message_id` UNIQUE, so the
		// picker has to accept a set and the composer has to post a repeated `file` part.
		const body = html();
		expect(body).toContain('multiple');
		expect(body).toContain('aria-label="Attach files"');
	});

	it('names the client for a client-sent message, which carries no user of its own', () => {
		// A client has no `auth.users` row, so `sender_name` is null and the conversation's own
		// contact name is what the header has to fall back to.
		const body = html([message({ sender_name: null })]);
		expect(body).toContain('Simple Accumulation');
		expect(body).toContain('Client');
	});
});

/**
 * The right-hand rail - who is on the conversation, every file that has crossed it, and what the
 * client still owes.
 *
 * The two things worth pinning here are both invisible from the thread. Its VISIBILITY is pure CSS
 * (`hidden lg:block` until the reader clicks the toggle), which is what makes it right before
 * hydration and impossible to flash - an `{#if}` on a measured width would render nothing on the
 * server and pop in on desktop. And its Files panel is derived from the LIVE message list rather
 * than from a fetch, so a file sent while the rail is open appears in it; a server-side list would
 * go stale on the first send.
 */
describe('the thread rail', () => {
	it('shares ONE card with the thread, split by a hairline', () => {
		// REVERSED. This section used to sit on `--background` so it matched Documents and
		// Activity; the platform inbox puts the same conversation on a card, and the two surfaces
		// mount the same components — one floating on the page while the other sat on a card read
		// as two different screens for one conversation. The card is on the surface they share,
		// never a second box beside the first: the rail is divided from the thread by a hairline.
		const body = html();
		expect(body).toContain('lg:border-l');
		expect(body).toContain('bg-card');
		const aside = body.slice(
			body.lastIndexOf('<aside', body.indexOf('aria-label="Client details"'))
		);
		expect(aside.slice(0, aside.indexOf('>'))).not.toContain('bg-card');
	});

	it('is collapsible bands, and leaves Files to the conversation pane', () => {
		// PEOPLE, not "Team" — the same word the record's own section uses for the same set. The
		// bands fold; FILES is not one of them, because a file belongs to the thread rather than to
		// the client and is a tab on the pane beside this rail.
		const body = html();
		const panel = body.slice(body.indexOf('aria-label="Client details"'));
		expect(panel).toContain('People');
		expect(panel).not.toContain('Team');
		expect(panel).not.toContain('Files');
		expect(panel).toContain('data-collapsible-trigger');
	});

	it('does not collapse — the client is part of the surface, not a drawer on it', () => {
		// It used to fold behind a toggle on the thread, which put this client's own advisers and
		// what they owe one click away on the tab that exists to show them.
		const body = html();
		expect(body).toContain('aria-label="Client details"');
		expect(body).not.toContain('aria-label="Hide client details"');
		const aside = body.slice(
			body.lastIndexOf('<aside', body.indexOf('aria-label="Client details"'))
		);
		expect(aside.slice(0, aside.indexOf('>'))).not.toContain('hidden');
	});

	it('names everyone on the conversation, with their relationship', () => {
		// A band again rather than a popout, so the names are in the server's markup — and the role
		// is the relationship on THIS conversation, never a job title: the team role's key, named
		// by the one list the record uses.
		const body = html([message()], {
			advisers: [adviser(), adviser({ adviser_id: 'u-2', name: 'Mark Ross', role: 'paraplanner' })]
		});
		expect(body).toContain('Craig Currie');
		expect(body).toContain('Adviser');
		expect(body).toContain('Mark Ross');
		expect(body).toContain('Paraplanner');
	});

	it('offers the Chat and Files tabs over the thread', () => {
		// WHERE THE LISTS LIVE NOW, and what is worth pinning from this mount: the files are a tab
		// on the conversation pane, and the owed queues a band in the rail. What each of them
		// CONTAINS is covered against the components that draw them —
		// `conversation-panels.ssr.test.ts` for the files, `message-rail.ssr.test.ts` for the
		// queues — rather than re-rendered here to say the same thing twice.
		const body = html();
		expect(body).toContain('role="tablist"');
		expect(body).toContain('Chat');
		expect(body).toContain('Files');
		// And the box that searches the thread sits in that same reserved row.
		expect(body).toContain('aria-label="Search this conversation"');
	});
});

describe('the thread groups a run of messages', () => {
	it('draws the sender once for a run, not once per message', () => {
		// Five short replies in one minute used to render five identical
		// "Craig Currie · Administrator · 22:54" headers with one word under each.
		const run = [1, 2, 3].map((n) =>
			mineMsg({ id: n, body: `reply ${n}`, created_at: `2026-08-24T08:5${n}:00Z` })
		);
		const body = html(run);
		expect(body.split('Administrator').length - 1).toBe(1);
		expect(body).toContain('reply 1');
		expect(body).toContain('reply 3');
	});

	it('gives only the FIRST bubble of a run its tail', () => {
		// A tail on every bubble reads as several people talking rather than one person continuing.
		const run = [1, 2].map((n) =>
			mineMsg({ id: n, body: `reply ${n}`, created_at: `2026-08-24T08:5${n}:00Z` })
		);
		const body = html(run);
		expect(body.split('rounded-tr-none').length - 1).toBe(1);
	});

	it('starts a new run when the other side answers', () => {
		const body = html([
			message({ id: 1, body: 'a question' }),
			mineMsg({ id: 2, body: 'an answer' })
		]);
		expect(body).toContain('rounded-tl-none');
		expect(body).toContain('rounded-tr-none');
	});

	it('breaks each day with a quiet pill, and no rule across the thread', () => {
		// The label used to sit on a hairline spanning the pane. A line drawn across a conversation
		// reads as a break in it rather than as a date, so the pill carries the day on its own.
		// The fixture's date is fixed and `formatDayLabel` reads the real clock, so the LABEL is
		// whatever today makes it — the treatment is what this pins, not the word in it.
		const body = html();
		expect(body).not.toContain('bg-border absolute inset-x-0 top-1/2 h-px');
		expect(body).toContain('bg-muted/60 text-muted-foreground rounded-full');
	});
});

/**
 * The "New" bar — where the reader left off.
 *
 * The trap it is built around: `markRead()` fires on mount, so anything reading the watermark
 * reactively erases the marker at the one moment it is useful. It is computed once from the props
 * and never recomputed, which also keeps a message arriving while you watch from dragging the bar
 * down to itself.
 */
describe('the unread marker', () => {
	const clientAt = (id: number, created_at: string) =>
		message({ id, created_at, sender_kind: 'client', sender_user_id: null });
	const staffAt = (id: number, created_at: string) =>
		message({ id, created_at, sender_kind: 'staff', sender_user_id: 'u-1', sender_name: 'Craig' });

	it('marks the first client message newer than the watermark', () => {
		const body = html(
			[clientAt(1, '2026-08-24T08:00:00Z'), clientAt(2, '2026-08-24T10:00:00Z')],
			{},
			{ staff_last_read_at: '2026-08-24T09:00:00Z' }
		);
		expect(body).toContain('aria-label="New messages"');
	});

	it('shows nothing when everything has been read', () => {
		const body = html(
			[clientAt(1, '2026-08-24T08:00:00Z')],
			{},
			{
				staff_last_read_at: '2026-08-24T09:00:00Z'
			}
		);
		expect(body).not.toContain('aria-label="New messages"');
	});

	it('never marks the firm’s OWN replies as new', () => {
		// A watermark older than a reply you sent yourself would otherwise put "New" above your own
		// words. The firm's messages are read by definition.
		const body = html(
			[staffAt(1, '2026-08-24T10:00:00Z')],
			{},
			{
				staff_last_read_at: '2026-08-24T09:00:00Z'
			}
		);
		expect(body).not.toContain('aria-label="New messages"');
	});

	it('treats a never-read conversation as all new', () => {
		const body = html([clientAt(1, '2026-08-24T08:00:00Z')], {}, { staff_last_read_at: null });
		expect(body).toContain('aria-label="New messages"');
	});

	it('is red, from the tenant’s own palette', () => {
		// `--destructive`, so a firm re-branding it re-brands this, and no palette colour is named
		// directly (the rule `tones.ts` sets). Nothing else in the thread wears it, which is what
		// makes the bar findable while scrolling.
		const body = html([clientAt(1, '2026-08-24T08:00:00Z')], {}, { staff_last_read_at: null });
		const from = body.indexOf('aria-label="New messages"');
		const bar = body.slice(from, body.indexOf('>New<', from));
		expect(bar).toContain('bg-destructive/60');
		expect(bar).toContain('text-destructive');
	});
});

describe('the thread’s rules run edge to edge', () => {
	it('pads the RUNS, not the scroll container, so a divider reaches both edges', () => {
		// Inset by the container's padding, a day or unread rule reads as a box drawn inside a box —
		// it stops short of the edge and so divides nothing.
		const body = html();
		expect(body).toContain('overflow-y-auto py-5');
		expect(body).not.toContain('overflow-y-auto py-5 pr-4 pl-4');
		expect(body).toContain('pr-4 pl-4');
	});
});

describe('the composer’s send key', () => {
	it('sends on Ctrl+Enter here, and says so', () => {
		// A plain Enter sends on the CLIENT portal, where the box holds one short reply. On this side
		// it sits in a working day of typing, and a stray Enter mid-thought sent a half-written
		// message to a client with no way to take it back. The placeholder carries the shortcut
		// because a send key nobody can discover is a box that appears not to work.
		const body = html();
		expect(body).toContain('Ctrl+Enter to send');
	});

	it('hides the composer without messages:write', () => {
		const body = render(ClientChat, {
			props: {
				conversation,
				initialMessages: [message()],
				canWriteMessages: false
			}
		}).body;
		expect(body).not.toContain('Ctrl+Enter to send');
		expect(body).not.toContain('aria-label="Send message"');
	});
});
