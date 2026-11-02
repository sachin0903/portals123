import { error, fail, redirect } from '@sveltejs/kit';
import {
	apiUrl,
	authHeaders,
	jsonHeaders,
	detail,
	type CodeRef,
	type IntegrationSummary,
	type TenantRef
} from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	let integrations: IntegrationSummary[] = [];
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/integrations`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) integrations = await res.json();
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	// Reference data for the edit sheet's firm Select and feature Switch grid. Best-effort: a
	// failure here narrows what the sheet can offer rather than taking the listing down with it.
	let firms: TenantRef[] = [];
	let features: CodeRef[] = [];
	try {
		const headers = authHeaders(locals.accessToken);
		const [firmsRes, featuresRes] = await Promise.all([
			fetch(`${apiUrl()}/integrations/tenants`, { headers }),
			fetch(`${apiUrl()}/integrations/features`, { headers })
		]);
		if (firmsRes.ok) firms = await firmsRes.json();
		if (featuresRes.ok) features = await featuresRes.json();
	} catch {
		/* the edit sheet just offers fewer options */
	}

	return {
		integrations,
		apiError,
		firms,
		features,
		canWrite: can(locals.permissions, locals.role, 'integrations:write')
	};
};

export const actions: Actions = {
	/**
	 * Approve/reject/reconfigure one integration from the list's edit sheet.
	 *
	 * `actioned_by` is resolved server-side by the API from the bearer token — never trusted from
	 * this body. The reject-requires-reason rule is enforced here too (the legacy's `canSave`
	 * rule, ported as-is) so a client that skipped the disabled-Save affordance still gets a clean
	 * 400 rather than relying solely on the API's own 422/CHECK.
	 */
	update: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'integrations:write'))
			return fail(403, {
				error: 'You need the integrations:write permission to manage integrations.'
			});

		const data = await request.formData();
		const uid = String(data.get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No integration given.' });

		const status = String(data.get('status') ?? 'pending');
		const reason = String(data.get('reason') ?? '').trim();
		if (status === 'rejected' && !reason)
			return fail(400, { uid, error: 'A reason is required to reject an integration.' });

		const firmIdRaw = String(data.get('firm_id') ?? '').trim();
		const body = {
			firm_id: firmIdRaw ? Number(firmIdRaw) : null,
			status,
			enabled: data.get('enabled') === 'on',
			features: data.getAll('features').map(String),
			reason: reason || null
		};

		const res = await fetch(`${apiUrl()}/integrations/${uid}`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { uid, error: await detail(res) });
		return { ok: true, uid };
	}
};
