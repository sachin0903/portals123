/**
 * SSR coverage for the shared thread renderer — the one implementation behind the platform inbox's
 * direct messages, its group feed and the client record's Messages section. Every rule pinned here
 * is one the three surfaces had drifted apart on before they shared this component, and each fails
 * silently: a thread that reads wrong renders perfectly.
 */
import { describe, expect, it } from 'vitest';
import type { ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import MessageThread from './message-thread.svelte';
import type { ApiGroupFeedMessage, ApiMessage } from './messages.js';

const base = {
	conversation_id: 1,
	conversation_uid: 'cnv_1',
	sender_is_support: false,
	attachments: [],
	request_item: null
};

const CLIENT_REPLY: ApiMessage = {
	...base,
	id: 1,
	sender_kind: 'client',
	sender_user_id: null,
	sender_name: null,
	sender_role: null,
	sent_by_me: false,
	body: 'Here are my statements',
	created_at: '2026-09-10T09:00:00Z'
};

/** Two minutes after the adviser's first message — inside the run gap, same sender. */
const STAFF_ONE: ApiMessage = {
	...base,
	id: 2,
	sender_kind: 'staff',
	sender_user_id: 'usr-1',
	sender_name: 'Craig Currie',
	sender_role: 'Administrator',
	sent_by_me: true,
	body: 'Thanks, looking now',
	created_at: '2026-09-10T09:05:00Z'
};

const STAFF_TWO: ApiMessage = {
	...STAFF_ONE,
	id: 3,
	body: 'All received',
	created_at: '2026-09-10T09:07:00Z'
};

const download = (conversationUid: string, attachmentUid: string) =>
	`/chats/${conversationUid}/attachments/${attachmentUid}`;

type ThreadProps = ComponentProps<typeof MessageThread>;

const html = (props: Partial<ThreadProps> & Pick<ThreadProps, 'messages'>) =>
	render(MessageThread, {
		props: { clientName: () => 'James Mitchell', download, ...props }
	}).body;

describe('MessageThread', () => {
	it('sits a short thread on the composer, not at the top of the pane', () => {
		// A conversation grows upwards from the box you type into. Hung from the top, two messages
		// left a metre of empty card between them and the composer. `mt-auto` rather than
		// `justify-end` on the scroller: with content taller than the pane, `justify-end` puts the
		// first message above the scroll origin where it cannot be reached.
		const body = html({ messages: [CLIENT_REPLY] });
		expect(body).toContain('mt-auto');
		expect(body).not.toContain('justify-end');
	});

	it('heads each day with a quiet pill, and no rule across the thread', () => {
		// A fixed past date, so the label is the absolute one whenever this runs — `formatDayLabel`
		// reads the real clock, and a fixture near today says "Yesterday" one day and a date the
		// next.
		const body = html({ messages: [{ ...CLIENT_REPLY, created_at: '2026-03-12T09:00:00Z' }] });
		expect(body).toContain('12 March 2026');
		// The label used to sit ON a hairline spanning the pane. A line drawn across a conversation
		// reads as a break in it rather than as a date, and the pill carries the day on its own.
		expect(body).not.toContain('bg-border absolute inset-x-0 top-1/2 h-px');
	});

	it('draws no search box of its own', () => {
		// The box lives in the pane's header row, which is `shrink-0` — here it would scroll away
		// with the very messages it filters. The thread takes the TERM and nothing else.
		expect(html({ messages: [CLIENT_REPLY] })).not.toContain('Search this conversation');
	});

	it('narrows the thread to what matches, on the body OR the sender', () => {
		// CLIENT-SIDE, and honest here: a conversation is loaded whole, so there is no "not on this
		// page" for the filter to lie about — the trap `book/listing/query.ts` exists to avoid.
		const body = html({
			search: 'transfer',
			messages: [
				{ ...CLIENT_REPLY, id: 1, body: 'about the transfer forms' },
				{ ...CLIENT_REPLY, id: 2, body: 'thanks for the call' }
			]
		});
		expect(body).toContain('about the transfer forms');
		expect(body).not.toContain('thanks for the call');
	});

	it('also matches what a bodiless row shows — a request title or an attachment name', () => {
		// A request card reads "Requested: Passport" and an attachment-only message reads its
		// filename; a word the reader can see on the row and cannot search for is a broken search.
		const body = html({
			search: 'passport',
			messages: [
				{
					...CLIENT_REPLY,
					id: 1,
					body: null,
					attachments: [
						{
							id: 1,
							uid: 'att_1',
							filename: 'passport-scan.pdf',
							content_type: 'application/pdf',
							size_bytes: 10
						}
					]
				},
				{ ...CLIENT_REPLY, id: 2, body: 'thanks for the call' }
			]
		});
		expect(body).toContain('passport-scan.pdf');
		expect(body).not.toContain('thanks for the call');
	});

	it('quotes the term back when nothing matches', () => {
		// "No messages" on a thread the reader can see has messages in it reads as a fault rather
		// than as a miss.
		const body = html({ search: 'zzz', messages: [CLIENT_REPLY] });
		expect(body).toContain('No messages match');
		expect(body).toContain('zzz');
	});

	it('draws one header per run, not per message', () => {
		const body = html({ messages: [CLIENT_REPLY, STAFF_ONE, STAFF_TWO] });
		// Two consecutive messages from the same person inside the run gap share one name/role/time
		// header; repeating it above every one-word reply is what runs exist to stop.
		expect(body.match(/Craig Currie/g)?.length).toBe(1);
		expect(body).toContain('All received');
		expect(body).toContain('Thanks, looking now');
	});

	it('puts the tail on the first bubble of a run only', () => {
		const body = html({ messages: [STAFF_ONE, STAFF_TWO] });
		// One un-rounded corner, on the sender's own side. On every bubble a run reads as several
		// separate people talking.
		expect(body.match(/rounded-tr-none/g)?.length).toBe(1);
	});

	it('mirrors only the row for your own messages, and colours the bubble', () => {
		const mine = html({ messages: [STAFF_ONE] });
		expect(mine).toContain('flex-row-reverse');
		expect(mine).toContain('bg-primary/10');
		// A client's message stays on the left in the muted bubble — `sent_by_me` is the server's
		// answer, and its absence must read as "not mine".
		const theirs = html({ messages: [CLIENT_REPLY] });
		expect(theirs).not.toContain('flex-row-reverse');
		expect(theirs).toContain('bg-muted');
	});

	it('names a client message from the surface, and a staff one from the row', () => {
		expect(html({ messages: [CLIENT_REPLY] })).toContain('James Mitchell');
		const staff = html({ messages: [STAFF_ONE] });
		expect(staff).toContain('Craig Currie');
		expect(staff).toContain('Administrator');
	});

	it('marks where the reader left off, above the whole run', () => {
		const body = html({ messages: [CLIENT_REPLY, STAFF_ONE], firstUnreadId: 1 });
		expect(body).toContain('aria-label="New messages"');
		expect(body).toContain('text-destructive');
	});

	it('stamps "Seen" under the last own message the other side has read, and only that one', () => {
		// Staff only. A client is never shown whether the firm has opened their message.
		const later: ApiMessage = { ...STAFF_ONE, id: 9, created_at: '2026-09-10T09:30:00Z' };
		const partial = html({ messages: [STAFF_ONE, later], seenUpTo: '2026-09-10T09:10:00Z' });
		expect(partial.match(/>Seen</g) ?? []).toHaveLength(0);
		const all = html({ messages: [STAFF_ONE, later], seenUpTo: '2026-09-10T10:00:00Z' });
		expect(all.match(/>Seen</g)).toHaveLength(1);
	});

	it('draws no New bar without a watermark', () => {
		expect(html({ messages: [CLIENT_REPLY] })).not.toContain('aria-label="New messages"');
	});

	it('builds every attachment link from the injected download route', () => {
		const withFile: ApiMessage = {
			...CLIENT_REPLY,
			attachments: [
				{
					id: 9,
					uid: 'att_9',
					filename: 'pension-statement.pdf',
					content_type: 'application/pdf',
					size_bytes: 251_000
				}
			]
		};
		const body = html({ messages: [withFile] });
		expect(body).toContain('/chats/cnv_1/attachments/att_9');
		expect(body).toContain('pension-statement.pdf');
		// The kind is a GLYPH now, not "PDF" set in a disc — a second filename beside the filename,
		// in a size small enough that reading it took longer than reading the icon does.
		expect(body).toContain('lucide-file-text');
		expect(body).not.toContain('>PDF<');
		// A fixed-width row, so a run of attachments does not make every bubble a different width.
		expect(body).toContain('w-64');
	});

	it('shows a request announcement as a read-only card', () => {
		const ask: ApiMessage = {
			...STAFF_ONE,
			body: null,
			request_item: { uid: 'itm_1', title: 'Photo ID', action: 'upload', status: 'pending' }
		};
		const body = html({ messages: [ask] });
		expect(body).toContain('Requested: Photo ID');
		expect(body).toContain('Pending');
	});

	describe('viewer="client"', () => {
		// The client's own /messages page: the same thread, seen from the other chair. Their own
		// messages become the filled bubble with no face and no name; the firm's keep theirs.
		const MINE: ApiMessage = { ...CLIENT_REPLY, sent_by_me: true };
		const THEIRS: ApiMessage = { ...STAFF_ONE, sent_by_me: false };

		it('draws own messages as the filled bubble, with no face and no name', () => {
			const body = html({ viewer: 'client', messages: [MINE] });
			expect(body).toContain('flex-row-reverse');
			expect(body).toContain('bg-primary text-primary-foreground');
			expect(body).not.toContain('bg-primary/10');
			expect(body).not.toContain('James Mitchell');
			expect(body).not.toContain('>Client<');
			// The time survives: it is the one header fact a run of your own still needs.
			expect(body).toMatch(/\d\d:\d\d/);
		});

		it('keeps the firm’s face and name, and never their role', () => {
			const body = html({ viewer: 'client', messages: [THEIRS] });
			expect(body).toContain('Craig Currie');
			expect(body).not.toContain('Administrator');
			expect(body).toContain('bg-muted');
			expect(body).not.toContain('flex-row-reverse');
		});

		it('names an unattributed firm row "Your firm", not "Staff"', () => {
			const anon: ApiMessage = { ...THEIRS, sender_name: null };
			expect(html({ viewer: 'client', messages: [anon] })).toContain('Your firm');
			expect(html({ messages: [anon] })).toContain('Staff');
		});

		it('never stamps a read receipt, even when the firm has read everything', () => {
			const later: ApiMessage = { ...MINE, id: 9, created_at: '2026-09-10T09:30:00Z' };
			expect(
				html({
					viewer: 'client',
					messages: [MINE, later],
					seenUpTo: '2026-09-10T10:00:00Z'
				})
			).not.toContain('>Seen<');
		});

		it('turns a pending upload request into the tap-to-upload card when asked to', () => {
			const ask: ApiMessage = {
				...THEIRS,
				body: null,
				request_item: { uid: 'itm_1', title: 'Photo ID', action: 'upload', status: 'pending' }
			};
			const live = html({ viewer: 'client', messages: [ask], onFulfil: () => {} });
			expect(live).toContain('<button');
			expect(live).toContain('Tap to upload a document');
			// Without a handler the card stays inert — and says so in the client's words.
			const inert = html({ viewer: 'client', messages: [ask] });
			expect(inert).not.toContain('Tap to upload');
			expect(inert).toContain('Requests page');
			// A fulfilled one reads "Sent" to the client, "Received" to the firm.
			const done: ApiMessage = {
				...ask,
				request_item: { ...ask.request_item!, status: 'fulfilled' }
			};
			expect(html({ viewer: 'client', messages: [done], onFulfil: () => {} })).toContain('Sent');
			expect(html({ messages: [done] })).toContain('Received');
		});
	});

	it('renders a group feed row through the same thread', () => {
		// `ApiGroupFeedMessage` is a different type that satisfies the same thread contract — one
		// SEND, tagged with everyone it reached.
		const batch: ApiGroupFeedMessage = {
			...base,
			id: 4,
			sender_kind: 'staff',
			sender_user_id: 'usr-1',
			sender_name: 'Craig Currie',
			sender_role: 'Administrator',
			sent_by_me: true,
			body: 'Statements due Friday',
			created_at: '2026-09-10T09:00:00Z',
			recipients: [
				{ contact_id: 10, contact_name: 'James Mitchell' },
				{ contact_id: 11, contact_name: 'Sarah Mitchell' }
			]
		};
		const body = html({ messages: [batch], clientName: () => 'Client' });
		expect(body).toContain('Statements due Friday');
	});

	it('heads the thread with a load error rather than an empty scroller', () => {
		const body = html({ messages: [], error: "Couldn't load this conversation." });
		expect(body).toContain("Couldn't load this conversation.");
	});
});
