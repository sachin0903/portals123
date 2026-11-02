/**
 * `?/uploadPhotos` — operations uploading new identity photos for a client, on their behalf. Pins
 * the validation that runs before any fetch (so a bad form never reaches the API) and that the
 * right multipart body goes to the right endpoint once it is clean.
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

/** A photo over ComplyCube's 34 KB floor, as the dialog sends it. */
const photo = (name: string, type = 'image/jpeg') =>
	new File([new Uint8Array(40_000)], name, { type });

function submit(fields: Record<string, string | File>) {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) {
		if (v instanceof File) body.append(k, v, v.name);
		else body.append(k, v);
	}
	return new Request('http://admin.test/aml/clients/cl_1', { method: 'POST', body });
}

type Call = { url: string; method: string; headers: Headers; body: unknown };

function harness(
	response: Response | null = new Response(
		JSON.stringify({ check_uid: 'chk_9', photos: ['document'], document_type: 'passport' })
	)
) {
	const calls: Call[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		if (response === null) throw new Error('network down');
		calls.push({
			url: String(url),
			method: init?.method ?? 'GET',
			headers: new Headers(init?.headers),
			body: init?.body
		});
		return response;
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

const run = async (
	fields: Record<string, string | File>,
	opts: Parameters<typeof harness>[0] = undefined,
	who = locals()
) => {
	const h = harness(opts);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const result = await (actions.uploadPhotos as any)({
		request: submit(fields),
		locals: who,
		params: PARAMS,
		fetch: h.fetch
	});
	return { result, ...h };
};

describe('?/uploadPhotos', () => {
	it('bounces a visitor without aml:adjudicate, before any fetch', async () => {
		const h = harness();
		await expect(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(actions.uploadPhotos as any)({
				request: submit({ idv_selfie: photo('selfie.jpg'), comment: 'Sent by email' }),
				locals: locals(['aml:read', 'aml:write']),
				params: PARAMS,
				fetch: h.fetch
			})
		).rejects.toMatchObject({ status: 403 });
		expect(h.calls).toHaveLength(0);
	});

	it('requires at least one photo', async () => {
		const { result, calls } = await run({ document_type: 'passport', comment: 'Sent by email' });
		expect(result).toMatchObject({
			status: 400,
			data: { action: 'uploadPhotos', error: 'Add at least one photo.' }
		});
		expect(calls).toHaveLength(0);
	});

	it('does not count an empty file as a photo', async () => {
		const empty = new File([], 'empty.jpg', { type: 'image/jpeg' });
		const { result, calls } = await run({ idv_selfie: empty, comment: 'Sent by email' });
		expect(result).toMatchObject({ status: 400, data: { error: 'Add at least one photo.' } });
		expect(calls).toHaveLength(0);
	});

	it('requires a non-blank comment', async () => {
		const { result, calls } = await run({ idv_selfie: photo('selfie.jpg'), comment: '   ' });
		expect(result).toMatchObject({
			status: 400,
			data: { action: 'uploadPhotos', error: 'Add a note for the record.' }
		});
		expect(calls).toHaveLength(0);
	});

	it('requires a known document type with a side of the document', async () => {
		const { result, calls } = await run({
			document_type: 'library_card',
			idv_document_front: photo('id-front.jpg'),
			comment: 'Sent by email'
		});
		expect(result).toMatchObject({ status: 400, data: { error: 'Choose the document type.' } });
		expect(calls).toHaveLength(0);
	});

	it('posts the document, its type and country, and the comment as multipart', async () => {
		const { result, calls } = await run({
			document_type: 'driving_licence',
			issuing_country: 'ie',
			idv_document_front: photo('id-front.jpg'),
			idv_document_back: photo('id-back.pdf', 'application/pdf'),
			comment: '  New licence, sent by email\r\nafter the old one expired  '
		});
		expect(result).toEqual({ action: 'uploadPhotos' });
		expect(calls).toHaveLength(1);
		expect(calls[0].method).toBe('POST');
		expect(calls[0].url).toMatch(/\/aml\/clients\/cl_1\/identity-photos$/);
		expect(calls[0].headers.get('authorization')).toBe('Bearer tok');
		// fetch sets the multipart boundary itself.
		expect(calls[0].headers.get('content-type')).toBeNull();

		const body = calls[0].body as FormData;
		expect(body).toBeInstanceOf(FormData);
		expect(body.get('document_type')).toBe('driving_licence');
		expect(body.get('issuing_country')).toBe('IE');
		expect(body.get('comment')).toBe('New licence, sent by email\nafter the old one expired');
		expect((body.get('idv_document_front') as File).name).toBe('id-front.jpg');
		expect((body.get('idv_document_back') as File).name).toBe('id-back.pdf');
		expect(body.get('idv_selfie')).toBeNull();
	});

	it('sends a selfie alone without the document type or country', async () => {
		const { calls } = await run({
			document_type: 'passport',
			issuing_country: 'GB',
			idv_selfie: photo('selfie.jpg'),
			comment: 'Better selfie, sent by email'
		});
		const body = calls[0].body as FormData;
		expect([...body.keys()].sort()).toEqual(['comment', 'idv_selfie']);
		expect((body.get('idv_selfie') as File).size).toBe(40_000);
	});

	it('leaves the issuing country to the API when none is chosen', async () => {
		const { calls } = await run({
			document_type: 'passport',
			idv_document_front: photo('id-front.jpg'),
			comment: 'New passport'
		});
		const body = calls[0].body as FormData;
		expect(body.get('issuing_country')).toBeNull();
		expect(body.get('document_type')).toBe('passport');
	});

	it('reports the service as unavailable when the fetch itself fails', async () => {
		const { result } = await run({ idv_selfie: photo('selfie.jpg'), comment: 'x' }, null);
		expect(result).toMatchObject({
			status: 503,
			data: { action: 'uploadPhotos', error: 'The service is unavailable.' }
		});
	});

	it('surfaces the API’s refusal, such as a check already running', async () => {
		const { result } = await run(
			{ idv_selfie: photo('selfie.jpg'), comment: 'x' },
			new Response(JSON.stringify({ detail: 'An identity check is already running.' }), {
				status: 409
			})
		);
		expect(result).toMatchObject({
			status: 409,
			data: { action: 'uploadPhotos', error: 'An identity check is already running.' }
		});
	});

	it('surfaces the photo ComplyCube refused', async () => {
		const { result } = await run(
			{ idv_selfie: photo('selfie.jpg'), comment: 'x' },
			new Response(JSON.stringify({ detail: 'ComplyCube could not read the selfie.' }), {
				status: 422
			})
		);
		expect(result).toMatchObject({
			status: 422,
			data: { error: 'ComplyCube could not read the selfie.' }
		});
	});
});
