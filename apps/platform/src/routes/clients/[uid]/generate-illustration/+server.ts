import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, detail, jsonHeaders } from '$lib/api';
// From './$types', not '@sveltejs/kit': the generic RequestHandler types `params` as the
// app-wide union of every route's params, so `params.uid` is `string | undefined` there and
// this route's own `uid` is not seen at all.
import type { RequestHandler } from './$types';

/**
 * Generate a pension illustration for this client.
 *
 * A thin pass-through: the API owns building the CTC1 XML from the client record, calling the
 * engine and filing the returned PDF. The browser never sees the engine, and the client's data
 * never round-trips through the form.
 *
 * Endpoint-only, and named for the verb rather than the resource: this used to sit at
 * `clients/[id]/illustrations`, sharing that URL with the record's Illustrations section (SvelteKit
 * negotiated by `accept`). That section is gone — filed illustrations are read from the client's
 * documents like any other paperwork — so the only caller left is the generate form in the drawer on
 * the record (`$lib/illustrations/generate`). Keeping the old path would have left a route directory
 * that answered 405 to anyone following a stale link to the tab.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	// illustrations:write (the six-splits re-key): generating calls a paid external engine, and
	// the API's create endpoint demands the same slug — this keeps the proxy from forwarding an
	// ungated call at all.
	if (!can(locals.permissions, locals.role, 'illustrations:write')) {
		return json(
			{ error: 'You do not have permission to generate illustrations.' },
			{ status: 403 }
		);
	}

	const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/illustrations`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: await request.text()
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
