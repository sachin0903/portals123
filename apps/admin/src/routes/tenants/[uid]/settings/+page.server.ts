import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
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

// Nothing to load: the firm itself comes from the [uid] layout, which every child inherits. The
// actions resolve the numeric id themselves, AFTER their write guard, so a read-only operator's
// action never costs an API call.
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/currency-settings`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return {
		currencySettings: (await res.json()) as {
			base_currency: string;
			allowed_currencies: string[];
			currencies: Array<{ code: string; currency: string | null; symbol: string | null }>;
		}
	};
};

export const actions: Actions = {
	editTenant: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'editTenant');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const payload = {
			name: String(data.get('name') ?? '').trim() || null,
			alias: String(data.get('alias') ?? '').trim() || null,
			website: String(data.get('website') ?? '').trim() || null,
			enabled: data.get('enabled') === 'on'
		};
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(payload)
		});
		if (!res.ok) return fail(res.status, { action: 'editTenant', error: await detail(res) });
		const body = await res.json();
		return {
			action: 'editTenant',
			ok: true,
			enabled: body?.enabled as boolean,
			// Present only when this save suspended the firm — the toast then states what it did.
			sessionsRevoked: (body?.sessions_revoked ?? null) as number | null
		};
	},

	setCurrencies: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setCurrencies');
		if (refused) return refused;
		const data = await request.formData();
		const baseCurrency = String(data.get('base_currency') ?? '').trim();
		const allowedCurrencies = data
			.getAll('allowed_currencies')
			.map(String)
			.map((code) => code.trim())
			.filter(Boolean);
		if (!baseCurrency || !allowedCurrencies.length) {
			return fail(400, {
				action: 'setCurrencies',
				error: 'Choose a base currency and at least one allowed currency.'
			});
		}
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/currency-settings`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				base_currency: baseCurrency,
				allowed_currencies: allowedCurrencies
			})
		});
		if (!res.ok) {
			return fail(res.status, { action: 'setCurrencies', error: await detail(res) });
		}
		return { action: 'setCurrencies', ok: true };
	},

	// Destroy the firm. The API refuses unless it is already suspended and `confirm` matches the
	// alias, so the two-step order (suspend → export → delete) is enforced server-side too.
	deleteTenant: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'deleteTenant');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const confirm = String(data.get('confirm') ?? '').trim();
		if (!confirm)
			return fail(400, { action: 'deleteTenant', error: 'Type the firm’s alias to confirm.' });
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}?confirm=${encodeURIComponent(confirm)}`,
			{ method: 'DELETE', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { action: 'deleteTenant', error: await detail(res) });
		redirect(303, '/tenants?deleted=1');
	}
};
