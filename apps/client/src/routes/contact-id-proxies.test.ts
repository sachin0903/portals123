/**
 * The proxies that take `contactId` from a JSON BODY — the one carrier the layout does not
 * control, since any script on the page can post whatever it likes. Each used to splice the value
 * into the API URL raw (`?contact_id=${body.contactId}`), so a string body value rewrote the query
 * the API received. They all go through `contactIdQuery` now; this pins that they do.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST as send } from './messages/send/+server';
import { POST as read } from './messages/read/+server';
import { POST as mark } from './notifications/mark/+server';
import { POST as dismissAll } from './notifications/dismiss-all/+server';

vi.mock('$lib/api', () => ({
	apiUrl: () => 'http://api.test',
	authHeaders: (t: string | null) => ({ authorization: `Bearer ${t}` }),
	jsonHeaders: (t: string | null) => ({ authorization: `Bearer ${t}` }),
	detail: async () => 'failed'
}));

type Handler = (event: unknown) => Promise<Response>;

async function posted(handler: Handler, body: unknown) {
	let url = '';
	const fetch = vi.fn(async (u: string) => {
		url = u;
		return new Response(JSON.stringify({ ok: true }), { status: 200 });
	});
	await handler({
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		fetch,
		request: new Request('http://portal.test/x', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(body)
		})
	});
	return url;
}

describe('contact_id from a JSON body', () => {
	it.each([
		[
			'messages/send',
			send as unknown as Handler,
			{ text: 'hi' },
			'http://api.test/messaging/thread/messages'
		],
		['messages/read', read as unknown as Handler, {}, 'http://api.test/messaging/thread/read'],
		[
			'notifications/mark',
			mark as unknown as Handler,
			{},
			'http://api.test/notifications/read-all'
		],
		[
			'notifications/dismiss-all',
			dismissAll as Handler,
			{},
			'http://api.test/notifications/dismiss-all'
		]
	])('%s forwards an integer and drops anything else', async (_name, handler, base, apiPath) => {
		expect(await posted(handler, { ...base, contactId: 7 })).toBe(`${apiPath}?contact_id=7`);
		expect(await posted(handler, { ...base, contactId: '7' })).toBe(`${apiPath}?contact_id=7`);
		expect(await posted(handler, { ...base, contactId: '7&scope=all' })).toBe(apiPath);
		expect(await posted(handler, { ...base, contactId: 'abc' })).toBe(apiPath);
		expect(await posted(handler, base)).toBe(apiPath);
	});
});
