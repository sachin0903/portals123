/**
 * `?/requestPhotos` — ops asking a portal client for new identity photos instead of deciding the
 * check. Pins the validation that runs before any fetch (so a bad form never reaches the API) and
 * that the right endpoint and body go out once it's clean.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

const locals = (permissions: string[] = ['aml:adjudicate']) =>
	({
		user: { id: 'u-ops' },
		accessToken: 'tok',
		permissions,
		role: null
	}) as never;

const PARAMS = { key: 'cl_1' };

/** A form from the client page: every act names the check it is on. */
function submit(fields: Record<string, string | string[]>) {
	const body = new FormData();
	body.append('check', 'chk_1');
	for (const [k, v] of Object.entries(fields)) {
		for (const one of Array.isArray(v) ? v : [v]) body.append(k, one);
	}
	return new Request('http://admin.test/aml/clients/cl_1', { method: 'POST', body });
}

function harness(response: Response | null = new Response(JSON.stringify({ ok: true }))) {
	const calls: { url: string; method: string; body: unknown }[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		if (response === null) throw new Error('network down');
		calls.push({ url: String(url), method: init?.method ?? 'GET', body: init?.body });
		return response;
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const run = async (
	fields: Record<string, string | string[]>,
	opts: Parameters<typeof harness>[0] = undefined,
	who = locals()
) => {
	const h = harness(opts);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const result = await (actions.requestPhotos as any)({
		request: submit(fields),
		locals: who,
		params: PARAMS,
		fetch: h.fetch
	});
	return { result, ...h };
};

describe('?/requestPhotos', () => {
	it('bounces a visitor without aml:adjudicate, before any fetch', async () => {
		const h = harness();
		await expect(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(actions.requestPhotos as any)({
				request: submit({ photos: 'document', reason: 'blurry', note: 'x' }),
				locals: locals(['aml:read']),
				params: PARAMS,
				fetch: h.fetch
			})
		).rejects.toMatchObject({ status: 403 });
		expect(h.calls).toHaveLength(0);
	});

	it('requires at least one photo', async () => {
		const { result, calls } = await run({ reason: 'blurry', note: 'Asked by email too' });
		expect(result).toMatchObject({
			status: 400,
			data: { action: 'requestPhotos', error: 'Choose at least one photo.' }
		});
		expect(calls).toHaveLength(0);
	});

	it('rejects a photo value that is not document or selfie', async () => {
		const { result, calls } = await run({
			photos: 'face',
			reason: 'blurry',
			note: 'Asked by email too'
		});
		expect(result).toMatchObject({ status: 400, data: { error: 'Choose at least one photo.' } });
		expect(calls).toHaveLength(0);
	});

	it('de-duplicates a repeated photo choice', async () => {
		const { calls } = await run({
			photos: ['document', 'document', 'selfie'],
			reason: 'blurry',
			note: 'Both sides and the selfie were unreadable'
		});
		expect(JSON.parse(String(calls[0].body)).photos).toEqual(['document', 'selfie']);
	});

	it('requires a reason from the client-safe list', async () => {
		const { result, calls } = await run({
			photos: 'document',
			reason: 'face mismatch',
			note: 'x'
		});
		expect(result).toMatchObject({
			status: 400,
			data: { error: 'Choose what the client will be told.' }
		});
		expect(calls).toHaveLength(0);
	});

	it('requires a non-blank note', async () => {
		const { result, calls } = await run({ photos: 'document', reason: 'blurry', note: '   ' });
		expect(result).toMatchObject({
			status: 400,
			data: { error: 'Add a note for the record.' }
		});
		expect(calls).toHaveLength(0);
	});

	it('posts the trimmed note as the comment to the request-photos endpoint', async () => {
		const { calls } = await run({
			photos: ['selfie'],
			reason: 'face',
			note: '  Face was in shadow  '
		});
		expect(calls).toHaveLength(1);
		expect(calls[0].method).toBe('POST');
		expect(calls[0].url).toMatch(/\/aml\/checks\/chk_1\/request-photos$/);
		expect(JSON.parse(String(calls[0].body))).toEqual({
			photos: ['selfie'],
			reason: 'face',
			comment: 'Face was in shadow'
		});
	});

	it('reports the service as unavailable when the fetch itself fails', async () => {
		const { result } = await run({ photos: 'document', reason: 'blurry', note: 'x' }, null);
		expect(result).toMatchObject({
			status: 503,
			data: { error: 'The service is unavailable.' }
		});
	});

	it('surfaces the API’s refusal, such as the last try already used', async () => {
		const { result } = await run(
			{ photos: 'document', reason: 'blurry', note: 'x' },
			new Response(JSON.stringify({ detail: 'This was the last try.' }), { status: 409 })
		);
		expect(result).toMatchObject({
			status: 409,
			data: { action: 'requestPhotos', error: 'This was the last try.' }
		});
	});
});
