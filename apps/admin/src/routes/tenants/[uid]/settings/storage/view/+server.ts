import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/** Resolve a short-lived presigned URL for one object (server-side, with the admin's token) and
 *  redirect the browser straight to it — the browser's per-row View/Download action. Keeps the
 *  presigned URL off the page (issued on click) and the access token server-only. */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// `book:read`, matching the storage browser page and the API's object-url endpoint: a
	// presigned URL to a client document is book (client-data) access, not tenant admin.
	if (!can(locals.permissions, locals.role, 'book:read'))
		error(403, 'Not authorised to view tenant files.');

	// Resolved AFTER the gate: an unauthorised caller must not cause an API call.
	// No parent() outside the load tree, so the lookup happens here.

	const key = url.searchParams.get('key') ?? '';
	if (!key) error(400, 'No object key');
	const q = new URLSearchParams({ key });
	if (url.searchParams.get('download') === '1') q.set('download', 'true');

	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/storage/object-url?${q}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const { url: presigned } = (await res.json()) as { url: string };
	redirect(302, presigned);
};
