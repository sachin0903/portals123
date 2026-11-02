import { json, error } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';
import { readAudience } from '@graphene/ui/email-editor';

/**
 * Mail a tenant's email template — the editor's CURRENT content — to the ACTING OPERATOR'S OWN
 * address, proxied to the admin API so the access token stays server-side. This is the only inbox
 * test the MODEL preset can have: the model tenant has no logins, so its templates cannot be tested
 * through the firm door at all.
 *
 * **No recipient crosses this boundary** (as on the firm route): the API resolves the address from
 * the operator's own session and ignores any address in the body, so this forwards the same four
 * fields `/preview` does. The mail is dressed in and sent AS the TARGET tenant, which is the point —
 * an operator curating a firm's template needs to see that firm's mail, not Graphene's.
 *
 * Gated by the target tenant's WRITE scope (model preset → `model:write`, a firm → `tenants:write`),
 * matching the API's `_require_tenant_scope(write=True)`: a read-only operator may preview a firm's
 * email and still not spend its sending reputation.
 */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	// The app's tenant routes are uid-addressed; `/admin/tenants/{tenant_id}` keeps its integer
	// contract, and an endpoint has no `parent()` to read the layout's tenant from — so it resolves,
	// through the one shared resolver the preview/versions proxies use.
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.write))
		error(403, `Not permitted — this needs ${scope.write}.`);

	const body = await request.json().catch(() => ({}));
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/email-templates/${params.templateUid}/send-test`,
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
	// The throttle is passed through as a response rather than thrown: `error()` cannot carry a
	// header, and `Retry-After` is what lets the toast name the wait instead of saying "shortly". The
	// body keeps SvelteKit's `{ message }` shape so the client parses both paths identically.
	if (res.status === 429) {
		const retryAfter = res.headers.get('retry-after');
		return json(
			{ message: await detail(res) },
			{ status: 429, headers: retryAfter ? { 'retry-after': retryAfter } : {} }
		);
	}
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
