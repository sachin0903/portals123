import { describe, it, expect, vi } from 'vitest';
import {
	createPreviewFetcher,
	defaultAudienceFor,
	type EmailPreview,
	type PreviewPayload
} from './preview';

const PAYLOAD: PreviewPayload = {
	subject: 's',
	preheader: 'p',
	html: '<p>hi</p>',
	css: '',
	audience: 'client'
};
const BODY: EmailPreview = { subject: 'S', preheader: 'P', html: '<p>H</p>', missing: [] };

const ok = (body: unknown) =>
	({ ok: true, status: 200, json: async () => body }) as unknown as Response;

/** `clone()` matters: errorMessage reads the body twice (json, then text). */
const fail = (status: number, json: unknown, text = '') =>
	({
		ok: false,
		status,
		clone: () => ({ json: async () => json }) as unknown as Response,
		text: async () => text
	}) as unknown as Response;

const loader = (f: unknown, url = 'eml_abc') => createPreviewFetcher(url, f as typeof fetch);

describe('createPreviewFetcher', () => {
	it('posts the live editor content, audience included, to the given preview URL', async () => {
		// A body without `audience` silently takes the server's `client` default, judging a staff template in the wrong brand; asserted on what the API actually receives, not the call.
		const f = vi.fn().mockResolvedValue(ok(BODY));
		const payload = { ...PAYLOAD, audience: 'staff' as const };
		expect(await loader(f, '/email-templates/eml_abc/preview')(payload)).toEqual({
			stale: false,
			data: BODY
		});
		const [url, init] = f.mock.calls[0];
		expect(url).toBe('/email-templates/eml_abc/preview');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body)).toEqual(payload);
	});

	it('reports a superseded response as stale so it cannot overwrite fresher content', async () => {
		let release: (r: Response) => void = () => {};
		const f = vi
			.fn()
			.mockImplementationOnce(() => new Promise<Response>((r) => (release = r)))
			.mockResolvedValueOnce(ok(BODY));
		const load = loader(f);

		const first = load(PAYLOAD);
		const second = await load(PAYLOAD);
		release(ok({ ...BODY, subject: 'STALE' }));

		expect(await first).toEqual({ stale: true });
		expect(second).toEqual({ stale: false, data: BODY });
	});

	it("prefers SvelteKit's { message }, then the raw body, then the status", async () => {
		const cases: Array<[Response, string]> = [
			[fail(403, { message: 'Not permitted' }), 'Not permitted'],
			[fail(500, null, 'boom'), 'boom'],
			[fail(502, null, ''), 'Preview failed (502)']
		];
		for (const [response, error] of cases)
			expect(await loader(vi.fn().mockResolvedValue(response))(PAYLOAD)).toEqual({
				stale: false,
				error
			});

		const offline = vi.fn().mockRejectedValue(new Error('offline'));
		expect(await loader(offline)(PAYLOAD)).toEqual({ stale: false, error: 'offline' });
	});
});

describe('defaultAudienceFor', () => {
	// Every expectation below is read off the real send site's code, not chosen for symmetry — if one moves, this test should fail.

	it('starts both task notices in the STAFF brand — their send site resolves the platform one', () => {
		// Mirrors notifications/email_consumer.py: a notice is dressed in `rule.portal`, and both task triggers ship the `assignee` audience, which catalogue.audience_portal maps to `platform` — a CONTACT assignee included, since the audience carries the portal and there is no client-keyed task audience. Both codes need it: they split from one template that branched on `is_reminder`, and their two triggers carry the same audience.
		for (const code of ['task_assigned', 'task_reminder'])
			expect(defaultAudienceFor(code), code).toBe('staff');
	});

	it('starts every client-addressed code in the client brand', () => {
		// The two document codes hang off triggers whose audience is keyed literally `client`, which catalogue.audience_portal maps to the client portal. An invite follows the portal being GRANTED (provisioning.py) and a reset the posting host (routers/internal.py), so `client` for those two is a starting position, not a claim about the send.
		for (const code of ['document_sent', 'request_raised', 'invite', 'password_reset'])
			expect(defaultAudienceFor(code), code).toBe('client');
	});

	it('leaves the general wrapper on the client default — it dresses both portals', () => {
		// `notification` is the one shipped code deliberately absent from the map: it is the single wrapper EVERY rule's notice is dropped into, client-audience and staff-audience alike, so no fixed entry could be borne out by the send. Falling through to the API's own default is the honest answer.
		expect(defaultAudienceFor('notification')).toBe('client');
	});

	it("falls back to the client brand for a firm's own code, matching the API default", () => {
		for (const code of ['firm_custom_thing', '', null, undefined])
			expect(defaultAudienceFor(code)).toBe('client');
	});

	it('never answers anything but the two audiences the API accepts', () => {
		for (const code of [
			'invite',
			'password_reset',
			'document_sent',
			'request_raised',
			'task_assigned',
			'task_reminder',
			'notification',
			'nonsense'
		])
			expect(['client', 'staff']).toContain(defaultAudienceFor(code));
	});
});
