/**
 * The client record's Messages load — specifically, which key it names the conversation by.
 *
 * `0112_messaging_uids` moved conversations, groups and attachments to uid addressing
 * (docs/api.md → "Addressing"), and this load was the one straggler: it opened the channel and then
 * fetched the thread as `/messaging/conversations/${conversation.id}/messages`. The open call
 * returns BOTH `id` and `uid`, so passing the serial was not a type error and not a crash — the API
 * answered a well-formed 404 and the page rendered its load-error banner. Every other call on this
 * surface (`ClientChat`'s send, read and attachment posts) already used the uid.
 *
 * The uid sweep's static scan (`apps/admin/src/route-addressing.ts`) does not cover this: it forbids
 * route segments named for a database key and LINKS interpolating one, and this is a server-side
 * fetch to the API. Hence a behavioural test on the URL itself.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const CONVERSATION = {
	id: 1,
	uid: 'cnv_7bf25db4e89d3e06',
	tenant_id: 3,
	contact_id: 3,
	contact_name: 'A Client'
};

function json(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

/** A parent() that grants messages:read, as the record layout does for a permitted operator. */
const parent = () =>
	Promise.resolve({
		detail: { contact: { id: CONVERSATION.contact_id } },
		canReadMessages: true,
		clientName: 'A Client'
	});

const locals = {
	user: { id: 'u1' },
	accessToken: 't',
	permissions: ['messages:write'],
	role: 'adviser'
};

const params = { uid: 'con_9b2e' };

/**
 * Answers the open POST with the conversation and the messages GET only when it is addressed by
 * UID — a serial 404s, exactly as the API does. `mockImplementation`, not `mockResolvedValue`: one
 * Response's body can only be read once (the LRU-cache trap recorded in CLAUDE.md).
 */
function harness(): { fetch: ReturnType<typeof vi.fn>; urls: string[] } {
	const urls: string[] = [];
	const fetch = vi.fn((url: string, init?: RequestInit) => {
		urls.push(url);
		if (init?.method === 'POST') return Promise.resolve(json(CONVERSATION));
		if (url.includes(`/conversations/${CONVERSATION.uid}/messages`)) {
			return Promise.resolve(json([{ uid: 'msg_1', body: 'hello' }]));
		}
		return Promise.resolve(json({ detail: 'Conversation not found' }, 404));
	});
	return { fetch, urls };
}

/**
 * Runs the load the way SvelteKit does: with a `depends`, and with the SAME fetch on the global —
 * the thread streams, so the reads that follow the conversation open go through `globalThis.fetch`
 * (they cannot be tracked as dependencies after `load` has returned) and must hit this harness too.
 */
async function runLoad(event: never) {
	const typed = event as { fetch: typeof fetch };
	const original = globalThis.fetch;
	globalThis.fetch = typed.fetch;
	try {
		const loaded = (await load({ depends: () => {}, ...(event as object) } as never)) as {
			thread: Promise<Record<string, unknown>>;
		};
		return { ...loaded, ...(await loaded.thread) } as never;
	} finally {
		globalThis.fetch = original;
	}
}

describe("the client record's Messages load", () => {
	it('fetches the thread by conversation uid, not by its serial id', async () => {
		const { fetch, urls } = harness();
		const data = (await runLoad({ locals, fetch, parent, params } as never)) as {
			messages: unknown[];
			loadError: boolean;
		};

		const messagesUrl = urls.find((u) => u.includes('/messages'));
		expect(messagesUrl).toContain(`/conversations/${CONVERSATION.uid}/messages`);
		// The regression, stated as the thing it must never be again.
		expect(messagesUrl).not.toContain(`/conversations/${CONVERSATION.id}/messages`);
		expect(data.messages).toHaveLength(1);
		expect(data.loadError).toBe(false);
	});

	it('reports loadError rather than an empty thread when the fetch fails', async () => {
		// An empty array and a failed read are different facts: one says "no messages yet", the
		// other "we could not tell". This is the branch the id/uid slip was landing in.
		const fetch = vi.fn((_url: string, init?: RequestInit) =>
			Promise.resolve(init?.method === 'POST' ? json(CONVERSATION) : json({}, 500))
		);
		const data = (await runLoad({ locals, fetch, parent, params } as never)) as {
			messages: unknown[];
			loadError: boolean;
		};
		expect(data.loadError).toBe(true);
		expect(data.messages).toEqual([]);
	});

	it('reports loadError when a failed open finds no existing thread', async () => {
		const fetch = vi.fn((_url: string, init?: RequestInit) => {
			if (init?.method === 'POST')
				return Promise.resolve(json({ detail: 'Contact not found' }, 404));
			return Promise.resolve(json([]));
		});
		const data = (await runLoad({ locals, fetch, parent, params } as never)) as {
			loadError: boolean;
			conversation: unknown;
		};
		expect(data.loadError).toBe(true);
		expect(data.conversation).toBeNull();
	});

	it('reopens an existing thread when creating one fails', async () => {
		const fetch = vi.fn((url: string, init?: RequestInit) => {
			if (init?.method === 'POST')
				return Promise.resolve(json({ detail: 'Contact not found' }, 404));
			if (url.includes(`/conversations/${CONVERSATION.uid}/messages`))
				return Promise.resolve(json([]));
			if (url.includes('/messaging/conversations')) return Promise.resolve(json([CONVERSATION]));
			return Promise.resolve(json([]));
		});
		const data = (await runLoad({ locals, fetch, parent, params } as never)) as {
			loadError: boolean;
			conversation: { uid: string } | null;
		};
		expect(data.loadError).toBe(false);
		expect(data.conversation?.uid).toBe(CONVERSATION.uid);
	});

	it('does not mint a thread for a read-only visitor', async () => {
		const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
			if (url.includes('/messaging/conversations')) return Promise.resolve(json([]));
			return Promise.resolve(json([]));
		});
		const data = (await runLoad({
			locals: { ...locals, permissions: ['messages:read'] },
			fetch,
			parent,
			params
		} as never)) as {
			loadError: boolean;
			conversation: unknown;
		};
		expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
		expect(data.loadError).toBe(false);
		expect(data.conversation).toBeNull();
	});

	it('lists conversations for this contact only, never the unfiltered inbox', async () => {
		const urls: string[] = [];
		const fetch = vi.fn((url: string, init?: RequestInit) => {
			urls.push(url);
			if (init?.method === 'POST') return Promise.resolve(json(CONVERSATION));
			if (url.includes(`/conversations/${CONVERSATION.uid}/messages`)) {
				return Promise.resolve(json([]));
			}
			if (url.includes('/messaging/conversations')) return Promise.resolve(json([CONVERSATION]));
			return Promise.resolve(json([]));
		});
		await runLoad({
			locals: { ...locals, permissions: ['messages:read'] },
			fetch,
			parent,
			params
		} as never);

		const listed = urls.filter(
			(u) => u.includes('/messaging/conversations') && !u.includes('/messages')
		);
		expect(listed.length).toBeGreaterThan(0);
		for (const u of listed) {
			expect(u).toContain(`contact_id=${CONVERSATION.contact_id}`);
		}
		expect(listed.some((u) => /\/messaging\/conversations\/?$/.test(u.split('?')[0]))).toBe(true);
		expect(
			listed.some((u) => u.includes('/messaging/conversations') && !u.includes('contact_id='))
		).toBe(false);
	});
});
