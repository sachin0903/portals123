/**
 * The documents library's loader and actions.
 *
 * Each test below pins a bug this page actually shipped:
 *  - a multi-file drop posted only the first file and reported success for all of them;
 *  - `send` reported the number of recipients it *asked* for, so a recipient the API dropped (or
 *    could not email) read back as sent and notified;
 *  - a failed side-fetch was swallowed into `[]`, so a transient 401 read as "no documents";
 *  - archiving was presented as one-way, with no call to POST /documents/{uid}/unarchive.
 * They fail against the versions that had those defects.
 *
 * Two things this suite now pins as ABSENCES, both deliberate:
 *  - the loader no longer fetches the archived slice, because the library has no view onto it (the
 *    Live/Archived switch is gone — deleted documents are an ops-side listing);
 *  - `send` is kept but offered by no surface (see its block); the client-facing act is a REQUEST
 *    without inventing a fallback: `notified` counts notices RAISED, never delivered. `send` stays
 *    reachable (a delivery is still a real, distinct act) but nothing on the surface offers it.
 */

import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness(handler: Handler) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: unknown, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		return handler(url, init);
	});
	return { fetch, calls };
}

const doc = (over: Record<string, unknown> = {}) => ({
	uid: 'doc_1',
	name: 'Suitability report',
	document_type: 'suitability',
	source: 'generated',
	status: 'generated',
	content_type: 'application/pdf',
	size_bytes: 1024,
	created_at: '2026-07-01T09:00:00Z',
	generated_at: '2026-07-01T09:00:00Z',
	template_version: 2,
	template_name: 'Suitability',
	template_uid: 'tpl_1',
	created_by: 'Ada Adviser',
	client: 'Bo Client',
	client_contact_id: null,
	client_viewed: false,
	client_viewed_at: null,
	...over
});

/** A loader event. `permissions` drives `can()`; the API base comes from $lib/api. The page itself
 *  takes `documents:show`, so it is added unless `show: false` — these cases are about what the
 *  page loads once it is open, not whether it opens. */
function loadEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	{
		permissions = ['documents:write'],
		search = '',
		show = true
	}: { permissions?: string[]; search?: string; show?: boolean } = {}
) {
	return {
		locals: {
			user: { id: 'u1' },
			permissions: show ? ['documents:show', ...permissions] : permissions,
			role: null,
			accessToken: 'tok'
		},
		fetch,
		depends: () => {},
		// The loader reads the search/filter query off the URL now, the way every server-driven
		// listing does — `search` is what a filtered visit looks like.
		url: new URL(`http://portal.test/documents${search}`)
	};
}

function actionEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	body: FormData,
	permissions: string[] = ['documents:write']
) {
	return {
		request: new Request('http://portal.test/documents?/upload', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

type LoadArg = Parameters<typeof load>[0];

/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
/** The library is STREAMED (a promise the page resolves), so it is awaited and flattened onto the
 *  permissions here — these cases are about the fetches, not the plumbing. */
const runLoad = async (event: unknown) => {
	const loaded = (await load(event as LoadArg)) as LoadData;
	return { ...loaded, ...(await loaded.library) };
};
type ActionArg = Parameters<typeof actions.upload>[0];

const ok: Handler = (url) => {
	if (url.includes('/firm/audit/actions')) return json({ items: [] });
	if (url.includes('/documents/clients')) return json([]);
	// `/files?…` — both halves of the library now carry the search/filter query, so this can no
	// longer match on the bare path. Matching `endsWith('/files')` quietly stopped matching once the
	// query string arrived, and the files call fell through to the documents branch: the library
	// reported every document TWICE, once as itself and once as a supporting file.
	if (url.includes('/files?') || url.endsWith('/files')) return json({ items: [], total: 0 });
	if (url.includes('/documents/types')) return json([]);
	return json({ items: [doc()], total: 1, facets: {} });
};

describe('documents library — load', () => {
	it('asks the API for the live list and the supporting files', async () => {
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch));
		const urls = calls.map((c) => c.url);
		expect(urls.some((u) => u.includes('/documents?') && u.includes('limit='))).toBe(true);
		expect(urls.some((u) => u.includes('/files?'))).toBe(true);
		expect(data.documents).toHaveLength(1);
		expect(data.loadError).toBe(false);
	});

	it('sends the SAME search and filters to both halves of the list', async () => {
		// The library is one list built from two endpoints. A filter that reached only /documents would
		// not narrow the supporting files — it would silently leave every one of them in the table,
		// which reads as the filter being ignored rather than as half of it being applied.
		const { fetch, calls } = harness(ok);
		await runLoad(loadEvent(fetch, { search: '?q=annual&type=statement&client=cnt_1' }));
		const asked = calls
			.map((c) => c.url)
			.filter((u) => u.includes('/documents?') || u.includes('/files?'));
		expect(asked).toHaveLength(2);
		for (const url of asked) {
			expect(url).toContain('q=annual');
			expect(url).toContain('type=statement');
			expect(url).toContain('client=cnt_1');
		}
	});

	it('unions the two facet lists and sums the two totals', async () => {
		// A Type option present only among supporting files still has to be offered, and the row
		// counter counts the whole match set — which is spread across both endpoints.
		const { fetch } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ items: [] });
			if (url.includes('/documents/clients')) return json([]);
			if (url.includes('/documents/types')) return json([]);
			if (url.includes('/files?'))
				return json({ items: [], total: 4, facets: { type: ['passport'] } });
			return json({ items: [doc()], total: 9, facets: { type: ['statement'] } });
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.facets.type).toEqual(['passport', 'statement']);
		expect(data.total).toBe(13);
	});

	it('does NOT fetch the archived slice — the library has no view onto it', async () => {
		// It used to load both slices up front for a client-side Live/Archived switch. The switch is
		// gone (archiving reads as deleting, and browsing a firm's deletions is an ops-side listing),
		// so the second round trip went with it — a fetch whose only consumer was a removed control.
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch));
		expect(calls.some((c) => c.url.includes('status=archived'))).toBe(false);
		expect(data).not.toHaveProperty('archivedDocuments');
		expect(data).not.toHaveProperty('view');
	});

	it('degrades a failed activity fetch to an empty feed, NOT a page error', async () => {
		// Recent activity now reads the action stream, gated by the SEPARATE activity:read permission.
		// A documents:read user without activity:read 403s it — that must degrade to an empty feed, not
		// a permanent error banner over a fully-loaded documents page (the docs/files/types all loaded).
		const { fetch } = harness((url) => {
			// 403 is deterministic, so fetchJson fails it fast rather than retrying.
			if (url.includes('/firm/audit/actions')) return json({ detail: 'nope' }, 403);
			if (url.includes('/documents/clients')) return json([]);
			if (url.includes('/files?')) return json({ items: [], total: 0 });
			return json([doc()]);
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.loadError).toBe(false);
		expect(data.activity).toEqual([]);
		// …and the documents that DID load are still there.
		expect(data.documents).toHaveLength(1);
	});

	it('flags a failed documents fetch rather than showing an empty library', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ items: [] });
			if (url.includes('/documents/clients')) return json([]);
			if (url.includes('/files?')) return json({ items: [], total: 0 });
			return json({ detail: 'nope' }, 403);
		});
		const data = await runLoad(loadEvent(fetch));
		expect(data.loadError).toBe(true);
		expect(data.documents).toEqual([]);
	});

	it('a read-only reader gets no clients fetch and no write affordances', async () => {
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch, { permissions: ['documents:read'] }));
		expect(data.canManage).toBe(false);
		expect(data.canSend).toBe(false);
		expect(calls.some((c) => c.url.includes('/documents/clients'))).toBe(false);
		expect(data.loadError).toBe(false);
	});

	it('splits canManage from canSend — drafting and release are different keys', async () => {
		// documents:write alone: drafting, no Send.
		const writer = await runLoad(
			loadEvent(harness(ok).fetch, { permissions: ['documents:write'] })
		);
		expect(writer.canManage).toBe(true);
		expect(writer.canSend).toBe(false);
		// A reader with documents:send (no write): Send and the recipient book, no drafting.
		const sender = harness(ok);
		const sent = await runLoad(
			loadEvent(sender.fetch, { permissions: ['documents:read', 'documents:send'] })
		);
		expect(sent.canManage).toBe(false);
		expect(sent.canSend).toBe(true);
		expect(sender.calls.some((c) => c.url.includes('/documents/clients'))).toBe(true);
	});

	it('touches the API not at all without documents:read', async () => {
		// documents:show alone opens the page but reads nothing — it is a gate on top of read.
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch, { permissions: [] }));
		expect(calls).toEqual([]);
		expect(data.documents).toEqual([]);
		expect(data.canManage).toBe(false);
	});

	it('refuses the page without documents:show, even to a documents:write holder', async () => {
		// documents:read (or write) alone reads documents on a client's record, not the firm library.
		const { fetch, calls } = harness(ok);
		await expect(
			load(
				loadEvent(fetch, { permissions: ['documents:write'], show: false }) as unknown as LoadArg
			)
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toEqual([]);
	});
});

/** The `file` parts of a multipart body the action posted. */
async function postedFiles(init: RequestInit | undefined): Promise<File[]> {
	const body = init?.body as FormData;
	return body.getAll('file').filter((f): f is File => f instanceof File);
}

const filePart = (name: string): File => new File([new Uint8Array([1, 2, 3])], name);

describe('documents library — upload', () => {
	it('uploads every file in the drop, not just the first', async () => {
		const { fetch, calls } = harness(() =>
			json({ documents: [doc(), doc(), doc()], rejected: [] })
		);
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		body.append('file', filePart('b.pdf'));
		body.append('file', filePart('c.pdf'));
		body.append('document_type', 'statement');
		const result = await actions.upload(actionEvent(fetch, body) as unknown as ActionArg);

		const posted = await postedFiles(calls[0].init);
		expect(posted.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf', 'c.pdf']);
		expect(result).toMatchObject({ ok: true, uploaded: 3, failures: [] });
	});

	it('keeps an explicit name for a single file and drops it for a set', async () => {
		// `document_type` is required by the action (the dialog forces a choice; no default) — these
		// bodies carry one so the tests exercise the naming rule, not the missing-type refusal.
		const single = harness(() => json({ documents: [doc()], rejected: [] }));
		const one = new FormData();
		one.append('file', filePart('a.pdf'));
		one.append('document_type', 'statement');
		one.append('name', 'Renamed.pdf');
		await actions.upload(actionEvent(single.fetch, one) as unknown as ActionArg);
		expect((single.calls[0].init?.body as FormData).get('name')).toBe('Renamed.pdf');

		const many = harness(() => json({ documents: [doc(), doc()], rejected: [] }));
		const two = new FormData();
		two.append('file', filePart('a.pdf'));
		two.append('file', filePart('b.pdf'));
		two.append('document_type', 'statement');
		two.append('name', 'Renamed.pdf');
		await actions.upload(actionEvent(many.fetch, two) as unknown as ActionArg);
		expect((many.calls[0].init?.body as FormData).get('name')).toBeNull();
	});

	it('refuses an upload with no document type — the dialog must force a specific choice', async () => {
		// The type is required and open-ended (a registry tag or a brand-new one); there is no 'other'
		// default any more, and the API rejects blank/'auto'/'other' anyway. Refused before any fetch.
		const { fetch, calls } = harness(ok);
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		const result = await actions.upload(actionEvent(fetch, body) as unknown as ActionArg);
		expect(result).toMatchObject({ status: 400 });
		expect((result as { data: { error: string } }).data.error).toContain('document type');
		expect(calls).toEqual([]);
	});

	it('names the files the API refused instead of reporting a blanket success', async () => {
		const { fetch } = harness(() =>
			json({
				documents: [doc()],
				rejected: [{ filename: 'huge.pdf', reason: 'exceeds 25 MB' }]
			})
		);
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		body.append('file', filePart('huge.pdf'));
		body.append('document_type', 'statement');
		const result = (await actions.upload(actionEvent(fetch, body) as unknown as ActionArg)) as {
			uploaded: number;
			failures: string[];
		};
		expect(result.uploaded).toBe(1);
		expect(result.failures).toEqual(['huge.pdf: exceeds 25 MB']);
	});

	it('fails with the API reason when nothing landed', async () => {
		const { fetch } = harness(() => json({ detail: 'a.pdf: unsupported file type' }, 422));
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		body.append('document_type', 'statement');
		const result = await actions.upload(actionEvent(fetch, body) as unknown as ActionArg);
		expect(result).toMatchObject({ status: 422 });
		expect((result as { data: { error: string } }).data.error).toContain('unsupported file type');
	});

	it('refuses a drop larger than the API accepts in one request', async () => {
		const { fetch, calls } = harness(() => json({ documents: [], rejected: [] }));
		const body = new FormData();
		for (let i = 0; i < 11; i++) body.append('file', filePart(`f${i}.pdf`));
		const result = await actions.upload(actionEvent(fetch, body) as unknown as ActionArg);
		expect(result).toMatchObject({ status: 422 });
		expect((result as { data: { error: string } }).data.error).toContain(
			'Upload no more than 10 files'
		);
		expect(calls).toEqual([]);
	});

	it('forwards a zero-byte file so the API can name it, instead of dropping it silently', async () => {
		// The action used to filter `size > 0` BEFORE building the body, so an empty file never reached
		// the API, never appeared in `rejected`, and the drop toasted a clean success for a file that
		// vanished.
		const { fetch, calls } = harness(() =>
			json({
				documents: [doc()],
				rejected: [{ filename: 'empty.pdf', reason: 'document must be 1..26214400 bytes' }]
			})
		);
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		body.append('file', new File([], 'empty.pdf'));
		body.append('document_type', 'statement');
		const result = (await actions.upload(actionEvent(fetch, body) as unknown as ActionArg)) as {
			uploaded: number;
			failures: string[];
		};
		expect((await postedFiles(calls[0].init)).map((f) => f.name)).toEqual(['a.pdf', 'empty.pdf']);
		expect(result.uploaded).toBe(1);
		expect(result.failures).toEqual(['empty.pdf: document must be 1..26214400 bytes']);
	});

	it('still drops a nameless empty part (an unfilled file input, not a file)', async () => {
		const { fetch, calls } = harness(() => json({ documents: [doc()], rejected: [] }));
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		body.append('file', new File([], ''));
		body.append('document_type', 'statement');
		await actions.upload(actionEvent(fetch, body) as unknown as ActionArg);
		expect((await postedFiles(calls[0].init)).map((f) => f.name)).toEqual(['a.pdf']);

		// …and a body of nothing BUT nameless empty parts is an empty drop, not a request to the API.
		const only = harness(ok);
		const bare = new FormData();
		bare.append('file', new File([], ''));
		expect(
			await actions.upload(actionEvent(only.fetch, bare) as unknown as ActionArg)
		).toMatchObject({ status: 400 });
		expect(only.calls).toEqual([]);
	});

	it('refuses an empty drop and a reader without documents:write', async () => {
		const empty = harness(ok);
		const noFiles = await actions.upload(
			actionEvent(empty.fetch, new FormData()) as unknown as ActionArg
		);
		expect(noFiles).toMatchObject({ status: 400 });
		expect(empty.calls).toEqual([]);

		const denied = harness(ok);
		const body = new FormData();
		body.append('file', filePart('a.pdf'));
		const forbidden = await actions.upload(
			actionEvent(denied.fetch, body, ['documents:read']) as unknown as ActionArg
		);
		expect(forbidden).toMatchObject({ status: 403 });
		expect(denied.calls).toEqual([]);
	});
});

// The `notify` describe block stood here — the action posted `contact_ids` to
// `POST /documents/{uid}/notify` and forwarded the API's own `notified`/`unreachable` numbers. Both
// the form action and the endpoint are gone: a document REQUEST asks a client to act on a document
// and records whether they did, so the notice was the weaker of two features doing one job.

describe('documents library — send (kept, but no longer offered by any surface)', () => {
	// Sending is documents:send (the six-splits re-key: release, not drafting).
	const SEND = ['documents:send'];
	function sendBody(ids: number[]): FormData {
		const body = new FormData();
		body.append('uid', 'doc_1');
		for (const id of ids) body.append('contact_ids', String(id));
		return body;
	}

	it("reports the API's count, not the number of recipients asked for", async () => {
		const { fetch } = harness(() => json({ sent: 1, notified: 1, not_notified: [] }));
		const result = await actions.send(
			actionEvent(fetch, sendBody([1, 2]), SEND) as unknown as ActionArg
		);
		expect(result).toMatchObject({ ok: true, sent: 1, requested: 2 });
	});

	it('carries the notification outcome through, so nobody is promised an email they never got', async () => {
		const { fetch } = harness(() => json({ sent: 2, notified: 1, not_notified: ['Bo Client'] }));
		const result = await actions.send(
			actionEvent(fetch, sendBody([1, 2]), SEND) as unknown as ActionArg
		);
		expect(result).toMatchObject({ sent: 2, notified: 1, notNotified: ['Bo Client'] });
	});

	it('refuses a send with no recipients and no document', async () => {
		const noOne = harness(ok);
		expect(
			await actions.send(actionEvent(noOne.fetch, sendBody([]), SEND) as unknown as ActionArg)
		).toMatchObject({ status: 400 });
		expect(noOne.calls).toEqual([]);

		const noDoc = harness(ok);
		const body = new FormData();
		body.append('contact_ids', '1');
		expect(
			await actions.send(actionEvent(noDoc.fetch, body, SEND) as unknown as ActionArg)
		).toMatchObject({ status: 400 });
		expect(noDoc.calls).toEqual([]);
	});

	it('refuses documents:write alone — yesterday’s key no longer releases to a client', async () => {
		// The six-splits posture: drafting (the default fixture permission) must not send.
		const denied = harness(ok);
		expect(
			await actions.send(actionEvent(denied.fetch, sendBody([1])) as unknown as ActionArg)
		).toMatchObject({ status: 403 });
		expect(denied.calls).toEqual([]);
	});
});

describe('documents library — delete (the API’s soft archive) and restore', () => {
	const uidBody = (uid: string): FormData => {
		const body = new FormData();
		body.append('uid', uid);
		return body;
	};

	it('archives through the API', async () => {
		const { fetch, calls } = harness(() => json(doc({ status: 'archived' })));
		const result = await actions.archive(
			actionEvent(fetch, uidBody('doc_1')) as unknown as ActionArg
		);
		expect(calls[0].url).toContain('/documents/doc_1/archive');
		expect(result).toMatchObject({ ok: true });
	});

	it('restores an archived document — archiving is recoverable', async () => {
		const { fetch, calls } = harness(() => json(doc({ status: 'generated' })));
		const result = await actions.unarchive(
			actionEvent(fetch, uidBody('doc_1')) as unknown as ActionArg
		);
		expect(calls[0].url).toContain('/documents/doc_1/unarchive');
		expect(calls[0].init?.method).toBe('POST');
		expect(result).toMatchObject({ ok: true });
	});

	it('refuses a restore without a document, or without documents:write', async () => {
		const noUid = harness(ok);
		expect(
			await actions.unarchive(actionEvent(noUid.fetch, new FormData()) as unknown as ActionArg)
		).toMatchObject({ status: 400 });
		expect(noUid.calls).toEqual([]);

		const denied = harness(ok);
		expect(
			await actions.unarchive(
				actionEvent(denied.fetch, uidBody('doc_1'), ['documents:read']) as unknown as ActionArg
			)
		).toMatchObject({ status: 403 });
		expect(denied.calls).toEqual([]);
	});

	it('surfaces the API failure rather than reporting a restore that did not happen', async () => {
		const { fetch } = harness(() => json({ detail: 'Document not found' }, 404));
		const result = await actions.unarchive(
			actionEvent(fetch, uidBody('doc_missing')) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 404 });
		expect((result as { data: { error: string } }).data.error).toBe('Document not found');
	});
});
