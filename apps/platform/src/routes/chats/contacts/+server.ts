import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { clientName } from '@graphene/ui';
import type { RequestHandler } from './$types';

/** Every client this reader may message — the Messages list itself, not a picker behind a "New"
 * button. Reuses the existing RLS-scoped contacts listing (the same one /clients renders); there
 * is no separate messaging-recipients endpoint to duplicate, and there does not need to be: who
 * may be messaged is `messages:write` plus the contact being in the caller's own tenant, which is
 * exactly what RLS already scopes this listing to (see docs/messaging.md — being that client's
 * assigned adviser is NOT required).
 *
 * UNCAPPED, and searched SERVER-side. It used to send `limit=200` and let the browser filter what
 * came back, so a firm with more clients than that silently could not reach the rest — no error,
 * no "showing 200 of 1,340", just names that were not there. `q` is the caller's search box. */
type ContactRow = {
	id: number;
	type: 'individual' | 'organisation';
	uid: string | null;
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
};

export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const q = url.searchParams.get('q')?.trim() ?? '';
	const res = await fetch(`${apiUrl()}/crm/contacts${q ? `?q=${encodeURIComponent(q)}` : ''}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body: unknown = await res.json();
	const items = (
		Array.isArray(body) ? body : ((body as { items?: unknown[] }).items ?? [])
	) as ContactRow[];
	// NAMED HERE, not in the browser. `crm.contacts.name` is the ORGANISATION's name and is null for
	// an individual, whose display name is title + first + surname — so the raw column listed half
	// the book as "Unknown". `clientName` is the one composition every other book surface uses, and
	// doing it at this boundary keeps the shared inbox taking a plain `{id, name}`.
	return json(items.map((c) => ({ id: c.id, name: clientName(c), kind: c.type })));
};
