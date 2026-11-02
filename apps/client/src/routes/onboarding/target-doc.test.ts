/**
 * What the client is given to READ before they agree.
 *
 * Pins the bug this shipped: the action handed the dialog the API's short-lived PRESIGNED URL, so a
 * declaration authored as a PDF (rather than inline HTML) opened a frame that never filled — the
 * object store has no public endpoint in a container deployment, and the portal renders a PDF with
 * pdf.js, which fetches the bytes rather than navigating to them, so a cross-origin URL needs CORS
 * the store does not send. The bytes are proxied same-origin now.
 *
 * And the half underneath it: `/terms` answers 200 for any `document_type='terms'` row, INCLUDING
 * one carrying a PDF and no HTML, so taking that branch on the status code alone reported an empty
 * body and the dialog told the client there was nothing to review.
 */

import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

type ActionArg = Parameters<typeof actions.targetDoc>[0];

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

/** Answers `/terms` and `/target` from the two responses given, recording what was asked. */
function harness(terms: Response, target: Response) {
	const urls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		urls.push(url);
		return url.endsWith('/terms') ? terms : target;
	});
	const body = new FormData();
	body.append('req_uid', 'req_1');
	body.append('item_uid', 'rqi_1');
	return {
		urls,
		event: {
			request: new Request('http://portal.test/onboarding?/targetDoc', { method: 'POST', body }),
			locals: { user: { id: 'u1' }, accessToken: 'tok' },
			fetch
		} as unknown as ActionArg
	};
}

describe('client onboarding — targetDoc', () => {
	it('serves a PDF declaration from our own origin, never a presigned URL', async () => {
		const { event, urls } = harness(
			json({ detail: 'Terms not found' }, 404),
			json({ name: 'Client Agreement v4.pdf', content_type: 'application/pdf' })
		);
		const result = await actions.targetDoc(event);
		expect(result).toMatchObject({
			ok: true,
			content_type: 'application/pdf',
			url: '/api/request-items/req_1/rqi_1/content',
			content: null
		});
		// The metadata call must not be asked for a URL it cannot render.
		expect(urls.some((u) => u.endsWith('/items/rqi_1/target'))).toBe(true);
	});

	it('falls through to the stored file when a terms row carries no HTML', async () => {
		const { event } = harness(
			json({ name: 'Terms', content: null }),
			json({ name: 'Terms.pdf', content_type: 'application/pdf' })
		);
		expect(await actions.targetDoc(event)).toMatchObject({
			content_type: 'application/pdf',
			url: '/api/request-items/req_1/rqi_1/content'
		});
	});

	it('prefers inline HTML when the declaration has it, and asks for no file', async () => {
		const { event, urls } = harness(
			json({ name: 'General terms', content: '<p>Read this</p>' }),
			json({ detail: 'Document not found' }, 404)
		);
		expect(await actions.targetDoc(event)).toMatchObject({
			content: '<p>Read this</p>',
			url: null,
			content_type: null
		});
		expect(urls.some((u) => u.endsWith('/target'))).toBe(false);
	});

	it('keeps a terms row with neither HTML nor a file as an acknowledgement, not a failure', async () => {
		const { event } = harness(
			json({ name: 'Declaration', content: null }),
			json({ detail: 'Document not found' }, 404)
		);
		expect(await actions.targetDoc(event)).toMatchObject({
			ok: true,
			name: 'Declaration',
			content: null,
			url: null
		});
	});

	it('reports a failure when there is no reviewable document at all', async () => {
		const { event } = harness(
			json({ detail: 'Terms not found' }, 404),
			json({ detail: 'Document not found' }, 404)
		);
		expect(await actions.targetDoc(event)).toMatchObject({ status: 404 });
	});
});
