/** The three outcomes the API answers must stay three outcomes in the UI: a suppressed send reported as success has the author trusting an email that never left; reported as failure has them hunting a bug that isn't there. */
import { describe, it, expect, vi } from 'vitest';
import { createPreviewFetcher, type PreviewPayload } from './preview';
import { createTestSender, testSendFeedback, type TestSendOutcome } from './send-test';

const PAYLOAD: PreviewPayload = {
	subject: 'Hi {{ first_name }}',
	preheader: 'p',
	html: '<p>x</p>',
	css: '',
	audience: 'client'
};

const DELIVERED: TestSendOutcome = {
	status: 'delivered',
	to: 'author@firm.test',
	subject: '[TEST] Welcome',
	detail: null,
	missing: []
};

const ok = (body: unknown) =>
	({
		ok: true,
		status: 200,
		headers: new Headers(),
		json: async () => body
	}) as unknown as Response;

/** `clone()` matters: errorMessage reads the body twice (json, then text). */
const fail = (status: number, json: unknown, text = '', headers: HeadersInit = {}) =>
	({
		ok: false,
		status,
		headers: new Headers(headers),
		clone: () => ({ json: async () => json }) as unknown as Response,
		text: async () => text
	}) as unknown as Response;

const sender = (f: unknown, url = 'eml') => createTestSender(url, f as typeof fetch);

describe('createTestSender', () => {
	it('posts the live editor content to the given send-test URL, and no recipient', async () => {
		const f = vi.fn().mockResolvedValue(ok(DELIVERED));
		expect(await sender(f, '/email-templates/eml_abc/send-test')(PAYLOAD)).toEqual({
			kind: 'sent',
			outcome: DELIVERED
		});
		const [url, init] = f.mock.calls[0];
		expect(url).toBe('/email-templates/eml_abc/send-test');
		expect(init.method).toBe('POST');
		// The exact key set: canvas plus the audience that picks brand and envelope identity — nothing that could aim this at somebody else, since the address is resolved server-side.
		expect(JSON.parse(init.body)).toEqual(PAYLOAD);
		expect(Object.keys(JSON.parse(init.body)).sort()).toEqual([
			'audience',
			'css',
			'html',
			'preheader',
			'subject'
		]);
	});

	it('posts byte-identical content to the preview fetcher, audience included', async () => {
		// Both take `PreviewPayload` because preview and test-send render through ONE renderer and must describe the same email; server-side `audience` picks both brand and sender (`resolve_sender(..., portal=)`), so a missing one tests the wrong address in the wrong brand — asserted on the wire, since assembling the body key by key is exactly how it went missing on the app proxies.
		const payload: PreviewPayload = { ...PAYLOAD, audience: 'staff' };
		const sendFetch = vi.fn().mockResolvedValue(ok(DELIVERED));
		const previewFetch = vi.fn().mockResolvedValue(ok({}));

		await sender(sendFetch, 'eml/send-test')(payload);
		await createPreviewFetcher('eml/preview', previewFetch as unknown as typeof fetch)(payload);

		expect(JSON.parse(sendFetch.mock.calls[0][1].body).audience).toBe('staff');
		expect(sendFetch.mock.calls[0][1].body).toBe(previewFetch.mock.calls[0][1].body);
	});

	it('reports a 429 as throttled, with Retry-After in seconds when it is usable', async () => {
		const throttled = vi
			.fn()
			.mockResolvedValue(fail(429, { message: 'slow' }, '', { 'retry-after': '45' }));
		expect(await sender(throttled)(PAYLOAD)).toEqual({ kind: 'throttled', retryAfterSeconds: 45 });

		const unusable: Record<string, string>[] = [
			{},
			{ 'retry-after': 'Wed, 05 Aug 2026 10:00:00 GMT' }
		];
		for (const headers of unusable) {
			const f = vi.fn().mockResolvedValue(fail(429, {}, '', headers));
			expect(await sender(f)(PAYLOAD)).toEqual({ kind: 'throttled', retryAfterSeconds: null });
		}
	});

	it("prefers SvelteKit's { message } for a refusal, then the body, then the status", async () => {
		const cases: Array<[Response, string]> = [
			[fail(403, { message: 'Not permitted' }), 'Not permitted'],
			[fail(500, null, 'boom'), 'boom'],
			[fail(502, null, ''), 'The test email failed (502).']
		];
		for (const [response, error] of cases) {
			const f = vi.fn().mockResolvedValue(response);
			expect(await sender(f)(PAYLOAD)).toEqual({ kind: 'error', error });
		}

		const offline = vi.fn().mockRejectedValue(new Error('offline'));
		expect(await sender(offline)(PAYLOAD)).toEqual({ kind: 'error', error: 'offline' });
	});
});

describe('testSendFeedback', () => {
	it('says where a delivered test went, what to look for, and which tokens arrived empty', () => {
		const fb = testSendFeedback({ kind: 'sent', outcome: DELIVERED });
		expect(fb.tone).toBe('success');
		expect(fb.message).toContain('author@firm.test');
		expect(fb.message).toContain('[TEST] Welcome');

		const missing = testSendFeedback({
			kind: 'sent',
			outcome: { ...DELIVERED, missing: ['client.first_name', 'brand.support_email'] }
		});
		expect(missing.tone).toBe('success');
		expect(missing.message).toContain('client.first_name, brand.support_email');
	});

	it('treats a suppressed send as a warning naming the setting — never a success', () => {
		const detailed = testSendFeedback({
			kind: 'sent',
			outcome: {
				status: 'suppressed',
				to: 'author@firm.test',
				subject: '[TEST] Welcome',
				detail: 'EMAIL_ALLOWLIST does not include this address.'
			}
		});
		expect(detailed.tone).toBe('warning');
		expect(detailed.message).toContain('EMAIL_ALLOWLIST');
		expect(detailed.message).not.toMatch(/sent to/i);

		const bare = testSendFeedback({
			kind: 'sent',
			outcome: { status: 'suppressed', to: 'a@b.test', subject: '[TEST] x' }
		});
		expect(bare.tone).toBe('warning');
		expect(bare.message).toContain('switched off');
	});

	it('reports a failed send as an error, with the provider detail when there is one', () => {
		expect(
			testSendFeedback({
				kind: 'sent',
				outcome: {
					status: 'failed',
					to: 'a@b.test',
					subject: '[TEST] x',
					detail: 'The email provider rejected the send.'
				}
			})
		).toEqual({
			tone: 'error',
			message: 'Couldn’t send the test email. The email provider rejected the send.'
		});
		expect(
			testSendFeedback({
				kind: 'sent',
				outcome: { status: 'failed', to: 'a@b.test', subject: '[TEST] x' }
			}).message
		).toContain('logged');
	});

	it('points a throttled author at the preview, and pluralises the wait', () => {
		expect(testSendFeedback({ kind: 'throttled', retryAfterSeconds: 1 }).message).toContain(
			'1 second.'
		);
		expect(testSendFeedback({ kind: 'throttled', retryAfterSeconds: 45 }).message).toContain(
			'45 seconds'
		);
		expect(testSendFeedback({ kind: 'throttled', retryAfterSeconds: 90 }).message).toContain(
			'2 minutes'
		);

		const noNumber = testSendFeedback({ kind: 'throttled', retryAfterSeconds: null });
		expect(noNumber.tone).toBe('warning');
		expect(noNumber.message).toContain('shortly');
		expect(noNumber.message).toContain('preview');
	});

	it('passes a proxy refusal straight through as the error copy', () => {
		expect(testSendFeedback({ kind: 'error', error: 'Not permitted' })).toEqual({
			tone: 'error',
			message: 'Not permitted'
		});
	});
});
