/**
 * `/chats` — the conversation rail is a window. Search stays uncapped.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';
import { GET as searchConversations } from './search/+server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness(handler: (url: string) => Response = () => json([])) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		return handler(url);
	});
	return { fetch, calls };
}

const reader = {
	user: { id: 'u1' },
	permissions: ['messages:read'],
	role: null,
	accessToken: 't'
};

describe('/chats load', () => {
	it('does not cap the conversation list, and does not search', async () => {
		// REVERSED: it used to ask for the newest fifty. This list is not a window onto the inbox
		// — the page merges it with the whole contact book, so a client whose thread fell outside
		// that fifty came back as a directory row reading "No messages yet" about someone the firm
		// has been talking to for a year, and opening them wrote that blank over the real thread.
		const { fetch, calls } = harness();
		const data = (await load({
			locals: reader,
			fetch,
			url: new URL('http://portal.test/chats')
		} as never)) as { inbox: Promise<unknown> };
		await data.inbox;
		const conversation = calls.find((url) => url.includes('/messaging/conversations'));
		expect(conversation).not.toContain('limit=');
		expect(conversation).not.toContain('q=');
	});

	it('names the thread to open without searching the list for it', async () => {
		// `?conversation=` is the header search's landing. Forwarding it as `q` would filter the
		// book down to nothing and the inbox would have no row to select.
		const { fetch, calls } = harness();
		const data = (await load({
			locals: reader,
			fetch,
			url: new URL('http://portal.test/chats?conversation=cnv_1')
		} as never)) as { openConversation: string | null; inbox: Promise<unknown> };
		await data.inbox;
		expect(data.openConversation).toBe('cnv_1');
		const conversation = calls.find((url) => url.includes('/messaging/conversations'));
		expect(conversation).not.toContain('q=');
		expect(conversation).not.toContain('conversation=');
	});

	it('does not cap a searched list', async () => {
		const { fetch, calls } = harness();
		const data = (await load({
			locals: reader,
			fetch,
			url: new URL('http://portal.test/chats?q=ada')
		} as never)) as { inbox: Promise<unknown> };
		await data.inbox;
		const conversation = calls.find((url) => url.includes('/messaging/conversations'));
		expect(conversation).toContain('q=ada');
		expect(conversation).not.toContain('limit=');
	});
});

describe('/chats/search', () => {
	it('forwards the query without a limit', async () => {
		const { fetch, calls } = harness();
		await searchConversations({
			locals: reader,
			fetch,
			url: new URL('http://portal.test/chats/search?q=ada')
		} as never);
		expect(calls[0]).toContain('/messaging/conversations?q=ada');
		expect(calls[0]).not.toContain('limit=');
	});
});
