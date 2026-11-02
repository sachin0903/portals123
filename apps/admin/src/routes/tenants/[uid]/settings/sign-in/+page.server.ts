import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type Environment } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/**
 * Server-side write guard: the page is reachable with tenants:read (the [id] layout's gate), so
 * every mutating action below must refuse a read-only operator itself — the API enforces the same
 * permission, but the action must fail honestly rather than surface whatever the API's 403 says.
 * Returns the `fail` to hand back, or null when the caller may proceed.
 */
const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'tenants:write')
		? null
		: fail(403, { action, error: 'You need the tenants:write permission to manage this firm.' });

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The firm's uid — what `/admin/tenants/{tenant_uid}` takes. It is the route's own parameter,
	// so nothing is resolved to get it.
	const tenantUid = params.uid;
	const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/environment`, {
		headers: authHeaders(locals.accessToken)
	});
	// The endpoint returns the single config object, or null when not yet configured.
	const environment: Environment | null = res.ok ? await res.json() : null;
	return { environment };
};

export const actions: Actions = {
	saveConfiguration: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'saveConfiguration');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		// No stage — the instance stamps it.
		const payload = {
			workos_client_id: String(data.get('workos_client_id') ?? '').trim(),
			issuer: String(data.get('issuer') ?? '').trim() || null,
			client_portal_host: String(data.get('client_portal_host') ?? '').trim() || null,
			platform_host: String(data.get('platform_host') ?? '').trim() || null,
			operations_host: String(data.get('operations_host') ?? '').trim() || null
		};
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/environment`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(payload)
		});
		if (!res.ok) return fail(res.status, { action: 'saveConfiguration', error: await detail(res) });
		// Registering or changing a client id/issuer changes how the API picks an environment to
		// verify tokens against, so the registry has to be reloaded here too — not only when keys
		// are next saved. Without it a newly-registered client id 401s until the API restarts.
		await fetch(`${apiUrl()}/admin/environments/refresh`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		return { action: 'saveConfiguration', ok: true };
	},

	setSecrets: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setSecrets');
		if (refused) return refused;
		const data = await request.formData();
		const environmentUid = String(data.get('environment_uid') ?? '');
		if (!environmentUid)
			return fail(400, { action: 'setSecrets', error: 'The configuration must be saved first.' });
		const res = await fetch(`${apiUrl()}/admin/environments/${environmentUid}/secrets`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				workos_api_key: String(data.get('workos_api_key') ?? ''),
				cookie_password: String(data.get('cookie_password') ?? '')
			})
		});
		if (!res.ok) return fail(res.status, { action: 'setSecrets', error: await detail(res) });
		// Pick up the updated keys in the API's verification registry immediately.
		await fetch(`${apiUrl()}/admin/environments/refresh`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		return { action: 'setSecrets', ok: true };
	},

	// Portal-via-application (docs/auth-applications-migration.md): record each portal's WorkOS
	// application client id. Blank fields are omitted so saving one portal never wipes another
	// (the API COALESCE-preserves). No registry refresh needed — resolve_claims reads these live.
	saveApplications: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'saveApplications');
		if (refused) return refused;
		const data = await request.formData();
		const field = (name: string) => String(data.get(name) ?? '').trim() || null;
		const payload = {
			client_portal_client_id: field('client_portal_client_id'),
			platform_client_id: field('platform_client_id'),
			operations_client_id: field('operations_client_id'),
			// Each application's OWN secret — the portal signs in with the (id, secret) pair.
			client_portal_secret: field('client_portal_secret'),
			platform_secret: field('platform_secret'),
			operations_secret: field('operations_secret')
		};
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/environment/applications`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(payload)
		});
		if (!res.ok) return fail(res.status, { action: 'saveApplications', error: await detail(res) });
		return { action: 'saveApplications', ok: true };
	}
};
