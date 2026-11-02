import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { toClientDoc, type ApiDocument } from '$lib/documents';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const headers = authHeaders(locals.accessToken);
	const res = await fetch(`${apiUrl()}/documents/${params.uid}`, { headers });
	if (res.status === 404 || res.status === 403) error(404, 'Document not found');
	if (!res.ok) error(res.status, 'Failed to load document');

	const api: ApiDocument = await res.json();
	// Preview + download go through our same-origin content proxy (see content/+server.ts): the
	// object store is compose-internal, so a presigned URL wouldn't resolve in the browser. Null
	// until the document has a retrievable file.
	const previewUrl = api.content_type ? `/documents/${params.uid}/content` : null;
	return {
		doc: toClientDoc(api),
		previewUrl,
		previewContentType: api.content_type
	};
};
