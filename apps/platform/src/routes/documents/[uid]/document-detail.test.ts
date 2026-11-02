/**
 * The document detail page's loader and rename action.
 *
 * Two shipped bugs are pinned here:
 *  - `rename` posted `name: name || null`, and the API COALESCEs a null away — so clearing the name
 *    field PATCHed nothing and the page reported success. It must be refused before the call.
 *  - a failing activity fetch (now the action-event stream, `/firm/audit/actions?document=<uid>`)
 *    was swallowed into `[]`, so a transient failure read as "no activity" with no retry banner.
 */

import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

function harness(handler: Handler) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: unknown, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		return handler(url, init);
	});
	return { fetch, calls };
}

const apiDoc = {
	uid: 'doc_1',
	name: 'Suitability report',
	document_type: 'suitability',
	source: 'generated',
	status: 'generated',
	content_type: 'application/pdf',
	size_bytes: 2048,
	created_at: '2026-07-01T09:00:00Z',
	generated_at: '2026-07-01T09:05:00Z',
	template_version: 1,
	template_name: 'Suitability',
	template_uid: 'tpl_1',
	created_by: 'Ada Adviser',
	client: 'Bo Client',
	client_contact_id: null,
	client_viewed: false,
	client_viewed_at: null
};

function loadEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	permissions: string[] = ['documents:write']
) {
	return {
		params: { uid: 'doc_1' },
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

function renameEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	fields: Record<string, string>,
	permissions: string[] = ['documents:write']
) {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.append(k, v);
	return {
		params: { uid: 'doc_1' },
		request: new Request('http://portal.test/documents/doc_1?/rename', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

type LoadArg = Parameters<typeof load>[0];

/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const runLoad = async (event: unknown) => {
	const loaded = (await load(event as LoadArg)) as LoadData;
	return { ...loaded, ...(await loaded.record) };
};
type ActionArg = Parameters<typeof actions.rename>[0];

describe('document detail — load', () => {
	it('degrades a failed activity fetch to an empty events pane, NOT a page error', async () => {
		// The events pane reads the action stream, gated by the SEPARATE activity:read permission; a
		// documents:read user without it 403s that call. It must degrade to an empty pane, not a
		// permanent banner over a record whose document and preview loaded.
		const { fetch } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ detail: 'nope' }, 403);
			if (url.includes('/requests')) return json([]);
			if (url.includes('/documents/clients')) return json([]);
			return json(apiDoc);
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.loadError).toBe(false);
		expect(data.events).toEqual([]);
		expect(data.doc?.name).toBe('Suitability report');
	});

	it('never fetches this document’s requests — the Requests tab is gone', async () => {
		// The loader used to call GET /documents/{uid}/requests on every record load, and that call was
		// the ONLY thing `loadError` counted. The tab it fed has been removed, so the call goes with it
		// rather than being left to cost a round trip nothing renders.
		const { fetch, calls } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ items: [] });
			return json(apiDoc);
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.loadError).toBe(false);
		expect(calls.some((c) => c.url.includes('/requests'))).toBe(false);
	});

	it('is clean when every panel loads', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/firm/audit/actions'))
				return json({
					items: [
						{
							id: 1,
							event_type: 'document.rendered',
							created_at: '2026-07-01T09:05:00Z',
							path_params: { uid: 'doc_1' },
							metadata: {},
							actor_name: 'Ada Adviser'
						}
					]
				});
			return json(apiDoc);
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.loadError).toBe(false);
		expect(data.events).toHaveLength(1);
		expect(data.canManage).toBe(true);
	});

	it('a missing document is an empty page, not an error', async () => {
		const { fetch } = harness(() => json({ detail: 'Document not found' }, 404));
		const data = await runLoad(loadEvent(fetch));
		expect(data.doc).toBeNull();
	});

	it('splits canManage (drafting) from canSend, and fetches the client book for NEITHER', async () => {
		// The six-splits posture still holds — `documents:write` drafts, `documents:send` is what
		// reaches a client (here: raising a request) — but this test used to assert the opposite of its
		// second half. It pinned that `canSend` FETCHED `/documents/clients`, which was the recipient
		// picker for the notify dialog. That dialog is gone, nothing has consumed `clients` since, and
		// the fetch stayed: one wasted API call on every record load, and counted in `loadError`, so a
		// failure of a request nobody wanted put a "couldn't be loaded" banner over a loaded record.
		// Asserting its ABSENCE is what stops it being reintroduced by the next reader who notices that
		// `canSend` is computed and assumes something must want a client list.
		const panels = (url: string) =>
			url.includes('/firm/audit/actions')
				? json({ items: [] })
				: url.includes('/requests')
					? json([])
					: json(apiDoc);
		const writer = harness(panels);
		const written = await runLoad(loadEvent(writer.fetch, ['documents:write']));
		expect(written.canManage).toBe(true);
		expect(written.canSend).toBe(false);
		expect(writer.calls.some((c) => c.url.includes('/documents/clients'))).toBe(false);

		const sender = harness(panels);
		const sent = await runLoad(loadEvent(sender.fetch, ['documents:read', 'documents:send']));
		expect(sent.canManage).toBe(false);
		expect(sent.canSend).toBe(true);
		expect(sender.calls.some((c) => c.url.includes('/documents/clients'))).toBe(false);
	});
});

describe('document detail — rename', () => {
	it('refuses an empty name rather than PATCHing a silent no-op', async () => {
		const { fetch, calls } = harness(() => json(apiDoc));
		const result = await actions.rename(
			renameEvent(fetch, { name: '', document_type: 'suitability' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect((result as { data: { error: string } }).data.error).toBe('A document needs a name.');
		expect(calls).toEqual([]);
	});

	it('refuses a whitespace-only name', async () => {
		const { fetch, calls } = harness(() => json(apiDoc));
		const result = await actions.rename(
			renameEvent(fetch, { name: '   ', document_type: 'suitability' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	/** The PATCH the action sent, if it sent one. */
	const patchOf = (calls: { init: RequestInit | undefined }[]) =>
		calls.find((c) => c.init?.method === 'PATCH');

	it('PATCHes the trimmed name, and NOTHING else', async () => {
		// Name only. This action used to post `document_type` too — it was the second write path for a
		// type, which is why it carried the retype guard. A document's type is set at upload now, so
		// the field is gone from the body and a stray one in the form is simply not forwarded.
		const { fetch, calls } = harness(() => json(apiDoc));
		const result = await actions.rename(
			renameEvent(fetch, {
				name: '  Annual review  ',
				document_type: 'review'
			}) as unknown as ActionArg
		);
		expect(result).toMatchObject({ ok: true });
		const patch = patchOf(calls);
		expect(patch).toBeDefined();
		expect(JSON.parse(String(patch?.init?.body))).toEqual({ name: 'Annual review' });
	});

	it('renames an ARCHIVED document, with no status lookup first', async () => {
		// The retype guard (a GET for the document's status before the PATCH) is gone with the type
		// field it protected. Renaming an archived document was always allowed — the guard's `nextType`
		// no-op arm existed to preserve exactly that — so this must stay true AND stay cheap: one
		// request, not two.
		const { fetch, calls } = harness(() => json({ ...apiDoc, status: 'archived' }));
		const result = await actions.rename(
			renameEvent(fetch, { name: 'Annual review 2025' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ ok: true });
		expect(JSON.parse(String(patchOf(calls)?.init?.body))).toEqual({ name: 'Annual review 2025' });
		expect(calls.filter((c) => (c.init?.method ?? 'GET') === 'GET')).toEqual([]);
	});

	it('refuses a reader without documents:write', async () => {
		const { fetch, calls } = harness(() => json(apiDoc));
		const result = await actions.rename(
			renameEvent(fetch, { name: 'Annual review' }, ['documents:read']) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 403 });
		expect(calls).toEqual([]);
	});

	it('surfaces the API error rather than claiming the rename worked', async () => {
		const { fetch } = harness(() => json({ detail: 'Document not found' }, 404));
		const result = await actions.rename(
			renameEvent(fetch, { name: 'Annual review' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 404 });
		expect((result as { data: { error: string } }).data.error).toBe('Document not found');
	});
});
