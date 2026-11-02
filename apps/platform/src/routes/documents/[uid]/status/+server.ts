import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Lightweight status probe for the generate flow's polling loop (docs/document-generation.md §8.1):
// returns the document's current render status AND the note the worker left on it.
//
// THE NOTE IS THE POINT. The worker writes an author-facing sentence onto `documents.error` for
// both outcomes — "No dataset with code 'book_fees' in this firm" on a failure, and "Dataset
// 'book_contributions' returned no rows" on a document that generated with an empty table. This
// endpoint dropped it, so a failure could only be reported as "check the template" and an empty
// document said nothing at all: the one fact that explains a blank table was already recorded and
// never reached the person looking at it.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'documents:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/documents/${params.uid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Document not found');
	const doc = await res.json();
	return json({ status: doc.status as string, note: (doc.error as string | null) ?? null });
};
