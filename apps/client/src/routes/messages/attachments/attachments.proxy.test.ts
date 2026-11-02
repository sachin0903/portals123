/**
 * The client portal's attachment proxy — that it forwards EVERY file, and everything that rides
 * beside them.
 *
 * THE DEFECT. A message may carry several attachments (0123), the composer appends one `file` part
 * per staged file, and the API takes a repeated part (`list[UploadFile]`). This proxy rebuilt the
 * body from `incoming.get('file')`, which returns only the FIRST — so a client attaching three
 * documents uploaded one and was told it had worked. Nothing caught it: the API accepted what it
 * was sent, the response was a well-formed message, and the two files that never left the browser
 * are invisible from either end.
 *
 * The other three assertions are for what the proxy's own header warns about: `request_item_uid`
 * turns an upload into a REQUEST FULFILMENT, and `contact_id` chooses which linked contact's thread
 * it lands in. Dropping either is silent — the message still arrives, just unlinked or in the wrong
 * thread.
 */
import { describe, expect, it, vi } from 'vitest';
import { POST } from './+server';

type Handler = Parameters<typeof POST>[0];

/** A send carrying `n` file parts under the one `file` key, as the composer sends them. */
function sendOf(names: string[], extra: Record<string, string> = {}): FormData {
	const body = new FormData();
	for (const name of names) body.append('file', new File([`bytes of ${name}`], name), name);
	body.append('text', 'note');
	for (const [k, v] of Object.entries(extra)) body.append(k, v);
	return body;
}

/** Runs the handler and hands back the multipart body and URL it forwarded to the API. */
async function forwarded(form: FormData) {
	const seen: { body: FormData | null; url: string } = { body: null, url: '' };
	const fetch = vi.fn(async (url: string, init?: RequestInit) => {
		seen.url = url;
		seen.body = (init?.body as FormData) ?? null;
		return new Response(JSON.stringify({ id: 1, attachments: [] }), { status: 200 });
	});
	const res = await POST({
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		request: new Request('http://portal.test/messages/attachments', {
			method: 'POST',
			body: form
		}),
		fetch
	} as unknown as Handler);
	return { res, sent: seen.body, url: seen.url, calls: fetch.mock.calls.length };
}

describe('the client attachment proxy', () => {
	it('forwards EVERY file part, in order, under the one key', async () => {
		const { res, sent } = await forwarded(sendOf(['first.pdf', 'second.pdf', 'third.pdf']));
		expect(res.status).toBe(200);
		const files = sent!.getAll('file') as File[];
		expect(files.map((f) => f.name)).toEqual(['first.pdf', 'second.pdf', 'third.pdf']);
	});

	it('sends ONE request, so the files stay on one message', async () => {
		// The composer used to loop, one message per file, because 0081 made `message_id` UNIQUE.
		// Three documents then reached the adviser as three bubbles they had to read as one.
		const { calls } = await forwarded(sendOf(['a.pdf', 'b.pdf']));
		expect(calls).toBe(1);
	});

	it('carries `request_item_uid`, which is what makes an upload a fulfilment', async () => {
		const { sent } = await forwarded(sendOf(['a.pdf'], { request_item_uid: 'it_7' }));
		expect(sent!.get('request_item_uid')).toBe('it_7');
	});

	it('puts `contact_id` on the QUERY string, not in the body', async () => {
		// The API route mixes it with File/Form params, so a plain scalar there resolves from the
		// query string — in the body it is simply ignored and the send lands in the wrong thread.
		const { sent, url } = await forwarded(sendOf(['a.pdf'], { contact_id: '42' }));
		expect(url).toContain('?contact_id=42');
		expect(sent!.get('contact_id')).toBeNull();
	});

	it('drops a `contact_id` that is not a bare integer rather than splicing it into the query', async () => {
		const { url } = await forwarded(sendOf(['a.pdf'], { contact_id: '42&request_item_uid=x' }));
		expect(url.endsWith('/messaging/thread/attachments')).toBe(true);
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
