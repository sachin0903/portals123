import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * A tenant's email-template version history, and the restore that walks an edit back.
 *
 * Version history IS the revert mechanism for an email template (there is no "reset to default"),
 * so an operator who edits a firm's template on their behalf needs the same door the firm has. The
 * shared `VersionHistoryDialog` (`@graphene/ui/email-editor`) fetches from an injected URL, and this
 * is the admin app's — proxied so the access token stays server-side, exactly as the sibling
 * `/preview` route does.
 *
 * Gating mirrors the API's own choice (`admin.py::_require_tenant_scope`) via `templateScope`:
 * reading history needs the target tenant's READ scope, restoring its WRITE scope — so a read-only
 * operator can see what changed and still not change it. Restores through this door are stamped
 * `authored_by_scope = 'operations'` by the API (migration 0051) and land in `audit.actions` naming
 * the operator; nothing here defeats that.
 */

/** Resolve the URL's tenant uid to the numeric id the API takes, and gate on that tenant's scope. */
async function tenantFor(
	uid: string,
	locals: App.Locals,
	fetch: typeof globalThis.fetch,
	need: 'read' | 'write'
): Promise<string> {
	if (!locals.user) error(401, 'Not signed in');
	// `/admin/tenants/{tenant_uid}` takes the uid the route already carries, so there is nothing
	// to resolve — an endpoint has no `parent()` to read the layout's tenant from, and now it does
	// not need one. What survives is the SCOPE check the resolve used to hang off.
	const scope = await templateScope(uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope[need]))
		error(403, `Not permitted — this needs ${scope[need]}.`);
	return uid;
}

const versions = (tenantUid: string, templateUid: string): string =>
	`${apiUrl()}/admin/tenants/${tenantUid}/email-templates/${templateUid}/versions`;

/**
 * List the template's versions, newest first — or ONE of them with `?vid=`, content included.
 *
 * The same route rather than a sibling: the dialog is handed a single `versionsUrl` and must reach
 * both from it, and a second URL threaded down from each portal is a second thing to get wrong.
 * Without the detail read a version could only be RESTORED, never looked at — so an author chose off
 * a subject line and found out what they had picked afterwards.
 */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	const tenantId = await tenantFor(params.uid, locals, fetch, 'read');
	const vid = url.searchParams.get('vid');
	const target = vid
		? `${versions(tenantId, params.templateUid)}/${encodeURIComponent(vid)}`
		: versions(tenantId, params.templateUid);
	const res = await fetch(target, { headers: authHeaders(locals.accessToken) });
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};

/** Restore a version → the API copies it into a NEW draft (never overwrites / auto-publishes). */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	const tenantId = await tenantFor(params.uid, locals, fetch, 'write');
	const body = await request.json().catch(() => ({}));
	const vid = String(body.vid ?? '');
	if (!vid) error(400, 'No version specified');
	const res = await fetch(`${versions(tenantId, params.templateUid)}/${vid}/restore`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
