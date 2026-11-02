/**
 * The group attachment proxy — that it forwards EVERY file it was given.
 *
 * The same defect the direct-message proxy carried (see the sibling test at
 * `chats/[uid]/attachments`): the body was rebuilt from `incoming.get('file')`, a single part,
 * while the inbox composer appends one part per staged file and the API takes `list[UploadFile]`.
 * Pinned separately because the two proxies are separate files that happen to agree — a fix applied
 * to one and not the other is exactly how this shape reappears.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST } from './+server';

type Handler = Parameters<typeof POST>[0];

async function forwarded(form: FormData) {
	const seen: { body: FormData | null } = { body: null };
	const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
		seen.body = (init?.body as FormData) ?? null;
		return new Response(JSON.stringify({ id: 1, attachments: [] }), { status: 200 });
	});
	const res = await POST({
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		params: { uid: 'grp_4a71' },
		request: new Request('http://portal.test/chats/groups/grp_4a71/attachments', {
			method: 'POST',
			body: form
		}),
		fetch
	} as unknown as Handler);
	return { res, sent: seen.body, calls: fetch.mock.calls.length };
}

describe('the group attachment proxy', () => {
	it('forwards EVERY file part, in order, on one request', async () => {
		const body = new FormData();
		for (const name of ['first.pdf', 'second.pdf'])
			body.append('file', new File([`bytes of ${name}`], name), name);
		body.append('text', 'for the group');
		const { res, sent, calls } = await forwarded(body);
		expect(res.status).toBe(200);
		expect(calls).toBe(1);
		expect((sent!.getAll('file') as File[]).map((f) => f.name)).toEqual([
			'first.pdf',
			'second.pdf'
		]);
		expect(sent!.get('text')).toBe('for the group');
	});

	it('refuses a send with no usable file, without calling the API', async () => {
		const body = new FormData();
		body.append('file', new File([], 'empty.pdf'), 'empty.pdf');
		body.append('text', '');
		const { res, calls } = await forwarded(body);
		expect(res.status).toBe(422);
		expect(calls).toBe(0);
	});
});
