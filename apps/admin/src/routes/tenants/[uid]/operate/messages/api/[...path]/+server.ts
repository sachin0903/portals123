import { json } from '@sveltejs/kit';
import { clientName } from '@graphene/ui';
import { canOperateWrite, targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';

type ContactRow = {
	id: number;
	type: 'individual' | 'organisation';
	uid: string | null;
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
};

function apiPath(path: string): string | null {
	if (path === 'contacts') return '/crm/contacts';
	if (
		path === 'conversations' ||
		path === 'groups' ||
		/^conversations\/[^/]+\/(?:messages|read|attachments(?:\/[^/]+)?)$/.test(path) ||
		/^groups\/[^/]+\/(?:messages|attachments)$/.test(path)
	) {
		return `/messaging/${path}`;
	}
	return null;
}

async function proxy(
	event: Parameters<RequestHandler>[0],
	method: 'GET' | 'POST'
): Promise<Response> {
	if (!event.locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const mapped = apiPath(event.params.path);
	if (!mapped) return json({ error: 'Unknown messaging route.' }, { status: 404 });
	if (method === 'POST' && !canOperateWrite(event.locals)) {
		return json({ error: 'This tenant portal is open in read-only mode.' }, { status: 403 });
	}

	// NO `limit` on `contacts`. That list is the whole set of people this reader may message — the
	// Messages list IS the book now — so a page cut here is not a smaller payload, it is clients
	// who cannot be reached and no words on screen saying so. `?q=` rides through from the caller
	// and `/crm/contacts` narrows it server-side, which is what keeps a large book usable.
	const query = new URLSearchParams(event.url.searchParams);
	let body: BodyInit | undefined;
	let headers: HeadersInit | undefined;
	if (method === 'POST') {
		const contentType = event.request.headers.get('content-type') ?? '';
		if (contentType.includes('multipart/form-data')) {
			const incoming = await event.request.formData();
			const outgoing = new FormData();
			for (const file of incoming
				.getAll('file')
				.filter((value): value is File => value instanceof File && value.size > 0)) {
				outgoing.append('file', file, file.name);
			}
			if (!outgoing.has('file'))
				return json({ error: 'Choose a file to attach.' }, { status: 422 });
			outgoing.append('text', String(incoming.get('text') ?? ''));
			body = outgoing;
		} else {
			body = await event.request.text();
			headers = { 'Content-Type': 'application/json' };
		}
	}
	const response = await targetRequest(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		mapped,
		{ method, body, headers },
		query
	);
	if (event.params.path === 'contacts' && response.ok) {
		const payload: unknown = await response.json();
		const rows = (
			Array.isArray(payload) ? payload : ((payload as { items?: unknown[] }).items ?? [])
		) as ContactRow[];
		// NAMED HERE, like the platform's own proxy. `crm.contacts.name` is the ORGANISATION's name
		// and is null for an individual, whose display name is title + first + surname — so the raw
		// column lists every person in the book as "Unknown". `kind` travels with the name for the
		// same reason: without it the list draws a person glyph on every trust and company.
		return json(rows.map((c) => ({ id: c.id, name: clientName(c), kind: c.type })));
	}
	const responseHeaders = new Headers();
	for (const name of ['content-type', 'content-disposition']) {
		const value = response.headers.get(name);
		if (value) responseHeaders.set(name, value);
	}
	return new Response(response.body, { status: response.status, headers: responseHeaders });
}

export const GET: RequestHandler = (event) => proxy(event, 'GET');
export const POST: RequestHandler = (event) => proxy(event, 'POST');
