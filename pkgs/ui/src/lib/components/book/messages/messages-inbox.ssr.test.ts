/**
 * SSR coverage for the operations inbox — now wired to the real messaging API (props in, no
 * fixtures). SSR skips `$effect` entirely (no live connection, no fetch), so this only pins what the
 * initial server-rendered markup carries: the conversation list and its unread/preview state.
 * Interaction (selecting a row, sending, live push) is client-side and covered by
 * apps/api/tests/test_messaging.py at the API layer.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import MessagesInbox from './messages-inbox.svelte';
import MessagesInboxSkeleton from './messages-inbox-skeleton.svelte';
import type { ApiConversation, ApiGroup } from './messages.js';

const CONVERSATIONS: ApiConversation[] = [
	{
		id: 1,
		uid: 'cnv_1',
		tenant_id: 1,
		contact_id: 10,
		contact_uid: '1a2b3c4d5e6f',
		contact_name: 'James Mitchell',
		adviser_user_id: 'adv-1',
		client_last_read_at: null,
		staff_last_read_at: null,
		created_at: '2026-08-04T10:00:00Z',
		last_message: "I've uploaded my pension statements",
		last_message_at: '2026-08-04T10:24:00Z',
		unread: true
	},
	{
		id: 2,
		uid: 'cnv_2',
		tenant_id: 1,
		contact_id: 11,
		contact_uid: '9z8y7x6w5v4u',
		contact_name: 'Margaret O’Brien',
		adviser_user_id: 'adv-1',
		client_last_read_at: null,
		staff_last_read_at: '2026-08-01T10:00:00Z',
		created_at: '2026-03-12T09:00:00Z',
		last_message: 'Thank you for clarifying',
		last_message_at: '2026-03-12T10:02:00Z',
		unread: false
	}
];

const GROUPS: ApiGroup[] = [
	{
		id: 1,
		uid: 'grp_1',
		name: 'The Mitchell Household',
		created_at: '2026-08-01T09:00:00Z',
		last_message: 'Reminder: statements due Friday',
		last_message_at: '2026-08-03T09:00:00Z'
	}
];

/** The one route set every case here mounts against — named so a case that varies a PROP does not
 *  have to restate eleven builders to do it. */
const ROUTES = {
	conversations: '/chats',
	search: (q: string) => `/chats/search?q=${q}`,
	contacts: () => '/chats/contacts',
	groups: '/chats/groups',
	conversation: (uid: string) => `/chats/${uid}`,
	group: (uid: string) => `/chats/groups/${uid}`,
	conversationAttachments: (uid: string) => `/chats/${uid}/attachments`,
	groupAttachments: (uid: string) => `/chats/groups/${uid}/attachments`,
	read: (uid: string) => `/chats/${uid}/read`,
	download: (uid: string, attachment: string) => `/chats/${uid}/attachments/${attachment}`,
	events: '/messages/events'
};

const html = (conversations: ApiConversation[] = CONVERSATIONS, groups: ApiGroup[] = GROUPS) =>
	render(MessagesInbox, { props: { conversations, groups, routes: ROUTES } }).body;

describe('MessagesInbox', () => {
	it('lists conversations with their preview and contact name', () => {
		const body = html();
		expect(body).toContain('James Mitchell');
		expect(body).toContain('Margaret O’Brien');
		expect(body).toContain("I've uploaded my pension statements");
	});

	it('states no count under the title', () => {
		// "N conversations" named the threads rather than the clients on the list, and once the list
		// became the whole book the number answered a question nobody asks of an inbox.
		const body = html();
		expect(body).not.toContain('2 conversations');
		expect(body).not.toContain('2 clients');
	});

	it('renders an empty state when there is nobody to list', () => {
		const body = html([]);
		expect(body).toContain('No clients match.');
	});

	it('renders the composer for the first conversation', () => {
		expect(html()).toContain('Type a message...');
	});

	it('opens the conversation the header search asked for, not the first row', () => {
		// A message hit used to link `/chats/{uid}`, the thread proxy. The inbox now arrives with
		// that uid and selects it; without this the click opened the newest conversation instead.
		const body = render(MessagesInbox, {
			props: {
				conversations: CONVERSATIONS,
				groups: GROUPS,
				routes: ROUTES,
				openConversation: 'cnv_2'
			}
		}).body;
		const pressedBefore = (name: string) => {
			const at = body.indexOf(name);
			return [...body.slice(0, at).matchAll(/aria-pressed="(true|false)"/g)].at(-1)?.[1];
		};
		expect(pressedBefore('Margaret')).toBe('true');
		expect(pressedBefore('James Mitchell')).toBe('false');
	});

	it('offers no separate "new message" flow, even with write', () => {
		// The list IS the picker now — every client the reader may write to is a row, and clicking
		// one with no thread opens it. A "+" opening a dialog over a list that already holds the
		// same names was the two of them partitioning the book between them, so neither ever showed
		// the set you were actually choosing from.
		const body = html();
		expect(body).not.toContain('aria-label="New direct message"');
		expect(body).not.toContain('New direct message');
		expect(body).not.toContain('everyone in the book already has a channel');
	});

	it('docks one full-width composer at the foot of the thread', () => {
		// SSR never runs the effect that loads messages, so this is the empty state — which is
		// exactly where the composer used to float in the middle of the pane at half the width of
		// the messages it answers, then jump to the bottom when the first one arrived.
		const body = html();
		expect(body).toContain('w-full px-4 pb-4');
		expect(body).not.toContain('mx-auto w-1/2');
	});

	it('hides New and the composer without messages:write', () => {
		const body = render(MessagesInbox, {
			props: {
				conversations: CONVERSATIONS,
				groups: GROUPS,
				canWrite: false,
				routes: {
					conversations: '/chats',
					search: (q: string) => `/chats/search?q=${q}`,
					contacts: () => '/chats/contacts',
					groups: '/chats/groups',
					conversation: (uid: string) => `/chats/${uid}`,
					group: (uid: string) => `/chats/groups/${uid}`,
					conversationAttachments: (uid: string) => `/chats/${uid}/attachments`,
					groupAttachments: (uid: string) => `/chats/groups/${uid}/attachments`,
					read: (uid: string) => `/chats/${uid}/read`,
					download: (uid: string, attachment: string) => `/chats/${uid}/attachments/${attachment}`,
					events: '/messages/events'
				}
			}
		}).body;
		expect(body).not.toContain('Type a message...');
		expect(body).not.toContain('aria-label="New direct message"');
		expect(body).not.toContain('aria-label="New group"');
	});

	it('does not offer Groups unless the app asks for them', () => {
		// An empty "Groups / No groups yet" block is a standing invitation to a feature nobody here
		// uses, and it reads as something broken rather than as something unused. Everything behind
		// it stays live — this is one switch, not a deletion.
		const body = html();
		// And with no Groups there is no heading either: a section header over the only section is
		// a row of chrome between the search box and the first client.
		expect(body).not.toContain('Direct messages');
		expect(body).not.toContain('Groups');
		expect(body).not.toContain('The Mitchell Household');
		expect(body).not.toContain('aria-label="New group"');
	});

	it('shows the section, its rows and its empty state when it is asked for', () => {
		const shown = render(MessagesInbox, {
			props: { conversations: CONVERSATIONS, groups: GROUPS, showGroups: true, routes: ROUTES }
		}).body;
		expect(shown).toContain('Groups');
		// With two lists on screen the headings earn their place and both are drawn.
		expect(shown).toContain('Direct messages');
		expect(shown).toContain('The Mitchell Household');
		const none = render(MessagesInbox, {
			props: { conversations: CONVERSATIONS, groups: [], showGroups: true, routes: ROUTES }
		}).body;
		expect(none).toContain('No groups yet.');
	});

	it('removes every write affordance in read-only mode', () => {
		const body = render(MessagesInbox, {
			props: {
				conversations: CONVERSATIONS,
				groups: GROUPS,
				canWrite: false,
				routes: {
					conversations: '/chats',
					search: () => '/chats/search',
					contacts: () => '/chats/contacts',
					groups: '/chats/groups',
					conversation: (uid: string) => `/chats/${uid}`,
					group: (uid: string) => `/chats/groups/${uid}`,
					conversationAttachments: (uid: string) => `/chats/${uid}/attachments`,
					groupAttachments: (uid: string) => `/chats/groups/${uid}/attachments`,
					read: (uid: string) => `/chats/${uid}/read`,
					download: (uid: string, attachment: string) => `/chats/${uid}/attachments/${attachment}`,
					events: '/messages/events'
				}
			}
		}).body;
		expect(body).not.toContain('aria-label="Send message"');
		expect(body).not.toContain('aria-label="New direct message"');
		expect(body).not.toContain('aria-label="New group"');
	});
});

describe('MessagesInboxSkeleton (SSR)', () => {
	it('is the inbox — 72-wide list, thread, 80-wide rail — not a table plus activity rail', () => {
		const body = render(MessagesInboxSkeleton).body;
		expect(body).toContain('aria-label="Loading"');
		expect(body).toContain('lg:w-72');
		expect(body).toContain('lg:w-80');
		expect(body).toContain('lg:flex-row');
		expect(body).toContain('h-[calc(100svh-var(--topbar-h,4rem))]');
		expect(body).not.toContain('Recent activity');
		expect(body).not.toContain('lg:grid-cols-[minmax(0,1fr)_22rem]');
	});
});
