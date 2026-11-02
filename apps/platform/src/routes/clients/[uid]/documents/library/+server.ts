import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { itemsOf, type ListingPage } from '@graphene/ui/book/listing';
import type { ApiDocument } from '@graphene/ui/book/documents';
import type { RequestHandler } from './$types';

/** Firm-wide documents for the client-record "add from library" / request pickers.
 *  Loaded on demand so opening the documents tab does not download the whole catalogue. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/documents?limit=200`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body = (await res.json()) as ApiDocument[] | ListingPage<ApiDocument>;
	return json(itemsOf(body));
};
