import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * A tenant's email image library, for the editor's image picker on the ops side: GET lists the
 * images under that tenant bucket's public `files/tenant/` prefix, POST adds one. Proxied so the
 * access token stays server-side, like the sibling preview / send-test / versions routes.
 *
 * The library is the TENANT'S, not the template's — the API endpoint takes no template id and the
 * one here is ignored. It sits under `[templateUid]` all the same, beside the editor's other
 * proxies, so the editor resolves it relative to its own route and reaches the right proxy in
 * either portal without the page threading a URL down.
 *
 * `files/tenant/*` is the only anonymous-read prefix a tenant bucket has, and an email image must
 * be fetchable by a mail client with no credential of ours — so this is not a general object
 * browser and must never become one. The API enforces the prefix; nothing here chooses it.
 */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.read)) error(403, 'Not permitted');

	const target = new URL(`${apiUrl()}/admin/tenants/${params.uid}/email-templates/images`);
	// The only parameter forwarded: S3's opaque continuation token. Anything else a caller appended
	// would be a parameter the API does not take.
	const token = url.searchParams.get('token');
	if (token) target.searchParams.set('token', token);
	const res = await fetch(target, { headers: authHeaders(locals.accessToken) });
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};

export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	// WRITE, unlike the GET: the object lands in that firm's own public prefix.
	if (!can(locals.permissions, locals.role, scope.write)) error(403, 'Not permitted');

	const incoming = await request.formData().catch(() => null);
	const file = incoming?.get('file');
	if (!(file instanceof File)) error(400, 'No image was uploaded');

	// Rebuilt rather than streamed through: the body has to be re-framed with its own boundary, and
	// `authHeaders` alone is passed on so fetch sets the Content-Type (a copied one names the old
	// boundary, and the API then parses nothing). The API sniffs the bytes — the declared type this
	// carries is not what decides.
	const body = new FormData();
	body.append('file', file, file.name || 'image');
	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/email-templates/images`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken),
		body
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json(), { status: 201 });
};
