/**
 * The staff attachment proxy — that it forwards EVERY file it was given.
 *
 * THE DEFECT. A message may carry several attachments, the composer appends one `file` part per
 * staged file, and the API takes a repeated part (`list[UploadFile]`). This proxy rebuilt the body
 * with `incoming.get('file')`, which returns only the FIRST — so a three-file send uploaded one
 * and reported success. Nothing caught it: the API accepted what it was sent, the response was a
 * well-formed message, and the two files that never left the browser are not visible from either
 * end. The same shape was in the group and client-portal proxies.
 *
 * Driving the handler directly with a real `FormData` is the only level this is visible at — the
 * component posts correctly and the API reads correctly; it was the hop between them that lost
 * them.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST } from './+server';

type Handler = Parameters<typeof POST>[0];

/** A request carrying `n` file parts under the same `file` key, as the composer sends them. */
function sendOf(names: string[], text = 'note'): FormData {
	const body = new FormData();
	for (const name of names) body.append('file', new File([`bytes of ${name}`], name), name);
	body.append('text', text);
	return body;
}

/** Runs the handler and hands back whatever multipart body it forwarded to the API. */
async function forwarded(form: FormData) {
	const seen: { body: FormData | null } = { body: null };
	const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
		seen.body = (init?.body as FormData) ?? null;
		return new Response(JSON.stringify({ id: 1, attachments: [] }), { status: 200 });
	});
	const res = await POST({
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		params: { uid: 'cnv_9b2e' },
		request: new Request('http://portal.test/chats/cnv_9b2e/attachments', {
			method: 'POST',
			body: form
		}),
		fetch
	} as unknown as Handler);
	return { res, sent: seen.body, calls: fetch.mock.calls.length };
}

describe('the staff attachment proxy', () => {
	it('forwards EVERY file part, in order, under the one key', async () => {
		const { res, sent } = await forwarded(sendOf(['first.pdf', 'second.pdf', 'third.pdf']));
		expect(res.status).toBe(200);
		const files = sent!.getAll('file') as File[];
		expect(files.map((f) => f.name)).toEqual(['first.pdf', 'second.pdf', 'third.pdf']);
	});

	it('sends ONE request, so the files stay on one message', async () => {
		// Several files is one message carrying several, not one message each — that is the whole
		// point of 0123, and a proxy that looped per file would undo it.
		const { calls } = await forwarded(sendOf(['a.pdf', 'b.pdf']));
		expect(calls).toBe(1);
	});

	it('carries the note alongside them', async () => {
		const { sent } = await forwarded(sendOf(['a.pdf'], 'here you go'));
		expect(sent!.get('text')).toBe('here you go');
	});

	it('refuses a send with no usable file, without calling the API', async () => {
		// An empty part is a browser's unfilled file input, not a file.
		const body = new FormData();
		body.append('file', new File([], 'empty.pdf'), 'empty.pdf');
		body.append('text', '');
		const { res, calls } = await forwarded(body);
		expect(res.status).toBe(422);
		expect(calls).toBe(0);
	});
});
