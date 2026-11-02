import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * The firm's email image library, for the editor's image picker: GET lists the images under the
 * tenant bucket's public `files/tenant/` prefix, POST adds one. Proxied so the access token stays
 * server-side, like the sibling preview / send-test / versions routes.
 *
 * The library is the FIRM'S, not the template's — the API endpoint takes no template id and the
 * one here is ignored. It sits under `[uid]` all the same, beside the editor's other proxies, so the
 * editor can resolve it relative to its own route and reach the right proxy in either portal
 * without the page threading a URL down.
 *
 * `files/tenant/*` is the only anonymous-read prefix a tenant bucket has, and an email image must be
 * fetchable by a mail client with no credential of ours — so this is not a general object browser
 * and must never become one. The API enforces the prefix; nothing here chooses it.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:read')) error(403, 'Not permitted');

	const token = url.searchParams.get('token');
	const target = new URL(`${apiUrl()}/email-templates/images`);
	// The only parameter forwarded: S3's opaque continuation token. Anything else a caller appended
	// would be a parameter the API does not take.
	if (token) target.searchParams.set('token', token);
	const res = await fetch(target, { headers: authHeaders(locals.accessToken) });
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};

export const POST: RequestHandler = async ({ request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:write')) error(403, 'Not permitted');

	const incoming = await request.formData().catch(() => null);
	const file = incoming?.get('file');
	if (!(file instanceof File)) error(400, 'No image was uploaded');

	// Rebuilt rather than streamed through: the body has to be re-framed with its own boundary, and
	// `authHeaders` alone is passed on so fetch sets the Content-Type (a copied one names the old
	// boundary, and the API then parses nothing). The API sniffs the bytes — the declared type this
	// carries is not what decides.
	const body = new FormData();
	body.append('file', file, file.name || 'image');
	const res = await fetch(`${apiUrl()}/email-templates/images`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken),
		body
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json(), { status: 201 });
};
