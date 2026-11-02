/**
 * `GET /journeys/[uid]/terms` — the printable copy of a terms declaration. Inline HTML terms come
 * back as a complete print sheet; PDF terms go to the document proxy, whose bytes the browser
 * prints itself.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';
import { escapeHtml, printableTerms } from './printable';

const locals = {
	user: { id: 'u1' },
	permissions: ['cases:read'],
	role: null,
	accessToken: 'tok',
	appName: 'Hobbis & Co'
};

function run(
	payload: Record<string, unknown> | null,
	{ status = 200, search = '?case=c1&step=terms&participant=7', who = locals } = {}
) {
	const fetch = vi.fn(async () =>
		payload
			? new Response(JSON.stringify(payload), { status })
			: new Response(JSON.stringify({ detail: 'Step not found.' }), { status })
	);
	const result = GET({
		params: { uid: 'ex1' },
		url: new URL(`http://platform.test/journeys/ex1/terms${search}`),
		locals: who,
		fetch
	} as never);
	return { fetch, result };
}

describe('GET /journeys/[uid]/terms', () => {
	it('serves inline terms as a standalone print sheet with the client and firm named', async () => {
		const { fetch, result } = run({
			name: 'General declaration',
			content: '<h2>Declaration</h2><p>I agree.</p>',
			document_uid: null,
			content_type: null,
			client_name: 'Joan Davis'
		});
		const res = await result;
		expect(res.status).toBe(200);
		expect(res.headers.get('content-type')).toMatch(/^text\/html/);
		const html = await res.text();
		expect(html).toContain('<title>General declaration</title>');
		expect(html).toContain('<h2>Declaration</h2><p>I agree.</p>');
		expect(html).toContain('Prepared for Joan Davis');
		expect(html).toContain('Hobbis &amp; Co');
		expect(html).toContain('window.print()');
		// The API is asked for exactly this step, with the participant as a filter.
		const [url] = fetch.mock.calls[0] as unknown as [string];
		expect(url).toBe(
			'http://127.0.0.1:8000/applications/ex1/terms?case_uid=c1&step_key=terms&participant_contact_id=7'
		);
	});

	it('sends PDF terms to the same-origin document proxy', async () => {
		const { result } = run({
			name: 'Client agreement',
			content: null,
			document_uid: 'doc_abc123',
			content_type: 'application/pdf',
			client_name: null
		});
		await expect(result).rejects.toMatchObject({
			status: 303,
			location: '/documents/doc_abc123/content'
		});
	});

	it('is a 404 when there is nothing to print, and relays the API refusal otherwise', async () => {
		await expect(
			run({
				name: 'Tick box',
				content: null,
				document_uid: null,
				content_type: null,
				client_name: null
			}).result
		).rejects.toMatchObject({ status: 404 });
		await expect(run(null, { status: 404 }).result).rejects.toMatchObject({
			status: 404,
			body: { message: 'Step not found.' }
		});
	});

	it('needs a step, and the permission to read journeys', async () => {
		const short = run(null, { search: '?case=c1' });
		await expect(short.result).rejects.toMatchObject({ status: 400 });
		expect(short.fetch).not.toHaveBeenCalled();
		const denied = run(null, { who: { ...locals, permissions: [] } });
		await expect(denied.result).rejects.toMatchObject({ status: 403 });
		expect(denied.fetch).not.toHaveBeenCalled();
	});
});

describe('printableTerms', () => {
	it('escapes every value but the (already sanitised) content', () => {
		const html = printableTerms({
			name: 'Terms <b>& conditions</b>',
			content: '<p>Body <em>kept</em></p>',
			clientName: 'O\'Brien "Jo"',
			firmName: null,
			printedOn: new Date('2026-09-30T09:00:00Z')
		});
		expect(html).toContain('<title>Terms &lt;b&gt;&amp; conditions&lt;/b&gt;</title>');
		expect(html).toContain('<p>Body <em>kept</em></p>');
		expect(html).toContain('O&#39;Brien &quot;Jo&quot;');
		expect(html).toContain('Printed 30 September 2026');
		expect(html).not.toContain('class="firm"');
	});

	it('escapeHtml covers the five characters that matter', () => {
		expect(escapeHtml(`<a href="x">Tom & Jerry's</a>`)).toBe(
			'&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;'
		);
	});
});
