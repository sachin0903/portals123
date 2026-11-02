import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { activeContactParam } from '$lib/active-contact';
import type { RequestHandler } from './$types';

// Server-side proxy for the client portal's ⌘K search (a client component can't hold the access
// token). The API's /search derives the portal from the caller's own claims, so a client session
// gets only the active client's accounts, holdings, transactions, documents, eligible insights
// and open action items — never a firm-wide list.
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const q = url.searchParams.get('q') ?? '';
	// `contact_id` narrows the hits to the active client, as every other read on this portal does.
	// Without it a login linked to more than one client was offered the others' documents and
	// action items, and a hit carries no owner — so opening one was the only way to find out.
	const cid = activeContactParam(url);
	const res = await fetch(
		`${apiUrl()}/search?q=${encodeURIComponent(q)}&limit=6${cid ? `&${cid}` : ''}`,
		{
			headers: authHeaders(locals.accessToken)
		}
	);
	if (!res.ok) error(res.status, 'We couldn’t search. Please try again.');
	return json(await res.json());
};
