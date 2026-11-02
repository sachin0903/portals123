/**
 * The /messages loader. The loader makes TWO calls — `/crm/home` (adviser display info)
 * and `/messaging/thread` (the real conversation) — and must
 * distinguish two states: a real thread, and a load failure (never substituted with fake-empty
 * data). Opening a thread no longer depends on an assigned adviser.
 *
 * `send`/`read`/`sendAttachment` moved off this route's form actions onto their own
 * `+server.ts` endpoints (send/, read/, attachments/) — see those files; there is no
 * dedicated test for them, matching the untested-proxy convention every other thin
 * auth-check-then-forward `+server.ts` route in this repo already follows.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type Adviser = { name: string; firm: string; email: string; phone: string };
type Conversation = { id: number; contact_id: number; unread: boolean };
type ApiMessage = { id: number; sender_kind: 'client' | 'staff'; body: string };

const ADVISER: Adviser = {
	name: 'Priya Raman',
	firm: 'Ashworth Wealth',
	email: 'priya@ashworth.test',
	phone: '0117 555 0100'
};
const CONVERSATION: Conversation = { id: 1, contact_id: 7, unread: false };
const MESSAGES: ApiMessage[] = [{ id: 1, sender_kind: 'staff', body: 'Hi there' }];

function stubFetch(opts: {
	adviser?: Adviser | null | number;
	thread?: { conversation: Conversation; messages: ApiMessage[] } | number;
}) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		calls.push(url);
		if (url.includes('/crm/home')) {
			const body = opts.adviser;
			return typeof body === 'number'
				? ({ ok: false, status: body } as unknown as Response)
				: ({
						ok: true,
						status: 200,
						json: async () => ({ adviser: body ?? null })
					} as unknown as Response);
		}
		if (url.includes('/messaging/thread')) {
			const body = opts.thread;
			return typeof body === 'number'
				? ({ ok: false, status: body } as unknown as Response)
				: ({ ok: true, status: 200, json: async () => body } as unknown as Response);
		}
		throw new Error(`Unexpected fetch: ${url}`);
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

type LoadArg = Parameters<typeof load>[0];

const BRANDING = {
	icon_url: 'https://cdn.test/ashworth.png',
	support: { email: 'help@ashworth.test', phone: '0117 555 0000' }
};
/** The firm as the contact when nobody is assigned ($lib/adviser::firmContact). */
const FIRM = {
	kind: 'firm',
	name: 'Ashworth Wealth',
	firm: 'Ashworth Wealth',
	email: 'help@ashworth.test',
	phone: '0117 555 0000',
	photoUrl: 'https://cdn.test/ashworth.png'
};

const run = (fetch: typeof globalThis.fetch, depends: (key: string) => void = () => {}) =>
	load({
		locals: {
			user: { id: 'u-me', email: 'jo@example.test' },
			accessToken: 'tok',
			branding: BRANDING,
			appName: 'Ashworth Wealth'
		},
		fetch,
		depends,
		parent: async () => ({ activeContactId: null })
	} as unknown as LoadArg) as Promise<{
		adviser: Adviser | null;
		conversation: Conversation | null;
		messages: ApiMessage[];
		loadError: boolean;
		immersive?: boolean;
	}>;

describe('adviser/messages loader', () => {
	it('registers the app:messages dependency the page invalidates', async () => {
		// The page re-reads the thread after every push and every send with
		// `invalidate('app:messages')`. Without this key that call is a no-op, and the only way to
		// refresh would be `invalidateAll()` — which is what produced the reload loop.
		const { fetch } = stubFetch({
			adviser: ADVISER,
			thread: { conversation: CONVERSATION, messages: MESSAGES }
		});
		const depends = vi.fn();
		await run(fetch, depends);
		expect(depends).toHaveBeenCalledWith('app:messages');
	});

	it('returns the real adviser and thread', async () => {
		const { fetch, calls } = stubFetch({
			adviser: ADVISER,
			thread: { conversation: CONVERSATION, messages: MESSAGES }
		});
		const data = await run(fetch);
		expect(data).toEqual({
			adviser: ADVISER,
			conversation: CONVERSATION,
			messages: MESSAGES,
			loadError: false,
			// The conversation is the screen below `lg`: the shell's top bar stands down and the
			// page's own header carries the way back (ClientShell `immersive`).
			immersive: true
		});
		expect(calls.some((c) => c.match(/\/crm\/home$/))).toBe(true);
		expect(calls.some((c) => c.match(/\/messaging\/thread$/))).toBe(true);
	});

	it('nobody assigned ⇒ the thread is with the FIRM: its name, icon and support contacts', async () => {
		// The chat still works without an adviser — whoever on the staff replies is named on their
		// own messages (the shared thread), and the header names the firm as the recipient.
		const { fetch } = stubFetch({
			adviser: null,
			thread: { conversation: CONVERSATION, messages: MESSAGES }
		});
		const data = await run(fetch);
		expect(data.adviser).toEqual(FIRM);
		expect(data.loadError).toBe(false);
	});

	it('flags a load failure when the thread fetch fails', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch({ adviser: null, thread: 404 });
		const data = await run(fetch);
		expect(data).toEqual({ adviser: FIRM, conversation: null, messages: [], loadError: true });
	});

	it('flags a load failure when the adviser fetch itself fails', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch({
			adviser: 503,
			thread: { conversation: CONVERSATION, messages: [] }
		});
		const data = await run(fetch);
		expect(data).toEqual({ adviser: null, conversation: null, messages: [], loadError: true });
	});

	it('flags a load failure when the thread fetch fails for a reason other than 404', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch({ adviser: ADVISER, thread: 500 });
		const data = await run(fetch);
		expect(data).toEqual({ adviser: ADVISER, conversation: null, messages: [], loadError: true });
	});
});

describe('adviser/messages thread', () => {
	it('draws the conversation on the shared thread and composer, in the client register', () => {
		// The page used to carry its own 500-line thread — a face, a name and a time over every
		// message, no bubbles, no runs — so nothing that improved the staff thread reached the
		// client. Now it is the SAME `MessageThread`/`MessageComposer` as the platform, told whose
		// eyes are on it: `viewer="client"` (own messages are the filled bubble, no "You"), no read
		// receipt, the tap-to-upload card wired to the fulfil dialog, and
		// the one-row composer where Enter sends on a keyboard (Shift+Enter breaks a line; the
		// shared composer keeps Enter as a newline on touch devices).
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		expect(source).toContain("from '@graphene/ui/book/messages'");
		expect(source).toContain('viewer="client"');
		expect(source).not.toContain('staff_last_read_at');
		expect(source).toContain('onFulfil={openFulfil}');
		expect(source).toContain('enterSends');
		expect(source).toContain('compact');
		expect(source).not.toContain('whitespace-pre-wrap'); // the shared thread's job, not a copy
	});

	it('names the FIRM as the recipient when nobody is assigned, by its whole name', () => {
		// `kind: 'firm'` ($lib/adviser): the firm's icon rather than a face, "Your firm · Client
		// support" under the name, and "Message Ashworth Wealth" rather than "Message Ashworth".
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		expect(source).toContain("adviser?.kind === 'firm'");
		expect(source).toContain('Your firm · Client support');
		expect(source).toContain('isFirm ? adviser.name : adviser.name.split(/\\s+/)[0]');
		expect(source).toContain('Building2Icon');
	});

	it('decides "mine" from sender_kind, and the New bar from the client watermark, once', () => {
		// The shared thread reads `sent_by_me`, which the API sets for a STAFF reader; a client is
		// the only client on their thread, so `sender_kind` is the answer — and a live push omits
		// the flag, which must still read as theirs. The "New" bar is computed from the first
		// render only: the effect marks the thread read straight away, and a derived value would
		// erase the bar as it did so.
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		expect(source).toContain("sent_by_me: m.sender_kind === 'client'");
		expect(source).toMatch(/const firstUnreadId = \(\(\) => \{[\s\S]*client_last_read_at/);
		expect(source).toMatch(
			/m\.sender_kind === 'staff' && \(!seenUpTo \|\| m\.created_at > seenUpTo\)/
		);
	});

	it('does not reload the whole tree from its own event stream', () => {
		// Measured before the fix: 138 EventSource connects, 138 `__data.json` reloads and 276
		// mark-read POSTs in fifteen seconds from ONE open tab. The stream effect must (a) key on a
		// primitive so a data reload does not restart it, (b) refresh only this page's loader, and
		// (c) treat the first `open` as the load it just did, not as a reconnect.
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		const effect = source.slice(source.indexOf('const refreshThread'), source.indexOf('</script>'));
		expect(effect).toContain("invalidate('app:messages')");
		expect(effect).not.toContain('invalidateAll()');
		expect(effect).toContain('const key = streamContactId;');
		expect(effect).not.toMatch(/\$effect\(\(\) => \{[^}]*data\.conversation/);
		expect(effect).toContain(
			"events.addEventListener('open', () => {\n\t\t\tif (opened) refresh();"
		);
	});
});
