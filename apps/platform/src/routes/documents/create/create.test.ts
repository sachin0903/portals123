/**
 * The generate page's loader.
 *
 * The page renders a LoadErrorBanner INSTEAD of the questionnaire when anything it needs failed to
 * load — a half-loaded input form would otherwise let someone generate a client-facing document from
 * missing inputs. These tests pin the flag each partial failure has to raise.
 */

import { describe, expect, it, vi } from 'vitest';
import { load, actions } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

type Handler = (url: string) => Response;

function harness(handler: Handler) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		return handler(url);
	});
	return { fetch, calls };
}

const template = {
	uid: 'tpl_1',
	name: 'Suitability',
	description: null,
	document_type: 'suitability',
	status: 'published',
	current_version: 3,
	published_version: 3,
	created_at: '2026-06-01T09:00:00Z',
	updated_at: '2026-06-02T09:00:00Z',
	created_by: 'Ada Adviser',
	input_form_uid: 'form_1',
	input_form_name: 'Fact-find'
};

/** Everything loads. Individual tests override one leg with a failure. */
const allOk: Handler = (url) => {
	if (url.includes('/input-form')) return json({ fields: [], source: 'snapshot' });
	if (url.includes('/variables')) return json({ variables: ['client.name'] });
	if (url.includes('/documents/clients')) return json([]);
	return json(template);
};

function event(
	fetch: ReturnType<typeof harness>['fetch'],
	{
		templateRef = 'tpl_1',
		permissions = ['documents:write', 'templates:read']
	}: { templateRef?: string; permissions?: string[] } = {}
) {
	const href = `http://portal.test/documents/create${templateRef ? `?template=${templateRef}` : ''}`;
	return {
		url: new URL(href),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

type LoadArg = Parameters<typeof load>[0];

/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const runLoad = async (event: unknown) => {
	const loaded = (await load(event as LoadArg)) as LoadData;
	return { ...loaded, ...(await loaded.wizard) };
};

describe('generate page — load', () => {
	it('is clean when the template, its questionnaire and the clients all load', async () => {
		const { fetch } = harness(allOk);
		const data = await runLoad(event(fetch));
		expect(data.loadError).toBe(false);
		expect(data.template?.uid).toBe('tpl_1');
		expect(data.templateVars).toEqual(['client.name']);
	});

	it('flags a failed questionnaire fetch, so the dialog is not opened on half its inputs', async () => {
		const { fetch } = harness((url) =>
			url.includes('/input-form') ? json({ detail: 'nope' }, 403) : allOk(url)
		);
		const data = await runLoad(event(fetch));
		expect(data.loadError).toBe(true);
		expect(data.fields).toEqual([]);
	});

	it('flags a failed clients fetch', async () => {
		const { fetch } = harness((url) =>
			url.includes('/documents/clients') ? json({ detail: 'nope' }, 403) : allOk(url)
		);
		expect((await runLoad(event(fetch))).loadError).toBe(true);
	});

	it('flags a failed variables fetch', async () => {
		const { fetch } = harness((url) =>
			url.includes('/variables') ? json({ detail: 'nope' }, 403) : allOk(url)
		);
		expect((await runLoad(event(fetch))).loadError).toBe(true);
	});

	it('flags a failed template fetch and stops there', async () => {
		const { fetch, calls } = harness(() => json({ detail: 'nope' }, 403));
		const data = await runLoad(event(fetch));
		expect(data.loadError).toBe(true);
		expect(data.template).toBeNull();
		expect(calls.filter((u) => u.includes('/input-form'))).toEqual([]);
	});

	it('no template chosen is an empty page, not an error', async () => {
		const { fetch, calls } = harness(allOk);
		const data = await runLoad(event(fetch, { templateRef: '' }));
		expect(data.loadError).toBe(false);
		expect(data.template).toBeNull();
		expect(calls).toEqual([]);
	});

	it('generating needs documents:write, not just templates:read', async () => {
		const { fetch, calls } = harness(allOk);
		const data = await runLoad(event(fetch, { permissions: ['templates:read', 'documents:read'] }));
		expect(data.template).toBeNull();
		expect(calls).toEqual([]);
	});
});

// -- the batch action ----------------------------------------------------------------------------
// The interesting behaviour is the shape of what comes back: the page shows the report instead of
// navigating, so a lost `rejected` list is a silently incomplete answer rather than an error.

/** A POST event for a form action, with the JSON payload the page sends. */
function postEvent(
	fetch: unknown,
	payload: unknown,
	{ permissions = ['templates:read', 'documents:write'] }: { permissions?: string[] } = {}
) {
	const body = new FormData();
	body.append('payload', JSON.stringify(payload));
	return {
		request: new Request('http://portal.test/documents/create?/generateBatch', {
			method: 'POST',
			body
		}),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

type BatchArg = Parameters<NonNullable<typeof actions.generateBatch>>[0];
const runBatch = async (event: unknown) =>
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(await actions.generateBatch!(event as BatchArg)) as any;

describe('generate page — batch action', () => {
	/** Typed with the two arguments the action actually calls `fetch` with, so a test can read the
	 *  request body off `mock.calls` instead of casting an untyped, zero-length tuple. */
	const batchOk = (documents: number, rejected: { contact_id: number; reason: string }[] = []) =>
		vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
			json({
				documents: Array.from({ length: documents }, (_, i) => ({ uid: `doc_${i}` })),
				rejected
			})
		);

	/** The JSON body of the nth `fetch` call. */
	const sentBody = (fetch: ReturnType<typeof batchOk>, n = 0) =>
		JSON.parse(String(fetch.mock.calls[n]![1].body));

	it('sends the clients and reports how many queued', async () => {
		const fetch = batchOk(3);
		const out = await runBatch(
			postEvent(fetch, { templateUid: 'tpl_1', contactIds: [1, 2, 3], answers: { a: 'b' } })
		);
		expect(out.queued).toBe(3);
		const sent = sentBody(fetch);
		expect(sent.contact_ids).toEqual([1, 2, 3]);
		expect(sent.template_uid).toBe('tpl_1');
		expect(sent.answers).toEqual({ a: 'b' });
	});

	it('carries the skipped clients through, camel-cased', async () => {
		// The whole reason the page does not redirect on success: this list is the only place a
		// missed client is named.
		const fetch = batchOk(1, [{ contact_id: 9, reason: 'Unknown client.' }]);
		const out = await runBatch(postEvent(fetch, { templateUid: 'tpl_1', contactIds: [1, 9] }));
		expect(out.rejected).toEqual([{ contactId: 9, reason: 'Unknown client.' }]);
	});

	it('passes the caller idempotency key, so a double-submit cannot double the batch', async () => {
		const fetch = batchOk(2);
		await runBatch(
			postEvent(fetch, { templateUid: 'tpl_1', contactIds: [1, 2], idempotencyKey: 'k1' })
		);
		expect(sentBody(fetch).idempotency_key).toBe('k1');
	});

	it('refuses with no clients, without calling the API', async () => {
		const fetch = batchOk(0);
		const out = await runBatch(postEvent(fetch, { templateUid: 'tpl_1', contactIds: [] }));
		expect(out.status).toBe(400);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('refuses without documents:write', async () => {
		const fetch = batchOk(0);
		const out = await runBatch(
			postEvent(
				fetch,
				{ templateUid: 'tpl_1', contactIds: [1] },
				{ permissions: ['documents:read'] }
			)
		);
		expect(out.status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('surfaces the API refusal rather than reporting a queue of nothing', async () => {
		const fetch = vi.fn(async () => json({ detail: 'Template not found' }, 404));
		const out = await runBatch(postEvent(fetch, { templateUid: 'gone', contactIds: [1] }));
		expect(out.status).toBe(404);
		expect(String(out.data.error)).toContain('Template not found');
	});
});
