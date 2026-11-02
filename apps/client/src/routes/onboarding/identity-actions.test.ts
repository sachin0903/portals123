import { describe, expect, it, vi } from 'vitest';
import { SUBMIT_FAILED, submitError } from '$lib/identity-capture';
import { actions } from './+page.server';

type SubmitArg = Parameters<typeof actions.identityCaptureSubmit>[0];

/** The API's 409 when the contact has no nationality or address country
 *  (`app/routers/applications.py::NATIONALITY_NEEDED`, pinned by
 *  `tests/test_identity_capture.py::test_a_client_with_no_nationality_is_told_what_to_do`). */
const NATIONALITY_NEEDED =
	'We need your nationality before we can check your ID. Please ask your adviser to add it.';

function photo(name: string, type = 'image/jpeg', bytes = 2048): File {
	return new File([new Uint8Array(bytes)], name, { type });
}

function submitEvent(
	response: Response,
	extra: (body: FormData) => void = () => {}
): SubmitArg & { fetch: ReturnType<typeof vi.fn> } {
	const body = new FormData();
	body.append('step_key', 'idv');
	body.append('case_uid', 'case_1');
	body.append('document_type', 'passport');
	extra(body);
	return {
		request: new Request('http://portal.test/onboarding?/identityCaptureSubmit', {
			method: 'POST',
			body
		}),
		locals: { user: { id: 'u1' }, accessToken: 'tok' },
		fetch: vi.fn(async () => response)
	} as unknown as SubmitArg & { fetch: ReturnType<typeof vi.fn> };
}

function json(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

/**
 * The digital identity check is the in-portal photo capture (`identityCaptureSubmit`). There is no
 * provider session to mint, and no action may offer one.
 */
describe('client onboarding — identity actions', () => {
	it('mints no provider session', () => {
		expect(actions).not.toHaveProperty('identitySession');
	});

	it('offers the capture submit, the manual handoff and its document read-back', () => {
		expect(actions).toHaveProperty('identityCaptureSubmit');
		expect(actions).toHaveProperty('identityManual');
		expect(actions).toHaveProperty('identityDocuments');
		expect(actions).toHaveProperty('identityPhone');
		expect(actions).toHaveProperty('identityPhoneCancel');
	});

	it('returns the phone link and its QR code for the desktop to show', async () => {
		const response = new Response(
			JSON.stringify({
				url: 'https://portal.test/i/once',
				qr_svg: '<svg></svg>',
				expires_at: '2026-10-06T13:00:00+00:00'
			}),
			{ status: 201, headers: { 'Content-Type': 'application/json' } }
		);
		const result = await actions.identityPhone(submitEvent(response));
		expect(result).toEqual({ url: 'https://portal.test/i/once', qr_svg: '<svg></svg>' });
	});

	it('sends the photos to the API in the submit itself, and nothing but the photo fields', async () => {
		const event = submitEvent(json(200, { status: 'submitted' }), (body) => {
			body.append('idv_document_front', photo('id-front.jpg'));
			body.append('idv_selfie', photo('selfie.png', 'image/png'));
			// An empty file is no photo, and a field that is not a photo key is not forwarded.
			body.append('idv_document_back', new File([], 'id-back.jpg', { type: 'image/jpeg' }));
			body.append('notes', 'hello');
			body.append('passport_scan', photo('other.jpg'));
		});

		const result = await actions.identityCaptureSubmit(event);

		expect(result).toEqual({ ok: true });
		expect(event.fetch).toHaveBeenCalledOnce();
		const [url, init] = event.fetch.mock.calls[0] as [string, RequestInit];
		expect(url).toMatch(/\/applications\/me\/identity-capture\/submit$/);
		expect(init.method).toBe('POST');
		// fetch sets the multipart boundary, so no content type is set by hand.
		const headers = init.headers as Record<string, string>;
		expect(Object.keys(headers).map((h) => h.toLowerCase())).not.toContain('content-type');
		expect(headers.Authorization).toBe('Bearer tok');

		const sent = init.body as FormData;
		expect(sent).toBeInstanceOf(FormData);
		expect([...sent.keys()]).toEqual([
			'step_key',
			'case_uid',
			'document_type',
			'idv_document_front',
			'idv_selfie'
		]);
		expect(sent.get('step_key')).toBe('idv');
		expect(sent.get('case_uid')).toBe('case_1');
		expect(sent.get('document_type')).toBe('passport');
		const front = sent.get('idv_document_front') as File;
		const selfie = sent.get('idv_selfie') as File;
		expect([front.name, front.type, front.size]).toEqual(['id-front.jpg', 'image/jpeg', 2048]);
		expect([selfie.name, selfie.type]).toEqual(['selfie.png', 'image/png']);
	});

	it('leaves out the step and case when the page names neither', async () => {
		const event = submitEvent(json(200, { status: 'submitted' }));
		const form = new FormData();
		form.append('document_type', 'national_id');
		form.append('idv_selfie', photo('selfie.jpg'));
		(event as { request: Request }).request = new Request(
			'http://portal.test/onboarding?/identityCaptureSubmit',
			{ method: 'POST', body: form }
		);

		await actions.identityCaptureSubmit(event);

		const sent = (event.fetch.mock.calls[0] as [string, RequestInit])[1].body as FormData;
		expect([...sent.keys()]).toEqual(['document_type', 'idv_selfie']);
	});

	it('refuses a document type it does not know, without calling the API', async () => {
		const event = submitEvent(json(200, {}));
		const form = new FormData();
		form.append('document_type', 'library_card');
		(event as { request: Request }).request = new Request(
			'http://portal.test/onboarding?/identityCaptureSubmit',
			{ method: 'POST', body: form }
		);

		const result = (await actions.identityCaptureSubmit(event)) as { status: number };

		expect(result.status).toBe(400);
		expect(event.fetch).not.toHaveBeenCalled();
	});

	it('hands the client the API’s nationality sentence word for word', async () => {
		const result = (await actions.identityCaptureSubmit(
			submitEvent(json(409, { detail: NATIONALITY_NEEDED }))
		)) as {
			status: number;
			data: { error: string };
		};
		expect(result.status).toBe(409);
		expect(result.data).toEqual({ error: NATIONALITY_NEEDED });
		// The page turns the failure into the error the capture component shows.
		expect(submitError({ type: 'failure', data: result.data })).toBe(NATIONALITY_NEEDED);
	});

	it('passes a refused photo’s reason through', async () => {
		const reason = 'The photo of your face must be a JPG or PNG.';
		const result = (await actions.identityCaptureSubmit(
			submitEvent(json(422, { detail: reason }))
		)) as { status: number; data: { error: string } };
		expect(result.status).toBe(422);
		expect(result.data).toEqual({ error: reason });
	});

	it('tells the client to send again when the photos could not be passed on', async () => {
		const result = (await actions.identityCaptureSubmit(
			submitEvent(json(503, { detail: 'Your photos could not be sent. Please try again.' }))
		)) as { status: number; data: { error: string } };
		expect(result.status).toBe(503);
		expect(result.data).toEqual({ error: SUBMIT_FAILED });
		expect(SUBMIT_FAILED).toBe('Your photos could not be sent. Please try again.');
	});
});
