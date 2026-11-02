/**
 * `?/acknowledge` — recording a terms acceptance taken in person. The record is the SIGNED COPY:
 * the action refuses to post without one, and forwards multipart (not JSON) so the file reaches the
 * API's own upload route.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

const locals = {
	user: { id: 'u1' },
	permissions: ['cases:read', 'cases:write'],
	role: null,
	accessToken: 'tok'
};

function run(form: FormData) {
	const fetch = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
	const result = actions.acknowledge({
		params: { uid: 'ex1' },
		request: new Request('http://platform.test/journeys/ex1?/acknowledge', {
			method: 'POST',
			body: form
		}),
		locals,
		fetch
	} as never);
	return { fetch, result };
}

function base(): FormData {
	const form = new FormData();
	form.set('case_uid', 'c1');
	form.set('step_key', 'terms');
	form.set('participant_contact_id', '7');
	form.set('note', 'signed in the 14 July meeting');
	return form;
}

describe('?/acknowledge', () => {
	it('refuses to record an acceptance with no signed copy', async () => {
		const { fetch, result } = run(base());
		const outcome = (await result) as { status: number; data: { error: string } };
		expect(outcome.status).toBe(400);
		expect(outcome.data.error).toMatch(/signed copy/i);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('treats an empty file the same as none', async () => {
		const form = base();
		form.set('file', new File([], 'empty.pdf', { type: 'application/pdf' }));
		const { fetch, result } = run(form);
		expect(((await result) as { status: number }).status).toBe(400);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('forwards the signed copy as multipart with the attestation fields', async () => {
		const form = base();
		form.set('file', new File(['%PDF-1.4'], 'terms-signed.pdf', { type: 'application/pdf' }));
		const { fetch, result } = run(form);
		expect(await result).toEqual({ ok: true });
		expect(fetch).toHaveBeenCalledTimes(1);
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toMatch(/\/applications\/ex1\/acknowledge$/);
		expect(init.method).toBe('POST');
		// Multipart: the browser/undici sets the boundary, so the action must NOT force JSON.
		const headers = init.headers as Record<string, string>;
		expect(headers['Content-Type'] ?? headers['content-type']).toBeUndefined();
		expect(headers.Authorization ?? headers.authorization).toBe('Bearer tok');
		const body = init.body as FormData;
		expect(body).toBeInstanceOf(FormData);
		expect(body.get('case_uid')).toBe('c1');
		expect(body.get('step_key')).toBe('terms');
		expect(body.get('participant_contact_id')).toBe('7');
		expect(body.get('attested')).toBe('true');
		expect(body.get('note')).toBe('signed in the 14 July meeting');
		expect((body.get('file') as File).name).toBe('terms-signed.pdf');
	});
});
