import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { ApiDocument, ClientOption } from '@graphene/ui/book/documents';
import type { RequestHandler } from './$types';

type FormOption = { uid: string; name: string; status: string };

/** Create-dialog pickers for a new request. Loaded when the dialog opens so the queue's first
 *  paint is only the paged list. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'documents:send')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const headers = authHeaders(locals.accessToken);
	const [clientsR, docsR, formsR] = await Promise.all([
		fetchJson<ClientOption[]>(fetch, `${apiUrl()}/documents/clients`, headers),
		fetchJson<ApiDocument[]>(fetch, `${apiUrl()}/documents`, headers),
		can(locals.permissions, locals.role, 'forms:read')
			? fetchJson<FormOption[]>(fetch, `${apiUrl()}/forms`, headers)
			: null
	]);
	return json({
		clients: clientsR.ok ? clientsR.data : [],
		documents: docsR.ok ? docsR.data : [],
		forms: formsR?.ok ? formsR.data.filter((f) => f.status === 'published') : []
	});
};
