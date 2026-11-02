import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

const canWrite = (locals: App.Locals) =>
	can(locals.permissions, locals.role, 'platforms:write') ||
	can(locals.permissions, locals.role, 'tenants:write');

export const load: PageServerLoad = async ({ locals, fetch, params, parent }) => {
	if (!locals.user) redirect(303, '/login');
	await parent();
	const headers = authHeaders(locals.accessToken);
	const base = `${apiUrl()}/admin/tenants/${params.uid}`;
	const [disclosure, schedules, models] = await Promise.all([
		fetch(`${base}/illustration-disclosure`, { headers }),
		fetch(`${base}/illustration-fee-schedules`, { headers }),
		fetch(`${base}/illustration-models`, { headers })
	]);
	return {
		canWrite: canWrite(locals),
		disclosure: disclosure.ok ? await disclosure.json() : null,
		schedules: schedules.ok ? await schedules.json() : [],
		models: models.ok ? await models.json() : []
	};
};

export const actions: Actions = {
	disclosure: async ({ request, locals, fetch, params }) => {
		if (!locals.user) redirect(303, '/login');
		if (!canWrite(locals))
			return fail(403, { error: 'You cannot edit this firm’s illustration disclosure.' });
		const data = await request.formData();
		const frn = String(data.get('frn') ?? '').trim();
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/illustration-disclosure`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				legal_name: String(data.get('legal_name') ?? '').trim() || null,
				frn: frn ? Number(frn) : null,
				chn: String(data.get('chn') ?? '').trim() || null,
				registered_office: String(data.get('registered_office') ?? '').trim() || null,
				illustration_warning: String(data.get('illustration_warning') ?? '').trim() || null,
				gia_isa_illustrations: data.get('gia_isa_illustrations') === 'on',
				illustrations_auto_share: data.get('illustrations_auto_share') === 'on'
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, action: 'disclosure' };
	},
	schedule: async ({ request, locals, fetch, params }) => {
		if (!locals.user) redirect(303, '/login');
		if (!canWrite(locals)) return fail(403, { error: 'You cannot edit this firm’s fee schedule.' });
		const data = await request.formData();
		let tiers: unknown;
		try {
			tiers = JSON.parse(String(data.get('tiers') ?? '[]'));
		} catch {
			return fail(400, { error: 'Tiers must be a JSON list of from, to and rate.' });
		}
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/illustration-fee-schedules`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				wrapper: String(data.get('wrapper') ?? 'GIA'),
				tiers,
				tier_basis: String(data.get('tier_basis') ?? 'all_client_assets'),
				product_rate: data.get('product_rate') || '0',
				dealing_rate_exchange_traded: data.get('dealing_rate_exchange_traded') || '0',
				dealing_rate_fund: data.get('dealing_rate_fund') || '0',
				effective_from: String(data.get('effective_from') ?? '')
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, action: 'schedule' };
	},
	model: async ({ request, locals, fetch, params }) => {
		if (!locals.user) redirect(303, '/login');
		if (!canWrite(locals)) return fail(403, { error: 'You cannot edit this model.' });
		const data = await request.formData();
		const modelUid = String(data.get('model_uid') ?? '');
		let assetMix: unknown;
		let holdings: unknown;
		try {
			assetMix = JSON.parse(String(data.get('asset_mix') ?? '{}'));
			holdings = JSON.parse(String(data.get('holdings') ?? '[]'));
		} catch {
			return fail(400, { error: 'Asset mix and holdings must be JSON.' });
		}
		const charge = String(data.get('model_charge') ?? '').trim();
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${params.uid}/illustration-models/${modelUid}`,
			{
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({
					model_charge: charge === '' ? null : charge,
					asset_mix: assetMix,
					holdings
				})
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, action: 'model' };
	}
};
