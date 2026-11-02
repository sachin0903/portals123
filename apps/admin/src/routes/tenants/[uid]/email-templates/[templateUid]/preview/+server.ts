import { json, error } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';
import { readAudience } from '@graphene/ui/email-editor';

/**
 * Render a sample preview of the editor's CURRENT content for a tenant's email template — merge
 * fields filled with representative values, CSS folded inline — proxied to the admin API so the
 * access token stays server-side. NEVER sends: the API's /preview endpoint only merges + inlines.
 *
 * Gated by the target tenant's READ scope (model preset → model:read, a firm → tenants:read), the
 * same choice the API makes in `_require_tenant_scope`.
 */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.read)) error(403, 'Not permitted');

	const body = await request.json().catch(() => ({}));
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/email-templates/${params.templateUid}/preview`,
		{
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				subject: String(body.subject ?? ''),
				preheader: String(body.preheader ?? ''),
				html: String(body.html ?? ''),
				css: String(body.css ?? ''),
				// Which of the firm's two brands dresses it — see the platform send-test proxy's header.
				audience: readAudience(body.audience)
			})
		}
	);
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
