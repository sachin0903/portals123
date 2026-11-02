/**
 * The request builder's server action.
 *
 * The create dialog validates client-side; this mirror exists so a malformed request is refused next
 * to the field rather than coming back as a raw 422 from the API — and so a request that the API
 * would reject never claims to have been raised. Every guard below asserts the API was NOT called.
 */

import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';
import { GET as loadPickers } from './pickers/+server';

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

type Item = Record<string, unknown>;

function createEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	fields: {
		contact_id?: string;
		items?: Item[] | string;
		priority?: string;
		due_date?: string;
		/** `?client=<uid>` on the URL — the locked-target case, which posts no `contact_id`. */
		client?: string;
	},
	permissions: string[] = ['documents:send']
) {
	const body = new FormData();
	if (fields.contact_id !== undefined) body.append('contact_id', fields.contact_id);
	if (fields.items !== undefined)
		body.append(
			'items',
			typeof fields.items === 'string' ? fields.items : JSON.stringify(fields.items)
		);
	if (fields.priority) body.append('priority', fields.priority);
	if (fields.due_date) body.append('due_date', fields.due_date);
	// `url` is part of every real action event, and `create` reads it: a builder LOCKED to a client
	// (arrived from a document's "Raise a request") posts no `contact_id`, so the id comes from
	// `?client=`. `client` here lets a case exercise that arm; omitting it is the picker case.
	const url = new URL('http://portal.test/documents/requests');
	if (fields.client) url.searchParams.set('client', fields.client);
	return {
		request: new Request(`${url.origin}${url.pathname}${url.search}?/create`, {
			method: 'POST',
			body
		}),
		url,
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	};
}

type ActionArg = Parameters<typeof actions.create>[0];
type LoadArg = Parameters<typeof load>[0];

/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;

/**
 * A `url` is supplied for every case: the loader reads `?tab` and `?q` from it, because the tab
 * and the search are the SERVER's question now rather than browser state. A real load event always
 * carries one — these cases simply predate the loader needing it — and omitting it threw before
 * any assertion could run, which reads as the loader being broken rather than the harness.
 */
const runLoad = async (event: Record<string, unknown>) => {
	const loaded = (await load({
		url: new URL('http://portal.test/documents/requests'),
		...event
	} as LoadArg)) as LoadData;
	return { ...loaded, ...(await loaded.library) };
};

const created = { uid: 'req_1', items: [] };
const upload: Item = { action: 'upload', title: 'Passport' };

describe('request create — the client comes from the FORM, and only the form', () => {
	it('takes the posted contact_id', async () => {
		const { fetch, calls } = harness(() => json(created));
		await actions.create(
			createEvent(fetch, { contact_id: '3', items: [upload] }) as unknown as ActionArg
		);
		expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({ contact_id: 3 });
	});

	it('IGNORES `?client=` on the URL, and does not resolve it', async () => {
		// The action used to fall back to `?client=<uid>` for a dialog locked by a document record's
		// "Raise a request", resolving the uid to a serial with an extra round trip. That button is
		// gone with the record's Requests tab — and the feature never worked anyway, because the page
		// parsed the same uid with `Number()` and so never locked the dialog. Pinned as a REFUSAL
		// rather than simply deleted: a create that silently picked a client off the URL would be a
		// request raised against somebody nobody chose on this screen.
		const { fetch, calls } = harness((url) =>
			url.includes('/crm/contacts/') ? json({ contact: { id: 8 } }) : json(created)
		);
		const result = await actions.create(
			createEvent(fetch, { items: [upload], client: 'con_9b2e' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});
});

describe('request create — validation mirror', () => {
	it('needs a client', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, { items: [upload] }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	it('needs at least one item', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, { contact_id: '7', items: [] }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	it('refuses malformed items rather than posting them', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, { contact_id: '7', items: 'not json' }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	it('needs a title on every item', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, {
				contact_id: '7',
				items: [upload, { action: 'upload', title: '  ' }]
			}) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	it('needs a target document for agree and acknowledge items', async () => {
		for (const action of ['agree', 'acknowledge']) {
			const { fetch, calls } = harness(() => json(created));
			const result = await actions.create(
				createEvent(fetch, {
					contact_id: '7',
					items: [{ action, title: 'Terms of business' }]
				}) as unknown as ActionArg
			);
			expect(result, action).toMatchObject({ status: 400 });
			expect(calls, action).toEqual([]);
		}
	});

	it('needs a form for a complete_form item', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, {
				contact_id: '7',
				items: [{ action: 'complete_form', title: 'Fact-find' }]
			}) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 400 });
		expect(calls).toEqual([]);
	});

	it('refuses anyone without documents:send — documents:write no longer raises requests', async () => {
		// The six-splits posture: raising a request is client-facing release. The parent slug
		// alone (documents:write) — yesterday's key — must refuse, as must a plain reader.
		for (const held of [['documents:read'], ['documents:write']]) {
			const { fetch, calls } = harness(() => json(created));
			const result = await actions.create(
				createEvent(fetch, { contact_id: '7', items: [upload] }, held) as unknown as ActionArg
			);
			expect(result, held.join()).toMatchObject({ status: 403 });
			expect(calls, held.join()).toEqual([]);
		}
	});

	it('posts a well-formed request and returns the new uid', async () => {
		const { fetch, calls } = harness(() => json(created));
		const result = await actions.create(
			createEvent(fetch, {
				contact_id: '7',
				priority: 'high',
				due_date: '2026-08-01',
				items: [upload, { action: 'agree', title: 'Terms', target_document_uid: 'doc_1' }]
			}) as unknown as ActionArg
		);
		expect(result).toMatchObject({ created: true, uid: 'req_1' });
		const sent = JSON.parse(String(calls[0].init?.body));
		expect(sent).toMatchObject({ contact_id: 7, priority: 'high', due_date: '2026-08-01' });
		expect(sent.items).toHaveLength(2);
	});

	it('surfaces the API error rather than claiming the request was raised', async () => {
		const { fetch } = harness(() => json({ detail: 'Contact not visible' }, 403));
		const result = await actions.create(
			createEvent(fetch, { contact_id: '7', items: [upload] }) as unknown as ActionArg
		);
		expect(result).toMatchObject({ status: 403 });
		expect((result as { data: { error: string } }).data.error).toBe('Contact not visible');
	});
});

describe('request list — load', () => {
	it('refuses the whole-book queue without documents:show, even to a documents:write holder', async () => {
		// The queue is the same firm-wide reach as the library itself (routes/documents/+page.server.ts)
		// — documents:read (or write) alone reads documents on a client's record, not the whole book.
		const { fetch, calls } = harness(() => json([]));
		await expect(
			load({
				url: new URL('http://portal.test/documents/requests'),
				locals: {
					user: { id: 'u1' },
					permissions: ['documents:write'],
					role: null,
					accessToken: 't'
				},
				fetch
			} as unknown as LoadArg)
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toEqual([]);
	});

	it('flags a failed fetch instead of reading as nothing outstanding', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/documents/requests')) return json({ detail: 'nope' }, 403);
			return json([]);
		});
		const data = await runLoad({
			locals: {
				user: { id: 'u1' },
				permissions: ['documents:show', 'documents:write'],
				role: null,
				accessToken: 't'
			},
			fetch
		});
		expect(data.loadError).toBe(true);
		expect(data.requests).toEqual([]);
	});

	it('first paint is the paged queue — create pickers wait for the dialog', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/documents/requests?'))
				return json({
					items: [],
					total: 0,
					counts: { pending: 0, fulfilled: 0, cancelled: 0 },
					overdue: 0
				});
			return json([]);
		});
		const data = await runLoad({
			locals: {
				user: { id: 'u1' },
				permissions: ['documents:show', 'documents:read', 'documents:send', 'forms:read'],
				role: null,
				accessToken: 't'
			},
			fetch
		});
		expect(data.canSend).toBe(true);
		expect(data.loadError).toBe(false);
		expect(calls.map((c) => c.url)).toEqual([expect.stringContaining('/documents/requests')]);
		expect(calls.some((c) => c.url.includes('/forms'))).toBe(false);
		expect(calls.some((c) => c.url.includes('/documents/clients'))).toBe(false);
	});

	it('documents:write without documents:send — the list loads, the create pickers do not', async () => {
		// The narrowed role: request management (cancel/deadline) is still write's, but the New
		// request dialog's pickers (clients/documents/forms) belong to the release act.
		const { fetch, calls } = harness(() => json([]));
		const data = await runLoad({
			locals: {
				user: { id: 'u1' },
				permissions: ['documents:show', 'documents:write', 'forms:read'],
				role: null,
				accessToken: 't'
			},
			fetch
		});
		expect(data.canManage).toBe(true);
		expect(data.canSend).toBe(false);
		expect(calls.map((c) => c.url)).toEqual([expect.stringContaining('/documents/requests')]);
	});
});

describe('request create — pickers', () => {
	it('offers only published forms to a client', async () => {
		const { fetch } = harness((url) => {
			if (url.endsWith('/forms'))
				return json([
					{ uid: 'f1', name: 'Draft', status: 'draft' },
					{ uid: 'f2', name: 'Live', status: 'published' }
				]);
			return json([]);
		});
		const res = await loadPickers({
			locals: {
				user: { id: 'u1' },
				permissions: ['documents:send', 'forms:read'],
				role: null,
				accessToken: 't'
			},
			fetch
		} as never);
		const body = await res.json();
		expect(body.forms.map((f: { uid: string }) => f.uid)).toEqual(['f2']);
	});

	it('refuses without documents:send', async () => {
		const { fetch, calls } = harness(() => json([]));
		const res = await loadPickers({
			locals: {
				user: { id: 'u1' },
				permissions: ['documents:write', 'forms:read'],
				role: null,
				accessToken: 't'
			},
			fetch
		} as never);
		expect(res.status).toBe(403);
		expect(calls).toEqual([]);
	});
});
