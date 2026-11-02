import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

// Files now list alongside documents in the Documents section (a passport isn't really a document,
// but it doesn't warrant its own tab). This route keeps existing links working via a redirect, and
// keeps its upload/delete ACTIONS live — the merged section posts to /files?/upload and
// /files?/delete (action POSTs never run this load). /files/[uid]/download is likewise unchanged.
export const load: PageServerLoad = () => {
	redirect(307, '/documents');
};

export const actions: Actions = {
	upload: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to upload files.' });
		}
		// Forward the multipart form straight through (authHeaders sets no Content-Type, so fetch
		// keeps the browser's multipart boundary).
		const res = await fetch(`${apiUrl()}/files/upload`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body: await request.formData()
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	delete: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to delete files.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No file selected.' });
		const res = await fetch(`${apiUrl()}/files/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
